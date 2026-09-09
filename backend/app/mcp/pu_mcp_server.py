"""
Servidor MCP (Model Context Protocol) para consultar estoque de produtos acabados no ERP
e banco de dados de normas/homologações em tempo real.

NOTA DE STATUS (Fase 4 do cronograma): as funções abaixo retornam dados SIMULADOS.
Substituir por chamadas reais ao ERP/LIMS da empresa quando os conectores estiverem definidos.

RESTRIÇÃO DE CAMPOS SENSÍVEIS (Fase 5, tarefa 6, docs/spec_rbac.md): `custo_industrial_kg`
e o detalhe completo de laudo (`laudo_numero`/`laboratorio_emissor`) só entram no dict de
retorno se o chamador passar `ver_custos`/`ver_laudo_completo=True` — a decisão de permissão
em si (Permission.VIEW_COSTS / VIEW_HOMOLOGATION_FULL) é tomada na camada HTTP
(app.main, via has_permission()) e chega até aqui só como booleano, para este módulo não
precisar conhecer User/Role/Permission.
"""
import json
from typing import Dict, Any

from app.rag.catalog_stats import obter_estatisticas_catalogo, listar_produtos_por_aplicacao
from app.rag.exceptions import RetrievalIndisponivelError
from app.rag.spec_search import (
    PROPRIEDADES,
    TOLERANCIA_PADRAO_PERCENTUAL,
    buscar_produtos_por_especificacao,
)

# Simulação de consulta ao ERP Corporativo (SAP / TOTVS / etc.)
def consultar_catalogo_erp(termo_busca: str, ver_custos: bool = False) -> Dict[str, Any]:
    """Consulta se o produto está ativo para faturamento, código ERP e embalagens."""
    # Exemplo simulado de retorno de ERP
    resultado = {
        "produto_encontrado": "PU-SEAT-5000 FR",
        "codigo_erp": "PRD-99841",
        "status_linha": "Ativo para Vendas",
        "embalagens_disponiveis": ["Tambor 200L", "IBC 1000L"],
        "prazo_fabricacao": "Em estoque / Pronta entrega",
        "familia": "Espuma Moldada a Frio (Cure MDI)"
    }
    if ver_custos:
        # Campo de exemplo — hoje não existe custo estruturado real em nenhum lugar do
        # sistema (docs/spec_rbac.md, "Campos sensíveis"). Vira campo real quando o ERP
        # real for integrado (Fase 4); o gate de permissão já fica pronto desde já.
        resultado["custo_industrial_kg"] = "R$ 18,40"
    return resultado

# Simulação de consulta ao Banco de Homologações & Normas
def consultar_normas_homologadas(norma_requerida: str, ver_laudo_completo: bool = False) -> Dict[str, Any]:
    """Verifica laudos oficiais de conformidade com normas regulatórias."""
    resultado = {
        "norma_pesquisada": norma_requerida,
        "produtos_certificados": ["PU-SEAT-5000 FR", "PU-FLEX-450-AUTO"],
        "resultado": "Aprovado - Autoextinguível (Taxa de queima < 100 mm/min)"
    }
    if ver_laudo_completo:
        resultado["laudo_numero"] = "CERT-2025-NBR9178"
        resultado["laboratorio_emissor"] = "IPT / SENAI"
    return resultado

# Diferente das duas funções acima: consulta dados REAIS do acervo já
# indexado no Qdrant (app.rag.catalog_stats), não simulados — cobre perguntas
# agregadas ("quantos produtos catalogados?") que a busca semântica de
# retrieve_products_context() nunca consegue responder (ela só traz um
# punhado de trechos por similaridade, não conta o total do acervo).
def consultar_estatisticas_catalogo() -> Dict[str, Any]:
    """Total de produtos distintos e documentos indexados no acervo real."""
    try:
        return obter_estatisticas_catalogo()
    except RetrievalIndisponivelError as e:
        return {"erro": f"Catálogo indisponível no momento: {e}"}

