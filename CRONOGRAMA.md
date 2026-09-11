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
- [x] Aplicar migrations do banco automaticamente no boot (**AUD-001, corrigido 2026-08-25**) — `backend/app/startup.py` roda `alembic upgrade head` via `ENTRYPOINT` antes de subir o servidor; stack nova via `docker-compose up` não fica mais com tabela `users` ausente. Ressalva: dev local sem Docker (`backend/run_local.py`) continua sem rodar migration — ver `docs/plano_correcao_auditoria_2026-08-25.md`, ticket 1.

**Nota (2026-08-25/26):** este checklist ainda descreve "3 serviços" e o embedding antigo (`text-embedding-004`/768); a stack real tem 4 serviços (Postgres entrou na Fase 5) e usa `gemini-embedding-001`/3072 (ou Ollama local/768, conforme `.env`). Divergência já catalogada em `docs/auditoria_2026-08-25.md` ("Divergências documentais" #1/#2) — correção proposital adiada para o ticket 12 do plano de correção (alinhar documentação só depois dos bugs de código serem resolvidos), não é um esquecimento.

**Dependências:** acesso às chaves de API dos provedores LLM escolhidos; Docker instalado no servidor.

**⚠️ Risco aberto:** a `GEMINI_API_KEY` atual está num tier de quota bem restrito (`quotaValue: "20"` observado em erro 429) — modelos antigos hardcoded no código (`gemini-2.0-flash`, `text-embedding-004`) já foram descontinuados pelo Google e foram trocados por `gemini-flash-latest`/`gemini-embedding-001` (2026-08-21). Verificar se há billing habilitado antes de qualquer uso em volume.

---

## Fase 1 — Ingestão de Dados Reais
**Status:** 🟨 **Em recuperação — 10.499 de 11.273 pontos históricos (faltam ~774); 3ª tentativa morreu sem progresso e está parada até investigação (Sessões 32/33, 2026-09-02).** Incidente original da Sessão 30 (2026-08-26): um bug na implementação do ticket 7 (reconciliação de índice) apagou os 11.273 pontos reais durante uma verificação — corrigido (ver abaixo), mas as três tentativas de reingestão completa foram interrompidas sem chegar aos 11.273 originais (disco cheio, reinício do Docker e reinício do PC; na terceira a contagem ficou inalterada por ~3h). Não relançar antes de investigar se estava travada. Sem backup/snapshot configurado até a Sessão 31 (ver Fase 8, item adiantado). **Os documentos-fonte nunca foram tocados**, só o índice vetorial. Detalhe do incidente original: `docs/incidente_2026-08-26_reingestao_apagou_colecao.md`.

**⚠️ Achado real na Sessão 32, ainda sem correção retroativa:** auditoria da coleção mostrou que **quase metade dos 10.499 pontos atuais é redundante** (pasta de rede duplicada + PDF/DOCX do mesmo boletim indexados em dobro) — `ingest_catalog_directory()` já ganhou filtros pra não acumular mais duplicata daqui pra frente (ver Fase 2), mas a limpeza do que já está indexado é uma decisão pendente com o usuário (operação destrutiva na coleção de produção), tratada à parte da reingestão atualmente parada.

**✅ AUD-007 corrigido em 2026-08-26 (ticket 7), incluindo o próprio bug do incidente acima:** `ingest_catalog_directory()` agora reconcilia (remove chunk obsoleto de arquivo que encolheu/mudou/saiu do acervo) — mas a primeira versão dessa reconciliação tinha um bug de escopo (tratava qualquer arquivo fora do diretório escaneado nesta execução como "removido", não só os de dentro) que foi exatamente a causa do incidente. Corrigido com `_arquivo_esta_no_escopo()` — só reconcilia arquivos dentro da árvore escaneada; validado com teste de regressão reproduzindo o incidente e revalidado contra o Qdrant real. Corrigidos junto: falha de embedding no meio de um arquivo não grava mais chunks parciais; corrida na criação da coleção não é mais erro fatal. **AUD-008 (validar dimensão do vetor) segue aberto — ticket 8.**

O checklist abaixo (`--full` concluído, 11.273 trechos) descreve o estado **histórico**, não o atual — mantido como registro do que já foi alcançado uma vez, não como afirmação de que os dados ainda estão lá.

- [x] Levantar acervo real de TDS, catálogos e laudos de homologação — pasta de rede identificada: `\\10.1.1.205\flexivel\GRUPOS\Qualidade\Documentação de Produto` (~37 famílias de produto, ex. FLEXX® AG, BT, CAT, HR, RIM etc., PDF+DOC)
- [x] Script `ingest_network.py` criado para apontar a ingestão à pasta de rede (`--test` = 1 família de produto / `--full` = acervo completo, ~12k arquivos)
- [x] Definir volume inicial de teste — subconjunto `FLEXX® AG` (71 arquivos PDF/DOC) escolhido como piloto via `--test`
- [x] Rodar `ingest_catalog_directory()` sobre os documentos reais — **feito 2026-08-21**: 52 trechos indexados de 39 arquivos (só PDFs; motor 100% local/gratuito via Ollama)
- [x] Validar qualidade do retrieval — pergunta de teste sobre "FLEXX AG 2047" retornou o boletim correto como top resultado (score 0.86)
- [x] Validar qualidade da extração de texto/tabelas técnicas em PDF — auditoria de 6 boletins técnicos de produtos diferentes: especificações (viscosidade, NCO%, densidade) saem legíveis com rótulo+unidade+valor juntos, mesmo sem estrutura de tabela preservada (PDFs viram texto corrido; DOCX preserva células separadas por `|`). Suficiente para um LLM interpretar corretamente.
- [x] Rodar `--full` sobre o acervo completo — **concluído 2026-08-22**: **11.273 trechos indexados de 8.377 arquivos** (3.933 arquivos ignorados, majoritariamente `.doc` legado + alguns temporários do Word `~$*.docx`)
- [ ] Ajustar `chunk_size`/`overlap` conforme padrão dos documentos da empresa — não bloqueante, avaliar com uso real
- [ ] Resolver `.doc` legado — `python-docx` só lê `.docx`; confirmado por assinatura de arquivo (OLE2) que são Word 97-2003 binário real, não corrupção. **Decisão do usuário: adiado** (solução exigiria LibreOffice, não instalado nem no host nem no container)
- [ ] Confirmar billing/quota da `GEMINI_API_KEY`/OpenAI antes de usar esses provedores em volume (hoje sem crédito nos dois — ver Fase 0)

**Dependências:** Fase 0 concluída ✅; acesso aos documentos técnicos da empresa — ✅ pasta de rede acessível a partir desta máquina (`\\10.1.1.205\flexivel`, testado 2026-08-21).

**⚠️ Bug crítico corrigido nesta fase:** a busca RAG (`retrieve_products_context`) estava **quebrada desde a Fase 0** — `qdrant-client` sem teto de versão instalava sempre a última (1.19.0), incompatível com o servidor Qdrant pinado (`v1.9.2`). O erro ficava mascarado por um `try/except` amplo que devolvia lista vazia, então o chat sempre respondia normalmente (via ferramenta MCP simulada ou conhecimento geral do modelo) sem nunca sinalizar que a base real nunca era consultada. Corrigido fixando `qdrant-client>=1.9.0,<1.10.0` no `requirements.txt`. Validado end-to-end em 2026-08-21.

---

## Fase 2 — Motor RAG & Agente Investigativo
**Status:** 🟨 Em andamento — **Sessão 40 (2026-09-11): perguntas claramente fora do domínio são recusadas antes do Qdrant e do LLM.** Sessões 39 e 35: requisitos técnicos compostos, consulta por especificação e por seção do boletim. Sessões 32 e 34 (2026-09-02): busca híbrida/listagem e guardrails de recomendação corrigidos. Retrieval puramente semântico (embedding local) provou repetidamente errar em cenários reais; a Sessão 34 corrigiu também o falso positivo `correia` → `corre` (341 resultados), o limite textual global sem ranking e a perda da demanda anterior em follow-ups. Correções explícitas agora persistem como restrições, respostas contraditórias/status comercial sem fonte são bloqueados, e ferramentas simuladas não são mais expostas ao LLM. Ver `PROGRESS.md`, Sessões 32, 34, 39 e 40. Catálogo real ainda **incompleto** (10.499 de 11.273 pontos históricos) — reingestão parada até investigação (ver Fase 1). Auditoria de 2026-08-25 encontrou bugs reais no motor; **AUD-002, AUD-003 e AUD-006 corrigidos em 2026-08-26** (tickets 6, 2 e 5). Ver `docs/plano_correcao_auditoria_2026-08-25.md`.

**✅ Sessão 35 (2026-09-09) — consulta por especificação técnica e por seção do boletim:**
- **Terceira dimensão de busca no acervo** (as duas primeiras: nome/família e aplicação/tipo, Sessão 32): o VALOR da especificação. `backend/app/rag/spec_search.py` lê a tabela do boletim por regra (20 propriedades canônicas), interpreta a pergunta ("hidroxila de 180", "viscosidade acima de 5000 cPs", "NCO entre 12 e 13%", "tempo de reação de 45 s") e varre o acervo inteiro — top-k de similaridade não serve aqui, porque o embedding não compara grandezas e traz o valor errado com cara de certo. Exposto como ferramenta MCP `consultar_produtos_por_especificacao` e também injetado no contexto quando a pergunta é claramente dessa natureza (não depender só de o modelo decidir chamar a ferramenta).
- **Resposta vazia é útil:** quando nada casa, a busca devolve a `faixa_no_acervo` daquela propriedade, para o agente dizer "não há produto com hidroxila 180; o acervo vai de 20 a 415 mgKOH/g" e perguntar se o valor está correto.
- **Consulta por seção do boletim** (`backend/app/rag/doc_sections.py`): características e vantagens, aplicação, perfil de reatividade, segurança e armazenamento, embalagens, informações complementares e especificações. Antes, perguntar "vantagens do AG 2032" trazia o chunk da tabela ou da FISPQ — produto certo, seção errada. Agora a seção pedida vira filtro de recuperação dentro do produto citado, reordenação do contexto e instrução explícita de não trocar de seção nem completar com conhecimento geral.
- **Leitura estruturada da tabela no contexto:** a extração de PDF embaralha as colunas e o LLM troca número/unidade/propriedade; todo contexto recuperado passa a levar junto o par propriedade→valor já resolvido, sem substituir o texto bruto.
- **Bugs corrigidos com regressão:** temperatura do ensaio lida como valor da propriedade ("Viscosidade, 25°C ... 800 a 900" → viscosidade 25); expoente da unidade entrando como número ("g/cm³" → 3); tempos de reatividade no formato minuto/segundo com apóstrofo (ex: 00'09'') lidos como números soltos (afetava tempo de creme/reação/gel/pega/cura — apontado pelo usuário durante a sessão); menção a "vantagens" em frase corrida classificada como cabeçalho de seção; código de produto virando valor de especificação. Custo da varredura reduzido em 4x trocando a normalização caractere a caractere por tabela de `str.translate`.
- **Prompt:** nova Situação D (pedido por especificação numérica) e Situação A ampliada para cobrir qualquer assunto do produto, não só especificação.
- **Testes:** 82 novos, com amostras de texto real da coleção de produção. Suítes completas rodadas contra **Postgres e Qdrant reais**: **342/342 backend**, **21/21 frontend**.
- **Validação ao vivo** (10.499 trechos, `gpt-4o-mini` real): varredura em ~5 s; "hidroxila de 180" devolve o FLEXX ADR 204 (158 a 178 mgKOH/g, Boletim) como "atende dentro da tolerância", com a faixa real na tabela; "vantagens do AG 2032" e "como armazenar o CAT 136 e qual a validade" respondem pela seção certa do boletim. **A validação real achou 4 bugs que os testes sintéticos não pegavam**, todos corrigidos com regressão: faixa absurda gerando falso positivo (era o caso de 4 dos 5 resultados de "hidroxila 180"); rótulo casando em prosa e lendo o código do produto como densidade (toda a família AG); separador de milhar partindo o número ao meio; e `A ± B` com B ≥ A virando faixa que casa com quase tudo.

**✅ Sessão 34 (2026-09-02) — incidente ADT/elastômero corrigido:**
- Busca por aplicação usa palavra/flexão inteira; `correia` não casa mais com `corretamente`/`corrente` em FISPQs (341 → 22 ocorrências literais reais, todas em Boletins da família TH na prévia).
- Follow-up de correção recupera com a demanda anterior e exclui a família rejeitada da busca positiva; consultas textuais são feitas por termo/flexão antes da pontuação, evitando que o limite de 50 resultados sem ranking esconda os documentos corretos.
- Guardrail pós-resposta bloqueia família rejeitada durante a conversa inteira nos fluxos síncrono e streaming; status ativo/estoque sem ERP é neutralizado.
- Listagem explícita de elastômeros é determinística nas rotas síncrona e streaming: só aceita evidência positiva em Boletim de que o produto/sistema produz poliuretano elastomérico. Validação no catálogo real: 46 resultados, zero ADT/CAT; o LLM não participa dessa classificação nem pode reinserir auxiliares.
- Ferramentas simuladas de ERP/LIMS removidas de `MCP_TOOLS_DEFINITIONS`; templates não afirmam mais código ERP/status comercial. Integração real continua pendente na Fase 4.
- Validação local real: top-6 somente Boletins FLEXX TH que citam o sistema elastomérico e as três aplicações; `ollama/qwen2.5:3b` não recomendou ADT. Produção `gpt-4o-mini` aguarda autorização específica para envio externo.

**✅ Sessão 32 (2026-09-02) — resumo dos achados/correções, detalhe completo em `PROGRESS.md`:**
- Busca híbrida (`retrieve_products_context`): código de produto exato (índice de texto no `filename`) + palavra-chave de conteúdo (índice de texto no `content`, mínimo 2 termos) complementam a busca semântica; aviso explícito no contexto quando um código citado não bate com nada real.
- Ferramentas MCP novas com dados reais do Qdrant (não simuladas): `consultar_estatisticas_catalogo` (total do acervo) e `consultar_produtos_por_aplicacao` (lista por categoria/família/tipo, com prévia de 10 + confirmação antes de listar tudo). **`stream_pu_matcher_agent` ganhou tool calling** — gap crítico: o frontend só usa o endpoint de streaming, que nunca chamava ferramentas MCP antes desta sessão.
- Deduplicação na ingestão (`ingest_catalog_directory`): ~metade da coleção real (10.499 pontos) é redundante (pasta duplicada + PDF/DOCX do mesmo boletim) — filtros novos previnem acúmulo futuro; limpeza retroativa é decisão pendente com o usuário (operação destrutiva).
- Distinção nome-de-produto vs. aplicação/segmento: `consultar_produtos_por_aplicacao` devolve dois blocos separados (`por_nome_ou_familia`, `por_aplicacao_ou_tipo`) — prompt ensina o agente a perguntar ao vendedor quando os dois derem resultados claramente diferentes, em vez de misturar (pedido explícito do usuário).
- Feedback útil/não útil (opcional) por resposta, mantido para análise. Um feedback negativo pode originar uma correção revisável; apenas treinamento aprovado e relevante entra no contexto do agente.

**✅ AUD-003 corrigido (2026-08-26, ticket 2):** `retrieve_products_context()` agora levanta `RetrievalIndisponivelError` quando o Qdrant/embedding falha de verdade (não confundir com coleção ainda vazia, que continua retornando `[]` normalmente). `/api/match` responde 503; `/api/match/stream` emite evento `error` e para sem chamar o LLM — o agente não responde mais "de conhecimento geral" achando que é só catálogo vazio. 8 testes novos.

**✅ AUD-006 corrigido (2026-08-26, ticket 5):** `run_pu_matcher_agent()` monta a mensagem `assistant` (com todas as tool_calls) uma vez só, antes do loop — não mais uma vez por tool_call — protocolo válido com 2+ ferramentas na mesma resposta do LLM. Junto: argumentos JSON inválidos numa tool_call não derrubam mais a request (vira resposta de erro pra tool, a conversa continua); `execute_mcp_tool()` devolve JSON de verdade (`json.dumps`) em vez de repr Python (`str(dict)`). 4 testes novos.

**✅ AUD-002 corrigido como infraestrutura (2026-08-26, ticket 6) — mas ainda não protege dado real:** ingestão nova classifica cada chunk (`payload["sensivel"]`, heurística de palavra-chave deliberadamente estreita — `_e_conteudo_sensivel()`, `ingestion.py`) e `retrieve_products_context(..., incluir_sensivel=...)` filtra na busca, fail-closed por padrão. `run_pu_matcher_agent` e `stream_pu_matcher_agent` (que ganhou o parâmetro `ver_custos` pela primeira vez) repassam a permissão `VIEW_COSTS` — reaproveitada também pra "fórmulas" por decisão de engenharia registrada em `docs/spec_rbac.md` (pendência 2 segue formalmente sem decisão de negócio, mas agora tem um comportamento técnico definido em vez de nenhum). 14 testes novos. **Não resolve o problema pra dado já indexado:** a classificação só entra em chunks novos — e agora nem há chunks reais indexados (ver incidente acima), então a reingestão que vai ser necessária de qualquer forma é também a oportunidade de já nascer classificada.

- [x] Testar fluxo conversacional investigativo (perguntas antes da recomendação) — **testado 2026-08-22, resultado negativo**: pergunta vaga de propósito ("Quero um produto para assento de ônibus", o próprio exemplo citado no `AGENT_SYSTEM_PROMPT`) não gerou perguntas de qualificação — o agente foi direto para uma recomendação final, inventando especificações (densidade, dureza) que não batem com nenhuma fonte real recuperada, e citou o produto simulado do MCP (`PU-SEAT-5000 FR`) em vez de reconhecer que faltam dados. Testado com `ollama/qwen2.5:3b`; não avaliado ainda com modelo maior/de nuvem — possível causa seja o modelo pequeno não seguir instruções complexas do system prompt, não a arquitetura do agente.
- [x] Validar qualidade do retrieval — bom para perguntas próximas do código do produto (ex: "FLEXX AG 2047", "FLEXX ADT 41200", top resultado correto); fraco para perguntas ambíguas/naturais de venda (ver item de reranking abaixo)
- [x] Ajustar `AGENT_SYSTEM_PROMPT` com terminologia real da empresa — **feito 2026-08-22, escopo limitado**: adicionada menção à marca real (FLEXX®) e seção explicando os 3 tipos de documento do acervo (Boletim Técnico = fonte de especificação/aplicação; FISPQ = só segurança, texto genérico entre produtos; Certificado/Análise = laudo de lote). Validado: pergunta combinando specs+segurança retornou densidade correta (1,04 ± 0,01 g/cm³, batendo com o Boletim real) e o modelo **não alucinou** dado de segurança quando a FISPQ não foi recuperada — admitiu a lacuna em vez de inventar. **Não implementado:** a parte de "critérios reais da empresa" (como o time comercial prioriza/qualifica demanda) — exige input do time comercial/P&D que não está disponível, mesma dependência já listada na Fase 3.
- [ ] Testar comportamento "opinativo" em casos de requisitos incompatíveis
- [x] Avaliar necessidade de reranking ou filtros por metadados — **necessidade confirmada com evidência**: pergunta ambígua ("produto para colagem de espuma aglomerada") retornou só FISPQ de famílias erradas no top 6 (`top_k` real do sistema); o Boletim correto (FLEXX AG 20106, que literalmente descreve "agente de colagem... espuma aglomerada") ficou na posição 42. Causa provável: FISPQ tem texto legal/genérico repetitivo entre produtos, diluindo a busca; Boletins têm seção de aplicação específica por produto, mais discriminativa. **Recomendação:** filtrar/priorizar Boletim sobre FISPQ na busca principal (campo de tipo de documento a ser adicionado no payload da ingestão — ainda não implementado para não interromper o `--full` em andamento) — **parcialmente endereçado na Sessão 35 sem reingestão:** a busca por especificação já prioriza Boletim sobre Certificado/FISPQ como evidência (tipo derivado do nome do arquivo, ver `_tipo_documento` em `spec_search.py`), e a recuperação por seção prioriza o trecho que abre a seção pedida. O campo de tipo no payload continua pendente — ele tornaria a priorização válida também na busca semântica principal.

**Dependências:** Fase 1 concluída (dados reais indexados).

**✅ Bug das ferramentas simuladas corrigido na Sessão 34:** o agente não recebe mais `consultar_catalogo_erp` nem `consultar_normas_homologadas` em `MCP_TOOLS_DEFINITIONS`; assim, os dados fake (`PU-SEAT-5000 FR`, status/estoque/laudos simulados) não podem ser usados em resposta. As funções permanecem apenas como scaffolding interno até a Fase 4 integrar fontes reais.

**Bloqueio de máquina lenta (Sessão 9) — parcialmente resolvido:** prompt trivial sem contexto caiu de 278s para 18.9s nesta sessão (causa da lentidão nunca identificada, aparentemente transitória). Porém pergunta real com RAG+tools ainda levou 103.8s — por isso o timeout do frontend (`frontend/app.py`) foi ampliado de 120s/90s para **240s** em ambas as rotas (stream e síncrona) nesta sessão, pra viabilizar o teste.

---

## Fase 3 — Templates de Resposta
**Status:** ⬜ Não iniciado

- [ ] Validar os 3 templates padrão (`proposta_tecnica_completa`, `comercial_rapido`, `parecer_interno_engenharia`) com o time comercial
- [ ] Ajustar campos obrigatórios conforme identidade visual/técnica da empresa
- [ ] Definir se templates ficam hardcoded (`templates.py`) ou parametrizáveis via UI/BD (Módulo 3 da proposta)

**Dependências:** feedback do time comercial/P&D sobre formato ideal.

---

## Fase 4 — Integrações MCP / ERP Reais
**Status:** ⬜ Não iniciado (`pu_mcp_server.py` ainda contém scaffolding simulado, mas ele não é exposto ao LLM desde a Sessão 34)

- [ ] Mapear endpoints reais do ERP (SAP/TOTVS/outro) para consulta de catálogo e estoque
- [ ] Mapear fonte real de laudos de homologação (LIMS ou repositório de qualidade)
- [ ] Substituir `consultar_catalogo_erp` e `consultar_normas_homologadas` por chamadas reais
- [ ] Definir autenticação/segurança da integração (rede interna, VPN, credenciais de serviço)

**Dependências:** acesso e credenciais aos sistemas ERP/LIMS da empresa; definição de responsável de TI para a integração.

---

## Fase 5 — RBAC & Governança
**Status:** ✅ Concluída (implementação) — auditoria de 2026-08-25 encontrou 5 bugs reais de governança nesta fase; **AUD-004, AUD-005 e AUD-010/AUD-011 corrigidos em 2026-08-26** (tickets 3, 4 e 10). Segue aberto: **AUD-009** (downgrade do Alembic deixa enums órfãos — ticket 11, sem bloqueio). 4 pendências funcionais seguem em aberto em `docs/spec_rbac.md` (custos/fórmulas/RAG sensível/CLI de bootstrap), não escondidas.

**✅ AUD-004 corrigido (2026-08-26, ticket 3):** `user_service.py` (`update_user`/`deactivate_user`) agora recusa qualquer mudança que zeraria os Admin TI ativos — `UltimoAdminError` → 409. Cobre o caso mais amplo do que o registrado originalmente (rebaixar *outro* Admin TI, não só a si mesmo), porque a checagem vive no service layer. Achado novo corrigido junto: canal lateral de tempo em `authenticate()` (bcrypt só rodava quando o username existia) — agora roda sempre, contra um hash dummy quando não existe. 10 testes novos.

**✅ AUD-005 corrigido (2026-08-26, ticket 4):** `MatchRequest.model_name` validado contra uma allowlist (`ALLOWED_CHAT_MODELS`, `app/config.py`); `.history` usa um schema estrito (`role` só `user`/`assistant`, `content` sempre string com limite de tamanho, sem campo extra) — cliente não injeta mais papel `system` arbitrário no histórico enviado ao LLM. 11 testes novos.

**✅ AUD-010 e AUD-011 corrigidos (2026-08-26, ticket 10):** rate limiting em `POST /api/auth/login` (`backend/app/auth/rate_limit.py`, novo — 5 tentativas falhas/60s por username, conta pra username inexistente também para não virar mais um canal de enumeração; **débito documentado**: em memória, não sobrevive a mais de 1 réplica do backend). Exceções internas redigidas nos 3 pontos que a auditoria achou (`/api/health`, `/api/match`, evento `error` do streaming) — mensagem genérica pro cliente, texto completo só no log do servidor. 7 testes novos.

**✅ Sessão 35b (2026-09-09) — tela de cadastro de usuários e perfis:** o backend estava pronto desde a tarefa 7, mas **sem interface** — provisionar exigia CLI ou `curl`, e por isso o sistema rodou até aqui com **um único usuário cadastrado** (`lucas.braun`, 24/08). Agora existe a página "Usuários e perfis" na área principal do Streamlit, visível só para Admin TI: lista com o estado de cada conta, cadastro, edição de nome/e-mail/perfil, redefinição de senha e desativar/reativar, com o motivo real do erro do backend visível na tela (`detail`), não um "erro 409" seco. **Gap real do backend fechado junto:** `POST /api/auth/users/{id}/activate` (novo) — havia desativação e não havia volta, o que só era tolerável enquanto desativar morava no CLI; com um botão na tela, desativar por engano deixou de ser hipotético. 13 testes novos (10 de tela via `AppTest`, 3 de rota). **Não validado:** a tela em navegador real — cobertura por `AppTest`, mesma limitação já registrada para o PWA.

**✅ Sessões 38–39 — treinamento do agente e requisitos compostos concluídos:** três modalidades — correção, conhecimento e exemplo — em coleção Qdrant separada (`pu_treinamento`). A interface permite cadastrar, listar e aprovar itens, e um feedback negativo abre o formulário para escrever a resposta correta. Correções usam escopo e similaridade; feedback bruto deixou de ser instrução global. Buscas com vários limites calculam a interseção, exigem a faixa inteira dentro de cada limite e geram resposta estruturada sem LLM. Ver `docs/spec_treinamento.md` e `PROGRESS.md`.

**✅ Sessão 38 (2026-09-10) — fila de aprovação de documentos:** perfis autorizados enviam arquivos (`UPLOAD_DOCUMENTS`) que entram numa fila; só quem tem `APPROVE_UPLOADS` decide o que vai para o Qdrant — enviar e aprovar são permissões separadas de propósito. Aprovar INDEXA primeiro e marca depois, para nunca existir documento "aprovado" fora do índice. A procedência vai no payload, o que permite REMOVER depois exatamente aqueles pontos. Imagem é recusada na entrada com explicação (sem OCR, seria arquivo inútil no acervo) — **OCR fica como pendência**. A tela tem duas abas cuja ordem depende de quem olha. Corrigido junto um bug da Sessão 37: a interface decidia quem é admin pelo slug do perfil, o que quebrou quando perfis viraram dinâmicos — agora é por permissão. 418/419 backend, 55/55 frontend. Ver `PROGRESS.md`, Sessão 38.

**✅ Sessão 37 (2026-09-10) — perfis dinâmicos:** perfis deixaram de ser enum fixo e viraram tabela editável (`perfis` + `perfil_permissoes`) — criar um perfil não exige mais migration nem deploy. A ATRIBUIÇÃO permissão↔perfil virou dado; a LISTA de permissões continua em código, porque cada permissão só existe se algum endpoint a verifica. "Perfil admin" é a permissão `MANAGE_USERS` marcável na tela. A invariante de "último Admin TI" foi generalizada para quatro caminhos de auto-bloqueio, contando gente ATIVA que administra. Migration `b7e1c4d92f30` com backup prévio e ciclo downgrade→upgrade exercitado contra o banco real. A tela virou duas abas (Usuários / Perfis e permissões), com o dicionário fixo do frontend substituído por busca na API — a mesma duplicação que causou o bug do 422 na Sessão 35d. 399/400 backend, 44/44 frontend. Ver `PROGRESS.md`, Sessão 37.

**✅ Sessão 36 (2026-09-10) — vínculo com o Active Directory implementado:** o "modelo híbrido" previsto no desenho da Fase 5 saiu do papel, **sem nenhuma migration** — `origem`, `external_id` único e `password_hash` nullable já existiam para isto. Um usuário pode ser vinculado a uma conta do AD pela tela de administração e passa a entrar com a senha da rede; a senha local é apagada no vínculo, para não existirem duas credenciais. O vínculo é pelo `objectGUID` (imutável), com o login resolvido a partir dele a cada autenticação — renomear alguém no AD não quebra o login. O AD **não** decide perfil nem permissão. 24 testes novos, sendo 11 contra o diretório REAL. Ver `PROGRESS.md`, Sessão 36.

**Decisão de provisionamento (2026-08-24):** manual agora, desenho pronto para AD/LDAP depois. Ver `docs/spec_rbac.md` para a comparação completa das 3 estratégias e a justificativa.

**Especificação completa:** `docs/spec_rbac.md` — modelo de usuário, perfis, estratégia de autorização centralizada, campos sensíveis, matriz de acesso (fonte: `docs/proposta_do_projeto_similaridade.md`, seção 5) e pendências funcionais.

**Plano incremental (9 tarefas):**
1. [x] Schema base: model `User` (SQLAlchemy) + enums `Role`/`UserStatus`/`UserOrigin` + serviço `postgres` no `docker-compose.yml` + migration inicial (Alembic) — **concluído 2026-08-24**, validado com 6 testes automatizados (primeira suíte de testes do projeto) rodando contra Postgres real
2. [x] Repository/service de usuários (CRUD, hash de senha) — **concluído 2026-08-24**: `backend/app/auth/security.py` (hash bcrypt, mín. 8 caracteres) + `backend/app/auth/user_service.py` (create/get/list/update/set_password/deactivate — "excluir" implementado como desativação, não apaga a linha, por auditoria). 15 novos testes (21/21 no total) contra Postgres real.
3. [x] Autenticação (login manual → token de sessão) — **concluído 2026-08-24**: `backend/app/auth/token.py` (JWT HS256 assinado com `SECRET_KEY`, algoritmo fixo — não confia em `alg` do token), `authenticate()` em `user_service.py`, `POST /api/auth/login` + `GET /api/auth/me` + dependency `get_current_user` (`backend/app/auth/router.py`). Token de usuário desativado depois do login para de funcionar no próximo request (checa status no banco, não só o token). 15 novos testes (36/36 no total), incluindo teste end-to-end ao vivo contra o servidor real. **Ainda não aplicada** a nenhum endpoint de negócio existente (`/api/match` etc.) — isso é a tarefa 5.
4. [x] Camada centralizada de autorização (`Permission`, `ROLE_PERMISSIONS`, `require_permission`) — **concluído 2026-08-24**: `backend/app/auth/permissions.py` — matriz `ROLE_PERMISSIONS` transcrita célula por célula de `docs/spec_rbac.md` (confirmado sem desvio pelo code review), deny-by-default nas 3 pendências (custos pro Técnico, excluir template pra Gestor/Químico-PD, gerenciar usuários pra Gestor). `get_current_user` extraído de `router.py` para `dependencies.py` (ajuste de direção de dependência — `permissions.py` não deveria depender do módulo de rotas HTTP). 9 novos testes (45/45 no total). **Ainda não aplicada a nenhum endpoint** — isso é a tarefa 5.
5. [x] Proteção dos endpoints existentes (`main.py`) — **concluído 2026-08-24**: `/api/match`, `/api/match/stream` e `/api/templates` exigem `Permission.VIEW_CATALOG`/`SELECT_TEMPLATE` (todos os 5 perfis têm, então na prática exige só estar autenticado); `/api/ingest` exige `Permission.MANAGE_INGESTION` (nova, só Admin TI — não estava na matriz original, adicionada porque a spec não cobria ingestão e o endpoint dispara reindexação de horas). `/` e `/api/health` deliberadamente continuam públicos (liveness/monitoramento, sem dado de negócio). 11 novos testes (56/56 no total), com a lógica de negócio (RAG/ingestão) mockada de propósito — testa só a porta de autorização.
   **🚨 Consequência real e imediata (resolvida abaixo):** o frontend Streamlit (`frontend/app.py`) **nunca enviava token de autenticação** — a partir desta tarefa, toda pergunta pela tela passou a falhar com 401. Achado pelo code review (eixo Spec), não estava em nenhuma tarefa numerada do plano original.

**Frontend: tela de login (fora da numeração das 9 tarefas, priorizada pelo usuário) — concluído 2026-08-24:** `frontend/app.py` ganhou portão de login (`st.form` + `POST /api/auth/login` + `GET /api/auth/me`, token guardado em `st.session_state`, nunca em cookie/localStorage/URL), header `Authorization: Bearer <token>` em toda chamada de negócio (`_auth_headers()`), exibição do usuário logado + botão de logout na sidebar, e tratamento de expiração de sessão (401 em `/api/match` ou `/api/match/stream` força logout completo — token, usuário e histórico de chat limpos via `_fazer_logout()` — e volta pro portão de login). Code review (eixo Standards) encontrou 3 problemas reais, todos corrigidos: (1) os dois pontos de tratamento de 401 duplicavam a limpeza de sessão em vez de chamar `_fazer_logout()`, e não limpavam `messages` — corrigido centralizando em `_fazer_logout()`; (2) construção do header `Bearer` duplicada — extraído helper `_bearer()`; (3) no caminho de streaming, um 401 deixava uma mensagem do assistente com conteúdo vazio presa no histórico — corrigido com uma flag `expired` verificada só depois do stream terminar, que aciona `_fazer_logout()` + `st.rerun()` em vez do fluxo normal de anexar histórico. Validado via simulação HTTP direta (login → `/me` → chamada de negócio com token válido, e com token inválido/ausente em `/api/match` e `/api/match/stream`, todos os casos retornando o status esperado) e inspeção de log do container — **não** validado visualmente em navegador (`chromium-cli` indisponível neste ambiente).
6. [x] Restrição de campos sensíveis — **concluído 2026-08-24 (camada estruturada):** `consultar_catalogo_erp` (campo de exemplo `custo_industrial_kg`) e `consultar_normas_homologadas` (`laudo_numero`/`laboratorio_emissor`) só retornam o dado sensível se o chamador passar `ver_custos`/`ver_laudo_completo=True` (default `False`, fail-closed); a permissão (`Permission.VIEW_COSTS`/`VIEW_HOMOLOGATION_FULL`) é decidida só em `/api/match` e desce como booleano puro até o servidor MCP — nunca por instrução de prompt. 11 novos testes (67/67 no total). **Pendência que continua real e não resolvida:** conteúdo RAG não estruturado (os 11.273 trechos já indexados) não tem metadado de "isto é custo/fórmula" — filtrar por perfil antes de montar o contexto do LLM exigiria re-ingestão, fora do escopo desta tarefa. `/api/match/stream` não recebeu o mesmo tratamento porque hoje não invoca nenhuma ferramenta MCP.
7. [x] Administração/provisionamento — **concluído 2026-08-24:** `backend/app/auth/admin_router.py` (novo) expõe via HTTP, atrás de `Permission.MANAGE_USERS` (só Admin TI), o que `user_service.py` já tinha desde a tarefa 2: `POST /api/auth/users` (criar), `GET /api/auth/users` (listar), `GET /api/auth/users/{id}` (obter), `PATCH /api/auth/users/{id}` (editar nome/email/perfil), `POST /api/auth/users/{id}/password` (redefinir senha) e `POST /api/auth/users/{id}/deactivate` ("excluir" continua sendo desativar, nunca apagar a linha). Guarda extra (decisão de engenharia, não requisito de negócio): ninguém pode desativar a própria conta — hoje só existe um Admin TI real, e sem essa guarda um clique errado travaria toda a administração. 14 novos testes (81/81 no total). Até esta tarefa, provisionar usuário só era possível via script direto no banco.
8. [x] Testes adicionais — **concluído 2026-08-24:** revisão de gaps reais na suíte já acumulada (81 testes das tarefas 1-7) em vez de padding redundante. 6 testes novos fechando lacunas genuínas: token expirado rejeitado na cadeia HTTP real (não só no `decode_access_token()` unitário), email duplicado na edição (só existia teste na criação), senha fraca na redefinição (só existia teste na criação), e perfil inválido rejeitado com 422. 87/87 no total. Cobertura de autorização (matriz completa, 5 perfis) e campos sensíveis já estava adequada das tarefas 4 e 6 — não repetido.
9. [x] Documentação final da fase — **concluído 2026-08-24:** `README.md` ganhou seção "Autenticação & Perfis (RBAC)" (fluxo de login, bootstrap do primeiro Admin TI — testado ao vivo contra o backend real antes de documentar, não só escrito de memória — e link pra `docs/spec_rbac.md`), tabela de endpoints atualizada com a permissão exigida por rota, árvore de estrutura do projeto atualizada (`backend/app/auth/`, `backend/alembic/`, etc.) e a seção "Status" (que ainda dizia "Fase 0 concluída, aguardando chaves de API" — desatualizada desde a Fase 1) corrigida para refletir o estado real. `docs/spec_rbac.md` teve a seção "Pendências" atualizada: item 3 (Gestor Comercial gerenciar usuários) marcado como implementado-negado pela tarefa 7 (não como confirmado pelo negócio — só como "tem código real por trás agora"); item 4 (RAG não estruturado) reconfirmado como não resolvido; novo item 5 registrando a ausência de um comando de bootstrap dedicado (hoje só script manual, documentado no README).

**⚠️ Débito de segurança identificado no code review da tarefa 3 (não corrigido — escopo maior que um ajuste pontual):** `POST /api/auth/login` não tem rate limiting/bloqueio por tentativas. Hoje é possível tentar senha ilimitadamente contra um username. Corrigir exigiria uma lib de rate limit (ex: `slowapi`) ou contador em Redis/memória — decisão de infraestrutura própria, não implementada nesta tarefa. Considerar antes de expor o login fora da rede interna (ver Fase 8).

**Dependências:** ~~definição de como os usuários serão provisionados~~ — **resolvida** (manual agora, híbrido-pronto).

---

## Fase 6 — Frontend / UX de Campo
**Status:** 🟨 Em andamento (identidade visual + PWA na Sessão 27; histórico persistente na Sessão 33; tela de usuários e **marca oficial** na Sessão 35; upload pelo campo do chat na Sessão 41; sem validação em navegador real)

- [x] Aplicar identidade visual da marca (paleta + tipografia) — **feito 2026-08-26**: `.streamlit/config.toml` (tema nativo: botões, inputs) + CSS injetado em `frontend/app.py` (Roboto, cards), a partir de `IDENTIDADE_VISUAL.md` (documento trazido de outro projeto — Next.js/Tailwind — e adaptado nesta sessão pro mecanismo do Streamlit). Ícone da sidebar trocado do placeholder genérico (flaticon externo) por um monograma "PU" na paleta da marca — **ainda não é o logo real**, nenhum arquivo foi fornecido; ver tabela de troca em `IDENTIDADE_VISUAL.md`.
- [x] Instalação como app (PWA) — **feito 2026-08-26, card simplificado em 2026-09-11**: card compacto na tela de login (`frontend/app.py`, componente HTML com `manifest.json` + Service Worker + evento `beforeinstallprompt`). O card só aparece quando a instalação está realmente disponível; caso contrário, fica recolhido. Exigiu o proxy Caddy porque o Service Worker precisa ser servido em `/` para controlar a página toda. Assets e fluxo estrutural cobertos por testes; o prompt nativo ainda depende de validação manual em Chrome/Edge.
- [x] Histórico persistente de conversas por usuário — **feito 2026-09-02 (Sessão 33)**: tabelas `conversations`/`conversation_messages` no PostgreSQL, CRUD autenticado com isolamento por proprietário, persistência nas rotas síncrona e streaming, sidebar para nova conversa/retomada/exclusão e recarga de fontes/modelo. Título derivado da primeira pergunta; últimas 8 mensagens entram no LLM. Validado com 241 testes backend, 11 frontend e três chamadas reais a `gpt-4o-mini` (incluindo continuidade após recarga e streaming).
- [x] **Marca oficial aplicada (Sessão 35d, 2026-09-09)** — logo e símbolo do Grupo Flexível substituíram o monograma placeholder. Originais em `frontend/static/brand/`, derivados de web gerados por `frontend/static/gerar_assets_marca.py` (ícones 192/512 + maskable pro PWA, favicon da aba, logo horizontal no login). 6 testes novos travam a forma
- [x] **Tela de administração de usuários (Sessão 35b, 2026-09-09)** — cadastro, edição de perfil, redefinição de senha e ativar/desativar, visível só para Admin TI. Até aqui, provisionar usuário exigia CLI.
- [x] **Upload pelo chat (Sessão 41, 2026-09-11)** — o campo de mensagem aceita vários PDF/DOC/DOCX/TXT somente quando o perfil tem `UPLOAD_DOCUMENTS`. O texto enviado junto vira observação, e os arquivos seguem para a fila existente; não entram no contexto do agente antes de `APPROVE_UPLOADS`. A rota repete o gate de permissão. Streamlit mínimo elevado para 1.43, versão que introduziu anexos em `st.chat_input`.
- [ ] Testar usabilidade em tablet/mobile na intranet/VPN
- [ ] Substituir o ícone placeholder pelo logo real do Grupo Flexível assim que o arquivo for fornecido
- [ ] Validar em navegador real (Chrome/Edge) que o card de PWA de fato dispara o prompt de instalação
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
**Status:** ⬜ Não iniciado (item de backup adiantado fora de ordem — ver nota abaixo)

- [ ] Definir servidor de produção (specs mínimas: 8 cores, 16–32GB RAM, SSD 200GB+)
- [x] Configurar backup do Qdrant e PostgreSQL — **feito 2026-08-31**: `backup.py` (raiz do repo, roda do host) cria snapshot da coleção via API do Qdrant e baixa pro host (`data/backups/qdrant/`), e `pg_dump` via `docker exec` no container Postgres (`data/backups/postgres/`); retenção mantém os 14 backups mais recentes de cada tipo por padrão (`--manter N` ajusta). Validado ao vivo contra os containers reais (não só mockado): snapshot de ~156MB e dump de ~3.7KB gerados com sucesso, `pg_dump` confirmado como SQL válido. Instruções de restauração no docstring do script. **Não é agendamento automático** — continua manual (rodar `python backup.py` periodicamente) ou exige configurar uma tarefa agendada do Windows (`schtasks`) por fora do repositório; decisão de quando/com que frequência automatizar fica para quando houver servidor de produção definido (item acima).
- [ ] Definir monitoramento (logs, uptime, custo de uso das APIs de LLM)
- [ ] Rollout gradual por perfil de usuário

**Dependências:** aprovação do piloto (Fase 7); infraestrutura de servidor on-premise disponível.

**Nota:** o item de backup foi adiantado fora da ordem normal das fases (que dependeria da aprovação do piloto) porque a ausência de backup já causou perda de dados real — ver `docs/incidente_2026-08-26_reingestao_apagou_colecao.md`. Os demais itens da fase continuam bloqueados pelas dependências normais.

---

## Como este cronograma é atualizado
A cada sessão de trabalho, o status das fases/itens acima é revisado e o arquivo [PROGRESS.md](PROGRESS.md) recebe uma nova entrada de log. Marcos concluídos são movidos de ⬜/🟨 para ✅.
