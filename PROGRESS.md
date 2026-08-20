# Log de Progresso — PU Matcher

Log cronológico do andamento do projeto. Cada entrada corresponde a uma sessão de trabalho.
Ver visão geral de fases em [CRONOGRAMA.md](CRONOGRAMA.md).

---

## 2026-08-20 — Kickoff do desenvolvimento

**Feito:**
- Análise dos documentos-base do projeto (`docs/proposta_do_projeto_similaridade.md` e `docs/guia_mvp_e_codigo_similaridade.md`)
- Estrutura de diretórios do MVP criada em `c:\rag` (backend, frontend, data)
- Scaffold completo do código do guia técnico:
  - `docker-compose.yml`, `Dockerfile.backend`, `Dockerfile.frontend`, `requirements.txt`, `.env.example`
  - `backend/app/main.py` (API FastAPI)
  - `backend/app/templates.py` (3 templates de resposta padronizados)
  - `backend/app/mcp/pu_mcp_server.py` (ferramentas MCP simuladas: catálogo ERP e normas)
  - `backend/app/rag/ingestion.py` e `backend/app/rag/engine.py` (ingestão e agente investigativo RAG)
  - `frontend/app.py` (interface de chat Streamlit)
- `CRONOGRAMA.md` criado com 9 fases (0 a 8)
- Repositório git inicializado

**Estado atual:** MVP ainda não executado — código é o scaffold do guia, com dados/ferramentas ERP simulados.

**Próximos passos (Fase 0 e 1):**
1. Preencher `.env` com chaves reais de API
2. Rodar `docker-compose up -d --build` e validar os 3 serviços (qdrant, backend, frontend)
3. Levantar acervo real de TDS/catálogos para iniciar a ingestão (Fase 1)

**Bloqueios/pendências:** nenhuma chave de API real fornecida ainda; acervo real de documentos técnicos ainda não disponibilizado.
