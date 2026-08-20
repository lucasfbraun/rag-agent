# Cronograma — PU Matcher

Cronograma por fases e marcos (sem datas fixas — ritmo definido pelas sessões de desenvolvimento).
Ver progresso detalhado sessão a sessão em [PROGRESS.md](PROGRESS.md).

Legenda de status: ⬜ Não iniciado · 🟨 Em andamento · ✅ Concluído · 🚫 Bloqueado

---

## Fase 0 — Setup do Ambiente
**Status:** 🟨 Em andamento (código completo e robusto; validação de execução bloqueada por dependências externas)

- [x] Estrutura de diretórios do projeto (`backend/`, `frontend/`, `data/`)
- [x] `docker-compose.yml`, Dockerfiles, `requirements.txt`
- [x] `.env.example` (variáveis de ambiente sem segredos reais)
- [x] Código base do backend (FastAPI), frontend (Streamlit), RAG e MCP simulado
- [x] Correção de bugs críticos: idempotência da ingestão, lazy init do Qdrant, suporte a `.txt`, modelos Gemini atualizados
- [x] Endpoint `/api/health` com status do Qdrant e da coleção
- [x] Endpoint `/api/ingest` para disparar reindexacão via REST
- [x] CLI `python -m app.cli ingest` e `python -m app.cli health`
- [x] Healthchecks nos 3 serviços Docker (`depends_on: condition: service_healthy`)
- [x] Indicador de status do backend/Qdrant na sidebar do frontend
- [x] Streaming de resposta: endpoint `POST /api/match/stream` (SSE/NDJSON) + `st.write_stream()` no frontend
- [x] Toggle de streaming na sidebar (ativar/desativar por sessão)
- [x] Scripts de dev local sem Docker (`backend/run_local.py`, `frontend/run_local.py`)
- [ ] Preencher `.env` local com chaves reais de API (Gemini/OpenAI/Anthropic)
- [ ] Validar `docker-compose up -d --build` rodando localmente
- [ ] Confirmar Qdrant acessível em `localhost:6333`

**Dependências:** acesso às chaves de API dos provedores LLM escolhidos; Docker instalado no servidor.

---

## Fase 1 — Ingestão de Dados Reais
**Status:** ⬜ Não iniciado

- [ ] Levantar acervo real de TDS, catálogos e laudos de homologação (PDF/DOCX)
- [ ] Definir volume inicial de teste (ex: 20–50 documentos representativos)
- [ ] Rodar `ingest_catalog_directory()` sobre os documentos reais
- [ ] Validar qualidade da extração de texto/tabelas (especialmente tabelas técnicas em PDF)
- [ ] Ajustar `chunk_size`/`overlap` conforme padrão dos documentos da empresa

**Dependências:** Fase 0 concluída; acesso aos documentos técnicos da empresa (SharePoint/OneDrive ou pasta local).

---

## Fase 2 — Motor RAG & Agente Investigativo
**Status:** ⬜ Não iniciado

- [ ] Testar fluxo conversacional investigativo (perguntas antes da recomendação)
- [ ] Validar qualidade do retrieval (top_k, relevância dos trechos retornados)
- [ ] Ajustar `AGENT_SYSTEM_PROMPT` com terminologia e critérios reais da empresa
- [ ] Testar comportamento "opinativo" em casos de requisitos incompatíveis
- [ ] Avaliar necessidade de reranking ou filtros por metadados (família química, norma)

**Dependências:** Fase 1 concluída (dados reais indexados).

---

## Fase 3 — Templates de Resposta
**Status:** ⬜ Não iniciado

- [ ] Validar os 3 templates padrão (`proposta_tecnica_completa`, `comercial_rapido`, `parecer_interno_engenharia`) com o time comercial
- [ ] Ajustar campos obrigatórios conforme identidade visual/técnica da empresa
- [ ] Definir se templates ficam hardcoded (`templates.py`) ou parametrizáveis via UI/BD (Módulo 3 da proposta)

**Dependências:** feedback do time comercial/P&D sobre formato ideal.

---

## Fase 4 — Integrações MCP / ERP Reais
**Status:** ⬜ Não iniciado (atualmente `pu_mcp_server.py` retorna dados simulados)

- [ ] Mapear endpoints reais do ERP (SAP/TOTVS/outro) para consulta de catálogo e estoque
- [ ] Mapear fonte real de laudos de homologação (LIMS ou repositório de qualidade)
- [ ] Substituir `consultar_catalogo_erp` e `consultar_normas_homologadas` por chamadas reais
- [ ] Definir autenticação/segurança da integração (rede interna, VPN, credenciais de serviço)

**Dependências:** acesso e credenciais aos sistemas ERP/LIMS da empresa; definição de responsável de TI para a integração.

---

## Fase 5 — RBAC & Governança
**Status:** ⬜ Não iniciado

- [ ] Modelar perfis de usuário (Vendedor, Técnico de Aplicação, Gestor Comercial, Químico/P&D, Admin TI) conforme matriz da proposta
- [ ] Implementar autenticação (login) e controle de acesso por perfil
- [ ] Restringir campos sensíveis (custos industriais, fórmulas) por perfil
- [ ] Persistir usuários/perfis em PostgreSQL (hoje ainda não implementado no código base)

**Dependências:** definição de como os usuários serão provisionados (AD/LDAP corporativo vs. cadastro manual).

---

## Fase 6 — Frontend / UX de Campo
**Status:** ⬜ Não iniciado (protótipo funcional em Streamlit já existe)

- [ ] Testar usabilidade em tablet/mobile na intranet/VPN
- [ ] Refinar identidade visual (hoje usa ícone genérico de placeholder)
- [ ] Avaliar se Streamlit atende ao produto final ou se migra para app web dedicado

**Dependências:** Fases 2–3 estáveis; feedback de uso real em campo.

---

## Fase 7 — Testes com Usuários Piloto
**Status:** ⬜ Não iniciado

- [ ] Selecionar grupo piloto de vendedores/técnicos de campo
- [ ] Rodar testes com casos reais de clientes (histórico recente de demandas)
- [ ] Coletar métricas: taxa de acerto do match, tempo de resposta, satisfação do vendedor
- [ ] Ajustar prompt/templates com base no feedback

**Dependências:** Fases 1–6 estáveis o suficiente para uso supervisionado.

---

## Fase 8 — Deploy On-Premise em Produção & Monitoramento
**Status:** ⬜ Não iniciado

- [ ] Definir servidor de produção (specs mínimas: 8 cores, 16–32GB RAM, SSD 200GB+)
- [ ] Configurar backup do Qdrant e PostgreSQL
- [ ] Definir monitoramento (logs, uptime, custo de uso das APIs de LLM)
- [ ] Rollout gradual por perfil de usuário

**Dependências:** aprovação do piloto (Fase 7); infraestrutura de servidor on-premise disponível.

---

## Como este cronograma é atualizado
A cada sessão de trabalho, o status das fases/itens acima é revisado e o arquivo [PROGRESS.md](PROGRESS.md) recebe uma nova entrada de log. Marcos concluídos são movidos de ⬜/🟨 para ✅.
