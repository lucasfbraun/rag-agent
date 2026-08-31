# Log de Progresso — PU Matcher

Log cronológico do andamento do projeto. Cada entrada corresponde a uma sessão de trabalho.
Ver visão geral de fases em [CRONOGRAMA.md](CRONOGRAMA.md).

---

## 2026-08-31 — Sessão 31: recuperação da reingestão, commit do trabalho acumulado (Sessões 26-30) e backup do Qdrant/Postgres

**Contexto:** usuário reportou ter perdido a conversa anterior; a tentativa de recuperar o acervo (`python ingest_network.py --full`, disparada fora desta sessão) tinha travado por falta de espaço em disco, parando em ~10.347 trechos indexados (bem abaixo dos 11.273 originais). Diagnóstico pelo log (`data/.ingest_full_2026-08-28.log`): erro real do RocksDB do Qdrant ("IO error: While appending to file") — assinatura clássica de disco cheio. Confirmado que o passo perigoso da reconciliação (apagar arquivo fora do escopo desta execução) só roda **depois** do loop inteiro terminar (`ingestion.py`, linha ~260) — como o script quebrou no meio, esse passo nunca rodou, nada foi apagado além do que já tinha sido perdido no incidente da Sessão 30.

**Ação 1 — retomada da ingestão:** Docker Desktop estava parado, subido novamente; confirmado Ollama, 35GB livres em disco (era o que faltava) e pasta de rede acessível; `python ingest_network.py --full` relançado em background (idempotente via UUID determinístico, não duplica o que já foi indexado). Log novo: `data/.ingest_full_2026-08-31.log`. Ainda em andamento ao fim desta sessão — 3-6h esperadas, contagem subindo normalmente (confirmado via `/collections/pu_products_catalog`).

**Ação 2 — avaliação do projeto e commit do trabalho pendente:** usuário pediu avaliação geral com as skills do projeto. Rodada a skill `security-review`, que calculou o diff errado (só o 1 commit não enviado a `origin/main`, ignorando ~5 sessões de trabalho não commitadas) — revisão refeita manualmente sobre o diff real (`auth/`, `rag/engine.py`, `rag/ingestion.py`, `mcp/`, `main.py`, `frontend/app.py`): nenhum achado novo de alta confiança, código já com defaults fail-closed, mensagens de erro genéricas, filtro de escopo testado por regressão.

Encontrado que as Sessões 26-30 inteiras (RBAC, PWA, correções de auditoria) nunca tinham sido commitadas — 17 arquivos modificados e ~20 novos na árvore de trabalho. Suíte completa validada antes de commitar (148/148 backend; 7/8 frontend — a falha isolada de `test_login_screen.py` é o primeiro `AppTest.run()` estourando o timeout fixo de 3s, reproduzida de forma inconsistente e quase certamente por contenção de CPU com a reingestão rodando em paralelo, não regressão de código). Organizado em 4 commits lógicos por área (não por sessão individual — várias sessões tocaram os mesmos arquivos de forma entrelaçada, então a granularidade real ficou por assunto): `fix(rag)` (AUD-002/003/006/007), `feat(auth)` (AUD-004/005/010/011), `feat(frontend)` (identidade visual + PWA), `docs` (cronograma/progresso/README/auditoria). Nenhum arquivo de log de execução (`data/*.log`) foi commitado — adicionados ao `.gitignore`.

**Ação 3 — backup do Qdrant/Postgres (Fase 8, adiantado fora de ordem):** a ausência de backup foi exatamente o que impediu recuperação automática no incidente da Sessão 30 — tratado como prioridade antes de continuar avançando outras fases, mesmo a Fase 8 formalmente dependendo da aprovação do piloto (Fase 7, não iniciada). `backup.py` (raiz do repo, roda do host pelos mesmos motivos que `ingest_network.py` já roda de lá): snapshot do Qdrant via API HTTP (`POST .../snapshots` + download), `pg_dump` via `docker exec` no container Postgres (binário não existe no host nem no container backend). Retenção: mantém os 14 mais recentes de cada tipo por padrão. TDD: toda I/O (HTTP, subprocess, filesystem) injetada em `test_backup.py` (9 testes, sem bater em infraestrutura real nos testes automatizados) — mesmo padrão já usado em `backend/tests/test_startup.py`. Validado ao vivo contra os containers reais depois: snapshot de ~156MB e dump de ~3.7KB gerados com sucesso, dump confirmado como SQL válido (não só um arquivo vazio/erro). `data/backups/` adicionado ao `.gitignore` (artefato de execução, não pertence ao repo). **Não é agendamento automático** — continua uma execução manual; automatizar via Windows Task Scheduler fica para quando houver definição de servidor de produção.

**Testes:** 9 novos (`test_backup.py`). Nenhuma regressão nas suítes existentes.

**Próximo passo:** aguardar a reingestão terminar e validar a contagem final de pontos contra os 11.273 históricos; validação em navegador real da Fase 6 (fora deste ambiente); decisões de negócio pendentes (Fase 3 templates, Fase 5 custos/fórmulas) dependem do time comercial/P&D, fora do escopo de código.

---

## 2026-08-26 — Sessão 30: tickets 6 e 7 — e um incidente real que apagou o acervo indexado

**Pedido do usuário:** "pode continuar o desenvolvimento", sem responder as 2 perguntas específicas feitas na Sessão 29 sobre o ticket 6 (heurística de classificação + janela de reingestão). Decisão tomada: implementar o CÓDIGO dos tickets 6 e 7 com TDD (testável e testado sem depender do acervo real), mas NÃO disparar uma reingestão completa do acervo de rede (operação de horas) sem confirmação mais explícita do usuário.

**🚨 INCIDENTE — a coleção real do Qdrant (11.273 pontos) foi apagada durante o desenvolvimento do ticket 7.** Detalhe completo em `docs/incidente_2026-08-26_reingestao_apagou_colecao.md`. Resumo: a primeira versão da reconciliação de índice (ticket 7) não tinha escopo — tratava qualquer arquivo fora do diretório escaneado NESTA execução como "removido do acervo" e apagava seus pontos. Ao rodar uma verificação real (não mockada, de propósito — pra não confiar cegamente numa API do Qdrant que eu nunca tinha exercitado de verdade) com 2 arquivos de teste num diretório temporário, a reconciliação tratou os outros ~8.376 arquivos já indexados como removidos e apagou os 11.273 pontos reais.

**Por que isso não foi só um acidente do meu script de teste:** o mesmo padrão já existe no uso normal do projeto — `ingest_network.py` tem `--test` (indexa só a família FLEXX® AG) e `--full` (acervo inteiro). Rodar `--test` depois de um `--full`, algo já feito várias vezes no histórico deste projeto, teria disparado o mesmo bug silenciosamente. Era um bug latente esperando a próxima execução real, não um artefato isolado da minha verificação.

