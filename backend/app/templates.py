"""
Módulo para gerenciamento de templates padronizados de resposta.
Permite à gestão comercial e técnica configurar o formato oficial de entrega.

POR QUE ESTE MÓDULO MUDOU (2026-09-18 — avaliação de arquitetura):

Até esta sessão, `obter_instrucao_template` injetava no prompt a ordem
"OBRIGATÓRIO ... ESTRUTURE SUA RESPOSTA FINAL ESTRITAMENTE SEGUINDO A ESTRUTURA
DESTE TEMPLATE", e os próprios templates já vinham com o veredito preenchido
("✅ Atende", "✅ Homologado", "✅ Compatível", "Temos o produto ideal para sua
demanda!"). O efeito prático: o modelo recebia N trechos recuperados — que
podiam ser do produto errado — e uma instrução obrigatória para preencher um
formulário que JÁ AFIRMAVA que a busca deu certo. Não havia forma de responder
"os trechos que recebi não sustentam isso" sem desobedecer.

Consequência observada no uso real: quem conhece o catálogo percebia o erro na
hora; quem NÃO conhece (vendedor em campo, que é o usuário-alvo) recebia uma
resposta bem formatada, confiante e com selos de ✅ sobre o produto errado, sem
nenhum sinal de que algo falhou. O sistema tinha exatamente a mesma aparência
acertando e errando — o que torna impossível perceber melhora ou piora.

A correção tem duas partes, e as duas vivem aqui:

1. A APRESENTAÇÃO PASSA A DEPENDER DO RESULTADO. O template comercial/técnico
   é o formato de UM dos quatro desfechos possíveis (candidato com evidência).
   Os outros três — comparação inconclusiva, nada encontrado, falta informação
   decisiva — têm formato próprio e são tão válidos quanto. Ver
   `FORMATOS_POR_DESFECHO`.

2. TODA AFIRMAÇÃO TÉCNICA CARREGA A EVIDÊNCIA QUE A SUSTENTA: documento de
   origem e o trecho literal. É o que permite a alguém que não conhece o
   produto conferir a resposta — lê o trecho citado e vê se ele fala do que
   foi perguntado. Sem isso, o usuário leigo não tem como auditar nada.

O status de cada requisito deixou de ser exemplificado como aprovado e passou a
ser uma escolha explícita entre TRÊS estados, incluindo "Não informado" —
ausência de dado no boletim não é reprovação nem aprovação (regra que já
constava em AGENT_SYSTEM_PROMPT e que o template contradizia na prática).
"""

# Os três estados que um requisito pode ter. Escritos uma vez e interpolados
# nos templates para que nenhum formato volte a exemplificar só o caso
# aprovado — foi assim que "✅ Atende" virou o padrão de fato das respostas.
_ESTADOS_DE_REQUISITO = "✅ Atende | ❌ Não atende | ❓ Não informado no documento"

