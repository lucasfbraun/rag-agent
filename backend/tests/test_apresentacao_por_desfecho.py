"""
Bloco 1 da correção de arquitetura (2026-09-18): a apresentação da resposta
passa a depender do DESFECHO da análise, e toda afirmação técnica precisa vir
com a evidência que a sustenta.

Por que estes testes existem: o template antigo mandava, em letras maiúsculas,
preencher ESTRITAMENTE um formulário que já vinha com "✅ Atende",
"✅ Homologado" e "Temos o produto ideal para sua demanda!" escritos. Com
trechos recuperados errados, o modelo não tinha como responder "isso não
sustenta a pergunta" sem desobedecer a instrução — e o usuário leigo, que é o
alvo do produto, recebia a mesma resposta confiante acertando ou errando.

Estes testes travam a regressão: são asserções sobre o PROMPT montado, que é a
única superfície onde essa pressão existia. Não medem a qualidade da resposta
do LLM (isso exige acervo real e conjunto de avaliação — ver Etapa 0 da
avaliação de arquitetura); medem que a instrução deixou de forçar sucesso.
"""
import pytest

from app.templates import (
    FORMATOS_POR_DESFECHO,
    TEMPLATES_DISPONIVEIS,
    obter_instrucao_template,
)

TODOS_OS_TEMPLATES = sorted(TEMPLATES_DISPONIVEIS.keys())


@pytest.mark.parametrize("template_id", TODOS_OS_TEMPLATES)
def test_instrucao_nao_ordena_preencher_o_template_incondicionalmente(template_id):
    """A ordem "OBRIGATÓRIO ... ESTRITAMENTE SEGUINDO A ESTRUTURA" não pode
    voltar: era ela que tornava "não encontrei" uma desobediência."""
    instrucao = obter_instrucao_template(template_id)
    normalizada = " ".join(instrucao.upper().split())
    assert "ESTRUTURE SUA RESPOSTA FINAL ESTRITAMENTE" not in normalizada
    assert "OBRIGATÓRIO:" not in normalizada


@pytest.mark.parametrize("template_id", TODOS_OS_TEMPLATES)
def test_instrucao_oferece_os_quatro_desfechos(template_id):
    """O template escolhido governa só o desfecho "candidato com evidência".
    Os outros três precisam chegar ao modelo em qualquer template, senão o
    único caminho oferecido continua sendo o de sucesso."""
    instrucao = obter_instrucao_template(template_id)
    assert "CANDIDATO COM EVIDÊNCIA" in instrucao
    assert "COMPARAÇÃO INCONCLUSIVA" in instrucao
    assert "NADA ENCONTRADO" in instrucao
    assert "FALTA INFORMAÇÃO DECISIVA" in instrucao
    for formato in FORMATOS_POR_DESFECHO.values():
        assert formato.strip() in instrucao


@pytest.mark.parametrize("template_id", TODOS_OS_TEMPLATES)
def test_instrucao_declara_nao_encontrado_como_resposta_legitima(template_id):
    """Sem isto o modelo trata ausência de evidência como falha própria e
    inventa um candidato para ter o que entregar."""
    instrucao = obter_instrucao_template(template_id)
    assert "LEGÍTIMA E ESPERADA" in instrucao


@pytest.mark.parametrize("template_id", TODOS_OS_TEMPLATES)
def test_instrucao_exige_trecho_literal_e_arquivo(template_id):
    """A evidência citável é o que permite a um leigo conferir a resposta sem
    conhecer o produto — é o objetivo declarado deste bloco."""
    instrucao = obter_instrucao_template(template_id)
    assert "trecho LITERAL" in instrucao
    assert "nome do arquivo de origem" in instrucao
    assert "essa frase não entra na resposta" in instrucao


@pytest.mark.parametrize("template_id", TODOS_OS_TEMPLATES)
def test_nenhum_template_afirma_sucesso_antes_da_analise(template_id):
    """Frases que declaravam o resultado no próprio formulário. "Temos o
    produto ideal para sua demanda!" abria o template comercial: o modelo
    copiava isso mesmo quando o contexto era de outro produto."""
    formato = TEMPLATES_DISPONIVEIS[template_id]["formato"]
    proibidas = (
        "Temos o produto ideal",
        "✅ Homologado",
        "✅ Compatível",
        "Drop-in direto",
    )
    for frase in proibidas:
        assert frase not in formato, f"{template_id} ainda afirma sucesso: {frase!r}"


@pytest.mark.parametrize("template_id", TODOS_OS_TEMPLATES)
def test_status_sempre_oferece_os_tres_estados_juntos(template_id):
    """Onde houver "✅ Atende", os outros dois estados precisam estar na mesma
    posição — um ✅ sozinho vira o exemplo que o modelo imita."""
    formato = TEMPLATES_DISPONIVEIS[template_id]["formato"]
    for posicao, trecho in enumerate(formato.split("✅ Atende")):
        if posicao == 0:
            continue
        assert trecho.lstrip().startswith("| ❌ Não atende | ❓ Não informado"), (
            f"{template_id}: '✅ Atende' aparece sem os estados alternativos ao lado"
        )


def test_template_desconhecido_cai_no_padrao_sem_quebrar():
    """main.py aceita template_id do cliente; um valor inválido não pode
    derrubar a montagem do prompt."""
    instrucao = obter_instrucao_template("template_que_nao_existe")
    assert TEMPLATES_DISPONIVEIS["proposta_tecnica_completa"]["formato"].strip() in instrucao


@pytest.mark.parametrize("template_id", TODOS_OS_TEMPLATES)
def test_campos_entre_colchetes_sao_declarados_como_instrucao(template_id):
    """Os formatos usam [colchetes] como campo a preencher. Sem dizer isso, o
    modelo já copiou literalmente "[NOME COMERCIAL DO PRODUTO]" na resposta."""
    instrucao = obter_instrucao_template(template_id)
    assert "nunca texto a copiar na resposta" in instrucao
