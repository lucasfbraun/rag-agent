# PU Matcher — Agente Investigativo e Consultivo de Match de Produtos (PU)

Agente RAG on-premise para apoiar vendas técnicas e engenharia de aplicação na indústria de poliuretanos,
localizando produtos já homologados no acervo da empresa a partir da demanda descrita pelo cliente.

Documentação de origem do projeto em [docs/](docs/):
- [Proposta do Projeto](docs/proposta_do_projeto_similaridade.md)
- [Guia Técnico do MVP](docs/guia_mvp_e_codigo_similaridade.md)

Acompanhamento do desenvolvimento:
- [CRONOGRAMA.md](CRONOGRAMA.md) — fases e marcos do projeto (fonte da verdade, versionada)
- [PROGRESS.md](PROGRESS.md) — log de progresso sessão a sessão (fonte da verdade, versionada)
- [Painel visual do projeto](https://claude.ai/code/artifact/91bf54cd-d88a-4816-abee-80f362863581) — dashboard com o mesmo conteúdo acima, republicado a cada avanço relevante

> **Nota:** o painel visual é uma página hospedada externamente no claude.ai (Claude Artifact) — **não existe como arquivo dentro deste repositório**. Ele é gerado a partir do conteúdo de `CRONOGRAMA.md`/`PROGRESS.md` e republicado no mesmo link acima sempre que esses arquivos forem atualizados. `CRONOGRAMA.md` e `PROGRESS.md` são a fonte da verdade; o painel é apenas um espelho visual de leitura.

## Stack

- **Backend:** FastAPI
- **RAG / Vector DB:** Qdrant
- **Multi-LLM:** LiteLLM (Gemini, OpenAI, Anthropic, Grok, ou Ollama local/gratuito — ver `.env.example`)
- **Frontend:** Streamlit, atrás de um proxy reverso (Caddy) — ver "Identidade visual & instalação como app (PWA)"
- **Autenticação & RBAC:** PostgreSQL + JWT (Fase 5 — ver seção abaixo)
- **Ferramentas vivas:** MCP (catálogo ERP e normas/homologações — atualmente simuladas, ver Fase 4 do cronograma)

## Como rodar localmente

1. Copie `.env.example` para `.env` e preencha as chaves de API reais (nunca commitar o `.env`):
   ```bash
   cp .env.example .env
   ```
2. Suba os contêineres (aguarda healthchecks automaticamente):
   ```bash
   docker-compose up -d --build
   ```
3. Verifique se todos os serviços estão saudáveis:
   ```bash
   docker-compose ps
   # ou via API:
   curl http://localhost:8000/api/health
   ```
4. Indexe documentos técnicos (coloque os arquivos em `data/raw_documents/` antes):
   ```bash
   # CLI (recomendado):
   docker exec -it pu_matcher_backend python -m app.cli ingest

   # Ou via API REST (roda em background):
   curl -X POST http://localhost:8000/api/ingest -H "Content-Type: application/json" -d '{"dir_path": "/app/data/raw_documents"}'
   ```
5. Crie o primeiro usuário Admin TI (obrigatório — sem ele ninguém consegue logar; ver "Autenticação & Perfis (RBAC)" abaixo para o comando).
6. Acesse a interface em `http://localhost:8501` e faça login com o usuário criado no passo anterior.
   > Desde a Sessão 27, `8501` é servido por um proxy Caddy na frente do Streamlit (serviço `proxy` no Compose), não pelo container `frontend` diretamente — necessário pro Service Worker do PWA funcionar (ver seção abaixo). Pra quem debuga direto no container, o Streamlit em si continua ouvindo em `8501` só na rede interna do Compose (sem porta publicada no host).

## Como rodar localmente (sem Docker)

Útil para desenvolvimento e debug sem precisar do Docker Desktop.

```bash
# Instalar dependências
pip install -r requirements.txt

# Terminal 1 — Backend
python backend/run_local.py

# Terminal 2 — Frontend
python frontend/run_local.py
```

A interface estará em `http://localhost:8501` e o backend em `http://localhost:8000`.
> **Qdrant:** Instale e rode localmente (`docker run -p 6333:6333 qdrant/qdrant`) ou aponte `QDRANT_HOST` para um servidor remoto.
> **PWA:** rodando assim (sem o proxy Caddy do Docker Compose), o card "Instalar aplicativo" da tela de login fica sempre desabilitado — o Service Worker só é servido na raiz (`/`) através do proxy. Isso é o comportamento esperado em dev local, não um bug.

## Autenticação & Perfis (RBAC)

Desde a Fase 5, toda funcionalidade de negócio (chat/match, templates, ingestão) exige login — `/` e `/api/health` continuam públicos (liveness/monitoramento). Autorização é decidida sempre no backend, nunca confiando só na interface.

**5 perfis** (`Vendedor`, `Técnico`, `Gestor Comercial`, `Químico/P&D`, `Admin TI`), cada um com um conjunto fixo de permissões. Referência completa da matriz de acesso, decisões de design e pendências funcionais em [docs/spec_rbac.md](docs/spec_rbac.md).

**Provisionamento é manual** (sem AD/LDAP integrado ainda — desenho já pronto pra isso, ver spec). Não existe auto-cadastro nem uma segunda conta Admin TI por padrão: o primeiro usuário precisa ser criado direto no banco, uma única vez, por quem tem acesso ao servidor:

```bash
docker exec -i pu_matcher_backend python <<'PYEOF'
from app.db import SessionLocal
from app.auth.user_service import create_user
from app.models import Role

session = SessionLocal()
create_user(
    session,
    username="admin",
    nome="Nome Completo",
    email="admin@suaempresa.com.br",
    password="TROQUE-ESTA-SENHA",   # mínimo 8 caracteres
    perfil=Role.ADMIN_TI,
)
session.commit()
session.close()
print("Usuário Admin TI criado.")
PYEOF
```

> Use um heredoc como acima (não `-c "..."` com a senha inline) para a senha não aparecer no histórico do shell/`docker inspect`. Depois de logado, o próprio Admin TI cria os demais usuários via API (`POST /api/auth/users`, ver tabela abaixo) — não precisa repetir esse passo manual.
>
> **Débito conhecido:** não existe hoje um comando de CLI dedicado pra esse bootstrap (`python -m app.cli create-admin` ou similar) — o script acima é o único caminho. Ver `docs/spec_rbac.md`, "Pendências".

**Fluxo de login:**
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "sua-senha"}'
# -> {"access_token": "...", "token_type": "bearer"}

