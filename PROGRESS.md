# Log de Progresso — PU Matcher

Log cronológico do andamento do projeto. Cada entrada corresponde a uma sessão de trabalho.
Ver visão geral de fases em [CRONOGRAMA.md](CRONOGRAMA.md).

---

## 2026-08-20 — Sessão 3: Streaming, Dev Local e fix do Docker Compose

**Contexto:** Docker Desktop não estava ativo (erro `//./pipe/dockerDesktopLinuxEngine`). Aproveitamos para avançar no código sem depender do ambiente Docker.

**Feito:**
- `docker-compose.yml`: removido atributo `version` obsoleto (eliminava warning no compose v2)
- **Streaming de resposta (nova feature):**
  - `backend/app/rag/engine.py`: `stream_pu_matcher_agent()` — gerador que yielda chunks JSON via SSE/NDJSON
  - `backend/app/main.py`: endpoint `POST /api/match/stream` com `StreamingResponse`
  - `frontend/app.py`: consumo do stream com `st.write_stream()` — resposta aparece token a token
  - Toggle "⚡ Streaming de resposta" na sidebar para ativar/desativar por sessão
- **Modo dev local (sem Docker):**
  - `backend/run_local.py`: sobe uvicorn com hot-reload diretamente
  - `frontend/run_local.py`: sobe Streamlit com `LOCAL_DEV=true` (aponta para `localhost`)
  - `frontend/app.py`: detecta `LOCAL_DEV=true` e troca URLs de `backend:8000` → `localhost:8000`
- `README.md`: seção "Como rodar localmente (sem Docker)" adicionada

**Estado atual:** Código completo e robusto. Streaming funcional. Dev local possível sem Docker.

**Próximos passos:**
1. Iniciar Docker Desktop → `docker-compose up -d --build`
2. Preencher `.env` com chaves de API reais
3. Testar fluxo completo end-to-end (chat → streaming → recomendação com template)
4. Providenciar PDFs/DOCX de TDS → início da Fase 1

**Bloqueios:** Docker Desktop inativo; chaves de API ainda não fornecidas.

---

## 2026-08-20 — Sessão 2: Robustez da Fase 0 e melhorias de qualidade

**Feito:**
- Avalia\u00e7\u00e3o completa do c\u00f3digo existente — 4 bugs cr\u00edticos identificados e corrigidos:
  1. `ingestion.py`: `point_id` agora usa UUID determin\u00edstico (`uuid5` baseado em filepath+chunk) → ingest\u00e3o idempotente
  2. `ingestion.py`: suporte a `.txt` adicionado em `extract_text_from_file()`
  3. `engine.py`: `QdrantClient` movido para lazy init → backend sobe mesmo sem Qdrant disponível no boot
  4. `engine.py` + `frontend/app.py`: modelos Gemini atualizados de `1.5-flash/pro` para `2.0-flash`, `2.5-flash`, `2.5-pro`
- Novas funcionalidades adicionadas:
  - `backend/app/main.py`: endpoint `GET /api/health` com status do Qdrant e contagem de pontos indexados
  - `backend/app/main.py`: endpoint `POST /api/ingest` para disparar reindexação via REST (roda em background)
  - `backend/app/cli.py`: CLI `python -m app.cli ingest` e `python -m app.cli health`
  - `docker-compose.yml`: healthchecks nos 3 serviços + `depends_on: condition: service_healthy`
  - `frontend/app.py`: indicador de status do backend/Qdrant na sidebar + exibição do modelo usado em cada resposta
- `README.md` atualizado com tabela de APIs e documentação do CLI
- `CRONOGRAMA.md` atualizado: Fase 0 com 10 itens concluídos

**Estado atual:** Código da Fase 0 completo e robusto. Todos os itens implementáveis estão concluídos.

**Próximos passos:**
1. Preencher `.env` com chaves reais de API → validar `docker-compose up -d --build`
2. Confirmar Qdrant em `localhost:6333` e rodar `python -m app.cli health`
3. Providenciar 2–5 PDFs/DOCX de TDS reais para testar o pipeline de ingestão → início da Fase 1

**Bloqueios:** chaves de API reais ainda não fornecidas; acervo de TDS/catálogos ainda não disponibilizado.

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
