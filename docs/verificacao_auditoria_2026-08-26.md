# Verificação de acompanhamento — 2026-08-26

Continuação de `docs/auditoria_2026-08-25.md`. Objetivo: (1) confirmar o que mudou desde ontem, (2) verificar se os 12 bugs então encontrados (AUD-001 a AUD-012) continuam presentes no HEAD atual, lendo o código real (não os números de linha antigos), e (3) caçar bugs novos que a auditoria de ontem não cobriu, lendo os arquivos por completo.

**Baseline:** `HEAD` em `9957071` (auditoria original) → `HEAD` atual `38545c9` (1 commit novo: `fix(startup): apply database migrations before boot`).

**Método:** 3 agentes em paralelo, cada um lendo por completo sua área e citando código atual:
- RAG/ingestão/MCP/templates (`backend/app/rag/*`, `backend/app/mcp/*`, `backend/app/templates.py`)
- Auth/RBAC/banco (`backend/app/auth/*`, `backend/app/db.py`, `backend/app/models.py`, `backend/alembic/*`, `docs/spec_rbac.md`)
- Frontend/infra/startup (`frontend/*`, `backend/app/cli.py`, `backend/app/config.py`, `backend/app/startup.py`, Dockerfiles, `docker-compose.yml`, `README.md`)

## AUD-001 — CRITICA — banco novo não recebia migration automaticamente

**Status: CORRIGIDO, com ressalvas.**

`backend/app/startup.py` roda `alembic -c /app/backend/alembic.ini upgrade head` (`subprocess.run(..., check=True)`) e só então substitui o processo pelo comando real via `os.execvp` — falha de migration aborta o boot corretamente (não engole exceção). Ligado como `ENTRYPOINT` em `Dockerfile.backend`. Testado em `backend/tests/test_startup.py` (feliz-caminho, com injeção de dependência).

Verificado nos ambientes reais de uso:
- `docker-compose up` (caminho documentado): sólido — `docker-compose.yml` já tinha `depends_on.postgres.condition: service_healthy`, então o Postgres aceita conexões antes do `startup.py` mesmo rodar.
- `docker exec` (comandos de ingestão/bootstrap do README): não reinvoca o ENTRYPOINT, sem duplicidade — ok.

**Ressalvas que continuam abertas:**
- **Dev local sem Docker não está coberto.** `backend/run_local.py` chama `uvicorn.run(...)` direto, nunca roda Alembic. A seção "Como rodar localmente (sem Docker)" do `README.md` também não menciona `alembic upgrade head`. Num Postgres local vazio, a primeira request que toca o banco falha.
- Sem timeout no `subprocess.run` do `startup.py` — se o Postgres estiver alcançável mas travado (packet-drop, não connection-refused), o container trava indefinidamente em vez de falhar rápido. Não é bug ativo hoje (só afeta cenários fora do `docker-compose up` documentado), mas vale registrar.
- Não há teste cobrindo o caminho de falha (migration falhando impede o `replace_process`) — garantido por construção do código, mas não coberto por teste.

## AUD-002 a AUD-012 — todos **CONFIRMADOS AINDA PRESENTES**

Nenhum destes foi tocado desde a auditoria de ontem. Evidência atual (HEAD `38545c9`):

