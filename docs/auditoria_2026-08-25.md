# Auditoria completa do projeto - 2026-08-25

## Resumo executivo

**Estado geral: nao esta pronto para continuar o cronograma sem correcoes.**

O sistema em execucao esta operacional (4/4 containers healthy, Qdrant com 11.273 pontos e migration atual no `head`) e a suite de RBAC passa integralmente (87/87). Entretanto, uma instalacao limpa nao aplica a migration automaticamente, falhas do retrieval continuam mascaradas como catalogo vazio, a governanca nao protege custos/formulas presentes no RAG e um Admin TI pode rebaixar o proprio perfil. A cobertura automatizada esta concentrada em autenticacao/RBAC; ingestao, retrieval, agente, streaming e frontend permanecem sem testes de comportamento proporcionais ao risco.

Nenhuma feature ou correcao foi implementada nesta auditoria. `CRONOGRAMA.md` e `PROGRESS.md` nao foram alterados.

## Escopo e fontes

- Planejamento: `CRONOGRAMA.md`.
- Estado declarado: `PROGRESS.md`.
- Estado real: codigo em `HEAD` (`9957071`), migration, containers e testes.
- Especificacoes auxiliares: `docs/proposta_do_projeto_similaridade.md`, `docs/guia_mvp_e_codigo_similaridade.md` e `docs/spec_rbac.md`.
- Baseline de code review: `b317738...9957071`.
- `request-refactor-plan` nao existe entre as Skills disponiveis nem nas Skills locais. A secao de refatoracao abaixo reproduz o formato solicitado.
- `research` nao foi acionada: nenhum achado exigiu validar comportamento externo; a evidencia local foi suficiente.

## Mapa da arquitetura atual

```text
Streamlit (frontend/app.py)
  -> FastAPI (backend/app/main.py)
     -> JWT + RBAC (app/auth/*) -> PostgreSQL (users)
     -> RAG engine
        -> embeddings (Ollama nativo ou LiteLLM)
        -> Qdrant (pu_products_catalog)
        -> LiteLLM (chat local/nuvem)
        -> ferramentas MCP simuladas (ERP e homologacoes)

CLI/API de ingestao
  -> extracao PDF/DOCX/DOC/TXT
  -> chunking + embedding
  -> upsert no Qdrant
```

| Modulo | Responsabilidade e interface | Dependencias/riscos |
|---|---|---|
| `frontend/app.py` | Login, selecao de modelo/template, chat sincrono/streaming | Sem testes de frontend; token apenas em `session_state`; depende de icone remoto |
| `app/main.py` | Rotas HTTP, gates de permissao e disparo de ingestao | Schemas permissivos para modelo/historico/caminho; excecoes internas retornadas ao cliente |
| `app/auth/*` | Hash bcrypt, JWT, usuario atual, matriz RBAC e administracao | Modulos razoavelmente profundos e melhor testados; falta protecao contra ultimo-admin e rate limiting |
| `app/db.py` + `models.py` | Seam SQLAlchemy/PostgreSQL e model `User` | Um unico schema; bootstrap de migration nao integrado ao start |
| `alembic/*` | Evolucao do schema relacional | Migration atual consistente com model; downgrade deixa tipos enum |
| `app/rag/engine.py` | Retrieval, prompt, tool calling e resposta | Maior concentracao de complexidade; caminhos sync/stream divergentes; falhas do Qdrant sao ocultadas |
| `app/rag/ingestion.py` | Extracao, chunks, IDs e upsert | Sem reconciliacao de chunks removidos; dimensao da colecao nao e validada; erros por arquivo sao absorvidos |
| `app/rag/embeddings.py` | Adapter de embedding Ollama/LiteLLM | Timeout fixo; resposta/dimensao nao validadas |
| `app/mcp/pu_mcp_server.py` | Ferramentas de catalogo e homologacao | Dados inteiramente simulados podem parecer reais ao modelo/usuario |
| `app/templates.py` | Tres formatos hardcoded | Conteudo ainda nao validado pelo negocio e sem teste de aderencia |
| Docker Compose | Qdrant, PostgreSQL, backend e frontend | 4 servicos; sem job/entrypoint de migration; sem fila real, apenas `BackgroundTasks` no processo web |

