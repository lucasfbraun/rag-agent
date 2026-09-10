from typing import List
from contextlib import contextmanager
from contextvars import ContextVar
import litellm


_vetores_da_consulta: ContextVar[dict | None] = ContextVar("vetores_da_consulta", default=None)


@contextmanager
def reutilizar_embeddings_na_consulta():
    """Reutiliza vetores só durante a preparação de UMA consulta.

    Catálogo e treinamento pesquisam a mesma pergunta com o mesmo modelo.
    O contexto é descartado inclusive em falhas e não armazena documentos,
    respostas ou resultados sujeitos a permissões/alterações do acervo.
    """
    token = _vetores_da_consulta.set({})
    try:
        yield
    finally:
        _vetores_da_consulta.reset(token)


def get_embedding(text: str, model: str) -> List[float]:
    """Gera o embedding de um texto via LiteLLM.

    O caminho especial para modelos `ollama/*` (chamada direta à API nativa,
    contornando um timeout de ~40s por chamada no cost-calculator do litellm)
    foi removido em 2026-09-09: o projeto passou a usar só motores pagos, por
    decisão do usuário. Está no histórico do git se o Ollama voltar.
    """
    cache = _vetores_da_consulta.get()
    if cache is None:
        return get_embeddings([text], model)[0]
    key = (model, text)
    if key not in cache:
        cache[key] = tuple(get_embeddings([text], model)[0])
    return list(cache[key])


def get_embeddings(texts: List[str], model: str) -> List[List[float]]:
    """Gera embeddings de vários textos numa chamada só, na ordem recebida.

    Existe por custo de tempo, medido contra a API real em 2026-09-09: uma
    chamada por texto leva ~716 ms (a latência é quase toda ida-e-volta de
    rede, não processamento), enquanto um lote de 128 sai a ~31 ms por texto.
    Numa reingestão do acervo inteiro isso é a diferença entre ~84 minutos e
    poucos minutos de embedding.

    A consulta continua usando `get_embedding` — ali é um texto só por
    natureza, e o lote não teria o que agrupar.
    """
    if not texts:
        return []
    resposta = litellm.embedding(model=model, input=texts, num_retries=3)
    # A API devolve `index` em cada item; ordenar por ele em vez de confiar na
    # ordem de chegada é o que garante que o vetor certo fica no chunk certo —
    # trocar dois vetores de lugar seria um erro silencioso, sem exceção e sem
    # sintoma até alguém reparar que a busca traz o produto errado.
    itens = sorted(resposta.data, key=lambda item: item["index"])
    return [item["embedding"] for item in itens]
