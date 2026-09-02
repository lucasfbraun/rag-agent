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


def listar_produtos_por_aplicacao(termo_busca: str, listar_todos: bool = False) -> Dict[str, Any]:
    """Lista produtos distintos do acervo cujo conteúdo menciona o termo
    dado — tanto aplicação/uso ("colchão", "cortiça") quanto tipo/natureza
    do produto ("cola", "espuma"); a busca é neutra quanto a isso, é uma
    correspondência de palavra-chave no texto do documento, não importa se a
    palavra descreve pra que o produto serve ou o que ele é.

    Por quê existe separado de retrieve_products_context: um pedido de
    LISTAGEM/categoria ("produtos para colchão", "produtos que são colas")
    não é a mesma coisa que um
    pedido de recomendação única — um top-k de poucos chunks (mesmo com a
    busca híbrida) nunca representa fielmente uma categoria com dezenas de
    produtos (achado real: "colchão" aparece em ~150 arquivos/dezenas de
    produtos distintos do acervo; duas variações de pergunta traziam
    conjuntos de top-6 sem nenhuma sobreposição). Isso varre a coleção
    inteira e devolve os NOMES dos produtos, não os trechos de texto — quem
    quiser detalhe de um item específico faz uma pergunta de acompanhamento,
    que aí sim usa retrieve_products_context normalmente.

    `listar_todos` (pedido do usuário): por padrão devolve só uma prévia
    (10 produtos) + o total real encontrado, pra o agente perguntar se o
    vendedor quer a lista completa antes de despejar dezenas de nomes.
    Quando `listar_todos=True`, devolve TODOS sem nenhum limite, não importa
    quantos sejam."""
    try:
        client = get_qdrant_client()
        stem = _termo_para_busca(termo_busca)
        produtos = set()
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
                content = (payload.get("content") or "").lower()
                if stem and stem in content:
                    produto = _produto_do_filepath(payload.get("filepath") or "")
                    if produto:
                        produtos.add(produto)
            if offset is None:
                break
    except Exception as e:
        raise RetrievalIndisponivelError(str(e)) from e

    lista = sorted(produtos)
    limite = None if listar_todos else 10
    return {
        "termo_buscado": termo_busca,
        "total_produtos_encontrados": len(lista),
        "produtos": lista if limite is None else lista[:limite],
        "truncado": limite is not None and len(lista) > limite,
    }
