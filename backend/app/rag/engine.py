import json
import logging
from typing import List, Dict, Any, Optional
import litellm
from app.templates import obter_instrucao_template
from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS, execute_mcp_tool
from app.rag.embeddings import get_embedding
from app.config import QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, EMBEDDING_MODEL, DEFAULT_CHAT_MODEL

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """Você é o PU Matcher, um Consultor Técnico Sênior e Especialista em Vendas Técnicas e Aplicações de Poliuretanos (PU) da linha FLEXX®.

SEU OBJETIVO PRINCIPAL:
Ajudar vendedores técnicos e engenheiros de aplicação a encontrar no acervo da empresa o PRODUTO EXISTENTE FLEXX® ou FORMULAÇÃO HOMOLOGADA que melhor atende à demanda trazida pelo cliente.

COMO INTERPRETAR OS DOCUMENTOS DO ACERVO (TERMINOLOGIA REAL DA EMPRESA):
O acervo tem 3 tipos de documento, cada um com um papel diferente — não trate todos como equivalentes:
   - "Boletim Técnico": a fonte principal para especificações e aplicação do produto (densidade, viscosidade, NCO%, dureza, uso recomendado). Priorize este documento para responder sobre especificações e adequação técnica.
   - "FISPQ": ficha de segurança do produto químico. Use apenas para informações de segurança/manuseio (EPIs, primeiros socorros, armazenamento) — NÃO é fonte confiável de especificação técnica ou de aplicação, o texto é em boa parte padrão/legal e se repete entre produtos diferentes.
   - "Certificado"/"ANALISE": laudo de lote específico — use como evidência de conformidade, não como especificação de referência do produto.

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

class RetrievalIndisponivelError(Exception):
    """Levantado quando o Qdrant/embedding falha de verdade (conexão, timeout,
    erro na busca) — ver AUD-003 em docs/auditoria_2026-08-25.md.

    Não confundir com coleção ainda não ingerida: esse é um estado normal do
    sistema (ninguém rodou a ingestão ainda) e continua retornando lista
    vazia. Só uma falha real levanta esta exceção, para que quem chama decida
    explicitamente o que fazer (503 no endpoint, evento de erro no streaming)
    em vez do agente responder de "conhecimento geral" achando que o catálogo
    só está vazio.
    """


def _get_qdrant_client():
    """Instancia o QdrantClient de forma lazy (não falha no import-time)."""
    from qdrant_client import QdrantClient
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)

def retrieve_products_context(
    query: str, top_k: int = 6, incluir_sensivel: bool = False
) -> List[Dict[str, Any]]:
    """
    Busca trechos de TDS e catálogos no banco vetorial Qdrant.

    Retorna lista vazia se a coleção simplesmente ainda não foi ingerida
    (estado normal). Levanta RetrievalIndisponivelError se o Qdrant ou o
    embedding falharem de verdade — o chamador decide o que fazer, não é mais
    engolido em silêncio aqui.

    `incluir_sensivel` (AUD-002, ticket 6): default False, fail-closed — quem
    esquecer de passar o parâmetro não vaza chunk classificado como sensível
    (custo/fórmula) por acidente, mesma disciplina já usada no MCP estruturado
    (tarefa 6 da Fase 5). Chunks sem o campo `sensivel` no payload (todo o
    acervo indexado antes desta sessão) não são afetados pelo filtro — a
    classificação só vale pra ingestão nova até uma reingestão do acervo real.
    """
    try:
        client = _get_qdrant_client()
        collections = client.get_collections().collections
    except Exception as e:
        logger.error("Erro ao conectar no Qdrant: %s", e)
        raise RetrievalIndisponivelError(str(e)) from e

    if not any(c.name == COLLECTION_NAME for c in collections):
        logger.warning(
            "Coleção '%s' não encontrada no Qdrant. "
            "Execute a ingestão de documentos antes de consultar.",
            COLLECTION_NAME
        )
        return []

    query_filter = None
    if not incluir_sensivel:
        from qdrant_client.http import models as qmodels
        query_filter = qmodels.Filter(
            must_not=[qmodels.FieldCondition(key="sensivel", match=qmodels.MatchValue(value=True))]
        )

    try:
        query_vector = get_embedding(query, EMBEDDING_MODEL)
        results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=top_k,
            query_filter=query_filter
        )
    except Exception as e:
        logger.error("Erro ao buscar no Qdrant: %s", e)
        raise RetrievalIndisponivelError(str(e)) from e

    return [hit.payload for hit in results]

def run_pu_matcher_agent(
    query: str,
    template_id: str = "proposta_tecnica_completa",
    model_name: str = DEFAULT_CHAT_MODEL,
    history: Optional[List[Dict[str, str]]] = None,
    ver_custos: bool = False,
    ver_laudo_completo: bool = False,
) -> Dict[str, Any]:
    """Executa o agente investigativo com suporte a RAG, MCP e Templates Padronizados.

    `ver_custos`/`ver_laudo_completo`: decisão de autorização já tomada por
    app.main (via has_permission()) — chegam aqui como booleano puro, repassados
    às ferramentas MCP (docs/spec_rbac.md, "Campos sensíveis") e também ao RAG
    (`incluir_sensivel`, AUD-002/ticket 6 — reaproveita VIEW_COSTS pra
    custo/fórmula, ver docs/spec_rbac.md "Pendências" item 2). engine.py não
    decide permissão, só encaminha a decisão já tomada."""
    docs = retrieve_products_context(query, incluir_sensivel=ver_custos)

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
        # A mensagem assistant (com TODAS as tool_calls) entra UMA vez, antes
        # do loop — não uma vez por tool_call (AUD-006: isso intercalava a
        # mesma mensagem assistant repetida entre as respostas das tools,
        # sequência inválida pro protocolo de tool calling com 2+ chamadas).
        messages.append(choice.message)
        for tool_call in choice.message.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Argumentos inválidos da tool_call %s (%s): %s", tool_call.id, fn_name, e
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": fn_name,
                    "content": json.dumps({"erro": "argumentos JSON inválidos, tente novamente"}),
                })
                continue
            tool_result = execute_mcp_tool(
                fn_name, fn_args, ver_custos=ver_custos, ver_laudo_completo=ver_laudo_completo
            )
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
    history: Optional[List[Dict[str, str]]] = None,
    ver_custos: bool = False,
):
    """
    Versão streaming do agente: gera chunks de texto à medida que o LLM responde.
    Usa Server-Sent Events (SSE) — cada chunk é um JSON com campo 'delta' ou 'done'.

    `ver_custos` (AUD-002, ticket 6): esta versão não chama ferramentas MCP,
    mas SEMPRE faz retrieval do RAG — precisa da permissão pra não vazar chunk
    sensível (custo/fórmula) igual `run_pu_matcher_agent`. Default False,
    fail-closed. Antes desta sessão o streaming não recebia nenhuma permissão
    porque não fazia sentido pro MCP (não chamado aqui) — mas isso nunca
    cobriu o RAG, que sempre rodou nesta função.
    """
    import json as _json

    try:
        docs = retrieve_products_context(query, incluir_sensivel=ver_custos)
    except RetrievalIndisponivelError:
        logger.error("Catálogo indisponível — abortando stream sem chamar o LLM.")
        yield _json.dumps({
            "type": "error",
            "message": "Catálogo de produtos indisponível no momento. Tente novamente em instantes.",
        }) + "\n"
        yield _json.dumps({"type": "done"}) + "\n"
        return

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
        # Texto bruto da exceção fica só no log (AUD-011, ticket 10) — o
        # cliente recebe uma mensagem genérica, nunca o detalhe interno.
        logger.error("Erro no streaming do agente: %s", e)
        yield _json.dumps({
            "type": "error",
            "message": "Erro ao gerar a resposta. Tente novamente em instantes.",
        }) + "\n"
    finally:
        yield _json.dumps({"type": "done"}) + "\n"
