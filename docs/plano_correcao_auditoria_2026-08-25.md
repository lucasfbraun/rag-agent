# Plano de correcao da auditoria - rascunho de tickets

Este e um rascunho local, nao publicado em issue tracker. O projeto nao possui `docs/agents/issue-tracker.md` nem tracker configurado para as Skills `to-spec`/`to-tickets`.

**Atualizado em 2026-08-26** apos verificacao de acompanhamento (`docs/verificacao_auditoria_2026-08-26.md`): ticket 1 entregue; tickets 2-12 confirmados ainda validos e sem alteracao; achados novos foram encaixados dentro do ticket da mesma area (marcados `[NOVO]`).

**Atualizado de novo em 2026-08-26 (sessao 28, TDD)**: tickets 2, 3 e 4 entregues — os tres sem bloqueio. Isso libera os tickets 5, 6, 7, 10 e 11, que dependiam so deles.

**Atualizado de novo em 2026-08-26 (sessao 29, TDD, continuacao)**: tickets 5 e 10 tambem entregues, ambos sem bloqueio. Tickets 6 e 7 ficaram pra depois de propósito — envolvem o acervo real (11.273 pontos ja indexados no Qdrant), decisao que precisa ser confirmada com o usuario antes de implementar (ver nota no fim do arquivo).

**Atualizado de novo em 2026-08-26 (sessao 30, TDD, continuacao)**: usuario pediu pra continuar o desenvolvimento sem responder as 2 perguntas especificas do ticket 6 (heuristica de classificacao + janela de reingestao). Decisao tomada: implementar o CODIGO dos tickets 6 e 7 (testavel e testado sem tocar a colecao real), mas NAO disparar uma reingestao completa do acervo de rede (~8.377 arquivos, operacao longa e cara) sem confirmacao mais explicita. Ticket 7 entregue por completo — mas causou um incidente real no processo (apagou os 11.273 pontos reais durante uma verificacao; ver ticket 7 abaixo e `docs/incidente_2026-08-26_reingestao_apagou_colecao.md`), corrigido e travado por teste de regressao na mesma sessao. Ticket 6 entregue como infraestrutura + heuristica conservadora, com o gap do acervo real (agora vazio) registrado explicitamente. Ticket 8 liberado (dependia so do 7).

## Ordem proposta

1. **Bootstrap confiavel do banco** — ✅ **CONCLUIDO em 2026-08-25** (`fix(startup): apply database migrations before boot`, commit `38545c9`)
   - Entrega: `backend/app/startup.py` roda `alembic upgrade head` (via `ENTRYPOINT` do `Dockerfile.backend`) antes de substituir o processo pelo comando da aplicacao; migration falha aborta o boot; testado em `backend/tests/test_startup.py`.
   - **Pendencia remanescente (nao bloqueante, registrar como debito):** dev local sem Docker (`backend/run_local.py`) nao roda migration nenhuma, e o README nao documenta isso na secao "sem Docker". Sem timeout no `subprocess.run` da migration.

2. **Distinguir catalogo vazio de retrieval indisponivel** — ✅ **CONCLUIDO em 2026-08-26**
   - Entrega: `RetrievalIndisponivelError` (nova, `backend/app/rag/engine.py`) — falha real de conexao/embedding/busca levanta a excecao em vez de `return []`; coleção genuinamente vazia (ainda nao ingerida) continua retornando `[]` normalmente, sao dois estados distintos agora. `/api/match` traduz pra 503 com mensagem generica; `stream_pu_matcher_agent` emite evento `error` + `done` e aborta sem chamar o LLM. 8 testes novos (`backend/tests/test_rag_retrieval.py`, `test_retrieval_failure_handling.py`).

3. **Preservar pelo menos um Admin TI ativo** — ✅ **CONCLUIDO em 2026-08-26**
   - Entrega: `UltimoAdminError` (nova, `backend/app/auth/user_service.py`) — `update_user()` (mudanca de `perfil`) e `deactivate_user()` verificam, ANTES de aplicar a mudanca, se o usuario e hoje o unico Admin TI ativo; se for, bloqueiam (409 via `admin_router.py`). Cobre o caso mais amplo que a descricao original (rebaixar *outro* Admin TI, nao so a si mesmo), porque a checagem vive no service layer, nao numa comparacao de ID no router.
   - Canal lateral de tempo tambem corrigido: `authenticate()` sempre chama `verify_password()` (contra um hash dummy fixo quando o usuario nao existe — `DUMMY_PASSWORD_HASH` em `security.py`), eliminando a diferenca de tempo de resposta que permitia enumerar usernames.
   - 10 testes novos (`test_last_admin_invariant.py`, `test_auth_timing_safety.py`).
   - **Nota de bastidor real:** a primeira versao dos testes assumia um banco sem nenhum outro Admin TI, o que e falso neste ambiente de dev (existe um Admin TI real e permanente, `lucas.braun`) — a invariante nunca disparava, o `pytest.raises` falhava sem excecao, e o rollback pulado deixava um lock que travava a fixture de limpeza contra a fixture da sessao, um deadlock sem nenhuma saida no terminal. Corrigido mockando a contagem nos cenarios "bloqueado" em vez de depender do estado global da tabela. Tambem foram encontradas e apagadas 5 linhas de usuario de teste orfas de tentativas anteriores travadas (nenhuma delas era `lucas.braun`, confirmado antes e depois).