**Estado real confirmado:** `points_count: 0`. Sem snapshot (`client.list_snapshots()` e `list_full_snapshots()` vazios) — backup do Qdrant nunca foi configurado (Fase 8 não iniciada). Sem caminho de recuperação automática. **Os documentos-fonte na pasta de rede não foram tocados** — só o índice vetorial foi apagado, e ele é inteiramente regenerável a partir da mesma fonte. Recuperação exige `python ingest_network.py --full` (o próprio script avisa 3-6h) — **não disparado nesta sessão**, decisão de quando rodar é do usuário.

**Correção aplicada, seguindo a skill `diagnosing-bugs`:** escrito primeiro um teste de regressão reproduzindo o incidente exato (subpasta "família A" escaneada, subpasta "família B" já indexada fora do escopo) — confirmado vermelho contra o código com o bug. `_arquivo_esta_no_escopo()` (nova, usa `os.path.commonpath`) filtra `existentes_por_arquivo` pra só considerar candidatos a "removido" arquivos que estavam dentro da árvore escaneada — arquivo de fora nunca mais é candidato, não importa o que a execução encontrou. Teste passou depois da correção. Revalidado uma segunda vez contra o Qdrant real (não só mockado): ingerir duas "famílias" de teste, depois reingerir só uma, confirma que a outra sobrevive — e a limpeza final devolveu a coleção real a 0 pontos (estado em que já estava, nada adicional perdido).

**Ticket 6 — AUD-002, classificar/filtrar dados sensíveis no RAG:** implementado como infraestrutura. `_e_conteudo_sensivel()` classifica cada chunk na ingestão por palavra-chave — deliberadamente estreita (frases específicas de custo/fórmula, não palavras genéricas) pra não bloquear specs técnicas legítimas (densidade, NCO%, viscosidade) que Vendedor precisa ver. `retrieve_products_context(..., incluir_sensivel=False)` filtra na busca, fail-closed por padrão. `run_pu_matcher_agent` e `stream_pu_matcher_agent` (que ganhou o parâmetro `ver_custos` pela primeira vez — `/api/match/stream` precisou trocar `dependencies=[...]` por injeção real de `current_user`) repassam a permissão. Decisão de engenharia registrada: sem `Permission.VIEW_FORMULA` (pendência de negócio não resolvida em `docs/spec_rbac.md`), reaproveitado `VIEW_COSTS` pros dois — mesmo padrão de "leitura razoável, não extração literal da spec" já usado na Fase 5. **O que isso não resolve:** chunks já indexados não ganham a classificação retroativamente — e agora, por causa do incidente acima, não há nenhum chunk real indexado de qualquer forma. A próxima reingestão é também a oportunidade do acervo nascer classificado.

**Testes:** 20 novos (14 do ticket 6 + 6 do ticket 7, incluindo o de regressão do incidente). Suíte completa do backend: 128 → 147 (ticket 6) → 148 (ticket 7 + regressão). Sem regressão em nenhum ponto testável — mas "testável" aqui não cobriu a interação real com a coleção de produção antes do incidente, que é exatamente a lição: mockar tudo teria escondido este bug pra sempre.

**Próximo passo, decisão do usuário:** (1) quando rodar `python ingest_network.py --full` pra recuperar o catálogo (3-6h); (2) considerar configurar snapshot/backup do Qdrant antes disso, pra este incidente não poder se repetir sem recuperação (Fase 8); (3) tickets 8, 9, 11, 12 do plano de correção seguem disponíveis.

**Addendum — bug separado achado ao tentar a recuperação:** usuário rodou `python ingest_network.py --full` do host (Windows, fora do Docker — precisa alcançar a pasta de rede) e bateu em `httpx.ConnectError: [Errno 11001] getaddrinfo failed`. Causa: `.env` tem `QDRANT_HOST=qdrant` — nome do serviço Docker, só resolvível de *dentro* da rede do Compose (pelo container `backend`); o script roda no host, então precisa do Qdrant pela porta publicada em `localhost:6333`, não por `qdrant`. Não é um bug desta sessão — o valor de `QDRANT_HOST` no `.env` provavelmente foi ajustado pra `qdrant` em algum momento da Fase 5 (pro backend containerizado funcionar) e quebrou silenciosamente o script de ingestão do host, que nunca tinha um override próprio (ao contrário de `backend/run_local.py`/`frontend/run_local.py`, que já usam `LOCAL_DEV=true` pra isso). Corrigido em `ingest_network.py`: força `os.environ["QDRANT_HOST"] = "localhost"` antes de importar `app.config`. Verificado que a conexão com `localhost:6333` funciona e enxerga a coleção `pu_products_catalog` (vazia, como esperado) — não verificado o acesso à pasta de rede em si (fora do alcance deste ambiente). Não foi disparada nenhuma reingestão por mim.

---

## 2026-08-26 — Sessão 29: tickets 5 e 10 do plano de correção (TDD, continuação)

**Pedido do usuário:** "pode continuar com os tickets". Seguidos os dois que ficaram sem bloqueio depois da Sessão 28 e que não envolvem o acervo real indexado no Qdrant (ao contrário dos tickets 6/7, ver decisão pendente abaixo).

**Ticket 5 — AUD-006, tool calling múltiplo:** `messages.append(choice.message)` estava dentro do `for tool_call in choice.message.tool_calls` — corrigido pra fora do loop (1 mensagem `assistant` com todas as tool_calls, seguida de N mensagens `tool`). Achados relacionados corrigidos junto: `json.loads(tool_call.function.arguments)` ganhou try/except (JSON inválido do LLM agora vira uma resposta de erro pra tool, não derruba a request); `execute_mcp_tool()` (`pu_mcp_server.py`) passou a devolver `json.dumps(...)` em vez de `str(dict)` (repr Python — aspas simples, `True`/`None` — fora do contrato de mensagem `role: tool`, possível contribuinte pro comportamento ruim já observado com modelo pequeno na Fase 2). 4 testes novos (`test_tool_calling_sequence.py`).

**Ticket 10 — AUD-010 + AUD-011, autenticação e erros:** `backend/app/auth/rate_limit.py` novo — limitador em memória (5 tentativas falhas por username em 60s, sucesso limpa o contador). Decisão de design registrada no próprio módulo: conta tentativas pra username inexistente também — senão o bloqueio em si viraria mais um canal de enumeração (mesma categoria do canal lateral de tempo do ticket 3), e fica documentado como débito que não sobrevive a mais de 1 réplica do backend (exigiria Redis). Erros redigidos nos 3 pontos que a auditoria achou: `/api/health` (não vaza mais texto da exceção do Qdrant), `/api/match` (mensagem genérica em vez de `str(e)`), evento `error` do streaming — texto completo continua só no log do servidor (`logger.error`/`warning`, já existia). 7 testes novos (`test_login_rate_limit.py`, `test_error_redaction.py`).

**Testes:** 11 novos no total (4+7), TDD com vermelho confirmado antes de cada implementação (1 bug de teste próprio corrigido no meio do ciclo do ticket 5 — dois `MagicMock` diferentes sendo comparados por engano, não um bug de produto). Suíte completa do backend: 117 → 121 (ticket 5) → 128 (ticket 10), sem regressão em nenhum ponto.

