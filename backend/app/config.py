"""
Configuração central do backend — fonte única da verdade para conexões e modelos padrão.

Antes desta extração, QDRANT_HOST/QDRANT_PORT/COLLECTION_NAME/EMBEDDING_MODEL estavam
duplicados como constantes de módulo em ingestion.py e engine.py (e o modelo de chat
padrão hardcoded em main.py + duas funções de engine.py), o que já causou bugs reais
de divergência quando os modelos precisaram trocar (ver PROGRESS.md).
"""
import os

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))

# Nome da coleção no Qdrant. Configurável desde 2026-09-09 por um motivo
# concreto: restaurar um snapshot cria a coleção com o nome que ela tinha na
# ORIGEM, e o Qdrant não renomeia coleção. Com o nome fixo no código, um
# snapshot restaurado sob outro nome deixava a aplicação reportando "base
# vetorial vazia" — tudo saudável, o dado ali do lado, e nenhuma indicação de
# que era só divergência de nome. Também permite mais de um projeto no mesmo
# Qdrant, que é o caso quando a instância é compartilhada.
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "pu_products_catalog")

# text-embedding-3-small (OpenAI) = 1536 dims | gemini-embedding-001 = 3072 dims.
# VECTOR_SIZE PRECISA bater com a dimensão do modelo escolhido, e o modelo precisa
# ser o mesmo na ingestão e na consulta — trocar de embedding obriga a recriar a
# coleção do zero (ver .env.example).
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "1536"))

# Alias sempre-atual do Gemini — resistente a descontinuações de versão pontuais
DEFAULT_CHAT_MODEL = "gemini/gemini-flash-latest"

# Modelos de chat oferecidos ao usuário (AUD-005, ticket 4 do plano de
# correção): sem uma allowlist, o cliente podia mandar qualquer string de
# modelo/provedor pro litellm.completion(), sem controle de custo/quota.
#
# A duplicação que existia aqui — esta lista mantida à mão em paralelo ao
# selectbox de frontend/app.py — foi eliminada em 2026-09-09: o frontend
# agora busca em GET /api/models. Ela chegou a cobrar o preço previsto na
# docstring do módulo: remover os modelos Ollama daqui sem reconstruir a
# imagem do frontend deixou a tela oferecendo um modelo que o servidor
# recusava, e toda pergunta virava 422 sem pista do motivo.
#
# A ORDEM importa: é a ordem do seletor na tela, e o primeiro é o que o
# usuário pega por padrão. `gpt-4o-mini` vem primeiro por ser o que está
# validado em uso contínuo neste projeto; os modelos Gemini vêm depois porque
# a chave atual está num tier de quota restrito e o endpoint já devolveu 503
# por sobrecarga em horário comercial.
MODELOS_DE_CHAT_EM_ORDEM = (
    "gpt-4o-mini",
    "gpt-4o",
    "gemini/gemini-flash-latest",
    "gemini/gemini-3.6-flash",
    "gemini/gemini-pro-latest",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "groq/llama-3.3-70b-versatile",
)

# Allowlist de modelos aceitos em MatchRequest.model_name — DERIVADA da tupla
# acima, nunca escrita à mão em paralelo. O frontend também não repete a
# lista: busca em GET /api/models (ver app.main.list_models).
ALLOWED_CHAT_MODELS = frozenset(MODELOS_DE_CHAT_EM_ORDEM)

# ---------------------------------------------------------------------------
# Expansão de consulta leigo → técnico (ver app/rag/query_expansion.py)
# ---------------------------------------------------------------------------
# Desligável por ambiente de propósito: é a forma de comparar o motor com e sem
# tradução sobre as MESMAS perguntas, sem reverter código. Ligado por padrão
# porque o caminho leigo (sem código de produto, sem nome de seção) é hoje o
# que mais erra, e é o caminho que todo usuário não-especialista percorre.
EXPANSAO_CONSULTA_ATIVA = os.getenv("EXPANSAO_CONSULTA_ATIVA", "true").lower() == "true"

