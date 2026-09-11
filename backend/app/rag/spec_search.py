"""
Consulta por ESPECIFICAÇÃO TÉCNICA — a pergunta é sobre um NÚMERO do produto
("quero um produto com hidroxila de 180", "viscosidade acima de 5000 cPs",
"NCO entre 12 e 13%"), não sobre nome, família ou aplicação.

Por que um módulo próprio (e não só RAG):
  - A busca vetorial não representa número: "hidroxila 180" e "hidroxila 34"
    geram vetores praticamente iguais (o embedding não sabe comparar
    grandezas), então o top-k traz "algum produto que fala de hidroxila",
    não "os produtos cuja hidroxila é 180". Mesmo problema já documentado
    para códigos de produto em app.rag.engine._detectar_codigos_produto.
  - A tabela de especificação chega ao índice EMBARALHADA pela extração de
    PDF/DOCX (ex: "Índice de hidroxilas mgKOH/g 54,0 – 58,0 56,80 Viscosidade
    Brookfield a 25 °C cPs 7500 – 8500 7810"). Jogar isso cru no prompt faz o
    LLM trocar coluna, unidade e produto. Aqui o texto é lido uma vez, de
    forma estruturada, e o agente recebe par propriedade→valor já resolvido.

FORMATOS REAIS COBERTOS (amostrados da coleção `pu_products_catalog` em
produção — a ordem das colunas MUDA de template para template, por isso o
parser NÃO tenta adivinhar qual número é mínimo/máximo/resultado; ele coleta
todos os números da célula e trabalha com a FAIXA observada, guardando o
trecho literal como evidência para o vendedor conferir):

    Índice de hidroxila(mg koh/g) 32,1 34,9 34,0      (mín. / máx. / resultado)
    Número de hidroxilas 34 mgKOH/g 31 - 34           (resultado / un. / espec.)
    Índice de hidroxilas mgKOH/g 54,0 – 58,0 56,80    (un. / espec. / resultado)
    Índice de hidroxilas mgKOH/g 28,0 a 32,0
    Índice de hidroxilas mgKOH/g 25,5 ± 2,5
    Teor de NCO 14,26 % 14,20 a 14,40
    Viscosidade a 25C cps ............ 1200 825
    Densidade 1,07 g/cm³ 1,00 a 1,10
    Densidade (g/cm³): 1,08 a 25ºC
    Índice de acidez mgKOH/g < 2,0 1,50

Números com ponto e três casas ("22.500 cPs") são lidos como MILHAR — todo o
acervo usa vírgula decimal, então "1.005" é 1005, não 1,005.
"""
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.config import COLLECTION_NAME
from app.rag.catalog_stats import _produto_do_filepath, _termo_bate_no_conteudo
from app.rag.exceptions import RetrievalIndisponivelError
from app.rag.ingestion import get_qdrant_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulário de propriedades
# ---------------------------------------------------------------------------
# `rotulos`: como a propriedade aparece ESCRITA no documento (a etiqueta da
#   linha da tabela). `termos_consulta`: como o vendedor fala dela na pergunta
#   ("hidroxila de 180", "quanto de NCO"). São listas diferentes de propósito:
#   o documento escreve "Índice de hidroxilas", o vendedor escreve "hidroxila".
# Rótulos sempre SEM ACENTO e em minúscula — a comparação roda sobre o texto
# normalizado (ver _normalizar_alinhado).
PROPRIEDADES: Dict[str, Dict[str, Any]] = {
    "indice_hidroxila": {
        "titulo": "Índice de hidroxila",
        "unidade_tipica": "mgKOH/g",
        "rotulos": [
            "indice de hidroxilas", "indice de hidroxila", "numero de hidroxilas",
            "numero de hidroxila", "teor de hidroxilas", "teor de hidroxila",
            "indice de oh", "numero de oh", "hidroxilas", "hidroxila",
        ],
        "termos_consulta": [
            "indice de hidroxila", "indice de hidroxilas", "numero de hidroxila",
            "numero de hidroxilas", "indice de oh", "valor de oh", "indice oh",
            "hidroxilas", "hidroxila",
        ],
    },
    "teor_nco": {
        "titulo": "Teor de NCO",
        "unidade_tipica": "%",
        "rotulos": ["teor de nco", "isocianato livre", "teor de isocianato", "nco"],
        "termos_consulta": ["teor de nco", "teor de isocianato", "isocianato livre", "nco"],
    },
    "viscosidade": {
        "titulo": "Viscosidade",
        "unidade_tipica": "cPs",
        "rotulos": ["viscosidade brookfield", "viscosidade"],
        "termos_consulta": ["viscosidade brookfield", "viscosidade"],
    },
    "densidade_imersao": {
        "titulo": "Densidade por imersão",
        "unidade_tipica": "kg/m³",
        "rotulos": [
            "densidade por imersao", "densidade de imersao", "densidade imersao",
        ],
        "termos_consulta": [
            "densidade por imersao", "densidade de imersao", "densidade imersao",
        ],
    },
    "densidade": {
        "titulo": "Densidade",
        "unidade_tipica": "g/cm³ ou kg/m³",
        "rotulos": ["densidade aparente", "densidade livre", "peso especifico", "densidade"],
        "termos_consulta": [
            "densidade aparente", "densidade livre", "peso especifico", "densidade",
        ],
    },
    "dureza": {
        "titulo": "Dureza",
        "unidade_tipica": "Shore A / Shore D / IFD",
        "rotulos": ["dureza shore a", "dureza shore d", "dureza", "shore a", "shore d"],
        "termos_consulta": [
            "dureza shore a", "dureza shore d", "dureza", "shore a", "shore d", "ifd",
        ],
    },
    "ph": {
        "titulo": "pH",
        "unidade_tipica": "",
        "rotulos": ["ph"],
        "termos_consulta": ["ph"],
    },
    "teor_solidos": {
        "titulo": "Teor de sólidos",
        "unidade_tipica": "%",
        "rotulos": ["teor de solidos", "solidos"],
        "termos_consulta": ["teor de solidos", "solidos"],
    },
    "teor_agua": {
        "titulo": "Teor de água",
        "unidade_tipica": "%",
        "rotulos": ["teor de agua", "teor de umidade", "umidade"],
        "termos_consulta": ["teor de agua", "teor de umidade", "umidade"],
    },
    "indice_acidez": {
        "titulo": "Índice de acidez",
        "unidade_tipica": "mgKOH/g",
        "rotulos": ["indice de acidez", "acidez"],
        "termos_consulta": ["indice de acidez", "acidez"],
    },
    "funcionalidade": {
        "titulo": "Funcionalidade",
        "unidade_tipica": "",
        "rotulos": ["funcionalidade"],
        "termos_consulta": ["funcionalidade"],
    },
    "relacao_trabalho": {
        "titulo": "Relação de trabalho",
        "unidade_tipica": "partes",
        "rotulos": ["relacao de trabalho"],
        "termos_consulta": ["relacao de trabalho"],
    },
    # --- Perfil de reatividade -------------------------------------------
    # Os tempos são o coração da conversa de reatividade com o cliente ("qual
    # o tempo de reação desse sistema?"). Todos ficam em SEGUNDOS internamente,
    # porque o acervo escreve no formato mm`ss`` ("Tempo de Cura 120`00``" =
    # 120 min) e comparar minuto com segundo sem converter daria falso match.
    "tempo_creme": {
        "titulo": "Tempo de creme",
        "unidade_tipica": "s",
        "tipo": "tempo",
        "rotulos": ["tempo de creme"],
        "termos_consulta": ["tempo de creme", "creme"],
    },
    "tempo_reacao": {
        "titulo": "Tempo de reação",
        "unidade_tipica": "s",
        "tipo": "tempo",
        "rotulos": ["tempo de reacao"],
        "termos_consulta": ["tempo de reacao", "tempo de resposta"],
    },
    "tempo_gel": {
        "titulo": "Tempo de gel / fio",
        "unidade_tipica": "s",
        "tipo": "tempo",
        "rotulos": ["tempo de gel", "tempo de fio"],
        "termos_consulta": ["tempo de gel", "tempo de fio"],
    },
    "tempo_pega": {
        "titulo": "Tempo de pega livre",
        "unidade_tipica": "s",
        "tipo": "tempo",
        "rotulos": ["tempo de pega livre", "tempo de pega", "pega livre"],
        "termos_consulta": ["tempo de pega livre", "tempo de pega", "pega livre"],
    },
    "tempo_cura": {
        "titulo": "Tempo de cura",
        "unidade_tipica": "s",
        "tipo": "tempo",
        "rotulos": ["tempo de cura"],
        "termos_consulta": ["tempo de cura", "tempo de secagem"],
    },
    "tempo_desmolde": {
        "titulo": "Tempo de desmolde",
        "unidade_tipica": "s",
        "tipo": "tempo",
        "rotulos": ["tempo de desmolde"],
        "termos_consulta": ["tempo de desmolde", "desmolde"],
    },
    "resiliencia": {
        "titulo": "Resiliência",
        "unidade_tipica": "%",
        "rotulos": ["resiliencia"],
        "termos_consulta": ["resiliencia"],
    },
    "alongamento": {
        "titulo": "Alongamento",
        "unidade_tipica": "%",
        "rotulos": ["alongamento na ruptura", "alongamento"],
        "termos_consulta": ["alongamento na ruptura", "alongamento"],
    },
    "resistencia_tracao": {
        "titulo": "Resistência à tração",
        "unidade_tipica": "MPa",
        "rotulos": ["resistencia a tracao", "tracao"],
        "termos_consulta": ["resistencia a tracao", "tracao"],
    },
    "resistencia_rasgo": {
        "titulo": "Resistência ao rasgo",
        "unidade_tipica": "N/mm",
        "rotulos": ["resistencia ao rasgo", "rasgo"],
        "termos_consulta": ["resistencia ao rasgo", "rasgo"],
    },
    "temperatura": {
        "titulo": "Temperatura",
        "unidade_tipica": "°C",
        "rotulos": ["temperatura de trabalho", "temperatura do molde", "temperatura"],
        "termos_consulta": [
            "temperatura de trabalho", "temperatura do molde", "temperatura",
        ],
    },
}