| ID | Severidade | Resumo | Evidência atual |
|---|---|---|---|
| AUD-002 | ALTA | Campos sensíveis (custo/fórmula) no RAG ignoram o perfil do usuário | `backend/app/rag/engine.py:85-97` e `:164-176`; payload da ingestão (`ingestion.py:114-121`) não carrega classificação de sensibilidade; `backend/tests/test_sensitive_fields.py:9-12` documenta isso como fora de escopo |
| AUD-003 | ALTA | Falha de retrieval tratada como catálogo vazio | `backend/app/rag/engine.py:46-69` — `except Exception: return []` ainda cobre client, embedding e busca |
| AUD-004 | ALTA | Admin TI pode causar lockout por auto-rebaixamento (**mais amplo do que o registrado ontem** — ver "Novos achados") | `admin_router.py:109-113` não injeta `current_user`; `user_service.py:79-93` (`update_user`) não tem nenhuma checagem de invariante para `perfil` |
| AUD-005 | ALTA | `model_name`/`history` do `/api/match` sem allowlist/validação | `backend/app/main.py:29-33`; `engine.py:106-108`/`185-187` faz `messages.extend(history[-8:])` sem checar `role` |
| AUD-006 | MEDIA | Tool calling múltiplo monta sequência inválida | `engine.py:129-142` — `messages.append(choice.message)` ainda dentro do `for` |
| AUD-007 | MEDIA | Reingestão não remove chunks obsoletos | `ingestion.py:72-141` só faz `upsert`, sem delete/reconciliação |
| AUD-008 | MEDIA | Dimensão/modelo da coleção não validados | `ingestion.py:16-33` (`init_qdrant_collection`) não confere dimensão contra coleção já existente |
| AUD-009 | MEDIA | Downgrade Alembic deixa enums do Postgres | `alembic/versions/a089248d3b0d_create_users_table.py:44-50` — `downgrade()` não dropa `user_status`/`user_role`/`user_origin` |
| AUD-010 | MEDIA | Login sem rate limiting | `backend/app/auth/router.py:36-49`; nada em `requirements.txt`/compose/middleware limita tentativas |
| AUD-011 | MEDIA | Erros internos expostos ao cliente | `engine.py:212-214` (stream), `main.py:118-120` (`/api/match`), `main.py:84-86` (`/api/health` público, sem auth) |
| AUD-012 | BAIXA | Frontend perde estado correto em erro de stream (**mais amplo do que o registrado ontem** — ver "Novos achados") | `frontend/app.py:240-241` não anexa erro a `_stream_state['answer']`; `engine.py:214-216` sempre emite `done` no `finally` |

## Novos achados (não estavam na auditoria de 2026-08-25)

### RAG / ingestão / MCP

- **NOVO-RAG-1 (MEDIA) — chunks parciais de um arquivo com falha ficam gravados no Qdrant mesmo assim.** `ingestion.py:91-132`: se `get_embedding` lança exceção no meio de um arquivo (ex. timeout transitório do Ollama/Gemini num chunk), os chunks já processados antes do erro já foram colocados na lista `points` compartilhada e são gravados no próximo flush de 100 pontos ou no upsert final — mas o arquivo inteiro é contado em `skipped_files`, então o relatório final diz "não indexado" enquanto parte do conteúdo está, na verdade, pesquisável. Agrava o AUD-007 (sem reconciliação, esse resíduo nunca é limpo nem mesmo numa reingestão completa).
- **NOVO-RAG-2 (MEDIA) — argumentos de tool call malformados derrubam a request.** `engine.py:132`: `json.loads(tool_call.function.arguments)` sem try/except. JSON inválido vindo do LLM (cenário real, especialmente com modelos locais pequenos — a própria auditoria original já registrou "modelo pequeno apresentou comportamento negativo") propaga `JSONDecodeError` até o handler genérico do `main.py`, virando um 500 com mensagem de parser bruta (interage com AUD-011) em vez do agente degradar para resposta só-texto.
- **NOVO-RAG-3 (BAIXA) — ordem de `sources` não é determinística.** `engine.py:148` e `:197`: `list(set([...]))` não garante ordem estável entre execuções; a lista de fontes exibida ao usuário pode mudar de ordem pra mesma pergunta idêntica. Troca simples por `dict.fromkeys(...)` ou `sorted(set(...))` resolve.
- **NOVO-RAG-4 (BAIXA) — resultado do MCP vai pro LLM como `str(dict)` Python, não JSON.** `pu_mcp_server.py:93,95` + `engine.py:141`: aspas simples, `True`/`False`/`None` em vez de `true`/`false`/`null` — fora do contrato usual de mensagem `role: tool`. Pode ser outro contribuinte pro comportamento ruim já observado com modelo pequeno na Fase 2.
- **NOVO-RAG-5 (BAIXA) — criação da coleção Qdrant não é segura contra concorrência.** `ingestion.py:16-33` faz check-then-act sem lock; duas chamadas simultâneas de `/api/ingest` numa coleção nova podem colidir em `create_collection`, uma delas falhando silenciosamente dentro da `BackgroundTask` (sem propagar erro pro chamador, que já recebeu 200).

