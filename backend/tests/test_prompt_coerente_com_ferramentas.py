"""
O prompt de sistema e as ferramentas MCP precisam contar a MESMA história.

POR QUE ESTES TESTES EXISTEM (21/09/2026)

A arquitetura mudou em três frentes — a cascata de evidência por natureza
(`consultar_produtos_por_tipo`), a classificação estrutural do catálogo
(`consultar_produtos_por_classificacao_catalogo`) e a generalização da
exclusão de famílias auxiliares — e o `AGENT_SYSTEM_PROMPT` ficou para trás.
Ele mandava chamar a ferramenta ERRADA exatamente no caso que a ferramenta
nova existe para atender, nunca citava a ferramenta nova, e mantinha uma regra
codificada para um único termo ("em listagens de elastômeros, não inclua ADT
nem CAT") que o código havia transformado em dado — com um escape que o texto
do prompt instruía a violar.

Isso importa mais desde a válvula de segurança de `_responder_natureza_do_produto`:
quando a cascata determinística não acha nada em nenhum dos quatro níveis, a
pergunta passa a seguir para o caminho conversacional. O LLM recebe MAIS
perguntas de natureza do que antes — e era lá que ele estava sendo instruído a
fazer a coisa errada.

Como os testes de `test_apresentacao_por_desfecho.py`, estes são asserções
sobre o PROMPT montado: é a única superfície onde essa instrução existe. Não
medem a qualidade da resposta do LLM.
"""
import re

import pytest

from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS
from app.rag.engine import AGENT_SYSTEM_PROMPT, _montar_system_instruction

NOMES_DE_FERRAMENTA = {t["function"]["name"] for t in MCP_TOOLS_DEFINITIONS}


def _bloco(cabecalho: str, ate: str) -> str:
    """Recorta o trecho do prompt que vai de `cabecalho` até `ate`."""
    inicio = AGENT_SYSTEM_PROMPT.index(cabecalho)
    fim = AGENT_SYSTEM_PROMPT.index(ate, inicio)
    return AGENT_SYSTEM_PROMPT[inicio:fim]


# --- a ferramenta nova precisa existir para o modelo -----------------------

def test_prompt_menciona_a_ferramenta_de_natureza():
    """Antes desta correção o modelo só conhecia `consultar_produtos_por_tipo`
    pela descrição em MCP_TOOLS_DEFINITIONS, enquanto o prompt — que é a
    instrução com mais peso — mandava usar outra ferramenta para a mesma
    pergunta."""
    assert "consultar_produtos_por_tipo" in AGENT_SYSTEM_PROMPT
    assert "consultar_produtos_por_tipo" in _montar_system_instruction(
        "proposta_tecnica_completa"
    )


def test_toda_ferramenta_citada_no_prompt_existe_de_verdade():
    """Invariante estrutural: o prompt não pode mandar chamar uma ferramenta
    que não está declarada — nem continuar citando uma que foi removida."""
    citadas = set(re.findall(r"`(consultar_[a-z_]+)`", AGENT_SYSTEM_PROMPT))
    assert citadas, "o prompt deixou de citar qualquer ferramenta"
    assert citadas <= NOMES_DE_FERRAMENTA, citadas - NOMES_DE_FERRAMENTA


def test_pedido_de_natureza_manda_chamar_a_ferramenta_de_natureza():
    bullet = _bloco("- Por TIPO/NATUREZA DO PRODUTO", "\n   - SEM NENHUMA CATEGORIA")
    assert "CHAME `consultar_produtos_por_tipo`" in bullet
    # E a ferramenta de aplicação só pode aparecer aqui como CONTRAINDICAÇÃO.
    assert "NÃO use `consultar_produtos_por_aplicacao`" in bullet


# --- as três intenções, cada uma com a sua ferramenta ----------------------

@pytest.mark.parametrize(
    "intencao,ferramenta",
    [
        ("1. NATUREZA", "consultar_produtos_por_tipo"),
        ("2. FINALIDADE", "consultar_produtos_por_aplicacao"),
        ("3. CLASSIFICAÇÃO ESTRUTURAL", "consultar_produtos_por_classificacao_catalogo"),
    ],
)
def test_as_tres_intencoes_estao_descritas_com_a_ferramenta_certa(intencao, ferramenta):
    """NATUREZA ("produtos que são X"), FINALIDADE ("produtos para X") e
    CLASSIFICAÇÃO ESTRUTURAL ("produtos da tecnologia/linha X") são três
    perguntas diferentes, com três ferramentas diferentes. Confundi-las é o
    erro que a distinção existe para impedir."""
    bloco = _bloco(intencao, "\n     ")
    assert f"`{ferramenta}`" in bloco, bloco


def test_o_criterio_de_distincao_e_utilizavel_nao_so_tres_nomes():
    """Listar os três nomes não ensina nada: o modelo precisa de um teste que
    ele consiga aplicar na frente da pergunta do vendedor."""
    bloco = _bloco("ANTES DE ESCOLHER A FERRAMENTA", "Cinco variações")
    # O teste de reescrita com o verbo explícito.
    assert "é um X" in bloco and "serve para fazer X" in bloco
    assert "está catalogado na linha X" in bloco
    # A pista sintática que resolve a maioria dos casos.
    assert "PREPOSIÇÃO" in bloco
    # E o que fazer quando a palavra serve para duas intenções.
    assert "PERGUNTE" in bloco


# --- os quatro níveis de evidência -----------------------------------------