class _TabelaDeNormalizacao(dict):
    """Tabela para `str.translate` que resolve cada caractere na primeira vez
    que o vê e guarda o resultado.

    Existe por custo: a varredura por especificação normaliza o acervo inteiro
    (milhares de trechos de ~4 KB), e chamar `unicodedata.normalize` por
    caractere levava a maior parte do tempo da consulta. Com a tabela, o mesmo
    trabalho vira um `translate` em C sobre alguns milhares de entradas
    distintas."""

    def __missing__(self, codigo: int) -> str:
        caractere = chr(codigo)
        decomposto = unicodedata.normalize("NFKD", caractere)
        base = "".join(c for c in decomposto if not unicodedata.combining(c))
        # Cada caractere de entrada vira EXATAMENTE um de saída (ver docstring
        # de _normalizar_alinhado) — daí o corte em base[0].
        resultado = (base[0] if base else " ").lower()
        self[codigo] = resultado
        return resultado


_TABELA_NORMALIZACAO = _TabelaDeNormalizacao()


def _normalizar_alinhado(texto: str) -> str:
    """Minúsculas e sem acento, preservando 1 caractere de saída por caractere
    de entrada — os índices do texto normalizado continuam válidos no texto
    ORIGINAL, que é de onde sai o trecho de evidência mostrado ao vendedor.
    Um NFKD aplicado à string inteira encurta o texto ("ç" -> "c" + cedilha
    combinante removida) e desalinha os índices."""
    return texto.translate(_TABELA_NORMALIZACAO)


def _remover_numeracao_de_secao(normalizado: str) -> str:
    """Apaga a numeração de seção da FISPQ ("9.10 Solubilidade em água",
    "10. ESTABILIDADE E REATIVIDADE") do texto inteiro, ANTES de procurar
    rótulos. Ela é índice do documento, nunca valor — sem isso a célula
    "Densidade (g/cm³): 1,05 9.10 Solubilidade..." virava uma densidade de
    1,05 a 9,10 (achado rodando o parser sobre o acervo real).

    Roda sobre o texto todo, não sobre a janela de cada célula: a janela é
    cortada no próximo rótulo, e um número de seção colado nesse corte
    ("... 9.11 Viscosidade") ficaria órfão no fim da janela, sem a palavra
    seguinte que o identifica como numeração.

    A substituição preserva o COMPRIMENTO (espaços no lugar) porque os índices
    do texto normalizado precisam continuar válidos no original, de onde sai o
    trecho de evidência."""
    for padrao in _PADRAO_NUMERACAO_DE_SECAO:
        normalizado = padrao.sub(lambda m: " " * len(m.group(0)), normalizado)
    return normalizado


def _padrao_de_rotulos(rotulos: List[str]) -> re.Pattern:
    """Alternância de rótulos, do mais longo para o mais curto — em regex a
    primeira alternativa que casa vence, então "indice de hidroxila" precisa
    vir antes de "hidroxila", senão toda linha vira o rótulo curto e o parser
    perde a etiqueta real da tabela."""
    ordenados = sorted(set(rotulos), key=len, reverse=True)
    corpo = "|".join(
        "".join(r"\s+" if ch == " " else re.escape(ch) for ch in rotulo)
        for rotulo in ordenados
    )
    return re.compile(rf"(?<![a-z0-9])({corpo})(?![a-z])")


_TODOS_OS_ROTULOS = [r for dados in PROPRIEDADES.values() for r in dados["rotulos"]]
_PADRAO_TODOS_ROTULOS = _padrao_de_rotulos(_TODOS_OS_ROTULOS)
_PADRAO_POR_PROPRIEDADE = {
    prop: _padrao_de_rotulos(dados["rotulos"]) for prop, dados in PROPRIEDADES.items()
}
_PROPRIEDADE_DO_ROTULO = {
    rotulo: prop for prop, dados in PROPRIEDADES.items() for rotulo in dados["rotulos"]
}

# Unidades como aparecem no acervo (sobre o texto já normalizado). São
# reconhecidas ANTES de extrair números porque algumas CARREGAM dígito depois
# da normalização: "g/cm³" vira "g/cm3" e "kg/m³" vira "kg/m3" — sem remover a
# unidade antes, esse "3" entraria na lista como se fosse valor de spec.
_PADRAO_UNIDADE = re.compile(
    r"mg\s*koh\s*/\s*g|kg\s*/\s*m3|gr?\s*/\s*cm3|mpa\.?\s*s|n\s*/\s*mm|"
    r"shore\s*[ad]|kgf\s*/\s*cm2|cps|cp\b|mpa|ppm|%|[°º]\s*c|\bseg\b|\bmin\b"
)
_UNIDADE_EXIBICAO = {
    "mgkoh/g": "mgKOH/g", "kg/m3": "kg/m³", "g/cm3": "g/cm³", "gr/cm3": "g/cm³",
    "cps": "cPs", "cp": "cPs", "mpa.s": "mPa.s", "mpas": "mPa.s", "mpa": "MPa",
    "ppm": "ppm", "%": "%", "°c": "°C", "ºc": "°C", "seg": "s", "min": "min",
    "n/mm": "N/mm", "kgf/cm2": "kgf/cm²", "shorea": "Shore A", "shored": "Shore D",
}

