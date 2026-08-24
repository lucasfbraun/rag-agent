# Log de Progresso — PU Matcher

Log cronológico do andamento do projeto. Cada entrada corresponde a uma sessão de trabalho.
Ver visão geral de fases em [CRONOGRAMA.md](CRONOGRAMA.md).

---

## 2026-08-24 — Sessão 20: Fase 5 (RBAC) — tarefa 5, proteção dos endpoints existentes

**Tarefa implementada:** `require_permission()` (pronto desde a tarefa 4) finalmente aplicado nos endpoints de negócio reais.

- `backend/app/main.py`: `dependencies=[Depends(require_permission(Permission.X))]` em `/api/match` e `/api/match/stream` (`Permission.VIEW_CATALOG`), `/api/templates` (`Permission.SELECT_TEMPLATE`). `/` e `/api/health` deliberadamente deixados públicos — comentário explícito no código explicando por quê (liveness/monitoramento, sem dado de negócio, Docker healthcheck usa `/`).
- **Lacuna real encontrada:** `/api/ingest` não tinha nenhuma permissão da matriz original que cobrisse ele — `docs/spec_rbac.md` nunca falou de ingestão. Adicionada `Permission.MANAGE_INGESTION` (nova, documentada no código como extensão além da spec original), concedida só a Admin TI — mesmo padrão conservador já usado nas pendências da tarefa 4.
- `backend/tests/test_endpoint_protection.py` (novo): 11 testes — sem token em cada endpoint de negócio (401), endpoints públicos continuam públicos (sem regressão), perfil com a permissão passa e chega até a lógica de negócio (mockada), perfil sem a permissão é barrado com 403 **antes** de qualquer efeito colateral (confirmado: `ingest_catalog_directory` mockado nunca é chamado quando barrado).

**Testes:** 56/56 no total. Validado também ao vivo contra o servidor real: `/api/match` sem token → 401; com token do Admin real (`lucas.braun`) → 200.

**Code review (skill `code-review`, 2 eixos):**
- **Standards:** achou 3 pontos. (1) Falso positivo — apontou que o teste de sucesso do `/api/ingest` bateria num diretório inexistente e falharia; **verifiquei ao vivo dentro do container real e o diretório existe** (o sub-agent tinha testado no host, não no container onde os testes rodam) — não corrigido, não era bug. (2) Real — o docstring de `permissions.py` afirmava categoricamente que nenhuma permissão foi "inventada sem evidência", o que ficou falso depois de `MANAGE_INGESTION`; **corrigido**, docstring atualizado pra reconhecer a exceção documentada. (3) Real — faltava teste do caminho de sucesso pra `/api/match/stream` (só tinha o 401); **corrigido**, novo teste adicionado.
- **Spec:** confirmou as permissões escolhidas batendo com a matriz, `MANAGE_INGESTION` bem documentada e conservadora, zero scope creep em `engine.py`/`rag/`. **Achado mais importante da tarefa:** confirmou que o frontend Streamlit nunca envia header de autenticação — a partir desta tarefa, **toda pergunta pela tela vai falhar com 401**. Não estava sendo tratado em nenhum lugar até esse achado — agora registrado explicitamente no `CRONOGRAMA.md` como pendência urgente nova.
- **Bug real que eu mesmo introduzi e corrigi durante a sessão:** ao adicionar o teste do `/api/match/stream`, um `Edit` impreciso duplicou/bagunçou o final de outro teste (deixou duas linhas soltas de asserção pertencentes ao teste anterior, causando `KeyError: 'answer'`). Achado ao rodar a suíte (não pelo code review — já tinha commitado a suspeita antes de rodar de novo), corrigido, suíte revalidada.

**Validações executadas:** `py_compile` em todos os arquivos; suíte completa rodada 2x (antes e depois da correção do bug de edição); `SELECT username, perfil FROM users` confirmando só o admin real, sem lixo de teste; `/api/health` confirmando 11.273 pontos intactos; teste ao vivo via `curl` confirmando 401 sem token e 200 com token real.

**Decisões técnicas importantes:**
1. `/` e `/api/health` permanecem públicos por decisão explícita, não por esquecimento — documentado em comentário no código
2. `MANAGE_INGESTION` é a segunda vez nesta fase que uma permissão precisou ser criada além da matriz original (documentada, não escondida) — sinal de que a matriz da proposta original nunca cobriu operações "administrativas de plataforma" (ingestão, e futuramente coisas como configuração do sistema), só recursos de negócio
3. Lógica de negócio mockada nos testes desta tarefa — o objetivo era testar a autorização, não o RAG/ingestão de novo (já cobertos em outras sessões)

**Pendências (fora do escopo desta tarefa):** campos sensíveis (tarefa 6), administração/provisionamento (tarefa 7), testes adicionais (tarefa 8), documentação final (tarefa 9).

**🚨 Pendência nova, urgente, fora do plano de 9 tarefas:** o frontend Streamlit precisa de tela de login (username/senha → guardar token → mandar `Authorization: Bearer` em toda chamada) antes de o sistema voltar a ser usável por qualquer vendedor real. Hoje, literalmente ninguém consegue usar o chat pela interface.

**Riscos:** nenhum novo além do já registrado (rate limiting do login, campos sensíveis em RAG não estruturado). O risco antigo "nenhum endpoint exige autenticação" está **resolvido** — agora é o oposto: autenticação funciona bem demais e quebrou o único jeito que existia de usar o sistema.

**Próximo item do cronograma:** tarefa 6 — Restrição de campos sensíveis (já tem pendência técnica conhecida, ver Sessão 16). Mas pode fazer sentido priorizar a tela de login do frontend antes, já que sem ela o sistema é inutilizável na prática — decisão de prioridade pro usuário.

---

## 2026-08-24 — Sessão 19: Fase 5 (RBAC) — tarefa 4, camada centralizada de autorização

**Tarefa implementada:** `Permission`/`ROLE_PERMISSIONS`/`require_permission`, seguindo o mesmo processo das tarefas 1-3.

- `backend/app/auth/permissions.py` (novo): enum `Permission` (8 permissões, uma por ação+recurso: `VIEW_CATALOG`, `VIEW_HOMOLOGATION_SUMMARY`/`FULL`, `SELECT_TEMPLATE`, `EDIT_TEMPLATE`, `DELETE_TEMPLATE`, `VIEW_COSTS`, `MANAGE_USERS`); `ROLE_PERMISSIONS` (dict `Role -> set[Permission]`) transcrito célula por célula da matriz em `docs/spec_rbac.md` — nenhuma permissão inventada; `has_permission()`; `require_permission()` (factory de dependency FastAPI, `Depends(require_permission(Permission.X))`).
- **Decisão de deny-by-default:** nas 3 pendências que a spec já tinha documentado sem decisão de negócio (custos pro Técnico, excluir template pra Gestor/Químico-PD, gerenciar usuários pra Gestor), a permissão foi **negada**, não concedida — comentário explícito no código aponta a política.
- `backend/app/auth/dependencies.py` (novo): `get_current_user` extraído de `router.py`. Motivo: `permissions.py` precisa dessa dependency, e não fazia sentido um módulo "mais core" (autorização) depender do módulo de rotas HTTP (`router.py`) — ajuste de direção de dependência, não mudança de comportamento.
- `backend/app/auth/router.py`: atualizado só pra importar de `dependencies.py` em vez de definir `get_current_user` inline — sem mudança de comportamento, `/login` e `/me` continuam idênticos.

**Testes:** `backend/tests/test_permissions.py`, 9 novos testes (45/45 no total) — todos os 5 perfis mapeados na matriz (nenhum cai no fallback silencioso), Admin TI tem todas as permissões, Vendedor não vê laudo completo/custos, Técnico vê laudo completo mas não custos (pendência negada), Gestor/Químico-PD editam template mas não excluem, só Admin TI gerencia usuários, e o comportamento do `require_permission()` como dependency (deixa passar quem tem a permissão, barra com 403 quem não tem). Não precisa de Postgres — `User` é só objeto Python nesses testes, não é persistido.