Nao existem repositories separados: `user_service.py` combina repository e regras de usuario. Nao existem filas/jobs persistentes, uploads, controllers separados ou integracoes ERP/LIMS reais.

## Especificacao reconstruida

### Fase 0 - ambiente executavel

- Problema: disponibilizar um MVP local com API, UI, RAG, MCP e observabilidade basica.
- Comportamento esperado: stack sobe de um checkout configurado, health reflete dependencias, chat sync/stream e ingestao podem ser acionados.
- Criterios: imagens constroem; servicos ficam healthy; configuracao vem do ambiente; erros de infraestrutura nao se confundem com ausencia de dados.
- Erros: segredo/banco ausente deve falhar alto; Qdrant/LLM indisponivel deve ser reportado sem resposta enganosa.
- Restricoes: nenhum segredo versionado; mesmos modelo/dimensao na ingestao e consulta.

### Fase 1 - indice fiel ao acervo

- Problema: tornar documentos reais pesquisaveis.
- Comportamento esperado: extrair formatos suportados, indexar de forma retomavel e manter o indice sincronizado com alteracoes/remocoes.
- Criterios: contagem verificavel, retrieval correto, extracao legivel, reexecucao sem duplicatas ou residuos.
- Erros: arquivo invalido e falha de embedding devem ser contabilizados; incompatibilidade vetorial deve abortar claramente.
- Restricoes: `.doc` legado permanece fora; planilhas e tabelas estruturadas da proposta nao foram entregues.

### Fase 2 - agente investigativo

- Problema: qualificar a demanda antes de recomendar produto real e sustentado por fontes.
- Comportamento esperado: perguntar diante de requisitos incompletos, recusar afirmacoes sem evidencia, priorizar Boletim e sinalizar integracao simulada.
- Criterios: produto/fatos citados existem nas fontes; requisitos incompatíveis geram alerta; falha do catalogo nao vira recomendacao geral silenciosa.
- Erros: modelo/provedor indisponivel, retrieval falho, tool call invalida e JSON invalido precisam de respostas controladas.
- Restricoes: modelo pequeno apresentou comportamento negativo; reranking/metadados nao foram implementados.

### Fase 5 - identidade e governanca

- Problema: autenticar usuarios e limitar acoes/dados por perfil.
- Comportamento esperado: banco nasce migrado, login emite token expiravel, usuario inativo perde acesso, endpoints negam por padrao e ultimo administrador nao pode ser perdido.
- Criterios: matriz dos cinco perfis; senhas nunca em texto puro; respostas sem hash; campos sensiveis filtrados antes do LLM; provisionamento administravel.
- Erros: login generico, token invalido/expirado 401, falta de permissao 403, duplicidade 409, entidade ausente 404.
- Restricoes: LDAP, revogacao individual, formulas/custos no RAG e bootstrap dedicado permanecem fora.

## Fases e itens verificados

### Fase 0 - declarada concluida; real: parcialmente confirmada