curl http://localhost:8000/api/match -H "Authorization: Bearer <access_token>" ...
```

## Estrutura do projeto

```
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── requirements.txt
├── .env.example
├── backend/app/
│   ├── main.py             # API REST FastAPI
│   ├── templates.py        # Templates padronizados de resposta
│   ├── db.py, models.py    # Postgres: usuários, feedback e conversas
│   ├── conversation_*.py   # CRUD e persistência do histórico por usuário
│   ├── auth/                # Autenticação, autorização e administração de usuários (Fase 5)
│   ├── mcp/                 # Ferramentas MCP (catálogo ERP, normas)
│   └── rag/                 # Ingestão e motor do agente investigativo
├── backend/alembic/        # Migrations do banco relacional (Fase 5)
├── frontend/app.py          # Interface de chat Streamlit (com tela de login + card de PWA)
├── frontend/static/         # manifest.json, service-worker.js e ícone do PWA (Sessão 27)
├── .streamlit/config.toml   # Tema (paleta da marca) + enableStaticServing
├── proxy/Caddyfile          # Proxy reverso — serve o Service Worker em "/" (ver seção PWA)
├── IDENTIDADE_VISUAL.md     # Guia de paleta/tipografia da marca (origem: projeto FIDC) + aplicação aqui
├── data/raw_documents/      # TDS, catálogos e homologações (não versionado)
└── docs/                    # Documentos originais da proposta, guia técnico e spec_rbac.md
```

## APIs disponíveis

| Endpoint | Método | Autenticação | Descrição |
|---|---|---|---|
| `/` | GET | Pública | Health check simples |
| `/api/health` | GET | Pública | Status detalhado (Qdrant + coleção) |
| `/api/auth/login` | POST | Pública | Login — devolve o token JWT |
| `/api/auth/me` | GET | Login | Confirma o token e devolve o usuário dono dele |
| `/api/templates` | GET | `SELECT_TEMPLATE` (todos os perfis) | Lista os templates disponíveis |
| `/api/match` | POST | `VIEW_CATALOG` (todos os perfis) | Executa o agente investigativo |
| `/api/match/stream` | POST | `VIEW_CATALOG` (todos os perfis) | Mesma coisa, em streaming (SSE/NDJSON) |
| `/api/conversations` | GET / POST | `VIEW_CATALOG` (todos os perfis) | Lista / cria conversas do usuário atual |
| `/api/conversations/{id}` | GET / DELETE | `VIEW_CATALOG` (proprietário) | Retoma / apaga uma conversa e suas mensagens |
| `/api/ingest` | POST | `MANAGE_INGESTION` (só Admin TI) | Dispara ingestão de documentos em background |
| `/api/auth/users` | GET / POST | `MANAGE_USERS` (só Admin TI) | Lista / cria usuário |
| `/api/auth/users/{id}` | GET / PATCH | `MANAGE_USERS` (só Admin TI) | Obtém / edita usuário |
| `/api/auth/users/{id}/password` | POST | `MANAGE_USERS` (só Admin TI) | Redefine a senha de um usuário |
| `/api/auth/users/{id}/deactivate` | POST | `MANAGE_USERS` (só Admin TI) | Desativa usuário ("excluir" nunca apaga a linha) |

Matriz completa de quem tem cada permissão em [docs/spec_rbac.md](docs/spec_rbac.md).

## CLI de Ingestão

```bash
# Verificar saúde do sistema
docker exec -it pu_matcher_backend python -m app.cli health