4. **Validar a fronteira de `/api/match`** — ✅ **CONCLUIDO em 2026-08-26**
   - Entrega: `ALLOWED_CHAT_MODELS` (nova allowlist, `backend/app/config.py`, espelha as opcoes do frontend — duplicacao documentada, nao unificada porque o frontend roda num processo separado sem `app.config`); `MatchRequest.model_name` validado contra ela; `HistoryMessage` (novo model Pydantic, `extra="forbid"`) substitui `List[dict]` — `role` limitado a `Literal["user","assistant"]`, `content` sempre string com `max_length`; `query` tambem ganhou `min_length`/`max_length`. Tudo isso vale automaticamente pra `/api/match` e `/api/match/stream` (mesmo schema). 11 testes novos (`test_match_request_validation.py`).
   - **[NOVO, 2026-08-26] Achados menores da mesma area, ainda nao corrigidos, incluir se for mexer no schema de novo:** `email` em `CriarUsuarioRequest`/`EditarUsuarioRequest` (`admin_router.py`) e `str` puro sem `EmailStr`; `update_user()` aceita `nome`/`email` vazio (`is not None` em vez de checar truthiness).

5. **Corrigir e testar tool calling multiplo** — ✅ **CONCLUIDO em 2026-08-26**
   - Entrega: `messages.append(choice.message)` movido pra fora do loop de tool_calls em `run_pu_matcher_agent()` (`engine.py`) — 1 mensagem `assistant` com todas as tool_calls, seguida de N mensagens `tool`, protocolo valido com 2+ tools na mesma resposta.
   - Achados relacionados corrigidos junto (mesmo seam): `json.loads(tool_call.function.arguments)` agora tem try/except — JSON invalido vindo do LLM gera uma resposta de erro pra tool em vez de derrubar a request inteira; `execute_mcp_tool()` (`pu_mcp_server.py`) devolve `json.dumps(...)` em vez de `str(dict)` (repr Python — aspas simples, `True`/`None` — fora do contrato usual de mensagem `role: tool`).
   - 4 testes novos (`test_tool_calling_sequence.py`).

6. **Classificar e filtrar dados sensiveis no RAG** — ✅ **CONCLUIDO como infraestrutura em 2026-08-26 — nao resolve o dado ja indexado**
   - Entrega: `_e_conteudo_sensivel()` (nova, `backend/app/rag/ingestion.py`) classifica cada chunk na ingestao por palavra-chave (deliberadamente estreita — frases especificas de custo/formula, nao palavras genericas, pra nao bloquear specs tecnicas legitimas como densidade/NCO%/viscosidade); grava `payload["sensivel"]`. `retrieve_products_context(..., incluir_sensivel=False)` (default fail-closed) filtra na busca via `Filter(must_not=[FieldCondition("sensivel", MatchValue(True))])`. `run_pu_matcher_agent`/`stream_pu_matcher_agent` repassam `ver_custos` pro filtro — `stream_pu_matcher_agent` ganhou esse parametro pela primeira vez (`/api/match/stream` precisou trocar `dependencies=[...]` por injecao real de `current_user`, igual `/api/match`). Decisao de engenharia registrada: sem `Permission.VIEW_FORMULA` (spec_rbac.md, pendencia 2, sem decisao de negocio), reaproveitado `VIEW_COSTS` pros dois — mesmo padrao "leitura razoavel, nao extracao literal" que a tarefa 6 da Fase 5 ja usou pro MCP. 14 testes novos.
   - **O que continua nao resolvido:** os pontos ja indexados nao ganham a classificacao retroativamente sem reprocessar os arquivos — e agora, por causa do incidente do ticket 7 (ver abaixo), NAO HA pontos reais indexados de jeito nenhum. A proxima reingestao (`--full`) e tambem a oportunidade de o acervo ja nascer classificado.