| Item declarado | Classificacao | Evidencia/observacao |
|---|---|---|
| Estrutura, Compose, Dockerfiles e requirements | CONFIRMADO | Arquivos existem; stack atual tem 4 servicos, nao 3 |
| `.env.example` sem segredos | CONFIRMADO | Valores vazios/placeholders; `.env` nao esta versionado nem aparece no historico |
| Backend, frontend, RAG e MCP | CONFIRMADO | Modulos existem; MCP permanece explicitamente simulado |
| Idempotencia/lazy init/TXT/modelos corrigidos | POSSIVEL BUG | Lazy init e TXT existem; a idempotencia deixa chunks obsoletos; retrieval ainda mascara falhas |
| `/api/health` | CONFIRMADO | Retornou API/Qdrant online, 11.273 pontos, status green |
| `/api/ingest` | IMPLEMENTADO SEM TESTES SUFICIENTES | Protegido, mas sem status de job, allowlist de caminho/modelo ou propagacao de falha |
| CLI ingest/health | CONFIRMADO | Implementada; sem testes automatizados |
| Healthchecks Docker | CONFIRMADO | 4/4 containers healthy; documento ainda fala em 3 |
| Indicador de status no frontend | IMPLEMENTADO SEM TESTES SUFICIENTES | Codigo presente; navegador controlavel indisponivel nesta auditoria |
| Streaming + toggle | IMPLEMENTADO SEM TESTES SUFICIENTES | NDJSON implementado; teste apenas mocka o gerador; docstrings alternam SSE/NDJSON |
| Scripts locais | CONFIRMADO | `backend/run_local.py` e `frontend/run_local.py` existem |
| Gemini `text-embedding-004`, 768 dims | DIVERGENTE DA DOCUMENTACAO | Config atual: `gemini-embedding-001`, 3072; checklist e risco do mesmo documento se contradizem |
| Chaves locais preenchidas | CONFIRMADO COM REDACAO | Variaveis relevantes estao definidas; valores nao foram exibidos |
| `docker-compose up` validado | CONFIRMADO NO ESTADO ATUAL | 4 containers healthy; nao prova bootstrap limpo |
| Qdrant acessivel | CONFIRMADO | Health real confirmou 11.273 pontos |

### Fase 1 - declarada concluida; real: funcional com lacunas de fidelidade e testes

| Item declarado | Classificacao | Evidencia/observacao |
|---|---|---|
| Acervo de rede levantado | CONFIRMADO POR CODIGO/HISTORICO | Caminhos existem no script; acesso de rede nao foi reexecutado para evitar ingestao/acesso externo desnecessario |
| `ingest_network.py --test/--full` | CONFIRMADO | Script existe; texto ainda diz `FLEXXI AG`, enquanto caminho e cronograma dizem `FLEXX AG` |
| Piloto de 71 arquivos | CONFIRMADO APENAS HISTORICAMENTE | Sem fixture/relogio de auditoria reproduzivel no repo |
| Ingestao piloto | CONFIRMADO APENAS HISTORICAMENTE | Resultado 52/39 documentado; nao repetido |
| Retrieval FLEXX AG 2047 | IMPLEMENTADO SEM TESTES SUFICIENTES | Evidencia manual historica; nenhuma regressao automatizada |
| Extracao de PDF/tabelas | PARCIALMENTE IMPLEMENTADO | Texto extraido; tabelas PDF nao preservam estrutura, `.doc` falha e planilhas nao sao suportadas |
| `--full` 11.273/8.377 | PARCIALMENTE CONFIRMADO | Qdrant confirma 11.273 pontos; quantidade de arquivos/ignorados depende apenas do log historico |

### Fase 2 - declarada em andamento; itens `[x]` representam atividades, nao aceite funcional

| Item marcado | Classificacao | Evidencia/observacao |
|---|---|---|
| Testar fluxo investigativo | POSSIVEL BUG | Teste foi executado e falhou: recomendacao imediata, especificacoes inventadas e produto MCP ficticio |
| Validar retrieval | PARCIALMENTE IMPLEMENTADO | Bom por codigo exato, ruim por linguagem natural; sem suite de avaliacao |
| Ajustar terminologia do prompt | CONFIRMADO | Prompt diferencia Boletim/FISPQ/Certificado; criterios comerciais seguem ausentes |
| Avaliar reranking/filtros | CONFIRMADO COMO DIAGNOSTICO | Necessidade foi demonstrada; solucao nao implementada |

### Fase 5 - declarada concluida; real: tarefas implementadas, governanca parcial