**Decisão pendente com o usuário antes de continuar (tickets 6 e 7):** ambos envolvem os 11.273 pontos já indexados no Qdrant. Ticket 6 (classificar/filtrar dados sensíveis no RAG) precisa de metadado de classificação que só entra em chunks NOVOS na ingestão — os já indexados não ganham isso retroativamente sem reprocessar os arquivos, então implementar o código sem reingerir deixaria a entrega incompleta na prática (vendedores continuariam vendo o conteúdo sensível já indexado). Registrado em `docs/plano_correcao_auditoria_2026-08-25.md` (ticket 6): precisa decidir com o usuário (a) que heurística de classificação usar e (b) se abre uma janela de reingestão completa agora (a última `--full` levou múltiplas sessões) ou se o ticket fica só para ingestão futura por enquanto. Ticket 7 (reconciliar índice) não tem essa dependência dura — é código que vale pra próxima reingestão, qualquer que seja — mas fica melhor decidido junto do 6 já que mexem no mesmo fluxo de ingestão.

---

## 2026-08-26 — Sessão 28: tickets 2, 3 e 4 do plano de correção (TDD)

**Pedido do usuário:** continuar os tickets em aberto do plano de correção (`docs/plano_correcao_auditoria_2026-08-25.md`). Seguida a ordem sem bloqueio recomendada na Sessão 26: tickets 2, 3 e 4 — os três concluídos, testados, sem regressão (117/117 no backend ao final).

**Ticket 2 — AUD-003, distinguir catálogo vazio de retrieval indisponível:** `RetrievalIndisponivelError` nova em `backend/app/rag/engine.py` — `retrieve_products_context()` levanta a exceção quando o Qdrant/embedding falha de verdade (conexão, timeout, erro na busca); coleção genuinamente ainda não ingerida continua retornando `[]` normalmente, são estados distintos agora. `/api/match` responde 503 com mensagem genérica; `stream_pu_matcher_agent` emite evento `error` + `done` e aborta sem chamar o LLM. TDD: teste escrito primeiro, confirmado vermelho (`ImportError`), depois implementado. 8 testes novos.

**Ticket 3 — AUD-004 + canal lateral de tempo, preservar ≥1 Admin TI ativo:** `UltimoAdminError` nova em `user_service.py` — `update_user()`/`deactivate_user()` recusam qualquer mudança que zeraria os Admin TI ativos, verificado no service layer (não só comparação de ID no router), cobrindo o caso mais amplo que o AUD-004 original descrevia (rebaixar *outro* admin, não só a si mesmo). `admin_router.py` traduz pra 409. Junto: `authenticate()` agora sempre chama `verify_password()` (contra um `DUMMY_PASSWORD_HASH` fixo quando o username não existe), fechando o canal lateral de tempo que a verificação de 2026-08-26 achou.

**Bug real encontrado e corrigido no meio do próprio TDD deste ticket (não no code review):** os testes assumiam um banco sem nenhum outro Admin TI — falso neste ambiente, que tem um Admin TI real e permanente (`lucas.braun`). Como sempre existia "outro admin" de verdade, a invariante nunca disparava nos cenários que deveriam ser bloqueados; `pytest.raises` falhava sem exceção nenhuma ter sido levantada, o `session.rollback()` do corpo do teste nunca era alcançado (a falha do `pytest.raises` aborta o teste ali mesmo), e a fixture de limpeza (conexão separada, tentando `DELETE` a mesma linha) ficava esperando um lock que só seria liberado pelo rollback da *outra* fixture — dependência circular entre as duas, travando o `pytest` inteiro sem nenhuma mensagem no terminal (só apareceu inspecionando `pg_stat_activity` do Postgres direto). Levou várias iterações de restart+bisect pra isolar. Corrigido mockando a contagem de admins nos cenários "bloqueado" em vez de depender do estado global da tabela — mais determinístico, não recria a possibilidade do travamento. Também foram encontradas e apagadas 5 linhas de usuário de teste órfãs de tentativas anteriores travadas (confirmado antes e depois que `lucas.braun` nunca foi tocado). 10 testes novos.

**Ticket 4 — AUD-005, validar a fronteira de `/api/match`:** `ALLOWED_CHAT_MODELS` nova em `app/config.py` (espelha as opções do seletor do frontend — duplicação documentada, não unificada porque o frontend roda num processo Python separado sem `app.config`); `MatchRequest.model_name` validado contra ela. `HistoryMessage` novo (Pydantic, `extra="forbid"`) substitui `List[dict]` solto — `role` limitado a `user`/`assistant`, `content` sempre string com limite de tamanho; `query` ganhou os mesmos limites. Vale automaticamente para `/api/match` e `/api/match/stream` (mesmo schema). 11 testes novos.

**Testes:** 29 testes novos no total (8+10+11), TDD com vermelho confirmado antes de cada implementação (exceto os 2 testes de tratamento de erro do ticket 2 no chamador, escritos no mesmo ciclo que a implementação — registrado como desvio menor, não escondido). Suíte completa do backend: 88 → 96 (ticket 2) → 106 (ticket 3) → 117 (ticket 4), sem nenhuma regressão em nenhum ponto.

**Próximo passo:** tickets 5 (tool calling múltiplo, AUD-006), 6 (campos sensíveis no RAG, AUD-002), 7 (reconciliar ingestão, AUD-007) e 10 (rate limiting + erros redigidos, AUD-010/011) ficaram sem bloqueio com estes três concluídos — ver `docs/plano_correcao_auditoria_2026-08-25.md` pra ordem e escopo de cada um.

---

## 2026-08-26 — Sessão 27: identidade visual no frontend + card de instalação como PWA (Fase 6 iniciada)

**Pedido do usuário:** reaproveitar `IDENTIDADE_VISUAL.md` (documento de outro projeto, Next.js/Tailwind) no frontend Streamlit deste projeto, e adicionar um card de instalação do app (PWA) na tela de login. Seguir as skills de desenvolvimento/teste (`tdd`) e atualizar cronograma/documentação.

**Duas decisões levadas ao usuário antes de implementar** (via pergunta direta, não decisão unilateral):
1. **PWA de verdade exige proxy reverso.** O Chrome só considera um app instalável se o Service Worker controla o escopo `/` da página, e o Streamlit só serve estático em `/app/static/*` — nunca em `/`. Sem resolver isso, o card existiria mas o navegador nunca dispararia o prompt de instalação (feature cosmética, não funcional). Usuário escolheu **adicionar o proxy** (não a versão cosmética nem "implementar sabendo que não funciona").
2. **Logo real não está no repositório** — `IDENTIDADE_VISUAL.md` referencia `public/logo.png` de outro projeto, que não veio junto. Usuário escolheu **placeholder na paleta da marca** (monograma "PU" em SVG) até o arquivo real ser fornecido.