**Code review (skill `code-review`, 2 eixos):** **primeira vez nesta fase que os dois eixos não encontraram nada pra corrigir.** Standards confirmou deny-by-default, ausência de bypass (grep em todo o diff não achou nenhum `if user.perfil ==` ad-hoc fora de `permissions.py`), e composição correta da dependency do FastAPI. Spec confirmou a matriz batendo célula por célula com `docs/spec_rbac.md` (incluindo as 3 pendências corretamente negadas) e zero scope creep (nenhum arquivo de `main.py`/`rag/`/endpoints de negócio no diff).

**Validações executadas:** `py_compile` em todos os arquivos; 45/45 testes passando; confirmado que o usuário real `lucas.braun` (Admin TI, criado na sessão anterior) segue intacto no banco, sem lixo de teste.

**Decisões técnicas importantes:**
1. `get_current_user` movido pra módulo próprio — a primeira vez nesta fase que uma tarefa exigiu um ajuste de arquitetura (não só código novo) pra manter a direção de dependência correta
2. Deny-by-default é a política formal agora, não só uma escolha pontual — qualquer pendência futura da matriz (e há several documentadas) deve ser tratada assim até virar decisão de negócio confirmada

**Pendências (fora do escopo desta tarefa):** proteção dos endpoints existentes (tarefa 5 — `require_permission` existe mas não está `Depends()` em nenhum lugar ainda), restrição de campos sensíveis (tarefa 6), administração (tarefa 7), testes adicionais (tarefa 8), documentação final (tarefa 9).

**Riscos:** a camada de autorização existe e está testada, mas **não protege nada ainda** — nenhum endpoint chama `require_permission()`. `/api/match` continua aberto pra qualquer perfil (ou nenhum).

**Próximo item do cronograma:** tarefa 5 — Proteção dos endpoints existentes (`main.py`). É aqui que a autorização passa a valer na prática.

---

## 2026-08-24 — Sessão 18: Fase 5 (RBAC) — tarefa 3, autenticação (login + token)

**Tarefa implementada:** login manual com token de sessão (JWT), seguindo o mesmo processo das tarefas 1-2.

- `backend/app/config.py`: `SECRET_KEY` (reaproveitado — já existia no `.env` sem uso desde a Fase 0) e `ACCESS_TOKEN_EXPIRE_MINUTES` (default 480min/8h). Mesmo padrão de falha alto se não definido.
- `backend/app/auth/token.py` (novo): `create_access_token`/`decode_access_token`, JWT HS256. Algoritmo fixo no decode (`algorithms=["HS256"]`) — não confia no campo `alg` do token, proteção contra ataque de confusão de algoritmo.
- `backend/app/auth/user_service.py`: `authenticate(session, username, password)` — credenciais erradas e usuário inexistente levantam a mesma exceção com a mesma mensagem (evita enumeração de usuário); usuário inativo levanta exceção própria, verificada *depois* da senha (não antes) para não revelar que a conta existe antes de confirmar a senha.
- `backend/app/auth/router.py` (novo): `POST /api/auth/login` e `GET /api/auth/me` + dependency `get_current_user`. Decisão: incluir `/me`/`get_current_user` nesta tarefa (não só o login) porque autenticação sem uma forma de *verificar* o token não é autenticação completa — mas nada foi aplicado aos endpoints de negócio existentes (isso é a tarefa 5), confirmado limpo pelo code review.
- `.env`/`.env.example`: `SECRET_KEY` real gerada (`secrets.token_urlsafe`), `ACCESS_TOKEN_EXPIRE_MINUTES`
- `requirements.txt`: `pyjwt`, `httpx` (necessário pro `TestClient` do FastAPI nos testes)

**Testes:** `backend/tests/test_auth.py`, 15 novos testes — round-trip de token, token expirado/adulterado/malformado rejeitados, `authenticate()` nos 4 cenários (certo/senha errada/não existe/inativo), e os endpoints HTTP via `TestClient` (login certo, senha errada, inativo, `/me` sem token, com token inválido, com token válido confirmando ausência de `password_hash` na resposta, e token de usuário desativado *depois* do login parando de funcionar). **36/36 no total.** Testado também ao vivo contra o servidor real rodando (não só `TestClient` em processo): criei um usuário manualmente, fiz login via `curl`, confirmei `/me` retornando dados corretos sem senha, e 401 sem token — limpo depois.

**Nota técnica:** os testes deste arquivo precisam `commit()` (o `TestClient` faz requisição HTTP de verdade, usando uma sessão separada da do teste) — diferente das tarefas 1-2, que só usavam `flush()+rollback()`. Por isso a limpeza aqui é por `DELETE` explícito no teardown de uma fixture (`created_user_ids`), não por rollback.

**Code review (skill `code-review`, 2 eixos, sem isolamento de worktree):**
- **Standards:** confirmou explicitamente algoritmo JWT fixo (proteção contra confusão de algoritmo), comparação de senha timing-safe (`bcrypt.checkpw`), expiração checada no decode, `SECRET_KEY` falhando alto. 1 achado real corrigido: o endpoint de login retornava **401 pra senha errada mas 403 pra conta desativada** — isso permite um atacante descobrir que um username existe e está desativado, mesmo sem saber a senha (enumeração de usuário). Corrigido: agora sempre 401 genérico pros dois casos; a distinção entre os dois continua existindo internamente (exceções diferentes no service), só não é exposta na resposta HTTP. 1 achado documentado sem correção: **login sem rate limiting** — corrigir exigiria uma lib própria (ex: `slowapi`) ou infraestrutura de contador, escopo maior que esta tarefa; registrado como débito de segurança explícito no `CRONOGRAMA.md`.
- **Spec:** confirmado sem scope creep (nada em `rag/`, `main.py` só ganhou 2 linhas pra montar o router, nenhuma lógica de perfil/permissão da tarefa 4); a decisão de incluir `/me` nesta tarefa foi avaliada e considerada justificada, não scope creep; todas as constraints de segurança satisfeitas com evidência checada linha a linha no diff.

**Validações executadas:** `py_compile` em todos os arquivos; 36/36 testes passando (2 rodadas — antes e depois da correção de enumeração); `SELECT count(*) FROM users` = 0 depois de cada rodada; teste manual ao vivo via `curl` contra o servidor real; `/api/health` confirmando 11.273 pontos intactos (sem regressão no RAG).

**Decisões técnicas importantes:**
1. Token JWT (stateless), não sessão em tabela própria — mais simples, sem precisar de tabela de sessões nem job de limpeza de sessão expirada; trade-off: não dá pra revogar um token individual antes de expirar (aceitável pra escopo atual, web de administração de sessões ativas não foi pedida)
2. `/me` incluído na tarefa (não só `/login`) — autenticação sem verificação não é completa; validado pelo code review como decisão correta, não scope creep
3. 401 genérico sempre no login, mesmo pra conta desativada — prioriza não vazar enumeração de usuário sobre dar uma mensagem mais específica pro usuário legítimo desativado (que pode descobrir o motivo por outro canal, ex: contatando o Admin TI diretamente)

**Pendências (fora do escopo desta tarefa):** autorização centralizada (tarefa 4), proteção dos endpoints de negócio existentes (tarefa 5), campos sensíveis (tarefa 6), administração (tarefa 7), testes adicionais (tarefa 8), documentação final (tarefa 9). Rate limiting do login fica como débito de segurança registrado, não uma tarefa numerada do plano original.

**Riscos:** login funciona mas **nenhum endpoint de negócio exige autenticação ainda** — `/api/match`, `/api/ingest` etc. continuam 100% abertos. Ter login funcionando não significa que o sistema está protegido.

**Próximo item do cronograma:** tarefa 4 — Camada centralizada de autorização (`Permission`, `ROLE_PERMISSIONS`, `require_permission`).

---

## 2026-08-24 — Sessão 17: Fase 5 (RBAC) — tarefa 2, repository/service de usuários