| Tarefa | Classificacao | Evidencia/observacao |
|---|---|---|
| Schema, model, Postgres e migration | POSSIVEL BUG | Model/migration consistentes e Alembic no head; stack limpa nao executa `upgrade head` |
| Repository/service e bcrypt | CONFIRMADO | Cobertura de CRUD, duplicidade, senha e desativacao |
| Login/JWT `/login` e `/me` | CONFIRMADO COM DEBITO | Token fixo HS256, expiracao e usuario inativo testados; sem rate limiting/revogacao individual |
| Autorizacao centralizada | CONFIRMADO | Cinco perfis e deny-by-default testados |
| Protecao de endpoints | CONFIRMADO | 401/403 e caminhos publicos testados; ingestao restrita a Admin TI |
| Login no Streamlit | IMPLEMENTADO SEM TESTES SUFICIENTES | Codigo presente; sem teste visual/e2e |
| Campos sensiveis | PARCIALMENTE IMPLEMENTADO | MCP estruturado e fail-closed; chunks RAG nao estruturados vazam o mesmo contexto a todos os perfis |
| Administracao de usuarios | POSSIVEL BUG | Fluxos principais testados; auto-rebaixamento/ultimo Admin nao protegido |
| Testes adicionais | CONFIRMADO | 87/87; concentrados em RBAC |
| Documentacao final | DIVERGENTE EM PONTOS | Boa cobertura de RBAC, mas bootstrap de migration e divergencias de Fase 0/embedding nao estao refletidos |

## Evidencias de execucao

| Comando/verificacao | Resultado |
|---|---|
| `docker compose ps` | 4/4 healthy: backend, frontend, postgres, qdrant |
| `docker compose exec -T backend pytest -q -ra` | **87 passed in 48.70s**, 0 failed, 0 skipped |
| `docker compose exec -T backend pytest --collect-only -q` | 87 testes coletados em 7 arquivos |
| `alembic -c backend/alembic.ini current` | `a089248d3b0d (head)` |
| `alembic -c backend/alembic.ini check` | `No new upgrade operations detected` |
| `python -m compileall -q backend frontend ingest_network.py` | PASS |
| `GET http://localhost:8000/api/health` | API/Qdrant online, 11.273 pontos, green |
| `git diff --check b317738...HEAD` | 2 whitespaces finais: `PROGRESS.md:614` e migration linha 4 |
| `docker compose build --quiet` | Inconclusivo: sem saida por varios minutos; interrompido, exit 1. Containers existentes nao foram alterados |
| Lint/typecheck | Nao configurados; nenhum comando aplicavel encontrado |
| Frontend visual/e2e | Nao executado: nenhum navegador controlavel disponivel |
| `pip check` | Inconclusivo: ficou sem saida e foi interrompido |

## Bugs e riscos confirmados ou fortemente sustentados

### AUD-001 - CRITICA - banco novo nao recebe migration automaticamente

- Esperado: `docker compose up` em dados novos entrega login utilizavel.
- Atual: `Dockerfile.backend` inicia diretamente `uvicorn`; Compose nao executa Alembic.
- Repro: iniciar com volume PostgreSQL novo e chamar `/api/auth/login`; a tabela `users` nao existe ate `alembic upgrade head` manual.
- Causa: migration validada manualmente, mas ausente do entrypoint/deploy.
- Arquivos: `Dockerfile.backend`, `docker-compose.yml`, `backend/alembic/`.
- Teste sugerido: smoke de bootstrap com volume efemero, migration e login/admin.

### AUD-002 - ALTA - campos sensiveis no RAG ignoram o perfil

- Esperado: Vendedor nao recebe custos/formulas.
- Atual: os mesmos seis chunks sao injetados para todos; apenas o MCP estruturado usa flags.
- Repro: indexar chunk com custo/formula, consultar como Vendedor e observar o contexto/resposta.
- Causa: payload nao possui classificacao de sensibilidade; autorizacao ocorre depois do retrieval.
- Arquivos: `engine.py`, `ingestion.py`, `docs/spec_rbac.md`.
- Teste sugerido: retrieval por perfil com fixtures publicas/sensiveis.

