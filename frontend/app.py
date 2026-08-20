import streamlit as st
import requests

API_URL = "http://backend:8000/api/match"
HEALTH_URL = "http://backend:8000/api/health"
TEMPLATES_URL = "http://backend:8000/api/templates"

st.set_page_config(
    page_title="PU Matcher - Consultor Técnico de Produtos",
    page_icon="🎯",
    layout="wide"
)

# ---------------------------------------------------------------------------
# Estado da Sessão
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []
if "template_id" not in st.session_state:
    st.session_state.template_id = "proposta_tecnica_completa"

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3081/3081559.png", width=60)
    st.title("PU Matcher")
    st.caption("Agente Investigativo para Match de Produtos de Poliuretano")
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
    st.subheader("⚙️ Motor de Inteligência Artificial")
    selected_model = st.selectbox(
        "Provedor / Modelo:",
        [
            "gemini/gemini-2.0-flash",
            "gemini/gemini-2.5-flash",
            "gemini/gemini-2.5-pro",
            "gpt-4o",
            "gpt-4o-mini",
            "claude-sonnet-4-5",
            "claude-3-5-haiku-20241022",
            "groq/llama-3.3-70b-versatile"
        ],
        index=0
    )

    st.divider()

    if st.button("🔄 Iniciar Nova Demanda de Cliente", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# ---------------------------------------------------------------------------
# Área Principal
# ---------------------------------------------------------------------------
st.markdown("### 🎯 Assistente de Vendas Técnicas & Match de Produtos")
st.caption(
    "Descreva a demanda do cliente (ex: 'Cliente quer desenvolver assento de ônibus de alta densidade') "
    "para que a IA avalie os requisitos e localize produtos existentes no catálogo."
)

# Exibe histórico de mensagens
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "sources" in msg and msg["sources"]:
            st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(msg['sources'])}")
        if "model_used" in msg:
            st.caption(f"🤖 Modelo: `{msg['model_used']}`")

# Input do Vendedor / Técnico
if prompt := st.chat_input("Digite a demanda ou responda às perguntas do agente..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Analisando requisitos técnicos e cruzando catálogo de produtos..."):
            try:
                payload = {
                    "query": prompt,
                    "template_id": st.session_state.template_id,
                    "model_name": selected_model,
                    "history": [
                        {"role": m["role"], "content": m["content"]}
                        for m in st.session_state.messages[:-1]
                    ]
                }

                response = requests.post(API_URL, json=payload, timeout=90)
                if response.status_code == 200:
                    data = response.json()
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
                else:
                    st.error(f"Erro na resposta da API ({response.status_code}): {response.text}")
            except requests.exceptions.ConnectionError:
                st.error(
                    "❌ Não foi possível conectar ao backend. "
                    "Verifique se os containers estão rodando com `docker-compose up -d`."
                )
            except requests.exceptions.Timeout:
                st.error(
                    "⏱️ Tempo limite excedido aguardando resposta do agente. "
                    "Tente novamente ou verifique os logs do backend."
                )
            except Exception as e:
                st.error(f"Erro inesperado: {e}")