**Tarefa implementada:** repository/service de usuários com hash de senha, seguindo o mesmo processo da tarefa 1 (implementar → testar → revisar → validar → documentar).

- `backend/app/auth/security.py` (novo): `hash_password`/`verify_password` via `bcrypt`, mínimo de 8 caracteres (`SenhaFracaError`). Único lugar do código que chama `bcrypt` diretamente.
- `backend/app/auth/user_service.py` (novo): `create_user`, `get_user_by_id`, `get_user_by_username`, `list_users`, `update_user`, `set_password`, `deactivate_user`. "Excluir" foi implementado como desativação (`status=INATIVO`), não remoção da linha — decisão justificada no próprio código (auditoria: perder histórico de quem fez o quê não é aceitável). Erros de duplicidade do banco (`IntegrityError`) são traduzidos em exceção de domínio (`UsuarioJaExisteError`) — quem chama o service nunca vê exceção do SQLAlchemy vazando.
- `requirements.txt`: `bcrypt>=4.1.0`
- `docs/spec_rbac.md`: documentada a política de senha (mínimo 8 caracteres — não é política de segurança completa, é só defesa contra senha vazia/trivial; falta requisito de negócio se a empresa tiver política própria)

**Testes:** `backend/tests/test_user_service.py`, 15 novos testes (hash/verify de senha, criação com duplicidade rejeitada, busca por username, listagem, update, troca de senha, desativação, e os casos de "não encontrado" de cada operação). **21/21 no total** (6 da tarefa 1 + 15 novos), rodados 3 vezes ao longo da sessão (antes e depois do code review, e na imagem final reconstruída) — sempre passando, sem dado de teste sobrando no banco.

**Code review (skill `code-review`, 2 eixos, direto no checkout principal — sem isolamento de worktree, que bloqueou a tarefa 1):**
- **Standards:** verificou explicitamente ausência de senha em texto puro/log, comparação de senha via `bcrypt.checkpw` (timing-safe), zero SQL cru. 2 achados reais de duplicação corrigidos: bloco "busca usuário ou lança erro" repetido 3x → extraído `_get_user_or_raise()`; bloco "flush ou traduz erro de duplicidade" repetido 2x → extraído `_flush_or_raise_duplicate()`. Um achado sem ação (Primitive Obsession leve nos parâmetros de `create_user` — aceitável com um único call site hoje).
- **Spec:** confirmado sem scope creep (nada em `main.py`/`engine.py`, nenhuma lógica de autenticação/autorização de tarefas futuras); todas as constraints de segurança satisfeitas com evidência; achado 1 lacuna de documentação (política de mínimo de senha não estava na spec) — **corrigido, documentado em `docs/spec_rbac.md`**.

**Validações executadas:** `py_compile` em todos os arquivos novos/alterados; 21/21 testes passando (3 rodadas); `SELECT count(*) FROM users` = 0 (sem sujeira); rebuild oficial do container e reconfirmação contra a imagem final, não só a cópia ao vivo usada pra iteração rápida.

**Decisões técnicas importantes:**
1. "Excluir" usuário = desativar, nunca DELETE — decisão de auditoria, documentada no código e aqui
2. `update_user` não permite trocar `username`/`origem`/`external_id` — só `nome`/`email`/`perfil`; troca de senha tem função própria (`set_password`), separada por ser operação sensível
3. Exceções de domínio (`UsuarioJaExisteError`, `UsuarioNaoEncontradoError`) escondem o SQLAlchemy do resto da aplicação — quem chama o service não precisa saber que existe um Postgres por trás

**Pendências (fora do escopo desta tarefa):** autenticação/login (tarefa 3), autorização centralizada (tarefa 4), proteção de endpoints (tarefa 5), campos sensíveis (tarefa 6, com a pendência técnica já registrada na Sessão 16), administração (tarefa 7), testes de integração ponta a ponta via API (tarefa 8), documentação final (tarefa 9).

**Riscos:** ainda não existe login nem verificação de perfil em endpoint nenhum — o service de usuários por si só não protege nada, só permite criar/gerenciar a conta. Não confundir "dá pra criar usuário" com "sistema está protegido".

**Próximo item do cronograma:** tarefa 3 — Autenticação (login manual → token de sessão).

---

## 2026-08-24 — Sessão 16: Fase 5 (RBAC) iniciada — Etapa 1 análise + Etapa 2 decisão de provisionamento + tarefa 1 (schema base)

**Processo seguido:** análise de arquitetura (skill `codebase-design`) antes de qualquer alteração; decisão de provisionamento explicitamente levantada e confirmada pelo usuário antes de implementar (não escolhida silenciosamente); especificação escrita (`docs/spec_rbac.md`, já que a skill `to-spec` está bloqueada para invocação pelo modelo — usuário tentou `/to-spec` e não funcionou no ambiente dele); plano incremental de 9 tarefas; implementada só a tarefa 1.

**Etapa 1 — Análise (nenhum arquivo alterado):** confirmado que não existe nenhuma autenticação, nenhum conceito de usuário, nenhum PostgreSQL, e nenhuma proteção nos endpoints hoje (busca exaustiva no código, zero hits reais). Achada a matriz de perfis já definida em `docs/proposta_do_projeto_similaridade.md` (seção 5) — usada como fonte real da matriz de acesso, não inventada. Achada lacuna arquitetural real: "custos industriais/fórmulas" (campos sensíveis citados no cronograma) não existem como dado estruturado hoje — vivem em texto livre de RAG, o que torna "restringir campo sensível no backend" tecnicamente mais complexo do que um filtro simples de dict.

**Etapa 2 — Decisão de provisionamento:** comparadas as 3 estratégias (manual / AD-LDAP puro / híbrido) nos critérios pedidos (complexidade, segurança, manutenção, dependência de infra, comportamento se AD cair, facilidade de dev/teste, impacto arquitetural). Recomendação apresentada e **confirmada pelo usuário**: manual agora, com a Interface de autenticação desenhada para aceitar um Adapter LDAP depois sem reescrever autorização. Motivo: nenhuma confirmação de que a empresa tem AD/LDAP disponível, nem credenciais, nem contato de TI.

**Tarefa 1 implementada — Schema base:**
- `backend/app/db.py` (novo): engine/sessão SQLAlchemy, `Base` declarativa, `get_session()` — única Seam de conexão com o Postgres
- `backend/app/models.py` (novo): model `User` + enums `Role` (5 perfis), `UserStatus`, `UserOrigin`, todos conforme `docs/spec_rbac.md`
- `backend/app/config.py`: estendido (não duplicado) com `POSTGRES_*`/`DATABASE_URL` — falha alto (`RuntimeError`) se `POSTGRES_PASSWORD` não estiver definida, em vez de conectar silenciosamente com senha em branco (corrigido no code review, ver abaixo)
- `docker-compose.yml`: novo serviço `postgres` (postgres:16-alpine), com `PGDATA` numa subpasta (senão o Postgres recusa inicializar por causa do `.gitkeep` no volume — bug real encontrado e corrigido)
- `backend/alembic/`: migrations configuradas para usar `app.config.DATABASE_URL` e `Base.metadata` (não duplica config); migration inicial `a089248d3b0d` criando a tabela `users`, aplicada e validada contra o Postgres real
- `.env`/`.env.example`: variáveis `POSTGRES_*` (senha real gerada com `secrets.token_urlsafe`, não commitada)
- `docs/spec_rbac.md` (novo): especificação completa da Fase 5

**Testes (primeira suíte automatizada do projeto):** `backend/tests/test_models.py`, 6 testes de integração contra Postgres real — persistência com defaults corretos, os 5 perfis, username/email duplicado rejeitado, usuário inativo, usuário de origem LDAP sem senha. **6/6 passaram**, sem dado de teste deixado no banco (fixture com rollback).