### AUD-003 - ALTA - falha de retrieval e tratada como catalogo vazio

- Esperado: indisponibilidade/incompatibilidade retorna erro controlado e impede recomendacao interna.
- Atual: qualquer excecao em cliente, embedding ou search retorna `[]`; o agente continua por conhecimento geral.
- Repro: derrubar Qdrant ou fornecer vetor incompatível e chamar match.
- Causa: `except Exception: return []` em `retrieve_products_context`.
- Arquivos: `backend/app/rag/engine.py:46`.
- Teste sugerido: Adapter Qdrant falha e endpoint retorna 503/estado degradado.

### AUD-004 - ALTA - Admin TI pode causar lockout por auto-rebaixamento

- Esperado: nenhuma operacao deixa zero administradores ativos.
- Atual: autodesativacao e proibida, mas `PATCH /api/auth/users/{proprio_id}` aceita trocar o proprio perfil.
- Repro: unico Admin altera o proprio perfil para `vendedor`; requisicao seguinte de admin recebe 403.
- Causa: guarda cobre somente `deactivate`.
- Arquivos: `admin_router.py:109`, `admin_router.py:125`.
- Teste sugerido: impedir auto-rebaixamento e rebaixamento/desativacao do ultimo Admin.

### AUD-005 - ALTA - modelo e papeis do historico sao controlados pelo cliente

- Esperado: modelos aprovados e historico limitado a mensagens `user`/`assistant` validas.
- Atual: `model_name: str` e `history: List[dict]` chegam ao LiteLLM sem allowlist/schema. Repro de schema aceitou `unapproved/provider-model` e papel `system`.
- Impacto: abuso de custo/quota, falhas por provedor e injecao estrutural de prompt.
- Arquivos: `main.py:29`, `engine.py:106`.
- Teste sugerido: 422 para modelo/papel/campos nao permitidos e limites de tamanho.

### AUD-006 - MEDIA - tool calling multiplo monta sequencia invalida

- Esperado: uma mensagem `assistant` com N tool calls, seguida de N respostas `tool`.
- Atual: a mensagem `assistant` inteira e anexada dentro do loop, uma vez por tool call.
- Repro: mock do LLM retornando duas tool calls e assercao sobre `messages` da segunda completion.
- Causa: `messages.append(choice.message)` dentro do `for`.
- Arquivo: `engine.py:129`.

### AUD-007 - MEDIA - reingestao conserva chunks obsoletos

- Esperado: indice reflete o arquivo atual e remove documento apagado/movido ou chunks excedentes.
- Atual: IDs usam `filepath::chunk_index` e somente `upsert`; nenhum delete/reconcile.
- Repro: indexar arquivo longo, reduzi-lo e reindexar; IDs excedentes permanecem.
- Arquivo: `ingestion.py:102`.

### AUD-008 - MEDIA - dimensao/modelo da colecao podem divergir

- Esperado: validar configuracao contra a colecao antes de processar arquivos.
- Atual: colecao existente nao tem dimensao conferida; API aceita `embedding_model` arbitrario enquanto `VECTOR_SIZE` e global.
- Efeito: erros por arquivo podem terminar em mensagem de conclusao, ou retrieval pode cair silenciosamente em `[]`.
- Arquivos: `ingestion.py:16`, `main.py:35`.

### AUD-009 - MEDIA - downgrade Alembic deixa enums PostgreSQL

- Esperado: `upgrade -> downgrade -> upgrade` reversivel.
- Atual: downgrade remove tabela/indices, mas nao `user_status`, `user_role`, `user_origin`.
- Arquivo: migration `a089248d3b0d`.

