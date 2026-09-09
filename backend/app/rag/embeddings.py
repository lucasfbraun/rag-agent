from typing import List
import litellm


def get_embedding(text: str, model: str) -> List[float]:
    """Gera o embedding de um texto via LiteLLM.

    O caminho especial para modelos `ollama/*` (chamada direta à API nativa,
    contornando um timeout de ~40s por chamada no cost-calculator do litellm)
    foi removido em 2026-09-09: o projeto passou a usar só motores pagos, por
    decisão do usuário. Está no histórico do git se o Ollama voltar.
    """
    emb_res = litellm.embedding(model=model, input=[text], num_retries=3)
    return emb_res.data[0]["embedding"]