**Code review (skill `code-review`, 2 eixos):**
- **Bloqueio técnico encontrado e contornado:** o primeiro sub-agent de Standards rodou isolado num git worktree próprio, que não enxerga mudanças só *staged* (não commitadas) no checkout principal — retornou "acesso bloqueado" honestamente em vez de inventar um resultado. Refeito sem isolamento de worktree, direto no checkout principal — funcionou.
- **Spec:** sem scope creep (nada em `main.py`/`engine.py`); todas as 5 restrições de segurança do pedido satisfeitas com evidência (sem senha em texto puro, sem senha de AD persistida, secrets via env, nada sensível logado, reaproveita `config.py` existente); achado 1 desvio não documentado (`external_id` com `unique=True` não estava na spec original) — **corrigido: justificativa adicionada à spec**, não ao código (a constraint em si estava certa)
- **Standards:** 2 achados reais corrigidos — (1) `POSTGRES_PASSWORD` com fallback silencioso para string vazia, trocado por falha explícita; (2) `lambda: datetime.now(timezone.utc)` triplicado em `models.py`, extraído para `_utcnow()`. Também removido import não utilizado (`Boolean`).
- Após as correções: rebuild, containers saudáveis, 6/6 testes passando de novo, e testado manualmente que o `RuntimeError` da senha realmente dispara quando `POSTGRES_PASSWORD` está vazia.

**Validações executadas:** `py_compile` em todos os arquivos novos/alterados; `alembic current` confirma migration no head; `SELECT count(*) FROM users` confirma 0 linhas (sem sujeira de teste); 6/6 testes pytest passando contra Postgres real, duas vezes (antes e depois das correções do review).

**Nenhum lint/typecheck configurado no projeto** (confirmado, mesmo achado da Sessão 15) — `py_compile` foi o mais próximo disponível.

**Decisões técnicas importantes:**
1. Enum Python fixo para `Role`, não tabela dinâmica — sem evidência de que o negócio precise criar/editar perfis via UI
2. `config.py` existente foi estendido, não duplicado — reaproveitando a Seam já criada na Sessão 12
3. Testes contra Postgres real (integração), não mocks — não havia padrão de teste no projeto pra seguir, e é o banco que já sobe via docker-compose no dev

**Pendências (não implementadas nesta tarefa, fora do escopo):**
1. Repository/service de usuários, autenticação, autorização centralizada, proteção de endpoints, restrição de campos sensíveis, administração, testes adicionais — tarefas 2–9 do plano
2. Matriz de acesso tem 3 pendências funcionais reais não resolvidas pela proposta original (documentadas em `docs/spec_rbac.md`): significado de "Opcional" pra Técnico ver custos; regra de acesso a "fórmulas" separada de custos; se Gestor Comercial gerencia usuários (assumido que não, só Admin TI, por ser a leitura mais segura)
3. Proteção de campos sensíveis no conteúdo RAG (não estruturado) — pendência técnica, não de negócio

**Riscos:** nenhuma lógica de autenticação/autorização existe ainda — o schema por si só não protege nada. Não confundir "tabela criada" com "sistema seguro".

**Próximo item do cronograma:** tarefa 2 — Repository/service de usuários (CRUD + hash de senha).

---

## 2026-08-24 — Sessão 15: Validação formal da Sessão 14 (commit `65125c0`)

**Contexto:** usuário pediu validação rigorosa e com evidência da última tarefa (ajuste de terminologia no `AGENT_SYSTEM_PROMPT`), não só a palavra de que "está tudo certo".

**Confirmado com evidência:**
- Diff isolado a 1 arquivo de código (`backend/app/rag/engine.py`, +10/-3) + docs — sem scope creep
- Container em execução tem o prompt atualizado carregado (verificado via `assert` dentro do container, não só no arquivo do host)
- Reexecução ao vivo da validação funcional: densidade "1,04 ± 0,01 g/cm³" reproduzida corretamente (33.5s desta vez); resposta de EPIs variou entre as duas execuções (uma admitiu a lacuna, outra deu orientação genérica de segurança) — sem alucinar dado específico falso nas duas

**Achado honesto:** este projeto **não tem nenhuma suíte de testes automatizados** (sem `pytest`, sem `test_*.py`, sem config de lint/typecheck). Validação desta tarefa foi funcional (chamada real à API), não por testes unitários — registrado como débito técnico do projeto, não desta tarefa especificamente.

**Code review (skill `code-review`, 2 eixos em paralelo, ponto fixo `d7b4565`):**
- **Standards** (sem `CODING_STANDARDS.md` no repo, usada baseline de smells do Fowler): 2 achados, ambos julgamento — duplicação de narrativa entre CRONOGRAMA.md/PROGRESS.md (convenção intencional do projeto, não é bug) e "Primitive Obsession" (taxonomia de tipo de documento como prosa no prompt em vez de dado estruturado — já é item futuro documentado, fora do escopo)
- **Spec** (CRONOGRAMA.md linha 62 como fonte): sem scope creep confirmado; lacuna de "critérios" já divulgada; 1 achado acionável — a categoria "Certificado"/"ANALISE" citada no prompt nunca tinha sido verificada com amostra real (diferente de Boletim/FISPQ, auditados nas Sessões 7/10)
- **Correção aplicada:** amostrei um `Certificado FLEXX® RGT 2437 49623.pdf` e um `FLEXX PI 2102 ANALISE.docx` reais no Qdrant — confirmam exatamente a caracterização do prompt ("laudo de lote específico", com número de lote, validade, situação aprovado/reprovado). Texto do prompt mantido como está, só a lacuna de evidência foi fechada.

**Testes/validações executados:** `py_compile` no arquivo alterado (sintaxe válida); busca confirmando ausência de suíte de testes e de config de lint/typecheck; chamada real `/api/match` reexecutada ao vivo; amostragem de 2 documentos reais no Qdrant pra fechar o achado do code review.

**Nenhuma alteração de código nesta sessão** — foi puramente validação/auditoria da Sessão 14.

**Riscos/débitos técnicos confirmados (não novos, mas reafirmados com evidência):**
1. Ausência total de suíte de testes automatizados no projeto
2. Taxonomia de tipo de documento vive só como texto no prompt, não como metadado estruturado na ingestão
3. Resposta do modelo pequeno tem variância entre execuções (mesma pergunta, respostas diferentes em qualidade de transparência sobre lacunas)

---

## 2026-08-22 — Sessão 14: Fase 2 — `AGENT_SYSTEM_PROMPT` ajustado com terminologia real (escopo limitado)

**Processo seguido:** commit da Sessão 13 feito primeiro (`d7b4565`). Próximo item pendente da Fase 2 em ordem: "Ajustar `AGENT_SYSTEM_PROMPT` com terminologia e critérios reais da empresa".

**Decisão de escopo (antes de implementar):** o item pede duas coisas de natureza diferente — "terminologia" (vocabulário, nomenclatura — tenho evidência real dos documentos indexados) e "critérios" (como o time comercial de fato prioriza/qualifica uma demanda — exige input do time comercial/P&D, que não está disponível; a própria Fase 3 já lista isso como dependência externa). Implementei só a parte de terminologia, documentando a parte de critérios como pendente — evita inventar "critérios de negócio" sem base real.

**Implementado em `backend/app/rag/engine.py` (`AGENT_SYSTEM_PROMPT`):**
- Menção à marca real "FLEXX®" (antes só "PU" genérico)
- Nova seção "COMO INTERPRETAR OS DOCUMENTOS DO ACERVO": explica os 3 tipos reais de documento (Boletim Técnico = fonte de especificação/aplicação; FISPQ = só segurança/manuseio, texto repetitivo entre produtos; Certificado/ANALISE = laudo de lote específico) e instrui o agente a priorizar Boletim para specs. Endereça diretamente o achado da Sessão 10 (FISPQ diluindo a busca) no nível do prompt, sem mexer na ingestão.

