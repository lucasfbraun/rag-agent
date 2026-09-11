"""Cookie de sessão lembrada para atravessar o fechamento do navegador."""

import json


COOKIE_LOGIN_PERSISTENTE = "pu_matcher_remember_token"


def ler_token_persistido(cookies):
    """Lê o cookie enviado na abertura da sessão do Streamlit."""
    try:
        return cookies.get(COOKIE_LOGIN_PERSISTENTE) or None
    except (AttributeError, TypeError):
        return None


def montar_script_cookie(token=None, max_age_seconds=0, recarregar=False):
    """Gera o JS executado no navegador para gravar ou remover o cookie.

    `st.context.cookies` é somente leitura. A escrita precisa acontecer no
    navegador; o componente HTML tem o mesmo acesso ao documento pai já usado
    pelo card do PWA.
    """
    valor = json.dumps(token or "")
    max_age = int(max_age_seconds) if token else 0
    recarga = (
        "setTimeout(function () { win.location.reload(); }, 80);"
        if recarregar else ""
    )
    return f"""
    <script>
    (function () {{
      var win = window.parent;
      if (!win || !win.document) {{ return; }}
      var token = {valor};
      var secure = win.location.protocol === "https:" ? "; Secure" : "";
      win.document.cookie = "{COOKIE_LOGIN_PERSISTENTE}=" + encodeURIComponent(token)
        + "; Path=/; Max-Age={max_age}; SameSite=Strict" + secure;
      {recarga}
    }})();
    </script>
    """