### AUD-010 - MEDIA - login sem rate limiting

- Esperado: limitar tentativas por conta/origem e observar bloqueios.
- Atual: bcrypt e chamado ilimitadamente em endpoint publico.
- Arquivo: `auth/router.py:36`.
- Status: ja documentado, ainda aberto.

### AUD-011 - MEDIA - erros internos sao expostos ao cliente

- Atual: `/api/match` usa `detail=str(e)`; streaming envia `message=str(e)`; health publico inclui texto da excecao do Qdrant.
- Risco: vazamento de nomes de host, provedor, configuracao e detalhes de falha.
- Arquivos: `main.py:84/118`, `engine.py:212`.

### AUD-012 - BAIXA - frontend perde estado correto em erro de stream

- Atual: evento `error` e exibido, mas nao entra em `_stream_state['answer']`; ao final, historico recebe resposta vazia/parcial e o backend sempre emite `done` no `finally`.
- Arquivos: `frontend/app.py:233`, `engine.py:212`.

## Seguranca

Confirmado positivamente:

- `.env` nao e rastreado e nao apareceu no historico consultado.
- Senhas usam bcrypt e nao aparecem nos schemas de resposta.
- JWT fixa HS256 e verifica expiracao/assinatura.
- Usuario desativado e recarregado do banco a cada request.
- Endpoints de negocio exigem permissao; raiz e health sao publicos por decisao explicita.
- Campos MCP estruturados falham fechados por default.

Riscos atuais: AUD-002, AUD-004, AUD-005, AUD-010 e AUD-011. Tambem falta uma politica corporativa de senha, auditoria de acoes e revogacao individual de token. Nao foi encontrada evidencia de SQL injection, command injection, upload inseguro, senha em texto puro ou secret real versionado.

## Banco e persistencia

- Model e migration coincidem; `alembic check` nao detectou drift.
- PK UUID, indices/unique de username e email, unique nullable de `external_id`, enums e nullability estao presentes.
- Nao ha foreign keys porque so existe a tabela `users`.
- Testes usam PostgreSQL real e limpam dados temporarios.
- Problemas: bootstrap ausente (AUD-001), downgrade incompleto (AUD-009), nenhuma trilha de auditoria apesar da justificativa de desativar em vez de excluir.

## Cobertura ausente prioritaria

- Bootstrap limpo: Compose + Alembic + primeiro login.
- Retrieval: sucesso, colecao vazia, Qdrant fora, embedding fora, vetor incompatível e payload malformado.
- Ingestao: PDF/DOCX/TXT, `.doc`, reexecucao, arquivo reduzido/removido, dimensao e relatorio de falhas.
- Agente: zero/uma/multiplas tools, JSON de argumentos invalido, ausencia de fonte, modelo indisponivel e requisitos incompletos.
- Seguranca: ultimo Admin, auto-rebaixamento, allowlist de modelo, schema/limites de historico, rate limit e redacao de erro.
- Streaming: protocolo, erro no meio, desconexao e paridade com fluxo sincrono.
- Frontend: login, expiracao, logout, sync/stream e renderizacao responsiva.

## Divergencias documentais

1. Fase 0 fala em 3 servicos/healthchecks; a stack atual tem 4 com PostgreSQL.
2. O checklist ainda marca `text-embedding-004`/768, enquanto a configuracao atual e `gemini-embedding-001`/3072 e o `.env` usa Ollama/768.
3. “Idempotencia corrigida” e mais forte que a garantia real: duplicatas por mesmo caminho/indice sao evitadas, mas residuos permanecem.
4. “Fase 5 concluida” precisa manter qualificacao explicita: tarefas concluidas, governanca de RAG incompleta e bootstrap quebrado.
5. README instrui `docker-compose up` e depois bootstrap do Admin, mas omite `alembic upgrade head` necessario num banco novo.
6. README mostra `/api/ingest` sem token no exemplo, embora a rota exija Admin TI.
7. Proposta pede planilhas e tabelas estruturadas; implementacao aceita texto achatado e nao suporta planilha.
8. `ingest_network.py` ainda exibe `FLEXXI AG`, divergindo do caminho real `FLEXX AG`.

