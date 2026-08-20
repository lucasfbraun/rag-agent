"""
Módulo para gerenciamento de templates padronizados de resposta.
Permite à gestão comercial e técnica configurar o formato oficial de entrega.
"""

TEMPLATES_DISPONIVEIS = {
    "proposta_tecnica_completa": {
        "nome": "📊 Proposta Técnica Comercial Completa (Padrão)",
        "descricao": "Ideal para envio formal com comparativo técnico detalhado e normas.",
        "formato": """
🎯 **RECOMENDAÇÃO DE PRODUTO HOMOLOGADO - PU MATCH**

• **Demanda Informada:** [Resumo das necessidades do cliente]
• **Produto Recomendado:** **[NOME COMERCIAL DO PRODUTO]** (Código ERP: `[CÓDIGO]`)
• **Família Química:** [ex: Sistema MDI Moldado a Frio / Poliol Poliéster / etc.]

📋 **Tabela Comparativa de Especificações:**
| Requisito do Cliente | Especificação do Produto Existente | Status |
| :--- | :--- | :---: |
| [Requisito 1: Densidade/Dureza] | [Valor na Ficha TDS] | ✅ Atende |
| [Requisito 2: Flamabilidade/Norma] | [Norma Homologada / Certificado] | ✅ Homologado |
| [Requisito 3: Tipo de Processo] | [Parâmetro Recomendado de Injeção] | ✅ Compatível |

💡 **Diferenciais e Orientações Técnicas de Aplicação:**
- [Vantagens competitivas do produto]
- [Dica de processo: temperatura de molde, relação NCO, desmoldagem]

⚠️ **Disponibilidade Comercial e Próximos Passos:**
- Produto ativo em linha.
- Sugestão: Solicitar amostra piloto para teste no molde do cliente.
"""
    },
    "comercial_rapido": {
        "nome": "⚡ Resumo Comercial Rápido (WhatsApp / E-mail)",
        "descricao": "Formato direto e ágil para resposta imediata ao cliente em visita.",
        "formato": """
✅ **Temos o produto ideal para sua demanda!**

* **Produto:** **[NOME COMERCIAL]** (Cód: `[CÓDIGO]`)
* **Aplicação Principal:** [Aplicação homologada]
* **Principais Destaques:**
  - [Propriedade 1: ex: Densidade 50 kg/m³ e alta resiliência]
  - [Propriedade 2: Atende norma de flamabilidade CONTRAN / ABNT]
* **Status:** Produto de linha em catálogo ativo.
* **Ficha Técnica (TDS):** [Nome do arquivo TDS anexado/referenciado]
"""
    },
    "parecer_interno_engenharia": {
        "nome": "🔬 Parecer de Engenharia de Aplicação (Interno)",
        "descricao": "Focado em análise interna de compatibilidade de processo e bancada.",
        "formato": """
🧪 **PARECER TÉCNICO INTERNO DE APLICAÇÃO**

1. **Cliente / Projeto:** [Identificação da Demanda]
2. **Produto de Linha Indicado:** [Nome e Código]
3. **Aderência Técnica:** [Alta / Média / Drop-in direto]
4. **Análise de Variáveis Críticas de Injeção:**
   - Relação Poliol/Isocianato recomendada: [ex: 100:45 pbw]
   - Tempo de Creme / Gel / Desmolde: [ex: 18s / 75s / 4.5 min]
5. **Observações de Homologação:** [Histórico de laudos em clientes similares]
"""
    }
}

def obter_instrucao_template(template_id: str) -> str:
    """Retorna o template formatado para ser injetado nas instruções do modelo."""
    tpl = TEMPLATES_DISPONIVEIS.get(template_id, TEMPLATES_DISPONIVEIS["proposta_tecnica_completa"])
    return f"""
OBRIGATÓRIO: Quando você tiver todas as informações necessárias e for recomendar o produto encontrado na base de dados, ESTRUTURE SUA RESPOSTA FINAL ESTRITAMENTE SEGUINDO A ESTRUTURA DESTE TEMPLATE:
{tpl['formato']}
"""
