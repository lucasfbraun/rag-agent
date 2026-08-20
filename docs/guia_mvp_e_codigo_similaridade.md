# 🛠️ Guia Técnico & Código do MVP: PU Matcher (On-Premise)

> **Agente Investigativo e Consultivo para Match de Produtos e Vendas Técnicas em Poliuretanos**  
> **Arquitetura:** RAG Híbrido On-Premise + LiteLLM Multi-Provider + Qdrant Vector DB + Streamlit UI + MCP Tools + Motor de Templates  

---

## 1. Pré-Requisitos do Servidor On-Premise

* **Sistema Operacional:** Linux (Ubuntu 22.04 LTS) ou Windows Server com Docker & Docker Compose.
* **Hardware Mínimo Recomendado:**
  * CPU: 8 Cores (para processamento paralelo de catálogos e TDS)
  * Memória RAM: 16 GB a 32 GB
  * Disco: SSD 200 GB+ livres para base vetorial e arquivos brutos
* **Rede:** Acesso liberado aos endpoints das APIs de IA utilizadas (Google Gemini, OpenAI, Anthropic Claude, Grok).

---

## 2. Estrutura de Diretórios do Projeto

```
pu-matcher/
├── docker-compose.yml            # Orquestração local de contêineres
├── Dockerfile.backend            # Imagem do Backend FastAPI
├── Dockerfile.frontend           # Imagem da Interface Web Streamlit
├── requirements.txt              # Dependências Python
├── .env                          # Variáveis de ambiente e chaves de API
├── backend/
│   └── app/
│       ├── __init__.py
│       ├── main.py               # API REST FastAPI
│       ├── templates.py          # Gerenciador de Templates Padronizados de Resposta
│       ├── mcp/
│       │   ├── __init__.py
│       │   └── pu_mcp_server.py  # Servidor MCP (Catálogo ERP e Homologação de Normas)
│       └── rag/
│           ├── __init__.py
│           ├── ingestion.py      # Ingestão em massa de TDS, Catálogos e Homologações
│           └── engine.py         # Orquestrador do Agente Investigativo + Match de Produtos
├── frontend/
│   └── app.py                    # Interface Streamlit com Seleção de Templates e Chat
└── data/
    ├── raw_documents/            # Milhares de TDS, Catálogos e Fichas de Homologação
    └── qdrant_storage/           # Persistência dos vetores
```

---

## 3. Arquivos de Infraestrutura e Configuração

### 📄 `docker-compose.yml`
```yaml
version: '3.8'

services:
  # 1. Banco de Dados Vetorial On-Premise
  qdrant:
    image: qdrant/qdrant:v1.9.2
    container_name: pu_matcher_qdrant
    restart: always
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - ./data/qdrant_storage:/qdrant/storage

  # 2. Backend FastAPI + Agente Investigativo
  backend:
    build:
      context: .
      dockerfile: Dockerfile.backend
    container_name: pu_matcher_backend
    restart: always
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      - qdrant
    volumes:
      - ./data/raw_documents:/app/data/raw_documents

  # 3. Frontend Web Streamlit (Campo & Vendas Técnicas)
  frontend:
    build:
      context: .
      dockerfile: Dockerfile.frontend
    container_name: pu_matcher_frontend
    restart: always
    env_file: .env
    ports:
      - "8501:8501"
    depends_on:
      - backend
```

---

### 📄 `requirements.txt`
```txt
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pydantic>=2.7.0
litellm>=1.40.0
qdrant-client>=1.9.0
mcp>=1.0.0
pypdf>=4.2.0
python-docx>=1.1.2
streamlit>=1.36.0
requests>=2.32.0
python-dotenv>=1.0.1
tiktoken>=0.7.0
```

---

### 📄 `.env`
```env
# Banco Vetorial Qdrant
QDRANT_HOST=qdrant
QDRANT_PORT=6333

# Chaves de API das IAs
GEMINI_API_KEY=AIzaSy...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GROK_API_KEY=xai-...

# Segredo de Sessão
SECRET_KEY=PU_MATCHER_SECRET_KEY_2026
```