## Refatoracoes recomendadas

### Unificar construcao da conversa RAG

- Problema: sync e stream duplicam contexto/prompt/historico e ja divergem em tools, autorizacao e erro.
- Risco: correcoes aplicadas a um caminho apenas.
- Beneficio: uma interface para montar mensagens e uma politica de entrada.
- Impacto: `engine.py`, schemas e testes.
- Testes antes: snapshots comportamentais das mensagens para zero/uma/multiplas fontes e historico.
- Passos: extrair schema de mensagem; extrair builder puro; migrar sync; migrar stream; validar paridade; depois decidir tool calling no stream.

### Separar retrieval de politica de fallback

- Problema: o Adapter Qdrant absorve todos os erros e decide continuar sem catalogo.
- Risco: recomendacao enganosa e observabilidade ruim.
- Beneficio: erro tipado, endpoint decide 503/fallback explicito, testes mais profundos.
- Testes antes: colecao ausente, vazia, indisponivel e busca valida.
- Passos: definir resultados tipados; remover `except Exception`; traduzir na camada HTTP/agente; adicionar metricas/log; manter fallback apenas quando autorizado.

### Tornar ingestao reconciliavel

- Problema: upsert incremental nao remove estado antigo nem valida schema vetorial.
- Risco: produtos/documentos obsoletos continuam recomendaveis.
- Beneficio: indice fiel, operacao auditavel e retomavel.
- Testes antes: reduzir, mover e excluir documento; falhar embedding no meio; dimensao diferente.
- Passos: adicionar `document_id`/hash/metadados; validar colecao; indexar versao; apagar pontos antigos do documento; registrar manifesto/resultado; reingerir.

Nao se recomenda refatoracao estetica ampla do modulo de auth; suas interfaces sao pequenas, centralizadas e bem cobertas.

## Debitos tecnicos

- Rate limiting, revogacao individual e politica corporativa de senha.
- Sem auditoria persistida de login, mudanca de perfil, ingestao ou recomendacao.
- MCP simulado indistinguivel de dado real na resposta.
- Reranking/tipo de documento ausentes.
- `.doc` legado e planilhas fora.
- Dependencias majoritariamente com apenas limite inferior; build pouco reproduzivel.
- Sem lint/typecheck/coverage/CI e sem teste visual/e2e.
- Ingestao em `BackgroundTasks` sem fila, lock, progresso, cancelamento ou exclusao mutua.
- Bootstrap do primeiro Admin depende de script manual.

## Proximas acoes recomendadas

### Corrigir imediatamente

1. AUD-001: aplicar migrations de forma explicita e testada no bootstrap/deploy.
2. AUD-003: parar de mascarar falha de retrieval como base vazia.
3. AUD-004: garantir ao menos um Admin TI ativo e impedir auto-rebaixamento.
4. AUD-002: bloquear uso piloto com dados sensiveis ate classificar/filtrar chunks.

### Corrigir antes de continuar o cronograma

1. AUD-005 e AUD-006: validar requests/modelos e corrigir protocolo de tools.
2. AUD-007 e AUD-008: reconciliar ingestao e validar dimensao/modelo.
3. Adicionar testes de retrieval/ingestao/agente/streaming nos seams publicos.
4. Redigir erros externos e adicionar rate limiting.
5. Atualizar README/cronograma/progresso somente depois de aprovar esta auditoria.

### Pode ser tratado posteriormente

1. Downgrade dos enums, whitespace e typo `FLEXXI`.
2. CLI dedicada de bootstrap, cobertura visual e pipeline de lint/typecheck.
3. `.doc`, planilhas, reranking e fila persistente, conforme prioridade de produto.