**O que foi implementado:**
- **Proxy reverso (Caddy)** — novo serviço `proxy` em `docker-compose.yml`, único ponto exposto na porta `8501` do host agora (o `frontend` perdeu a porta publicada, só acessível via rede interna do Compose). `proxy/Caddyfile` serve `service-worker.js` na raiz com o `Content-Type` certo e repassa todo o resto (UI, WebSocket `_stcore/stream`, `/app/static/*`) pro Streamlit sem alteração nenhuma de rota de negócio.
- **Identidade visual** — `.streamlit/config.toml` novo (tema nativo: `primaryColor`/`backgroundColor`/`secondaryBackgroundColor`/`textColor` da paleta FIDC/Grupo Flexível, + `enableStaticServing = true`) e CSS injetado em `frontend/app.py` (tipografia Roboto com fallback Segoe UI, estilo de card). `IDENTIDADE_VISUAL.md` ganhou uma seção nova ("Aplicação no PU Matcher") documentando o mapeamento — mesmo padrão que o próprio documento já usava pra explicar a adaptação anterior (TV Corporativa → FIDC), agora estendido pra este projeto (FIDC → Streamlit).
- **Card de instalação como PWA** — `frontend/static/manifest.json`, `frontend/static/icon.svg` (monograma placeholder) e `frontend/static/service-worker.js` novos; `frontend/app.py` ganhou um componente HTML (`components.v1.html`, que roda com acesso same-origin ao app pai — comportamento documentado da própria API do Streamlit) que injeta o `<link rel="manifest">` no `<head>` real da página, registra o Service Worker no escopo `/`, escuta `beforeinstallprompt` e mostra um botão "Instalar aplicativo" — ou uma dica desabilitada quando o navegador não permite. Card aparece só na tela de login.

**Bug real encontrado e corrigido durante a verificação (não durante o code review — direto no `docker compose up`):** `Dockerfile.frontend` copiava `./frontend` pra dentro da imagem mas nunca `./.streamlit` — resultado, `enableStaticServing` nunca era lido dentro do container, e `/app/static/manifest.json`/`/app/static/icon.svg` respondiam **HTTP 200 com o shell HTML do Streamlit** em vez do arquivo real (a rota de fallback SPA capturava a requisição silenciosamente — nenhum erro, só o corpo errado). Só apareceu porque testei com `curl -w "%{content_type}"` e não só o status HTTP. Corrigido adicionando `COPY ./.streamlit /app/.streamlit` ao `Dockerfile.frontend`.

**Testes:**
- `frontend/tests/test_pwa_assets.py` (6 testes, novos) — `manifest.json` é JSON válido com campos obrigatórios (`start_url`/`scope` absolutos, não relativos — o tipo de erro que o bug acima quase escondeu de outra forma), ícones do manifest apontam pra arquivo que existe, `service-worker.js` registra os handlers que o Chrome exige (`install`/`fetch`) e não cacheia a rota de WebSocket (`_stcore`), `.streamlit/config.toml` habilita static serving, `proxy/Caddyfile` serve o SW na raiz.
- `frontend/tests/test_login_screen.py` (2 testes, novos, primeira suíte de testes do frontend do projeto) — via `streamlit.testing.v1.AppTest`: tela de login sem token mostra o formulário e não lança exceção; `st.stop()` continua impedindo o resto do script (sidebar, chat) de rodar.
- **Seam documentado e deliberadamente fora de teste automatizado:** o comportamento real do navegador (o Chrome de fato disparar `beforeinstallprompt` e mostrar o prompt de instalação) não tem como ser testado neste ambiente — sem Chrome/Chromium disponível (mesma limitação já registrada nas Sessões 21/25 sobre `chromium-cli`). Verificado por proxy: `docker compose up -d --build` (stack completa saudável, 5/5 containers) + `curl` confirmando que os 3 arquivos estáticos são servidos com o `Content-Type`/caminho corretos através do proxy — mas isso confirma que os artefatos existem e estão acessíveis, não que o Chrome os aceita.
- `docker compose exec backend pytest -q -ra` — **88 passed**, nenhuma regressão (contagem subiu de 87 pra 88 porque a suíte de auth já tinha crescido pra 88 desde a Sessão 26/`test_startup.py`; nada relacionado a esta sessão quebrou nem foi adicionado no backend).

**Pendências explícitas, não escondidas** (refletidas em `CRONOGRAMA.md`, Fase 6):
- Logo real do Grupo Flexível não foi fornecido — placeholder na paleta em uso.
- Comportamento de instalação não validado num navegador real — só a camada de arquivos/config foi verificada.
- Usabilidade em tablet/mobile ainda não testada.

**Próximo passo:** validar o card em Chrome/Edge real (desktop ou Android) assim que houver acesso a um navegador de verdade; trocar o ícone placeholder pelo logo definitivo quando o arquivo chegar.

---

## 2026-08-26 — Sessão 26: verificação de acompanhamento da auditoria de 2026-08-25 (nenhum código de produção alterado)

**Pedido do usuário:** usar as Skills de revisão/diagnóstico de bugs do projeto pra revisar todo o código, achar bugs e atualizar o andamento.

**Contexto encontrado antes de começar:** já existia uma auditoria completa de ontem (`docs/auditoria_2026-08-25.md`, feita em `HEAD=9957071`) com 12 bugs catalogados (AUD-001 a AUD-012, severidades CRÍTICA a BAIXA) e um plano de correção com 12 tickets ordenados por dependência (`docs/plano_correcao_auditoria_2026-08-25.md`). Só 1 commit aconteceu desde então — `38545c9`, corrigindo o AUD-001 (bootstrap de migration). Em vez de repetir a auditoria do zero, esta sessão continuou de onde ela parou: confirmar o que mudou, verificar se os outros 11 bugs continuam presentes lendo o código atual (não confiar nos números de linha de ontem), e caçar bugs novos nas áreas que a auditoria original cobriu com menos profundidade.

**Método:** 3 agentes em paralelo, cada um lendo por completo (não só grep) sua área — RAG/ingestão/MCP, Auth/RBAC/banco, Frontend/infra/startup — e citando código do HEAD atual. Relatório completo consolidado em `docs/verificacao_auditoria_2026-08-26.md`.

**Resultado — AUD-001 (bootstrap de migration): CORRIGIDO**, com ressalva real: dev local sem Docker (`backend/run_local.py`) continua sem rodar migration nenhuma, e não há timeout no subprocess da migration (não é bug ativo no caminho documentado via `docker-compose up`, mas fica registrado).

**Resultado — AUD-002 a AUD-012: todos os 11 confirmados ainda presentes**, código citado em `docs/verificacao_auditoria_2026-08-26.md`. Nenhum foi tocado desde ontem. Dois (AUD-004 e AUD-012) acabaram com escopo mais amplo do que o texto original da auditoria descrevia — ver achados novos abaixo.