# Idem: dados reais (não simulados), mas devolve uma LISTA de produtos por
# aplicação/uso em vez de um total agregado — cobre pedido de categoria
# ("produtos para colchão"), que um top-k de poucos chunks do RAG nunca
# representaria fielmente (achado real: "colchão" aparece em ~150
# arquivos/dezenas de produtos distintos do acervo).
def consultar_produtos_por_aplicacao(termo_busca: str = "", listar_todos: bool = False) -> Dict[str, Any]:
    """Lista produtos distintos do acervo cujo conteúdo menciona a
    aplicação/uso dado. Sem `termo_busca` (vazio), lista TODOS os produtos
    do catálogo, sem filtro nenhum (pedido do usuário: "listar todos os
    produtos" sem categoria).

    `listar_todos` (pedido do usuário): por padrão devolve prévia de 10 + o
    total real; quando True, devolve todos, sem limite nenhum."""
    try:
        return listar_produtos_por_aplicacao(termo_busca, listar_todos=listar_todos)
    except RetrievalIndisponivelError as e:
        return {"erro": f"Catálogo indisponível no momento: {e}"}

# Idem: dados reais. Terceira dimensão de consulta ao acervo, ao lado de nome
# (família) e conteúdo (aplicação/tipo) — o NÚMERO da especificação técnica.
# Cobre a pergunta que nem o RAG nem as duas ferramentas acima respondem:
# "quais produtos têm hidroxila de 180?" (ver app.rag.spec_search).
def consultar_produtos_por_especificacao(
    propriedade: str,
    valor: float,
    operador: str = "igual",
    valor_maximo: float = None,
    tolerancia_percentual: float = TOLERANCIA_PADRAO_PERCENTUAL,
    listar_todos: bool = False,
) -> Dict[str, Any]:
    """Produtos do acervo cuja especificação técnica atende ao valor pedido."""
    try:
        return buscar_produtos_por_especificacao(
            propriedade=propriedade,
            valor=valor,
            operador=operador,
            valor_maximo=valor_maximo,
            tolerancia_percentual=tolerancia_percentual,
            listar_todos=listar_todos,
        )
    except RetrievalIndisponivelError as e:
        return {"erro": f"Catálogo indisponível no momento: {e}"}


_DESCRICAO_PROPRIEDADES = ", ".join(
    f"'{chave}' ({dados['titulo']}"
    + (f", em {dados['unidade_tipica']}" if dados["unidade_tipica"] else "")
    + ")"
    for chave, dados in PROPRIEDADES.items()
)