# Indexar documentos
docker exec -it pu_matcher_backend python -m app.cli ingest
docker exec -it pu_matcher_backend python -m app.cli ingest --dir /app/data/raw_documents
```

## Backup (Qdrant + Postgres)

```bash
# do host (nao de dentro de um container), com a stack rodando via docker compose
python backup.py                  # Qdrant + Postgres
python backup.py --qdrant-only
python backup.py --postgres-only
python backup.py --manter 30      # retencao (default: 14 backups mais recentes de cada tipo)
```

Gera um snapshot da coleção do Qdrant (`data/backups/qdrant/`, via API HTTP do próprio Qdrant) e um `pg_dump` do Postgres (`data/backups/postgres/`, via `docker exec` no container — o binário `pg_dump` só existe lá). Nenhum dos dois é commitado (`data/backups/` no `.gitignore`). Instruções de restauração no docstring de `backup.py`. **Execução manual** — ainda não agendado automaticamente (Windows Task Scheduler, a configurar quando houver servidor de produção definido, ver `CRONOGRAMA.md` Fase 8). Motivação: `docs/incidente_2026-08-26_reingestao_apagou_colecao.md` — sem isso, o incidente que apagou a coleção real não tinha nenhum caminho de recuperação automática.

## Identidade visual & instalação como app (PWA)

A paleta e a tipografia vêm de [IDENTIDADE_VISUAL.md](IDENTIDADE_VISUAL.md) (originalmente escrito pro projeto FIDC, em Next.js/Tailwind) — a seção final desse documento ("Aplicação no PU Matcher") explica onde cada cor vive aqui: `.streamlit/config.toml` para os widgets nativos, CSS injetado em `frontend/app.py` pro resto (tipografia Roboto, cards).

**Logo:** ainda não foi fornecido o arquivo real do Grupo Flexível. `frontend/static/icon.svg` é um monograma placeholder ("PU" em verde-petróleo) usado na sidebar e no ícone do manifest até o arquivo real chegar — troca é local, ver a tabela em `IDENTIDADE_VISUAL.md`.

**Instalar como app (PWA):** a tela de login mostra um card "Instalar aplicativo" quando o navegador permite (Chrome/Edge desktop ou Android, critérios de instalabilidade atendidos). Isso exigiu um proxy reverso (Caddy, serviço `proxy` no Compose) na frente do Streamlit — o Service Worker precisa ser servido em `/` pra controlar a página inteira, e o Streamlit só serve estático em `/app/static/*`. Sem o proxy (ex: `frontend/run_local.py`), o card aparece desabilitado com uma dica em vez de simular sucesso.

> **Não testado em navegador real** — este ambiente não tem Chrome/Chromium disponível pra automação. O que foi verificado: os 3 arquivos (`manifest.json`, `icon.svg`, `service-worker.js`) são servidos com o `Content-Type` e no caminho certos através do proxy (`docker compose up` + `curl`), e a suíte `frontend/tests/test_pwa_assets.py` trava se alguém quebrar essa forma no futuro. O comportamento de instalação em si (o Chrome de fato mostrar o prompt) precisa de verificação manual num navegador real antes de considerar a Fase 6 fechada.

## Status

🟨 **Reingestão em andamento (recuperando do incidente de 2026-08-26).** A coleção real do Qdrant foi apagada durante o desenvolvimento (Sessão 30, detalhe completo em `docs/incidente_2026-08-26_reingestao_apagou_colecao.md`); os documentos-fonte na pasta de rede não foram tocados, só o índice. `python ingest_network.py --full` está rodando (disparado 2026-08-31, 3-6h esperadas) — validar a contagem final contra os 11.273 pontos históricos quando terminar. Backup configurado desde 2026-08-31 (ver seção acima) para que uma futura perda de índice tenha caminho de recuperação, o que não existia no incidente original.

Fases 0 (Setup) e 5 (RBAC & Governança — 9/9 tarefas, autenticação/autorização/administração de usuários funcionando de ponta a ponta) concluídas. Fase 1 (Ingestão) recuperando (ver acima) — o código de ingestão/reconciliação está pronto (ver auditoria abaixo). Fase 2 (motor RAG/agente investigativo) em andamento, com gaps de comportamento já identificados. Fase 6 (Frontend/UX de Campo) iniciada — identidade visual da marca aplicada e card de instalação como PWA (ver seção acima), ainda sem validação em navegador real nem no logo definitivo. Fase 8 com o item de backup adiantado (ver seção acima); os demais itens seguem não iniciados, assim como as Fases 3, 4 e 7. Ferramentas de ERP/normas ainda simuladas (Fase 4).

Auditoria de qualidade de código em andamento desde 2026-08-25 — 9 de 12 bugs já corrigidos. Ver `docs/auditoria_2026-08-25.md`, `docs/verificacao_auditoria_2026-08-26.md` e `docs/plano_correcao_auditoria_2026-08-25.md` para bugs confirmados e plano de correção.

Estado completo, sessão a sessão, em [PROGRESS.md](PROGRESS.md); visão geral por fase em [CRONOGRAMA.md](CRONOGRAMA.md).
