"""Otimização de vetores não mantém dados entre consultas nem cacheia falhas."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest

from app.rag.embeddings import get_embedding, reutilizar_embeddings_na_consulta


def test_chave_inclui_modelo_e_texto_e_vetor_retornado_e_independente():
    with patch("app.rag.embeddings.get_embeddings", return_value=[[1.0]]) as provider:
        with reutilizar_embeddings_na_consulta():
            get_embedding("pergunta", "modelo-a")[0] = 9.0
            assert get_embedding("pergunta", "modelo-a") == [1.0]
            get_embedding("outra pergunta", "modelo-a")
            get_embedding("pergunta", "modelo-b")
            assert provider.call_count == 3
        get_embedding("pergunta", "modelo-a")
        assert provider.call_count == 4


def test_falha_nao_fica_no_cache_e_saida_por_excecao_descarta_o_escopo():
    with patch("app.rag.embeddings.get_embeddings", side_effect=[
        RuntimeError("indisponível"), [[1.0]], [[2.0]],
    ]) as provider:
        with pytest.raises(ValueError):
            with reutilizar_embeddings_na_consulta():
                with pytest.raises(RuntimeError):
                    get_embedding("pergunta", "modelo")
                assert get_embedding("pergunta", "modelo") == [1.0]
                raise ValueError("falha posterior")
        assert get_embedding("pergunta", "modelo") == [2.0]
        assert provider.call_count == 3


def test_consultas_simultaneas_nao_compartilham_cache():
    both_started = Barrier(2, timeout=2)

    def query(_):
        with reutilizar_embeddings_na_consulta():
            both_started.wait()
            get_embedding("pergunta", "modelo")
            get_embedding("pergunta", "modelo")

    with patch("app.rag.embeddings.get_embeddings", return_value=[[1.0]]) as provider:
        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(query, range(2)))
        assert provider.call_count == 2
