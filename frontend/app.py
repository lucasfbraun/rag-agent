import streamlit as st
import requests

API_URL = "http://backend:8000/api/match"
TEMPLATES_URL = "http://backend:8000/api/templates"

st.set_page_config(
    page_title="PU Matcher - Consultor Técnico de Produtos",
    page_icon="🎯",
    layout="wide"
)

# Estado da Sessão
if "messages" not in st.session_state:
    st.session_state.messages = []
if "template_id" not in st.session_state:
    st.session_state.template_id = "proposta_tecnica_completa"

# Sidebar com Parametrizações
with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/3081/3081559.png", width=60)
    st.title("PU Matcher")
    st.caption("Agente Investigativo para Match de Produtos de Poliuretano")
    st.divider()

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
    st.subheader("⚙️ Motor de Inteligência Artificial")
    selected_model = st.selectbox(
        "Provedor / Modelo:",
        [
            "gemini/gemini-1.5-flash",
            "gemini/gemini-1.5-pro",
            "gpt-4o",
            "gpt-4o-mini",
            "claude-3-5-sonnet-20240620",
            "groq/llama-3.1-70b-versatile"
        ],
        index=0
    )

    if st.button("🔄 Iniciar Nova Demanda de Cliente", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# Área Principal
st.markdown("### 🎯 Assistente de Vendas Técnicas & Match de Produtos")
st.caption("Descreva a demanda do cliente (ex: 'Cliente quer desenvolver assento de ônibus de alta densidade') para que a IA avalie os requisitos e localize produtos existentes no catálogo.")

# Exibe histórico
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "sources" in msg and msg["sources"]:
            st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(msg['sources'])}")

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

                # Chamada ao Backend FastAPI
                response = requests.post(API_URL, json=payload, timeout=60)
                if response.status_code == 200:
                    data = response.json()
                    st.markdown(data["answer"])
                    if data["sources"]:
                        st.caption(f"📚 **Boletins Técnicos (TDS) Consultados:** {', '.join(data['sources'])}")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": data["answer"],
                        "sources": data["sources"]
                    })
                else:
                    st.error(f"Erro na resposta da API: {response.text}")
            except Exception as e:
                st.error(f"Erro de comunicação com o backend: {e}")
