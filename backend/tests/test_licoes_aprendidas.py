"""
Pedido do usuário: "é necessário que o agente sempre consulte" o feedback
negativo (útil/não útil) — não pode ser um recurso que precisa ser
solicitado, tem que entrar em toda consulta sozinho.

Seam: `_montar_licoes_str`/`_montar_system_instruction` (app.rag.engine) com
`app.feedback_service.obter_licoes_de_feedback` mockado — testa a montagem
do prompt, não o banco (já coberto em test_feedback_service.py). O import é
tardio dentro da função (lazy, mesmo padrão de _get_qdrant_client), então o
patch alvo é o módulo de origem, não app.rag.engine.
"""
from unittest.mock import patch

from app.rag.engine import _montar_licoes_str, _montar_system_instruction, AGENT_SYSTEM_PROMPT


def test_sem_licoes_negativas_bloco_fica_vazio():
    with patch("app.feedback_service.obter_licoes_de_feedback", return_value=[]):
        assert _montar_licoes_str() == ""


def test_com_licoes_negativas_bloco_lista_a_pergunta_e_o_comentario():
    licoes = [{"query": "produtos para colchão", "comentario": "trouxe produto errado"}]
    with patch("app.feedback_service.obter_licoes_de_feedback", return_value=licoes):
        bloco = _montar_licoes_str()
    assert "LIÇÕES APRENDIDAS" in bloco
    assert "produtos para colchão" in bloco
    assert "trouxe produto errado" in bloco


def test_licao_sem_comentario_ainda_aparece_so_com_a_pergunta():
    licoes = [{"query": "produtos para automotivo", "comentario": None}]
    with patch("app.feedback_service.obter_licoes_de_feedback", return_value=licoes):
        bloco = _montar_licoes_str()
    assert "produtos para automotivo" in bloco


def test_falha_ao_consultar_licoes_nao_derruba_a_montagem_do_prompt():
    with patch("app.feedback_service.obter_licoes_de_feedback", side_effect=Exception("postgres fora do ar")):
        assert _montar_licoes_str() == ""


def test_montar_system_instruction_sempre_inclui_o_bloco_quando_ha_licoes():
    """"Sempre consulta" — não é opcional, entra em toda montagem do prompt
    de sistema, sem precisar de nenhum parâmetro extra pra ligar."""
    licoes = [{"query": "produtos para colchão", "comentario": "trouxe produto errado"}]
    with patch("app.feedback_service.obter_licoes_de_feedback", return_value=licoes):
        prompt = _montar_system_instruction("proposta_tecnica_completa")
    assert AGENT_SYSTEM_PROMPT in prompt
    assert "LIÇÕES APRENDIDAS" in prompt
    assert "trouxe produto errado" in prompt