# Fim da célula: a partir daqui o texto já é outra coisa (rodapé do laudo,
# observações, próxima seção) e qualquer número seria de outra propriedade.
_MARCADORES_FIM_DE_CELULA = (
    "observ", "este laudo", "informacoes adicionais", "informacoes complementares",
    "quimico:", "responsavel", "aplicacao", "caracteristicas e vantagens",
    "seguranca e armazenamento", "manuseio", "os dados obtidos",
)

# Quantos caracteres depois do rótulo ainda pertencem à mesma célula da tabela.
# 90 cobre o pior caso real observado ("Índice de hidroxila(mg koh/g) 32,1 34,9
# 34,0" — três colunas mais a unidade escrita por extenso) sem atravessar para
# a linha seguinte na maioria dos templates; quando atravessa, o corte por
# próximo rótulo/marcador acima resolve.
_JANELA_APOS_ROTULO = 90

# Número em convenção pt-BR, com ou sem separador de milhar. A alternativa do
# milhar vem primeiro porque a regex fica com a PRIMEIRA que casar: sem ela,
# "20.000,00 ± 6.000,00" era lido a partir do meio do número ("000,00 ± 6.000"),
# virando 0 ± 6000 — uma faixa de -6000 a 6000 para uma viscosidade real de
# 14.000 a 26.000 cPs (achado ao rodar o parser sobre o acervo real).
_NUMERO = r"\d{1,3}(?:\.\d{3})+(?:,\d{1,4})?|\d{1,7}(?:[.,]\d{1,4})?"
_PADRAO_NUMERO = re.compile(rf"(?<![\w,.])({_NUMERO})(?![\w])")
# A pergunta frequentemente cola a unidade ao valor ("200Kg/m³"). Nos
# documentos, a versão estrita acima evita códigos e outros ruídos; na consulta,
# os códigos de produto já foram removidos pelo chamador e letras adjacentes são
# necessárias para reconhecer esse modo natural de escrever a medida.
_PADRAO_NUMERO_CONSULTA = re.compile(rf"(?<![\w,.])({_NUMERO})(?![\d,.])")
_PADRAO_MAIS_MENOS = re.compile(rf"(?<![\w,.])({_NUMERO})\s*±\s*({_NUMERO})")

# Numeração de seção de FISPQ ("9.10 Solubilidade em água", "10. ESTABILIDADE
# E REATIVIDADE") — índice do documento, nunca valor. Sem tirar, a célula
# "Densidade (g/cm³): 1,05 9.10 Solubilidade..." virava densidade de 1,05 a
# 9,10. Exige letra logo depois justamente para não confundir com um valor: no
# acervo inteiro o decimal é vírgula, então "9.10" seguido de palavra é
# numeração, não medida.
_PADRAO_NUMERACAO_DE_SECAO = (
    re.compile(r"(?<![\w,.])\d{1,2}\.\d{1,2}(?=\s*[a-z])"),
    re.compile(r"(?<![\w,.])\d{1,2}\.(?=\s+[a-z])"),
)
# Tempo de reatividade no formato do acervo: "00`09``", "00’09’’", "120`00``"
# (minutos e segundos, com apóstrofo simples/duplo, aspa curva ou crase — a
# extração de PDF alterna entre os quatro para o MESMO documento).
_PADRAO_TEMPO_MINUTO_SEGUNDO = re.compile(
    r"(\d{1,3})\s*[`'’´]\s*(\d{1,2})\s*(?:[`'’´]{2}|[\"”])"
)
_PADRAO_TEMPERATURA_QUALIFICADORA = (
    re.compile(r"\d{1,3}\s*[°º]\s*c\b"),
    re.compile(r"\b[aà]\s+\d{1,3}\s*c\b"),
)

# Acima disso não é especificação de produto: é número de laudo, lote, CAS ou
# ano solto que escapou do corte de célula.
_VALOR_MAXIMO_PLAUSIVEL = 1_000_000.0


def _para_float(token: str) -> Optional[float]:
    """Converte número na convenção pt-BR. Ponto com exatamente 3 casas é
    separador de MILHAR ("22.500" -> 22500), porque o decimal no acervo
    inteiro é vírgula."""
    token = token.strip()
    try:
        if "," in token:
            return float(token.replace(".", "").replace(",", "."))
        partes = token.split(".")
        if len(partes) == 2 and len(partes[1]) == 3:
            return float(token.replace(".", ""))
        return float(token)
    except ValueError:
        return None


def _detectar_unidade(janela: str) -> Tuple[Optional[str], str]:
    """Devolve (unidade para exibição, janela sem nenhuma ocorrência de
    unidade). A remoção é o ponto principal: unidade com expoente vira dígito
    depois da normalização e contaminaria a lista de valores."""
    achado = _PADRAO_UNIDADE.search(janela)
    unidade = None
    if achado:
        chave = re.sub(r"\s+", "", achado.group(0))
        unidade = _UNIDADE_EXIBICAO.get(chave, achado.group(0).strip())
    return unidade, _PADRAO_UNIDADE.sub(" ", janela)


# Duas palavras compridas seguidas — a assinatura de uma etiqueta de coluna que
# o parser não conhece ("Consumo Médio por Demão", "Espessura Recomendada").
_PADRAO_DUAS_PALAVRAS = re.compile(r"[a-z]{4,}\s+[a-z]{3,}")


def _cortar_no_texto_apos_os_valores(janela: str) -> str:
    """Fecha a célula quando começa uma etiqueta desconhecida depois dos
    números.

    Os cortes por rótulo conhecido e por marcador só pegam o que já está
    catalogado; o acervo tem colunas fora dessa lista, e a janela invadia a
    seguinte — achado real: "Alongamento (%) | 700,0 Consumo Médio por Demão |
    0,4 Kg/m2*" virava um alongamento de 0,4 a 700%.

    Só corta DEPOIS do primeiro número: antes dele o texto ainda pode ser a
    continuação do próprio rótulo ou a descrição do ensaio."""
    primeiro_numero = _PADRAO_NUMERO.search(janela)
    if not primeiro_numero:
        return janela
    etiqueta = _PADRAO_DUAS_PALAVRAS.search(janela, primeiro_numero.end())
    return janela[:etiqueta.start()] if etiqueta else janela


def _remover_temperatura_qualificadora(janela: str, propriedade: str) -> str:
    """Tira o "a 25 °C" que qualifica a CONDIÇÃO do ensaio ("Viscosidade, 25°C
    cPs 800 a 900") — sem isso o 25 entra como se fosse o valor da propriedade
    e todo produto do acervo "casa" viscosidade 25.

    Roda ANTES de `_detectar_unidade`, e essa ordem é o bug que esta função já
    custou uma vez: `°C` também é uma unidade válida, então detectar unidade
    primeiro apagava justamente o `°C` que marca o número como temperatura,
    deixando o `25` órfão e indistinguível de um valor de especificação."""
    if propriedade == "temperatura":
        return janela
    for padrao in _PADRAO_TEMPERATURA_QUALIFICADORA:
        janela = padrao.sub(" ", janela)
    return janela


