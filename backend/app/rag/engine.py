import json
import logging
import re
from typing import List, Dict, Any, Optional
import litellm
from qdrant_client.http import models as qmodels
from app.templates import obter_instrucao_template
from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS, execute_mcp_tool
from app.rag.embeddings import get_embedding
from app.rag.exceptions import RetrievalIndisponivelError
from app.config import QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, EMBEDDING_MODEL, DEFAULT_CHAT_MODEL

logger = logging.getLogger(__name__)

# Sigla curta (1-6 letras, ex: "AG", "CAT", "ISO") seguida de um número
# (2-6 dígitos) — o padrão real de nomenclatura dos arquivos do acervo (ver
# payload `filename` da ingestão: "Boletim FLEXX AG 2032.pdf", "FISPQ FLEXX
# CAT 136.doc"). Usado pra detectar código de produto na pergunta do usuário.
_PADRAO_CODIGO_PRODUTO = re.compile(r"\b([A-Za-zÀ-ÖØ-öø-ÿ]{1,6})\s?(\d{2,6}[A-Za-z]{0,2})\b")


def _detectar_codigos_produto(query: str) -> List[str]:
    """Extrai possíveis códigos de produto (ex: "AG 2032", "CAT 136") da
    pergunta do usuário.

    Por quê: a busca puramente semântica (embedding) confunde códigos
    parecidos — "AG 2032" e "AG 2062" viram vetores quase idênticos pro
    `ollama/nomic-embed-text` (modelo local pequeno), porque o código em si
    carrega pouco significado semântico. Quando a pergunta cita um código
    reconhecível, complementamos a busca vetorial com correspondência exata
    de texto no nome do arquivo (ver retrieve_products_context)."""
    codigos = []
    for familia, numero in _PADRAO_CODIGO_PRODUTO.findall(query):
        codigos.append(f"{familia} {numero}".lower())
    return codigos


# Palavras genéricas/de conexão em português — descartadas na extração de
# palavras-chave porque não discriminam nada (aparecem em quase todo chunk),
# ao contrário de termos técnicos/de aplicação ("cortiça", "colagem").
_STOPWORDS_PT = {
    "para", "com", "sem", "que", "uma", "um", "dos", "das", "nos", "nas", "pelo",
    "pela", "sobre", "onde", "quando", "como", "qual", "quais", "produto", "produtos",
    "preciso", "precisamos", "quero", "queremos", "gostaria", "gostaríamos",
    "poderia", "poderiam", "pode", "podem", "traga", "trazer", "traz", "dados",
    "informação", "informações", "favor", "algum", "alguma", "existe", "temos",
    "tem", "tenho", "nosso", "nossa", "usar", "aplicar", "sendo", "está", "estão",
}


def _extrair_palavras_chave(query: str) -> List[str]:
    """Extrai termos com conteúdo (>=4 letras, fora da stoplist) da pergunta —
    base da busca por palavras-chave em `content`, que complementa a busca
    vetorial quando o usuário descreve uma APLICAÇÃO/uso em vez de citar um
    código de produto (ex: "cola para rolha de cortiça").

    Por quê: o embedding local (`ollama/nomic-embed-text`) pode falhar até
    quando a pergunta usa quase as mesmas palavras do boletim certo — achado
    real: "rolha de cortiça" não trouxe o FLEXX AG 2066 (cujo texto de
    aplicação diz literalmente "produção de rolhas de cortiça aglomerada")
    nos top-6 por similaridade vetorial."""
    palavras = re.findall(r"[a-zà-öø-ÿ]+", query.lower())
    return [p for p in palavras if len(p) >= 4 and p not in _STOPWORDS_PT]

