import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

API_BASE = "http://backend:8000"
# Modo local: se rodar fora do Docker, usa localhost
if os.getenv("LOCAL_DEV", "false").lower() == "true":
    API_BASE = "http://localhost:8000"

API_URL = f"{API_BASE}/api/match/stream"
API_URL_SYNC = f"{API_BASE}/api/match"
HEALTH_URL = f"{API_BASE}/api/health"
LOGIN_URL = f"{API_BASE}/api/auth/login"
ME_URL = f"{API_BASE}/api/auth/me"
FEEDBACK_URL = f"{API_BASE}/api/feedback"
CONVERSATIONS_URL = f"{API_BASE}/api/conversations"
USERS_URL = f"{API_BASE}/api/auth/users"
MODELS_URL = f"{API_BASE}/api/models"

# `page_icon` aceita caminho de arquivo além de emoji — o símbolo da marca
# substitui o 🎯 placeholder na aba do navegador e no atalho do PWA.
st.set_page_config(
    page_title="PU Matcher - Consultor Técnico de Produtos",
    page_icon=os.path.join(STATIC_DIR, "favicon.png"),
    layout="wide"
)


# ---------------------------------------------------------------------------
# Identidade visual (docs/../IDENTIDADE_VISUAL.md — paleta FIDC/Grupo Flexível)
#
# Streamlit não é Tailwind: as cores dos widgets prontos (botões, inputs)
# vêm do [theme] em .streamlit/config.toml (equivalente ao tailwind.config.ts
# do projeto de origem); o que sobra — tipografia Roboto e o estilo de card
# ("bg-white rounded-lg shadow-sm border" no guia) — não tem hook nativo no
# Streamlit, por isso é CSS injetado aqui.
# ---------------------------------------------------------------------------
def _inject_brand_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');

        /* Só no .stApp, sem "*" e sem !important: font-family é herdado por
        padrão, então isso já cobre todo texto comum via cascata. Um seletor
        universal com !important sobrescreveria também as fontes de ícone
        (olho da senha, menu hamburguer, setas) que o Streamlit/BaseWeb
        aplicam via font-family própria — resultado: ícone vira texto cru
        ("visibility", "menu") em vez do glifo. Manter simples aqui é o que
        deixa os ícones intactos. */
        .stApp {
            font-family: 'Roboto', 'Segoe UI', Arial, sans-serif;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


_inject_brand_css()


# ---------------------------------------------------------------------------
# Título com o símbolo da marca
# ---------------------------------------------------------------------------
# URL servida pelo próprio Streamlit (`enableStaticServing = true` em
# .streamlit/config.toml), não um data URI em base64.
#
# A primeira versão embutia o PNG como `data:image/png;base64,...`. Funciona em
# tese, mas depende de o sanitizador de HTML do Streamlit (DOMPurify) aceitar
# data URI em `<img>` — uma regra que muda entre versões e falha em SILÊNCIO:
# a tag some, o cabeçalho aparece sem ícone nenhum, e não há erro em lugar
# algum para explicar. A URL estática não depende disso, cacheia normalmente
# pelo navegador e dispensa ler e codificar o arquivo a cada execução.
_URL_ICONE = "/app/static/favicon.png"


def _titulo_com_icone(texto: str) -> None:
    """Cabeçalho de seção com o símbolo do Grupo Flexível no lugar do emoji.

    Por que `<img>` inline e não `st.image` numa coluna ao lado: o ícone
    precisa ficar na MESMA linha do texto e alinhado ao meio dele. `st.columns`
    põe os dois em blocos irmãos, o alinhamento vertical fica à mercê da altura
    da linha, e no celular as colunas colapsam uma sobre a outra — jogando o
    ícone para cima do título."""
    st.markdown(
        f'<h3 style="display:flex;align-items:center;gap:10px;margin:0 0 .4rem 0;">'
        f'<img src="{_URL_ICONE}" alt="" style="height:26px;width:26px;flex:none;">'
        f'<span>{texto}</span></h3>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Card de instalação do PWA — só aparece na tela de login (ver bloco abaixo).
#
# Por que um componente HTML (iframe) e não st.markdown: precisamos rodar
# JavaScript de verdade (manifest, Service Worker, evento beforeinstallprompt),
# e st.markdown(unsafe_allow_html=True) não executa <script>. O iframe de
# components.v1.html roda com acesso same-origin ao app pai (documentado na
# própria API do Streamlit), então o script alcança window.parent para
# registrar o Service Worker no escopo "/" da aplicação real — não do iframe.
#
# O Service Worker raiz só existe por trás do proxy Caddy (proxy/Caddyfile);
# sem ele, o Chrome nunca dispara beforeinstallprompt e o botão fica
# permanentemente desabilitado com uma dica em vez de travar mudo.
# ---------------------------------------------------------------------------
def _render_pwa_install_card():
    components.html(
        """
        <div id="pu-install-card" class="pu-install-card">
          <div class="pu-install-text">
            <strong>📲 Instale o PU Matcher</strong>
            <div id="pu-install-hint">Verificando disponibilidade neste navegador…</div>
          </div>
          <button id="pu-install-btn" disabled>Verificando…</button>
        </div>
        <style>
          body { margin: 0; }
          .pu-install-card {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 14px;
            background: #FFFFFF;
            border: 1px solid #DDE3EA;
            border-radius: 10px;
            box-shadow: 0 1px 2px rgba(45,58,74,0.06);
            padding: 14px 18px;
            font-family: 'Roboto','Segoe UI',Arial,sans-serif;
            color: #2D3A4A;
          }
          .pu-install-text strong { font-size: 14px; }
          #pu-install-hint { font-size: 12.5px; color: #7A8FA6; margin-top: 2px; }
          #pu-install-btn {
            font-family: inherit;
            font-size: 13px;
            font-weight: 500;
            color: #FFFFFF;
            background: #0F7C70;
            border: none;
            border-radius: 6px;
            padding: 8px 14px;
            cursor: pointer;
            white-space: nowrap;
          }
          #pu-install-btn:hover:not(:disabled) { background: #14534D; }
          #pu-install-btn:disabled { background: #B7C2CC; cursor: default; }
        </style>
        <script>
        (function () {
          var win = window.parent;
          if (!win || !win.document) { return; }

          // Estado compartilhado entre reruns do Streamlit — cada rerun recria
          // este iframe do zero, mas o Service Worker/listener só deve ser
          // registrado uma vez por carregamento real da página.
          if (!win.__puInstallState) {
            win.__puInstallState = { event: null, installed: false };
          }

          function notify() {
            try { win.dispatchEvent(new Event("pu-install-state-changed")); }
            catch (e) {}
          }

          if (!win.__puPwaInitialized) {
            win.__puPwaInitialized = true;

            try {
              var head = win.document.head;
              if (!win.document.getElementById("pu-manifest-link")) {
                var link = win.document.createElement("link");
                link.id = "pu-manifest-link";
                link.rel = "manifest";
                link.href = "/app/static/manifest.json";
                head.appendChild(link);
              }
              if (!win.document.getElementById("pu-theme-color-meta")) {
                var meta = win.document.createElement("meta");
                meta.id = "pu-theme-color-meta";
                meta.name = "theme-color";
                meta.content = "#0C3B38";
                head.appendChild(meta);
              }
            } catch (e) {}

            try {
              if (win.navigator.serviceWorker) {
                win.navigator.serviceWorker
                  .register("/service-worker.js", { scope: "/" })
                  .catch(function () {});
              }
            } catch (e) {}

            try {
              win.addEventListener("beforeinstallprompt", function (e) {
                e.preventDefault();
                win.__puInstallState.event = e;
                notify();
              });
              win.addEventListener("appinstalled", function () {
                win.__puInstallState.installed = true;
                win.__puInstallState.event = null;
                notify();
              });
            } catch (e) {}
          }

          function render() {
            var card = document.getElementById("pu-install-card");
            var btn = document.getElementById("pu-install-btn");
            var hint = document.getElementById("pu-install-hint");
            if (!card || !btn || !hint) { return; }

            var standalone = false;
            try { standalone = win.matchMedia("(display-mode: standalone)").matches; }
            catch (e) {}

            var state = win.__puInstallState;
            if (state.installed || standalone) {
              card.style.display = "none";
              return;
            }
            card.style.display = "flex";
            if (state.event) {
              btn.disabled = false;
              btn.textContent = "Instalar aplicativo";
              hint.textContent = "Acesso rápido direto da tela inicial, sem abrir o navegador.";
            } else {
              btn.disabled = true;
              btn.textContent = "Instalação indisponível";
              hint.textContent = "Disponível em Chrome/Edge (computador ou Android) quando o app atender aos critérios do navegador.";
            }
          }

          var btnEl = document.getElementById("pu-install-btn");
          if (btnEl) {
            btnEl.addEventListener("click", function () {
              var state = win.__puInstallState;
              if (!state.event) { return; }
              state.event.prompt();
              state.event.userChoice.finally(function () {
                state.event = null;
                notify();
              });
            });
          }

          win.addEventListener("pu-install-state-changed", render);
          render();
        })();
        </script>
        """,
        height=84,
    )


# ---------------------------------------------------------------------------
# Estado da Sessão
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "template_id" not in st.session_state:
    st.session_state.template_id = "proposta_tecnica_completa"
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "current_user" not in st.session_state:
    st.session_state.current_user = None
if "active_conversation_id" not in st.session_state:
    st.session_state.active_conversation_id = None
if "pagina" not in st.session_state:
    st.session_state.pagina = "chat"


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _auth_headers() -> dict:
    return _bearer(st.session_state.access_token)


def _fazer_logout():
    """Único lugar que limpa a sessão — todo 401 deve chamar isto, não
    reimplementar os mesmos três `session_state = None/[]` na mão."""
    st.session_state.access_token = None
    st.session_state.current_user = None
    st.session_state.messages = []
    st.session_state.active_conversation_id = None
    st.session_state.pagina = "chat"


def _clear_message_state():
    st.session_state.messages = []
    for key in list(st.session_state):
        if key.startswith("feedback_widget_") or key.startswith("feedback_enviado_"):
            del st.session_state[key]


def _nova_conversa():
    st.session_state.active_conversation_id = None
    _clear_message_state()


def _carregar_conversa(conversation_id: str) -> bool:
    try:
        response = requests.get(
            f"{CONVERSATIONS_URL}/{conversation_id}",
            headers=_auth_headers(),
            timeout=10,
        )
        if response.status_code == 401:
            _fazer_logout()
            return False
        if response.status_code != 200:
            return False
        data = response.json()
        _clear_message_state()
        st.session_state.active_conversation_id = data["id"]
        st.session_state.messages = [
            {
                "role": message["role"],
                "content": message["content"],
                "sources": message.get("sources") or [],
                "model_used": message.get("model_used") or "",
            }
            for message in data.get("messages", [])
        ]
        return True
    except requests.exceptions.RequestException:
        return False


def _apagar_conversa(conversation_id: str) -> bool:
    try:
        response = requests.delete(
            f"{CONVERSATIONS_URL}/{conversation_id}",
            headers=_auth_headers(),
            timeout=10,
        )
        if response.status_code == 401:
            _fazer_logout()
            return False
        if response.status_code != 204:
            return False
        if st.session_state.active_conversation_id == conversation_id:
            _nova_conversa()
        return True
    except requests.exceptions.RequestException:
        return False


def _enviar_feedback(query: str, resposta: dict, util: bool) -> bool:
    """Envia a avaliação (útil/não útil) de uma resposta já exibida.
    Pedido do usuário: não é obrigatório dar feedback, mas quando dado
    precisa ser salvo — o agente passa a consultar isso em toda pergunta
    futura (backend/app/rag/engine.py, _montar_licoes_str)."""
    try:
        resp = requests.post(
            FEEDBACK_URL,
            json={
                "query": query,
                "answer": resposta.get("content", ""),
                "util": util,
                "model_used": resposta.get("model_used"),
                "sources": resposta.get("sources") or [],
            },
            headers=_auth_headers(),
            timeout=10,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def _renderizar_feedback(idx: int, msg: dict):
    """Widget de polegar pra cima/baixo abaixo de uma resposta do agente —
    st.feedback("thumbs") devolve 0 (não útil), 1 (útil) ou None (sem
    clique ainda). Só envia UMA vez por mensagem (controlado via
    session_state, não reenvia a cada rerun do Streamlit)."""
    enviado_key = f"feedback_enviado_{idx}"
    if st.session_state.get(enviado_key):
        st.caption("✅ Obrigado pelo feedback!")
        return

    pergunta_anterior = ""
    if idx > 0 and st.session_state.messages[idx - 1]["role"] == "user":
        pergunta_anterior = st.session_state.messages[idx - 1]["content"]

    selecao = st.feedback("thumbs", key=f"feedback_widget_{idx}")
    if selecao is not None:
        util = selecao == 1
        if _enviar_feedback(pergunta_anterior, msg, util):
            st.session_state[enviado_key] = True
            st.rerun()


# ---------------------------------------------------------------------------
# Modelos de IA disponíveis
# ---------------------------------------------------------------------------
# Lista mantida pelo BACKEND (GET /api/models), não escrita aqui. Manter as
# duas em paralelo já quebrou a tela: os modelos Ollama saíram da allowlist do
# servidor, a imagem do frontend não foi reconstruída junto, e toda pergunta
# passou a devolver 422 — a tela oferecendo um modelo que o backend recusava,
# sem nenhuma pista do motivo para quem estava usando.
_MODELOS_DE_EMERGENCIA = ["gpt-4o-mini"]


@st.cache_data(ttl=300, show_spinner=False)
def _buscar_modelos_disponiveis():
    """Modelos aceitos pelo backend, na ordem em que ele os oferece.

    Em cache por 5 minutos: a lista muda de release em release, não de
    interação em interação, e sem cache seria uma chamada HTTP a cada rerun do
    Streamlit — que acontece a cada tecla digitada.

    Se o backend estiver fora do ar, cai num único modelo conhecido em vez de
    lista vazia: `st.selectbox([])` quebraria a sidebar inteira, e aí a pessoa
    perderia também o histórico e o status, não só a escolha de modelo."""
    try:
        resposta = requests.get(MODELS_URL, headers=_auth_headers(), timeout=10)
        if resposta.status_code == 200:
            modelos = resposta.json().get("models") or []
            if modelos:
                return modelos
    except requests.exceptions.RequestException:
        pass
    return _MODELOS_DE_EMERGENCIA


# ---------------------------------------------------------------------------
# Administração de usuários (Admin TI)
#
# O backend já expunha tudo em /api/auth/users desde a Fase 5, tarefa 7 — mas
# sem tela, provisionar usuário exigia CLI ou curl, e por isso o sistema rodou
# até aqui com UM usuário só. Esta é a interface daquelas rotas, nada novo de
# regra de negócio: permissão, invariante de último Admin TI e "excluir =
# desativar" continuam decididos no servidor. O frontend esconde o que o
# usuário não pode fazer; ele não é quem autoriza.
# ---------------------------------------------------------------------------
PERFIS = {
    "vendedor": "Vendedor",
    "tecnico": "Técnico de Aplicação",
    "gestor": "Gestor Comercial",
    "quimico_pd": "Químico / P&D",
    "admin_ti": "Admin TI",
}


def _rotulo_perfil(perfil: str) -> str:
    return PERFIS.get(perfil, perfil.replace("_", " ").title())


def _pode_administrar_usuarios() -> bool:
    user = st.session_state.current_user
    return bool(user) and user.get("perfil") == "admin_ti"


def _api_usuarios(metodo: str, caminho: str = "", **kwargs):
    """Chamada às rotas de administração. Devolve (ok, dados_ou_mensagem).

    Centraliza as três coisas que se repetiriam em cada botão da tela: header
    de autenticação, expiração de sessão (401 derruba o login em vez de virar
    um erro genérico) e a tradução do corpo de erro do FastAPI, que traz o
    motivo real em `detail` — é ali que chegam "email já usado", "senha fraca"
    e "não é possível desativar o último Admin TI ativo"."""
    try:
        resposta = requests.request(
            metodo, f"{USERS_URL}{caminho}", headers=_auth_headers(), timeout=15, **kwargs
        )
    except requests.exceptions.RequestException:
        return False, "Não foi possível falar com o backend."

    if resposta.status_code == 401:
        _fazer_logout()
        st.rerun()
    if resposta.status_code == 403:
        return False, "Seu perfil não tem permissão para administrar usuários."
    if 200 <= resposta.status_code < 300:
        return True, (resposta.json() if resposta.content else None)
    try:
        detalhe = resposta.json().get("detail")
    except ValueError:
        detalhe = None
    if isinstance(detalhe, list):  # erro de validação do Pydantic
        detalhe = "; ".join(item.get("msg", "") for item in detalhe)
    return False, detalhe or f"Erro {resposta.status_code}."


def _render_form_novo_usuario():
    with st.form("form_novo_usuario", border=False, clear_on_submit=True):
        col_a, col_b = st.columns(2)
        username = col_a.text_input("Usuário (login)", placeholder="nome.sobrenome")
        nome = col_b.text_input("Nome completo")
        col_c, col_d = st.columns(2)
        email = col_c.text_input("E-mail", placeholder="nome@grupoflexivel.com.br")
        perfil = col_d.selectbox(
            "Perfil", options=list(PERFIS), format_func=_rotulo_perfil,
            help="Define o que a pessoa enxerga: custo industrial e laudo completo, "
                 "por exemplo, só aparecem para os perfis autorizados.",
        )
        senha = st.text_input("Senha inicial", type="password")
        st.caption("A pessoa entra com essa senha; troque depois em 'Redefinir senha'.")

        if st.form_submit_button("Cadastrar usuário", type="primary", use_container_width=True):
            if not (username and nome and email and senha):
                st.error("Preencha usuário, nome, e-mail e senha.")
                return
            ok, retorno = _api_usuarios("POST", json={
                "username": username, "nome": nome, "email": email,
                "password": senha, "perfil": perfil,
            })
            if ok:
                st.success("Usuário cadastrado.")
                st.rerun()
            else:
                st.error(retorno)


def _render_cartao_usuario(usuario: dict, eu_mesmo: bool):
    ativo = usuario["status"] == "ativo"
    with st.container(border=True):
        cabecalho, acao = st.columns([5, 1])
        with cabecalho:
            marcador = "" if ativo else " · :red[desativado]"
            sufixo = " · :grey[(você)]" if eu_mesmo else ""
            st.markdown(f"**{usuario['nome']}**{sufixo}{marcador}")
            st.caption(
                f"`{usuario['username']}` · {usuario['email']} · "
                f"{_rotulo_perfil(usuario['perfil'])}"
            )
        with acao:
            if eu_mesmo:
                # O backend recusa a autodesativação; esconder o botão evita
                # oferecer uma ação que só pode terminar em erro.
                st.caption("—")
            elif ativo:
                if st.button("Desativar", key=f"off_{usuario['id']}", use_container_width=True):
                    ok, retorno = _api_usuarios("POST", f"/{usuario['id']}/deactivate")
                    if ok:
                        st.rerun()
                    st.error(retorno)
            else:
                if st.button(
                    "Reativar", key=f"on_{usuario['id']}", type="primary",
                    use_container_width=True,
                ):
                    ok, retorno = _api_usuarios("POST", f"/{usuario['id']}/activate")
                    if ok:
                        st.rerun()
                    st.error(retorno)

        with st.expander("Editar"):
            with st.form(f"form_editar_{usuario['id']}", border=False):
                col_a, col_b, col_c = st.columns([2, 2, 1.4])
                nome = col_a.text_input("Nome", value=usuario["nome"], key=f"n_{usuario['id']}")
                email = col_b.text_input("E-mail", value=usuario["email"], key=f"e_{usuario['id']}")
                perfil = col_c.selectbox(
                    "Perfil", options=list(PERFIS), format_func=_rotulo_perfil,
                    index=list(PERFIS).index(usuario["perfil"]) if usuario["perfil"] in PERFIS else 0,
                    key=f"p_{usuario['id']}",
                )
                if st.form_submit_button("Salvar alterações"):
                    ok, retorno = _api_usuarios("PATCH", f"/{usuario['id']}", json={
                        "nome": nome, "email": email, "perfil": perfil,
                    })
                    if ok:
                        st.rerun()
                    st.error(retorno)

            with st.form(f"form_senha_{usuario['id']}", border=False, clear_on_submit=True):
                nova_senha = st.text_input("Nova senha", type="password", key=f"s_{usuario['id']}")
                if st.form_submit_button("Redefinir senha"):
                    if not nova_senha:
                        st.error("Informe a nova senha.")
                    else:
                        ok, retorno = _api_usuarios(
                            "POST", f"/{usuario['id']}/password",
                            json={"new_password": nova_senha},
                        )
                        if ok:
                            st.success("Senha redefinida.")
                        else:
                            st.error(retorno)


def _render_pagina_usuarios():
    st.markdown("### 👥 Usuários e perfis")
    st.caption(
        "Cadastre quem vai acessar o PU Matcher e defina o perfil de cada um. "
        "Desativar preserva o histórico da pessoa — a conta nunca é apagada."
    )

    ok, usuarios = _api_usuarios("GET")
    if not ok:
        st.error(usuarios)
        return

    ativos = [u for u in usuarios if u["status"] == "ativo"]
    aba_lista, aba_novo = st.tabs(
        [f"Cadastrados ({len(ativos)} ativos)", "Cadastrar usuário"]
    )
    with aba_novo:
        _render_form_novo_usuario()
    with aba_lista:
        eu = (st.session_state.current_user or {}).get("id")
        for usuario in usuarios:
            _render_cartao_usuario(usuario, eu_mesmo=usuario["id"] == eu)


# ---------------------------------------------------------------------------
# Portão de login — nada abaixo deste bloco roda sem token válido em sessão.
# Backend (Fase 5, tarefa 5) passou a exigir autenticação em /api/match e
# companhia; sem isto o frontend simplesmente parou de funcionar.
# ---------------------------------------------------------------------------
if not st.session_state.access_token:
    # Card centralizado com componentes nativos do Streamlit (st.container
    # border=True + st.columns), não HTML/CSS solto: um <div> aberto via
    # st.markdown não "abraça" os widgets que vêm depois — cada st.xxx é um
    # elemento irmão, não filho, então não dava pra fazer um card de verdade
    # dessa forma (essa lacuna, mais o !important genérico que quebrou os
    # ícones do BaseWeb, foi o motivo da tela ter ficado feia na 1ª tentativa).
    # st.columns colapsa para largura cheia em telas estreitas (comportamento
    # nativo do grid do Streamlit) — é o que dá a responsividade no celular
    # sem precisar de media query escrita à mão.
    _, login_col, _ = st.columns([1, 1.3, 1])
    with login_col:
        with st.container(border=True):
            # Logo horizontal (com o nome da marca) na entrada; o símbolo
            # sozinho fica para a sidebar, onde não há espaço para o nome.
            st.image(os.path.join(STATIC_DIR, "logo.png"), width=240)
            st.markdown("## PU Matcher")
            st.caption("Consultor Técnico de Vendas & Match de Produtos de Poliuretano")
            _render_pwa_install_card()
            with st.form("login_form", border=False):
                username = st.text_input("Usuário")
                password = st.text_input("Senha", type="password")
                submitted = st.form_submit_button(
                    "Entrar", type="primary", use_container_width=True
                )
                if submitted:
                    try:
                        login_resp = requests.post(
                            LOGIN_URL, json={"username": username, "password": password}, timeout=10
                        )
                        if login_resp.status_code == 200:
                            token = login_resp.json()["access_token"]
                            me_resp = requests.get(ME_URL, headers=_bearer(token), timeout=10)
                            st.session_state.access_token = token
                            st.session_state.current_user = me_resp.json() if me_resp.status_code == 200 else None
                            st.rerun()
                        else:
                            st.error("Usuário ou senha incorretos.")
                    except requests.exceptions.ConnectionError:
                        st.error(
                            "❌ Não foi possível conectar ao backend. "
                            "Verifique se os containers estão rodando com `docker-compose up -d`."
                        )
                    except Exception as e:
                        st.error(f"Erro inesperado: {e}")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    # Símbolo da marca (o "X" do Grupo Flexível). Substituiu em 2026-09-09 o
    # ícone placeholder que existia até os arquivos oficiais serem fornecidos.
    st.image(os.path.join(STATIC_DIR, "icon-192.png"), width=52)
    st.title("PU Matcher")
    st.caption("Agente Investigativo para Match de Produtos de Poliuretano")

    # --- Usuário logado ---
    user = st.session_state.current_user
    if user:
        st.caption(f"👤 **{user['nome']}** · {user['perfil'].replace('_', ' ').title()}")
    if st.button("🚪 Sair", use_container_width=True):
        _fazer_logout()
        st.rerun()

    # Administração só aparece para quem pode administrar. Isto é conveniência
    # de interface, não segurança: quem forjar a chamada esbarra em
    # Permission.MANAGE_USERS no backend, que é onde a decisão mora.
    if _pode_administrar_usuarios():
        if st.session_state.pagina == "chat":
            if st.button(
                "Usuários e perfis", icon=":material/group:", use_container_width=True
            ):
                st.session_state.pagina = "usuarios"
                st.rerun()
        else:
            if st.button(
                "Voltar ao chat", icon=":material/arrow_back:",
                type="primary", use_container_width=True,
            ):
                st.session_state.pagina = "chat"
                st.rerun()

    st.divider()

    # --- Histórico de conversas ---
    st.subheader("Conversas")
    if st.button(
        "Nova conversa",
        icon=":material/add:",
        use_container_width=True,
        type="primary",
    ):
        _nova_conversa()
        st.rerun()

    conversations = []
    try:
        conversations_response = requests.get(
            CONVERSATIONS_URL, headers=_auth_headers(), timeout=10
        )
        if conversations_response.status_code == 401:
            _fazer_logout()
            st.rerun()
        elif conversations_response.status_code == 200:
            conversations = conversations_response.json()
        else:
            st.caption("Histórico indisponível.")
    except requests.exceptions.RequestException:
        st.caption("Histórico indisponível.")

    for conversation in conversations:
        conversation_id = conversation["id"]
        title_col, delete_col = st.columns([5, 1])
        with title_col:
            if st.button(
                conversation["title"],
                key=f"open_conversation_{conversation_id}",
                icon=":material/chat_bubble:" if st.session_state.active_conversation_id == conversation_id else ":material/chat_bubble_outline:",
                type="primary" if st.session_state.active_conversation_id == conversation_id else "secondary",
                use_container_width=True,
            ):
                if _carregar_conversa(conversation_id):
                    st.rerun()
                else:
                    st.error("Não foi possível carregar a conversa.")
        with delete_col:
            if st.button(
                "",
                key=f"delete_conversation_{conversation_id}",
                icon=":material/delete:",
                help=f"Excluir conversa: {conversation['title']}",
            ):
                if _apagar_conversa(conversation_id):
                    st.rerun()
                else:
                    st.error("Não foi possível excluir a conversa.")

    st.divider()

    # --- Status do backend ---
    st.subheader("🔌 Status do Sistema")
    try:
        health_resp = requests.get(HEALTH_URL, timeout=4)
        if health_resp.status_code == 200:
            health = health_resp.json()
            qdrant_ok = "online" in str(health.get("qdrant", ""))
            points = health.get("collection", {}).get("points_count", 0)
            st.success(f"Backend ✅ | Qdrant {'✅' if qdrant_ok else '⚠️'}")
            if qdrant_ok:
                if points and int(points) > 0:
                    st.caption(f"📚 {points} trechos de TDS indexados")
                else:
                    st.warning("Base vetorial vazia — execute a ingestão.")
        else:
            st.error("Backend com erro de saúde.")
    except Exception:
        st.error("Backend inacessível.")

    st.divider()

    # --- Template ---
    st.subheader("📋 Padrão de Resposta (Template)")
    template_option = st.selectbox(
        "Selecione o Formato da Resposta:",
        [
            ("📊 Proposta Técnica Completa", "proposta_tecnica_completa"),
            ("⚡ Resumo Comercial Rápido (WhatsApp)", "comercial_rapido"),
            ("🔬 Parecer de Engenharia Interno", "parecer_interno_engenharia")
        ],
        format_func=lambda x: x[0]
    )
    st.session_state.template_id = template_option[1]

    st.divider()

    # --- Modelo de IA ---
    # A lista vem do backend (GET /api/models), não escrita aqui. Manter as
    # duas em paralelo já quebrou a tela: os modelos Ollama saíram da allowlist
    # do servidor, a imagem do frontend não foi reconstruída junto, e toda
    # pergunta passou a devolver 422 oferecendo um modelo que o backend
    # recusava — sem nenhuma pista do motivo para quem estava usando.
    st.subheader("⚙️ Motor de Inteligência Artificial")
    modelos = _buscar_modelos_disponiveis()
    selected_model = st.selectbox("Provedor / Modelo:", modelos, index=0)

    # --- Modo streaming ---
    use_streaming = st.toggle("⚡ Streaming de resposta", value=True,
                              help="Exibe a resposta do agente em tempo real, token a token.")


# ---------------------------------------------------------------------------
# Área Principal
# ---------------------------------------------------------------------------
# A administração ocupa a área principal inteira em vez de virar um expander
# na sidebar: são formulários e uma lista que crescem com o número de pessoas.
# `st.stop()` evita renderizar o chat por baixo — o histórico da conversa
# continua em session_state e volta intacto ao clicar em "Voltar ao chat".
if st.session_state.pagina == "usuarios":
    if not _pode_administrar_usuarios():
        st.session_state.pagina = "chat"
        st.rerun()
    _render_pagina_usuarios()
    st.stop()

_titulo_com_icone("Assistente de Vendas Técnicas &amp; Match de Produtos")
st.caption(
    "Descreva a demanda do cliente (ex: 'Cliente quer desenvolver assento de ônibus de alta densidade') "
    "para que a IA avalie os requisitos e localize produtos existentes no catálogo."
)

# Exibe histórico de mensagens
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "sources" in msg and msg["sources"]:
            st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(msg['sources'])}")
        if "model_used" in msg and msg["model_used"]:
            st.caption(f"🤖 Modelo: `{msg['model_used']}`")
        if msg["role"] == "assistant":
            _renderizar_feedback(idx, msg)

# ---------------------------------------------------------------------------
# Input e processamento
# ---------------------------------------------------------------------------
if prompt := st.chat_input("Digite a demanda ou responda às perguntas do agente..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    payload = {
        "query": prompt,
        "template_id": st.session_state.template_id,
        "model_name": selected_model,
        "conversation_id": st.session_state.active_conversation_id,
        "history": [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.messages[:-1]
        ]
    }

    with st.chat_message("assistant"):
        if use_streaming:
            _stream_state = {
                "sources": [],
                "model": selected_model,
                "answer": "",
                "expired": False,
            }

            def _token_generator():
                """Consome o stream NDJSON e yield apenas os tokens de texto."""
                try:
                    with requests.post(
                        API_URL, json=payload, headers=_auth_headers(), stream=True, timeout=240
                    ) as resp:
                        if resp.status_code == 401:
                            _stream_state["expired"] = True
                            yield "\n\n🔒 Sua sessão expirou. Recarregue a página e faça login de novo."
                            return
                        resp.raise_for_status()
                        for raw_line in resp.iter_lines():
                            if not raw_line:
                                continue
                            try:
                                event = json.loads(raw_line)
                            except json.JSONDecodeError:
                                continue

                            if event["type"] == "meta":
                                _stream_state["sources"] = event.get("sources", [])
                                _stream_state["model"] = event.get("model_used", selected_model)
                                st.session_state.active_conversation_id = event.get(
                                    "conversation_id", st.session_state.active_conversation_id
                                )
                            elif event["type"] == "delta":
                                token = event.get("content", "")
                                _stream_state["answer"] += token
                                yield token
                            elif event["type"] == "error":
                                yield f"\n\n❌ Erro: {event.get('message', 'desconhecido')}"
                            elif event["type"] == "done":
                                break
                except requests.exceptions.ConnectionError:
                    yield (
                        "\n\n❌ Não foi possível conectar ao backend. "
                        "Verifique se os containers estão rodando com `docker-compose up -d`."
                    )
                except requests.exceptions.Timeout:
                    yield "\n\n⏱️ Tempo limite excedido aguardando resposta do agente."
                except Exception as e:
                    yield f"\n\n❌ Erro inesperado: {e}"

            st.write_stream(_token_generator())

            if _stream_state["expired"]:
                _fazer_logout()
                st.rerun()

            if _stream_state["sources"]:
                st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(_stream_state['sources'])}")
            st.caption(f"🤖 Modelo: `{_stream_state['model']}`")

            st.session_state.messages.append({
                "role": "assistant",
                "content": _stream_state["answer"],
                "sources": _stream_state["sources"],
                "model_used": _stream_state["model"]
            })
            _renderizar_feedback(len(st.session_state.messages) - 1, st.session_state.messages[-1])

        else:
            # Modo síncrono (fallback sem streaming)
            with st.spinner("Analisando requisitos técnicos e cruzando catálogo de produtos..."):
                try:
                    response = requests.post(API_URL_SYNC, json=payload, headers=_auth_headers(), timeout=240)
                    if response.status_code == 401:
                        _fazer_logout()
                        st.error("🔒 Sua sessão expirou. Recarregue a página e faça login de novo.")
                        st.rerun()
                    elif response.status_code == 200:
                        data = response.json()
                        st.session_state.active_conversation_id = data.get(
                            "conversation_id", st.session_state.active_conversation_id
                        )
                        st.markdown(data["answer"])
                        if data.get("sources"):
                            st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(data['sources'])}")
                        if data.get("model_used"):
                            st.caption(f"🤖 Modelo: `{data['model_used']}`")
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": data["answer"],
                            "sources": data.get("sources", []),
                            "model_used": data.get("model_used", "")
                        })
                        _renderizar_feedback(len(st.session_state.messages) - 1, st.session_state.messages[-1])
                    else:
                        st.error(f"Erro na resposta da API ({response.status_code}): {response.text}")
                except requests.exceptions.ConnectionError:
                    st.error(
                        "❌ Não foi possível conectar ao backend. "
                        "Verifique se os containers estão rodando com `docker-compose up -d`."
                    )
                except Exception as e:
                    st.error(f"Erro inesperado: {e}")
