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


def _produto_do_filepath(filepath: str) -> Optional[str]:
    """Extrai o nome da pasta-produto mais próxima do arquivo, pulando
    subpastas administrativas/de referência conhecidas. Retorna None se o
    caminho não tiver profundidade suficiente para conter uma pasta de
    produto, ou se nenhuma pasta não-administrativa aparecer dentro do
    limite de busca."""
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


def _conteudo_comprova_tipo_elastomero(filepath: str, content: str) -> bool:
    """True somente para evidência positiva no Boletim do próprio produto.

    "Aditivo para elastômeros" e "catalisador usado em elastômeros" são
    relações de uso, não classificação da natureza do produto.
    """
    nome_arquivo = _SEPARADOR_CAMINHO.split(filepath)[-1]
    if "boletim" not in _normalizar_sem_acentos(nome_arquivo):
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

    def _flexoes(palavra: str) -> set[str]:
        """Flexões conservadoras, suficientes para singular/plural sem usar
        prefixos abertos que confundem `correia` com `corretamente`."""
        if palavra in {"pu", "pus", "poliuretano", "poliuretanos"}:
            return {"pu", "pus", "poliuretano", "poliuretanos"}
        variantes = {palavra}
        if palavra.endswith("ao"):
            variantes.add(f"{palavra[:-2]}oes")
        elif palavra.endswith("oes"):
            variantes.add(f"{palavra[:-3]}ao")
        elif palavra.endswith("s"):
            variantes.add(palavra[:-1])
        else:
            variantes.add(f"{palavra}s")
        return variantes

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


def _codigo_bate_no_texto(codigo: str, texto: str) -> bool:
    """Confirma código completo, tolerando espaço, hífen ou quebra de linha."""
    partes = re.findall(r"[a-z0-9]+", _normalizar_sem_acentos(codigo))
    if not partes:
        return False
    separador = r"[\s\-_®]*"
    padrao = r"(?<![a-z0-9])" + separador.join(map(re.escape, partes))
    padrao += r"(?![a-z0-9])"
    return bool(re.search(padrao, _normalizar_sem_acentos(texto)))


def _trecho_da_mencao(codigo: str, content: str, largura: int = 320) -> str:
    """Recorta contexto suficiente para distinguir uso, comparação e negação."""
    texto = re.sub(r"\s+", " ", content or "").strip()
    partes = re.findall(r"[a-z0-9]+", _normalizar_sem_acentos(codigo))
    if not texto or not partes:
        return ""
    padrao = r"(?<![a-z0-9])" + r"[\s\-_®]*".join(map(re.escape, partes))
    padrao += r"(?![a-z0-9])"
    match = re.search(padrao, _normalizar_sem_acentos(texto))
    if not match:
        return ""
    metade = largura // 2
    inicio = max(0, match.start() - metade)
    fim = min(len(texto), match.end() + metade)
    prefixo = "…" if inicio else ""
    sufixo = "…" if fim < len(texto) else ""
    return f"{prefixo}{texto[inicio:fim].strip()}{sufixo}"


def buscar_produtos_que_mencionam(
    codigo: str,
    incluir_sensivel: bool = False,
) -> List[Dict[str, Any]]:
    """Busca reversa completa: outros Boletins que mencionam ``codigo``.

    O catálogo inteiro é paginado até o fim e o resultado não usa top-k nem
    prévia. A confirmação local cobre código separado por espaço, hífen ou
    quebra de linha, sem depender da tokenização do índice textual do Qdrant.
    O documento do próprio produto é excluído, pois seu cabeçalho normalmente
    menciona o código sem representar uma relação com outro item.
    """
    codigo = (codigo or "").strip()
    if not codigo:
        return []
    encontrados: Dict[str, Dict[str, Any]] = {}
    try:
        client = get_qdrant_client()
        offset = None
        while True:
            pontos, offset = client.scroll(
                collection_name=COLLECTION_NAME,
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
                    or _codigo_bate_no_texto(codigo, produto)
                    or _codigo_bate_no_texto(codigo, filename)
                ):
                    continue
                if not _codigo_bate_no_texto(codigo, content):
                    continue
                trecho = _trecho_da_mencao(codigo, content)
                dados = encontrados.setdefault(
                    produto, {"documentos": set(), "mencoes": []}
                )
                dados["documentos"].add(filename)
                mencao = {"documento": filename, "trecho": trecho}
                if mencao not in dados["mencoes"]:
                    dados["mencoes"].append(mencao)
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    return [
        {
            "produto": produto,
            "documentos": sorted(dados["documentos"]),
            "mencoes": dados["mencoes"],
        }
        for produto, dados in sorted(encontrados.items())
    ]


def _resumo_lista(produtos: set, listar_todos: bool) -> Dict[str, Any]:
    lista = sorted(produtos)
    limite = None if listar_todos else 10
    return {
        "total": len(lista),
        "produtos": lista if limite is None else lista[:limite],
        "truncado": limite is not None and len(lista) > limite,
    }


def listar_produtos_por_aplicacao(termo_busca: str = "", listar_todos: bool = False) -> Dict[str, Any]:
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

    `listar_todos` (pedido do usuário): por padrão cada bucket devolve só
    uma prévia (10 produtos) + o total real, pra o agente perguntar se o
    vendedor quer a lista completa antes de despejar dezenas/centenas de
    nomes. Quando `listar_todos=True`, cada bucket devolve TODOS sem
    nenhum limite, não importa quantos sejam."""
    try:
        client = get_qdrant_client()
        produtos_por_nome = set()
        produtos_por_conteudo = set()
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

    return {
        "termo_buscado": termo_busca,
        "por_nome_ou_familia": _resumo_lista(produtos_por_nome, listar_todos),
        "por_aplicacao_ou_tipo": _resumo_lista(produtos_por_conteudo, listar_todos),
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
