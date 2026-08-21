import os
from typing import List
import requests
import litellm

OLLAMA_API_BASE = os.getenv("OLLAMA_API_BASE", "http://localhost:11434")


def get_embedding(text: str, model: str) -> List[float]:
    """
    Gera o embedding de um texto.

    Modelos "ollama/*" vão direto na API nativa do Ollama: o cost-calculator do
    litellm tenta buscar info do modelo via /api/show e falha repetidamente contra
    o Ollama, adicionando ~40s de timeout por chamada mesmo com a resposta já pronta.
    """
    if model.startswith("ollama/"):
        ollama_model = model.split("/", 1)[1]
        resp = requests.post(
            f"{OLLAMA_API_BASE}/api/embed",
            json={"model": ollama_model, "input": text},
            timeout=60
        )
        resp.raise_for_status()
        return resp.json()["embeddings"][0]

    emb_res = litellm.embedding(model=model, input=[text], num_retries=3)
    return emb_res.data[0]["embedding"]