**Validação:** pergunta combinando as duas categorias de documento ("Quais os EPIs recomendados e a densidade do FLEXX CAT 43?") via `ollama/qwen2.5:3b`, 98.2s:
- **Densidade: correta** — "1,04 ± 0,01 g/cm³", batendo exatamente com o valor real do Boletim FLEXX CAT 43. Fonte citada: `Boletim FLEXX CAT 43.pdf`.
- **EPIs: sem alucinação** — como a FISPQ não foi recuperada nessa consulta, o modelo reconheceu a lacuna ("consulte a FISPQ para informações precisas") em vez de inventar equipamentos de proteção específicos. Melhora real de comportamento vs. o teste da Sessão 13 (que tinha inventado especificações completas sem nenhuma fonte real).
- Formato de saída ainda não segue o template padrão (JSON solto, não a estrutura com emojis/tabela) — limitação já conhecida do modelo pequeno, não é o foco desta tarefa.

**Testes/validações executados:**
- Rebuild do container `backend` — healthy, `points_count: 11273` intacto
- Chamada real via `/api/match`, HTTP 200, resposta inspecionada manualmente contra os dados reais do Boletim

**Pendências desta tarefa:**
1. "Critérios reais da empresa" — não implementado, precisa de input do time comercial/P&D
2. Formato de saída do template ainda inconsistente com modelo pequeno — considerar se é tarefa separada (Fase 3, validação de templates)
3. Bug do MCP simulado (Sessão 13) segue sem correção

**Próximo item do cronograma (Fase 2, em ordem):** "Testar comportamento opinativo em casos de requisitos incompatíveis"

**Commit desta sessão:** `65125c0`.

---

## 2026-08-22 — Sessão 13: Fase 2 — teste do fluxo conversacional investigativo (resultado: gap encontrado)

**Processo seguido:** leitura obrigatória de CRONOGRAMA.md e PROGRESS.md antes de qualquer alteração; fase atual identificada como Fase 2; primeiro item pendente com dependência satisfeita escolhido ("Testar fluxo conversacional investigativo"), após confirmar que o bloqueio documentado (máquina lenta) tinha melhorado o suficiente para tentar.

**Checagem de dependência (não presumida, testada):**
- Prompt trivial sem contexto: **18.9s** (era 278s na Sessão 9 — melhora grande, causa nunca identificada, aparentemente transitória)
- Pergunta real com RAG + tools: **>150s** no primeiro teste — acima do timeout de 120s do frontend

**Implementado (mínimo necessário para viabilizar o teste):** `frontend/app.py` — timeout de `requests.post` ampliado de 120s→240s (stream) e 90s→240s (síncrono). Rebuild do container frontend.

**Teste executado:** pergunta vaga de propósito — "Quero um produto para assento de ônibus" (o exemplo literal citado no `AGENT_SYSTEM_PROMPT` como caso que DEVE gerar 2-4 perguntas de qualificação antes de recomendar). Modelo: `ollama/qwen2.5:3b`. Tempo: 103.8s (dentro do novo timeout).

**Resultado: negativo.** O agente não fez nenhuma pergunta de qualificação — foi direto para uma recomendação final completa, com tabela de especificações inventadas (densidade 50 kg/m³, dureza 85 Shore A) que não correspondem a nenhuma fonte real recuperada. As `sources` retornadas (FISPQ de produtos não relacionados: VSB, F 210, CL 2097) não têm relação com assentos automotivos. O produto "recomendado" (`PU-SEAT-5000 FR`) veio da ferramenta MCP **simulada**, não da base real indexada.

**Achado adicional (bug, não corrigido — fora do escopo desta tarefa):** o agente usa dados da ferramenta MCP simulada como se fossem reais, sem sinalizar ao usuário que aquilo não veio do catálogo/RAG real. Risco de o vendedor tratar um produto fictício como real.

**Interpretação:** não está claro se é falha da arquitetura do agente (prompt/fluxo) ou limitação do modelo pequeno (3B) em seguir instruções complexas do system prompt — não testado ainda com modelo maior/de nuvem para isolar a causa.

**Fase 2 no cronograma:** item marcado como `[x]` (a atividade de TESTAR foi completada e validada) mas com o resultado negativo documentado explicitamente — não é um "passou".

**Testes/validações executados:**
- Rebuild do container `frontend` — healthy
- Chamada real via `/api/match` com timeout de 220s — completou em 103.8s, HTTP 200
- Resposta inspecionada manualmente contra a especificação do `AGENT_SYSTEM_PROMPT`

**Próximo item do cronograma (Fase 2, em ordem):** "Ajustar `AGENT_SYSTEM_PROMPT` com terminologia e critérios reais da empresa" — mas dado o achado desta sessão, pode fazer mais sentido primeiro investigar por que o comportamento investigativo não está sendo seguido (testar com modelo maior, revisar se as instruções estão claras o suficiente) antes de ajustar terminologia. Não decidido — fica para o usuário priorizar na próxima sessão.

**Bloqueios/riscos para intervenção humana:**
1. Comportamento investigativo do agente não funciona como especificado — precisa de decisão: investigar com modelo mais forte, ou reescrever o prompt, ou aceitar como limitação conhecida por enquanto?
2. Bug do MCP simulado sendo tratado como dado real — risco de negócio (vendedor pode repassar produto fictício ao cliente) — vale corrigir antes de qualquer teste com usuário piloto real (Fase 7)
3. Máquina segue instável em performance (melhora não explicada, pode regredir)

---

## 2026-08-22 — Sessão 12: Deepening — `config.py` como fonte única da verdade (fora do cronograma, a pedido do usuário)

**Contexto:** não é um item do cronograma — usuário pediu uma análise de arquitetura (skill `codebase-design`) antes de seguir com desenvolvimento. A análise achou duplicação real de configuração espalhada por `main.py`, `engine.py`, `ingestion.py` e `cli.py` (`QDRANT_HOST`/`PORT`, `COLLECTION_NAME`, `EMBEDDING_MODEL`, `VECTOR_SIZE`, modelo de chat padrão — esse último hardcoded em 3 lugares diferentes), que já causou bugs reais de divergência nesta sessão (ex: `cli.py --model` ainda apontava pro OpenAI antigo, dessincronizado do resto).

**Implementado:** `backend/app/config.py` (novo) como única fonte da verdade para essas constantes. `main.py`, `engine.py`, `ingestion.py`, `cli.py` atualizados para importar de lá em vez de redefinir. `/api/health` (main.py) e `cmd_health` (cli.py) também deduplicados — ambos reimplementavam a mesma checagem de conectividade com o Qdrant.

**Validado:** rebuild do container backend, `/api/health` retornando `points_count: 11273` corretamente (dado da Fase 1 intacto), `python -m app.cli health` funcionando dentro do container. Nenhuma regressão.

**Arquivos alterados:** `backend/app/config.py` (novo), `backend/app/main.py`, `backend/app/rag/engine.py`, `backend/app/rag/ingestion.py`, `backend/app/cli.py`.

---

## 2026-08-22 — Sessão 11: `--full` concluído — Fase 1 fechada

**Resultado final da ingestão completa** (`ingest_network.py --full`, rodando desde a Sessão 10):

- **11.273 trechos indexados de 8.377 arquivos** (confirmado batendo com `points_count` real no Qdrant)
- **3.933 arquivos ignorados** — majoritariamente `.doc` legado (adiado por decisão do usuário, ver Sessão 10), mais alguns arquivos temporários de bloqueio do Word (`~$*.docx`, gerados quando um documento está aberto — comportamento correto ignorá-los, não são documentos reais) e 1 PDF genuinamente vazio ("Ponto de Fulgor lembrete.pdf")
- Rodou inteiramente com motor local/gratuito (Ollama `nomic-embed-text`), sem custo de API

**Fase 1 marcada como ✅ Concluído** no cronograma — acervo real completo indexado e buscável. Itens não-bloqueantes que seguem em aberto: ajuste fino de `chunk_size`/`overlap` (avaliar com uso real) e suporte a `.doc` legado (adiado).

**Estado atual:** base vetorial completa e pronta para uso. Próxima fronteira é a Fase 2 (qualidade de conversa) — segue bloqueada pela lentidão anormal da máquina para inferência local de chat (ver Sessão 9) e pela falta de crédito em Gemini/OpenAI (ver Sessão 6).

