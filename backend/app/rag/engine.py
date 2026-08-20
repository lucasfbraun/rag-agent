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
