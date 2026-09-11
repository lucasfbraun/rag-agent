import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import os
from urllib.parse import quote

from chat_upload import EXTENSOES_DOCUMENTO, enviar_anexos_chat, separar_entrada_chat

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
DOCUMENTOS_URL = f"{API_BASE}/api/documentos"
PERFIS_URL = f"{API_BASE}/api/auth/perfis"
TREINAMENTO_URL = f"{API_BASE}/api/treinamento"

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

# Avatares do chat. Os padrões do `st.chat_message` são um boneco VERMELHO para
# o usuário e um AMARELO para o assistente — as duas únicas cores fortes da
# tela sem relação com a identidade visual, que é verde. O agente usa o próprio
# símbolo da marca; o vendedor, a contraparte em verde-petróleo.
# Aqui é caminho de arquivo (o `avatar` do chat_message aceita qualquer coisa
# que o `st.image` aceite), não a URL estática usada no cabeçalho.
_AVATARES = {
    "user": os.path.join(STATIC_DIR, "avatar-usuario.png"),
    "assistant": os.path.join(STATIC_DIR, "favicon.png"),
}


def _avatar(papel: str):
    """Avatar do papel, ou None para o Streamlit decidir — um papel novo no
    futuro não pode quebrar a renderização da conversa."""
    return _AVATARES.get(papel)


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
# sem ele, o Chrome nunca dispara beforeinstallprompt e o card permanece
# oculto, sem ocupar espaço na tela de login com uma ação indisponível.
# ---------------------------------------------------------------------------
def _render_pwa_install_card():
    components.html(
        """
        <div id="pu-install-card" class="pu-install-card">
          <div class="pu-install-text">
            <strong>📲 Instalar PU Matcher</strong>
            <div class="pu-install-hint">Acesso rápido pela tela inicial</div>
          </div>
          <button id="pu-install-btn">Instalar</button>
        </div>
        <style>
          body { margin: 0; }
          .pu-install-card {
            display: none;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            background: #FFFFFF;
            border: 1px solid #DDE3EA;
            border-radius: 10px;
            box-shadow: 0 1px 2px rgba(45,58,74,0.06);
            padding: 9px 12px;
            font-family: 'Roboto','Segoe UI',Arial,sans-serif;
            color: #2D3A4A;
          }
          .pu-install-text { min-width: 0; }
          .pu-install-text strong { font-size: 13.5px; white-space: nowrap; }
          .pu-install-hint {
            font-size: 11.5px;
            color: #7A8FA6;
            margin-top: 1px;
            white-space: nowrap;
          }
          #pu-install-btn {
            font-family: inherit;
            font-size: 12.5px;
            font-weight: 600;
            color: #FFFFFF;
            background: #0F7C70;
            border: none;
            border-radius: 6px;
            padding: 7px 14px;
            cursor: pointer;
            white-space: nowrap;
          }
          #pu-install-btn:hover:not(:disabled) { background: #14534D; }
          @media (max-width: 430px) {
            .pu-install-hint { display: none; }
          }
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
            if (!card || !btn) { return; }

            function setVisible(visible) {
              card.style.display = visible ? "flex" : "none";
              try {
                window.frameElement.style.height = visible ? "56px" : "0px";
              } catch (e) {}
            }

            var standalone = false;
            try { standalone = win.matchMedia("(display-mode: standalone)").matches; }
            catch (e) {}

            var state = win.__puInstallState;
            if (state.installed || standalone) {
              setVisible(false);
              return;
            }
            if (state.event) {
              setVisible(true);
            } else {
              setVisible(false);
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
        height=56,
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
        if (
            key.startswith("feedback_widget_")
            or key.startswith("feedback_enviado_")
            or key.startswith("feedback_util_")
            or key.startswith("correcao_enviada_")
        ):
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


def _renderizar_form_correcao(idx: int, pergunta: str, msg: dict):
    if st.session_state.get(f"correcao_enviada_{idx}"):
        st.success("Correção enviada para aprovação da equipe técnica.")
        return
    with st.expander("Ensinar a resposta correta", expanded=True):
        st.caption(
            "A correção só passa a influenciar o agente depois de aprovada. "
            "Informe o escopo para ela não ser aplicada a outro caso."
        )
        with st.form(f"form_correcao_{idx}", clear_on_submit=True):
            correta = st.text_area(
                "Qual seria a resposta correta?", key=f"correta_{idx}", max_chars=8000,
            )
            produto = st.text_input(
                "Produto ou código relacionado (opcional)", key=f"produto_correcao_{idx}",
                help="Preencha quando a correção vale para um produto específico.",
            )
            aplicacao = st.text_input(
                "Aplicação ou condição (opcional)", key=f"aplicacao_correcao_{idx}",
            )
            fonte = st.text_input(
                "Fonte da correção (opcional)", key=f"fonte_correcao_{idx}",
                placeholder="ex: Boletim FLEXX AG 2032 rev. 03, seção Aplicação",
            )
            if st.form_submit_button("Enviar correção para aprovação", type="primary"):
                if len(correta.strip()) < 10:
                    st.error("Descreva a resposta correta com pelo menos 10 caracteres.")
                    return
                ok, retorno = _api_treinamento("POST", json={
                    "tipo": "correcao",
                    "pergunta": pergunta,
                    "resposta": correta.strip(),
                    "resposta_original": msg.get("content", ""),
                    "produto": produto.strip() or None,
                    "aplicacao": aplicacao.strip() or None,
                    "fonte": fonte.strip() or None,
                })
                if ok:
                    st.session_state[f"correcao_enviada_{idx}"] = True
                    st.rerun()
                st.error(retorno)


def _renderizar_feedback(idx: int, msg: dict):
    """Widget de polegar pra cima/baixo abaixo de uma resposta do agente —
    st.feedback("thumbs") devolve 0 (não útil), 1 (útil) ou None (sem
    clique ainda). Só envia UMA vez por mensagem (controlado via
    session_state, não reenvia a cada rerun do Streamlit)."""
    enviado_key = f"feedback_enviado_{idx}"
    if st.session_state.get(enviado_key):
        st.caption("✅ Obrigado pelo feedback!")
        if (
            st.session_state.get(f"feedback_util_{idx}") is False
            and _pode_treinar_agente()
        ):
            pergunta = ""
            if idx > 0 and st.session_state.messages[idx - 1]["role"] == "user":
                pergunta = st.session_state.messages[idx - 1]["content"]
            _renderizar_form_correcao(idx, pergunta, msg)
        return

    pergunta_anterior = ""
    if idx > 0 and st.session_state.messages[idx - 1]["role"] == "user":
        pergunta_anterior = st.session_state.messages[idx - 1]["content"]

    selecao = st.feedback("thumbs", key=f"feedback_widget_{idx}")
    if selecao is not None:
        util = selecao == 1
        if _enviar_feedback(pergunta_anterior, msg, util):
            st.session_state[enviado_key] = True
            st.session_state[f"feedback_util_{idx}"] = util
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
# Os perfis vêm do BACKEND, não de um dicionário aqui.
#
# Até 2026-09-10 esta lista era fixa, o que fazia sentido enquanto perfil era
# um enum. Com perfis criáveis pela tela, um dicionário fixo ficaria
# desatualizado no primeiro perfil novo — e é exatamente a duplicação que já
# custou caro neste projeto: a lista de modelos mantida à mão nos dois lados
# fez toda pergunta virar 422 sem pista do motivo (Sessão 35d).
def _api_perfis(metodo: str, caminho: str = "", **kwargs):
    """Chamada às rotas de perfis. Mesmo contrato de `_api_usuarios`."""
    return _chamar_api(f"{PERFIS_URL}{caminho}", metodo, **kwargs)


@st.cache_data(ttl=60, show_spinner=False)
def _perfis_disponiveis() -> list:
    """Perfis cadastrados, do backend.

    Cache curto porque a lista é lida várias vezes por render (cada cartão de
    usuário monta um selectbox) e o Streamlit reexecuta o script a cada
    interação. Toda mutação de perfil limpa o cache explicitamente — sem isso,
    um perfil recém-criado sumiria da lista por até um minuto."""
    ok, retorno = _api_perfis("GET")
    return retorno if ok and isinstance(retorno, list) else []


def _mapa_de_perfis() -> dict:
    return {p["slug"]: p for p in _perfis_disponiveis()}


def _slugs_de_perfis() -> list:
    return [p["slug"] for p in _perfis_disponiveis()]


def _rotulo_perfil(perfil: str) -> str:
    encontrado = _mapa_de_perfis().get(perfil)
    if encontrado:
        return encontrado["nome"]
    # Perfil que sumiu da lista (excluído por outro administrador enquanto esta
    # tela estava aberta): mostra o slug em vez de quebrar a renderização.
    return perfil.replace("_", " ").title()


def _tem_permissao(chave: str) -> bool:
    """Checagem por PERMISSÃO, vinda de `/api/auth/me`.

    Antes isto comparava o slug do perfil com "admin_ti" — o que ficou ERRADO
    no instante em que perfis viraram dinâmicos (Sessão 37): um perfil criado
    pela tela com a permissão de administrar não veria a tela de
    administração. O backend já autorizava corretamente; era só a interface
    que escondia o caminho.

    Isto é conveniência de interface, não autorização: quem forjar a chamada
    esbarra em `require_permission` no servidor."""
    user = st.session_state.current_user or {}
    return chave in (user.get("permissoes") or [])


def _pode_administrar_usuarios() -> bool:
    return _tem_permissao("manage_users")


def _pode_enviar_documentos() -> bool:
    return _tem_permissao("upload_documents")


def _pode_aprovar_documentos() -> bool:
    return _tem_permissao("approve_uploads")


def _pode_treinar_agente() -> bool:
    return _tem_permissao("train_agent")


def _pode_aprovar_treinamento() -> bool:
    return _tem_permissao("approve_training")


def _pode_acessar_treinamento() -> bool:
    return _pode_treinar_agente() or _pode_aprovar_treinamento()


def _api_usuarios(metodo: str, caminho: str = "", **kwargs):
    """Chamada às rotas de administração de usuários."""
    return _chamar_api(f"{USERS_URL}{caminho}", metodo, **kwargs)


def _api_treinamento(metodo: str, caminho: str = "", **kwargs):
    return _chamar_api(f"{TREINAMENTO_URL}{caminho}", metodo, **kwargs)


def _chamar_api(url: str, metodo: str, **kwargs):
    """Chamada autenticada à API. Devolve (ok, dados_ou_mensagem).

    Centraliza as três coisas que se repetiriam em cada botão da tela: header
    de autenticação, expiração de sessão (401 derruba o login em vez de virar
    um erro genérico) e a tradução do corpo de erro do FastAPI, que traz o
    motivo real em `detail` — é ali que chegam "email já usado", "senha fraca"
    e "esta mudança deixaria o sistema sem nenhum administrador ativo"."""
    try:
        resposta = requests.request(
            metodo, url, headers=_auth_headers(), timeout=15, **kwargs
        )
    except requests.exceptions.RequestException:
        return False, "Não foi possível falar com o backend."

    if resposta.status_code == 401:
        _fazer_logout()
        st.rerun()
    if resposta.status_code == 403:
        return False, "Seu perfil não tem permissão para esta ação."
    if 200 <= resposta.status_code < 300:
        return True, (resposta.json() if resposta.content else None)
    try:
        detalhe = resposta.json().get("detail")
    except ValueError:
        detalhe = None
    if isinstance(detalhe, list):  # erro de validação do Pydantic
        detalhe = "; ".join(item.get("msg", "") for item in detalhe)
    return False, detalhe or f"Erro {resposta.status_code}."


@st.cache_data(ttl=300, show_spinner=False)
def _ldap_disponivel() -> bool:
    """Se esta instalação tem Active Directory configurado.

    Em cache porque a resposta não muda entre cliques e o Streamlit reexecuta
    o script inteiro a cada interação. Na dúvida (backend fora, erro), assume
    NÃO: melhor esconder o recurso do que oferecer algo que vai falhar."""
    ok, retorno = _api_usuarios("GET", "/ldap/status")
    return bool(ok and isinstance(retorno, dict) and retorno.get("configurado"))


def _render_vinculo_ldap(usuario: dict):
    """Vínculo da conta local com uma conta do Active Directory.

    Vincular APAGA a senha local — o aviso na tela existe porque, sem ele, o
    administrador não tem como saber que a senha que ele acabou de definir vai
    deixar de valer. A regra em si é aplicada no servidor
    (user_service.vincular_ldap), aqui só é comunicada."""
    vinculado = usuario.get("origem") == "ldap"

    if vinculado:
        st.success(
            f"Autentica pelo **Active Directory** — a senha é a da rede. "
            f"Conta vinculada: `{usuario.get('external_id')}`"
        )
        with st.form(f"form_desvincular_{usuario['id']}", border=False, clear_on_submit=True):
            st.caption(
                "Desvincular devolve o login para senha local. A senha nova é "
                "obrigatória: sem ela o usuário ficaria sem forma nenhuma de entrar."
            )
            senha = st.text_input("Senha local nova", type="password", key=f"du_{usuario['id']}")
            if st.form_submit_button("Desvincular do AD"):
                if not senha:
                    st.error("Informe a senha local que passará a valer.")
                else:
                    ok, retorno = _api_usuarios(
                        "POST", f"/{usuario['id']}/unlink-ldap", json={"new_password": senha}
                    )
                    if ok:
                        st.rerun()
                    st.error(retorno)
        return

    st.caption(
        "Vincular faz o usuário entrar com a **senha da rede**. A senha local é "
        "apagada — assim, desligar a conta no AD corta o acesso aqui também."
    )
    termo = st.text_input(
        "Procurar no Active Directory", key=f"ad_busca_{usuario['id']}",
        placeholder="nome, login ou e-mail",
    )
    if len(termo.strip()) < 2:
        return

    ok, retorno = _api_usuarios("GET", f"/ldap/search?q={quote(termo.strip())}")
    if not ok:
        st.error(retorno)
        return

    contas = retorno.get("contas", [])
    if not contas:
        st.info("Nenhuma conta encontrada com esse termo.")
        return

    for conta in contas:
        coluna_dados, coluna_acao = st.columns([4, 1])
        with coluna_dados:
            st.markdown(f"**{conta['nome'] or conta['login']}** · `{conta['login']}`")
            if not conta["habilitado"]:
                st.caption(":red[Conta desabilitada no AD — não pode ser vinculada]")
            elif conta.get("email"):
                st.caption(conta["email"])
        with coluna_acao:
            # Conta desabilitada aparece na lista mas sem botão: esconder o
            # resultado faria o administrador procurar de novo sem entender.
            if conta["habilitado"] and st.button(
                "Vincular", key=f"ad_link_{usuario['id']}_{conta['external_id']}",
                use_container_width=True,
            ):
                ok, retorno = _api_usuarios(
                    "POST", f"/{usuario['id']}/link-ldap",
                    json={"external_id": conta["external_id"]},
                )
                if ok:
                    st.rerun()
                st.error(retorno)


def _render_cadastro_pelo_ad():
    """Cadastro a partir de uma conta do Active Directory.

    É o caminho preferencial quando há AD: os dados da pessoa já existem no
    diretório, então digitá-los de novo só cria oportunidade de erro de grafia
    — e um nome divergente entre os dois sistemas atrapalha auditoria depois.

    A busca fica FORA do formulário de propósito: dentro de um `st.form`, o
    Streamlit só reexecuta o script no submit, então a lista de resultados
    nunca apareceria enquanto a pessoa digita."""
    chave_escolhida = "ad_conta_escolhida"

    escolhida = st.session_state.get(chave_escolhida)
    if not escolhida:
        termo = st.text_input(
            "Procurar no Active Directory", key="ad_novo_busca",
            placeholder="nome, login ou e-mail",
        )
        if len(termo.strip()) < 2:
            st.caption("Digite ao menos 2 caracteres para procurar.")
            return

        ok, retorno = _api_usuarios("GET", f"/ldap/search?q={quote(termo.strip())}")
        if not ok:
            st.error(retorno)
            return
        contas = retorno.get("contas", [])
        if not contas:
            st.info("Nenhuma conta encontrada com esse termo.")
            return

        for conta in contas:
            coluna_dados, coluna_acao = st.columns([4, 1])
            with coluna_dados:
                st.markdown(f"**{conta['nome'] or conta['login']}** · `{conta['login']}`")
                if not conta["habilitado"]:
                    st.caption(":red[Conta desabilitada no AD]")
                else:
                    st.caption(conta.get("email") or ":orange[sem e-mail no AD]")
            with coluna_acao:
                if conta["habilitado"] and st.button(
                    "Selecionar", key=f"ad_novo_{conta['external_id']}",
                    use_container_width=True,
                ):
                    st.session_state[chave_escolhida] = conta
                    st.rerun()
        return

    st.success(f"Conta do AD: **{escolhida['nome'] or escolhida['login']}** · `{escolhida['login']}`")
    if st.button("Escolher outra conta", key="ad_novo_trocar"):
        del st.session_state[chave_escolhida]
        st.rerun()

    with st.form("form_novo_usuario_ad", border=False):
        col_a, col_b = st.columns(2)
        # Login e nome vêm do diretório, mas continuam editáveis: o
        # `displayName` do AD às vezes traz cargo ou setor junto do nome.
        username = col_a.text_input("Usuário (login)", value=escolhida["login"])
        nome = col_b.text_input("Nome completo", value=escolhida["nome"] or escolhida["login"])
        col_c, col_d = st.columns(2)
        email = col_c.text_input("E-mail", value=escolhida.get("email") or "")
        perfil = col_d.selectbox(
            "Perfil", options=_slugs_de_perfis(), format_func=_rotulo_perfil, key="ad_novo_perfil"
        )
        st.caption(
            "Sem campo de senha: esta pessoa entra com a **senha da rede**. "
            "Desligar a conta no AD corta o acesso aqui automaticamente."
        )

        if st.form_submit_button("Cadastrar com acesso pelo AD", type="primary", use_container_width=True):
            if not (username and nome and email):
                st.error("Preencha usuário, nome e e-mail.")
                return
            ok, retorno = _api_usuarios("POST", "/ldap", json={
                "external_id": escolhida["external_id"], "perfil": perfil,
                "username": username, "nome": nome, "email": email,
            })
            if ok:
                del st.session_state[chave_escolhida]
                st.success("Usuário cadastrado com acesso pelo Active Directory.")
                st.rerun()
            else:
                st.error(retorno)


def _render_form_novo_usuario():
    # Com AD disponível, o cadastro pelo diretório vem PRIMEIRO: é o caminho
    # que evita redigitar dados que já existem e que dispensa inventar uma
    # senha inicial para depois trocá-la.
    if _ldap_disponivel():
        origem = st.radio(
            "Como esta pessoa vai entrar?",
            ["Active Directory (senha da rede)", "Senha local"],
            horizontal=True, key="ad_novo_origem",
        )
        st.divider()
        if origem.startswith("Active Directory"):
            _render_cadastro_pelo_ad()
            return

    with st.form("form_novo_usuario", border=False, clear_on_submit=True):
        col_a, col_b = st.columns(2)
        username = col_a.text_input("Usuário (login)", placeholder="nome.sobrenome")
        nome = col_b.text_input("Nome completo")
        col_c, col_d = st.columns(2)
        email = col_c.text_input("E-mail", placeholder="nome@grupoflexivel.com.br")
        perfil = col_d.selectbox(
            "Perfil", options=_slugs_de_perfis(), format_func=_rotulo_perfil,
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
            origem = " · 🔗 AD" if usuario.get("origem") == "ldap" else ""
            st.caption(
                f"`{usuario['username']}` · {usuario['email']} · "
                f"{_rotulo_perfil(usuario['perfil'])}{origem}"
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
                    "Perfil", options=_slugs_de_perfis(), format_func=_rotulo_perfil,
                    index=(_slugs_de_perfis().index(usuario["perfil"])
                           if usuario["perfil"] in _slugs_de_perfis() else 0),
                    key=f"p_{usuario['id']}",
                )
                if st.form_submit_button("Salvar alterações"):
                    ok, retorno = _api_usuarios("PATCH", f"/{usuario['id']}", json={
                        "nome": nome, "email": email, "perfil": perfil,
                    })
                    if ok:
                        st.rerun()
                    st.error(retorno)

            if _ldap_disponivel():
                st.divider()
                st.markdown("**Active Directory**")
                _render_vinculo_ldap(usuario)

            # Redefinir senha não aparece para quem autentica no AD: a senha
            # local desse usuário é NULL e definir uma não teria efeito nenhum
            # no login — só confundiria quem clicasse.
            if usuario.get("origem") == "ldap":
                return

            st.divider()
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


@st.cache_data(ttl=300, show_spinner=False)
def _catalogo_de_permissoes() -> list:
    """Permissões que o sistema conhece, com rótulo legível.

    Vem do backend pelo mesmo motivo dos perfis e dos modelos: a lista muda a
    cada release que acrescenta uma permissão, e um checkbox chamado
    "manage_ingestion" não diz a ninguém o que libera."""
    ok, retorno = _api_perfis("GET", "/permissoes")
    return retorno if ok and isinstance(retorno, list) else []


def _limpar_cache_de_perfis():
    """Chamar depois de QUALQUER mutação de perfil — sem isso a lista fica
    até um minuto desatualizada e a pessoa acha que a ação não funcionou."""
    _perfis_disponiveis.clear()


def _checkboxes_de_permissoes(prefixo: str, marcadas: set) -> list:
    """Grade de permissões em duas colunas. Devolve as chaves marcadas."""
    catalogo = _catalogo_de_permissoes()
    if not catalogo:
        st.warning("Não foi possível carregar a lista de permissões.")
        return sorted(marcadas)

    escolhidas = []
    colunas = st.columns(2)
    for indice, permissao in enumerate(catalogo):
        with colunas[indice % 2]:
            if st.checkbox(
                permissao["rotulo"], value=permissao["chave"] in marcadas,
                key=f"{prefixo}_{permissao['chave']}",
            ):
                escolhidas.append(permissao["chave"])
    return escolhidas


def _render_cartao_perfil(perfil: dict):
    with st.container(border=True):
        cabecalho, contagem = st.columns([4, 1])
        with cabecalho:
            selos = []
            if perfil["administra"]:
                selos.append(":green[administrador]")
            if perfil["protegido"]:
                selos.append(":grey[do sistema]")
            st.markdown(f"**{perfil['nome']}**" + (f" · {' · '.join(selos)}" if selos else ""))
            st.caption(f"`{perfil['slug']}` · {perfil['descricao'] or 'sem descrição'}")
        with contagem:
            st.metric("usuários", perfil["usuarios"], label_visibility="visible")

        with st.expander(f"Editar permissões ({len(perfil['permissoes'])})"):
            with st.form(f"form_perfil_{perfil['id']}", border=False):
                col_a, col_b = st.columns([1, 2])
                nome = col_a.text_input("Nome", value=perfil["nome"], key=f"pn_{perfil['id']}")
                descricao = col_b.text_input(
                    "Descrição", value=perfil["descricao"] or "", key=f"pd_{perfil['id']}"
                )
                st.caption(
                    "O identificador (`slug`) não é editável: ele é usado por integração e "
                    "pelos testes, e renomeá-lo quebraria referências sem aviso."
                )
                st.divider()
                permissoes = _checkboxes_de_permissoes(
                    f"perm_{perfil['id']}", set(perfil["permissoes"])
                )
                if st.form_submit_button("Salvar", type="primary"):
                    ok, retorno = _api_perfis("PATCH", f"/{perfil['id']}", json={
                        "nome": nome, "descricao": descricao, "permissoes": permissoes,
                    })
                    if ok:
                        _limpar_cache_de_perfis()
                        st.rerun()
                    st.error(retorno)

            # Excluir fica FORA do formulário: dentro dele, o Streamlit só
            # reexecuta no submit, e o botão não teria efeito.
            if perfil["protegido"]:
                st.caption("Perfil do sistema — não pode ser excluído. As permissões acima continuam editáveis.")
            elif perfil["usuarios"]:
                st.caption(
                    f"{perfil['usuarios']} usuário(s) usam este perfil. "
                    "Mova essas pessoas para outro perfil antes de excluí-lo."
                )
            elif st.button("Excluir perfil", key=f"px_{perfil['id']}"):
                ok, retorno = _api_perfis("DELETE", f"/{perfil['id']}")
                if ok:
                    _limpar_cache_de_perfis()
                    st.rerun()
                st.error(retorno)


def _render_form_novo_perfil():
    with st.form("form_novo_perfil", border=False, clear_on_submit=True):
        col_a, col_b = st.columns([1, 2])
        slug = col_a.text_input("Identificador", placeholder="ex: supervisor")
        nome = col_b.text_input("Nome do perfil", placeholder="ex: Supervisor de Vendas")
        descricao = st.text_input("Descrição (opcional)")
        st.caption(
            "O identificador só aceita letras minúsculas, números e `_`, e **não pode ser "
            "alterado depois** — ele é a referência estável do perfil."
        )
        st.divider()
        st.markdown("**Permissões**")
        permissoes = _checkboxes_de_permissoes("novo_perfil", set())

        if st.form_submit_button("Criar perfil", type="primary", use_container_width=True):
            if not (slug and nome):
                st.error("Preencha o identificador e o nome.")
                return
            ok, retorno = _api_perfis("POST", json={
                "slug": slug.strip().lower(), "nome": nome.strip(),
                "descricao": descricao.strip() or None, "permissoes": permissoes,
            })
            if ok:
                _limpar_cache_de_perfis()
                st.success(f"Perfil **{nome}** criado.")
                st.rerun()
            else:
                st.error(retorno)


def _render_pagina_perfis():
    st.caption(
        "Perfis definem o que cada pessoa pode fazer. O sistema impede qualquer mudança "
        "que o deixe sem nenhum administrador ativo."
    )
    perfis = _perfis_disponiveis()
    if not perfis:
        st.error("Não foi possível carregar os perfis.")
        return

    aba_lista, aba_novo = st.tabs([f"Perfis ({len(perfis)})", "Criar perfil"])
    with aba_novo:
        _render_form_novo_perfil()
    with aba_lista:
        for perfil in perfis:
            _render_cartao_perfil(perfil)


def _api_documentos(metodo: str, caminho: str = "", **kwargs):
    return _chamar_api(f"{DOCUMENTOS_URL}{caminho}", metodo, **kwargs)


def _enviar_documento(arquivo, observacao: str):
    """Único contrato de envio, usado pela página de documentos e pelo chat."""
    return _api_documentos(
        "POST",
        files={"arquivo": (arquivo.name, arquivo.getvalue())},
        data={"observacao": observacao or ""},
    )


_ROTULO_STATUS = {
    "pendente": ":orange[aguardando aprovação]",
    "aprovado": ":green[no acervo]",
    "rejeitado": ":red[recusado]",
}


def _render_form_envio():
    st.caption(
        "Envie boletins, fichas técnicas ou certificados que faltam no acervo. "
        "O arquivo **não entra direto**: alguém com permissão de aprovação revisa antes, "
        "porque o que entra aqui vira fonte que o agente cita como verdade para toda a equipe."
    )
    with st.form("form_envio_documento", border=False, clear_on_submit=True):
        arquivo = st.file_uploader("Arquivo", type=list(EXTENSOES_DOCUMENTO))
        # Aviso VISÍVEL, não no tooltip do uploader: quem está prestes a mandar
        # um PDF digitalizado precisa ler isso sem passar o mouse em nada.
        st.caption(
            "PDF, Word ou texto. **Imagens e PDFs digitalizados não são aceitos** — sem "
            "OCR o texto não é extraível, e o arquivo não acrescentaria nada às respostas "
            "do agente."
        )
        observacao = st.text_area(
            "O que é este documento?", max_chars=1000,
            placeholder="ex: boletim do FLEXX AG 2032, revisão 03 — substitui a rev 02 do acervo",
        )
        st.caption("A observação é o que o aprovador lê para decidir. Sem ela, ele recebe um PDF sem contexto.")

        if st.form_submit_button("Enviar para aprovação", type="primary", use_container_width=True):
            if arquivo is None:
                st.error("Escolha um arquivo.")
                return
            ok, retorno = _enviar_documento(arquivo, observacao)
            if ok:
                st.success(f"**{arquivo.name}** enviado. Você será avisado quando for revisado.")
                st.rerun()
            else:
                st.error(retorno)


def _render_cartao_documento(documento: dict, pode_aprovar: bool):
    with st.container(border=True):
        st.markdown(
            f"**{documento['nome_arquivo']}** · {_ROTULO_STATUS.get(documento['status'], documento['status'])}"
        )
        tamanho = documento["tamanho_bytes"] / 1024
        detalhe = f"enviado por {documento['enviado_por']} · {tamanho:.0f} KB"
        if documento["status"] == "aprovado":
            detalhe += f" · {documento['chunks_indexados']} trechos no acervo"
        st.caption(detalhe)
        if documento.get("observacao"):
            st.markdown(f"> {documento['observacao']}")
        if documento.get("motivo_decisao"):
            st.caption(f"Decisão de {documento['decidido_por']}: {documento['motivo_decisao']}")

        if not pode_aprovar:
            return

        if documento["status"] == "pendente":
            aprovar, recusar = st.columns(2)
            with aprovar:
                if st.button("Aprovar e indexar", key=f"ap_{documento['id']}",
                             type="primary", use_container_width=True):
                    ok, retorno = _api_documentos("POST", f"/{documento['id']}/aprovar")
                    if ok:
                        st.rerun()
                    st.error(retorno)
            with recusar:
                with st.popover("Recusar", use_container_width=True):
                    motivo = st.text_input("Motivo", key=f"mt_{documento['id']}")
                    st.caption("Obrigatório: sem o motivo, quem enviou reenvia o mesmo arquivo.")
                    if st.button("Confirmar recusa", key=f"rc_{documento['id']}"):
                        if not motivo.strip():
                            st.error("Informe o motivo.")
                        else:
                            ok, retorno = _api_documentos(
                                "POST", f"/{documento['id']}/recusar", json={"motivo": motivo}
                            )
                            if ok:
                                st.rerun()
                            st.error(retorno)

        elif documento["status"] == "aprovado":
            with st.popover("Remover do acervo"):
                st.caption(
                    "Tira os trechos deste documento do índice. Use quando o arquivo "
                    "aprovado se mostrar errado ou desatualizado."
                )
                motivo = st.text_input("Motivo", key=f"rmt_{documento['id']}")
                if st.button("Confirmar remoção", key=f"rm_{documento['id']}"):
                    if not motivo.strip():
                        st.error("Informe o motivo.")
                    else:
                        ok, retorno = _api_documentos(
                            "POST", f"/{documento['id']}/remover", json={"motivo": motivo}
                        )
                        if ok:
                            st.rerun()
                        st.error(retorno)


def _render_pagina_documentos():
    _titulo_com_icone("Documentos do acervo")
    pode_aprovar = _pode_aprovar_documentos()

    ok, documentos = _api_documentos("GET")
    if not ok:
        st.error(documentos)
        return

    pendentes = [d for d in documentos if d["status"] == "pendente"]
    # Quem aprova vê a fila primeiro — é o que ele veio fazer. Quem só envia
    # vê o formulário primeiro, pelo mesmo motivo.
    rotulo_fila = (
        f"Fila de aprovação ({len(pendentes)})" if pode_aprovar
        else f"Meus envios ({len(documentos)})"
    )
    abas = st.tabs([rotulo_fila, "Enviar documento"] if pode_aprovar
                   else ["Enviar documento", rotulo_fila])
    aba_fila, aba_envio = (abas[0], abas[1]) if pode_aprovar else (abas[1], abas[0])

    with aba_envio:
        _render_form_envio()

    with aba_fila:
        if not documentos:
            st.info(
                "Nenhum documento na fila." if pode_aprovar
                else "Você ainda não enviou nenhum documento."
            )
            return
        if pode_aprovar and pendentes:
            st.caption(f"{len(pendentes)} aguardando decisão.")
        for documento in documentos:
            _render_cartao_documento(documento, pode_aprovar)


_ROTULO_TIPO_TREINAMENTO = {
    "correcao": "Correção de resposta",
    "conhecimento": "Conhecimento interno",
    "exemplo": "Exemplo de formato",
}


def _render_form_treinamento():
    st.caption(
        "Registre uma orientação reutilizável. Correções e conhecimento ficam pendentes "
        "até aprovação; exemplos ensinam apenas o formato da resposta."
    )
    with st.form("form_treinamento", clear_on_submit=True):
        tipo = st.selectbox(
            "O que deseja ensinar?", list(_ROTULO_TIPO_TREINAMENTO),
            format_func=lambda valor: _ROTULO_TIPO_TREINAMENTO[valor],
        )
        pergunta = st.text_area(
            "Pergunta ou título", max_chars=4000,
            placeholder="ex: Qual cola usar para rolha de cortiça?",
        )
        resposta = st.text_area("Resposta correta ou orientação", max_chars=8000)
        produto = st.text_input("Produto ou código relacionado (opcional)")
        aplicacao = st.text_input("Aplicação ou condição (opcional)")
        fonte = st.text_input(
            "Fonte (opcional)",
            placeholder="ex: Boletim FLEXX AG 2032 rev. 03, seção Aplicação",
        )
        if st.form_submit_button("Registrar ensinamento", type="primary", use_container_width=True):
            if len(pergunta.strip()) < 10 or len(resposta.strip()) < 10:
                st.error("Pergunta e resposta precisam ter pelo menos 10 caracteres.")
                return
            ok, retorno = _api_treinamento("POST", json={
                "tipo": tipo, "pergunta": pergunta.strip(), "resposta": resposta.strip(),
                "produto": produto.strip() or None,
                "aplicacao": aplicacao.strip() or None,
                "fonte": fonte.strip() or None,
            })
            if ok:
                if retorno["status"] == "aprovado":
                    st.success("Exemplo registrado e já disponível para o agente.")
                else:
                    st.success("Ensinamento enviado para aprovação.")
                st.rerun()
            st.error(retorno)


def _render_cartao_treinamento(item: dict, pode_aprovar: bool):
    with st.container(border=True):
        status = _ROTULO_STATUS.get(item["status"], item["status"])
        tipo = _ROTULO_TIPO_TREINAMENTO.get(item["tipo"], item["tipo"])
        st.markdown(f"**{tipo}** · {status}")
        st.caption(f"por {item['criado_por']} · {item['created_at'][:10]}")
        st.markdown(f"**Pergunta/título:** {item['pergunta']}")
        st.markdown(f"**Conteúdo:** {item['resposta']}")
        escopo = []
        if item.get("produto"):
            escopo.append(f"produto: {item['produto']}")
        if item.get("aplicacao"):
            escopo.append(f"aplicação: {item['aplicacao']}")
        if item.get("fonte"):
            escopo.append(f"fonte: {item['fonte']}")
        if escopo:
            st.caption(" · ".join(escopo))
        if item.get("motivo_decisao"):
            st.warning(f"Motivo da decisão: {item['motivo_decisao']}")

        if not pode_aprovar or item["status"] != "pendente":
            return
        aprovar_col, recusar_col = st.columns(2)
        with aprovar_col:
            if st.button(
                "Aprovar", key=f"treino_ap_{item['id']}", type="primary",
                use_container_width=True,
            ):
                ok, retorno = _api_treinamento("POST", f"/{item['id']}/aprovar")
                if ok:
                    st.rerun()
                st.error(retorno)
        with recusar_col:
            with st.popover("Recusar", use_container_width=True):
                motivo = st.text_input("Motivo", key=f"treino_motivo_{item['id']}")
                if st.button("Confirmar recusa", key=f"treino_rec_{item['id']}"):
                    if not motivo.strip():
                        st.error("Informe o motivo.")
                    else:
                        ok, retorno = _api_treinamento(
                            "POST", f"/{item['id']}/recusar", json={"motivo": motivo.strip()}
                        )
                        if ok:
                            st.rerun()
                        st.error(retorno)


def _render_pagina_treinamento():
    _titulo_com_icone("Treinar o agente")
    pode_treinar = _pode_treinar_agente()
    pode_aprovar = _pode_aprovar_treinamento()
    ok, itens = _api_treinamento("GET")
    if not ok:
        st.error(itens)
        return
    pendentes = [item for item in itens if item["status"] == "pendente"]
    rotulo = f"Fila de aprovação ({len(pendentes)})" if pode_aprovar else f"Meus ensinamentos ({len(itens)})"
    rotulos_abas = [rotulo]
    if pode_treinar:
        rotulos_abas = [rotulo, "Novo ensinamento"] if pode_aprovar else ["Novo ensinamento", rotulo]
    abas = st.tabs(rotulos_abas)
    indice_lista = 0 if pode_aprovar or not pode_treinar else 1
    with abas[indice_lista]:
        if not itens:
            st.info("Nenhum ensinamento registrado.")
        for item in itens:
            _render_cartao_treinamento(item, pode_aprovar)
    if pode_treinar:
        indice_novo = 1 if pode_aprovar else 0
        with abas[indice_novo]:
            _render_form_treinamento()


def _render_pagina_administracao():
    """Área de administração: usuários e perfis, em abas.

    Uma tela só porque as duas coisas se olham o tempo todo — ao criar um
    perfil você quer ver quem usa, e ao mover alguém de perfil quer conferir o
    que aquele perfil permite."""
    st.markdown("### 👥 Usuários e perfis")
    aba_usuarios, aba_perfis = st.tabs(["Usuários", "Perfis e permissões"])
    with aba_usuarios:
        _render_pagina_usuarios()
    with aba_perfis:
        _render_pagina_perfis()


def _render_pagina_usuarios():
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
    if st.session_state.pagina != "chat":
        if st.button(
            "Voltar ao chat", icon=":material/arrow_back:",
            type="primary", use_container_width=True,
        ):
            st.session_state.pagina = "chat"
            st.rerun()
    else:
        # Cada atalho aparece conforme a PERMISSÃO, não conforme o nome do
        # perfil: com perfis criáveis pela tela, amarrar a um slug esconderia
        # a funcionalidade de um perfil novo que a tem de direito.
        if _pode_enviar_documentos():
            if st.button("Documentos", icon=":material/upload_file:", use_container_width=True):
                st.session_state.pagina = "documentos"
                st.rerun()
        if _pode_acessar_treinamento():
            if st.button("Treinar agente", icon=":material/school:", use_container_width=True):
                st.session_state.pagina = "treinamento"
                st.rerun()
        if _pode_administrar_usuarios():
            if st.button("Usuários e perfis", icon=":material/group:", use_container_width=True):
                st.session_state.pagina = "usuarios"
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
# Cada página confere a própria permissão antes de desenhar: um `pagina`
# deixado em sessão (troca de usuário no mesmo navegador) não pode renderizar
# uma tela que aquele perfil não pode ver.
if st.session_state.pagina == "usuarios":
    if not _pode_administrar_usuarios():
        st.session_state.pagina = "chat"
        st.rerun()
    _render_pagina_administracao()
    st.stop()

if st.session_state.pagina == "documentos":
    if not _pode_enviar_documentos():
        st.session_state.pagina = "chat"
        st.rerun()
    _render_pagina_documentos()
    st.stop()

if st.session_state.pagina == "treinamento":
    if not _pode_acessar_treinamento():
        st.session_state.pagina = "chat"
        st.rerun()
    _render_pagina_treinamento()
    st.stop()

_titulo_com_icone("Assistente de Vendas Técnicas &amp; Match de Produtos")
st.caption(
    "Descreva a demanda do cliente (ex: 'Cliente quer desenvolver assento de ônibus de alta densidade') "
    "para que a IA avalie os requisitos e localize produtos existentes no catálogo."
)

# Exibe histórico de mensagens
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"], avatar=_avatar(msg["role"])):
        st.markdown(msg["content"])
        if "sources" in msg and msg["sources"]:
            st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(msg['sources'])}")
        if "model_used" in msg and msg["model_used"]:
            st.caption(f"🤖 Modelo: `{msg['model_used']}`")
        if msg["role"] == "assistant":
            _renderizar_feedback(idx, msg)

# ---------------------------------------------------------------------------
# Input, anexos e processamento
# ---------------------------------------------------------------------------
aceita_anexos = _pode_enviar_documentos()
if aceita_anexos:
    st.caption(
        "📎 Você pode anexar PDF, Word ou TXT aqui. O texto digitado junto vira a "
        "observação do envio, e o documento só entra no acervo depois da aprovação."
    )

entrada_chat = st.chat_input(
    "Digite a demanda ou responda às perguntas do agente...",
    accept_file="multiple" if aceita_anexos else False,
    file_type=list(EXTENSOES_DOCUMENTO) if aceita_anexos else None,
)
prompt = None
if entrada_chat:
    texto_chat, anexos = separar_entrada_chat(entrada_chat)
    if anexos:
        with st.chat_message("user", avatar=_avatar("user")):
            st.markdown(texto_chat or "Envio de documentos para o acervo")
            st.caption(" · ".join(arquivo.name for arquivo in anexos))

        enviados, falhas = enviar_anexos_chat(anexos, texto_chat, _enviar_documento)
        if enviados:
            st.success(
                f"{len(enviados)} documento(s) enviado(s) para aprovação: "
                + ", ".join(enviados)
            )
        for nome, erro in falhas:
            st.error(f"Não foi possível enviar **{nome}**: {erro}")
    elif texto_chat:
        prompt = texto_chat

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar=_avatar("user")):
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

    with st.chat_message("assistant", avatar=_avatar("assistant")):
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
