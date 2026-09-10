"""
Índice do conhecimento treinado pela equipe (Sessão 38, item 4).

Coleção Qdrant **separada** do acervo (`pu_treinamento`), e a separação é uma
decisão de projeto, não organização:

  1. O acervo é reconstruído por reingestão completa — aconteceu na Sessão 35c.
     Se o treinamento estivesse na mesma coleção, teria sido apagado junto. E,
     ao contrário de um boletim, ele não tem arquivo de origem para ser
     reindexado a partir de nada: seria perda definitiva.
  2. `catalog_stats` varre a coleção inteira contando produtos. Itens de
     treinamento entrariam nessas contagens como se fossem produtos, e a
     resposta "temos N produtos catalogados" passaria a mentir.
  3. Precedência e limites são diferentes dos do acervo, e coleções separadas
     tornam isso trivial em vez de um filtro em toda consulta.

O que entra no embedding é a PERGUNTA (ou o título, no caso de conhecimento),
não a resposta: o item precisa ser encontrado quando alguém pergunta algo
parecido, e é a pergunta que se parece com a pergunta.
"""
import logging
from typing import Any, Dict, List

from qdrant_client.http import models as qmodels

from app.config import EMBEDDING_MODEL, VECTOR_SIZE
from app.rag.embeddings import get_embedding
from app.rag.ingestion import get_qdrant_client

logger = logging.getLogger(__name__)

COLECAO_TREINAMENTO = "pu_treinamento"

# Teto por modalidade numa consulta. Sem ele, uma base de treinamento grande
# empurraria o acervo para fora do contexto — e o agente passaria a responder
# de memória curada em vez de documento, que é o oposto do projeto.
LIMITES = {"correcao": 2, "conhecimento": 3, "exemplo": 2}

# Abaixo disto o item não tem relação real com a pergunta. Sem corte, o Qdrant
# devolve sempre os N mais próximos — mesmo que o mais próximo seja distante —
# e uma correção sobre cortiça apareceria numa pergunta sobre colchão.
SIMILARIDADE_MINIMA = 0.35


def _garantir_colecao(client) -> None:
    existentes = {c.name for c in client.get_collections().collections}
    if COLECAO_TREINAMENTO in existentes:
        return
    client.create_collection(
        collection_name=COLECAO_TREINAMENTO,
        vectors_config=qmodels.VectorParams(
            size=VECTOR_SIZE, distance=qmodels.Distance.COSINE
        ),
    )
    logger.info("Coleção de treinamento '%s' criada.", COLECAO_TREINAMENTO)


def indexar(item) -> None:
    """Põe (ou atualiza) um item aprovado no índice.

    O id do ponto é o id do item no Postgres — assim reindexar depois de uma
    edição sobrescreve em vez de duplicar, e remover é direto."""
    client = get_qdrant_client()
    _garantir_colecao(client)

    vetor = get_embedding(item.pergunta, EMBEDDING_MODEL)
    client.upsert(
        collection_name=COLECAO_TREINAMENTO,
        points=[
            qmodels.PointStruct(
                id=str(item.id),
                vector=vetor,
                payload={
                    "tipo": item.tipo.value,
                    "pergunta": item.pergunta,
                    "resposta": item.resposta,
                    "autor": item.criado_por.nome,
                    # A data vai para o prompt: o agente e o leitor precisam
                    # poder pesar a idade de uma orientação interna. Ver a
                    # decisão 4.3 de docs/spec_treinamento.md — não há
                    # expiração automática, porque ela apagaria conhecimento
                    # ainda válido em silêncio.
                    "data": item.created_at.strftime("%m/%Y"),
                },
            )
        ],
    )


def remover(item_id) -> None:
    """Tira um item do índice — usado ao recusar, editar ou excluir."""
    client = get_qdrant_client()
    try:
        client.delete(
            collection_name=COLECAO_TREINAMENTO,
            points_selector=qmodels.PointIdsList(points=[str(item_id)]),
        )
    except Exception as e:
        # Coleção ainda não existe, ou o ponto já não está lá: nos dois casos o
        # resultado desejado (o item fora do índice) já vale.
        logger.warning("Falha ao remover item de treinamento do índice: %s", e)


