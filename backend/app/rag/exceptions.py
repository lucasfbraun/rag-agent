class RetrievalIndisponivelError(Exception):
    """Levantado quando o Qdrant/embedding falha de verdade (conexão, timeout,
    erro na busca) — ver AUD-003 em docs/auditoria_2026-08-25.md.

    Não confundir com coleção ainda não ingerida: esse é um estado normal do
    sistema (ninguém rodou a ingestão ainda) e continua retornando lista
    vazia. Só uma falha real levanta esta exceção, para que quem chama decida
    explicitamente o que fazer (503 no endpoint, evento de erro no streaming)
    em vez do agente responder de "conhecimento geral" achando que o catálogo
    só está vazio.

    Módulo próprio (não em engine.py) para evitar import circular: engine.py
    importa app.mcp.pu_mcp_server (ferramentas MCP), e módulos que precisam
    desta exceção (catalog_stats.py, pu_mcp_server.py) não podem importar
    engine.py de volta.
    """