# Derivado da allowlist, nunca escrito à mão em paralelo: MODELOS_DE_CHAT_EM_ORDEM[0]
# é o modelo validado em uso contínuo neste projeto (ver comentário da tupla).
# A tradução é uma tarefa curta e barata — não há motivo para gastar o modelo
# caro aqui, nem para acoplá-la ao modelo que o usuário escolheu na tela.
#
# CONSEQUÊNCIA A SABER: como este modelo é fixo e não acompanha o seletor da
# tela, a pergunta digitada pelo usuário vai para ESTE provedor mesmo quando ele
# escolheu outro para conversar. Só o texto da pergunta — a tradução não vê
# documento nenhum do acervo. Quem precisar de um único provedor para tudo
# define esta variável.
#
# `or` em vez do default de `os.getenv`: variável DECLARADA e VAZIA no .env
# devolve "", não o default — e "" chegaria ao litellm como nome de modelo,
# derrubando a tradução em silêncio (fail-open) em toda pergunta. Com `or`,
# vazio e ausente se comportam igual, que é o que o .env.example promete.
EXPANSAO_CONSULTA_MODELO = (
    os.getenv("EXPANSAO_CONSULTA_MODELO") or MODELOS_DE_CHAT_EM_ORDEM[0]
)

# Teto de termos traduzidos. Cada termo vira um `scroll` próprio no Qdrant na
# busca por palavra-chave — e `_variantes_palavra_chave` pode gerar mais de uma
# flexão por termo, então o custo real em ida-e-volta é um pequeno múltiplo
# deste número, não ele exato. 6 é ponto de partida para experimento, não valor
# validado — ajuste junto com a medição, não no escuro.
EXPANSAO_MAX_TERMOS = int(os.getenv("EXPANSAO_MAX_TERMOS", "6"))

# A tradução é síncrona e fica NA FRENTE de toda a recuperação no caminho
# leigo. Sem teto, o padrão do litellm é 6000 s: um provedor lento (não com
# erro — lento) penduraria a pergunta do vendedor por minutos antes de o
# fail-open agir. Uma tradução que não chega em poucos segundos não vale a
# espera, porque a busca sem ela é exatamente o comportamento anterior.
EXPANSAO_TIMEOUT_SEGUNDOS = float(os.getenv("EXPANSAO_TIMEOUT_SEGUNDOS", "8"))

# ---------------------------------------------------------------------------
# Busca por palavra-chave no acervo (ver app/rag/engine.retrieve_products_context)
# ---------------------------------------------------------------------------
# CONTEXTO DO PROBLEMA QUE ESTES TETOS RESOLVEM: `client.scroll` do Qdrant é
# PAGINAÇÃO, não busca — percorre a coleção em ordem de ID e não ordena por
# relevância. Com um `limit=50` fixo por termo, um acervo em que trezentos
# trechos contêm "cortiça" devolvia cinquenta ARBITRÁRIOS, e o trecho certo
# podia nunca entrar nos candidatos. Sem exceção, sem log, resposta final com
# cara de normal. A correção varre o termo inteiro trazendo só os IDs (baratos)
# e busca o payload (caro) apenas dos escolhidos.
#
# A Query API do Qdrant, que faria busca híbrida nativa com reordenação do
# servidor, só existe a partir da 1.10. A imagem fixada em docker-compose.yml é
# a v1.9.2 e requirements.txt prende o cliente em >=1.9.0,<1.10.0 — então
# reordenação aqui é local, por construção, até uma atualização planejada de
# servidor E cliente.

# Quantos IDs no MÁXIMO são varridos por flexão de termo. O acervo real tem
# ~11.000 pontos, então este teto cobre a varredura COMPLETA de qualquer termo
# que discrimine alguma coisa; quem estoura é o termo que aparece em mais de um
# terço do acervo — justamente o que menos ajuda a separar produto. Quando o
# teto corta, a varredura vira incompleta e isso SAI EM LOG (warning), porque o
# defeito que estamos corrigindo é, na essência, uma falha silenciosa.
# 4096 é ponto de partida para experimento, não valor validado — meça a
# distribuição real de hits por termo antes de mexer.
BUSCA_TEXTUAL_TETO_IDS_POR_TERMO = int(os.getenv("BUSCA_TEXTUAL_TETO_IDS_POR_TERMO", "4096"))