7. **Reconciliar o indice com o acervo** — ✅ **CONCLUIDO em 2026-08-26 — causou um incidente real no meio do caminho, corrigido na mesma sessao**
   - Entrega: `_pontos_existentes_por_arquivo()` + reconciliacao por arquivo em `ingest_catalog_directory()` — chunk obsoleto de arquivo que encolheu e apagado; arquivo que saiu do acervo tem todos os pontos apagados. Corrigidos junto: falha de embedding no meio de um arquivo nao grava mais chunks parciais (lista de pontos por arquivo, descartada inteira em erro); corrida na criacao da colecao (`init_qdrant_collection`) nao e mais erro fatal.
   - **🚨 INCIDENTE (ver `docs/incidente_2026-08-26_reingestao_apagou_colecao.md`):** a primeira versao da reconciliacao nao tinha escopo — tratava QUALQUER arquivo fora do diretorio desta execucao como "removido do acervo". Rodar uma verificacao real (2 arquivos de teste) contra a colecao de producao apagou os 11.273 pontos reais. O mesmo padrao existia em uso normal do projeto (`ingest_network.py --test` indexa so 1 familia — rodar `--test` depois de um `--full` teria feito o mesmo). Nao havia snapshot/backup configurado (Fase 8 nao iniciada) — sem recuperacao automatica. Documentos-fonte intactos (so o indice foi apagado), mas a colecao real esta vazia agora — reingestao (`--full`, 3-6h) e necessaria, nao disparada nesta sessao, decisao do usuario.
   - **Correcao:** `_arquivo_esta_no_escopo()` (nova) usa `os.path.commonpath` pra confirmar que um filepath esta dentro da arvore de `dir_path` antes de considera-lo candidato a "removido" — arquivo fora do escopo escaneado nunca e mais candidato a exclusao, nao importa o que essa execucao encontrou. Teste de regressao reproduz o incidente exato (confirmado vermelho antes, verde depois) e revalidado contra o Qdrant real (nao so mockado).
   - 6 testes novos (incluindo o de regressao do incidente).

8. **Validar modelo e dimensao vetorial antes da ingestao**
   - Bloqueado por: nenhum (ticket 7 concluido 2026-08-26).
   - Entrega: incompatibilidade aborta cedo, com diagnostico unico; falhas nao sao contabilizadas como conclusao bem-sucedida.

9. **Cobrir os seams RAG/ingestao/streaming**
   - Bloqueado por: tickets 7 e 8 (2, 4 e 5 ja concluidos).
   - Entrega: testes de integracao para retrieval, ingestao, tools e NDJSON, incluindo erros e regressões encontradas.
   - **[NOVO, 2026-08-26] AUD-012 e mais amplo do que o descrito:** nao e so o evento `error` do backend que o frontend perde — os handlers de `ConnectionError`, `Timeout` e `Exception` generica do proprio generator em `frontend/app.py` tambem escrevem na tela sem nunca mesclar em `_stream_state['answer']`, entao o historico salvo fica vazio/parcial em qualquer um desses casos, nao so no erro reportado pelo backend. Corrigir os 4 pontos juntos.

10. **Endurecer autenticacao e respostas de erro** — ✅ **CONCLUIDO em 2026-08-26**
    - Entrega: rate limiting em `POST /api/auth/login` (`backend/app/auth/rate_limit.py`, novo — em memoria, 5 tentativas falhas / 60s por username, conta pra username existente ou nao pra nao virar mais um canal de enumeracao; sucesso limpa o contador; **debito documentado**: nao sobrevive a mais de 1 replica do backend, precisaria de Redis pra isso). Erros publicos redigidos nos 3 pontos que a auditoria achou (AUD-011): `/api/health` (texto da excecao do Qdrant), `/api/match` (excecao generica), evento `error` do streaming — todos com mensagem generica pro cliente, texto completo continua so no log do servidor.
    - 7 testes novos (`test_login_rate_limit.py`, `test_error_redaction.py`).

11. **Corrigir reversibilidade do schema**
    - Bloqueado por: ticket 1.
    - Entrega: `upgrade -> downgrade -> upgrade` funciona em banco efemero, incluindo enums.

12. **Alinhar documentacao ao estado validado**
    - Bloqueado por: tickets 1 a 11.
    - Entrega: cronograma, progresso e README descrevem comandos, servicos, modelos, limites e evidencias atuais sem contradicoes.

## Decomposicoes que estavam misturadas

- “Idempotencia” misturou evitar duplicata com reconciliar alteracao/remocao; sao criterios distintos.
- “Campos sensiveis” misturou MCP estruturado com RAG nao estruturado; precisam de tickets separados.
- “Agente investigativo” misturou retrieval, estado conversacional, politica de recomendacao, tool calling e formatacao.
- “Ingestao em background” misturou autorizacao, execucao, observabilidade, concorrencia e resultado do job.
- “Fase 5 concluida” misturou entrega das nove tarefas com cumprimento integral da governanca.

Antes de publicar tickets em um tracker, confirmar granularidade, dependencias e se os tickets 6/7 exigem janela de reingestao dos 11.273 pontos.
