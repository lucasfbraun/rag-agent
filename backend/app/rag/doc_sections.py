"""
Seções do Boletim Técnico — a pergunta é sobre um ASSUNTO do produto, não
sobre um número nem sobre um nome: "quais as vantagens do AG 2032", "como
armazenar o CAT 136", "qual a reatividade desse produto", "em que embalagem
vem".

Por que existe: um boletim vira ~5 a 15 chunks no índice, e a busca vetorial
escolhe entre eles por similaridade geral. Perguntar "vantagens do X" trazia
o chunk da FISPQ ou o da tabela de especificação — texto do produto certo,
seção errada — e o agente respondia com o que tinha na mão. Aqui a seção
pedida vira um filtro explícito de recuperação e uma instrução explícita no
contexto.

Os rótulos abaixo são os cabeçalhos REAIS observados na coleção
`pu_products_catalog` em produção (boletins FLEXX, linha Polimper/Pró e
FISPQs), não um padrão idealizado — daí variações como "SEGURANÇA E
ARMAZENAMENTO" (boletim) e "MANUSEIO E ARMAZENAMENTO" (FISPQ, seção 7)
convivendo na mesma seção canônica.
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional

# `rotulos`: cabeçalho como aparece no DOCUMENTO (sem acento, minúsculo).
# `gatilhos`: como o vendedor pede aquilo na PERGUNTA.
# `termo_indice`: token único usado no filtro de texto do Qdrant — o índice de
#   `content` é tokenizado por palavra (ver app.rag.ingestion._garantir_indices_texto),
#   então frase inteira não casa; precisa ser a palavra mais discriminante da seção.
SECOES: Dict[str, Dict[str, Any]] = {
    "especificacoes": {
        "titulo": "Especificações técnicas",
        "rotulos": [
            "especificacoes tecnicas", "especificacao tecnica", "propriedades tipicas",
            "caracteristicas tecnicas", "parametros de controle", "propriedades fisico-quimicas",
        ],
        "gatilhos": [
            "especificacao", "especificacoes", "ficha tecnica", "propriedades",
            "dados tecnicos", "parametros",
        ],
        "termos_indice": ["especificações", "propriedades"],
    },
    "caracteristicas_vantagens": {
        "titulo": "Características e vantagens",
        "rotulos": [
            "caracteristicas e vantagens", "vantagens e caracteristicas",
            "caracteristicas do produto", "vantagens", "beneficios",
        ],
        "gatilhos": [
            "vantagens", "vantagem", "beneficios", "beneficio", "diferenciais",
            "diferencial", "pontos fortes", "caracteristicas", "caracteristica",
        ],
        "termos_indice": ["vantagens", "características"],
    },
    "aplicacao": {
        "titulo": "Aplicação / uso recomendado",
        "rotulos": [
            "aplicacao", "aplicacoes", "uso recomendado", "usos recomendados",
            "manuseio e aplicacao", "preparacao da superficie", "modo de uso",
        ],
        "gatilhos": [
            "aplicacao", "aplicacoes", "onde usar", "para que serve", "uso recomendado",
            "como aplicar", "modo de uso", "indicado para",
        ],
        "termos_indice": ["aplicação"],
    },
    "reatividade": {
        "titulo": "Perfil de reatividade",
        "rotulos": [
            "perfil tipico de reatividade", "perfil de reatividade", "reatividade",
            "tempos", "perfil de cura",
        ],
        "gatilhos": [
            "reatividade", "perfil de reatividade", "tempo de creme", "tempo de gel",
            "tempo de pega", "tempo de desmolde", "cura", "tempo de cura",
        ],
        "termos_indice": ["reatividade"],
    },
    "seguranca_armazenamento": {
        "titulo": "Segurança e armazenamento",
        "rotulos": [
            "seguranca e armazenamento", "manuseio e armazenamento", "armazenamento",
            "estabilidade e armazenagem", "precaucoes", "validade",
            "controle de exposicao e protecao individual",
        ],
        "gatilhos": [
            "armazenamento", "armazenar", "armazenagem", "estocagem", "estocar",
            "seguranca", "epi", "epis", "manuseio", "validade", "prazo de validade",
            "shelf life", "precaucoes", "cuidados",
        ],
        "termos_indice": ["armazenamento", "validade"],
    },
    "embalagem": {
        "titulo": "Embalagens",
        "rotulos": [
            "embalagens", "embalagem", "fornecimento", "apresentacao comercial",
        ],
        "gatilhos": [
            "embalagem", "embalagens", "tambor", "tambores", "balde", "baldes",
            "bombona", "bombonas", "ibc", "como e fornecido", "envase", "quantos quilos",
        ],
        "termos_indice": ["embalagens", "embalagem"],
    },
    "informacoes_complementares": {
        "titulo": "Informações complementares",
        "rotulos": [
            "informacoes complementares", "informacoes adicionais", "observacoes",
        ],
        "gatilhos": ["informacoes complementares", "observacoes", "recomendacoes gerais"],
        "termos_indice": ["complementares"],
    },
}

# Palavras que são PEDIDO DE SEÇÃO, não termo de conteúdo. Ficam de fora da
# extração genérica de palavras-chave do RAG (app.rag.engine): "vantagens",
# "armazenamento" e "reatividade" aparecem em quase todo boletim do acervo, e
# como palavra-chave solta só enchiam o top-k de ruído. A seção pedida é
# tratada pelo caminho próprio deste módulo, que é mais preciso.
PALAVRAS_DE_SECAO = {
    gatilho
    for dados in SECOES.values()
    for gatilho in dados["gatilhos"]
    if " " not in gatilho
} | {"vantagem", "vantagens", "beneficios", "reatividade", "armazenamento", "embalagens"}


def _normalizar(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _padrao(termos: List[str]) -> re.Pattern:
    ordenados = sorted(set(termos), key=len, reverse=True)
    corpo = "|".join(
        "".join(r"\s+" if ch == " " else re.escape(ch) for ch in termo)
        for termo in ordenados
    )
    return re.compile(rf"(?<![a-z0-9])({corpo})(?![a-z])")


_PADRAO_GATILHOS = {
    secao: _padrao(dados["gatilhos"]) for secao, dados in SECOES.items()
}
_PADRAO_ROTULOS = {
    secao: _padrao(dados["rotulos"]) for secao, dados in SECOES.items()
}
_PADRAO_QUALQUER_ROTULO = _padrao(
    [rotulo for dados in SECOES.values() for rotulo in dados["rotulos"]]
)


def detectar_secoes(query: str) -> List[str]:
    """Seções do boletim que a pergunta está pedindo, na ordem em que
    aparecem. Lista vazia quando a pergunta não é sobre uma seção — o
    caminho normal de recuperação segue intocado."""
    if not query:
        return []
    texto = _normalizar(query)
    posicoes = []
    for secao, padrao in _PADRAO_GATILHOS.items():
        achado = padrao.search(texto)
        if achado:
            posicoes.append((achado.start(), secao))
    return [secao for _, secao in sorted(posicoes)]


def termos_de_indice(secoes: List[str]) -> List[str]:
    """Tokens para o filtro de texto do Qdrant sobre `content`. Um por seção
    pedida, com as flexões que o cabeçalho realmente usa no acervo."""
    termos = []
    for secao in secoes:
        termos.extend(SECOES.get(secao, {}).get("termos_indice", []))
    return list(dict.fromkeys(termos))


def titulo_da_secao(secao: str) -> str:
    return SECOES.get(secao, {}).get("titulo", secao)


# Pontuação que fecha a frase/célula anterior — depois dela, uma palavra
# isolada como "Embalagens" ou "Validade" é cabeçalho, não continuação.
_FIM_DE_FRASE = ".:;|)]}•▪*→-–—"


def _e_cabecalho(content: str, inicio: int, rotulo: str) -> bool:
    """Distingue o CABEÇALHO da seção de uma menção solta à mesma palavra.

    Rótulo de várias palavras ("características e vantagens") só aparece como
    título, então passa direto. O caso perigoso é o rótulo de uma palavra:
    "Embalagens Baldes de 20 Kg" é cabeçalho, mas "o produto tem vantagens
    frente ao concorrente" é texto corrido — e promover esse segundo trecho ao
    topo do contexto faz o agente responder a seção errada com confiança.

    A regra olha o que vem ANTES no texto ORIGINAL (não no normalizado, que já
    perdeu a caixa): título vem depois do fim de uma frase/célula, no início do
    trecho, ou depois de algo sem letra minúscula — que é como os cabeçalhos
    reais do acervo aparecem ("...manipulado por pessoas capacitadas.
    Embalagens Baldes...", "ESPECIFICAÇÕES TÉCNICAS | FLEXX® ISO 13108
    APLICAÇÃO")."""
    if " " in rotulo:
        return True
    anterior = content[:inicio].rstrip()
    if not anterior:
        return True
    if anterior[-1] in _FIM_DE_FRASE:
        return True
    ultima_palavra = anterior.split()[-1]
    return not any(c.islower() for c in ultima_palavra)


def _ocorrencia_de_secao(content: str, secao: str) -> Optional[re.Match]:
    padrao = _PADRAO_ROTULOS.get(secao)
    if not padrao:
        return None
    normalizado = _normalizar(content)
    for achado in padrao.finditer(normalizado):
        if _e_cabecalho(content, achado.start(), re.sub(r"\s+", " ", achado.group(1))):
            return achado
    return None


def contem_secao(content: str, secao: str) -> bool:
    """True se o trecho contém o CABEÇALHO da seção — não basta citar a
    palavra no meio do texto. É o que separa o chunk que realmente carrega a
    seção daquele que só a menciona de passagem."""
    return _ocorrencia_de_secao(content, secao) is not None


def extrair_secao(content: str, secao: str) -> Optional[str]:
    """Recorta do cabeçalho da seção até o próximo cabeçalho conhecido.

    Devolve None quando a seção não começa neste trecho. O corte é
    deliberadamente conservador: sem cabeçalho seguinte, vai até o fim do
    chunk — é melhor entregar texto a mais (o agente lê) do que cortar no
    meio de uma recomendação de segurança."""
    inicio = _ocorrencia_de_secao(content, secao)
    if not inicio:
        return None
    normalizado = _normalizar(content)
    padrao = _PADRAO_ROTULOS[secao]

    fim = len(content)
    for seguinte in _PADRAO_QUALQUER_ROTULO.finditer(normalizado, inicio.end()):
        # Dois cortes indevidos possíveis aqui, ambos truncam a seção no meio:
        # o próprio cabeçalho reaparecendo dentro do texto, e uma palavra de
        # outra seção usada em frase corrida ("manter a embalagem fechada" não
        # abre a seção Embalagens).
        if padrao.match(normalizado, seguinte.start()):
            continue
        if not _e_cabecalho(content, seguinte.start(), re.sub(r"\s+", " ", seguinte.group(1))):
            continue
        fim = seguinte.start()
        break
    return re.sub(r"\s+", " ", content[inicio.start():fim]).strip() or None


def montar_instrucao_de_secao(secoes: List[str]) -> str:
    """Instrução explícita anexada ao contexto quando a pergunta é sobre uma
    seção. Sem ela o modelo responde com o que estiver mais à mão no contexto
    — que muitas vezes é a seção errada do produto certo."""
    if not secoes:
        return ""
    titulos = ", ".join(f'"{titulo_da_secao(secao)}"' for secao in secoes)
    return (
        f"\n\n🎯 SEÇÃO PEDIDA: a pergunta é sobre {titulos} do produto. Responda a partir "
        "DESSA(S) SEÇÃO(ÕES) dos documentos acima — os trechos que a contêm foram colocados "
        "primeiro no contexto. Se a seção pedida NÃO estiver presente nos trechos acima, diga "
        "que essa informação específica não consta no documento recuperado, em vez de responder "
        "com outra seção (ex: entregar especificação técnica quando o que foi pedido foi "
        "embalagem ou armazenamento)."
    )