def _valores_da_janela(
    janela: str, tipo: Optional[str] = None
) -> Tuple[List[float], Optional[str], bool]:
    """Devolve (valores, unidade forçada, faixa declarada pelo documento).

    A unidade só é forçada no caso de tempo: o cabeçalho da coluna no acervo é
    "Min." mas o dado ali é minuto`segundo``, então herdar "min" como unidade
    daria um número certo com rótulo errado.

    "Faixa declarada" marca a leitura vinda de um `A ± B` — ali os dois limites
    são do próprio documento, então ela nunca é descartada pela checagem de
    plausibilidade, mesmo quando fica larga ("Viscosidade 12,5 ± 12,5")."""
    if tipo == "tempo":
        segundos = [
            int(minutos) * 60 + int(sobra)
            for minutos, sobra in _PADRAO_TEMPO_MINUTO_SEGUNDO.findall(janela)
        ]
        if segundos:
            return segundos[:6], "s", False

    mais_menos = _PADRAO_MAIS_MENOS.search(janela)
    if mais_menos:
        nominal = _para_float(mais_menos.group(1))
        tolerancia = _para_float(mais_menos.group(2))
        if nominal is not None and tolerancia is not None:
            # Tolerância maior que o nominal existe no acervo ("Viscosidade
            # 12,5 ± 12,5"). O piso é 0: especificação negativa não significa
            # nada em nenhuma destas propriedades.
            return [max(0.0, nominal - tolerancia), nominal, nominal + tolerancia], None, True

    valores = []
    for token in _PADRAO_NUMERO.findall(janela):
        valor = _para_float(token)
        if valor is not None and 0 <= valor <= _VALOR_MAXIMO_PLAUSIVEL:
            valores.append(valor)
        if len(valores) >= 6:
            break
    return valores, None, False


# Razão máxima entre o maior e o menor número de uma mesma célula. Uma
# especificação real é uma faixa de tolerância, não uma ordem de grandeza:
# nos formatos do acervo a razão fica perto de 1 ("32,1 34,9 34,0" = 1,09;
# "800 a 1200" = 1,5) e o pior caso legítimo observado é o teor de água
# ("---- 0,10 0,03" = 3,3).
#
# Achado rodando a busca contra a coleção real (10.499 trechos): quando a
# janela atravessa a coluna vizinha sem um ponto de corte reconhecível, sai
# coisa como "hidroxila 60 a 16500 mgKOH/g" — e aí o produto casa com
# QUALQUER valor pedido dentro dessa faixa, que é exatamente o erro silencioso
# que esta busca existe para evitar. Leitura assim é descartada: sem leitura é
# melhor que leitura inventada.
_RAZAO_MAXIMA_DA_FAIXA = 10.0


# Palavra minúscula de 3+ letras entre o rótulo e o primeiro número.
_PADRAO_PALAVRA_DE_PROSA = re.compile(r"[a-z]{3,}")


def _celula_de_tabela(janela: str) -> bool:
    """True quando o que vem depois do rótulo parece uma CÉLULA de tabela, e
    não uma frase que por acaso cita a propriedade.

    Numa tabela o número vem quase colado no rótulo, separado no máximo por
    unidade, pontuação e pipes ("Índice de hidroxila(mg koh/g) 32,1 34,9",
    "Teor de água % ---- 0,10"). Em prosa há palavras no meio.

    Achado rodando a busca contra a coleção real: o boletim do FLEXX AG 20103
    diz "pesar a quantidade de FLEXX A G 20 103 necessária" logo depois de
    "densidade do bloco desejada" — e a extração de PDF ainda parte o código do
    produto em "20 103". Sem esta checagem, o parser lia uma densidade de "20 a
    103" a partir do NOME DO PRODUTO, e o mesmo acontecia em toda a família AG
    (20106, 20108, 20133...), enchendo a busca por densidade de produtos que
    nem declaram densidade.

    Recebe a janela já sem unidade e sem o qualificador de temperatura — senão
    "cps"/"koh" contariam como palavra de prosa."""
    primeiro_numero = _PADRAO_NUMERO.search(janela)
    if not primeiro_numero:
        return False
    return not _PADRAO_PALAVRA_DE_PROSA.search(janela[:primeiro_numero.start()])


def _leitura_e_plausivel(valores: List[float], faixa_declarada: bool) -> bool:
    if faixa_declarada:
        # "A ± B" com B >= A não é tolerância, é ruído de extração (colunas
        # coladas): "Teor de NCO 21 ± 21" viraria "0 a 42 %", faixa que casa
        # com quase qualquer valor pedido.
        return min(valores) > 0 or max(valores) == 0
    if len(valores) == 1:
        return True
    minimo, maximo = min(valores), max(valores)
    if minimo <= 0:
        # Zero ao lado de outros números numa célula de especificação é sinal
        # de que a janela pegou outra coluna, não de que o mínimo é zero.
        return maximo <= 0
    return maximo / minimo <= _RAZAO_MAXIMA_DA_FAIXA