**Próximos passos:**
1. Retomar teste de qualidade de chat quando a máquina normalizar ou houver crédito de nuvem
2. Implementar a recomendação da Sessão 10 (priorizar Boletim sobre FISPQ na busca) — requer um novo campo de metadado na ingestão; como o acervo completo já foi indexado, isso pode ser feito como um ajuste incremental (reingestão é idempotente) quando fizer sentido
3. `.doc` legado permanece pendente até decisão sobre instalar LibreOffice

**Bloqueios:** mesmos da Sessão 9 (máquina lenta pra chat local, sem crédito de nuvem) — não afetam mais a Fase 1, que está concluída.

---

## 2026-08-21 — Sessão 10: `--full` retomado + auditoria de qualidade de extração + `.env.example`/README atualizados

**Contexto:** retomada do `--full` (tinha parado em 252 pontos após a Sessão 9). Enquanto roda em background, seguido o cronograma com trabalho que não compete por Ollama/CPU com a ingestão.

**Documentação corrigida:** `.env.example` não tinha nenhuma menção a `OLLAMA_API_BASE`/`EMBEDDING_MODEL`/`VECTOR_SIZE` (só existiam no `.env` real, não versionado) — adicionado com comentários explicando `host.docker.internal` (containers) vs. `localhost` (scripts no host). `README.md` também não citava Ollama na lista de provedores — corrigido.

**Falso alarme investigado:** usuário reportou ver conteúdo "corrompido" (acentos e `®` virando `�`) e um arquivo (`FISPQ FLEXX® CL 2034.pdf`) aparentemente indexado com "só duas linhas". Investigação:
- A corrupção de caracteres era **só exibição no terminal** (Windows console não rendendo UTF-8) — o dado real salvo no Qdrant está com acentuação perfeita, confirmado escrevendo em arquivo e relendo.
- O arquivo da FISPQ está **completo**: 3 chunks (4820 + 4218 + 94 caracteres) cobrindo as 16 seções inteiras do documento. O que pareceu "duas linhas" foi só um chunk pequeno (o rodapé final) visto isoladamente, sem perceber os outros dois chunks maiores.

**Auditoria de qualidade de extração (Fase 1, item validado):** amostrados 6 boletins técnicos de produtos diferentes (adesivos, catalisadores, pré-polímeros). Especificações técnicas (viscosidade, NCO%, densidade, faixas com ±) saem legíveis, rótulo+unidade+valor adjacentes, tanto em PDF (texto corrido) quanto DOCX (células separadas por `|`). Conclusão: extração é funcionalmente boa o suficiente para o LLM responder perguntas técnicas — mesmo sem preservar estrutura de tabela.

**Decisão sobre `.doc` legado:** avaliadas as opções (instalar LibreOffice no Windows do usuário vs. só no container vs. adiar). Usuário optou por **adiar** — LibreOffice não está instalado em nenhum dos dois ambientes, e instalar no host é uma mudança fora do escopo do projeto que exige confirmação explícita.

**Nota sobre commits:** usuário commitou via VS Code (`3680f79 indexação de itens`, `ed5a467 ajustado .env`) capturando todas as correções das Sessões 6–10 — Claude não commitou nada diretamente nesta sessão.

**Estado atual:** `--full` em andamento (passou de 252 → 1152+ pontos durante esta sessão). Fase 1 com extração de texto e retrieval validados; faltam `--full` terminar, `.doc` legado (adiado) e ajuste de `chunk_size`/`overlap`.

**Próximos passos:**
1. Acompanhar `--full` até terminar (ou até decidir interromper)
2. Retomar teste de qualidade de chat quando a máquina normalizar ou houver crédito de nuvem
3. `.doc` legado fica pendente até o usuário decidir instalar LibreOffice

**Bloqueios:** nenhum novo — mesmos da Sessão 9 (máquina lenta pra chat local, sem crédito de nuvem).

---

## 2026-08-21 — Sessão 9: Docker caiu e voltou sozinho + Ollama local anormalmente lento nesta máquina

**Contexto:** entre sessões, Docker Desktop parou de rodar (provável reinício/hibernação da máquina) e derrubou o `--full` no meio. Ao reabrir o Docker, os containers subiram sozinhos (`restart: always`) e os dados sobreviveram — coleção Qdrant foi de 52 para **252 pontos** antes de parar, nada foi perdido (persistido em `data/qdrant_storage/`, 67MB em disco).

**Retomando o teste de chat, novo problema:** perguntas via frontend voltaram a falhar com "não foi possível conectar ao backend". Investigação:
- Não era o container caindo (sempre healthy)
- Não era falta de conexão com o Ollama (confirmado alcançável do container)
- Era **tempo de resposta genuinamente anormal**: uma pergunta real com `qwen2.5:7b` (com RAG + tools) levou **283 segundos** — bem acima do timeout de 120s do frontend
- Testamos trocar para `qwen2.5:3b` (modelo bem menor, mesma família, baixado nesta sessão) esperando resolver por tamanho — **não resolveu**: um prompt trivial ("diga apenas OK", sem contexto, sem ferramentas) levou **278 segundos**. Isso não é comportamento normal de um modelo de 3B em CPU (deveria ser segundos, não minutos)

**Diagnóstico:** o problema não é tamanho de modelo nem código do projeto — é o **ambiente desta máquina** especificamente. Processo `llama-server` (runner de inferência do Ollama) rodando com uso de memória baixo pro tamanho do modelo, processo `mstsc.exe` (cliente de Área de Trabalho Remota) ativo simultaneamente — pode ser máquina virtual/remota com CPU compartilhada/limitada, antivírus escaneando os arquivos do modelo em tempo real, ou pressão de memória causando troca de disco pesada. Não investigado a fundo (fora do escopo de código) — decisão do usuário foi pausar os testes de chat local por agora.

**Também correto e registrado, mas não resolvido nesta sessão:** o mesmo bug de overhead do litellm (`OllamaError: Could not get model info`, ver Sessão 7) também afeta chamadas de **chat**, não só embedding — só o embedding foi migrado pro helper direto (`app/rag/embeddings.py`). Não vale a pena investir nisso agora dado que o problema real (minutos de latência) é muito maior que os ~40s de overhead do litellm.

**Estado atual:** RAG (retrieval) segue validado e funcionando — a etapa de busca no Qdrant retorna os documentos certos rapidamente, o gargalo é só a geração de texto do modelo de chat local nesta máquina. Modelo padrão do dropdown do frontend ficou em `ollama/qwen2.5:3b` (adicionado como primeira opção, `qwen2.5:7b` continua disponível).

**Próximos passos:**
1. Investigar a causa da lentidão da máquina (antivírus, RDP, memória) quando o usuário tiver tempo — fora do escopo de código
2. Retomar teste de chat quando: (a) a máquina normalizar, ou (b) Gemini/OpenAI tiverem crédito de novo (essas nuvens respondiam em segundos antes de ficarem sem saldo — ver Sessão 6)
3. Retomar o `--full` da ingestão (parou em 252/muitos milhares de pontos) quando fizer sentido — é idempotente, seguro rodar de novo

**Bloqueios:** ambiente local (máquina) com desempenho anormal para inferência local — não é bug de código, investigação de infraestrutura pausada a pedido do usuário.

---

## 2026-08-21 — Sessão 8: `--full` rodando + achado sobre limite do Ollama local (CPU-only)

**Contexto:** disparado `ingest_network.py --full` (acervo completo, ~12k arquivos, todas as ~37 famílias de produto). Durante a execução, usuário testou o chat e recebeu "não foi possível conectar ao backend".