**Cobertura de teste desta área:** zero testes unitários diretos para `engine.py`, `ingestion.py` ou `embeddings.py` — só `test_sensitive_fields.py` (camada MCP) e `test_startup.py` existem em `backend/tests/` fora do RBAC.

### Auth / RBAC / banco

- **NOVO-AUTH-1 (ALTA) — canal lateral de tempo derruba a própria proteção contra enumeração de usuário que o código tenta implementar.** `user_service.py:111-117` (`authenticate`): `user is None or ... or not verify_password(...)` — o `or` do Python faz curto-circuito, então quando o username não existe, o bcrypt (~100ms) nunca roda; quando existe mas a senha está errada, roda. A mensagem de erro é uniforme, mas o tempo de resposta não é — um oráculo confiável de enumeração, exatamente o que o comentário em `router.py:38-43` diz querer evitar. Correção: sempre chamar `verify_password` contra um hash fixo/dummy quando `user is None`.
- **NOVO-AUTH-2 (reforça AUD-004) — a invariante "pelo menos 1 Admin TI ativo" não é verificada em lugar nenhum**, nem para auto-edição nem para editar outro usuário. Um Admin TI pode rebaixar outro Admin TI (o único outro admin) sem bloqueio nenhum — não precisa ser literalmente a própria conta pra chegar em zero admins. A correção precisa ficar em `user_service.py` (contagem de admins ativos antes de qualquer mudança de `perfil`/`status` que reduza esse número), não numa comparação de ID no router.
- **NOVO-AUTH-3 (BAIXA) — `email` em `CriarUsuarioRequest`/`EditarUsuarioRequest` é `str` puro, sem `EmailStr`.** `admin_router.py:62-77` aceita qualquer string única como email, inclusive `"not-an-email"`.
- **NOVO-AUTH-4 (BAIXA) — `update_user()` permite sobrescrever `nome`/`email` com string vazia.** `user_service.py:86-89` checa `is not None`, não truthiness — `PATCH {"nome": ""}` apaga o nome silenciosamente.

### Frontend / infra

- **NOVO-FE-1 (reforça AUD-012) — a perda de estado no erro de stream não é só o evento `error` do backend.** Em `frontend/app.py`, os handlers de `ConnectionError` (linhas 244-248), `Timeout` (249-250) e `Exception` genérica (251-252) do próprio generator também escrevem texto pra tela sem nunca mesclar em `_stream_state['answer']` — mesmo sintoma (histórico salvo fica vazio/parcial), mais pontos de origem do que o único caso descrito ontem.
- **NOVO-FE-2 (confirma divergência já registrada) — o exemplo de `curl` do `/api/ingest` no README ainda não tem `Authorization`,** apesar da rota exigir `Permission.MANAGE_INGESTION` desde a Fase 5. Ainda não corrigido.
- Nenhum bug novo de correção encontrado em `backend/app/cli.py`, `backend/app/config.py`, `frontend/run_local.py`, `Dockerfile.frontend`, `requirements.txt`, ou nas partes não-auth de `main.py` (`/`, `/api/health`, `/api/ingest`, wiring do `/api/match/stream`).

## O que isso muda no plano

Nenhum item do `docs/plano_correcao_auditoria_2026-08-25.md` fica bloqueado por esta verificação — ela apenas confirma que os tickets 2 a 12 continuam válidos como estavam, marca o ticket 1 como entregue, e adiciona os achados novos como itens dentro dos tickets que já cobrem a mesma área (ver arquivo atualizado). Nenhuma correção de código foi feita nesta verificação — só leitura e confirmação, como na auditoria original.
