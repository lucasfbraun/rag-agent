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
- **Multi-LLM:** LiteLLM (Gemini, OpenAI, Anthropic, Grok)
- **Frontend:** Streamlit
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
5. Acesse a interface em `http://localhost:8501`.

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

## Estrutura do projeto

```
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
├── requirements.txt
├── .env.example
├── backend/app/
│   ├── main.py            # API REST FastAPI
│   ├── templates.py       # Templates padronizados de resposta
│   ├── mcp/                # Ferramentas MCP (catálogo ERP, normas)
│   └── rag/                # Ingestão e motor do agente investigativo
├── frontend/app.py         # Interface de chat Streamlit
├── data/raw_documents/     # TDS, catálogos e homologações (não versionado)
└── docs/                   # Documentos originais da proposta e guia técnico
```

## APIs disponíveis

| Endpoint | Método | Descrição |
|---|---|---|
| `/` | GET | Health check simples |
| `/api/health` | GET | Status detalhado (Qdrant + coleção) |
| `/api/templates` | GET | Lista os templates disponíveis |
| `/api/match` | POST | Executa o agente investigativo |
| `/api/ingest` | POST | Dispara ingestão de documentos em background |

## CLI de Ingestão

```bash
# Verificar saúde do sistema
docker exec -it pu_matcher_backend python -m app.cli health

# Indexar documentos
docker exec -it pu_matcher_backend python -m app.cli ingest
docker exec -it pu_matcher_backend python -m app.cli ingest --dir /app/data/raw_documents
```

## Status

Fase 0 (Setup) concluída — código robusto com healthchecks, CLI e tratamento de erros. Aguardando chaves de API e documentos reais para Fase 1.
Ferramentas de ERP/normas ainda simuladas (Fase 4 do [CRONOGRAMA.md](CRONOGRAMA.md)).