TEMPLATES_DISPONIVEIS = {
    "proposta_tecnica_completa": {
        "nome": "📊 Proposta Técnica Comercial Completa (Padrão)",
        "descricao": "Ideal para envio formal com comparativo técnico detalhado e normas.",
        "formato": f"""
🎯 **ANÁLISE DE PRODUTO — PU MATCH**

• **Demanda Informada:** [Resumo das necessidades do cliente]
• **Produto com Evidência no Acervo:** **[NOME COMERCIAL DO PRODUTO]** (Código ERP: não verificado nesta base)
• **Família Química:** [ex: Sistema MDI Moldado a Frio / Poliol Poliéster — só se o documento disser]
• **Documento de Origem:** [nome do arquivo de onde veio a evidência]

📋 **Tabela Comparativa de Especificações:**
| Requisito do Cliente | Valor no Documento | Status | Fonte |
| :--- | :--- | :---: | :--- |
| [Requisito 1] | [Valor lido, ou "não consta"] | [{_ESTADOS_DE_REQUISITO}] | [arquivo] |
| [Requisito 2] | [Valor lido, ou "não consta"] | [{_ESTADOS_DE_REQUISITO}] | [arquivo] |

🔍 **Trechos Literais que Sustentam a Recomendação:**
> "[cole aqui o trecho EXATO do documento, sem reescrever]"
> — [nome do arquivo]

💡 **Diferenciais e Orientações Técnicas de Aplicação:**
- [Apenas o que estiver escrito no documento; se for conhecimento geral de PU, rotule como tal]

⚠️ **Disponibilidade Comercial e Próximos Passos:**
- Status comercial não verificado nesta base; confirmar no ERP.
- Sugestão: Solicitar amostra piloto para teste no molde do cliente.
"""
    },
    "comercial_rapido": {
        "nome": "⚡ Resumo Comercial Rápido (WhatsApp / E-mail)",
        "descricao": "Formato direto e ágil para resposta imediata ao cliente em visita.",
        "formato": """
📌 **Encontrei no acervo um produto com evidência para essa demanda:**

* **Produto:** **[NOME COMERCIAL]** (Código ERP: não verificado nesta base)
* **Aplicação no Documento:** [o que o Boletim Técnico diz sobre o uso]
* **Principais Destaques:**
  - [Propriedade e valor, exatamente como consta no documento]
  - [Norma, só se houver laudo/homologação citado no documento]
* **Por que este produto:** "[trecho literal do boletim que liga o produto à demanda]"
* **Status:** Status comercial não verificado nesta base; confirmar no ERP.
* **Ficha Técnica (TDS):** [nome do arquivo]
"""
    },
    "parecer_interno_engenharia": {
        "nome": "🔬 Parecer de Engenharia de Aplicação (Interno)",
        "descricao": "Focado em análise interna de compatibilidade de processo e bancada.",
        "formato": """
🧪 **PARECER TÉCNICO INTERNO DE APLICAÇÃO**

1. **Cliente / Projeto:** [Identificação da Demanda]
2. **Produto de Linha Avaliado:** [Nome e Código]
3. **Aderência Técnica:** [Alta / Média / Baixa / Não avaliável com os documentos recuperados]
4. **Base Documental:** [arquivos consultados — e o que cada um sustenta]
5. **Análise de Variáveis Críticas de Injeção:**
   - Relação Poliol/Isocianato: [valor do documento, ou "não consta"]
   - Tempo de Creme / Gel / Desmolde: [valores do documento, ou "não consta"]
6. **Lacunas de Evidência:** [o que seria necessário confirmar em bancada ou com P&D]
7. **Observações de Homologação:** [apenas laudos efetivamente citados nos documentos]
"""
    }
}


# Desfechos que NÃO são "achei o produto". Existem para que o modelo tenha uma
# forma legítima e padronizada de dizer que não achou — antes, o único formato
# oferecido pressupunha sucesso, e toda falha de recuperação era redigida como
# se fosse uma recomendação.
FORMATOS_POR_DESFECHO = {
    "inconclusivo": """
🤔 **ANÁLISE INCONCLUSIVA**

• **Demanda Informada:** [resumo]
• **O que encontrei:** [produto(s) candidato(s)]
• **O que os documentos comprovam:** "[trecho literal]" — [arquivo]
• **O que NÃO consegui comprovar:** [o requisito que ficou sem evidência]
• **Como confirmar:** [consultar qual documento / falar com quem]

Não apresento isto como recomendação porque a evidência recuperada não cobre todos os requisitos.
""",
    "nao_encontrado": """
❌ **NÃO ENCONTREI EVIDÊNCIA NO ACERVO**

• **Demanda Informada:** [resumo do que foi pedido]
• **O que busquei:** [termos/critérios efetivamente pesquisados]
• **O que voltou:** [nada, ou apenas documentos de outra aplicação/produto — diga qual]

Isso significa que **não há evidência explícita no acervo indexado**, e não que o produto não exista na empresa.
Sugestão: confirmar o termo usado internamente para essa aplicação, ou encaminhar para a equipe técnica/P&D.
""",
    "falta_informacao": """
❓ **PRECISO DE UMA INFORMAÇÃO PARA CONTINUAR**

• **O que já sei da sua demanda:** [resumo]
• **Candidatos possíveis:** [produtos que sobraram, se houver]
• **O que decide entre eles:** [a ÚNICA variável que falta, com o porquê]

[faça a pergunta, uma só, em linguagem simples]
""",
}