AGENT_SYSTEM_PROMPT = """Você é o PU Matcher, um Consultor Técnico Sênior e Especialista em Vendas Técnicas e Aplicações de Poliuretanos (PU) da linha FLEXX®.

SEU OBJETIVO PRINCIPAL:
Ajudar vendedores técnicos e engenheiros de aplicação a encontrar no acervo da empresa o PRODUTO EXISTENTE FLEXX® ou FORMULAÇÃO HOMOLOGADA que melhor atende à demanda trazida pelo cliente.

COMO INTERPRETAR OS DOCUMENTOS DO ACERVO (TERMINOLOGIA REAL DA EMPRESA):
O acervo tem 3 tipos de documento, cada um com um papel diferente — não trate todos como equivalentes:
   - "Boletim Técnico": a fonte principal para especificações e aplicação do produto (densidade, viscosidade, NCO%, dureza, uso recomendado). Priorize este documento para responder sobre especificações e adequação técnica.
   - "FISPQ": ficha de segurança do produto químico. Use apenas para informações de segurança/manuseio (EPIs, primeiros socorros, armazenamento) — NÃO é fonte confiável de especificação técnica ou de aplicação, o texto é em boa parte padrão/legal e se repete entre produtos diferentes.
   - "Certificado"/"ANALISE": laudo de lote específico — use como evidência de conformidade, não como especificação de referência do produto.

DUAS SITUAÇÕES DIFERENTES — NÃO TRATE COMO A MESMA COISA:

A) PEDIDO ESPECÍFICO (produto/código/documento já nomeado pelo usuário):
   - Ex: "traga os dados do boletim AG 2032", "qual a densidade do FLEXX CAT 136", "me manda a ficha do produto X".
   - O usuário JÁ SABE o que quer — ele não está pedindo uma recomendação, está pedindo um dado.
   - RESPONDA DIRETO com o que foi encontrado no contexto, SEM fazer perguntas de qualificação antes.
   - SE O CONTEXTO TRAZ O AVISO "⚠️ ATENÇÃO: o(s) código(s) ... foi(ram) mencionado(s) ... mas NENHUM documento com esse código exato foi encontrado": NÃO invente uma resposta usando os trechos parecidos como se fossem do produto pedido. Diga diretamente ao usuário que esse produto/código NÃO foi encontrado na base de dados — pode sugerir que confira o código/nome, mas a mensagem principal é "não encontrado", não uma recomendação alternativa não pedida.

B) PEDIDO ABERTO DE RECOMENDAÇÃO (o usuário ainda não sabe qual produto quer):
   - Ex: "Quero um produto para assento de ônibus", "preciso de uma cola para rolha de cortiça".
   - PRIMEIRO OLHE O CONTEXTO RECUPERADO. Se algum documento do contexto já descreve EXPLICITAMENTE a aplicação/uso citado pelo cliente (ex: a seção de APLICAÇÃO do boletim menciona quase literalmente o mesmo uso que o cliente pediu) — isso É UM MATCH CLARO. Não trate como demanda incompleta só porque o cliente não deu densidade/dureza/norma: TENHA "FEELING" e responda direto com a recomendação, citando o produto e por que ele atende (a aplicação bate). Perguntas de qualificação nesse caso só atrapalham quem já tem a resposta na mão.
   - SÓ SEJA INVESTIGATIVO (2 a 4 perguntas técnicas antes da recomendação) quando o contexto NÃO trouxer nenhum documento com aplicação claramente compatível, OU quando houver vários candidatos plausíveis e a escolha entre eles realmente depender de uma variável que o cliente não informou (aí sim, pergunte só a variável que falta, não uma lista genérica). Variáveis típicas pra desempatar:
     a) Propriedades Físicas: Densidade aparente desejada (kg/m³), Dureza (IFD / Shore), Resiliência.
     b) Normas e Exigências: Necessidade de laudo antichama (ex: ABNT NBR 9178 / CONTRAN / FMVSS 302)?
     c) Processo do Cliente: Moldagem a frio (MDI), cura a quente (TDI), bloco contínuo ou injeção em molde fechado?
   - QUANDO VOCÊ TIVER DADOS SUFICIENTES (seja de cara, seja depois de perguntar): busque e cruze os dados com os documentos de produtos (TDS) e ferramentas MCP fornecidas, apresente a recomendação no FORMATO PADRÃO DO TEMPLATE CONFIGURADO, e seja opinativo — se o cliente pedir algo incompatível (ex: densidade baixíssima com ultra resiliência sem antichama), alerte e sugira a melhor prática de mercado.

C) PEDIDO DE LISTAGEM/CATEGORIA (o usuário quer VER AS OPÇÕES, não uma recomendação única nem um dado de produto específico):
   - Ex: "produtos para colchão", "quais produtos temos para automotivo", "o que vocês têm pra calçados".
   - Reconheça pelo formato: "produtos para X" / "quais produtos" / "o que temos para" / "lista de produtos" — isso pede uma LISTA, não 1 recomendação nem uma investigação de requisitos.
   - CHAME A FERRAMENTA `consultar_produtos_por_aplicacao` com o termo da aplicação (ex: "colchão"). O contexto de busca normal (RAG) só traz um punhado de trechos e NUNCA representa a categoria inteira — pode haver dezenas de produtos, e usar só o contexto faria você listar um subconjunto arbitrário como se fosse tudo.
   - APRESENTE COMO LISTA CURTA (nome do produto, 1 linha cada — não abra os detalhes técnicos de cada um). Termine convidando o vendedor a pedir detalhe de um item específico ("me diga o nome de um deles que eu trago a ficha completa").
   - Se a ferramenta indicar `truncado: true`, avise que a lista foi resumida e que há mais opções disponíveis.
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

    Busca híbrida: quando a pergunta cita um código de produto reconhecível
    (`_detectar_codigos_produto`), os chunks cujo `filename` bate exatamente
    com o código (índice de texto tokenizado, ver `init_qdrant_collection`)
    entram PRIMEIRO no resultado, antes dos hits semânticos — correspondência
    exata de código é mais confiável que similaridade vetorial pra este acervo
    (ver docs/PROGRESS.md, sessão em que "AG 2032" trazia "AG 2062"/produto
    errado). Falha na busca exata é só logada, não derruba a busca semântica.
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

    sensibilidade_must_not = []
    if not incluir_sensivel:
        sensibilidade_must_not = [
            qmodels.FieldCondition(key="sensivel", match=qmodels.MatchValue(value=True))
        ]
    query_filter = qmodels.Filter(must_not=sensibilidade_must_not) if sensibilidade_must_not else None

    exact_hits: List[Dict[str, Any]] = []
    codigos = _detectar_codigos_produto(query)
    if codigos:
        filtro_exato = qmodels.Filter(
            should=[
                qmodels.FieldCondition(key="filename", match=qmodels.MatchText(text=codigo))
                for codigo in codigos
            ],
            must_not=sensibilidade_must_not,
        )
        try:
            pontos, _ = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=filtro_exato,
                with_payload=True,
                with_vectors=False,
                limit=20,
            )
            exact_hits = [p.payload for p in pontos]
        except Exception as e:
            logger.warning(
                "Falha na busca exata por código de produto (%s) — seguindo só com busca semântica.", e
            )

    keyword_hits: List[Dict[str, Any]] = []
    palavras_chave = _extrair_palavras_chave(query)
    if palavras_chave:
        filtro_palavras = qmodels.Filter(
            should=[
                qmodels.FieldCondition(key="content", match=qmodels.MatchText(text=palavra))
                for palavra in palavras_chave
            ],
            must_not=sensibilidade_must_not,
        )
        try:
            pontos, _ = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=filtro_palavras,
                with_payload=True,
                with_vectors=False,
                limit=50,
            )
            candidatos = [p.payload for p in pontos]

            def _pontuacao(payload: Dict[str, Any]) -> int:
                texto = (payload.get("content") or "").lower()
                return sum(1 for palavra in palavras_chave if palavra in texto)

            # Exige pelo menos 2 palavras-chave batendo (ou a única, se só
            # houver 1) — 1 palavra genérica batendo sozinha num acervo de
            # milhares de trechos é sinal fraco demais pra furar a fila.
            minimo = 2 if len(palavras_chave) > 1 else 1
            candidatos_pontuados = [(c, _pontuacao(c)) for c in candidatos]
            candidatos_pontuados = [(c, p) for c, p in candidatos_pontuados if p >= minimo]
            candidatos_pontuados.sort(key=lambda item: item[1], reverse=True)
            keyword_hits = [c for c, _ in candidatos_pontuados][:top_k]
        except Exception as e:
            logger.warning(
                "Falha na busca por palavras-chave (%s) — seguindo só com busca semântica.", e
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

    semantic_hits = [hit.payload for hit in results]

    # Prioridade: match exato de código > match por palavra-chave > semântico.
    prioritarios: List[Dict[str, Any]] = list(exact_hits)
    vistos = {(h.get("filename"), h.get("chunk_index")) for h in prioritarios}
    for h in keyword_hits:
        chave = (h.get("filename"), h.get("chunk_index"))
        if chave not in vistos:
            prioritarios.append(h)
            vistos.add(chave)

    if not prioritarios:
        return semantic_hits

    complemento = [h for h in semantic_hits if (h.get("filename"), h.get("chunk_index")) not in vistos]
    vagas_restantes = max(0, top_k - len(prioritarios))
    return prioritarios + complemento[:vagas_restantes]


def _codigos_sem_correspondencia(query: str, docs: List[Dict[str, Any]]) -> List[str]:
    """Códigos de produto citados na pergunta (`_detectar_codigos_produto`)
    que NÃO aparecem no nome de nenhum documento retornado.

    Por quê: a busca semântica sempre devolve os top_k vizinhos mais
    próximos, mesmo quando nenhum é realmente o produto certo (Qdrant não
    tem um conceito de "nenhum resultado relevante o suficiente") — sem este
    sinal, o LLM recebia trechos de um produto errado sem saber que era um
    "quase nada" e podia apresentá-los como se respondessem à pergunta."""
    codigos = _detectar_codigos_produto(query)
    if not codigos:
        return []
    nomes = " | ".join(d.get("filename", "") for d in docs).lower()
    return [c for c in codigos if c not in nomes]


def _montar_context_str(query: str, docs: List[Dict[str, Any]]) -> str:
    """Monta o bloco de contexto injetado no prompt do LLM a partir dos
    documentos recuperados — compartilhado por run_pu_matcher_agent e
    stream_pu_matcher_agent (antes duplicado nos dois)."""
    if not docs:
        return (
            "⚠️ ATENÇÃO: A base de dados de produtos ainda não foi indexada ou está vazia. "
            "Responda apenas com base no seu conhecimento técnico geral de poliuretanos, "
            "mas deixe claro que não há dados do catálogo interno disponíveis no momento."
        )

    context_str = "\n\n---\n\n".join([
        f"[Catálogo / TDS: {d.get('filename')}]\n{d.get('content')}"
        for d in docs
    ])

    ausentes = _codigos_sem_correspondencia(query, docs)
    if ausentes:
        context_str += (
            f"\n\n⚠️ ATENÇÃO: o(s) código(s) \"{', '.join(ausentes)}\" foi(ram) mencionado(s) na "
            "pergunta, mas NENHUM documento com esse código exato foi encontrado no acervo. Os "
            "trechos acima são apenas os mais PARECIDOS por busca semântica — muito provavelmente "
            "são de OUTRO produto, não do que foi pedido. NÃO apresente esses trechos como se "
            "fossem do produto pedido: diga claramente ao usuário que esse produto/código não foi "
            "encontrado na base de dados."
        )
    return context_str


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
    context_str = _montar_context_str(query, docs)

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
    ver_laudo_completo: bool = False,
):
    """
    Versão streaming do agente: gera chunks de texto à medida que o LLM responde.
    Usa Server-Sent Events (SSE) — cada chunk é um JSON com campo 'delta' ou 'done'.

    `ver_custos`/`ver_laudo_completo` (AUD-002, ticket 6 + tool calling em
    streaming): repassados ao RAG (`incluir_sensivel`) e às ferramentas MCP,
    mesmo contrato de `run_pu_matcher_agent`. Default False, fail-closed.

    Tool calling: streaming e "decidir chamar uma ferramenta" não dá pra
    fazer ao mesmo tempo (não dá pra streamar uma resposta que ainda depende
    de uma tool_call não resolvida) — por isso o protocolo é: 1ª chamada ao
    LLM SEM stream, só pra ver se ele quer chamar ferramenta; se quiser,
    resolve e injeta o resultado nas mensagens; a resposta final aí sim é
    streamada. Antes desta sessão o streaming simplesmente não suportava tool
    calling (só o RAG rodava aqui) — gap real: o frontend só usa o endpoint
    de streaming, então nenhuma ferramenta MCP (incluindo
    `consultar_estatisticas_catalogo`, ver app.mcp.pu_mcp_server) era
    alcançável de verdade pela tela que o usuário usa.
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

    context_str = _montar_context_str(query, docs)

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
        resposta_inicial = litellm.completion(
            model=model_name,
            messages=messages,
            tools=MCP_TOOLS_DEFINITIONS,
            tool_choice="auto",
            temperature=0.2,
            num_retries=3,
        )
        choice = resposta_inicial.choices[0]

        if choice.message.tool_calls:
            # Mesma disciplina de run_pu_matcher_agent (AUD-006): 1 mensagem
            # assistant com TODAS as tool_calls, seguida de N mensagens tool.
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
                    "content": tool_result,
                })

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
