"""
Testes de restrição de campos sensíveis (Fase 5, tarefa 6, docs/spec_rbac.md).

Escopo real testável hoje: a camada ESTRUTURADA das ferramentas MCP simuladas
(custo industrial em consultar_catalogo_erp, laudo completo em
consultar_normas_homologadas) e a ponte entre a permissão do usuário logado
(app.main) e o booleano que chega até essas ferramentas.

Fora de escopo, e não fingido aqui: filtrar conteúdo sensível dentro do texto
livre já indexado no Qdrant (RAG) — não há metadado de "isto é custo/fórmula"
nos 11.273 trechos já ingeridos; pendência registrada em docs/spec_rbac.md,
não resolvida por esta tarefa.
"""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Role, User
from app.auth.user_service import create_user
from app.main import app
from app.mcp.pu_mcp_server import consultar_catalogo_erp, consultar_normas_homologadas, execute_mcp_tool


# --- camada MCP pura (sem HTTP, sem DB) -----------------------------------------

def test_catalogo_erp_sem_ver_custos_nao_inclui_custo():
    resultado = consultar_catalogo_erp("PU-SEAT-5000 FR")
    assert "custo_industrial_kg" not in resultado


def test_catalogo_erp_com_ver_custos_inclui_custo():
    resultado = consultar_catalogo_erp("PU-SEAT-5000 FR", ver_custos=True)
    assert "custo_industrial_kg" in resultado


def test_normas_sem_ver_laudo_completo_omite_numero_e_laboratorio():
    resultado = consultar_normas_homologadas("ABNT NBR 9178")
    assert "laudo_numero" not in resultado
    assert "laboratorio_emissor" not in resultado
    # Resumo continua presente — Vendedor tem VIEW_HOMOLOGATION_SUMMARY, não nada
    assert "resultado" in resultado
    assert "produtos_certificados" in resultado


def test_normas_com_ver_laudo_completo_inclui_numero_e_laboratorio():
    resultado = consultar_normas_homologadas("ABNT NBR 9178", ver_laudo_completo=True)
    assert resultado["laudo_numero"] == "CERT-2025-NBR9178"
    assert resultado["laboratorio_emissor"] == "IPT / SENAI"


def test_execute_mcp_tool_default_e_fail_closed_para_custos():
    """Quem esquecer de passar ver_custos não deve vazar custo por acidente."""
    resultado = execute_mcp_tool("consultar_catalogo_erp", {"termo_busca": "x"})
    assert "custo_industrial_kg" not in resultado


def test_execute_mcp_tool_default_e_fail_closed_para_laudo():
    resultado = execute_mcp_tool("consultar_normas_homologadas", {"norma_requerida": "x"})
    assert "laudo_numero" not in resultado


def test_execute_mcp_tool_repassa_ver_custos_true():
    resultado = execute_mcp_tool("consultar_catalogo_erp", {"termo_busca": "x"}, ver_custos=True)
    assert "custo_industrial_kg" in resultado


def test_execute_mcp_tool_repassa_ver_laudo_completo_true():
    resultado = execute_mcp_tool(
        "consultar_normas_homologadas", {"norma_requerida": "x"}, ver_laudo_completo=True
    )
    assert "laudo_numero" in resultado


# --- ponte permissão -> booleano em /api/match (HTTP + DB real) ----------------

@pytest.fixture
def created_user_ids():
    ids = []
    yield ids
    if ids:
        cleanup = SessionLocal()
        try:
            for user_id in ids:
                user = cleanup.get(User, user_id)
                if user is not None:
                    cleanup.delete(user)
            cleanup.commit()
        finally:
            cleanup.close()


@pytest.fixture
def client():
    return TestClient(app)


def _token_for(perfil: Role, created_user_ids, client) -> str:
    u = uuid.uuid4().hex[:8]
    session = SessionLocal()
    try:
        user = create_user(
            session, username=f"sens_{u}", nome="Usuário Campo Sensível",
            email=f"sens_{u}@teste.local", password="senha_segura_123", perfil=perfil,
        )
        session.commit()
        created_user_ids.append(user.id)
    finally:
        session.close()

    resp = client.post("/api/auth/login", json={"username": f"sens_{u}", "password": "senha_segura_123"})
    return resp.json()["access_token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_match_vendedor_chama_agente_com_ver_custos_false(client, created_user_ids):
    """Vendedor não tem VIEW_COSTS na matriz (docs/spec_rbac.md) — main.py deve
    computar ver_custos=False e repassar para o motor, não deixar a decisão
    pro LLM via instrução de prompt."""
    token = _token_for(Role.VENDEDOR, created_user_ids, client)
    with patch("app.main.run_pu_matcher_agent") as mock_agent:
        mock_agent.return_value = {"answer": "mock", "sources": [], "model_used": "mock"}
        resp = client.post("/api/match", json={"query": "teste"}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert mock_agent.call_args.kwargs["ver_custos"] is False
    assert mock_agent.call_args.kwargs["ver_laudo_completo"] is False


def test_match_gestor_chama_agente_com_ver_custos_true(client, created_user_ids):
    """Gestor tem VIEW_COSTS e VIEW_HOMOLOGATION_FULL na matriz."""
    token = _token_for(Role.GESTOR, created_user_ids, client)
    with patch("app.main.run_pu_matcher_agent") as mock_agent:
        mock_agent.return_value = {"answer": "mock", "sources": [], "model_used": "mock"}
        resp = client.post("/api/match", json={"query": "teste"}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert mock_agent.call_args.kwargs["ver_custos"] is True
    assert mock_agent.call_args.kwargs["ver_laudo_completo"] is True


def test_match_tecnico_ve_laudo_completo_mas_nao_custos(client, created_user_ids):
    """Técnico tem VIEW_HOMOLOGATION_FULL mas não VIEW_COSTS (pendência resolvida
    como negado por padrão, ver docs/spec_rbac.md, "Pendências", item 1)."""
    token = _token_for(Role.TECNICO, created_user_ids, client)
    with patch("app.main.run_pu_matcher_agent") as mock_agent:
        mock_agent.return_value = {"answer": "mock", "sources": [], "model_used": "mock"}
        resp = client.post("/api/match", json={"query": "teste"}, headers=_auth_header(token))
    assert resp.status_code == 200
    assert mock_agent.call_args.kwargs["ver_custos"] is False
    assert mock_agent.call_args.kwargs["ver_laudo_completo"] is True