**Diagnóstico:** não foi bug de código nem container caindo (containers seguiam healthy o tempo todo). Causa real: `ollama ps`/`api/ps` mostrou os dois modelos carregados ao mesmo tempo — `qwen2.5:7b` (chat) e `nomic-embed-text` (embedding) — ambos com `size_vram: 0`, ou seja, **100% CPU, sem GPU disponível nesta máquina**. O Ollama processa uma requisição por vez; com o `--full` bombardeando embeddings continuamente, uma pergunta de chat concorrente entra na fila e pode demorar minutos — numa medição chegou a **8 minutos**, bem acima do timeout de 120s do frontend (`frontend/app.py`), daí o erro de "conexão".

**Também confirmado:** o mesmo bug de overhead do litellm (~40s extras por chamada tentando buscar `/api/show` no Ollama e falhando, ver Sessão 7) afeta as chamadas de **chat**, não só embedding — `engine.py` ainda usa `litellm.completion()` puro para chat, só o embedding foi migrado pro helper direto (`app/rag/embeddings.py`). Ainda não corrigido para chat: exigiria reimplementar o ciclo de tool-calling (usado pelas ferramentas MCP) contra a API nativa do Ollama, escopo maior — decisão foi não fazer agora.

**Decisão:** usuário optou por deixar o `--full` rodando e aceitar o chat lento/instável nesse período, em vez de pausar a ingestão ou só aumentar o timeout do frontend. Ingestão é idempotente/retomável (IDs determinísticos), então não há risco em rodar `--full` e `--test` novamente depois se precisar.

**Nota para Fase 8 (produção):** se o modelo local (Ollama) for considerado para produção, esta sessão mostrou que **CPU-only não sustenta ingestão em massa + chat concorrente** no mesmo host sem fila/lentidão séria. Vale considerar GPU dedicada ou separar o host de ingestão do host que serve o chat, caso o Ollama local vire a opção definitiva (hoje é fallback por falta de crédito no Gemini/OpenAI).

**Estado atual:** `--full` seguia rodando em background ao fim desta sessão, coleção Qdrant crescendo (confirmado retorno de fontes de múltiplas famílias, ex. FLEXX ISO, FLEXX CAT, n-PENTANO, além da FLEXX AG já indexada na Sessão 7).

---

## 2026-08-21 — Sessão 7: Motor 100% gratuito (Ollama local) + primeira ingestão real + RAG estava quebrado desde o início

**Contexto:** com Gemini e OpenAI sem crédito (ver Sessão 6), configuramos um motor local gratuito via Ollama (já instalado na máquina) e rodamos a primeira ingestão real de teste. No processo, dois bugs sérios apareceram — um de performance, outro de correção (este último, crítico: a busca RAG nunca funcionou).

**1) Motor local via Ollama:**
- Chat: `qwen2.5:7b` (já baixado, suporta tool calling) — virou opção padrão no dropdown do frontend
- Embedding: `nomic-embed-text` baixado (274MB, 768 dims)
- `.env`: adicionado `OLLAMA_API_BASE=http://host.docker.internal:11434` (endereço usado pelos containers para falar com o Ollama no host) e `EMBEDDING_MODEL=ollama/nomic-embed-text` / `VECTOR_SIZE=768`
- Atenção: rodar scripts direto no host (fora do Docker) exige *override* de `OLLAMA_API_BASE=http://localhost:11434` e `QDRANT_HOST=localhost` — `host.docker.internal` resolve por DNS no host, mas a conexão real trava/timeout; é um endereço pensado para container→host, não host→host.

**2) Bug de performance — litellm + Ollama (corrigido):** `litellm.embedding()` para modelos `ollama/*` levava ~44s por chamada (a chamada real levava 2s; os outros 42s eram o cost-calculator do litellm tentando 3x buscar `/api/show` no Ollama e falhando). Criado `backend/app/rag/embeddings.py` com `get_embedding()`: para modelos `ollama/*` vai direto na API nativa do Ollama (`requests.post .../api/embed`), sem passar pelo litellm; outros provedores continuam via litellm. `ingestion.py` e `engine.py` atualizados para usar essa função. Resultado: ingestão que ia levar horas passou a rodar em minutos.

**3) Bug crítico — RAG nunca retornou resultados, desde a Fase 0 (corrigido):** ao testar a primeira pergunta contra dados reais, `sources` sempre voltava vazio. Causa: `requirements.txt` tinha `qdrant-client>=1.9.0` (sem teto), então o pip sempre instala a versão mais nova — hoje 1.19.0. Nessa versão o método `.search()` foi removido do cliente (`AttributeError`), e o substituto `.query_points()` não existe no servidor Qdrant pinado no `docker-compose.yml` (`v1.9.2`) — 404 do servidor. Como `retrieve_products_context()` tem um `try/except` amplo que devolve lista vazia em qualquer erro, isso ficava **mascarado**: o chat sempre respondia (usando só a ferramenta MCP simulada ou conhecimento geral do modelo), nunca dava erro visível, e nunca teve motivo óbvio pra alguém desconfiar que a busca no catálogo real estava sempre falhando silenciosamente.
   - Correção: `requirements.txt` → `qdrant-client>=1.9.0,<1.10.0` (fixa a mesma faixa de versão do servidor); `engine.py` voltou a usar `.search()` (compatível com 1.9.x)
   - **Validado:** pergunta sobre "FLEXX AG 2047" retornou como top resultado (score 0.86) exatamente `Boletim FLEXX AG 2047.pdf` — RAG funcionando de ponta a ponta pela primeira vez no projeto

**4) Primeira ingestão de teste executada:** `python ingest_network.py --test` (família FLEXX® AG) → **52 trechos indexados de 39 arquivos** (todos PDF). 30 arquivos `.doc` legados (formato binário OLE2 pré-2007, confirmado pelo cabeçalho do arquivo) foram pulados — `python-docx` só lê `.docx`, limitação conhecida e ainda não resolvida.

**Estado atual:** pipeline RAG completo e validado end-to-end com dados reais, 100% em motores gratuitos/locais (Ollama). Fase 1 tem dados reais indexados e buscáveis pela primeira vez.

**Próximos passos:**
1. Rodar `--full` quando fizer sentido (avaliar tempo: CPU local é mais lento que nuvem — ideal medir taxa de indexação em minutos antes de comprometer horas)
2. Resolver `.doc` legado (LibreOffice headless, antiword, ou reexportar os arquivos como `.docx`/PDF na origem)
3. Quando Gemini/OpenAI tiverem crédito de novo, comparar qualidade de resposta Ollama vs. nuvem antes de decidir qual vai pra produção

**Bloqueios:** nenhum técnico — pipeline validado e desbloqueado.

---

## 2026-08-21 — Sessão 6: Modelos Gemini descontinuados + limite de quota da chave atual

**Contexto:** usuário testou o chat pela UI e recebeu `litellm.NotFoundError ... "This model models/gemini-2.0-flash is no longer available"`. Investigação mostrou que o Google descontinuou vários modelos usados no projeto.

**Causa raiz:**
- `gemini-2.0-flash` (chat) e `text-embedding-004` (embedding, usado na ingestão) foram desativados pelo Google — confirmado batendo direto na API do Gemini com a chave real do `.env`. `gemini-2.5-flash`/`gemini-2.5-pro` também retornam 404 ("no longer available **to new users**") — a chave do projeto é nova e não tem acesso a esses modelos legados.
- Modelos atuais confirmados funcionando com a chave: `gemini-flash-latest`, `gemini-3.6-flash`, `gemini-3.5-flash`, `gemini-3.1-flash-lite`. Modelos "pro" (`gemini-pro-latest`, `gemini-3.1-pro-preview`) respondem 429 (quota, não indisponibilidade).
- Embedding: `text-embedding-004` não existe mais; substituído por `gemini-embedding-001`, que retorna **3072 dimensões** (não 768).

