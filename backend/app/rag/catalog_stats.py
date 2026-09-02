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
from typing import Any, Dict, Optional

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


def _termo_para_busca(termo: str) -> str:
    """Stem simplificado: corta os 2 últimos caracteres de termos com 6+
    letras. Por quê: plural/singular em português nem sempre compartilha
    sufixo literal — "colchão" NÃO é substring de "colchões" (ã vs õ) — mas
    "colch" é prefixo comum aos dois. Termos curtos (<6) ficam como estão:
    cortar demais viraria ruído (ex: "cola" -> "col" bateria em "coluna",
    "colar", que não têm nada a ver)."""
    termo = termo.strip().lower()
    return termo[:-2] if len(termo) >= 6 else termo


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


def _termo_bate_no_conteudo(termo_busca: str, content_lower: str) -> bool:
    """True se `termo_busca` aparece no conteúdo do documento.

    Achado real: buscar "CAT" (família de produto) com substring simples
    ("cat" in content) batia em "catálise"/"catalisador" — termos reais de
    química que aparecem no conteúdo de ~500 produtos sem NENHUMA relação
    com a família "CAT" — inflando o resultado de ~36 (correto, por nome)
    para ~550.

    Frase de várias palavras (ex: "construção civil"): risco de falso
    positivo por fragmento de palavra é praticamente zero — continua
    substring simples, mesmo comportamento de antes. Termo de 1 palavra é
    onde mora o risco: longos (>=6 letras, mesmo corte de
    _termo_para_busca) usam PREFIXO de palavra, pra cobrir plural em
    português ("colch" prefixo de "colchão"/"colchões"); curtos exigem a
    palavra INTEIRA, sem folga de prefixo — "cat" nunca é prefixo válido
    pra decidir sozinho, tem gente demais nesse balaio (catálise,
    catalisador, categoria...)."""
    termo_lower = termo_busca.strip().lower()
    stem = _termo_para_busca(termo_busca)

    if " " in termo_lower:
        return stem in content_lower

    palavras = _SEPARADOR_PALAVRA_CONTEUDO.split(content_lower)
    termo_foi_cortado = stem != termo_lower
    if termo_foi_cortado:
        return any(p.startswith(stem) for p in palavras if p)
    return stem in palavras


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
    - `por_aplicacao_ou_tipo`: o termo (com stem simplificado) aparece no
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
                if _termo_bate_no_conteudo(termo_busca, content):
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
