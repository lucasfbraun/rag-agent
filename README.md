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
2. Suba os contêineres:
   ```bash
   docker-compose up -d --build
   ```
3. Indexe documentos técnicos (coloque os arquivos em `data/raw_documents/` antes):
   ```bash
   docker exec -it pu_matcher_backend python -c "from app.rag.ingestion import ingest_catalog_directory; ingest_catalog_directory('/app/data/raw_documents')"
   ```
4. Acesse a interface em `http://localhost:8501`.

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

## Status

MVP em fase de scaffold inicial (Fase 0 do [CRONOGRAMA.md](CRONOGRAMA.md)). Ferramentas de ERP/normas ainda simuladas.