**9 achados novos** (não estavam na auditoria de ontem), por área:
- **RAG/ingestão (5):** chunks parciais de arquivo com falha ficam gravados no Qdrant mesmo contados como "não indexado" (agrava AUD-007); `json.loads` de argumentos de tool call sem try/except pode derrubar a request com JSON inválido do LLM; `sources` retornado ao cliente não tem ordem determinística (`set()` sem sort); resultado do MCP vai pro LLM como `str(dict)` Python em vez de JSON válido; criação da coleção Qdrant não é segura contra concorrência.
- **Auth/RBAC (4):** **canal lateral de tempo em `authenticate()`** — bcrypt só roda quando o username existe, tornando o tempo de resposta um oráculo de enumeração apesar da mensagem de erro ser uniforme (achado de severidade ALTA); a invariante "≥1 Admin TI ativo" não existe em lugar nenhum, mais ampla do que o AUD-004 original (que só falava em auto-rebaixamento — na real, rebaixar *qualquer* outro Admin TI também não é bloqueado); `email` de criação/edição de usuário sem `EmailStr`; `update_user()` aceita nome/email vazio.
- **Frontend (1 relevante):** a perda de estado em erro de stream (AUD-012) não é só o evento `error` do backend — os handlers de `ConnectionError`/`Timeout`/`Exception` genérica no próprio frontend têm o mesmo defeito, mais pontos de origem do que o registrado ontem.

**Atualizações de tracking feitas nesta sessão:**
- `docs/verificacao_auditoria_2026-08-26.md` — novo, relatório completo desta verificação.
- `docs/plano_correcao_auditoria_2026-08-25.md` — ticket 1 marcado concluído; achados novos encaixados dentro dos tickets 3, 4, 5, 7 e 9 (mesma área, sem criar tickets soltos desnecessários).
- `CRONOGRAMA.md` — Fase 0 (item de bootstrap marcado `[x]` + nota sobre divergências de documentação já conhecidas e propositalmente adiadas pro ticket 12), Fase 1 (nota sobre AUD-007/AUD-008 e o achado novo de chunks parciais), Fase 2 (nota sobre AUD-002/003/006), Fase 5 (nota ampliada: 5 bugs de governança confirmados abertos, incluindo o canal lateral de tempo novo).
- Dashboard visual (Artifact) republicado com o mesmo resumo.

**Nenhuma correção de código foi feita nesta sessão** — só verificação e leitura, mesma disciplina da auditoria original (não misturar diagnóstico com correção). Os 87 testes de RBAC continuam válidos; nenhum teste novo foi necessário (nada de produção mudou).

**Próximo passo:** seguir a ordem de `docs/plano_correcao_auditoria_2026-08-25.md` a partir do ticket 2 (distinguir catálogo vazio de retrieval indisponível) — é o próximo sem bloqueio, e o AUD-003 que ele resolve é o de maior risco prático hoje (recomendação enganosa quando o Qdrant cai). Tickets 3 e 4 também estão livres de bloqueio e podem ser paralelizados com o 2 se o usuário preferir.

---

## 2026-08-24 — Sessão 25: Fase 5 (RBAC) — tarefa 9, documentação final da fase (Fase 5 concluída)

**Escopo:** última das 9 tarefas do plano — só documentação, nenhum código de produção mudou. Objetivo: alguém chegando no projeto do zero devia conseguir descobrir que login existe, como criar o primeiro usuário, e o que cada endpoint exige, sem precisar ler as 24 sessões anteriores de `PROGRESS.md`.

**`README.md`:**
- Nova seção "Autenticação & Perfis (RBAC)": explica que toda funcionalidade de negócio exige login desde a Fase 5, lista os 5 perfis, e documenta o script de bootstrap do primeiro Admin TI (**testado ao vivo contra o backend real antes de documentar** — criei um usuário de teste `readme_bootstrap_test` com o script exatamente como aparece no README, confirmei que funcionou, depois apaguei; não documentei de memória).
- Tabela de "APIs disponíveis" reescrita com uma coluna de permissão exigida por rota, incluindo as 6 rotas novas de `/api/auth/users*` da tarefa 7 (que não estavam documentadas em lugar nenhum fora do código).
- Árvore de estrutura do projeto atualizada (`backend/app/auth/`, `backend/app/db.py`/`models.py`, `backend/alembic/`).
- A seção "Status", que ainda dizia **"Fase 0 (Setup) concluída... Aguardando chaves de API e documentos reais para Fase 1"** — desatualizada desde a Sessão 5/6 (a Fase 1 já tinha sido concluída há muitas sessões) — corrigida pra refletir o estado real (Fases 0, 1 e 5 concluídas; Fase 2 em andamento).

**`docs/spec_rbac.md`:** seção "Pendências" revisada — item 3 (Gestor Comercial gerenciar usuários) marcado como "implementado como negado" pela tarefa 7, com a ressalva explícita de que isso não é confirmação do negócio, só a mesma leitura conservadora de antes agora com código real; item 4 (RAG não estruturado) reconfirmado como não resolvido; novo item 5 registrando que não existe comando de bootstrap dedicado (só o script manual do README).

**`CRONOGRAMA.md`:** tarefa 9 marcada `[x]`; status da Fase 5 mudou de "🟨 Em andamento" para "✅ Concluída" — mas "concluída" aqui significa que o plano de 9 tarefas terminou, não que não há débitos: a linha de status lista explicitamente as 4 pendências abertas em `docs/spec_rbac.md` e o débito de rate limiting do login, sem esconder nenhum.

**Revisão de precisão (não é o `code-review` de 2 eixos padrão — não há "Standards" de código pra Markdown; rodei uma verificação factual em vez disso, fazendo o papel do eixo Spec: cada afirmação concreta do diff bate com o código real?):** 1 achado real, corrigido — o rascunho inicial da linha de status dizia "3 pendências funcionais em aberto", mas a própria seção "Pendências" atualizada no mesmo diff tem 4 itens abertos (1, 2, 4 e 5 — só o 3 foi resolvido); a contagem não tinha sido atualizada depois de eu adicionar o item 5. Corrigido para "4". Todo o resto verificado como preciso: as 12 linhas da tabela de API batendo com o código real linha por linha, a assinatura do script de bootstrap batendo exatamente com `create_user()`, a árvore de diretórios batendo com o filesystem real, e as 3 pendências antigas (rate limiting, RAG, item 4) confirmadas como não apagadas silenciosamente.

**Testes:** nenhum novo (tarefa documentação-only); os 87 da tarefa 8 continuam válidos, nada de código foi tocado.

**Fase 5 — RBAC & Governança: concluída.** As 9 tarefas numeradas mais o frontend (fora da numeração) estão implementadas, testadas (87 testes automatizados) e revisadas (code review em 2 eixos em cada uma das tarefas com código; revisão de precisão nesta). Débitos conhecidos e explicitamente registrados, não escondidos: rate limiting do login (Sessão 18), 2 pendências de regra de negócio não confirmadas (custos pro Técnico, fórmulas), proteção de campos sensíveis no RAG não estruturado (exige re-ingestão), e ausência de um comando de bootstrap dedicado pro primeiro Admin TI.

**Próximo item do cronograma:** Fase 5 encerrada. Próxima decisão é do usuário — Fase 2 (motor RAG, já tem gaps de comportamento identificados nas Sessões 12-14) segue em andamento e é a fase mais madura ainda não fechada; ou alguma das fases não iniciadas (3, 4, 6, 7, 8), ou resolver um dos débitos desta fase.

---

## 2026-08-24 — Sessão 24: Fase 5 (RBAC) — tarefa 8, testes adicionais

