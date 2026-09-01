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
}


def _produto_do_filepath(filepath: str) -> Optional[str]:
    """Extrai o nome da pasta-produto mais próxima do arquivo, pulando
    subpastas administrativas conhecidas. Retorna None se o caminho não tiver
    profundidade suficiente para conter uma pasta de produto."""
    partes = [p.strip() for p in _SEPARADOR_CAMINHO.split(filepath) if p.strip()]
    if len(partes) < 2:
        return None
    for pasta in reversed(partes[:-1]):
        if pasta.lower() not in _PASTAS_NAO_PRODUTO:
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
