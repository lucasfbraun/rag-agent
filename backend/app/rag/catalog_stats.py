"""
Estatísticas agregadas do acervo real indexado no Qdrant (distinto das
ferramentas MCP simuladas em app.mcp.pu_mcp_server, que ainda esperam
integração com ERP/LIMS real — Fase 4 do cronograma).

Contagem de "produto" é uma aproximação sobre a estrutura de pastas da rede
(cada arquivo fica em `.../<Família>/<Produto>/arquivo.pdf`, às vezes com uma
subpasta administrativa entre o produto e o arquivo — `Obsoletos`,
`Certificados`, etc.) — não existe um campo estruturado "código de produto"
na ingestão hoje. Ver `_produto_do_filepath`.
"""
import re
import unicodedata
from typing import Any, Dict, List, Optional

from qdrant_client.http import models as qmodels

from app.rag.ingestion import get_qdrant_client
from app.rag.exceptions import RetrievalIndisponivelError
from app.config import COLLECTION_NAME

_SEPARADOR_CAMINHO = re.compile(r"[\\/]+")

# Nomes de subpasta que NÃO representam um produto novo — o produto real é a
# pasta mais próxima do arquivo que não é uma destas (ex: ".../FLEXX AG 2032
# /Obsoletos/Boletim antigo.pdf" -> produto = "FLEXX AG 2032", não "Obsoletos").
_PASTAS_NAO_PRODUTO = {
    "obsoletos", "obsoleto", "certificados", "certificado",
    "fichas de emergência", "fichas de emergencia",
    "revisão anterior", "revisao anterior",
    # Segmentos estruturais fixos da árvore de rede (mesmos em TODO filepath
    # do acervo, confirmado por amostragem) — nunca um produto, mesmo quando
    # um arquivo solto (sem pasta de produto própria) faz o algoritmo subir
    # até aqui procurando uma pasta não-administrativa.
    "documentação de produto", "documentacao de produto", "qualidade", "grupos", "flexivel",
}

# Palavras que, sozinhas dentro do nome da pasta, indicam pasta administrativa
# ou de referência — nunca um produto de verdade (achado real: "FISPQ",
# "AMOSTRA FLEXX PI", "FLEXX CL AMOSTRA", "TESTE PALMILHA" apareciam como se
# fossem produtos distintos numa listagem por categoria, ex: "produtos que
# são colas"). Correspondência por PALAVRA INTEIRA (não substring) — não
# basta a pasta conter essas letras, precisa ser uma das palavras que a
# compõem, pra não excluir por engano um produto cujo nome só pareça com isso.
#
# "restaurado" cobre o mesmo marcador de "DOCUMENTAÇÃO DE PRODUTO RESTAURADO
# 0906" já usado em app.rag.ingestion (_MARCADOR_PASTA_MENOS_PRIORITARIA) —
# sem isso, um arquivo direto dentro de "FISPQ" (excluída) nessa árvore subia
# até a raiz "DOCUMENTAÇÃO DE PRODUTO RESTAURADO 0906" e ERA reportado como
# se essa raiz fosse o produto (achado ao validar a correção do FISPQ acima).
_PALAVRAS_PASTA_NAO_PRODUTO = {"fispq", "amostra", "teste", "restaurado"}

_SEPARADOR_PALAVRA = re.compile(r"[\s\-_]+")


def _eh_pasta_administrativa(pasta: str) -> bool:
    pasta_lower = pasta.lower()
    if pasta_lower in _PASTAS_NAO_PRODUTO:
        return True
    palavras = _SEPARADOR_PALAVRA.split(pasta_lower)
    return any(p in _PALAVRAS_PASTA_NAO_PRODUTO for p in palavras)


# Limite de quantas pastas subir procurando uma não-administrativa. Sem
# isso, um arquivo solto dentro de várias pastas administrativas encadeadas
# (ex: ".../RESTAURADO 0906/FISPQ/arquivo.pdf" — as duas são excluídas)
# acabaria subindo até segmentos estruturais da rede ("Qualidade", "GRUPOS",
# o próprio host) e reportando a raiz do compartilhamento como se fosse 1
# produto — pior que simplesmente não atribuir produto a esse arquivo. Toda
# estrutura real de produto observada no acervo (pasta-produto + no máximo 1
# subpasta administrativa, como "Obsoletos/Certificados") cabe em 2 níveis;
# 4 dá folga sem abrir a porta pra subir até a raiz.
_PROFUNDIDADE_MAXIMA_BUSCA_PRODUTO = 4


def _produto_esta_indisponivel(referencia: str) -> bool:
    """True para marcadores explícitos que impedem oferecer o produto."""
    texto = _normalizar_sem_acentos(referencia)
    return (
        bool(re.search(r"\binativ[oa]s?\b", texto))
        or "nao ofertar" in texto
        or bool(re.search(r"\bdescontinuad[oa]s?\b", texto))
        or "fora de linha" in texto
        or bool(re.search(r"\berrad[oa]s?\b", texto))
    )


def _produto_do_filepath(filepath: str) -> Optional[str]:
    """Extrai o nome da pasta-produto mais próxima do arquivo, pulando
    subpastas administrativas/de referência conhecidas. Retorna None se o
    caminho não tiver profundidade suficiente para conter uma pasta de
    produto, ou se nenhuma pasta não-administrativa aparecer dentro do
    limite de busca."""
    if _produto_esta_indisponivel(filepath):
        return None
    partes = [p.strip() for p in _SEPARADOR_CAMINHO.split(filepath) if p.strip()]
    if len(partes) < 2:
        return None
    candidatos = list(reversed(partes[:-1]))[:_PROFUNDIDADE_MAXIMA_BUSCA_PRODUTO]
    for pasta in candidatos:
        if not _eh_pasta_administrativa(pasta):
            return pasta
    return None


def obter_estatisticas_catalogo() -> Dict[str, Any]:
    """Varre a coleção inteira (só payload `filepath`, sem vetor) e devolve
    contagem de produtos distintos e de documentos indexados.

    Levanta RetrievalIndisponivelError se o Qdrant estiver fora do ar — mesmo
    contrato de retrieve_products_context, pro chamador (MCP tool) decidir o
    que informar ao usuário em vez de mascarar com contagem zerada."""
    try:
        client = get_qdrant_client()
        produtos = set()
        documentos = set()
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                filepath = (ponto.payload or {}).get("filepath")
                if not filepath:
                    continue
                documentos.add(filepath)
                produto = _produto_do_filepath(filepath)
                if produto:
                    produtos.add(produto)
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    return {
        "produtos_catalogados": len(produtos),
        "documentos_indexados": len(documentos),
    }


_SEPARADOR_PALAVRA_PRODUTO = re.compile(r"[\s\-_®]+")


def _termo_bate_no_nome_produto(termo: str, produto: str) -> bool:
    """True se `termo` aparece como PALAVRA INTEIRA no nome do produto —
    cobre pedido por FAMÍLIA/CÓDIGO ("CAT", "TH", "AG", "COLOR": o acervo
    segue o padrão FLEXX <FAMÍLIA> <NÚMERO>, ex: "FLEXX CAT 42"). Palavra
    inteira, não substring — "cat" não pode bater em "catalisador"/
    "categoria" (que apareceriam via busca de conteúdo, não de nome)."""
    if not termo or not produto:
        return False
    palavras = _SEPARADOR_PALAVRA_PRODUTO.split(produto.lower())
    return termo.strip().lower() in palavras


