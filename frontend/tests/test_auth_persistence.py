from auth_persistence import (
    COOKIE_LOGIN_PERSISTENTE,
    ler_token_persistido,
    montar_script_cookie,
)


def test_le_token_do_cookie_recebido_na_abertura_da_sessao():
    assert ler_token_persistido({COOKIE_LOGIN_PERSISTENTE: "jwt-teste"}) == "jwt-teste"
    assert ler_token_persistido({}) is None


def test_script_persiste_cookie_com_prazo_e_protecao_de_contexto():
    script = montar_script_cookie("jwt-teste", max_age_seconds=3600, recarregar=True)

    assert COOKIE_LOGIN_PERSISTENTE in script
    assert "Max-Age=3600" in script
    assert "SameSite=Strict" in script
    assert "location.reload" in script


def test_script_de_logout_remove_cookie_sem_recarregar_quando_solicitado():
    script = montar_script_cookie(None, recarregar=False)

    assert "Max-Age=0" in script
    assert "location.reload" not in script