---

### 📄 `Dockerfile.backend`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./backend /app/backend

ENV PYTHONPATH=/app

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

### 📄 `Dockerfile.frontend`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./frontend /app/frontend

EXPOSE 8501

CMD ["streamlit", "run", "frontend/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

---

## 4. Código do Gerenciador de Templates Padronizados

### 📄 `backend/app/templates.py`
```python
"""
Módulo para gerenciamento de templates padronizados de resposta.
Permite à gestão comercial e técnica configurar o formato oficial de entrega.
"""

TEMPLATES_DISPONIVEIS = {
    "proposta_tecnica_completa": {
        "nome": "📊 Proposta Técnica Comercial Completa (Padrão)",
        "descricao": "Ideal para envio formal com comparativo técnico detalhado e normas.",
        "formato": """
🎯 **RECOMENDAÇÃO DE PRODUTO HOMOLOGADO - PU MATCH**

• **Demanda Informada:** [Resumo das necessidades do cliente]
• **Produto Recomendado:** **[NOME COMERCIAL DO PRODUTO]** (Código ERP: `[CÓDIGO]`)
• **Família Química:** [ex: Sistema MDI Moldado a Frio / Poliol Poliéster / etc.]

📋 **Tabela Comparativa de Especificações:**
| Requisito do Cliente | Especificação do Produto Existente | Status |
| :--- | :--- | :---: |
| [Requisito 1: Densidade/Dureza] | [Valor na Ficha TDS] | ✅ Atende |
| [Requisito 2: Flamabilidade/Norma] | [Norma Homologada / Certificado] | ✅ Homologado |
| [Requisito 3: Tipo de Processo] | [Parâmetro Recomendado de Injeção] | ✅ Compatível |

💡 **Diferenciais e Orientações Técnicas de Aplicação:**
- [Vantagens competitivas do produto]
- [Dica de processo: temperatura de molde, relação NCO, desmoldagem]

⚠️ **Disponibilidade Comercial e Próximos Passos:**
- Produto ativo em linha.
- Sugestão: Solicitar amostra piloto para teste no molde do cliente.
"""
    },
    "comercial_rapido": {
        "nome": "⚡ Resumo Comercial Rápido (WhatsApp / E-mail)",
        "descricao": "Formato direto e ágil para resposta imediata ao cliente em visita.",
        "formato": """
✅ **Temos o produto ideal para sua demanda!**

* **Produto:** **[NOME COMERCIAL]** (Cód: `[CÓDIGO]`)
* **Aplicação Principal:** [Aplicação homologada]
* **Principais Destaques:**
  - [Propriedade 1: ex: Densidade 50 kg/m³ e alta resiliência]
  - [Propriedade 2: Atende norma de flamabilidade CONTRAN / ABNT]
* **Status:** Produto de linha em catálogo ativo.
* **Ficha Técnica (TDS):** [Nome do arquivo TDS anexado/referenciado]
"""
    },
    "parecer_interno_engenharia": {
        "nome": "🔬 Parecer de Engenharia de Aplicação (Interno)",
        "descricao": "Focado em análise interna de compatibilidade de processo e bancada.",
        "formato": """
🧪 **PARECER TÉCNICO INTERNO DE APLICAÇÃO**

1. **Cliente / Projeto:** [Identificação da Demanda]
2. **Produto de Linha Indicado:** [Nome e Código]
3. **Aderência Técnica:** [Alta / Média / Drop-in direto]
4. **Análise de Variáveis Críticas de Injeção:**
   - Relação Poliol/Isocianato recomendada: [ex: 100:45 pbw]
   - Tempo de Creme / Gel / Desmolde: [ex: 18s / 75s / 4.5 min]
5. **Observações de Homologação:** [Histórico de laudos em clientes similares]
"""
    }
}

def obter_instrucao_template(template_id: str) -> str:
    """Retorna o template formatado para ser injetado nas instruções do modelo."""
    tpl = TEMPLATES_DISPONIVEIS.get(template_id, TEMPLATES_DISPONIVEIS["proposta_tecnica_completa"])
    return f"""
OBRIGATÓRIO: Quando você tiver todas as informações necessárias e for recomendar o produto encontrado na base de dados, ESTRUTURE SUA RESPOSTA FINAL ESTRITAMENTE SEGUINDO A ESTRUTURA DESTE TEMPLATE:
{tpl['formato']}
"""
```

---

## 5. Código do Servidor MCP (Dados Vivos de Catálogo e Normas)

### 📄 `backend/app/mcp/pu_mcp_server.py`
```python
"""
Servidor MCP (Model Context Protocol) para consultar estoque de produtos acabados no ERP
e banco de dados de normas/homologações em tempo real.
"""
from typing import Dict, Any

# Simulação de consulta ao ERP Corporativo (SAP / TOTVS / etc.)
def consultar_catalogo_erp(termo_busca: str) -> Dict[str, Any]:
    """Consulta se o produto está ativo para faturamento, código ERP e embalagens."""
    # Exemplo simulado de retorno de ERP
    return {
        "produto_encontrado": "PU-SEAT-5000 FR",
        "codigo_erp": "PRD-99841",
        "status_linha": "Ativo para Vendas",
        "embalagens_disponiveis": ["Tambor 200L", "IBC 1000L"],
        "prazo_fabricacao": "Em estoque / Pronta entrega",
        "familia": "Espuma Moldada a Frio (Cure MDI)"
    }

# Simulação de consulta ao Banco de Homologações & Normas
def consultar_normas_homologadas(norma_requerida: str) -> Dict[str, Any]:
    """Verifica laudos oficiais de conformidade com normas regulatórias."""
    return {
        "norma_pesquisada": norma_requerida,
        "produtos_certificados": ["PU-SEAT-5000 FR", "PU-FLEX-450-AUTO"],
        "laudo_numero": "CERT-2025-NBR9178",
        "laboratorio_emissor": "IPT / SENAI",
        "resultado": "Aprovado - Autoextinguível (Taxa de queima < 100 mm/min)"
    }

# Definição de ferramentas no padrão MCP / LiteLLM
MCP_TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "consultar_catalogo_erp",
            "description": "Consulta código ERP, disponibilidade de linha e embalagens de um produto de poliuretano.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termo_busca": {"type": "string", "description": "Nome ou código do produto"}
                },
                "required": ["termo_busca"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_normas_homologadas",
            "description": "Consulta quais produtos da empresa já foram testados e homologados para normas específicas (antichama, ABNT, ASTM, FMVSS).",
            "parameters": {
                "type": "object",
                "properties": {
                    "norma_requerida": {"type": "string", "description": "Código da norma (ex: 'ABNT NBR 9178', 'FMVSS 302', 'UL94')"}
                },
                "required": ["norma_requerida"]
            }
        }
    }
]

def execute_mcp_tool(tool_name: str, arguments: dict) -> str:
    """Executa a ferramenta MCP chamada pelo agente."""
    if tool_name == "consultar_catalogo_erp":
        return str(consultar_catalogo_erp(arguments.get("termo_busca", "")))
    elif tool_name == "consultar_normas_homologadas":
        return str(consultar_normas_homologadas(arguments.get("norma_requerida", "")))
    return "Ferramenta não encontrada."
```

---

## 6. Código da Ingestão e do Orquestrador Investigativo

### 📄 `backend/app/rag/ingestion.py` (Indexador de TDS e Catálogos no Qdrant)
```python
import os
import glob
from typing import List
from pypdf import PdfReader
import docx
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
import litellm

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = "pu_products_catalog"

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

def init_qdrant_collection():
    """Garante que a coleção de produtos e TDS exista no Qdrant."""
    collections = client.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qmodels.VectorParams(
                size=1536,
                distance=qmodels.Distance.COSINE
            )
        )
        print(f"✅ Coleção '{COLLECTION_NAME}' inicializada.")

def extract_text_from_file(filepath: str) -> str:
    """Extrai texto e tabelas técnicas de PDFs e DOCX de produtos."""
    ext = os.path.splitext(filepath)[1].lower()
    text = ""
    if ext == ".pdf":
        reader = PdfReader(filepath)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    elif ext in [".docx", ".doc"]:
        doc = docx.Document(filepath)
        for p in doc.paragraphs:
            text += p.text + "\n"
        for table in doc.tables:
            for row in table.rows:
                text += " | ".join(c.text.strip() for c in row.cells) + "\n"
    return text

def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> List[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        if len(chunk.strip()) > 40:
            chunks.append(chunk)
    return chunks

def ingest_catalog_directory(dir_path: str):
    """Indexa milhares de Boletins Técnicos (TDS), Catálogos e Homologações."""
    init_qdrant_collection()
    files = glob.glob(os.path.join(dir_path, "**/*.*"), recursive=True)
    print(f"🚀 Iniciando indexação de {len(files)} arquivos de produtos...")

    points = []
    point_id = 1

    for file_path in files:
        if not file_path.lower().endswith(('.pdf', '.docx', '.txt')):
            continue

        filename = os.path.basename(file_path)
        try:
            raw_text = extract_text_from_file(file_path)
            chunks = chunk_text(raw_text)

            for chunk_idx, chunk in enumerate(chunks):
                emb_res = litellm.embedding(model="text-embedding-3-small", input=[chunk])
                vector = emb_res.data[0]['embedding']

                points.append(
                    qmodels.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "filename": filename,
                            "filepath": file_path,
                            "chunk_index": chunk_idx,
                            "content": chunk
                        }
                    )
                )
                point_id += 1

                if len(points) >= 100:
                    client.upsert(collection_name=COLLECTION_NAME, points=points)
                    points = []
                    print(f"💾 {point_id} trechos de catálogo indexados...")

        except Exception as e:
            print(f"⚠️ Erro ao processar {filename}: {e}")

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
    print(f"🎉 Catálogo completo indexado com sucesso!")
```

---

### 📄 `backend/app/rag/engine.py` (Motor do Agente Investigativo e Opinativo)
```python
import os
import json
from typing import List, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
import litellm
from app.templates import obter_instrucao_template
from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS, execute_mcp_tool

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = "pu_products_catalog"

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

AGENT_SYSTEM_PROMPT = """Você é o PU Matcher, um Consultor Técnico Sênior e Especialista em Vendas Técnicas e Aplicações de Poliuretanos (PU).

SEU OBJETIVO PRINCIPAL:
Ajudar vendedores técnicos e engenheiros de aplicação a encontrar no acervo da empresa o PRODUTO EXISTENTE ou FORMULAÇÃO HOMOLOGADA que melhor atende à demanda trazida pelo cliente.

COMPORTAMENTO INVESTIGATIVO E OPINATIVO (MUITO IMPORTANTE):
1. NÃO DÊ UMA RESPOSTA FINAL IMEDIATA SE OS REQUISITOS ESTIVEREM INCOMPLETOS:
   - Se o usuário disser apenas 'Quero um produto para assento de ônibus', você DEVE ser opinativo e fazer de 2 a 4 perguntas técnicas assertivas para qualificar a demanda antes de dar a recomendação definitiva.
   - Pergunte sobre variáveis críticas na química de PU:
     a) Propriedades Físicas: Densidade aparente desejada (kg/m³), Dureza (IFD / Shore), Resiliência.
     b) Normas e Exigências: Necessidade de laudo antichama (ex: ABNT NBR 9178 / CONTRAN / FMVSS 302)?
     c) Processo do Cliente: Moldagem a frio (MDI), cura a quente (TDI), bloco contínuo ou injeção em molde fechado?
2. QUANDO VOCÊ TIVER DADOS SUFICIENTES:
   - Busque e cruze os dados com os documentos de produtos (TDS) e ferramentas MCP fornecidas.
   - Apresente a recomendação no FORMATO PADRÃO DO TEMPLATE CONFIGURADO.
   - Seja opinativo: se o cliente pedir algo incompatível (ex: densidade baixíssima com ultra resiliência sem antichama), alerte e sugira a melhor prática de mercado.
"""

def retrieve_products_context(query: str, top_k: int = 6) -> List[Dict[str, Any]]:
    """Busca trechos de TDS e catálogos no banco vetorial Qdrant."""
    emb_res = litellm.embedding(model="text-embedding-3-small", input=[query])
    query_vector = emb_res.data[0]['embedding']

    results = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=top_k
    )
    return [hit.payload for hit in results]

def run_pu_matcher_agent(
    query: str, 
    template_id: str = "proposta_tecnica_completa",
    model_name: str = "gemini/gemini-1.5-flash",
    history: List[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Executa o agente investigativo com suporte a RAG, MCP e Templates Padronizados."""
    docs = retrieve_products_context(query)

    context_str = "\n\n---\n\n".join([
        f"[Catálogo / TDS: {d.get('filename')}]\n{d.get('content')}"
        for d in docs
    ])

    template_instruction = obter_instrucao_template(template_id)

    system_instruction = f"""{AGENT_SYSTEM_PROMPT}

DIRETRIZ DE PADRONIZAÇÃO DE RESPOSTA:
{template_instruction}
"""

    messages = [{"role": "system", "content": system_instruction}]

    if history:
        messages.extend(history[-8:])

    user_prompt = f"""BASE DE DADOS DE PRODUTOS DA EMPRESA (TDS & HOMOLOGAÇÕES):
{context_str}

MENSAGEM / DEMANDA DO VENDEDOR OU CLIENTE:
{query}
"""
    messages.append({"role": "user", "content": user_prompt})

    # Chamada com ferramentas MCP (Catálogo ERP + Normas)
    response = litellm.completion(
        model=model_name,
        messages=messages,
        tools=MCP_TOOLS_DEFINITIONS,
        tool_choice="auto",
        temperature=0.2
    )

    choice = response.choices[0]

    # Processamento de ferramentas MCP se o modelo acionar
    if choice.message.tool_calls:
        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)
            tool_result = execute_mcp_tool(fn_name, fn_args)

            messages.append(choice.message)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": fn_name,
                "content": tool_result
            })

        final_response = litellm.completion(
            model=model_name,
            messages=messages,
            temperature=0.2
        )
        answer = final_response.choices[0].message.content
    else:
        answer = choice.message.content

    sources = list(set([d.get("filename") for d in docs if d.get("filename")]))

    return {
        "answer": answer,
        "sources": sources,
        "model_used": model_name
    }
```

---

### 📄 `backend/app/main.py` (API REST FastAPI)
```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.rag.engine import run_pu_matcher_agent
from app.templates import TEMPLATES_DISPONIVEIS

app = FastAPI(title="PU Matcher API", version="2.0.0")

class MatchRequest(BaseModel):
    query: str
    template_id: str = "proposta_tecnica_completa"
    model_name: str = "gemini/gemini-1.5-flash"
    history: Optional[List[dict]] = []

@app.get("/")
def health():
    return {"status": "online", "service": "PU Matcher - Product Match & Consultative Sales"}

@app.get("/api/templates")
def list_templates():
    """Lista todos os templates cadastrados no sistema."""
    return TEMPLATES_DISPONIVEIS

@app.post("/api/match")
def match_product(req: MatchRequest):
    try:
        res = run_pu_matcher_agent(
            query=req.query,
            template_id=req.template_id,
            model_name=req.model_name,
            history=req.history
        )
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

---

## 7. Código da Interface do Usuário (Streamlit)

### 📄 `frontend/app.py`
```python
import streamlit as st
import requests

API_URL = "http://backend:8000/api/match"
TEMPLATES_URL = "http://backend:8000/api/templates"

st.set_page_config(
    page_title="PU Matcher - Consultor Técnico de Produtos", 
    page_icon="🎯", 
    layout="wide"
)

# Estado da Sessão
if "messages" not in st.session_state:
    st.session_state.messages = []
if "template_id" not in st.session_state:
    st.session_state.template_id = "proposta_tecnica_completa"

# Sidebar com Parametrizações
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3081/3081559.png", width=60)
    st.title("PU Matcher")
    st.caption("Agente Investigativo para Match de Produtos de Poliuretano")
    st.divider()

    st.subheader("📋 Padrão de Resposta (Template)")
    template_option = st.selectbox(
        "Selecione o Formato da Resposta:",
        [
            ("📊 Proposta Técnica Completa", "proposta_tecnica_completa"),
            ("⚡ Resumo Comercial Rápido (WhatsApp)", "comercial_rapido"),
            ("🔬 Parecer de Engenharia Interno", "parecer_interno_engenharia")
        ],
        format_func=lambda x: x[0]
    )
    st.session_state.template_id = template_option[1]

    st.divider()
    st.subheader("⚙️ Motor de Inteligência Artificial")
    selected_model = st.selectbox(
        "Provedor / Modelo:",
        [
            "gemini/gemini-1.5-flash",
            "gemini/gemini-1.5-pro",
            "gpt-4o",
            "gpt-4o-mini",
            "claude-3-5-sonnet-20240620",
            "groq/llama-3.1-70b-versatile"
        ],
        index=0
    )

    if st.button("🔄 Iniciar Nova Demanda de Cliente", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# Área Principal
st.markdown("### 🎯 Assistente de Vendas Técnicas & Match de Produtos")
st.caption("Descreva a demanda do cliente (ex: 'Cliente quer desenvolver assento de ônibus de alta densidade') para que a IA avalie os requisitos e localize produtos existentes no catálogo.")

# Exibe histórico
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "sources" in msg and msg["sources"]:
            st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(msg['sources'])}")

# Input do Vendedor / Técnico
if prompt := st.chat_input("Digite a demanda ou responda às perguntas do agente..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analisando requisitos técnicos e cruzando catálogo de produtos..."):
            try:
                payload = {
                    "query": prompt,
                    "template_id": st.session_state.template_id,
                    "model_name": selected_model,
                    "history": [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state.messages[:-1]
                    ]
                }
                
                # Chamada ao Backend FastAPI
                response = requests.post(API_URL, json=payload, timeout=60)
                if response.status_code == 200:
                    data = response.json()
                    st.markdown(data["answer"])
                    if data["sources"]:
                        st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(data['sources'])}")
                    
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": data["answer"],
                        "sources": data["sources"]
                    })
                else:
                    st.error(f"Erro na resposta da API: {response.text}")
            except Exception as e:
                st.error(f"Erro de comunicação com o backend: {e}")
```

---

## 8. Passo a Passo de Execução no Servidor Local

### 1. Iniciar os Contêineres Docker
No terminal do servidor on-premise, execute:
```bash
docker-compose up -d --build
```

### 2. Indexar os Boletins Técnicos (TDS) e Catálogos
Coloque os arquivos em `./data/raw_documents/` e execute:
```bash
docker exec -it pu_matcher_backend python -c "
from app.rag.ingestion import ingest_catalog_directory
ingest_catalog_directory('/app/data/raw_documents')
"
```

### 3. Acessar e Testar o Fluxo Investigativo
Abra `http://localhost:8501` e faça o teste com uma demanda aberta:
1. Escreva: *"O cliente quer desenvolver um assento para ônibus com espuma de alta durabilidade."*
2. Veja o agente **questionar ativamente** sobre densidade, flamabilidade CONTRAN e processo de cura.
3. Responda às perguntas e veja o agente gerar a **recomendação padronizada no template oficial**.
