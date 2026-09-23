"""
Identidade do caminho de recuperação que atendeu cada resposta.

POR QUE ISTO EXISTE

O motor não é um só: `run_pu_matcher_agent` é uma cascata de detectores, e a
resposta que sai depende inteiramente de qual deles disparou primeiro. Uma
listagem por natureza química ("quais produtos são elastômeros") é respondida
por varredura determinística do acervo; uma pergunta aberta de vendedor cai na
recuperação vetorial + LLM. São mecanismos diferentes, com taxas de acerto
diferentes, e corrigir um não diz nada sobre o outro.

`model_used` já distinguia três grupos — `"catalogo-estruturado"`,
`"escopo-deterministico"` e o nome do modelo de chat —, mas o primeiro agrupa
CINCO detectores distintos num rótulo só. Para o relatório de validação, esse
agrupamento apaga justamente o que se quer medir: se o caminho de natureza
acerta 90% e o de aplicação acerta 40%, "catalogo-estruturado: 65%" não diz a
ninguém onde trabalhar.

Daí um campo novo, `caminho`, mais fino que `model_used` e independente dele.
`model_used` continua existindo com o mesmo significado de sempre — é o que a
tela mostra e o que o feedback do usuário já grava; trocá-lo quebraria dado
histórico sem ganho.

Este módulo não importa nada: é constante pura, para que o relatório e a CLI
possam ler rótulos de caminho sem arrastar litellm, Qdrant e embeddings junto.
"""

CAMINHO_FORA_DE_ESCOPO = "escopo-deterministico"
CAMINHO_BUSCA_REVERSA = "busca-reversa-produto"
CAMINHO_REQUISITOS_COMPOSTOS = "requisitos-compostos"
CAMINHO_NATUREZA = "natureza-do-produto"
CAMINHO_CLASSIFICACAO = "classificacao-catalogo"
CAMINHO_APLICACAO = "aplicacao-com-evidencia"
CAMINHO_CONVERSACIONAL = "conversacional-rag"
CAMINHO_DOWNLOAD_DIRETO = "download-direto-fontes"

# Respostas gravadas antes desta sessão não têm caminho registrado. Elas
# entram no relatório sob este rótulo em vez de sumirem: uma pergunta real já
# avaliada continua valendo como regressão mesmo sem saber qual mecanismo a
# respondeu — só não conta para a taxa por caminho.
CAMINHO_DESCONHECIDO = "nao-registrado"

ROTULOS_DE_CAMINHO: dict[str, str] = {
    CAMINHO_FORA_DE_ESCOPO: "Recusa determinística (pergunta fora do escopo)",
    CAMINHO_BUSCA_REVERSA: "Busca reversa por código de produto",
    CAMINHO_REQUISITOS_COMPOSTOS: "Requisitos técnicos compostos (densidade, Shore…)",
    CAMINHO_NATUREZA: "Natureza química do produto",
    CAMINHO_CLASSIFICACAO: "Listagem por tecnologia/linha do catálogo",
    CAMINHO_APLICACAO: "Aplicação com evidência literal no boletim",
    CAMINHO_CONVERSACIONAL: "Recuperação vetorial + LLM (caminho aberto)",
    CAMINHO_DOWNLOAD_DIRETO: "Download direto de fontes do RAG",
    CAMINHO_DESCONHECIDO: "Não registrado (resposta anterior à medição)",
}

CAMINHOS_CONHECIDOS: tuple[str, ...] = tuple(ROTULOS_DE_CAMINHO)


def rotulo_de_caminho(caminho: str | None) -> str:
    """Rótulo legível; caminho desconhecido volta como ele mesmo.

    Não levanta: um valor gravado por uma versão mais nova da aplicação não
    pode derrubar o relatório de uma versão mais velha — mesma disciplina de
    `perfil_permissoes`, onde permissão órfã é ignorada em vez de estourar.
    """
    if not caminho:
        return ROTULOS_DE_CAMINHO[CAMINHO_DESCONHECIDO]
    return ROTULOS_DE_CAMINHO.get(caminho, caminho)