# Tamanho de cada página do `scroll`. É o que limita o PIOR CASO em número de
# idas ao Qdrant: são no máximo ceil(TETO / PÁGINA) chamadas por flexão. Com os
# padrões (4096 / 2048) são no máximo 2, contra 1 da versão que trazia 50
# payloads arbitrários — e a página só traz IDs, não o texto dos trechos.
# 2048 é ponto de partida para experimento, não valor validado.
BUSCA_TEXTUAL_PAGINA_SCROLL = int(os.getenv("BUSCA_TEXTUAL_PAGINA_SCROLL", "2048"))

# Quantos payloads (o dado caro: cada trecho tem ~700 palavras) são realmente
# buscados no Qdrant depois da varredura. A varredura é completa; ESTE é o
# único corte, e ele é aplicado sobre candidatos já ORDENADOS por quantos
# termos distintos da pergunta cada trecho cobre — não sobre ordem de ID.
# Precisa ficar bem acima de `top_k` (6, ou 10 com consulta traduzida) para
# sobrar margem à pontuação local, e bem abaixo do acervo para o custo de rede
# não depender do tamanho da coleção.
# 60 é ponto de partida para experimento, não valor validado.
BUSCA_TEXTUAL_ORCAMENTO_PAYLOADS = int(os.getenv("BUSCA_TEXTUAL_ORCAMENTO_PAYLOADS", "60"))

# Banco relacional (Fase 5 — RBAC & Governança). Usuários/perfis, separado do Qdrant (vetorial).
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", 5432))
POSTGRES_DB = os.getenv("POSTGRES_DB", "pu_matcher")
POSTGRES_USER = os.getenv("POSTGRES_USER", "pu_matcher")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
if not POSTGRES_PASSWORD:
    raise RuntimeError(
        "POSTGRES_PASSWORD não definida — configure no .env antes de iniciar a aplicação "
        "(sem isso a conexão com o banco de usuários seria feita com senha em branco)."
    )
DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# Autenticação (Fase 5, tarefa 3). Assina o token de sessão — mesmo SECRET_KEY que já
# existia no .env sem uso; reaproveitado em vez de criar um segundo segredo.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY não definida — configure no .env antes de iniciar a aplicação "
        "(sem isso os tokens de sessão seriam assinados com uma chave previsível)."
    )
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 480))  # 8h, um turno
REMEMBER_ME_EXPIRE_DAYS = int(os.getenv("REMEMBER_ME_EXPIRE_DAYS", 30))

# ---------------------------------------------------------------------------
# Active Directory / LDAP (vínculo de usuário — ver app/auth/ldap_service.py)
# ---------------------------------------------------------------------------
# Opcional: sem LDAP_SERVER a aplicação funciona igual, só sem a opção de
# vincular conta ao AD. `ldap_configurado()` é quem decide se a funcionalidade
# aparece, para a tela não oferecer algo que não existe naquela instalação.
LDAP_SERVER = os.getenv("LDAP_SERVER", "")
LDAP_DOMAIN = os.getenv("LDAP_DOMAIN", "")
LDAP_BASE_DN = os.getenv("LDAP_BASE_DN", "")
LDAP_BIND_USER = os.getenv("LDAP_BIND_USER", "")
LDAP_BIND_PASSWORD = os.getenv("LDAP_BIND_PASSWORD", "")

# 636 (LDAPS) por padrão, não 389: no bind de login a senha do usuário atravessa
# a rede, e em 389 ela vai em TEXTO CLARO. Verificado em 2026-09-10 que o
# controlador de domínio da Flexível atende em 636.
LDAP_PORT = int(os.getenv("LDAP_PORT", "636"))
LDAP_USE_SSL = os.getenv("LDAP_USE_SSL", "true").lower() == "true"

# AD corporativo costuma usar certificado de CA interna, ausente do truststore
# do container — daí o padrão ser não validar. É um débito consciente: protege
# contra escuta passiva, não contra man-in-the-middle dentro da rede. Ligue
# quando a CA interna estiver instalada na imagem.
LDAP_VALIDAR_CERTIFICADO = os.getenv("LDAP_VALIDAR_CERTIFICADO", "false").lower() == "true"