**Correções aplicadas:**
- `backend/app/main.py`, `backend/app/rag/engine.py`, `frontend/app.py`: modelo padrão de chat trocado para `gemini/gemini-flash-latest` (alias sempre-atual, mais resistente a descontinuações futuras); dropdown do frontend atualizado.
- `backend/app/rag/ingestion.py`, `backend/app/rag/engine.py`: `EMBEDDING_MODEL` → `gemini/gemini-embedding-001`; `VECTOR_SIZE` → `3072` (seguro trocar, coleção Qdrant ainda não existe).
- `backend/app/main.py`: `IngestRequest.embedding_model` também alinhado (estava sobrescrevendo o default certo do `ingestion.py` com `text-embedding-3-small`, da OpenAI, por engano).
- Adicionado `num_retries` (3 no chat/consulta, 5 na ingestão) em todas as chamadas `litellm.completion`/`litellm.embedding` — o modelo `gemini-flash-latest` está retornando 503 "high demand" com frequência (Google, lado do servidor); com retry automático a maioria dos casos passa a se resolver sozinha. Precisou adicionar `tenacity` ao `requirements.txt` (dependência que o mecanismo de retry do litellm usa e não estava instalada).

**Achado novo e importante para a Fase 1:** ao testar repetidamente, a chave `GEMINI_API_KEY` atual bateu em **429 (quota excedida)** — a resposta do Google mostra `quotaValue: "20"` para o modelo, ou seja, a chave está num tier gratuito bem restrito (poucas requisições por período). Isso é uma preocupação real para a ingestão em massa (`--full`, ~12k arquivos = milhares de chamadas de embedding): nesse tier a ingestão completa provavelmente vai estourar quota constantemente, mesmo com retry/backoff. Recomendo verificar se há billing habilitado no projeto Google (AI Studio/Vertex) antes de rodar `--full`, ou tratar isso com throttling adicional no script.

**Estado atual:** chat voltando a funcionar (confirmado via `/api/match` real, resposta 200 OK); containers `backend`/`frontend` reconstruídos e healthy. Testes repetidos consumiram parte da quota diária da chave — evitar bater a API sem necessidade pelo resto do dia.

**Próximos passos:**
1. Verificar/ativar billing na chave Gemini antes de rodar qualquer ingestão em volume
2. Rodar `python ingest_network.py --test` (agora com embedding corrigido) quando a quota tiver folga
3. Confirmar visualmente no frontend que o chat responde sem 503/429 recorrentes

**Bloqueios:** quota da API Gemini (tier gratuito, quotaValue 20) é o principal risco para a Fase 1 em escala — sem upgrade de billing, ingestão de milhares de documentos deve ser lenta/instável.

---

## 2026-08-21 — Sessão 5: Auditoria de cronograma + acervo real localizado (trabalho não documentado da Sessão 4)

**Contexto:** verificação solicitada pelo usuário — havia trabalho feito e commitado (`6dcb8e6 "ler dados de pasta da rede"`, 2026-08-20) que não tinha sido registrado no CRONOGRAMA.md/PROGRESS.md. Auditoria completa do estado real do projeto (git log, containers, .env, Qdrant, acesso à rede).

**Trabalho não documentado encontrado e agora registrado:**
- `ingest_network.py` (novo): script que aponta a ingestão para a pasta de rede real da empresa (`\\10.1.1.205\flexivel\GRUPOS\Qualidade\Documentação de Produto`), com modo `--test` (1 família de produto) e `--full` (acervo completo, ~12k arquivos, 3-6h estimado)
- `backend/app/rag/ingestion.py` e `engine.py`: modelo de embedding trocado de `text-embedding-3-small` (OpenAI) para `gemini/text-embedding-004` (768 dims), configurável via `EMBEDDING_MODEL`/`VECTOR_SIZE` — alinhado com a chave Gemini já preenchida no `.env`

**Bugs encontrados e corrigidos nesta sessão:**
- `ingest_network.py`: `ACERVO_TESTE` apontava para `FLEXXI® AG` (nome errado) — pasta real é `FLEXX® AG` (confirmado via listagem da pasta de rede, 71 arquivos PDF/DOC)
- `ingest_network.py`: `ACERVO_BASE` estava sem o acento em "Documentação" — caminho não existia; corrigido para bater com o caminho real (`Documentação de Produto`)

**Verificações de estado atual:**
- Docker: 3/3 containers rodando e healthy há 16h (`pu_matcher_qdrant`, `pu_matcher_backend`, `pu_matcher_frontend`)
- `.env`: `GEMINI_API_KEY` preenchida (chave real); `OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`GROK_API_KEY`/`SECRET_KEY` seguem vazias
- `GET /api/health` → API e Qdrant online, mas coleção `pu_products_catalog` com **0 pontos** — a ingestão real ainda não foi executada, apesar do script já existir e funcionar
- Pasta de rede confirmada acessível a partir desta máquina (ping OK, `Test-Path` OK na raiz e no caminho acentuado)
- `data/raw_documents/` local está vazio (só `.gitkeep`) — a estratégia atual é ingerir direto da pasta de rede via `ingest_network.py`, não copiar arquivos para dentro do repo

**Limitação identificada (não corrigida ainda):** `extract_text_from_file()` usa `python-docx`, que só lê `.docx` — os muitos arquivos `.doc` (formato binário antigo) presentes no acervo real provavelmente falham na extração (capturado pelo `try/except`, então não derruba a ingestão, mas o conteúdo desses arquivos fica de fora silenciosamente).

**Estado atual:** ambiente 100% funcional, acervo real localizado e acessível, script de ingestão corrigido — **pronto para rodar o primeiro teste real**: `python ingest_network.py --test`.

**Próximos passos:**
1. Rodar `python ingest_network.py --test` (indexa apenas `FLEXX® AG`, 71 arquivos) e validar no `/api/health` que `points_count > 0`
2. Testar uma pergunta real no frontend (`localhost:8501`) sobre um produto FLEXX AG e conferir se o RAG recupera contexto correto
3. Decidir se vale a pena tratar os arquivos `.doc` legados (conversão via LibreOffice/antiword) antes do `--full`, já que fazem parte relevante do acervo
4. Só depois rodar `--full` (~12k arquivos, 3-6h) para o acervo completo

**Bloqueios:** nenhum bloqueio técnico — ingestão de teste pode ser rodada imediatamente.

---

## 2026-08-20 — Sessão 4: Debug Docker — todos os containers Healthy

**Contexto:** `docker-compose up` falhava com containers unhealthy após healthchecks.

**Diagnóstico e correções:**
- Healthcheck Qdrant: `curl: not found` → imagem qdrant/qdrant não tem curl nem wget (imagem ultra-minimalista)
  - Correção: usar `bash -c 'exec 3<>/dev/tcp/localhost/6333'` (TCP nativo do bash, sem dependências)
  - Validado com `docker exec qdrant_test bash -c '...'` → `TCP OK`
- Healthcheck Backend/Frontend: `curl: not found` → python:3.11-slim também não tem curl
  - Correção: usar `python -c "import urllib.request..."` (Python sempre disponível)
- Backend crashava no boot: `ModuleNotFoundError: No module named 'app'`
  - Causa: `PYTHONPATH=/app` mas código usa `from app.xxx` (precisa de `/app/backend` no path)
  - Correção: `Dockerfile.backend` → `PYTHONPATH=/app/backend`, `CMD uvicorn app.main:app`
  - Removido `curl` da instalação apt (não era necessário)
- Containers antigos presos após falha: `docker rm -f` para limpeza forçada

**Resultado:** Stack completa rodando:
```
pu_matcher_qdrant    → healthy ✅
pu_matcher_backend   → healthy ✅  
pu_matcher_frontend  → healthy ✅
```
`GET /api/health` retorna `{"api": "online", "qdrant": "online", "collection": {"points_count": 0}}`

**Estado atual:** Ambiente Docker 100% funcional. Pronto para ingestão de dados reais (Fase 1).

**Próximos passos:**
1. Preencher `.env` com chaves de API reais (Gemini/OpenAI/Anthropic)
2. Colocar PDFs/DOCX de TDS em `data/raw_documents/` e rodar ingestão
3. Testar fluxo completo end-to-end no frontend em `http://localhost:8501`

**Bloqueios:** chaves de API ainda não fornecidas; acervo de TDS/catálogos ainda não disponibilizado.

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