@pytest.mark.parametrize(
    "nivel",
    [
        "classificacao_estrutural",
        "identidade_declarada",
        "composicao_comprovada",
        "mencao_no_documento",
    ],
)
def test_prompt_descreve_cada_nivel_de_evidencia(nivel):
    """A ferramenta devolve `nivel_atendido`; se o modelo não souber o que cada
    nível prova, ele entrega a lista certa com a força errada — e todo o
    trabalho da cascata se perde na última milha."""
    assert f"`{nivel}`" in AGENT_SYSTEM_PROMPT


def test_prompt_obriga_declarar_a_forca_da_evidencia():
    bloco = _bloco("COMO APRESENTAR OS QUATRO NÍVEIS", "\nD) PEDIDO POR ESPECIFICAÇÃO")
    assert "`nivel_atendido`" in bloco
    assert "SEMPRE nomeie o nível que respondeu" in bloco


def test_mencao_nunca_pode_ser_apresentada_como_classificacao():
    """O nível mais fraco é o mais perigoso: é o que faz um documento que só
    CITA a palavra virar "produto que é X" na resposta ao cliente."""
    bloco = _bloco("- `mencao_no_documento`", "\n   REGRAS OBRIGATÓRIAS")
    assert "NUNCA apresente isto como classificação" in bloco


def test_composicao_nao_pode_ser_apresentada_como_identidade():
    bloco = _bloco("- `composicao_comprovada`", "\n     - `mencao_no_documento`")
    assert "NÃO é o mesmo que o produto SER aquilo" in bloco


def test_cascata_vazia_nao_pode_virar_busca_por_aplicacao():
    """A válvula de segurança manda a pergunta para o caminho conversacional
    justamente quando os quatro níveis estão vazios. Se o modelo "salvar" a
    resposta com uma busca de aplicação, ele apresenta menção no conteúdo como
    prova de natureza — exatamente o que a cascata acabou de recusar."""
    bloco = _bloco("COMO APRESENTAR OS QUATRO NÍVEIS", "\nD) PEDIDO POR ESPECIFICAÇÃO")
    assert "`nivel_atendido` nulo" in bloco
    assert "NADA ENCONTRADO" in bloco


def test_nivel_substitui_o_trecho_literal_exigido_pelo_template():
    """`obter_instrucao_template` exige trecho literal + arquivo para toda
    afirmação técnica. A varredura do catálogo devolve NOMES de produto, não
    trecho — sem dizer isso, as duas instruções se contradizem no mesmo prompt
    e o modelo escolhe uma delas sozinho."""
    prompt = _montar_system_instruction("proposta_tecnica_completa")
    assert "trecho LITERAL" in prompt  # a exigência do template continua de pé
    assert "A EVIDÊNCIA CITÁVEL DESTA FERRAMENTA É O RÓTULO DO NÍVEL" in prompt


# --- a regra de famílias auxiliares virou dado, com escape -----------------

def test_regra_codificada_de_elastomero_nao_voltou():
    """A exclusão de ADT/CAT deixou de ser uma regra escrita à mão para
    elastômero e virou dado em `_FAMILIAS_AUXILIARES_DO_CATALOGO`, válido para
    QUALQUER natureza."""
    assert "Em LISTAGENS de elastômeros" not in AGENT_SYSTEM_PROMPT
    assert "Em LISTAGENS POR NATUREZA" in AGENT_SYSTEM_PROMPT
    assert "de QUALQUER natureza, não só de elastômeros" in AGENT_SYSTEM_PROMPT


def test_quem_pergunta_pela_familia_auxiliar_recebe_a_familia_auxiliar():
    """O escape de `_produto_e_familia_auxiliar`: "quais produtos são
    catalisadores?" tem a família CAT como resposta certa. O prompt anterior
    instruía o modelo a fazer o contrário disso."""
    assert "EXCEÇÃO OBRIGATÓRIA" in AGENT_SYSTEM_PROMPT
    assert "não há catalisadores no catálogo" in AGENT_SYSTEM_PROMPT


def test_prompt_nao_ressuscita_o_parametro_aposentado():
    """`exigir_natureza` foi removido do código; se voltar ao prompt, o modelo
    passa a mandar um argumento que ninguém lê."""
    assert "exigir_natureza" not in AGENT_SYSTEM_PROMPT


# --- o que NÃO podia ser perdido nesta reescrita ---------------------------

@pytest.mark.parametrize(
    "regra",
    [
        # Cada uma destas foi paga com um erro real em produção.
        "Menção não é classificação",
        "não prova que o produto É um elastômero",
        "CORREÇÃO EXPLÍCITA DO USUÁRIO",
        "uma categoria parecida NÃO comprova a aplicação solicitada",
        "não possui integração real com ERP/LIMS",
        "nunca junte os dois numa lista só",
    ],
)
def test_regras_caras_continuam_no_prompt(regra):
    assert regra in AGENT_SYSTEM_PROMPT


def test_prompt_proibe_citar_numero_sem_mostrar_os_produtos():
    """CASO REAL (21/09/2026): perguntado sobre a tecnologia elastômero, o
    agente respondeu "encontrei 45 produtos que mencionam aplicações
    relacionadas, mas não são classificados como elastômeros" — e não mostrou
    nenhum, nem ofereceu mostrar.

    Para quem não conhece o catálogo é a pior resposta possível: afirma que
    existe, recusa-se a contar, e não deixa caminho. A regra dos quatro níveis
    já cobria `consultar_produtos_por_tipo`; o número veio de outra ferramenta,
    então a proibição precisa ser geral.
    """
    from app.rag.engine import AGENT_SYSTEM_PROMPT

    assert "CITOU UM NÚMERO, MOSTRE OS PRODUTOS" in AGENT_SYSTEM_PROMPT
    assert "É PROIBIDO" in AGENT_SYSTEM_PROMPT
    assert "quer ver esses 45?" in AGENT_SYSTEM_PROMPT
