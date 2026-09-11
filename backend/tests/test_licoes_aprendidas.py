"""Feedback bruto não pode se tornar instrução factual sem aprovação."""
from unittest.mock import patch

from app.rag.engine import _montar_system_instruction, AGENT_SYSTEM_PROMPT


def test_feedback_negativo_bruto_nao_entra_no_prompt():
    with patch("app.feedback_service.obter_licoes_de_feedback") as feedback:
        prompt = _montar_system_instruction("proposta_tecnica_completa")

    feedback.assert_not_called()
    assert AGENT_SYSTEM_PROMPT in prompt
    assert "LIÇÕES APRENDIDAS" not in prompt


def test_prompt_preserva_guardrails_de_correcao_explicita():
    prompt = _montar_system_instruction("proposta_tecnica_completa")
    assert "CORREÇÃO EXPLÍCITA DO USUÁRIO" in prompt
    assert "não prova que o produto É um elastômero" in prompt
