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
- **Frontend:** Streamlit
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
│   ├── db.py, models.py    # Conexão Postgres e model User (Fase 5)
│   ├── auth/                # Autenticação, autorização e administração de usuários (Fase 5)
│   ├── mcp/                 # Ferramentas MCP (catálogo ERP, normas)
│   └── rag/                 # Ingestão e motor do agente investigativo
├── backend/alembic/        # Migrations do banco relacional (Fase 5)
├── frontend/app.py          # Interface de chat Streamlit (com tela de login)
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

## Status

Fases 0 (Setup), 1 (Ingestão — 11.273 trechos de 8.377 arquivos reais indexados) e 5 (RBAC & Governança — 9/9 tarefas, autenticação/autorização/administração de usuários funcionando de ponta a ponta) concluídas. Fase 2 (motor RAG/agente investigativo) em andamento, com gaps de comportamento já identificados. Fases 3, 4, 6, 7 e 8 ainda não iniciadas. Ferramentas de ERP/normas ainda simuladas (Fase 4).

Estado completo, sessão a sessão, em [PROGRESS.md](PROGRESS.md); visão geral por fase em [CRONOGRAMA.md](CRONOGRAMA.md).
