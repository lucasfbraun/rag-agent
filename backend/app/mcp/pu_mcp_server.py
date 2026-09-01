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
def consultar_produtos_por_aplicacao(termo_busca: str, listar_todos: bool = False) -> Dict[str, Any]:
    """Lista produtos distintos do acervo cujo conteúdo menciona a aplicação/uso dado.

    `listar_todos` (pedido do usuário): por padrão devolve prévia de 10 + o
    total real; quando True, devolve todos, sem limite nenhum."""
    try:
        return listar_produtos_por_aplicacao(termo_busca, listar_todos=listar_todos)
    except RetrievalIndisponivelError as e:
        return {"erro": f"Catálogo indisponível no momento: {e}"}

# Definição de ferramentas no padrão MCP / LiteLLM
MCP_TOOLS_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "consultar_catalogo_erp",
            "description": "Consulta código ERP, disponibilidade de linha e embalagens de um produto de poliuretano.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termo_busca": {"type": "string", "description": "Nome ou código do produto"}
                },
                "required": ["termo_busca"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_normas_homologadas",
            "description": "Consulta quais produtos da empresa já foram testados e homologados para normas específicas (antichama, ABNT, ASTM, FMVSS).",
            "parameters": {
                "type": "object",
                "properties": {
                    "norma_requerida": {"type": "string", "description": "Código da norma (ex: 'ABNT NBR 9178', 'FMVSS 302', 'UL94')"}
                },
                "required": ["norma_requerida"]
            }
        }
    },
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
            "description": "Lista/conta produtos distintos do acervo real cujo conteúdo menciona uma aplicação/uso (ex: 'colchão', 'cortiça', 'automotivo'). Use SEMPRE que o pedido for uma LISTAGEM ou uma CONTAGEM POR CATEGORIA ('produtos para colchão', 'quais produtos temos para X', 'quantos produtos para automotivo temos') — em vez de um produto específico ou uma recomendação única. NÃO confundir com consultar_estatisticas_catalogo, que é o total do acervo INTEIRO, sem filtro de categoria. Por padrão devolve só uma prévia (10 produtos) + o total real encontrado — pergunte ao usuário se ele quer a lista completa ou só essa prévia antes de decidir; se ele pedir 'todos'/'a lista completa', chame de novo com listar_todos=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "termo_busca": {"type": "string", "description": "Aplicação/uso citado pelo cliente (ex: 'colchão', 'cortiça')"},
                    "listar_todos": {"type": "boolean", "description": "true para listar TODOS os produtos encontrados, sem limite nenhum (só use depois que o usuário confirmar que quer a lista completa); false (padrão) devolve uma prévia de até 10"}
                },
                "required": ["termo_busca"]
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
    return json.dumps({"erro": "Ferramenta não encontrada."})