def obter_instrucao_template(template_id: str) -> str:
    """Retorna a instrução de apresentação para o prompt de sistema.

    A instrução NÃO é mais uma ordem incondicional de preencher o template
    (ver docstring do módulo). Ela primeiro obriga a classificar o desfecho e
    só então oferece o formato correspondente. O template escolhido pelo
    usuário na tela governa apenas o desfecho "candidato com evidência" — os
    outros três têm formato fixo, porque são sobre a AUSÊNCIA de evidência e
    não teria sentido variar o layout disso por preferência comercial.
    """
    tpl = TEMPLATES_DISPONIVEIS.get(template_id, TEMPLATES_DISPONIVEIS["proposta_tecnica_completa"])
    return f"""
ANTES DE ESCREVER, CLASSIFIQUE O DESFECHO DESTA CONSULTA. A APRESENTAÇÃO SEGUE O DESFECHO —
NUNCA O CONTRÁRIO. Escolha exatamente um dos quatro:

(1) CANDIDATO COM EVIDÊNCIA — existe nos documentos recuperados um produto cujo próprio
    Boletim Técnico sustenta a demanda. Use o formato do template configurado (abaixo).
(2) COMPARAÇÃO INCONCLUSIVA — há candidato, mas pelo menos um requisito ficou sem evidência.
(3) NADA ENCONTRADO — os documentos recuperados não sustentam a demanda. É uma resposta
    LEGÍTIMA E ESPERADA, não uma falha sua: o acervo pode não ter o produto, ou a busca pode
    não ter alcançado o documento certo. Diga isso com todas as letras.
(4) FALTA INFORMAÇÃO DECISIVA — só quando a escolha entre candidatos reais depende de uma
    variável que o usuário não informou.

REGRA DE OURO: é melhor entregar (2), (3) ou (4) corretamente do que entregar (1) sem
sustentação. NUNCA escolha (1) só para ter algo a apresentar, e NUNCA preencha um campo do
template com suposição, conhecimento geral de mercado ou dado de outro produto. Campo sem
evidência recebe "não consta no documento" — em nenhuma hipótese um ✅.

EVIDÊNCIA CITÁVEL — VALE PARA OS QUATRO DESFECHOS:
Quem lê esta resposta pode não conhecer o produto e precisa conseguir conferir sozinho.
Então toda afirmação técnica vem acompanhada de (a) o nome do arquivo de origem e (b) o
trecho LITERAL do documento, copiado sem reescrever, entre aspas. Se você não consegue
apontar o trecho que sustenta uma frase, essa frase não entra na resposta.

FORMATO DO DESFECHO (1) — CANDIDATO COM EVIDÊNCIA:
{tpl['formato']}

FORMATO DO DESFECHO (2) — COMPARAÇÃO INCONCLUSIVA:
{FORMATOS_POR_DESFECHO['inconclusivo']}

FORMATO DO DESFECHO (3) — NADA ENCONTRADO:
{FORMATOS_POR_DESFECHO['nao_encontrado']}

FORMATO DO DESFECHO (4) — FALTA INFORMAÇÃO DECISIVA:
{FORMATOS_POR_DESFECHO['falta_informacao']}

Os campos entre [colchetes] são instruções de preenchimento, nunca texto a copiar na resposta.
"""
