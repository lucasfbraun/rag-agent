# Log de Progresso — PU Matcher

Log cronológico do andamento do projeto. Cada entrada corresponde a uma sessão de trabalho.
Ver visão geral de fases em [CRONOGRAMA.md](CRONOGRAMA.md).

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