# Definição de ferramentas no padrão MCP / LiteLLM
MCP_TOOLS_DEFINITIONS = [
    # ERP e homologações ainda são simulações fixas. As funções continuam
    # disponíveis para testes da infraestrutura/RBAC, mas não são oferecidas
    # ao LLM até existirem conectores reais.
    {
        "type": "function",
        "function": {
            "name": "consultar_estatisticas_catalogo",
            "description": "Consulta números agregados do acervo INTEIRO (quantos produtos catalogados no total, sem filtro de categoria/aplicação). Use só para 'quantos produtos temos catalogados' no total. Se a pergunta filtrar por categoria/aplicação (ex: 'quantos produtos para automotivo'), use consultar_produtos_por_aplicacao em vez desta.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_produtos_por_aplicacao",
            "description": "Lista/conta produtos distintos do acervo real, procurando `termo_busca` de DUAS formas independentes e devolvendo os resultados em DOIS blocos separados (nunca misturados): `por_nome_ou_familia` (o termo é a FAMÍLIA/CÓDIGO do NOME do produto, ex: 'CAT', 'TH', 'AG', 'COLOR' — o acervo segue o padrão FLEXX <FAMÍLIA> <NÚMERO>, ex: 'FLEXX CAT 42') e `por_aplicacao_ou_tipo` (o termo aparece no CONTEÚDO do documento como APLICAÇÃO/USO, ex: 'colchão', 'cortiça', 'automotivo', ou TIPO/NATUREZA DO PRODUTO, ex: 'cola', 'espuma', 'selante'). Os dois blocos podem vir os dois preenchidos ao mesmo tempo pro MESMO termo (ex: 'CAT' pode ser família E aparecer citado no conteúdo de outros produtos) — quando isso acontecer, NÃO junte os dois: são interpretações diferentes do termo, e cabe a você (ou ao usuário, se não estiver claro pelo contexto da conversa) decidir qual vale. SEM `termo_busca` (omitido/vazio), lista TODOS os produtos do catálogo em `por_nome_ou_familia`, sem filtro nenhum — use para 'liste todos os produtos' (sem categoria/família nenhuma citada). Use SEMPRE que o pedido for uma LISTAGEM ou CONTAGEM POR CATEGORIA/FAMÍLIA. Se o pedido for só 'quantos produtos catalogados' (um número, sem precisar da lista), use consultar_estatisticas_catalogo em vez desta, que é mais leve. Por padrão cada bloco devolve só uma prévia (10 produtos) + o total real — pergunte ao usuário se ele quer a lista completa ou só essa prévia antes de decidir; se ele pedir 'todos'/'a lista completa', chame de novo com listar_todos=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termo_busca": {"type": "string", "description": "Família/código do nome do produto (ex: 'CAT', 'TH', 'AG'), OU aplicação/uso, OU tipo de produto (ex: 'colchão', 'cortiça', 'cola', 'espuma'). Omita ou deixe vazio para listar TODOS os produtos, sem filtro."},
                    "listar_todos": {"type": "boolean", "description": "true para listar TODOS os produtos encontrados, sem limite nenhum (só use depois que o usuário confirmar que quer a lista completa); false (padrão) devolve uma prévia de até 10"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_produtos_por_especificacao",
            "description": (
                "Encontra produtos do acervo real pelo VALOR NUMÉRICO de uma especificação técnica — "
                "ex: 'quero um produto com hidroxila de 180', 'viscosidade acima de 5000 cPs', "
                "'NCO entre 12 e 13%', 'tempo de reação de 45 segundos', 'densidade de 35 kg/m³'. "
                "Use SEMPRE que o pedido combinar o nome de uma propriedade técnica com um número. "
                "NÃO use busca semântica para isso: o embedding não compara grandezas, então ele "
                "traz 'algum produto que fala de hidroxila', não os que têm hidroxila 180. "
                "Não use quando o usuário já citou um código de produto e quer o dado DELE "
                "(ex: 'qual a hidroxila do AG 2032') — isso o contexto recuperado já responde. "
                "A resposta traz, por produto, a faixa lida na tabela, a unidade e o documento de "
                "origem (Boletim Técnico é a especificação de referência; Certificado/Laudo vale "
                "para o lote). Traz também `faixa_no_acervo`, o mínimo e o máximo dessa propriedade "
                "em TODO o acervo: quando `total` for 0, informe essa faixa ao vendedor e pergunte "
                "se o valor pedido está correto, em vez de só dizer 'não encontrei'. Por padrão "
                "devolve prévia de 10 + o total real; se o vendedor pedir a lista completa, chame "
                "de novo com listar_todos=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "propriedade": {
                        "type": "string",
                        "description": (
                            "Chave canônica da propriedade. Valores aceitos: "
                            f"{_DESCRICAO_PROPRIEDADES}. Traduza o termo do vendedor para uma "
                            "destas chaves (ex: 'hidroxila'/'índice de OH' -> 'indice_hidroxila'; "
                            "'isocianato livre' -> 'teor_nco'). Propriedades de tempo são sempre "
                            "em SEGUNDOS (converta minutos/horas antes de chamar)."
                        ),
                        "enum": sorted(PROPRIEDADES.keys()),
                    },
                    "valor": {
                        "type": "number",
                        "description": "Valor pedido. Com operador 'entre', é o limite INFERIOR.",
                    },
                    "operador": {
                        "type": "string",
                        "enum": ["igual", "maior", "menor", "entre"],
                        "description": (
                            "'igual' (padrão) casa por tolerância; 'maior'/'menor' para 'acima "
                            "de'/'abaixo de'; 'entre' exige também valor_maximo."
                        ),
                    },
                    "valor_maximo": {
                        "type": "number",
                        "description": "Limite superior — obrigatório quando operador='entre'.",
                    },
                    "tolerancia_percentual": {
                        "type": "number",
                        "description": (
                            f"Só para operador 'igual'. Padrão {TOLERANCIA_PADRAO_PERCENTUAL:g}%. "
                            "Aumente quando a primeira busca não achar nada e o vendedor aceitar "
                            "um valor aproximado; use 0 para casar apenas a faixa declarada no "
                            "documento."
                        ),
                    },
                    "listar_todos": {
                        "type": "boolean",
                        "description": "true devolve todos os produtos, sem limite; false (padrão) devolve prévia de 10 + total.",
                    },
                },
                "required": ["propriedade", "valor"],
            }
        }
    }
]

