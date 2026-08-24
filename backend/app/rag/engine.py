import json
import logging
from typing import List, Dict, Any, Optional
import litellm
from app.templates import obter_instrucao_template
from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS, execute_mcp_tool
from app.rag.embeddings import get_embedding
from app.config import QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, EMBEDDING_MODEL, DEFAULT_CHAT_MODEL

logger = logging.getLogger(__name__)

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

def _get_qdrant_client():
    """Instancia o QdrantClient de forma lazy (não falha no import-time)."""
    from qdrant_client import QdrantClient
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)

def retrieve_products_context(query: str, top_k: int = 6) -> List[Dict[str, Any]]:
    """
    Busca trechos de TDS e catálogos no banco vetorial Qdrant.
    Retorna lista vazia (com aviso) se a coleção não existir ou estiver vazia.
    """
    try:
        client = _get_qdrant_client()

        collections = client.get_collections().collections
        if not any(c.name == COLLECTION_NAME for c in collections):
            logger.warning(
                "Coleção '%s' não encontrada no Qdrant. "
                "Execute a ingestão de documentos antes de consultar.",
                COLLECTION_NAME
            )
            return []

        query_vector = get_embedding(query, EMBEDDING_MODEL)

        results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=top_k
        )
        return [hit.payload for hit in results]

    except Exception as e:
        logger.error("Erro ao buscar no Qdrant: %s", e)
        return []

def run_pu_matcher_agent(
    query: str,
    template_id: str = "proposta_tecnica_completa",
    model_name: str = DEFAULT_CHAT_MODEL,
    history: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """Executa o agente investigativo com suporte a RAG, MCP e Templates Padronizados."""
    docs = retrieve_products_context(query)

    if docs:
        context_str = "\n\n---\n\n".join([
            f"[Catálogo / TDS: {d.get('filename')}]\n{d.get('content')}"
            for d in docs
        ])
    else:
        context_str = (
            "⚠️ ATENÇÃO: A base de dados de produtos ainda não foi indexada ou está vazia. "
            "Responda apenas com base no seu conhecimento técnico geral de poliuretanos, "
            "mas deixe claro que não há dados do catálogo interno disponíveis no momento."
        )

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

    response = litellm.completion(
        model=model_name,
        messages=messages,
        tools=MCP_TOOLS_DEFINITIONS,
        tool_choice="auto",
        temperature=0.2,
        num_retries=3
    )

    choice = response.choices[0]

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
        final_response = litellm.completion(model=model_name, messages=messages, temperature=0.2, num_retries=3)
        answer = final_response.choices[0].message.content
    else:
        answer = choice.message.content

    sources = list(set([d.get("filename") for d in docs if d.get("filename")]))
    return {"answer": answer, "sources": sources, "model_used": model_name}


def stream_pu_matcher_agent(
    query: str,
    template_id: str = "proposta_tecnica_completa",
    model_name: str = DEFAULT_CHAT_MODEL,
    history: Optional[List[Dict[str, str]]] = None
):
    """
    Versão streaming do agente: gera chunks de texto à medida que o LLM responde.
    Usa Server-Sent Events (SSE) — cada chunk é um JSON com campo 'delta' ou 'done'.
    """
    import json as _json

    docs = retrieve_products_context(query)

    if docs:
        context_str = "\n\n---\n\n".join([
            f"[Catálogo / TDS: {d.get('filename')}]\n{d.get('content')}"
            for d in docs
        ])
    else:
        context_str = (
            "⚠️ ATENÇÃO: A base de dados de produtos ainda não foi indexada ou está vazia. "
            "Responda apenas com base no seu conhecimento técnico geral de poliuretanos, "
            "mas deixe claro que não há dados do catálogo interno disponíveis no momento."
        )

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

    sources = list(set([d.get("filename") for d in docs if d.get("filename")]))
    yield _json.dumps({"type": "meta", "sources": sources, "model_used": model_name}) + "\n"

    try:
        response = litellm.completion(
            model=model_name,
            messages=messages,
            temperature=0.2,
            stream=True,
            num_retries=3
        )
        for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                yield _json.dumps({"type": "delta", "content": delta}) + "\n"
    except Exception as e:
        logger.error("Erro no streaming do agente: %s", e)
        yield _json.dumps({"type": "error", "message": str(e)}) + "\n"
    finally:
        yield _json.dumps({"type": "done"}) + "\n"