def extrair_especificacoes(
    content: str, propriedades: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Lê a(s) tabela(s) de especificação de um trecho de documento.

    Cada item devolvido é a leitura de UMA célula: a propriedade canônica, a
    faixa de valores encontrada ali, a unidade e o trecho literal (evidência).
    `propriedades` restringe a busca — é o que deixa a varredura do acervo
    inteiro barata quando a pergunta é sobre uma propriedade só."""
    if not content:
        return []

    normalizado = _remover_numeracao_de_secao(_normalizar_alinhado(content))
    if propriedades:
        padroes = [
            (prop, _PADRAO_POR_PROPRIEDADE[prop])
            for prop in propriedades
            if prop in _PADRAO_POR_PROPRIEDADE
        ]
    else:
        padroes = [(None, _PADRAO_TODOS_ROTULOS)]

    achados: List[Dict[str, Any]] = []
    for propriedade_alvo, padrao in padroes:
        for ocorrencia in padrao.finditer(normalizado):
            rotulo = re.sub(r"\s+", " ", ocorrencia.group(1))
            propriedade = propriedade_alvo or _PROPRIEDADE_DO_ROTULO.get(rotulo)
            if not propriedade:
                continue

            inicio = ocorrencia.end()
            janela = normalizado[inicio:inicio + _JANELA_APOS_ROTULO]

            proximo_rotulo = _PADRAO_TODOS_ROTULOS.search(janela)
            if proximo_rotulo and proximo_rotulo.start() > 0:
                janela = janela[:proximo_rotulo.start()]
            for marcador in _MARCADORES_FIM_DE_CELULA:
                posicao = janela.find(marcador)
                if posicao > 0:
                    janela = janela[:posicao]
            janela = _cortar_no_texto_apos_os_valores(janela)

            tipo = PROPRIEDADES[propriedade].get("tipo")
            janela_util = _remover_temperatura_qualificadora(janela, propriedade)
            # Algumas escalas fazem parte do próprio rótulo ("Dureza Shore A")
            # e, portanto, ficam antes da janela de valores. Preserve essa
            # informação para não considerar Shore A e Shore D equivalentes.
            unidade_rotulo, _ = _detectar_unidade(ocorrencia.group(0))
            unidade_janela, janela_sem_unidade = _detectar_unidade(janela_util)
            unidade = unidade_rotulo or unidade_janela
            if not _celula_de_tabela(janela_sem_unidade):
                continue
            valores, unidade_forcada, faixa_declarada = _valores_da_janela(
                janela_sem_unidade, tipo
            )
            if not valores or not _leitura_e_plausivel(valores, faixa_declarada):
                continue
            unidade = unidade_forcada or unidade

            fim_trecho = min(inicio + len(janela), len(content))
            achados.append({
                "propriedade": propriedade,
                "titulo": PROPRIEDADES[propriedade]["titulo"],
                "rotulo_no_documento": content[ocorrencia.start():ocorrencia.end()].strip(),
                "valores": valores,
                "minimo": min(valores),
                "maximo": max(valores),
                "unidade": unidade,
                "trecho": re.sub(r"\s+", " ", content[ocorrencia.start():fim_trecho]).strip(),
            })
    return achados


# ---------------------------------------------------------------------------
# Interpretação da pergunta
# ---------------------------------------------------------------------------

_TERMOS_CONSULTA = [
    (termo, prop)
    for prop, dados in PROPRIEDADES.items()
    for termo in dados["termos_consulta"]
]
_PADRAO_TERMOS_CONSULTA = _padrao_de_rotulos([t for t, _ in _TERMOS_CONSULTA])
_PROPRIEDADE_DO_TERMO = {termo: prop for termo, prop in _TERMOS_CONSULTA}

_PADRAO_ENTRE = re.compile(
    r"\bentre\s+(\d{1,7}(?:[.,]\d{1,4})?)\s*(?:e|a|ate)\s+(\d{1,7}(?:[.,]\d{1,4})?)"
)
_PADRAO_FAIXA = re.compile(
    r"\b(?:de\s+)?(\d{1,7}(?:[.,]\d{1,4})?)\s*(?:a|ate|-|–)\s*(\d{1,7}(?:[.,]\d{1,4})?)"
)
_EXPRESSOES_MAIOR = (
    "acima de", "maior que", "maior do que", "superior a", "a partir de",
    "no minimo", "pelo menos", "mais de", ">=", ">",
)
_EXPRESSOES_MENOR = (
    "abaixo de", "menor que", "menor do que", "inferior a", "no maximo",
    "menos de", "<=", "<",
)

# Tolerância padrão de ±5% quando o vendedor pede um valor pontual ("hidroxila
# de 180"). Ele quase nunca quer o valor exato ao decimal — quer o produto que
# ENTREGA aquele patamar. A faixa declarada no documento vence a tolerância:
# se a especificação é 175–185, um pedido de 180 casa por CONTER o valor, não
# por aproximação.
TOLERANCIA_PADRAO_PERCENTUAL = 5.0

_PADRAO_UNIDADE_DE_TEMPO = re.compile(
    r"\b(segundos?|seg\b|s\b|minutos?|min\b|horas?|h\b)"
)
_FATOR_PARA_SEGUNDOS = {"s": 1, "seg": 1, "segundo": 1, "segundos": 1,
                        "min": 60, "minuto": 60, "minutos": 60,
                        "h": 3600, "hora": 3600, "horas": 3600}


def _fator_de_tempo(texto_apos_o_numero: str) -> int:
    """Multiplicador para levar o número da pergunta a SEGUNDOS: "2 minutos"
    -> 60, "45 segundos" -> 1.

    Sem unidade o fator é 1 (segundo) — é assim que o vendedor fala do tempo
    de creme e de reação, que dominam o acervo ("creme de 10", "reação de
    45"). A unidade só conta se vier COLADA no número; um "min" que aparece 20
    caracteres depois pertence a outra frase."""
    achado = _PADRAO_UNIDADE_DE_TEMPO.search(texto_apos_o_numero.strip())
    if not achado or achado.start() > 1:
        return 1
    return _FATOR_PARA_SEGUNDOS[achado.group(1).rstrip(".")]


def _unidade_da_consulta(rotulo: str, antes: str, depois: str) -> Optional[str]:
    """Prefere a unidade depois da propriedade e aceita a forma valor+unidade antes."""
    unidade_depois = _detectar_unidade(f"{rotulo} {depois}")[0]
    return unidade_depois or _detectar_unidade(antes[-40:])[0]


def interpretar_consulta_especificacao(
    query: str, codigos_produto: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """Traduz "quero um produto com hidroxila de 180" em critério de busca.

    Devolve None quando a pergunta NÃO é uma busca por especificação — é o
    que impede a varredura cara de disparar em pergunta comum. Exige as duas
    coisas juntas: nome de propriedade conhecida E um número.

    `codigos_produto`: códigos já detectados na pergunta pelo chamador (ex:
    "AG 2032"). São removidos antes de procurar o número, senão "qual a
    hidroxila do AG 2032" viraria uma busca por hidroxila = 2032. Vem pronto
    do chamador em vez de ser redetectado aqui para não duplicar a regra de
    nomenclatura, que já vive em app.rag.engine.
    """
    if not query:
        return None

    texto = _normalizar_alinhado(query)
    for codigo in codigos_produto or []:
        texto = texto.replace(_normalizar_alinhado(codigo), " ")

    ocorrencia = _PADRAO_TERMOS_CONSULTA.search(texto)
    if not ocorrencia:
        return None
    propriedade = _PROPRIEDADE_DO_TERMO.get(re.sub(r"\s+", " ", ocorrencia.group(1)))
    if not propriedade:
        return None

    depois = texto[ocorrencia.end():]
    antes = texto[:ocorrencia.start()]

    eh_tempo = PROPRIEDADES[propriedade].get("tipo") == "tempo"

    entre = _PADRAO_ENTRE.search(depois) or _PADRAO_ENTRE.search(antes)
    faixa = entre or _PADRAO_FAIXA.search(depois)
    if faixa:
        minimo, maximo = _para_float(faixa.group(1)), _para_float(faixa.group(2))
        if minimo is not None and maximo is not None and minimo != maximo:
            if eh_tempo:
                # A unidade vem uma vez só, no fim ("entre 8 e 12 segundos") —
                # ela vale para os DOIS extremos, não só para o último.
                fator = _fator_de_tempo(faixa.string[faixa.end():])
                minimo, maximo = minimo * fator, maximo * fator
            return {
                "propriedade": propriedade,
                "operador": "entre",
                "valor": min(minimo, maximo),
                "valor_maximo": max(minimo, maximo),
                "unidade": (
                    "s" if eh_tempo
                    else _unidade_da_consulta(ocorrencia.group(0), antes, depois)
                ),
                "tolerancia_percentual": 0.0,
            }

    numero = _PADRAO_NUMERO_CONSULTA.search(depois)
    trecho_operador = depois[:numero.start()] if numero else ""
    if not numero:
        anteriores = list(_PADRAO_NUMERO_CONSULTA.finditer(antes))
        if not anteriores:
            return None
        numero = anteriores[-1]
        trecho_operador = antes[numero.end():]
    valor = _para_float(numero.group(1))
    if valor is None:
        return None
    if eh_tempo:
        valor *= _fator_de_tempo(numero.string[numero.end():])

    operador = "igual"
    contexto_operador = f"{antes[-40:]} {trecho_operador}"
    if any(expressao in contexto_operador for expressao in _EXPRESSOES_MAIOR):
        operador = "maior"
    elif any(expressao in contexto_operador for expressao in _EXPRESSOES_MENOR):
        operador = "menor"

    return {
        "propriedade": propriedade,
        "operador": operador,
        "valor": valor,
        "valor_maximo": None,
        "unidade": (
            "s" if eh_tempo
            else _unidade_da_consulta(ocorrencia.group(0), antes, depois)
        ),
        "tolerancia_percentual": (
            TOLERANCIA_PADRAO_PERCENTUAL if operador == "igual" else 0.0
        ),
    }


def interpretar_consulta_especificacoes(
    query: str, codigos_produto: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Extrai todos os requisitos numéricos independentes de uma pergunta.

    Cada propriedade recebe apenas o trecho até a próxima propriedade. Isso
    impede que o número de dureza seja atribuído à densidade, por exemplo.
    A interface antiga continua disponível e representa o primeiro critério.
    """
    if not query:
        return []
    texto = _normalizar_alinhado(query)
    for codigo in codigos_produto or []:
        texto = texto.replace(_normalizar_alinhado(codigo), " ")

    ocorrencias = list(_PADRAO_TERMOS_CONSULTA.finditer(texto))
    criterios: List[Dict[str, Any]] = []
    propriedades_vistas = set()
    for indice, ocorrencia in enumerate(ocorrencias):
        fim = ocorrencias[indice + 1].start() if indice + 1 < len(ocorrencias) else len(texto)
        # Inclui o trecho desde a propriedade anterior para aceitar a ordem
        # natural "no mínimo 200 kg/m³ de densidade por imersão". O parser
        # prefere o número depois do rótulo; só usa o último número anterior
        # quando não há nenhum depois.
        inicio = ocorrencias[indice - 1].end() if indice > 0 else 0
        fragmento = texto[inicio:fim]
        criterio = interpretar_consulta_especificacao(fragmento)
        if not criterio or criterio["propriedade"] in propriedades_vistas:
            continue
        propriedades_vistas.add(criterio["propriedade"])
        criterios.append(criterio)
    return criterios


# ---------------------------------------------------------------------------
# Busca no acervo
# ---------------------------------------------------------------------------

_LIMITE_PREVIA = 10
_SEPARADOR_CAMINHO = re.compile(r"[\\/]+")


def _tipo_documento(filename: str) -> str:
    """Boletim vale mais que laudo como REFERÊNCIA de especificação: o laudo é
    de um lote específico (regra já fixada no prompt do agente). O tipo vai na
    resposta para o agente citar a fonte certa."""
    nome = _normalizar_alinhado(filename or "")
    if "boletim" in nome:
        return "Boletim Técnico"
    if "fispq" in nome:
        return "FISPQ"
    if "certificado" in nome or "analise" in nome or "laudo" in nome:
        return "Certificado/Laudo de lote"
    return "Outro"


_PRIORIDADE_DOCUMENTO = {
    "Boletim Técnico": 0, "Certificado/Laudo de lote": 1, "Outro": 2, "FISPQ": 3,
}


def _atende_criterio(
    especificacao: Dict[str, Any],
    operador: str,
    valor: float,
    valor_maximo: Optional[float],
    tolerancia_percentual: float,
) -> bool:
    minimo, maximo = especificacao["minimo"], especificacao["maximo"]
    # Para afirmar que o PRODUTO atende a um limite, toda a faixa declarada
    # precisa estar do lado solicitado. Usar apenas um extremo favorável fazia
    # 18,6–32,9 aparecer como "abaixo de 32" e 200–230 como "abaixo de 220".
    if operador == "maior":
        return minimo >= valor
    if operador == "menor":
        return maximo <= valor
    if operador == "entre" and valor_maximo is not None:
        return minimo >= valor and maximo <= valor_maximo
    folga = abs(valor) * (tolerancia_percentual / 100.0)
    return (minimo - folga) <= valor <= (maximo + folga)


def _distancia_do_alvo(especificacao: Dict[str, Any], valor: float) -> float:
    minimo, maximo = especificacao["minimo"], especificacao["maximo"]
    if minimo <= valor <= maximo:
        return 0.0
    return min(abs(minimo - valor), abs(maximo - valor))


def _formatar_segundos(segundos: float) -> str:
    """Tempo em linguagem de chão de fábrica: "45 s", "2 min 42 s", "2 h".
    O vendedor lê "7200 s" e não sabe se é rápido ou lento."""
    total = int(round(segundos))
    if total < 60:
        return f"{total} s"
    if total < 3600:
        minutos, resto = divmod(total, 60)
        return f"{minutos} min" if resto == 0 else f"{minutos} min {resto} s"
    horas, resto = divmod(total, 3600)
    minutos = resto // 60
    return f"{horas} h" if minutos == 0 else f"{horas} h {minutos} min"


def _formatar_valores(especificacao: Dict[str, Any]) -> str:
    if PROPRIEDADES.get(especificacao["propriedade"], {}).get("tipo") == "tempo":
        fmt = _formatar_segundos
    else:
        def fmt(numero: float) -> str:
            texto = f"{numero:.4f}".rstrip("0").rstrip(".")
            return (texto or "0").replace(".", ",")

    if especificacao["minimo"] == especificacao["maximo"]:
        return fmt(especificacao["minimo"])
    return f"{fmt(especificacao['minimo'])} a {fmt(especificacao['maximo'])}"


def _unidade_para_exibicao(especificacao: Dict[str, Any]) -> str:
    """Tempo já sai formatado com a unidade embutida ("2 min 42 s") — repetir
    o "s" ao lado viraria "2 min 42 s s"."""
    if PROPRIEDADES.get(especificacao["propriedade"], {}).get("tipo") == "tempo":
        return ""
    return especificacao["unidade"] or ""


def _descrever_criterio(
    propriedade: str,
    operador: str,
    valor: float,
    valor_maximo: Optional[float],
    tolerancia_percentual: float,
) -> str:
    titulo = PROPRIEDADES[propriedade]["titulo"]
    if PROPRIEDADES[propriedade].get("tipo") == "tempo":
        fmt = _formatar_segundos
    else:
        sufixo = f" {PROPRIEDADES[propriedade]['unidade_tipica']}".rstrip()

        def fmt(numero: float) -> str:
            return f"{numero:g}{sufixo}"

    if operador == "maior":
        return f"{titulo} maior ou igual a {fmt(valor)}"
    if operador == "menor":
        return f"{titulo} menor ou igual a {fmt(valor)}"
    if operador == "entre" and valor_maximo is not None:
        return f"{titulo} entre {fmt(valor)} e {fmt(valor_maximo)}"
    if tolerancia_percentual:
        return f"{titulo} ≈ {fmt(valor)} (tolerância de ±{tolerancia_percentual:g}%)"
    return f"{titulo} = {fmt(valor)}"


def buscar_produtos_por_especificacao(
    propriedade: str,
    valor: float,
    operador: str = "igual",
    valor_maximo: Optional[float] = None,
    tolerancia_percentual: float = TOLERANCIA_PADRAO_PERCENTUAL,
    listar_todos: bool = False,
) -> Dict[str, Any]:
    """Varre o acervo indexado e devolve os produtos cuja especificação
    `propriedade` atende ao critério pedido.

    Por que varredura completa e não top-k: a pergunta é "TODOS os produtos
    com hidroxila 180", e um top-k de similaridade devolveria um punhado
    arbitrário de trechos que falam de hidroxila — a contagem sairia errada e
    produtos válidos ficariam de fora sem ninguém perceber. Mesma decisão já
    tomada em app.rag.catalog_stats.listar_produtos_por_aplicacao.

    Também devolve `faixa_no_acervo`: mínimo e máximo da propriedade em TODO o
    acervo. Quando nada casa, é isso que transforma um "não encontrei" seco em
    resposta útil ("não há produto com hidroxila 180; o acervo vai de 20 a 415
    mgKOH/g") — e custa zero, os números já foram lidos na varredura.
    """
    if propriedade not in PROPRIEDADES:
        return {
            "erro": f"Propriedade '{propriedade}' não reconhecida.",
            "propriedades_suportadas": sorted(PROPRIEDADES.keys()),
        }

    melhor_por_produto: Dict[str, Dict[str, Any]] = {}
    minimo_acervo: Optional[float] = None
    maximo_acervo: Optional[float] = None
    unidade_acervo: Optional[str] = None

    try:
        client = get_qdrant_client()
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath", "filename", "content"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                payload = ponto.payload or {}
                filepath = payload.get("filepath") or ""
                produto = _produto_do_filepath(filepath)
                if not produto:
                    continue

                filename = payload.get("filename") or _SEPARADOR_CAMINHO.split(filepath)[-1]
                for especificacao in extrair_especificacoes(
                    payload.get("content") or "", propriedades=[propriedade]
                ):
                    minimo_acervo = (
                        especificacao["minimo"] if minimo_acervo is None
                        else min(minimo_acervo, especificacao["minimo"])
                    )
                    maximo_acervo = (
                        especificacao["maximo"] if maximo_acervo is None
                        else max(maximo_acervo, especificacao["maximo"])
                    )
                    unidade_acervo = unidade_acervo or especificacao["unidade"]

                    if not _atende_criterio(
                        especificacao, operador, valor, valor_maximo, tolerancia_percentual
                    ):
                        continue

                    tipo = _tipo_documento(filename)
                    candidato = {
                        "produto": produto,
                        "valores": _formatar_valores(especificacao),
                        "unidade": _unidade_para_exibicao(especificacao),
                        "documento": filename,
                        "tipo_documento": tipo,
                        "trecho": especificacao["trecho"][:220],
                        "_ordem": (
                            _PRIORIDADE_DOCUMENTO.get(tipo, 9),
                            _distancia_do_alvo(especificacao, valor),
                        ),
                    }
                    atual = melhor_por_produto.get(produto)
                    if atual is None or candidato["_ordem"] < atual["_ordem"]:
                        melhor_por_produto[produto] = candidato
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    encontrados = sorted(
        melhor_por_produto.values(),
        key=lambda item: (item["_ordem"][1], item["produto"]),
    )
    for item in encontrados:
        item.pop("_ordem", None)

    limite = None if listar_todos else _LIMITE_PREVIA
    return {
        "propriedade": propriedade,
        "propriedade_titulo": PROPRIEDADES[propriedade]["titulo"],
        "criterio": _descrever_criterio(
            propriedade, operador, valor, valor_maximo, tolerancia_percentual
        ),
        "total": len(encontrados),
        "produtos": encontrados if limite is None else encontrados[:limite],
        "truncado": limite is not None and len(encontrados) > limite,
        "faixa_no_acervo": (
            None if minimo_acervo is None
            else {
                "minimo": minimo_acervo,
                "maximo": maximo_acervo,
                "unidade": unidade_acervo or PROPRIEDADES[propriedade]["unidade_tipica"],
            }
        ),
        "aviso": (
            "Valores lidos automaticamente da tabela do documento. Boletim Técnico é a "
            "especificação de referência; Certificado/Laudo vale para o lote analisado. "
            "Confirme no documento citado antes de fechar a proposta."
        ),
    }


def _unidades_compativeis(unidade_pedida: Optional[str], unidade_documento: Optional[str]) -> bool:
    """Compara escalas quando ambas estão explícitas; ausência permanece desconhecida."""
    if not unidade_pedida or not unidade_documento:
        return True
    aliases = {
        "cps": "viscosidade", "mpa.s": "viscosidade",
        "shore a": "shore a", "shore d": "shore d",
        "kg/m³": "kg/m³", "g/cm³": "g/cm³",
    }
    pedida = aliases.get(unidade_pedida.lower(), unidade_pedida.lower())
    documento = aliases.get(unidade_documento.lower(), unidade_documento.lower())
    return pedida == documento


def buscar_produtos_por_especificacoes(
    criterios: List[Dict[str, Any]], listar_todos: bool = False,
) -> Dict[str, Any]:
    """Aplica vários requisitos obrigatórios na mesma varredura do acervo.

    Um produto só entra quando possui evidência compatível para TODOS os
    critérios. Ausência de uma propriedade não é tratada como atendimento.
    """
    if len(criterios) < 2:
        return {"erro": "Informe pelo menos dois critérios técnicos."}
    invalidas = [c.get("propriedade") for c in criterios if c.get("propriedade") not in PROPRIEDADES]
    if invalidas:
        return {
            "erro": f"Propriedade(s) não reconhecida(s): {', '.join(map(str, invalidas))}.",
            "propriedades_suportadas": sorted(PROPRIEDADES.keys()),
        }

    melhores: List[Dict[str, Dict[str, Any]]] = [{} for _ in criterios]
    faixas = [{"minimo": None, "maximo": None, "unidade": None} for _ in criterios]
    try:
        client = get_qdrant_client()
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath", "filename", "content"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                payload = ponto.payload or {}
                filepath = payload.get("filepath") or ""
                produto = _produto_do_filepath(filepath)
                if not produto:
                    continue
                filename = payload.get("filename") or _SEPARADOR_CAMINHO.split(filepath)[-1]
                content = payload.get("content") or ""
                for indice, criterio in enumerate(criterios):
                    for especificacao in extrair_especificacoes(
                        content, propriedades=[criterio["propriedade"]]
                    ):
                        faixa = faixas[indice]
                        faixa["minimo"] = (
                            especificacao["minimo"] if faixa["minimo"] is None
                            else min(faixa["minimo"], especificacao["minimo"])
                        )
                        faixa["maximo"] = (
                            especificacao["maximo"] if faixa["maximo"] is None
                            else max(faixa["maximo"], especificacao["maximo"])
                        )
                        faixa["unidade"] = faixa["unidade"] or especificacao["unidade"]
                        if not _unidades_compativeis(
                            criterio.get("unidade"), especificacao.get("unidade")
                        ):
                            continue
                        if not _atende_criterio(
                            especificacao, criterio["operador"], criterio["valor"],
                            criterio.get("valor_maximo"), criterio["tolerancia_percentual"],
                        ):
                            continue
                        tipo = _tipo_documento(filename)
                        candidato = {
                            "propriedade": criterio["propriedade"],
                            "propriedade_titulo": PROPRIEDADES[criterio["propriedade"]]["titulo"],
                            "valores": _formatar_valores(especificacao),
                            "unidade": _unidade_para_exibicao(especificacao),
                            "documento": filename,
                            "tipo_documento": tipo,
                            "trecho": especificacao["trecho"][:220],
                            "_ordem": (
                                _PRIORIDADE_DOCUMENTO.get(tipo, 9),
                                _distancia_do_alvo(especificacao, criterio["valor"]),
                            ),
                        }
                        atual = melhores[indice].get(produto)
                        if atual is None or candidato["_ordem"] < atual["_ordem"]:
                            melhores[indice][produto] = candidato
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    produtos_completos = set(melhores[0])
    for por_produto in melhores[1:]:
        produtos_completos.intersection_update(por_produto)

    encontrados = []
    for produto in produtos_completos:
        requisitos = [por_produto[produto].copy() for por_produto in melhores]
        ordem = sum(item["_ordem"][1] for item in requisitos)
        for item in requisitos:
            item.pop("_ordem", None)
        encontrados.append({"produto": produto, "requisitos": requisitos, "_ordem": ordem})
    encontrados.sort(key=lambda item: (item["_ordem"], item["produto"]))
    for item in encontrados:
        item.pop("_ordem", None)

    descricoes = []
    for criterio, faixa in zip(criterios, faixas):
        descricoes.append({
            "propriedade": criterio["propriedade"],
            "criterio": _descrever_criterio(
                criterio["propriedade"], criterio["operador"], criterio["valor"],
                criterio.get("valor_maximo"), criterio["tolerancia_percentual"],
            ),
            "faixa_no_acervo": (
                None if faixa["minimo"] is None else {
                    "minimo": faixa["minimo"], "maximo": faixa["maximo"],
                    "unidade": faixa["unidade"] or PROPRIEDADES[criterio["propriedade"]]["unidade_tipica"],
                }
            ),
        })
    limite = None if listar_todos else _LIMITE_PREVIA
    return {
        "criterios": descricoes,
        "total": len(encontrados),
        "produtos": encontrados if limite is None else encontrados[:limite],
        "truncado": limite is not None and len(encontrados) > limite,
        "aviso": (
            "O produto só foi incluído quando todos os requisitos foram encontrados e atendidos. "
            "Boletim Técnico é a especificação de referência; confirme os documentos citados."
        ),
    }


def buscar_produtos_por_aplicacao_e_especificacoes(
    termos_aplicacao: List[str],
    criterios: List[Dict[str, Any]],
    listar_todos: bool = False,
) -> Dict[str, Any]:
    """Cruza aplicação e números comprovados no mesmo Boletim Técnico.

    A chave da interseção é ``(produto, documento)``. Isso evita aprovar um
    produto porque um boletim antigo menciona a aplicação enquanto outro
    documento, sem aquela aplicação, contém um valor compatível.
    """
    termos = [termo.strip() for termo in termos_aplicacao if termo and termo.strip()]
    if not termos:
        return {"erro": "Informe a aplicação desejada."}
    if not criterios:
        return {"erro": "Informe pelo menos um critério técnico."}
    invalidas = [
        criterio.get("propriedade")
        for criterio in criterios
        if criterio.get("propriedade") not in PROPRIEDADES
    ]
    if invalidas:
        return {
            "erro": f"Propriedade(s) não reconhecida(s): {', '.join(map(str, invalidas))}.",
            "propriedades_suportadas": sorted(PROPRIEDADES.keys()),
        }

    aplicacoes: Dict[Tuple[str, str], set] = {}
    melhores: List[Dict[Tuple[str, str], Dict[str, Any]]] = [
        {} for _ in criterios
    ]
    faixas = [
        {"minimo": None, "maximo": None, "unidade": None}
        for _ in criterios
    ]

    try:
        client = get_qdrant_client()
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath", "filename", "content"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                payload = ponto.payload or {}
                filepath = payload.get("filepath") or ""
                produto = _produto_do_filepath(filepath)
                if not produto:
                    continue
                filename = payload.get("filename") or _SEPARADOR_CAMINHO.split(filepath)[-1]
                if _tipo_documento(filename) != "Boletim Técnico":
                    continue

                content = payload.get("content") or ""
                chave = (produto, filename)
                termos_encontrados = {
                    termo for termo in termos if _termo_bate_no_conteudo(termo, content)
                }
                if termos_encontrados:
                    aplicacoes.setdefault(chave, set()).update(termos_encontrados)

                for indice, criterio in enumerate(criterios):
                    for especificacao in extrair_especificacoes(
                        content, propriedades=[criterio["propriedade"]]
                    ):
                        faixa = faixas[indice]
                        faixa["minimo"] = (
                            especificacao["minimo"] if faixa["minimo"] is None
                            else min(faixa["minimo"], especificacao["minimo"])
                        )
                        faixa["maximo"] = (
                            especificacao["maximo"] if faixa["maximo"] is None
                            else max(faixa["maximo"], especificacao["maximo"])
                        )
                        faixa["unidade"] = faixa["unidade"] or especificacao["unidade"]
                        if not _unidades_compativeis(
                            criterio.get("unidade"), especificacao.get("unidade")
                        ):
                            continue
                        if not _atende_criterio(
                            especificacao,
                            criterio["operador"],
                            criterio["valor"],
                            criterio.get("valor_maximo"),
                            criterio["tolerancia_percentual"],
                        ):
                            continue
                        candidato = {
                            "propriedade": criterio["propriedade"],
                            "propriedade_titulo": PROPRIEDADES[criterio["propriedade"]]["titulo"],
                            "valores": _formatar_valores(especificacao),
                            "unidade": _unidade_para_exibicao(especificacao),
                            "documento": filename,
                            "tipo_documento": "Boletim Técnico",
                            "trecho": especificacao["trecho"][:220],
                            "_ordem": _distancia_do_alvo(
                                especificacao, criterio["valor"]
                            ),
                        }
                        atual = melhores[indice].get(chave)
                        if atual is None or candidato["_ordem"] < atual["_ordem"]:
                            melhores[indice][chave] = candidato
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    documentos_compativeis = set(aplicacoes)
    for por_documento in melhores:
        documentos_compativeis.intersection_update(por_documento)

    melhor_documento_por_produto: Dict[str, Dict[str, Any]] = {}
    for produto, filename in documentos_compativeis:
        chave = (produto, filename)
        requisitos = [por_documento[chave].copy() for por_documento in melhores]
        ordem = sum(item.pop("_ordem") for item in requisitos)
        candidato = {
            "produto": produto,
            "aplicacao": {
                "documento": filename,
                "termos_encontrados": sorted(aplicacoes[chave]),
            },
            "requisitos": requisitos,
            "_ordem": ordem,
        }
        atual = melhor_documento_por_produto.get(produto)
        if atual is None or candidato["_ordem"] < atual["_ordem"]:
            melhor_documento_por_produto[produto] = candidato

    encontrados = sorted(
        melhor_documento_por_produto.values(),
        key=lambda item: (item["_ordem"], item["produto"]),
    )
    for item in encontrados:
        item.pop("_ordem", None)

    descricoes = []
    for criterio, faixa in zip(criterios, faixas):
        descricoes.append({
            "propriedade": criterio["propriedade"],
            "criterio": _descrever_criterio(
                criterio["propriedade"], criterio["operador"], criterio["valor"],
                criterio.get("valor_maximo"), criterio["tolerancia_percentual"],
            ),
            "faixa_no_acervo": (
                None if faixa["minimo"] is None else {
                    "minimo": faixa["minimo"],
                    "maximo": faixa["maximo"],
                    "unidade": faixa["unidade"]
                    or PROPRIEDADES[criterio["propriedade"]]["unidade_tipica"],
                }
            ),
        })

    limite = None if listar_todos else _LIMITE_PREVIA
    return {
        "aplicacao": {"termos_buscados": termos},
        "criterios": descricoes,
        "total": len(encontrados),
        "produtos": encontrados if limite is None else encontrados[:limite],
        "truncado": limite is not None and len(encontrados) > limite,
        "aviso": (
            "O produto só foi incluído quando aplicação e especificação foram "
            "comprovadas no mesmo Boletim Técnico. Confirme o documento citado "
            "antes de fechar a proposta."
        ),
    }


# ---------------------------------------------------------------------------
# Ficha estruturada dos documentos já recuperados
# ---------------------------------------------------------------------------

_MAXIMO_DOCUMENTOS_NA_FICHA = 8
_MAXIMO_PROPRIEDADES_POR_DOCUMENTO = 12


def resumir_especificacoes_dos_documentos(docs: List[Dict[str, Any]]) -> str:
    """Bloco de leitura estruturada das tabelas dos documentos recuperados.

    Resolve um problema concreto: o texto extraído do PDF chega ao prompt com
    as colunas embaralhadas, e o LLM erra qual número pertence a qual
    propriedade. Aqui a leitura é feita uma vez, por regra, e o agente recebe o
    par propriedade→valor pronto — sem substituir o texto original, que
    continua no contexto para ele conferir."""
    if not docs:
        return ""

    por_documento: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for doc in docs[:_MAXIMO_DOCUMENTOS_NA_FICHA * 4]:
        filename = doc.get("filename") or "documento sem nome"
        for especificacao in extrair_especificacoes(doc.get("content") or ""):
            propriedades = por_documento.setdefault(filename, {})
            # A mesma propriedade pode aparecer em vários chunks do mesmo
            # arquivo (tabela quebrada pelo chunking); a primeira leitura basta.
            propriedades.setdefault(especificacao["propriedade"], especificacao)

    documentos = [(nome, props) for nome, props in por_documento.items() if props]
    if not documentos:
        return ""

    linhas = [
        "",
        "📋 LEITURA ESTRUTURADA DAS TABELAS DE ESPECIFICAÇÃO (extraída por regra dos mesmos "
        "documentos acima — o texto corrido pode vir com as colunas embaralhadas pela extração "
        "do PDF; quando os dois divergirem, PREFIRA os valores desta lista e cite o documento):",
    ]
    for nome, propriedades in documentos[:_MAXIMO_DOCUMENTOS_NA_FICHA]:
        itens = list(propriedades.values())[:_MAXIMO_PROPRIEDADES_POR_DOCUMENTO]
        detalhes = "; ".join(
            f"{item['titulo']}: {_formatar_valores(item)}"
            + (f" {_unidade_para_exibicao(item)}" if _unidade_para_exibicao(item) else "")
            for item in itens
        )
        linhas.append(f"- [{nome}] {detalhes}")
    return "\n".join(linhas)