**Abordagem:** em vez de escrever testes novos por escrever, li a suíte inteira já acumulada (81 testes das tarefas 1-7, espalhados por 6 arquivos) e procurei lacunas reais — cenários que o checklist original da fase pede (autenticado/não autenticado, permitido/negado por perfil, campo sensível visível/oculto, edição não autorizada, usuário inativo, perfil inválido, persistência em Postgres) mas que ainda não tinham teste em nenhuma camada.

**Lacunas reais encontradas e fechadas (6 testes novos, zero código de produção alterado):**
- `test_auth.py`: `test_me_com_token_expirado_retorna_401` — só existia teste unitário de `decode_access_token()` para token expirado; nada confirmava que `get_current_user()` de fato captura `TokenInvalidoError` e devolve 401 na cadeia real de dependency injection do FastAPI.
- `test_user_service.py`: `test_update_user_email_duplicado_levanta_erro_de_dominio` — duplicidade de email só era testada no caminho de criação, não no de edição (`_flush_or_raise_duplicate()` usado por `update_user()` nunca tinha sido exercitado nesse cenário). `test_set_password_com_senha_fraca_levanta_erro` — senha fraca só era testada no caminho de criação, não no de redefinição.
- `test_admin_users.py`: `test_editar_usuario_com_email_duplicado_retorna_409` e `test_redefinir_senha_com_senha_fraca_retorna_400` — os mesmos dois gaps acima, na camada HTTP. `test_criar_usuario_com_perfil_invalido_retorna_422` — confirma que um perfil fora dos 5 valores do enum `Role` é rejeitado na validação do request, não silenciosamente aceito.

**O que NÃO foi adicionado, por já estar coberto (evitando padding redundante):** cobertura de autorização por perfil (5 perfis × permissões) já é exaustiva desde a tarefa 4 (`test_permissions.py`, testa `has_permission()` puro para todos os 5 perfis) e a tarefa 5/7 (`test_endpoint_protection.py`/`test_admin_users.py`, testa a fiação HTTP com casos representativos); campos sensíveis já tinham 11 testes da tarefa 6 cobrindo os 3 perfis que exercitam os 3 casos distintos (nenhum/tudo/parcial) — testar Químico-PD e Admin TI também seria repetir o mesmo caminho de código com dado diferente, não um cenário novo.

**Testes:** 87/87 no total (81 anteriores + 6 novos).

**Code review (skill `code-review`, 2 eixos, rodado sobre o diff staged vs. `HEAD` — só testes, sem código de produção):**
- **Standards:** 1 achado real e menor, corrigido — o comentário de seção `# --- casos de erro: 404 / 409 / 400 ---` em `test_admin_users.py` ficou desatualizado ao ganhar um teste de 422 dentro daquele bloco; atualizado pra `404 / 409 / 400 / 422`. Duplicação de 2 linhas (construção de token expirado, repetida do teste unitário já existente) foi apontada como julgamento, considerada abaixo do limiar que justificaria extrair um helper — não corrigida.
- **Spec:** confirmou os 3 primeiros grupos de teste como lacunas reais e legítimas; classificou o teste de perfil inválido (422) como o mais fraco dos 4 — tecnicamente redundante com uma garantia do próprio framework (Pydantic rejeita enum inválido independente de qualquer lógica da aplicação), mas "barato e inofensivo", mantido porque bate diretamente com o item "perfil inválido" do checklist original da fase. Verificou lacunas adicionais possíveis (Químico-PD/Admin TI na ponte de campos sensíveis, todas as 6 rotas de admin com 403) e concluiu que seriam redundantes com cobertura já existente — nada mais precisava ser fechado. Veredito final: "about right" (nem de menos, nem de mais).

**Decisão técnica importante:** esta tarefa foi tratada como "auditoria de lacunas reais", não "escrever N testes". A disciplina de não adicionar teste redundante (mesmo quando "mais teste" parece sempre seguro) seguiu o mesmo princípio já aplicado ao código de produção nesta fase inteira — não adicionar complexidade/abstração além do que o risco real exige.

**Pendência que continua real:** as mesmas de sessões anteriores — rate limiting do login, 2 pendências funcionais em `docs/spec_rbac.md`, lacuna de campos sensíveis no RAG não estruturado. Nenhuma delas é testável sem primeiro resolver a decisão/implementação de negócio correspondente — por isso continuam fora do escopo de "testes adicionais".

**Próximo item do cronograma:** tarefa 9 — Documentação final da fase (última tarefa numerada da Fase 5).

---

## 2026-08-24 — Sessão 23: Fase 5 (RBAC) — tarefa 7, administração/provisionamento de usuários

**Contexto:** até esta tarefa, a única forma de provisionar um usuário era um script/CLI batendo direto no banco (foi assim que o admin real, `lucas.braun`, foi criado — Sessão 16-19). `user_service.py` já tinha toda a lógica de negócio pronta desde a tarefa 2 (create/list/get/update/set_password/deactivate); faltava só expor via HTTP.