def execute_mcp_tool(
    tool_name: str,
    arguments: dict,
    ver_custos: bool = False,
    ver_laudo_completo: bool = False,
) -> str:
    """Executa a ferramenta MCP chamada pelo agente.

    `ver_custos`/`ver_laudo_completo` vêm da camada de autorização (via engine.py,
    a partir de app.main) — default False, então quem esquecer de passar o
    parâmetro nunca vaza campo sensível por acidente (fail-closed).

    Retorna JSON válido (não `str(dict)`/repr Python — aspas simples,
    `True`/`None` em vez de `true`/`null` — que é o que ia pro LLM antes,
    fora do contrato usual de mensagem `role: tool`; ver AUD-006, ticket 5)."""
    if tool_name == "consultar_catalogo_erp":
        return json.dumps(consultar_catalogo_erp(arguments.get("termo_busca", ""), ver_custos=ver_custos))
    elif tool_name == "consultar_normas_homologadas":
        return json.dumps(consultar_normas_homologadas(arguments.get("norma_requerida", ""), ver_laudo_completo=ver_laudo_completo))
    elif tool_name == "consultar_estatisticas_catalogo":
        return json.dumps(consultar_estatisticas_catalogo())
    elif tool_name == "consultar_produtos_por_aplicacao":
        return json.dumps(consultar_produtos_por_aplicacao(
            arguments.get("termo_busca", ""), listar_todos=arguments.get("listar_todos", False)
        ))
    elif tool_name == "consultar_produtos_por_especificacao":
        try:
            valor = float(arguments["valor"])
        except (KeyError, TypeError, ValueError):
            # Argumento gerado pelo LLM não é confiável por si só: sem valor
            # numérico a busca não tem critério, e devolver erro legível é o
            # que permite ao modelo corrigir na rodada seguinte (mesmo
            # tratamento dado a JSON inválido em app.rag.engine).
            return json.dumps({
                "erro": "Argumento 'valor' ausente ou não numérico — informe o número pedido "
                        "pelo vendedor (propriedades de tempo em segundos)."
            })
        valor_maximo = arguments.get("valor_maximo")
        try:
            valor_maximo = None if valor_maximo is None else float(valor_maximo)
        except (TypeError, ValueError):
            valor_maximo = None
        return json.dumps(consultar_produtos_por_especificacao(
            propriedade=arguments.get("propriedade", ""),
            valor=valor,
            operador=arguments.get("operador", "igual"),
            valor_maximo=valor_maximo,
            tolerancia_percentual=float(
                arguments.get("tolerancia_percentual", TOLERANCIA_PADRAO_PERCENTUAL) or 0
            ),
            listar_todos=arguments.get("listar_todos", False),
        ))
    return json.dumps({"erro": "Ferramenta não encontrada."})