def buscar(pergunta: str) -> Dict[str, List[Dict[str, Any]]]:
    """Itens de treinamento relevantes para a pergunta, agrupados por tipo.

    Devolve dicionário vazio — nunca levanta — quando a coleção não existe ou
    o Qdrant falha: treinamento é um complemento, e perder o complemento não
    pode derrubar uma consulta que o acervo responderia sozinho."""
    vazio = {tipo: [] for tipo in LIMITES}
    if not pergunta or not pergunta.strip():
        return vazio

    try:
        client = get_qdrant_client()
        if COLECAO_TREINAMENTO not in {c.name for c in client.get_collections().collections}:
            return vazio
        vetor = get_embedding(pergunta, EMBEDDING_MODEL)
    except Exception as e:
        logger.warning("Busca de treinamento indisponível (%s) — seguindo sem ela.", e)
        return vazio

    resultado = {tipo: [] for tipo in LIMITES}
    for tipo, limite in LIMITES.items():
        try:
            achados = client.search(
                collection_name=COLECAO_TREINAMENTO,
                query_vector=vetor,
                limit=limite,
                score_threshold=SIMILARIDADE_MINIMA,
                query_filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(
                        key="tipo", match=qmodels.MatchValue(value=tipo)
                    )]
                ),
            )
        except Exception as e:
            logger.warning("Falha ao buscar treinamento do tipo %s: %s", tipo, e)
            continue
        resultado[tipo] = [hit.payload for hit in achados]
    return resultado


def montar_bloco(pergunta: str) -> str:
    """Bloco de contexto com o conhecimento treinado, pronto para o prompt.

    Cada modalidade entra com um papel EXPLÍCITO, e essa rotulagem é a parte
    que mais importa:

      - Correção tem a maior precedência, mas **não substitui** a resposta: ela
        casa por similaridade, e duas perguntas parecidas podem ter respostas
        diferentes por um detalhe (densidade, norma, aplicação).
      - Conhecimento entra como orientação INTERNA, não como documento — o
        agente precisa poder dizer "segundo orientação interna" em vez de
        atribuir aquilo a um boletim que não diz isso.
      - Exemplo é modelo de FORMA, não fonte de fato. Sem essa marcação o LLM
        copia os números do exemplo para a resposta real: é o modo de falha
        clássico de few-shot com conteúdo técnico.
    """
    itens = buscar(pergunta)
    if not any(itens.values()):
        return ""

    linhas = ["", "🎓 CONHECIMENTO TREINADO PELA EQUIPE (curado por pessoas, não extraído de documento):"]

    for item in itens["correcao"]:
        linhas.append(
            f'\n⭐ CORREÇÃO REGISTRADA ({item["data"]}, por {item["autor"]}) — para uma pergunta '
            f'praticamente igual a esta, a equipe corrigiu a resposta para:\n'
            f'   Pergunta: "{item["pergunta"]}"\n'
            f'   Resposta correta: {item["resposta"]}\n'
            "   USE ISTO com prioridade sobre a sua própria formulação. Mas CONFIRA se o caso é "
            "mesmo o mesmo: se a pergunta atual difere em densidade, norma, aplicação ou produto, "
            "diga isso em vez de repetir a correção como se coubesse."
        )

    for item in itens["conhecimento"]:
        linhas.append(
            f'\n📌 ORIENTAÇÃO INTERNA ({item["data"]}, por {item["autor"]}): {item["pergunta"]}\n'
            f'   {item["resposta"]}\n'
            "   Isto NÃO está em boletim nenhum — apresente como orientação interna da equipe, "
            "nunca como se fosse conteúdo de um documento. Se CONTRADIZER um boletim do contexto, "
            "mostre os dois lados com as fontes e encaminhe para a equipe técnica/P&D; não escolha "
            "um lado sozinho."
        )

    if itens["exemplo"]:
        linhas.append(
            "\n✍️ EXEMPLOS DE COMO RESPONDER — modelo de FORMA (estrutura, tom, nível de detalhe). "
            "Os NÚMEROS e PRODUTOS abaixo são ilustrativos e NÃO valem como fato: nunca os "
            "reaproveite na resposta real."
        )
        for item in itens["exemplo"]:
            linhas.append(f'   Pergunta: "{item["pergunta"]}"\n   Resposta modelo: {item["resposta"]}')

    return "\n".join(linhas)