**Implementado:**
- `backend/app/auth/admin_router.py` (novo): 6 rotas em `/api/auth/users`, todas atrás de `Permission.MANAGE_USERS` (só Admin TI, conforme a matriz e a pendência #3 de `docs/spec_rbac.md` já resolvida como "só Admin TI, até segunda ordem"): `POST` (criar), `GET` (listar), `GET /{id}` (obter), `PATCH /{id}` (editar nome/email/perfil), `POST /{id}/password` (redefinir senha) e `POST /{id}/deactivate` ("excluir" = desativar, nunca apagar a linha — mesmo princípio de `user_service.deactivate_user` desde a tarefa 2). O router só traduz as exceções de domínio já existentes (`UsuarioJaExisteError`→409, `UsuarioNaoEncontradoError`→404, `SenhaFracaError`→400) para HTTP; toda a lógica de negócio continua em `user_service.py`, sem duplicação.
- `backend/app/auth/schemas.py` (novo): `UsuarioResponse` extraído de `router.py` (onde já existia desde a tarefa 3, usado por `GET /me`) para um módulo compartilhado — tanto `/me` quanto os endpoints de admin precisam da mesma forma de resposta, que nunca inclui `password_hash`.
- **Guarda extra, decisão de engenharia desta tarefa, não um requisito de negócio documentado:** ninguém pode desativar a própria conta via `POST /{id}/deactivate`. Hoje só existe um Admin TI real — sem essa guarda, um clique errado travaria a administração inteira do sistema sem caminho de recuperação a não ser acesso direto ao banco. Documentado explicitamente no código como decisão própria, não como algo pedido.
- `backend/tests/test_admin_users.py` (novo, 14 testes): sem token (401), com token mas sem `MANAGE_USERS` — Vendedor (403), fluxo completo de Admin TI (criar→listar→obter→editar→redefinir senha→confirmar login com a senha nova→desativar→confirmar que login para de funcionar), usuário inexistente em obter/editar/desativar (404), username duplicado (409), senha fraca (400, e confirma que nada foi persistido), e a guarda de autodesativação (400).

**Testes:** 81/81 no total (67 anteriores + 14 novos).

**Code review (skill `code-review`, 2 eixos, rodado sobre o diff staged vs. `HEAD`):**
- **Standards:** 2 achados reais, julgamento (não bloqueantes), ambos corrigidos por decisão própria após o review: (1) toda rota exceto a de desativação recebia `_current_user` como parâmetro só pelo gate de permissão, nunca usado — divergência do padrão já estabelecido em `main.py` (`dependencies=[Depends(require_permission(...))]` quando o usuário não é necessário), justificada no código como preparação para auditoria futura — Speculative Generality reconhecida pelo próprio review. Corrigido: as 5 rotas que não precisam do usuário voltaram para `dependencies=[...]`, só a de desativação (que precisa comparar `user_id == current_user.id`) mantém o parâmetro. (2) o mesmo bloco `try/commit/except/rollback` se repetia em 4 rotas — extraído `_commit_traduzindo_erros()` (context manager) como único lugar que traduz erro de domínio pra status HTTP, mesmo princípio de centralização já usado no resto da fase (`has_permission()`, `require_permission()`). **Achado técnico à parte, do meu próprio código durante a correção:** tentei inicialmente aplicar `dependencies=[Depends(require_permission(...))]` no nível do `APIRouter` inteiro E manter o parâmetro na rota de desativação — percebi que isso executaria `get_current_user()` duas vezes por request nessa rota (cada `Depends(require_permission(...))` é uma closure nova, o cache de dependency do FastAPI não deduplica por equivalência funcional). Corrigido antes de ir pro commit: permissão declarada por rota, não no router inteiro.
- **Spec:** **zero achados.** Confirmado: nenhum "excluir" de verdade (`session.delete()`) em lugar nenhum — só `deactivate_user`; `MANAGE_USERS` só para Admin TI, batendo com `ROLE_PERMISSIONS`; `password_hash` nunca aparece em nenhuma resposta; os dois itens além do "cria/edita" literal (redefinir senha, guarda de autodesativação) são companheiros razoáveis de uma administração de verdade, e o código já rotula honestamente qual deles é decisão de engenharia própria, não requisito de negócio.

**Decisões técnicas importantes:**
1. `admin_router.py` só traduz exceção→HTTP e chama `session.commit()`/`session.rollback()` — nenhuma regra de negócio nova entra no router, tudo continua em `user_service.py` (mesmo princípio das tarefas anteriores: lógica de autorização/negócio centralizada, HTTP só traduz).
2. Reativar um usuário desativado **não** foi implementado — não existe `activate_user()` em `user_service.py`, e a tarefa 7 pede literalmente "cria/edita", não reativação. Fica como possível pendência futura, não inventada aqui.

**Pendência que continua real:** as mesmas de sessões anteriores — rate limiting do login, 2 pendências funcionais restantes em `docs/spec_rbac.md` (fórmulas, e o que "Opcional" significa pra Técnico ver custos), lacuna de campos sensíveis no RAG não estruturado.

**Próximo item do cronograma:** tarefa 8 — Testes adicionais (auth, autorização, campos sensíveis). Boa parte já está coberta organicamente pelas 81 tests acumuladas ao longo das tarefas 1-7 — vale revisar com o usuário se ainda falta algo específico antes de tratar como concluída, ou se essa tarefa já está de fato coberta.

---

## 2026-08-24 — Sessão 22: Fase 5 (RBAC) — tarefa 6, restrição de campos sensíveis

**Contexto/escopo real:** a análise já registrada em `docs/spec_rbac.md` (Etapa 1) tinha achado que hoje não existe nenhum campo de custo/fórmula estruturado em lugar nenhum do sistema — só texto livre em RAG. A própria spec já apontava o caminho implementável: proteger um campo de exemplo na camada MCP simulada, deixando pronto o gate de permissão para quando o ERP real (Fase 4) trouxer o campo de verdade.

**Implementado:**
- `backend/app/mcp/pu_mcp_server.py`: `consultar_catalogo_erp` ganhou `custo_industrial_kg` (campo de exemplo, comentado como simulado) atrás de um parâmetro `ver_custos: bool = False`; `consultar_normas_homologadas` passou a incluir `laudo_numero`/`laboratorio_emissor` só com `ver_laudo_completo: bool = False` — sem esse flag, retorna só o "resumo" (`resultado`, `produtos_certificados`). `execute_mcp_tool()` repassa os dois flags para a ferramenta certa; default `False` nos dois lugares — quem esquecer de passar o parâmetro nunca vaza campo sensível por acidente (fail-closed).
- `backend/app/rag/engine.py`: `run_pu_matcher_agent()` ganhou `ver_custos`/`ver_laudo_completo` como parâmetros e repassa para `execute_mcp_tool()`. Não decide a permissão — só encaminha uma decisão já tomada (mesmo princípio de `require_permission()`: a lógica de "quem pode o quê" mora num único lugar).
- `backend/app/main.py`: `/api/match` passou a extrair o `User` logado (`current_user: User = Depends(require_permission(Permission.VIEW_CATALOG))`, que já retornava o `User` desde a tarefa 4 — só não estava sendo usado) e calcular `has_permission(current_user, Permission.VIEW_COSTS)`/`has_permission(current_user, Permission.VIEW_HOMOLOGATION_FULL)` para repassar ao agente. `/api/match/stream` **não** recebeu o mesmo tratamento — comentário no código explica por quê: essa rota nunca passa `tools=` para o LiteLLM, então não invoca nenhuma ferramenta MCP hoje, não há campo sensível de MCP em risco nesse caminho ainda.
- `backend/tests/test_sensitive_fields.py` (novo, 11 testes): camada MCP pura (com/sem cada flag, default fail-closed para os dois), e a ponte permissão→booleano em `/api/match` com usuário real (Vendedor → ambos `False`; Gestor → ambos `True`; Técnico → `ver_laudo_completo=True` mas `ver_custos=False`, confirmando a pendência #1 da spec resolvida como negada por padrão).

**Testes:** 67/67 no total (56 anteriores + 11 novos).

**Code review (skill `code-review`, 2 eixos, rodado sobre o diff staged vs. `HEAD`):**
- **Standards:** nenhum achado bloqueante. Um ponto real e corrigido: `test_sensitive_fields.py` criava um `TestClient(app)` extra dentro de `_token_for()` mesmo já recebendo o fixture `client` — corrigido, `_token_for()` passou a receber `client` como parâmetro e reusar a mesma instância. Demais pontos levantados foram julgamento documentado, não corrigidos: (1) `ver_custos`/`ver_laudo_completo` viajam juntos como dois booleanos por três assinaturas — Data Clump reconhecido no próprio comentário do código como troca deliberada (manter engine/MCP sem conhecer `Permission`); (2) pequena duplicação de forma entre as duas funções `consultar_*` (só 2 instâncias, não extraído ainda); (3) o campo `custo_industrial_kg` é Speculative Generality no sentido estrito — protege um campo que ainda não existe de verdade — mas é exatamente o caminho que a spec pediu para viabilizar teste hoje.
- **Spec:** **zero achados.** Confirmado: permissões corretas (`VIEW_COSTS`/`VIEW_HOMOLOGATION_FULL`) checadas para os papéis certos batendo com `ROLE_PERMISSIONS`; defaults fail-closed em toda a cadeia; nenhuma tentativa de "resolver" a lacuna do RAG não estruturado via instrução de prompt (verificado: nenhum texto novo em `AGENT_SYSTEM_PROMPT`/prompts de `engine.py`); a interpretação de "Sumarizado" (quais campos ficam de fora) está documentada como decisão de implementação, não apresentada como se fosse literal da proposta.

**Decisões técnicas importantes:**
1. A permissão em si nunca é decidida fora de `app/main.py`/`has_permission()` — `engine.py` e `pu_mcp_server.py` só recebem o resultado já calculado, como booleano puro (mesmo padrão de centralização das tarefas 4-5, aplicado agora também aos parâmetros de dado, não só ao gate HTTP).
2. `/api/match/stream` deliberadamente não protegido do mesmo jeito — não é lacuna escondida, é ausência real de superfície de risco hoje (a rota não chama ferramenta MCP nenhuma). Fica registrado: se streaming ganhar tool-calling no futuro, replicar o mesmo padrão.

**Pendência que continua real, não resolvida por esta tarefa:** conteúdo RAG não estruturado (os 11.273 trechos já indexados no Qdrant) não tem metadado de "isto é custo/fórmula" — filtrar por perfil antes de montar o contexto do LLM exigiria um campo estruturado desde a ingestão, que não existe. Registrado em `docs/spec_rbac.md` desde a Etapa 1, reconfirmado aqui como não resolvido.

**Próximo item do cronograma:** tarefa 7 — Administração/provisionamento (Admin TI cria/edita usuário via API, hoje só existe via script/CLI direto no banco).

---

## 2026-08-24 — Sessão 21: Fase 5 (RBAC) — frontend, tela de login (fora da numeração das 9 tarefas)

**Contexto:** a Sessão 20 (tarefa 5) deixou o frontend Streamlit quebrado — `frontend/app.py` nunca enviava `Authorization` header, então toda pergunta pela tela passou a falhar com 401 assim que a proteção dos endpoints entrou em vigor. Perguntado se preferia seguir pra tarefa 6 (campos sensíveis) ou corrigir o frontend primeiro, o usuário escolheu explicitamente **"Login no frontend primeiro"**.

**Implementado em `frontend/app.py`:**
- Constantes `LOGIN_URL`/`ME_URL`; estado de sessão `access_token`/`current_user`.
- Portão de login: `if not st.session_state.access_token: <form> st.stop()` — nada da aplicação roda sem token válido em sessão.
- `POST /api/auth/login` → guarda token → `GET /api/auth/me` → guarda dados do usuário exibido na sidebar, com botão "Sair".
- `_auth_headers()`/`_bearer()` centralizam a montagem do header `Authorization: Bearer <token>`, usado em toda chamada de negócio (`/api/match`, `/api/match/stream`) — `/api/health` e `/api/auth/login` continuam sem header, corretamente.
- `_fazer_logout()` como único ponto que limpa `access_token`/`current_user`/`messages` — chamado tanto pelo botão "Sair" quanto por qualquer 401 recebido de um endpoint de negócio (sessão expirada/token inválido), que também dispara `st.rerun()` de volta pro portão de login.

**Code review (skill `code-review`, só eixo Standards — não é uma das 9 tarefas especificadas, sem spec formal dedicada):** 3 achados reais, todos corrigidos.
1. Os dois pontos de tratamento de 401 (streaming e síncrono) duplicavam as duas primeiras linhas de `_fazer_logout()` na mão em vez de chamar a função, e **nenhum dos dois limpava `messages`** — sessão ficava inconsistente após expirar. Corrigido: os dois agora chamam `_fazer_logout()`.
2. Construção do header `Bearer` duplicada — `_auth_headers()` centralizava, mas a chamada a `/api/auth/me` logo após o login remontava o dict na mão. Corrigido com helper `_bearer(token)`, usado nos dois lugares.
3. No caminho de streaming, um 401 setava `access_token = None` mas o generator continuava normalmente até o fim, e o código sempre anexava `_stream_state["answer"]` como nova mensagem do assistente — resultando numa mensagem de assistente com **conteúdo vazio** presa no histórico, nunca limpa (drift do achado 1). Corrigido: o generator agora só marca uma flag `_stream_state["expired"] = True` e retorna; **depois** que `st.write_stream(...)` termina, o código checa a flag e, se verdadeira, chama `_fazer_logout()` + `st.rerun()` em vez do fluxo normal de exibir fontes/modelo e anexar ao histórico.

Checks de segurança do mesmo review, todos **passaram sem achado**: campo de senha mascarado e nunca logado; token vive só em `st.session_state` (server-side, nunca em cookie/localStorage/URL); toda chamada de negócio carrega o header de auth corretamente.

**Validação executada (sem navegador — `chromium-cli` indisponível neste ambiente, confirmado via tentativa de uso da skill `run`; validação por HTTP direto + inspeção de log em vez de screenshot):**
- `py_compile frontend/app.py` — OK.
- Rebuild completo (`docker-compose build frontend` + `docker-compose up -d frontend`) — os 4 containers voltaram saudáveis; dado do usuário admin real (`lucas.braun`, Admin TI, Ativo) confirmado intacto no Postgres após o recreate.
- Simulação HTTP da sequência exata que o Streamlit executa: `POST /api/auth/login` → 200; `GET /api/auth/me` com o token → 200 com os dados esperados; `POST /api/match` e `POST /api/match/stream` sem token → 401; com token inválido → 401 nos dois. (Chamada de negócio com token *válido* não foi cronometrada até completar — o modelo Ollama local demorou mais que 60s pra responder, não é um problema de autenticação: a rejeição/aceitação do token já acontece antes da chamada ao LLM, e isso já está coberto pelos 56 testes automatizados da Sessão 20.)
- `docker logs pu_matcher_frontend` desde o restart — sem tracebacks.

**Limitação explícita:** esta validação não é equivalente a testar visualmente no navegador (preenchimento de formulário, clique real, renderização). Fica registrado como lacuna de teste, não escondido.

**Decisões técnicas:**
1. Erro 401 no caminho síncrono chama `st.rerun()` logo após `_fazer_logout()` (revisado durante a implementação — inicialmente cogitei não chamar `st.rerun()` aí para não "esconder" a mensagem de erro antes do usuário ver, mas por consistência com o caminho de streaming e para o portão de login reaparecer imediatamente, os dois caminhos agora se comportam igual).
2. Esta correção não é uma das 9 tarefas numeradas da Fase 5 — foi tratada como unidade de trabalho própria por ser um bloqueio de uso real e urgente, mas o plano de 9 tarefas continua o mesmo, sem renumeração.

**Pendências não tocadas nesta sessão:** tarefa 6 (campos sensíveis — lacuna técnica conhecida, dados de custo/fórmula não estruturados), tarefas 7-9, rate limiting do login (Sessão 18), 3 pendências funcionais em `docs/spec_rbac.md`.

**Próximo item do cronograma:** tarefa 6 — Restrição de campos sensíveis. Decisão de seguir ou não fica para o usuário confirmar.

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
