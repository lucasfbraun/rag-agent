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
from typing import Dict, Any

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
    parâmetro nunca vaza campo sensível por acidente (fail-closed)."""
    if tool_name == "consultar_catalogo_erp":
        return str(consultar_catalogo_erp(arguments.get("termo_busca", ""), ver_custos=ver_custos))
    elif tool_name == "consultar_normas_homologadas":
        return str(consultar_normas_homologadas(arguments.get("norma_requerida", ""), ver_laudo_completo=ver_laudo_completo))
    return "Ferramenta não encontrada."