_SEPARADOR_PALAVRA_CONTEUDO = re.compile(r"[^a-zà-öø-ÿ]+")


def _normalizar_sem_acentos(texto: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _padrao_nome_do_produto(produto: Optional[str]) -> Optional[str]:
    """Regex que casa o nome do produto no texto, tolerando espaço, hífen e
    símbolos entre as partes ("FLEXX AG 2032", "FLEXX® AG-2032")."""
    partes_produto = re.findall(r"[a-z0-9]+", _normalizar_sem_acentos(produto or ""))
    if not partes_produto:
        return None
    return (
        r"(?<![a-z0-9])"
        + r"[^a-z0-9]*".join(map(re.escape, partes_produto))
        + r"(?![a-z0-9])"
    )


def _singular_e_plural(palavra: str) -> List[str]:
    """Singular e plural de uma palavra portuguesa, sem acento.

    REGRESSÃO REAL (revisão de 18/09/2026): a primeira versão só sabia tirar e
    pôr um "s" no fim. Isso cobre "elastomeros"↔"elastomero" e acabou aí:

        catalisadores -> catalisadore   (nunca alcança "catalisador")
        vernizes      -> vernize
        poliois       -> polioi
        catalisador   -> catalisadors

    Consequência: "quais produtos são catalisadores?" devolvia ZERO num acervo
    onde o Boletim diz, literalmente, "FLEXX CAT 42 é um catalisador" — o mesmo
    bug que esta cascata existe para matar, ressuscitado em outra palavra.
    Valia igual para vernizes, polióis, endurecedores e aceleradores.

    Gera CANDIDATOS, não a forma correta: uma flexão que não existe na língua
    simplesmente não casa com nada, e é mais barata que uma tabela de exceções.
    """
    base = (palavra or "").strip()
    if not base:
        return []
    formas = [base]
    if base.endswith("oes"):                       # elastomeroes? / ligacoes
        formas.append(f"{base[:-3]}ao")
    elif base.endswith("aes"):                     # pães
        formas.append(f"{base[:-3]}ao")
    elif base.endswith("is") and len(base) > 3:    # polióis -> poliol, papeis -> papel
        formas.append(f"{base[:-2]}l")
        formas.append(f"{base[:-1]}l")             # -eis -> -el
    elif base.endswith("es") and len(base) > 3:    # catalisadores -> catalisador
        formas.append(base[:-2])                   # vernizes -> verniz
        formas.append(base[:-1])                   # cobre plural simples em -es
    elif base.endswith("s"):
        formas.append(base[:-1])
    if base.endswith("ao"):                        # elastomerão? / mamão
        formas.append(f"{base[:-2]}oes")
    elif base.endswith(("r", "z", "s")):           # catalisador -> catalisadores
        formas.append(f"{base}es")
    elif base.endswith("l"):                       # poliol -> polióis
        formas.append(f"{base[:-1]}is")
    elif base.endswith("m"):
        formas.append(f"{base[:-1]}ns")
    elif not base.endswith("s"):
        formas.append(f"{base}s")
    # A palavra ORIGINAL nunca é descartada, por mais curta que seja. O teto de
    # 3 letras vale só para as flexões DERIVADAS, que abaixo disso viram ruído.
    # Aplicá-lo à base quebrava a comparação termo-a-termo de
    # `_termo_bate_no_conteudo`: em "assento de ônibus", a palavra "de" deixava
    # de casar consigo mesma e a expressão inteira parava de ser encontrada.
    derivadas = [f for f in formas[1:] if len(f) >= 3]
    return list(dict.fromkeys([base, *derivadas]))


def _variantes_do_termo(termo: str) -> List[str]:
    """Formas do termo que podem aparecer no boletim: singular, plural e a
    forma adjetiva quando ela é derivável ("elastômero" → "elastomérico").

    Uma variante que não existe na língua ("adesivo" → "adesivico") não causa
    dano: ela simplesmente nunca casa com nada.
    """
    base = _normalizar_sem_acentos(termo).strip()
    if not base:
        return []
    variantes = _singular_e_plural(base)
    # Forma adjetiva, a partir do SINGULAR: "elastomero" → "elastomerico".
    # `len > 4` protege o caso real "oleo" → "oleico", que colidiria com o
    # "ácido oleico" que aparece em FISPQ.
    for forma in list(variantes):
        if forma.endswith("o") and len(forma) > 4:
            radical = forma[:-1]
            variantes.extend([
                f"{radical}ico", f"{radical}icos", f"{radical}ica", f"{radical}icas",
            ])
    return list(dict.fromkeys(variantes))


def _padrao_identidade_declarada(termo: str, filepath: str) -> Optional[str]:
    """Regex única da regra de identidade — predicado e trecho usam esta.

    Existe separada porque o predicado casa sobre o texto normalizado
    enquanto o recorte literal refaz a busca sobre o texto original com
    espaços colapsados: são duas buscas, mas UMA regra. Duas regras fariam a
    resposta citar uma frase diferente da que aceitou o produto.
    """
    nome_arquivo = _SEPARADOR_CAMINHO.split(filepath)[-1]
    if "boletim" not in _normalizar_sem_acentos(nome_arquivo):
        return None
    padrao_produto = _padrao_nome_do_produto(_produto_do_filepath(filepath))
    variantes = _variantes_do_termo(termo)
    if not padrao_produto or not variantes:
        return None
    alternativas = "|".join(re.escape(v) for v in variantes)
    # "poliuretano/produto/material <adjetivo>" é a forma como um boletim
    # costuma declarar natureza, e vale tanto para elastomérico quanto para
    # qualquer outro adjetivo derivado do termo.
    return (
        padrao_produto
        + r"\s*(?:,|-)?\s*(?:e|trata-se\s+de|consiste\s+em)\s+"
        + r"(?:um\s+|uma\s+)?"
        + rf"(?:(?:poliuretano|produto|material|sistema)\s+)?(?:{alternativas})\b"
    )


def _match_identidade_declarada(
    termo: str, filepath: str, content: str
) -> Optional[re.Match]:
    """A regra de identidade, devolvendo ONDE ela casou."""
    padrao = _padrao_identidade_declarada(termo, filepath)
    if not padrao:
        return None
    return re.search(padrao, _normalizar_sem_acentos(content))


def _conteudo_declara_produto_como(
    termo: str, filepath: str, content: str
) -> bool:
    """O próprio Boletim diz que o produto É da natureza `termo`.

    GENÉRICA DESDE 18/09/2026. Antes existiam duas cópias literais desta
    função — uma para "isocianato", outra para "elastômero" — idênticas exceto
    pelo substantivo. Cada natureza nova que alguém quisesse perguntar exigia
    uma terceira cópia, e foi assim que o motor acumulou seis funções só sobre
    elastômero. O termo agora é parâmetro.

    O nome do produto precisa ser o SUJEITO da declaração: "FLEXX X é um
    elastômero" qualifica; "FLEXX X produz elastômero" e "adequado para
    produção de poliuretano elastomérico" não — aquilo é finalidade, e tem
    nível próprio na cascata (`_conteudo_comprova_composicao_do_termo`).
    """
    return _match_identidade_declarada(termo, filepath, content) is not None


def _conteudo_declara_produto_como_isocianato(filepath: str, content: str) -> bool:
    """Detecta quando o próprio produto é explicitamente um isocianato."""
    return _conteudo_declara_produto_como("isocianato", filepath, content)


def _rotulo_estrutural_catalogo(valor: str) -> str:
    """Normaliza um segmento da hierarquia sem preservar marcas/símbolos."""
    return re.sub(r"[^a-z0-9]+", " ", _normalizar_sem_acentos(valor)).strip()


# REMOVIDAS em 21/09/2026, junto com o parâmetro `exigir_natureza`:
#   `_conteudo_declara_produto_como_elastomero` — era um apelido de uma linha
#   para `_conteudo_declara_produto_como("elastomero", ...)`, que já é genérica
#   por termo e alimenta o nível `identidade_declarada` da cascata.
#   `_conteudo_comprova_tecnologia_rigido` — reimplementava, só para RG, a
#   leitura da árvore do catálogo que `_documento_atual_com_produto_catalogavel`
#   + `_classificacoes_do_filepath` fazem para QUALQUER linha (obsoletos,
#   INATIVO, pastas "COM ISO" e pastas de família sem código inclusive), e que
#   `listar_produtos_por_classificacao_catalogo` e o nível
#   `classificacao_estrutural` já usam.


def _conteudo_comprova_tipo_elastomero(filepath: str, content: str) -> bool:
    """True somente para evidência positiva no Boletim do próprio produto.

    "Aditivo para elastômeros", "catalisador usado em elastômeros" e um
    isocianato que participa da combinação são relações de uso, não
    classificação da natureza/tecnologia do produto.
    """
    nome_arquivo = _SEPARADOR_CAMINHO.split(filepath)[-1]
    if "boletim" not in _normalizar_sem_acentos(nome_arquivo):
        return False
    if _conteudo_declara_produto_como_isocianato(filepath, content):
        return False

    texto = _normalizar_sem_acentos(content)
    evidencias_positivas = (
        r"\bproduz(?:ir|em)?\s+(?:um\s+)?elastomero\b",
        r"\bobter\s+(?:um\s+)?elastomero\b",
        r"\bformacao\s+(?:de\s+)?elastomero\b",
        r"\bsistema\s+elastomerico\b",
        r"\bpoliuretano\s+elastomerico\b",
    )
    return any(re.search(padrao, texto) for padrao in evidencias_positivas)


def _flexoes_da_palavra(palavra: str) -> set[str]:
    """Flexões conservadoras, suficientes para singular/plural sem usar
    prefixos abertos que confundem `correia` com `corretamente`.

    Usa `_singular_e_plural` — a mesma regra da cascata de natureza. Antes
    havia aqui uma segunda cópia que só sabia tirar e pôr um "s", com o
    mesmo defeito: "catalisadores" não encontrava "catalisador", e uma
    busca por menção do plural em -es voltava vazia.

    Estava aninhada em `_termo_bate_no_conteudo`; subiu para o módulo quando
    `_padrao_do_termo_no_conteudo` passou a precisar das MESMAS flexões para
    localizar no texto o trecho que sustenta a menção. Duas regras de flexão
    diferentes fariam a resposta citar uma frase que não é a que decidiu.
    """
    if palavra in {"pu", "pus", "poliuretano", "poliuretanos"}:
        return {"pu", "pus", "poliuretano", "poliuretanos"}
    return set(_singular_e_plural(palavra)) or {palavra}


def _padrao_do_termo_no_conteudo(termo_busca: str) -> Optional[str]:
    """Regex que localiza no texto o que `_termo_bate_no_conteudo` aceitou.

    Serve só para RECORTAR O TRECHO de evidência: quem decide se o produto
    entra continua sendo `_termo_bate_no_conteudo`, comparando tokens. Se
    este padrão não encontrar nada (termo com dígito, por exemplo, que a
    tokenização trata como separador), a resposta cita o documento sem trecho
    — nunca inventa um.
    """
    tokens = [
        p for p in _SEPARADOR_PALAVRA_CONTEUDO.split(_normalizar_sem_acentos(termo_busca))
        if p
    ]
    if not tokens:
        return None
    grupos = [
        "(?:" + "|".join(
            re.escape(f) for f in sorted(_flexoes_da_palavra(t), key=len, reverse=True)
        ) + ")"
        for t in tokens
    ]
    # Fronteira só em letras, e não em `\b`, porque a tokenização de
    # `_SEPARADOR_PALAVRA_CONTEUDO` trata dígito como separador.
    return r"(?<![a-z])" + r"[^a-z]+".join(grupos) + r"(?![a-z])"


def _termo_bate_no_conteudo(termo_busca: str, content_lower: str) -> bool:
    """True se `termo_busca` aparece no conteúdo do documento.

    Achado real: buscar "CAT" (família de produto) com substring simples
    ("cat" in content) batia em "catálise"/"catalisador" — termos reais de
    química que aparecem no conteúdo de ~500 produtos sem NENHUMA relação
    com a família "CAT" — inflando o resultado de ~36 (correto, por nome)
    para ~550.

    A comparação usa tokens inteiros e um conjunto conservador de flexões
    singular/plural. Isso cobre "colchão"/"colchões" e
    "correia"/"correias", sem o prefixo aberto que fazia "correia" casar
    com "corretamente" e "corrente" em centenas de FISPQs."""
    def _normalizar(texto: str) -> str:
        return _normalizar_sem_acentos(texto)

    _flexoes = _flexoes_da_palavra

    termo_tokens = [p for p in _SEPARADOR_PALAVRA_CONTEUDO.split(_normalizar(termo_busca)) if p]
    conteudo_tokens = [p for p in _SEPARADOR_PALAVRA_CONTEUDO.split(_normalizar(content_lower)) if p]
    if not termo_tokens:
        return False

    largura = len(termo_tokens)
    for inicio in range(len(conteudo_tokens) - largura + 1):
        trecho = conteudo_tokens[inicio:inicio + largura]
        if all(valor in _flexoes(esperado) for esperado, valor in zip(termo_tokens, trecho)):
            return True
    return False


def _padrao_do_codigo(codigo: str) -> Optional[str]:
    """Regex que casa o código completo, tolerando espaço, hífen e quebra."""
    partes = re.findall(r"[a-z0-9]+", _normalizar_sem_acentos(codigo))
    if not partes:
        return None
    return (
        r"(?<![a-z0-9])"
        + r"[\s\-_®]*".join(map(re.escape, partes))
        + r"(?![a-z0-9])"
    )


def _codigo_bate_no_texto(codigo: str, texto: str) -> bool:
    """Confirma código completo, tolerando espaço, hífen ou quebra de linha."""
    padrao = _padrao_do_codigo(codigo)
    if not padrao:
        return False
    return bool(re.search(padrao, _normalizar_sem_acentos(texto)))


def _trecho_em_volta(
    padrao: Optional[str], content: str, largura: int = 320
) -> str:
    """Recorta um trecho LITERAL do `content` em volta da primeira ocorrência.

    O recorte é feito sobre o texto original (só com espaços em branco
    colapsados), não sobre a forma normalizada usada para localizar: o que
    volta é o que está escrito no documento, com acento e maiúscula, para que
    quem lê a resposta possa conferir a frase no PDF. A busca acontece na
    forma sem acento porque é assim que todas as regras deste módulo casam.

    `largura` é o teto do trecho, em caracteres — nunca se devolve o chunk
    inteiro, que no acervo real chega a 700 palavras.
    """
    texto = re.sub(r"\s+", " ", content or "").strip()
    if not texto or not padrao:
        return ""
    match = re.search(padrao, _normalizar_sem_acentos(texto))
    if not match:
        return ""
    metade = largura // 2
    inicio = max(0, min(match.start(), len(texto)) - metade)
    fim = min(len(texto), match.end() + metade)
    prefixo = "…" if inicio else ""
    sufixo = "…" if fim < len(texto) else ""
    return f"{prefixo}{texto[inicio:fim].strip()}{sufixo}"


def _trecho_da_mencao(codigo: str, content: str, largura: int = 320) -> str:
    """Recorta contexto suficiente para distinguir uso, comparação e negação."""
    return _trecho_em_volta(_padrao_do_codigo(codigo), content, largura)


def buscar_produtos_que_mencionam(
    codigo: str | List[str],
    incluir_sensivel: bool = False,
    familias_destino: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Busca reversa completa: Boletins que mencionam todos os códigos.

    Todas as ocorrências textuais indexadas são paginadas até o fim e o
    resultado não usa top-k nem prévia. A confirmação local cobre código
    separado por espaço, hífen ou quebra de linha; o filtro inicial inclui
    também as grafias compactas para não perder resultados por tokenização.
    Quando há vários códigos, eles podem estar em trechos diferentes do mesmo
    Boletim, mas o produto só entra no resultado se todos forem confirmados no
    mesmo documento. Os documentos dos próprios produtos de origem são
    excluídos, pois seus
    cabeçalhos não representam uma relação com outro item.
    """
    codigos_recebidos = [codigo] if isinstance(codigo, str) else codigo
    codigos = list(dict.fromkeys(
        item.strip()
        for item in (codigos_recebidos or [])
        if item and item.strip()
    ))
    if not codigos:
        return []
    partes_por_codigo = {
        item: re.findall(r"[a-z0-9]+", _normalizar_sem_acentos(item))
        for item in codigos
    }
    if any(not partes for partes in partes_por_codigo.values()):
        return []
    variantes_codigo = list(dict.fromkeys(
        variante
        for item, partes in partes_por_codigo.items()
        for variante in (
            item,
            " ".join(partes),
            "-".join(partes),
            "".join(partes),
        )
    ))
    familias = list(dict.fromkeys(
        familia.strip().lower()
        for familia in (familias_destino or [])
        if familia and familia.strip()
    ))
    encontrados: Dict[str, Dict[str, Any]] = {}
    try:
        client = get_qdrant_client()
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=qmodels.Filter(should=[
                    qmodels.FieldCondition(
                        key="content", match=qmodels.MatchText(text=variante)
                    )
                    for variante in variantes_codigo
                ]),
                with_payload=["filepath", "filename", "content", "sensivel"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                payload = ponto.payload or {}
                if payload.get("sensivel") is True and not incluir_sensivel:
                    continue
                filepath = payload.get("filepath") or ""
                filename = payload.get("filename") or (
                    _SEPARADOR_CAMINHO.split(filepath)[-1] if filepath else ""
                )
                if "boletim" not in _normalizar_sem_acentos(filename):
                    continue
                produto = _produto_do_filepath(filepath)
                content = payload.get("content") or ""
                if (
                    not produto
                    or (
                        familias
                        and not any(
                            _termo_bate_no_nome_produto(familia, produto)
                            for familia in familias
                        )
                    )
                    or any(
                        _codigo_bate_no_texto(item, produto)
                        or _codigo_bate_no_texto(item, filename)
                        for item in codigos
                    )
                ):
                    continue
                codigos_no_conteudo = [
                    item for item in codigos
                    if _codigo_bate_no_texto(item, content)
                ]
                if not codigos_no_conteudo:
                    continue
                dados = encontrados.setdefault(
                    produto, {
                        "documentos": set(),
                        "mencoes": [],
                        "codigos_por_documento": {},
                    }
                )
                dados["documentos"].add(filename)
                dados["codigos_por_documento"].setdefault(filename, set()).update(
                    codigos_no_conteudo
                )
                for codigo_encontrado in codigos_no_conteudo:
                    mencao = {
                        "documento": filename,
                        "trecho": _trecho_da_mencao(codigo_encontrado, content),
                    }
                    if len(codigos) > 1:
                        mencao["codigo"] = codigo_encontrado
                    if mencao not in dados["mencoes"]:
                        dados["mencoes"].append(mencao)
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    resultados = []
    for produto, dados in sorted(encontrados.items()):
        documentos_completos = sorted(
            documento
            for documento, codigos_encontrados in dados["codigos_por_documento"].items()
            if all(item in codigos_encontrados for item in codigos)
        )
        if not documentos_completos:
            continue
        resultados.append({
            "produto": produto,
            "documentos": documentos_completos,
            "mencoes": [
                mencao for mencao in dados["mencoes"]
                if mencao["documento"] in documentos_completos
            ],
        })
    return resultados


def _resumo_lista(
    produtos: set,
    listar_todos: bool,
    evidencias: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Resumo de um nível; com `evidencias`, cita a prova de cada produto.

    ORÇAMENTO DE TAMANHO (decisão de 18/09/2026). O trecho literal só
    acompanha a PRÉVIA (até 10 produtos). Com `listar_todos=True` o acervo
    real devolve centenas de produtos, e centenas de trechos de até 240
    caracteres viram uma parede de texto que ninguém lê — o oposto do que a
    citação existe para fazer. Na lista completa fica o nome do documento,
    que é o suficiente para o vendedor abrir o PDF e conferir, e ele pode
    pedir o detalhe de um produto específico.
    """
    lista = sorted(produtos)
    limite = None if listar_todos else 10
    exibidos = lista if limite is None else lista[:limite]
    resumo: Dict[str, Any] = {
        "total": len(lista),
        "produtos": exibidos,
        "truncado": limite is not None and len(lista) > limite,
    }
    if evidencias is not None:
        resumo["evidencias"] = {
            produto: (
                dict(evidencias[produto])
                if limite is not None
                else {
                    chave: valor
                    for chave, valor in evidencias[produto].items()
                    if chave != "trecho"
                }
            )
            for produto in exibidos
            if produto in evidencias
        }
    return resumo


def listar_produtos_por_aplicacao(
    termo_busca: str = "",
    listar_todos: bool = False,
) -> Dict[str, Any]:
    """Lista produtos distintos do acervo, separando DUAS interpretações
    possíveis do mesmo termo — pedido do usuário: o agente precisa entender
    a diferença entre "nome de produto" e "aplicação/segmento", e perguntar
    quando não tiver certeza de qual o vendedor quis dizer, em vez de
    misturar as duas coisas num resultado só:

    - `por_nome_ou_familia`: o termo é uma PALAVRA INTEIRA do NOME do
      produto (padrão do acervo: FLEXX <FAMÍLIA> <NÚMERO>, ex: "FLEXX CAT
      42" — família "CAT"). Cobre "produtos CAT", "produtos da família TH".
    - `por_aplicacao_ou_tipo`: o termo (com flexão singular/plural) aparece no
      CONTEÚDO do documento — cobre aplicação/uso ("colchão", "cortiça") ou
      tipo/natureza do produto ("cola", "espuma").

    Por quê separado (achado real, validando "CAT" ao vivo): um boletim de
    "FLEXX AG 20102" tem uma tabela comparativa que MENCIONA "FLEXX CAT 90"
    (produto concorrente citado como referência) — o conteúdo bate em "cat"
    genuinamente, mas "FLEXX AG 20102" não tem NADA a ver com a família CAT.
    Misturar os dois sinais num resultado só (como esta função fazia antes)
    inflava famílias de código com produtos de outras famílias que só
    CITAM aquele código. Separar deixa claro pro agente (e pro usuário,
    quando perguntado) qual interpretação está sendo usada.

    SEM `termo_busca` (vazio/None), NENHUM filtro é aplicado — todo o
    catálogo entra em `por_nome_ou_familia`, `por_aplicacao_ou_tipo` fica
    vazio (pedido do usuário: "listar todos os produtos" sem categoria).

    Por quê existe separado de retrieve_products_context: um pedido de
    LISTAGEM ("produtos para colchão", "produtos que são colas", "produtos
    CAT", "liste todos os produtos") não é a mesma coisa que um pedido de
    recomendação única — um top-k de poucos chunks (mesmo com a busca
    híbrida) nunca representa fielmente uma categoria (ou o catálogo
    inteiro) com centenas de produtos. Isso varre a coleção inteira e
    devolve os NOMES dos produtos, não os trechos de texto — quem quiser
    detalhe de um item específico faz uma pergunta de acompanhamento, que
    aí sim usa retrieve_products_context normalmente.

    ESTA FUNÇÃO NÃO RESPONDE "quais produtos SÃO X". Até 21/09/2026 ela
    aceitava `exigir_natureza=True`, que prometia comprovar a natureza do
    material e só funcionava para dois termos codificados à mão — "elastômero"
    e "rígidos". Para qualquer outra palavra o parâmetro não fazia nada, em
    silêncio: o modelo pedia prova de natureza e recebia uma busca textual
    comum, sem saber. Natureza tem ferramenta própria desde então,
    `listar_produtos_por_tipo`, que devolve os quatro níveis de evidência
    ROTULADOS em vez de um bucket booleano — e o ramo de "rígidos" era, na
    verdade, classificação de catálogo, que tem
    `listar_produtos_por_classificacao_catalogo`.

    `listar_todos` (pedido do usuário): por padrão cada bucket devolve só
    uma prévia (10 produtos) + o total real, pra o agente perguntar se o
    vendedor quer a lista completa antes de despejar dezenas/centenas de
    nomes. Quando `listar_todos=True`, cada bucket devolve TODOS sem
    nenhum limite, não importa quantos sejam."""
    try:
        client = get_qdrant_client()
        produtos_por_nome = set()
        produtos_por_conteudo = set()
        produtos_declarados_isocianatos = set()
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath", "content"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                payload = ponto.payload or {}
                produto = _produto_do_filepath(payload.get("filepath") or "")
                if not produto:
                    continue

                if not termo_busca:
                    produtos_por_nome.add(produto)
                    continue

                if _termo_bate_no_nome_produto(termo_busca, produto):
                    produtos_por_nome.add(produto)
                    continue

                content = (payload.get("content") or "").lower()
                termo_normalizado = _normalizar_sem_acentos(termo_busca)
                busca_tipo_elastomero = termo_normalizado in {
                    "elastomero", "elastomeros", "elastomerico", "elastomericos",
                }
                if busca_tipo_elastomero:
                    if _conteudo_declara_produto_como_isocianato(
                        payload.get("filepath") or "", content
                    ):
                        produtos_declarados_isocianatos.add(produto)
                        continue
                    bate = _conteudo_comprova_tipo_elastomero(
                        payload.get("filepath") or "", content
                    )
                else:
                    bate = _termo_bate_no_conteudo(termo_busca, content)
                if bate:
                    produtos_por_conteudo.add(produto)
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    produtos_por_conteudo.difference_update(produtos_declarados_isocianatos)
    return {
        "termo_buscado": termo_busca,
        "por_nome_ou_familia": _resumo_lista(produtos_por_nome, listar_todos),
        "por_aplicacao_ou_tipo": _resumo_lista(produtos_por_conteudo, listar_todos),
    }


_ROTULO_RAIZ_CATALOGO = "documentacao de produto"
_ROTULOS_DOCUMENTO_HISTORICO = {"obsoleto", "obsoletos", "revisao anterior"}
_ALIASES_CLASSIFICACAO_CATALOGO = {
    # O acervo usa o código corporativo RG no caminho, enquanto as pessoas
    # normalmente dizem "rígidos". Códigos e nomes das demais linhas são
    # descobertos dinamicamente e não precisam entrar neste mapa.
    "rigido": {"flexx rg"},
    "rigidos": {"flexx rg"},
    "rigida": {"flexx rg"},
    "rigidas": {"flexx rg"},
    "poliuretano rigido": {"flexx rg"},
    "poliuretanos rigidos": {"flexx rg"},
}


def _classificacoes_do_filepath(filepath: str, produto: str) -> List[tuple[str, str]]:
    """Extrai todas as linhas/sublinhas ancestrais do produto no catálogo."""
    partes = [
        parte.strip()
        for parte in _SEPARADOR_CAMINHO.split(filepath)
        if parte.strip()
    ]
    rotulos = [_rotulo_estrutural_catalogo(parte) for parte in partes]
    try:
        indice_raiz = rotulos.index(_ROTULO_RAIZ_CATALOGO)
    except ValueError:
        return []

    rotulo_produto = _rotulo_estrutural_catalogo(produto)
    indices_produto = [
        indice
        for indice, rotulo in enumerate(rotulos[:-1])
        if rotulo == rotulo_produto
    ]
    if not indices_produto:
        return []
    indice_produto = indices_produto[-1]
    if indice_produto <= indice_raiz:
        return []

    resultado = []
    for parte, rotulo in zip(
        partes[indice_raiz + 1 : indice_produto],
        rotulos[indice_raiz + 1 : indice_produto],
    ):
        if not rotulo or _eh_pasta_administrativa(parte):
            continue
        if rotulo in _ROTULOS_DOCUMENTO_HISTORICO:
            continue
        resultado.append((rotulo, parte))
    return resultado


def _documento_atual_com_produto_catalogavel(filepath: str, produto: str) -> bool:
    """Aceita somente um Boletim atual pertencente a uma pasta-produto real."""
    partes = [
        parte.strip()
        for parte in _SEPARADOR_CAMINHO.split(filepath)
        if parte.strip()
    ]
    if not partes or "boletim" not in _normalizar_sem_acentos(partes[-1]):
        return False
    rotulos = [_rotulo_estrutural_catalogo(parte) for parte in partes]
    if any(rotulo in _ROTULOS_DOCUMENTO_HISTORICO for rotulo in rotulos[:-1]):
        return False
    if _produto_esta_indisponivel(filepath):
        return False

    rotulo_produto = _rotulo_estrutural_catalogo(produto)
    if not rotulo_produto or rotulo_produto.startswith("com "):
        return False
    # Pastas de família sem um código/nome adicional não são produtos. Isso
    # elimina, por exemplo, FLEXX RGB e FLEXX RGT quando um arquivo está solto.
    if re.fullmatch(r"flexx\s+[a-z]{1,10}", rotulo_produto):
        return False
    if not re.search(r"\d", rotulo_produto) and not re.match(
        r"^(?:flexx|flexcolor|softflex|pro\b|angeltech\b|polivedo\b)",
        rotulo_produto,
    ):
        return False
    return True


def _aliases_de_rotulo_classificacao(rotulo: str) -> set[str]:
    aliases = {rotulo}
    if rotulo.startswith("flexx "):
        aliases.add(rotulo.removeprefix("flexx ").strip())
    if rotulo.endswith(" flexx"):
        prefixo = rotulo.removesuffix(" flexx").strip()
        aliases.update({prefixo, f"flexx {prefixo}"})
    if rotulo.startswith("iso "):
        aliases.add("iso")
    return aliases


def _resolver_classificacoes_catalogo(
    termo_classificacao: str,
    classificacoes_disponiveis: set[str],
) -> set[str]:
    termo = _rotulo_estrutural_catalogo(termo_classificacao)
    alvos_semanticos = _ALIASES_CLASSIFICACAO_CATALOGO.get(termo)
    if alvos_semanticos:
        return classificacoes_disponiveis.intersection(alvos_semanticos)
    return {
        classificacao
        for classificacao in classificacoes_disponiveis
        if termo in _aliases_de_rotulo_classificacao(classificacao)
    }


def listar_produtos_por_classificacao_catalogo(
    termo_classificacao: str,
    listar_todos: bool = False,
) -> Dict[str, Any]:
    """Lista produtos pela tecnologia/linha estrutural, sem busca textual.

    Toda linha ou sublinha presente entre a raiz ``Documentação de Produto``
    e a pasta do produto é descoberta automaticamente. Assim, linhas novas e
    consultas por código (BT, TH, RGE etc.) não exigem mudança de prompt.
    """
    try:
        client = get_qdrant_client()
        classificacoes_por_produto: Dict[str, set[str]] = {}
        nomes_classificacoes: Dict[str, str] = {}
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                filepath = ((ponto.payload or {}).get("filepath") or "").strip()
                produto = _produto_do_filepath(filepath)
                if not produto or not _documento_atual_com_produto_catalogavel(
                    filepath, produto
                ):
                    continue
                classificacoes = _classificacoes_do_filepath(filepath, produto)
                if not classificacoes:
                    continue
                bucket = classificacoes_por_produto.setdefault(produto, set())
                for rotulo, nome in classificacoes:
                    bucket.add(rotulo)
                    nomes_classificacoes.setdefault(rotulo, nome)
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    disponiveis = set(nomes_classificacoes)
    alvos = _resolver_classificacoes_catalogo(termo_classificacao, disponiveis)
    produtos = {
        produto
        for produto, classificacoes in classificacoes_por_produto.items()
        if classificacoes.intersection(alvos)
    }
    resumo = _resumo_lista(produtos, listar_todos)
    return {
        "termo_buscado": termo_classificacao,
        "classificacoes": sorted(nomes_classificacoes[alvo] for alvo in alvos),
        **resumo,
        "classificacoes_disponiveis": sorted(nomes_classificacoes.values()),
    }


# ---------------------------------------------------------------------------
# Cascata de evidência para "quais produtos são X" — genérica por termo
# ---------------------------------------------------------------------------
#
# POR QUE ISTO EXISTE (18/09/2026)
#
# A pergunta "quais produtos são elastômeros?" respondia "não encontrei" num
# acervo com centenas de boletins. A causa não era recuperação: a varredura lê
# a coleção inteira. Era a REGRA DE ACEITAÇÃO, que exigia o boletim conter
# literalmente "<produto> é um elastômero" — uma frase que boletim técnico não
# escreve. O resultado era zero, sempre, por mais documentos que existissem.
#
# Essa regra foi apertada de propósito, depois que um isocianato apareceu
# listado como elastômero por participar de uma combinação. A correção trocou
# um erro pelo oposto: de listar demais para não listar nada. É o pêndulo que
# acontece quando não há medição — e que, no motor antigo, se repetia por
# terminologia, uma função nova de cada vez.
#
# A saída não é escolher entre estrito e frouxo. É ORDENAR A EVIDÊNCIA e dizer
# ao usuário qual nível respondeu. Do mais forte ao mais fraco:
#
#   1. classificacao_estrutural — a árvore de pastas do catálogo diz que o
#      produto pertence àquela tecnologia/linha. É a única prova independente
#      de como o boletim foi redigido, e por isso vem primeiro.
#   2. identidade_declarada — o boletim do próprio produto diz que ele É aquilo.
#   3. composicao_comprovada — o boletim comprova que o produto produz, forma
#      ou compõe um sistema daquele tipo. Não é o mesmo que ser, e é rotulado
#      como tal.
#   4. mencao_no_documento — o termo aparece no documento. É o mais fraco e
#      nunca deve ser apresentado como classificação; serve para não terminar
#      a conversa em "não há nada" quando há algo a investigar.
#
# NADA AQUI É ESPECÍFICO DE ELASTÔMERO. O termo é parâmetro em todos os quatro
# níveis, e a mesma cascata responde "quais produtos são adesivos", "são
# selantes", "são catalisadores" ou qualquer terminologia que o usuário traga.

NIVEIS_DE_EVIDENCIA = (
    "classificacao_estrutural",
    "identidade_declarada",
    "composicao_comprovada",
    "mencao_no_documento",
)

# A CASCATA GUARDA A EVIDÊNCIA QUE ACEITOU CADA PRODUTO (18/09/2026)
#
# O Bloco 1 da avaliação de arquitetura exige que toda afirmação técnica venha
# com o documento de origem e o trecho literal, porque o usuário-alvo é um
# vendedor que NÃO conhece os produtos e precisa conferir sozinho. A cascata
# afirmava "o Boletim do próprio produto declara que ele é isso" e devolvia
# `sources: []` — a afirmação mais forte do motor, sem nada para conferir.
#
# O filepath e o content já estavam em mãos no exato ponto em que o produto era
# aceito; a evidência era calculada e jogada fora. Agora é guardada, sob duas
# restrições de custo:
#
#   - UMA evidência por produto por nível, a PRIMEIRA que aparecer. A varredura
#     passa por ~11.000 pontos e um produto pode ser aceito por dezenas deles;
#     guardar todas multiplicaria memória por nada, já que a resposta cita uma.
#   - Trecho com teto de caracteres, nunca o chunk inteiro (que no acervo real
#     chega a 700 palavras e encheria a tela com uma citação só).
_LARGURA_TRECHO_EVIDENCIA = 240

# Aditivos e catalisadores participam da reação, mas não SÃO o material que ela
# produz — vale para elastômero, para espuma e para qualquer outra natureza.
# Era uma regra escrita à mão dentro do caminho de elastômero; virou dado, e
# por isso passou a valer para todo termo.
#
# O MAPA GUARDA A NATUREZA DE CADA FAMÍLIA, e não só a sigla, por causa de uma
# regressão real (revisão de 18/09/2026): a exclusão era incondicional, então
# "quais produtos são catalisadores?" removia justamente os FLEXX CAT — a
# resposta certa — e a pergunta caía para o nível de menção, rotulada como a
# evidência mais fraca do sistema. Quem pergunta PELA família auxiliar não pode
# ser excluído por ela, mesma disciplina que `_NATUREZAS_EXCLUDENTES` já tinha.
_FAMILIAS_AUXILIARES_DO_CATALOGO = {
    "adt": ("aditivo",),
    "cat": ("catalisador", "curativo"),
}

# Naturezas que, quando declaradas, impedem o produto de ser classificado como
# OUTRA coisa. Um produto que o boletim declara isocianato não é um elastômero,
# por mais que participe da combinação que produz um. A lista é comparada com o
# termo perguntado: quem pergunta "quais produtos são isocianatos" não é
# excluído pela própria natureza que pediu.
_NATUREZAS_EXCLUDENTES = ("isocianato",)


def _produto_e_familia_auxiliar(produto: str, termos_perguntados: List[str]) -> bool:
    """True se o produto é de uma família auxiliar QUE NÃO É a natureza pedida.

    `termos_perguntados` é o escape: perguntar "quais produtos são
    catalisadores" não pode excluir a família CAT, que é a resposta.
    """
    naturezas_pedidas = {
        forma
        for termo in termos_perguntados
        for forma in _variantes_do_termo(termo)
    }
    rotulo = _rotulo_estrutural_catalogo(produto)
    for sigla, naturezas in _FAMILIAS_AUXILIARES_DO_CATALOGO.items():
        if any(
            forma in naturezas_pedidas
            for natureza in naturezas
            for forma in _variantes_do_termo(natureza)
        ):
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(sigla)}(?![a-z0-9])", rotulo):
            return True
    return False


def _padroes_composicao_do_termo(termo: str, filepath: str) -> List[str]:
    """Regexes únicas da regra de composição — predicado e trecho usam estas.

    Mesma disciplina de `_padrao_identidade_declarada`: quem decide e quem
    cita a evidência leem da MESMA regra.
    """
    nome_arquivo = _SEPARADOR_CAMINHO.split(filepath)[-1]
    if "boletim" not in _normalizar_sem_acentos(nome_arquivo):
        return []
    variantes = _variantes_do_termo(termo)
    if not variantes:
        return []
    alternativas = "|".join(re.escape(v) for v in variantes)
    return [
        rf"\bproduz(?:ir|em)?\s+(?:um\s+|uma\s+)?(?:{alternativas})\b",
        rf"\bobter\s+(?:um\s+|uma\s+)?(?:{alternativas})\b",
        rf"\b(?:formacao|producao|fabricacao|obtencao)\s+(?:de\s+)?"
        rf"(?:um\s+|uma\s+)?(?:{alternativas})\b",
        # Só "sistema" e "poliuretano" — era o que a versão de elastômero
        # fazia. Incluir "produto|material|peca" (revisão de 18/09/2026)
        # transformava boilerplate em prova: "Este PRODUTO ADESIVO deve ser
        # armazenado em local seco" virava composição comprovada, e
        # "PRODUTO NOVO da linha" fazia "novo" parecer uma natureza. Pior, é
        # identidade sendo rotulada com o texto do nível de composição, que
        # diz ao vendedor o contrário do que a fonte afirma.
        rf"\b(?:sistema|poliuretano)\s+(?:{alternativas})\b",
    ]


def _match_composicao_do_termo(
    termo: str, filepath: str, content: str
) -> Optional[re.Match]:
    """A regra de composição, devolvendo ONDE ela casou."""
    texto = _normalizar_sem_acentos(content)
    for padrao in _padroes_composicao_do_termo(termo, filepath):
        match = re.search(padrao, texto)
        if match:
            return match
    return None


def _conteudo_comprova_composicao_do_termo(
    termo: str, filepath: str, content: str
) -> bool:
    """O boletim comprova que o produto PRODUZ ou COMPÕE algo daquele tipo.

    Versão genérica de `_conteudo_comprova_tipo_elastomero`. Distinta da
    identidade de propósito: "sistema para obtenção de elastômeros" prova
    finalidade, não natureza, e a resposta precisa dizer qual das duas está
    mostrando em vez de tratar como equivalentes.
    """
    return _match_composicao_do_termo(termo, filepath, content) is not None


def _trecho_de_composicao(termo: str, filepath: str, content: str) -> str:
    """Trecho literal do boletim que sustenta a composição comprovada."""
    for padrao in _padroes_composicao_do_termo(termo, filepath):
        trecho = _trecho_em_volta(padrao, content, _LARGURA_TRECHO_EVIDENCIA)
        if trecho:
            return trecho
    return ""


def listar_produtos_por_tipo(
    termo: str,
    listar_todos: bool = False,
    sinonimos: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Responde "quais produtos são X" com os quatro níveis de evidência.

    Faz UMA varredura da coleção e devolve os quatro níveis calculados, mais
    `nivel_atendido`: o mais forte que encontrou algo. Quem chama decide como
    apresentar, mas tem todos os níveis à mão — é o que permite responder
    "nenhum boletim declara isso, porém 45 produtos compõem sistemas desse
    tipo, veja" em vez de encerrar em "não encontrei".

    `sinonimos` são termos equivalentes vindos de fora (a tradução
    leigo→técnico de `app.rag.query_expansion`, por exemplo, que transforma
    "borracha" em "elastômero"). Entram em todos os níveis junto com o termo
    original, para que a terminologia do usuário não precise coincidir com a
    do acervo.

    Cada nível traz `evidencias`: por produto listado, o documento de origem e
    — nos três níveis textuais — o trecho literal do boletim que o aceitou.
    Ver o comentário sobre evidência acima de `_LARGURA_TRECHO_EVIDENCIA` e a
    regra de orçamento em `_resumo_lista`.
    """
    termos = [t for t in [termo, *(sinonimos or [])] if t and t.strip()]
    if not termos:
        return {
            "termo_buscado": termo,
            "nivel_atendido": None,
            "niveis": {
                nivel: _resumo_lista(set(), listar_todos, {})
                for nivel in NIVEIS_DE_EVIDENCIA
            },
            "classificacoes_encontradas": [],
        }

    por_nivel: Dict[str, set] = {nivel: set() for nivel in NIVEIS_DE_EVIDENCIA}
    # nível -> produto -> evidência. `setdefault` garante a restrição de custo:
    # a PRIMEIRA evidência encontrada fica, as seguintes são descartadas.
    evidencia_por_nivel: Dict[str, Dict[str, Dict[str, Any]]] = {
        nivel: {} for nivel in NIVEIS_DE_EVIDENCIA
    }
    excluidos: set = set()

    # Invariantes do laço, calculados UMA vez. Estavam sendo recalculados por
    # ponto — e o acervo real tem ~11.000 pontos, então isso sozinho custava
    # segundos por pergunta (revisão de 18/09/2026), numa chamada síncrona que
    # o vendedor espera na tela.
    formas_pedidas = {forma for t in termos for forma in _variantes_do_termo(t)}
    naturezas_excludentes_aplicaveis = [
        natureza
        for natureza in _NATUREZAS_EXCLUDENTES
        if _normalizar_sem_acentos(natureza) not in formas_pedidas
    ]

    try:
        client = get_qdrant_client()
        classificacoes_por_produto: Dict[str, set] = {}
        nomes_classificacoes: Dict[str, str] = {}
        # produto -> rótulo -> documento onde a hierarquia foi lida. O nível
        # estrutural só é resolvido DEPOIS da varredura (a interseção com os
        # alvos depende de conhecer todas as classificações do acervo), então
        # a origem precisa esperar aqui até lá.
        documento_da_classificacao: Dict[str, Dict[str, str]] = {}
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath", "content"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                payload = ponto.payload or {}
                filepath = (payload.get("filepath") or "").strip()
                produto = _produto_do_filepath(filepath)
                if not produto:
                    continue
                content = payload.get("content") or ""
                nome_arquivo = _SEPARADOR_CAMINHO.split(filepath)[-1]

                # Nível 1 — hierarquia do catálogo, independente do texto.
                if _documento_atual_com_produto_catalogavel(filepath, produto):
                    for rotulo, nome in _classificacoes_do_filepath(filepath, produto):
                        classificacoes_por_produto.setdefault(produto, set()).add(rotulo)
                        nomes_classificacoes.setdefault(rotulo, nome)
                        documento_da_classificacao.setdefault(
                            produto, {}
                        ).setdefault(rotulo, nome_arquivo)

                # Natureza conflitante declarada: tira o produto dos níveis de
                # classificação (1 a 3), nunca do nível de menção.
                for natureza in naturezas_excludentes_aplicaveis:
                    if _conteudo_declara_produto_como(natureza, filepath, content):
                        excluidos.add(produto)

                for t in termos:
                    # A EVIDÊNCIA É RECORTADA DA MESMA REGRA QUE ACEITOU. Nada
                    # aqui muda quem entra em cada nível: as condições são as
                    # de sempre; só se acrescenta de onde veio a prova.
                    if _conteudo_declara_produto_como(t, filepath, content):
                        por_nivel["identidade_declarada"].add(produto)
                        evidencia_por_nivel["identidade_declarada"].setdefault(
                            produto,
                            {
                                "tipo_de_prova": "textual",
                                "documento": nome_arquivo,
                                "trecho": _trecho_em_volta(
                                    _padrao_identidade_declarada(t, filepath),
                                    content,
                                    _LARGURA_TRECHO_EVIDENCIA,
                                ),
                            },
                        )
                    if _conteudo_comprova_composicao_do_termo(t, filepath, content):
                        por_nivel["composicao_comprovada"].add(produto)
                        evidencia_por_nivel["composicao_comprovada"].setdefault(
                            produto,
                            {
                                "tipo_de_prova": "textual",
                                "documento": nome_arquivo,
                                "trecho": _trecho_de_composicao(t, filepath, content),
                            },
                        )
                    if _termo_bate_no_conteudo(t, content.lower()):
                        por_nivel["mencao_no_documento"].add(produto)
                        evidencia_por_nivel["mencao_no_documento"].setdefault(
                            produto,
                            {
                                "tipo_de_prova": "textual",
                                "documento": nome_arquivo,
                                "trecho": _trecho_em_volta(
                                    _padrao_do_termo_no_conteudo(t),
                                    content,
                                    _LARGURA_TRECHO_EVIDENCIA,
                                ),
                            },
                        )
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    disponiveis = set(nomes_classificacoes)
    alvos: set = set()
    for t in termos:
        alvos |= _resolver_classificacoes_catalogo(t, disponiveis)
    por_nivel["classificacao_estrutural"] = {
        produto
        for produto, classificacoes in classificacoes_por_produto.items()
        if classificacoes.intersection(alvos)
    }
    # A PROVA ESTRUTURAL NÃO É UM TRECHO DE TEXTO. Ela é o caminho na árvore do
    # catálogo: o produto está dentro da pasta daquela linha. Inventar um
    # "trecho" aqui seria atribuir ao boletim uma frase que ele não tem — e é
    # justamente esse nível que a resposta apresenta como a evidência MAIS
    # FORTE. A estrutura diz isso em `tipo_de_prova`, e quem apresenta cita a
    # classificação e o documento, não uma citação inexistente.
    for produto in por_nivel["classificacao_estrutural"]:
        rotulo = sorted(classificacoes_por_produto[produto].intersection(alvos))[0]
        evidencia_por_nivel["classificacao_estrutural"][produto] = {
            "tipo_de_prova": "estrutural",
            "classificacao": nomes_classificacoes[rotulo],
            "documento": documento_da_classificacao.get(produto, {}).get(rotulo, ""),
        }

    # Aditivos/catalisadores e naturezas conflitantes saem dos níveis que
    # afirmam CLASSIFICAÇÃO. O nível de menção é explicitamente "apareceu no
    # documento" e não afirma nada sobre o produto, então não filtra.
    auxiliares = {
        p
        for p in por_nivel["identidade_declarada"] | por_nivel["composicao_comprovada"]
        if _produto_e_familia_auxiliar(p, termos)
    }
    for nivel in ("identidade_declarada", "composicao_comprovada"):
        por_nivel[nivel] -= excluidos | auxiliares

    nivel_atendido = next(
        (nivel for nivel in NIVEIS_DE_EVIDENCIA if por_nivel[nivel]), None
    )
    return {
        "termo_buscado": termo,
        "termos_pesquisados": termos,
        "nivel_atendido": nivel_atendido,
        "niveis": {
            nivel: _resumo_lista(
                por_nivel[nivel], listar_todos, evidencia_por_nivel[nivel]
            )
            for nivel in NIVEIS_DE_EVIDENCIA
        },
        "classificacoes_encontradas": sorted(nomes_classificacoes[a] for a in alvos),
    }


def buscar_evidencias_de_aplicacao_explicita(termos_busca: List[str]) -> List[Dict[str, Any]]:
    """Localiza menções literais da aplicação no Boletim do próprio produto.

    Esta consulta é deliberadamente mais estrita que uma busca semântica. Uma
    categoria vizinha, como ``automotivo``, não comprova uma aplicação mais
    específica, como ``assento de ônibus``. FISPQ e certificado também não são
    usados como evidência de aplicação.
    """
    termos = [termo.strip() for termo in termos_busca if termo and termo.strip()]
    if not termos:
        return []

    try:
        client = get_qdrant_client()
        encontrados: Dict[str, Dict[str, set]] = {}
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
                with_payload=["filepath", "content"],
                with_vectors=False,
                limit=1000,
                offset=offset,
            )
            for ponto in pontos:
                payload = ponto.payload or {}
                filepath = payload.get("filepath") or ""
                nome_arquivo = _SEPARADOR_CAMINHO.split(filepath)[-1]
                if "boletim" not in _normalizar_sem_acentos(nome_arquivo):
                    continue

                produto = _produto_do_filepath(filepath)
                if not produto:
                    continue

                content = payload.get("content") or ""
                termos_encontrados = [
                    termo for termo in termos if _termo_bate_no_conteudo(termo, content)
                ]
                if not termos_encontrados:
                    continue

                evidencia = encontrados.setdefault(
                    produto, {"documentos": set(), "termos_encontrados": set()}
                )
                evidencia["documentos"].add(nome_arquivo)
                evidencia["termos_encontrados"].update(termos_encontrados)
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    return [
        {
            "produto": produto,
            "documentos": sorted(dados["documentos"]),
            "termos_encontrados": sorted(dados["termos_encontrados"]),
        }
        for produto, dados in sorted(encontrados.items())
    ]
