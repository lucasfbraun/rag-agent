# Cronograma — PU Matcher

Cronograma por fases e marcos (sem datas fixas — ritmo definido pelas sessões de desenvolvimento).
Ver progresso detalhado sessão a sessão em [PROGRESS.md](PROGRESS.md).

Legenda de status: ⬜ Não iniciado · 🟨 Em andamento · ✅ Concluído · 🚫 Bloqueado

---

## Fase 0 — Setup do Ambiente
**Status:** ✅ Concluído (ambiente Docker validado e rodando; aguardando chaves de API e documentos reais para Fase 1)

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
- [x] Migração do modelo de embedding para Gemini (`text-embedding-004`, 768 dims) via `EMBEDDING_MODEL`/`VECTOR_SIZE` — alinhado com a chave de API já disponível
- [x] Preencher `.env` local com `GEMINI_API_KEY` real — **concluído** (OpenAI/Anthropic/Grok seguem em branco, não bloqueia)
- [x] Validar `docker-compose up -d --build` rodando localmente — **concluído ✅ (3/3 containers Healthy)**
- [x] Confirmar Qdrant acessível em `localhost:6333` — **concluído ✅ (`/api/health` retorna online)**

**Dependências:** acesso às chaves de API dos provedores LLM escolhidos; Docker instalado no servidor.

**⚠️ Risco aberto:** a `GEMINI_API_KEY` atual está num tier de quota bem restrito (`quotaValue: "20"` observado em erro 429) — modelos antigos hardcoded no código (`gemini-2.0-flash`, `text-embedding-004`) já foram descontinuados pelo Google e foram trocados por `gemini-flash-latest`/`gemini-embedding-001` (2026-08-21). Verificar se há billing habilitado antes de qualquer uso em volume.

---

## Fase 1 — Ingestão de Dados Reais
**Status:** 🟨 Em andamento (primeira ingestão real feita e validada — falta o acervo completo)

- [x] Levantar acervo real de TDS, catálogos e laudos de homologação — pasta de rede identificada: `\\10.1.1.205\flexivel\GRUPOS\Qualidade\Documentação de Produto` (~37 famílias de produto, ex. FLEXX® AG, BT, CAT, HR, RIM etc., PDF+DOC)
- [x] Script `ingest_network.py` criado para apontar a ingestão à pasta de rede (`--test` = 1 família de produto / `--full` = acervo completo, ~12k arquivos)
- [x] Definir volume inicial de teste — subconjunto `FLEXX® AG` (71 arquivos PDF/DOC) escolhido como piloto via `--test`
- [x] Rodar `ingest_catalog_directory()` sobre os documentos reais — **feito 2026-08-21**: 52 trechos indexados de 39 arquivos (só PDFs; motor 100% local/gratuito via Ollama)
- [x] Validar qualidade do retrieval — pergunta de teste sobre "FLEXX AG 2047" retornou o boletim correto como top resultado (score 0.86)
- [ ] Rodar `--full` sobre o acervo completo (~12k arquivos) — ainda não feito, avaliar tempo antes de comprometer horas rodando em CPU local
- [ ] Ajustar `chunk_size`/`overlap` conforme padrão dos documentos da empresa
- [ ] Resolver `.doc` legado — `python-docx` só lê `.docx`; confirmado por assinatura de arquivo (OLE2) que são Word 97-2003 binário real, não corrupção. 30 dos 69 arquivos testados foram pulados por isso
- [ ] Confirmar billing/quota da `GEMINI_API_KEY`/OpenAI antes de usar esses provedores em volume (hoje sem crédito nos dois — ver Fase 0)

**Dependências:** Fase 0 concluída ✅; acesso aos documentos técnicos da empresa — ✅ pasta de rede acessível a partir desta máquina (`\\10.1.1.205\flexivel`, testado 2026-08-21).

**⚠️ Bug crítico corrigido nesta fase:** a busca RAG (`retrieve_products_context`) estava **quebrada desde a Fase 0** — `qdrant-client` sem teto de versão instalava sempre a última (1.19.0), incompatível com o servidor Qdrant pinado (`v1.9.2`). O erro ficava mascarado por um `try/except` amplo que devolvia lista vazia, então o chat sempre respondia normalmente (via ferramenta MCP simulada ou conhecimento geral do modelo) sem nunca sinalizar que a base real nunca era consultada. Corrigido fixando `qdrant-client>=1.9.0,<1.10.0` no `requirements.txt`. Validado end-to-end em 2026-08-21.

---

## Fase 2 — Motor RAG & Agente Investigativo
**Status:** 🟨 Em andamento (retrieval validado; teste de conversa pausado por lentidão da máquina)

- [ ] Testar fluxo conversacional investigativo (perguntas antes da recomendação)
- [x] Validar qualidade do retrieval — testado com múltiplas perguntas reais (ex: "FLEXX AG 2047", "FLEXX ADT 41200"), retornou os documentos corretos como top resultado em todos os casos
- [ ] Ajustar `AGENT_SYSTEM_PROMPT` com terminologia e critérios reais da empresa
- [ ] Testar comportamento "opinativo" em casos de requisitos incompatíveis
- [ ] Avaliar necessidade de reranking ou filtros por metadados (família química, norma)

**Dependências:** Fase 1 concluída (dados reais indexados).

**🚫 Bloqueio atual:** teste de conversa (qualidade da resposta gerada, não do retrieval) pausado — máquina de dev está anormalmente lenta pra inferência local (ver PROGRESS.md Sessão 9: resposta trivial sem contexto levou 278s com modelo de 3B). Retoma quando a máquina normalizar ou quando Gemini/OpenAI tiverem crédito de novo.

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
