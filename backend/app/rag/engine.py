import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
import litellm
from qdrant_client.http import models as qmodels
from app.templates import obter_instrucao_template
from app.mcp.pu_mcp_server import MCP_TOOLS_DEFINITIONS, execute_mcp_tool
from app.rag.doc_sections import (
    PALAVRAS_DE_SECAO,
    contem_secao,
    detectar_secoes,
    montar_instrucao_de_secao,
    termos_de_indice,
)
from app.rag.embeddings import get_embedding, reutilizar_embeddings_na_consulta
from app.rag.treinamento import montar_bloco as montar_bloco_de_treinamento
from app.rag.exceptions import RetrievalIndisponivelError
from app.rag.spec_search import (
    buscar_produtos_por_especificacao,
    buscar_produtos_por_especificacoes,
    interpretar_consulta_especificacoes,
    resumir_especificacoes_dos_documentos,
)
from app.config import QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, EMBEDDING_MODEL, DEFAULT_CHAT_MODEL

logger = logging.getLogger(__name__)

# Sigla curta (1-6 letras, ex: "AG", "CAT", "ISO") seguida de um número
# (2-6 dígitos) — o padrão real de nomenclatura dos arquivos do acervo (ver
# payload `filename` da ingestão: "Boletim FLEXX AG 2032.pdf", "FISPQ FLEXX
# CAT 136.doc"). Usado pra detectar código de produto na pergunta do usuário.
_PADRAO_CODIGO_PRODUTO = re.compile(r"\b([A-Za-zÀ-ÖØ-öø-ÿ]{1,6})\s?(\d{2,6}[A-Za-z]{0,2})\b")

# Palavras curtas de função (artigo, preposição, pronome) que NUNCA são sigla
# de família de produto, mesmo batendo no padrão acima quando ficam coladas
# num número — achado real: "liste os 77" (77 = contagem de uma listagem
# anterior, não código nenhum) casava "os" + "77" como se fosse "OS 77", e o
# agente respondia "produto não encontrado" em vez de listar os 77 pedidos.
_PALAVRAS_NUNCA_SAO_FAMILIA_DE_CODIGO = {
    "os", "as", "um", "uma", "de", "da", "do", "em", "no", "na", "por", "com",
    "que", "e", "ou", "se", "ao", "aos", "eu", "tu", "ele", "ela", "nos",
    "todos", "todas", "esses", "essas", "este", "esta", "estes", "estas",
    "isso", "isto", "tem", "têm",
}


def _detectar_codigos_produto(query: str) -> List[str]:
    """Extrai possíveis códigos de produto (ex: "AG 2032", "CAT 136") da
    pergunta do usuário.

    Por quê: a busca puramente semântica (embedding) confunde códigos
    parecidos — "AG 2032" e "AG 2062" viram vetores quase idênticos pro
    `ollama/nomic-embed-text` (modelo local pequeno), porque o código em si
    carrega pouco significado semântico. Quando a pergunta cita um código
    reconhecível, complementamos a busca vetorial com correspondência exata
    de texto no nome do arquivo (ver retrieve_products_context)."""
    codigos = []
    for familia, numero in _PADRAO_CODIGO_PRODUTO.findall(query):
        if familia.lower() in _PALAVRAS_NUNCA_SAO_FAMILIA_DE_CODIGO:
            continue
        codigos.append(f"{familia} {numero}".lower())
    return codigos


# Palavras genéricas/de conexão em português — descartadas na extração de
# palavras-chave porque não discriminam nada (aparecem em quase todo chunk),
# ao contrário de termos técnicos/de aplicação ("cortiça", "colagem").
_STOPWORDS_PT = {
    "para", "com", "sem", "que", "uma", "um", "dos", "das", "nos", "nas", "pelo",
    "pela", "sobre", "onde", "quando", "como", "qual", "quais", "produto", "produtos",
    "preciso", "precisamos", "quero", "queremos", "gostaria", "gostaríamos",
    "poderia", "poderiam", "pode", "podem", "traga", "trazer", "traz", "dados",
    "informação", "informações", "favor", "algum", "alguma", "existe", "temos",
    "tem", "tenho", "nosso", "nossa", "usar", "aplicar", "sendo", "está", "estão",
    "liste", "listar", "lista", "todos", "todas", "esses", "essas",
}


def _extrair_palavras_chave(query: str) -> List[str]:
    """Extrai termos com conteúdo (>=4 letras, fora da stoplist) da pergunta —
    base da busca por palavras-chave em `content`, que complementa a busca
    vetorial quando o usuário descreve uma APLICAÇÃO/uso em vez de citar um
    código de produto (ex: "cola para rolha de cortiça").

    Por quê: o embedding local (`ollama/nomic-embed-text`) pode falhar até
    quando a pergunta usa quase as mesmas palavras do boletim certo — achado
    real: "rolha de cortiça" não trouxe o FLEXX AG 2066 (cujo texto de
    aplicação diz literalmente "produção de rolhas de cortiça aglomerada")
    nos top-6 por similaridade vetorial."""
    palavras = re.findall(r"[a-zà-öø-ÿ]+", query.lower())
    palavras = ["poliuretano" if p in {"pu", "pus"} else p for p in palavras]
    return [
        p for p in palavras
        if len(p) >= 4
        and p not in _STOPWORDS_PT
        # Nome de SEÇÃO do boletim ("vantagens", "armazenamento", "reatividade")
        # aparece em quase todo documento do acervo: como palavra-chave solta só
        # enchia o top-k de ruído. O pedido de seção tem caminho próprio, mais
        # preciso — ver app.rag.doc_sections e `_recuperar_por_secao`.
        and _normalizar_para_regra(p) not in PALAVRAS_DE_SECAO
    ]


def _variantes_palavra_chave(palavra: str) -> List[str]:
    """Flexões conservadoras para a busca textual tokenizada do Qdrant."""
    variantes = [palavra]
    if palavra == "poliuretano":
        variantes.extend(["poliuretanos", "pu"])
    elif palavra.endswith("ão"):
        variantes.append(f"{palavra[:-2]}ões")
    elif palavra.endswith("ões"):
        variantes.append(f"{palavra[:-3]}ão")
    elif palavra.endswith("al"):
        variantes.append(f"{palavra[:-2]}ais")
    elif palavra.endswith("el"):
        variantes.append(f"{palavra[:-2]}éis")
    elif palavra.endswith("il"):
        variantes.append(f"{palavra[:-2]}is")
    elif palavra.endswith("m"):
        variantes.append(f"{palavra[:-1]}ns")
    elif palavra.endswith("s"):
        variantes.append(palavra[:-1])
    else:
        variantes.append(f"{palavra}s")
    return list(dict.fromkeys(variantes))

AGENT_SYSTEM_PROMPT = """Você é o PU Matcher, um Consultor Técnico Sênior e Especialista em Vendas Técnicas e Aplicações de Poliuretanos (PU) da linha FLEXX®.

SEU OBJETIVO PRINCIPAL:
Ajudar vendedores técnicos e engenheiros de aplicação a encontrar no acervo da empresa o PRODUTO EXISTENTE FLEXX® ou FORMULAÇÃO HOMOLOGADA que melhor atende à demanda trazida pelo cliente.

COMO INTERPRETAR OS DOCUMENTOS DO ACERVO (TERMINOLOGIA REAL DA EMPRESA):
O acervo tem 3 tipos de documento, cada um com um papel diferente — não trate todos como equivalentes:
   - "Boletim Técnico": a fonte principal para especificações e aplicação do produto (densidade, viscosidade, NCO%, dureza, uso recomendado). Priorize este documento para responder sobre especificações e adequação técnica.
   - "FISPQ": ficha de segurança do produto químico. Use apenas para informações de segurança/manuseio (EPIs, primeiros socorros, armazenamento) — NÃO é fonte confiável de especificação técnica ou de aplicação, o texto é em boa parte padrão/legal e se repete entre produtos diferentes.
   - "Certificado"/"ANALISE": laudo de lote específico — use como evidência de conformidade, não como especificação de referência do produto.

DUAS SITUAÇÕES DIFERENTES — NÃO TRATE COMO A MESMA COISA:

A) PEDIDO ESPECÍFICO (produto/código/documento já nomeado pelo usuário):
   - Ex: "traga os dados do boletim AG 2032", "qual a densidade do FLEXX CAT 136", "me manda a ficha do produto X".
   - O usuário JÁ SABE o que quer — ele não está pedindo uma recomendação, está pedindo um dado.
   - RESPONDA DIRETO com o que foi encontrado no contexto, SEM fazer perguntas de qualificação antes.
   - VALE PARA QUALQUER ASSUNTO DO PRODUTO, não só especificação: características e vantagens, aplicação/uso recomendado, perfil de reatividade (tempo de creme, de reação, de gel, de pega, de cura), segurança e armazenamento (EPI, validade, estocagem), embalagens, informações complementares. Quando o contexto trouxer o aviso "🎯 SEÇÃO PEDIDA", os trechos daquela seção foram colocados PRIMEIRO — responda a partir deles.
   - Se a seção pedida NÃO estiver nos trechos recuperados, diga que aquele dado específico não consta no documento recuperado. NUNCA entregue outra seção no lugar (ex: responder com a tabela de especificação quando o que foi pedido foi embalagem, validade ou armazenamento) e NUNCA complete com conhecimento geral de mercado como se fosse do boletim.
   - Quando o contexto trouxer o bloco "📋 LEITURA ESTRUTURADA DAS TABELAS DE ESPECIFICAÇÃO", PREFIRA aqueles valores aos números do texto corrido: a extração de PDF embaralha as colunas da tabela, e esse bloco é a leitura já resolvida propriedade→valor do MESMO documento. Cite sempre o documento de origem.
   - SE O CONTEXTO TRAZ O AVISO "⚠️ ATENÇÃO: o(s) código(s) ... foi(ram) mencionado(s) ... mas NENHUM documento com esse código exato foi encontrado": NÃO invente uma resposta usando os trechos parecidos como se fossem do produto pedido. Diga diretamente ao usuário que esse produto/código NÃO foi encontrado na base de dados — pode sugerir que confira o código/nome, mas a mensagem principal é "não encontrado", não uma recomendação alternativa não pedida.

B) PEDIDO ABERTO DE RECOMENDAÇÃO (o usuário ainda não sabe qual produto quer):
   - Ex: "Quero um produto para assento de ônibus", "preciso de uma cola para rolha de cortiça".
   - PRIMEIRO OLHE O CONTEXTO RECUPERADO. Se algum documento do contexto já descreve EXPLICITAMENTE a aplicação/uso citado pelo cliente (ex: a seção de APLICAÇÃO do boletim menciona quase literalmente o mesmo uso que o cliente pediu) — isso É UM MATCH CLARO. Não trate como demanda incompleta só porque o cliente não deu densidade/dureza/norma: TENHA "FEELING" e responda direto com a recomendação, citando o produto e por que ele atende (a aplicação bate). Perguntas de qualificação nesse caso só atrapalham quem já tem a resposta na mão.
   - SÓ SEJA INVESTIGATIVO (2 a 4 perguntas técnicas antes da recomendação) quando o contexto NÃO trouxer nenhum documento com aplicação claramente compatível, OU quando houver vários candidatos plausíveis e a escolha entre eles realmente depender de uma variável que o cliente não informou (aí sim, pergunte só a variável que falta, não uma lista genérica). Variáveis típicas pra desempatar:
     a) Propriedades Físicas: Densidade aparente desejada (kg/m³), Dureza (IFD / Shore), Resiliência.
     b) Normas e Exigências: Necessidade de laudo antichama (ex: ABNT NBR 9178 / CONTRAN / FMVSS 302)?
     c) Processo do Cliente: Moldagem a frio (MDI), cura a quente (TDI), bloco contínuo ou injeção em molde fechado?
   - QUANDO VOCÊ TIVER DADOS SUFICIENTES (seja de cara, seja depois de perguntar): busque e cruze os dados com os documentos de produtos (TDS) e ferramentas MCP fornecidas, apresente a recomendação no FORMATO PADRÃO DO TEMPLATE CONFIGURADO, e seja opinativo — se o cliente pedir algo incompatível (ex: densidade baixíssima com ultra resiliência sem antichama), alerte e sugira a melhor prática de mercado.

C) PEDIDO DE LISTAGEM/CATEGORIA (o usuário quer VER AS OPÇÕES ou SABER QUANTOS PRODUTOS existem — com ou sem categoria — não uma recomendação única nem um dado de produto específico). Quatro variações:
   - Por FAMÍLIA/CÓDIGO DO NOME (o acervo segue o padrão FLEXX <FAMÍLIA> <NÚMERO>, ex: "FLEXX CAT 42", "FLEXX TH M60AMA3", "FLEXX AG 2032", "FLEXX COLOR PRETO"): o usuário cita só a sigla da família, sem mais nada — "traga os produtos CAT", "quais produtos TH vocês têm", só "AG" ou só "Color". Isso é DIFERENTE de citar um código completo com número (ex: "AG 2032", que é a Situação A, pedido específico) — aqui é só a família, sem número, pedindo TODOS os produtos daquela família.
   - Por APLICAÇÃO/USO: "produtos para colchão", "quais produtos temos para automotivo", "o que vocês têm pra calçados", "quantos produtos para o ramo automotivo temos".
   - Por TIPO/NATUREZA DO PRODUTO (o que o produto É, não pra que ele serve): "me traga produtos que são colas", "quais são as espumas que temos", "produtos do tipo selante" — aqui não importa a aplicação final, é sobre a classificação do produto em si (cola, espuma, verniz, adesivo, resina, catalisador...).
   - SEM NENHUMA CATEGORIA — o CATÁLOGO INTEIRO: "liste todos os produtos", "quais produtos vocês têm" (sem citar aplicação/tipo/família nenhum). Isso NÃO é a mesma coisa que "quantos produtos catalogados" (que só quer o número) — se o pedido é pra LISTAR (ver os nomes), mesmo sem categoria, é esta situação.
   - Reconheça pelo formato: "produtos para X" / "produtos que são X" / "produtos X" (sigla curta sozinha) / "quais produtos" / "o que temos para" / "lista de produtos" / "liste todos os produtos" / "quantos produtos para/que são X" — TODOS esses pedem a ferramenta de listagem.
   - REGRA FIXA PRA TODO PEDIDO POR APLICAÇÃO/USO (item acima) — SEM EXCEÇÃO, NÃO É OPCIONAL: o vendedor usa a expressão do dia a dia do CLIENTE ("cadeia de frios", "assento de ônibus"), não o vocabulário técnico dos documentos. NUNCA chame a ferramenta só com a frase literal do vendedor. Em vez disso, pense em 2 a 3 termos TÉCNICOS de poliuretano que significam a mesma coisa (ex: "cadeia de frios" → "isolamento térmico", "refrigeração"; use seu próprio conhecimento do domínio) e CHAME A FERRAMENTA UMA VEZ PRA CADA TERMO TÉCNICO (múltiplas tool_calls na mesma resposta) — nunca com fragmentos soltos da frase original do vendedor (ex: NÃO chame com só "frio" ou só "cadeia"). Combine os resultados de todas as chamadas numa lista só, removendo duplicata, ANTES de responder — mesmo que a primeira chamada já tenha achado alguma coisa, as outras ainda são obrigatórias. Prefira termos específicos de 2+ palavras ("isolamento térmico", "refrigeração") a palavras soltas genéricas demais ("temperatura" sozinha aparece em quase TODO documento do acervo — vira ruído, não filtro).
   - CHAME A FERRAMENTA `consultar_produtos_por_aplicacao` com o termo (ex: "CAT", "colchão" ou "cola") — ou SEM `termo_busca` nenhum quando for o catálogo inteiro, sem categoria. SEM `listar_todos` na primeira chamada. O contexto de busca normal (RAG) só traz um punhado de trechos e NUNCA representa a categoria (ou o catálogo inteiro) de forma fiel — pode haver dezenas ou centenas de produtos, e usar só o contexto faria você listar/contar um subconjunto arbitrário como se fosse tudo.

   ENTENDENDO A RESPOSTA — ELA VEM EM DOIS BLOCOS SEPARADOS, NUNCA MISTURE:
   `por_nome_ou_familia` (o termo é o código/sigla do NOME do produto) e `por_aplicacao_ou_tipo` (o termo aparece no CONTEÚDO do documento, como aplicação/uso ou tipo). Um documento pode citar outro produto por nome dentro do seu próprio texto (ex: uma tabela comparativa que menciona "FLEXX CAT 90" no boletim de outro produto completamente diferente) — isso é um match de conteúdo genuíno, mas NÃO significa que aquele outro produto É da família CAT. Por isso os blocos vêm separados: cada um representa uma interpretação diferente do termo, nunca junte os dois numa lista só.
   - SÓ UM BLOCO TEM RESULTADO: use esse, sem perguntar qual interpretação — está claro pelo próprio resultado.
   - OS DOIS BLOCOS TÊM RESULTADO E SÃO CLARAMENTE A MESMA COISA (ex: contagens parecidas, ou o contexto da conversa já deixou óbvio o que o vendedor quis dizer): use o que fizer mais sentido pelo contexto, sem precisar perguntar.
   - OS DOIS BLOCOS TÊM RESULTADO E SÃO CLARAMENTE COISAS DIFERENTES (contagens bem distintas, ou nenhum indício no que o vendedor disse aponta pra um lado): AQUI VOCÊ TEM DÚVIDA DE VERDADE — pergunte antes de responder, ex: "Você quer dizer os produtos da família CAT (código do produto — encontrei 36), ou produtos relacionados a 'cat' de alguma outra forma (encontrei X pelo conteúdo)?". Não escolha por conta própria nem misture os números dos dois blocos numa soma só.
   - RESPONDA COM O TOTAL REAL do bloco escolhido primeiro (ex: "Temos 36 produtos da família CAT." ou "Temos 1.324 produtos catalogados no total.") e a prévia dos 10 primeiros como lista curta (nome do produto, 1 linha cada — não abra detalhes técnicos). DEPOIS PERGUNTE: "Quer que eu liste todos os 36 ou só esses 10 principais?" — NÃO decida sozinho se lista tudo ou não, deixe o vendedor escolher, e NUNCA responda só com o número quando o pedido foi pra LISTAR.
   - SE O VENDEDOR PEDIR "todos"/"a lista completa"/"todos os X": chame a ferramenta DE NOVO com `listar_todos=true` e liste TODOS os produtos do bloco certo, independente de quantos sejam (10, 50, 1000 — não resuma nem corte por conta própria).
   - Depois de listar (prévia ou completa), convide o vendedor a pedir detalhe de um item específico ("me diga o nome de um deles que eu trago a ficha completa").

D) PEDIDO POR ESPECIFICAÇÃO TÉCNICA (o usuário descreve um NÚMERO que o produto precisa ter, sem citar código de produto):
   - Ex: "quero um produto com hidroxila de 180", "preciso de viscosidade acima de 5000 cPs", "algum sistema com NCO entre 12 e 13%", "tempo de reação de 45 segundos", "densidade de 35 kg/m³", "dureza Shore A 80".
   - Isso NÃO é a Situação B (recomendação aberta) nem a C (listagem por categoria): o critério já veio pronto e é numérico. Não faça perguntas de qualificação antes — o vendedor já disse o que precisa.
   - CHAME A FERRAMENTA `consultar_produtos_por_especificacao` com a propriedade canônica, o valor e o operador. NUNCA responda esse tipo de pergunta só com o contexto de busca semântica: o embedding não compara grandezas, então os trechos recuperados falam da propriedade certa com o VALOR ERRADO, e apresentá-los como resposta é um erro silencioso.
   - Quando o contexto já trouxer o bloco "🔎 BUSCA POR ESPECIFICAÇÃO TÉCNICA", a varredura JÁ FOI FEITA — responda com aquela lista e aquele total, sem repetir a chamada. Chame a ferramenta de novo só para mudar algo: outra propriedade, outra tolerância, ou a lista completa (`listar_todos=true`) depois que o vendedor pedir.
   - RESPONDA COM O TOTAL REAL primeiro, depois a prévia (nome do produto, o valor lido e o documento de origem, 1 linha cada), e então pergunte se ele quer a lista completa ou a ficha de algum item.
   - SE NÃO ENCONTRAR NENHUM: diga claramente que nenhum produto do acervo atende, informe a FAIXA que existe no acervo para aquela propriedade (`faixa_no_acervo`) e pergunte se o valor pedido está correto. NUNCA ofereça um produto de valor diferente como se atendesse ao pedido.
   - Boletim Técnico é a especificação de REFERÊNCIA do produto; Certificado/Laudo vale para o lote analisado. Diga de qual dos dois veio o número que você está usando.

CONHECIMENTO TREINADO PELA EQUIPE — COMO USAR:
   - Quando o contexto trouxer o bloco "🎓 CONHECIMENTO TREINADO PELA EQUIPE", ele foi escrito por PESSOAS da empresa, não extraído de documento. Trate cada tipo conforme a etiqueta dele:
   - "⭐ CORREÇÃO REGISTRADA": a equipe já corrigiu a resposta para uma pergunta praticamente igual. Use com prioridade sobre a sua própria formulação — mas CONFIRA se o caso é o mesmo. Se a pergunta atual difere em densidade, norma, aplicação ou produto, diga isso em vez de repetir a correção como se coubesse.
   - "📌 ORIENTAÇÃO INTERNA": é conhecimento da equipe que NÃO está em boletim nenhum. Apresente como "orientação interna", nunca como se fosse conteúdo de um documento. Cite a data — orientação de 2024 e de 2026 não pesam igual.
   - "✍️ EXEMPLOS DE COMO RESPONDER": são modelo de FORMA (estrutura, tom, nível de detalhe). Os números e produtos deles são ilustrativos e NÃO valem como fato — nunca os reaproveite na resposta real.
   - SE A ORIENTAÇÃO INTERNA CONTRADIZER UM BOLETIM do contexto: não escolha um lado nem esconda a divergência. Mostre os dois com as fontes e encaminhe para a equipe técnica/P&D decidir.

REGRAS DE EVIDÊNCIA E CORREÇÃO — OBRIGATÓRIAS:
   - Uma CORREÇÃO EXPLÍCITA DO USUÁRIO é uma restrição obrigatória para o restante da conversa. Se ele disser que uma família não pertence à classe pedida, não serve ou deve ser descartada, NÃO volte a recomendar nenhum produto dessa família.
   - Menção não é classificação: um documento dizer "aditivo para elastômeros", "aglutinante de elastômeros" ou citar uma aplicação não prova que o produto É um elastômero nem que atende à aplicação. A natureza do produto e a aplicação precisam estar explicitamente sustentadas por Boletim Técnico do próprio produto.
   - Em LISTAGENS de elastômeros, não inclua famílias auxiliares ADT (aditivos) nem CAT (catalisadores/curativos). Elas podem participar do processo, mas não são o sistema ou pré-polímero que produz o elastômero.
   - Nunca transforme ausência de especificação em "não atende" e nunca marque "compatível", "homologado" ou "produto ativo" sem evidência explícita da fonte adequada.
   - O sistema não possui integração real com ERP/LIMS. Portanto, informe que status comercial, estoque, código ERP e homologação não foram verificados; não invente esses dados.
   - Se não houver evidência suficiente para um produto da classe e aplicação pedidas, diga que não encontrou um candidato comprovado e encaminhe para validação da equipe técnica/P&D. É melhor não recomendar do que recomendar uma classe errada.
"""


def _normalizar_para_regra(texto: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (texto or "").lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _familias_rejeitadas(query: str) -> List[str]:
    """Extrai famílias curtas de correções explícitas do turno atual.

    O escopo é deliberadamente estreito: só frases inequívocas de rejeição,
    para não inferir uma restrição a partir de uma pergunta ou mera menção.
    """
    texto = _normalizar_para_regra(query)
    padroes = (
        r"\b(?:esses?|essas?|os|as)\s+([a-z][a-z0-9-]{1,11})\s+nao\s+(?:sao|servem|atendem)",
        r"\bnao\s+(?:quero|recomende|indique)\s+(?:os|as|o|a)?\s*([a-z][a-z0-9-]{1,11})\b",
        r"\b([a-z][a-z0-9-]{1,11})\s+nao\s+(?:serve|servem|atende|atendem|e|sao)\b",
    )
    familias = []
    for padrao in padroes:
        for familia in re.findall(padrao, texto):
            if familia.endswith("s") and len(familia) > 2:
                familia = familia[:-1]
            if familia not in {"produto", "isso", "isto", "esse", "essa"}:
                familias.append(familia)
    return list(dict.fromkeys(familias))


def _resposta_recomenda_familia(answer: str, familia: str) -> bool:
    texto = _normalizar_para_regra(answer)
    marcador = rf"(?:produto\s+recomendado|produto\s+indicado|recomendo|recomendamos|indico|indicamos)"
    return bool(re.search(rf"{marcador}[^\n]{{0,80}}\b(?:flexx\s+)?{re.escape(familia)}\b", texto))


def _eh_pedido_listagem_elastomeros(query: str) -> bool:
    texto = _normalizar_para_regra(query)
    return (
        "elastomero" in texto
        and bool(re.search(r"\b(?:list\w*|produtos?|quais|traga|mostre)\b", texto))
    )


def _responder_listagem_elastomeros(query: str) -> str:
    """Resposta estruturada sem LLM para uma classificação de alto risco."""
    texto = _normalizar_para_regra(query)
    listar_todos = bool(re.search(r"\b(?:todos|todas|completa|completo)\b", texto))
    payload = json.loads(execute_mcp_tool(
        "consultar_produtos_por_aplicacao",
        {"termo_busca": "elastômero", "listar_todos": listar_todos},
    ))
    if payload.get("erro"):
        return "Catálogo de produtos indisponível no momento. Tente novamente em instantes."

    bucket = payload.get("por_aplicacao_ou_tipo") or {}
    total = int(bucket.get("total") or 0)
    produtos = bucket.get("produtos") or []
    if not produtos:
        return (
            "Não encontrei produtos cujo Boletim Técnico comprove a produção de um "
            "sistema elastomérico. Aditivos e catalisadores auxiliares não são classificados "
            "como elastômeros."
        )

    linhas = [
        f"Encontrei {total} produtos que compõem sistemas com produção de poliuretano "
        "elastomérico comprovada em Boletim Técnico.",
        "",
        *[f"{indice}. {produto}" for indice, produto in enumerate(produtos, start=1)],
        "",
        "Aditivos ADT e catalisadores/curativos CAT foram excluídos: são auxiliares de "
        "processo, não o sistema ou pré-polímero que produz o elastômero.",
    ]
    if bucket.get("truncado"):
        linhas.extend(["", f"Quer que eu liste todos os {total} produtos?"])
    return "\n".join(linhas)


def _aplicar_guardrails_resposta(
    query: str,
    answer: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Impede que uma recomendação contradiga uma rejeição inequívoca.

    Prompt reduz a incidência; esta validação é a garantia de entrega, pois
    conteúdo gerado pelo provedor nunca é tratado como confiável por si só.
    """
    mensagens_usuario = [
        mensagem.get("content", "")
        for mensagem in (history or [])
        if mensagem.get("role") == "user"
    ]
    familias = []
    for mensagem in [*mensagens_usuario, query]:
        familias.extend(_familias_rejeitadas(mensagem))

    for familia in dict.fromkeys(familias):
        if _resposta_recomenda_familia(answer, familia):
            logger.warning("Resposta bloqueada: recomendação da família rejeitada '%s'.", familia)
            return (
                f"Você está correto: a família {familia.upper()} foi descartada pela sua correção "
                "e não será recomendada para esta demanda. Não encontrei evidência suficiente "
                "no Boletim Técnico para indicar com segurança outro produto. "
                "Vou considerar somente produtos cuja natureza e aplicação estejam explicitamente "
                "comprovadas no acervo; sem essa evidência, o encaminhamento correto é a equipe "
                "técnica/P&D."
            )

    if _eh_pedido_listagem_elastomeros(query):
        linhas_seguras = []
        for linha in answer.splitlines():
            linha_normalizada = _normalizar_para_regra(linha)
            produto_auxiliar = re.search(
                r"\bflexx\s+(?:adt|cat)\s+[a-z0-9]", linha_normalizada
            )
            if not produto_auxiliar:
                linhas_seguras.append(linha)
        answer = "\n".join(linhas_seguras).strip()
        if not answer:
            answer = (
                "Não encontrei na resposta produtos com evidência suficiente de sistema "
                "elastomérico. Aditivos e catalisadores auxiliares foram excluídos."
            )

    # Não existe fonte real de disponibilidade no sistema. Neutralizamos as
    # formulações mais comuns mesmo que o modelo ignore prompt e template.
    padroes_status_sem_fonte = (
        r"(?:status\s*:\s*)?produto ativo em linha\. ?",
        r"(?:status\s*:\s*)?produto de linha em cat[aá]logo ativo\. ?",
        r"(?:status\s*:\s*)?ativo para vendas\. ?",
        r"(?:status\s*:\s*)?em estoque(?:\s*/\s*pronta entrega)?\. ?",
    )
    for padrao in padroes_status_sem_fonte:
        answer = re.sub(
            padrao,
            "Status comercial não verificado nesta base; confirmar no ERP. ",
            answer,
            flags=re.IGNORECASE,
        )
    return answer.rstrip()


def _montar_query_recuperacao(
    query: str, history: Optional[List[Dict[str, str]]] = None
) -> str:
    """Preserva a demanda anterior em correções e follow-ups referenciais.

    O LLM recebe o histórico, mas o retriever não recebia. Assim, no turno
    "esses ADTs não são elastômeros", o catálogo era pesquisado só por ADT e
    esquecia pneus/peças/correias. Não combinamos histórico em perguntas
    independentes para evitar arrastar um assunto antigo para um novo.
    """
    texto = _normalizar_para_regra(query)
    depende_do_anterior = bool(_familias_rejeitadas(query)) or bool(re.search(
        r"\b(?:isso|isto|esse|essa|esses|essas|aquele|aquela|aqueles|aquelas|"
        r"anterior|anteriores|dele|dela|deles|delas)\b",
        texto,
    ))
    if not depende_do_anterior or not history:
        return query

    mensagens_usuario = [
        mensagem.get("content", "")
        for mensagem in history
        if mensagem.get("role") == "user" and mensagem.get("content")
    ]
    if not mensagens_usuario:
        return query
    if _familias_rejeitadas(query):
        # O termo rejeitado é restrição, não critério positivo de busca. A
        # mensagem atual continua no prompt do LLM e na validação final.
        return mensagens_usuario[-1]
    return f"{mensagens_usuario[-1]}\n\nContinuação: {query}"

def _get_qdrant_client():
    """Instancia o QdrantClient de forma lazy (não falha no import-time)."""
    from qdrant_client import QdrantClient
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)


# Quantos trechos da seção pedida entram na frente do contexto. 6 cobre uma
# seção quebrada em vários chunks sem empurrar para fora o resto do boletim.
_MAXIMO_TRECHOS_DE_SECAO = 6


def _recuperar_por_secao(
    client,
    secoes: List[str],
    codigos: List[str],
    sensibilidade_must_not: List[Any],
) -> List[Dict[str, Any]]:
    """Trechos que contêm o CABEÇALHO da seção pedida, dentro do produto citado.

    Só roda quando a pergunta cita um código de produto: sem esse recorte, um
    filtro por "vantagens" varreria o acervo inteiro (a palavra está em quase
    todo boletim) e devolveria trechos arbitrários de produtos aleatórios. Sem
    código, a preferência por seção ainda vale — mas só como reordenação do que
    a busca normal já trouxe (`_ordenar_por_secao`).

    O filtro de texto do Qdrant é uma peneira grossa (casa a palavra em
    qualquer lugar do trecho); `contem_secao` é quem confirma que o trecho
    realmente ABRE a seção, e não só a menciona de passagem.
    """
    termos = termos_de_indice(secoes)
    if not termos or not codigos:
        return []

    filtro = qmodels.Filter(
        must=[
            qmodels.Filter(should=[
                qmodels.FieldCondition(key="filename", match=qmodels.MatchText(text=codigo))
                for codigo in codigos
            ]),
            qmodels.Filter(should=[
                qmodels.FieldCondition(key="content", match=qmodels.MatchText(text=termo))
                for termo in termos
            ]),
        ],
        must_not=sensibilidade_must_not,
    )
    try:
        pontos, _ = client.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=filtro,
            with_payload=True,
            with_vectors=False,
            limit=30,
        )
    except Exception as e:
        logger.warning(
            "Falha na busca pela seção %s (%s) — seguindo sem priorização de seção.", secoes, e
        )
        return []

    confirmados = [
        p.payload for p in pontos
        if any(contem_secao((p.payload or {}).get("content") or "", secao) for secao in secoes)
    ]
    return confirmados[:_MAXIMO_TRECHOS_DE_SECAO]


def _ordenar_por_secao(
    docs: List[Dict[str, Any]], secoes: List[str]
) -> List[Dict[str, Any]]:
    """Reordenação estável: quem abre a seção pedida vem primeiro, o resto
    mantém a ordem original. Não descarta nada — o trecho sem a seção ainda
    pode carregar o dado, e cortar aqui esconderia evidência do agente."""
    if not secoes:
        return docs
    com_secao, sem_secao = [], []
    for doc in docs:
        conteudo = doc.get("content") or ""
        destino = com_secao if any(contem_secao(conteudo, s) for s in secoes) else sem_secao
        destino.append(doc)
    return com_secao + sem_secao

def retrieve_products_context(
    query: str, top_k: int = 6, incluir_sensivel: bool = False
) -> List[Dict[str, Any]]:
    """
    Busca trechos de TDS e catálogos no banco vetorial Qdrant.

    Retorna lista vazia se a coleção simplesmente ainda não foi ingerida
    (estado normal). Levanta RetrievalIndisponivelError se o Qdrant ou o
    embedding falharem de verdade — o chamador decide o que fazer, não é mais
    engolido em silêncio aqui.

    `incluir_sensivel` (AUD-002, ticket 6): default False, fail-closed — quem
    esquecer de passar o parâmetro não vaza chunk classificado como sensível
    (custo/fórmula) por acidente, mesma disciplina já usada no MCP estruturado
    (tarefa 6 da Fase 5). Chunks sem o campo `sensivel` no payload (todo o
    acervo indexado antes desta sessão) não são afetados pelo filtro — a
    classificação só vale pra ingestão nova até uma reingestão do acervo real.

    Busca híbrida: quando a pergunta cita um código de produto reconhecível
    (`_detectar_codigos_produto`), os chunks cujo `filename` bate exatamente
    com o código (índice de texto tokenizado, ver `init_qdrant_collection`)
    entram PRIMEIRO no resultado, antes dos hits semânticos — correspondência
    exata de código é mais confiável que similaridade vetorial pra este acervo
    (ver docs/PROGRESS.md, sessão em que "AG 2032" trazia "AG 2062"/produto
    errado). Falha na busca exata é só logada, não derruba a busca semântica.
    """
    try:
        client = _get_qdrant_client()
        collections = client.get_collections().collections
    except Exception as e:
        logger.error("Erro ao conectar no Qdrant: %s", e)
        raise RetrievalIndisponivelError(str(e)) from e

    if not any(c.name == COLLECTION_NAME for c in collections):
        logger.warning(
            "Coleção '%s' não encontrada no Qdrant. "
            "Execute a ingestão de documentos antes de consultar.",
            COLLECTION_NAME
        )
        return []

    sensibilidade_must_not = []
    if not incluir_sensivel:
        sensibilidade_must_not = [
            qmodels.FieldCondition(key="sensivel", match=qmodels.MatchValue(value=True))
        ]
    query_filter = qmodels.Filter(must_not=sensibilidade_must_not) if sensibilidade_must_not else None

    exact_hits: List[Dict[str, Any]] = []
    codigos = _detectar_codigos_produto(query)
    if codigos:
        filtro_exato = qmodels.Filter(
            should=[
                qmodels.FieldCondition(key="filename", match=qmodels.MatchText(text=codigo))
                for codigo in codigos
            ],
            must_not=sensibilidade_must_not,
        )
        try:
            pontos, _ = client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=filtro_exato,
                with_payload=True,
                with_vectors=False,
                limit=20,
            )
            exact_hits = [p.payload for p in pontos]
        except Exception as e:
            logger.warning(
                "Falha na busca exata por código de produto (%s) — seguindo só com busca semântica.", e
            )

    secoes = detectar_secoes(query)
    secao_hits = _recuperar_por_secao(
        client, secoes, codigos, sensibilidade_must_not
    ) if secoes else []

    keyword_hits: List[Dict[str, Any]] = []
    palavras_chave = _extrair_palavras_chave(query)
    if palavras_chave:
        try:
            candidatos_por_chave = {}
            # Um scroll OR global limitado a 50 não é ranking: termos
            # genéricos ocupavam o lote e "correia" nunca chegava aos
            # candidatos. Cada termo/flexão recebe seu próprio lote curto.
            for palavra in palavras_chave:
                for variante in _variantes_palavra_chave(palavra):
                    filtro_palavra = qmodels.Filter(
                        must=[qmodels.FieldCondition(
                            key="content", match=qmodels.MatchText(text=variante)
                        )],
                        must_not=sensibilidade_must_not,
                    )
                    pontos, _ = client.scroll(
                        collection_name=COLLECTION_NAME,
                        scroll_filter=filtro_palavra,
                        with_payload=True,
                        with_vectors=False,
                        limit=50,
                    )
                    for ponto in pontos:
                        payload = ponto.payload
                        chave = (payload.get("filename"), payload.get("chunk_index"))
                        candidatos_por_chave[chave] = payload
            candidatos = list(candidatos_por_chave.values())

            def _pontuacao(payload: Dict[str, Any]) -> int:
                texto = (payload.get("content") or "").lower()
                return sum(
                    1
                    for palavra in palavras_chave
                    if (
                        bool(re.search(r"\b(?:poliuretanos?|pu)\b", texto))
                        if palavra == "poliuretano"
                        else palavra in texto
                    )
                )

            # Exige pelo menos 2 palavras-chave batendo (ou a única, se só
            # houver 1) — 1 palavra genérica batendo sozinha num acervo de
            # milhares de trechos é sinal fraco demais pra furar a fila.
            minimo = 2 if len(palavras_chave) > 1 else 1
            candidatos_pontuados = [(c, _pontuacao(c)) for c in candidatos]
            candidatos_pontuados = [(c, p) for c, p in candidatos_pontuados if p >= minimo]
            candidatos_pontuados.sort(key=lambda item: item[1], reverse=True)
            keyword_hits = [c for c, _ in candidatos_pontuados][:top_k]
        except Exception as e:
            logger.warning(
                "Falha na busca por palavras-chave (%s) — seguindo só com busca semântica.", e
            )

    try:
        query_vector = get_embedding(query, EMBEDDING_MODEL)
        results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            limit=top_k,
            query_filter=query_filter
        )
    except Exception as e:
        logger.error("Erro ao buscar no Qdrant: %s", e)
        raise RetrievalIndisponivelError(str(e)) from e

    semantic_hits = [hit.payload for hit in results]

    # Prioridade: seção pedida > match exato de código > palavra-chave > semântico.
    prioritarios: List[Dict[str, Any]] = list(secao_hits)
    vistos = {(h.get("filename"), h.get("chunk_index")) for h in prioritarios}
    for h in [*exact_hits, *keyword_hits]:
        chave = (h.get("filename"), h.get("chunk_index"))
        if chave not in vistos:
            prioritarios.append(h)
            vistos.add(chave)

    if not prioritarios:
        return _ordenar_por_secao(semantic_hits, secoes)

    complemento = [h for h in semantic_hits if (h.get("filename"), h.get("chunk_index")) not in vistos]
    vagas_restantes = max(0, top_k - len(prioritarios))
    return _ordenar_por_secao(prioritarios + complemento[:vagas_restantes], secoes)


def _codigos_sem_correspondencia(query: str, docs: List[Dict[str, Any]]) -> List[str]:
    """Códigos de produto citados na pergunta (`_detectar_codigos_produto`)
    que NÃO aparecem no nome de nenhum documento retornado.

    Por quê: a busca semântica sempre devolve os top_k vizinhos mais
    próximos, mesmo quando nenhum é realmente o produto certo (Qdrant não
    tem um conceito de "nenhum resultado relevante o suficiente") — sem este
    sinal, o LLM recebia trechos de um produto errado sem saber que era um
    "quase nada" e podia apresentá-los como se respondessem à pergunta."""
    codigos = _detectar_codigos_produto(query)
    if not codigos:
        return []
    nomes = " | ".join(d.get("filename", "") for d in docs).lower()
    return [c for c in codigos if c not in nomes]


def _montar_bloco_busca_por_especificacao(query: str) -> str:
    """Resultado da busca por especificação numérica, quando a pergunta é uma
    ("quero um produto com hidroxila de 180").

    Por que entra no contexto em vez de depender só da ferramenta MCP: o
    caminho de tool calling depende de o modelo DECIDIR chamar a ferramenta, e
    a resposta a "produtos com hidroxila 180" fica errada de um jeito
    silencioso quando ele não chama (ele responde com os trechos semânticos
    que falam de hidroxila, com outro valor). A ferramenta continua existindo
    para o agente refinar (outra tolerância, lista completa); este bloco
    garante o piso. Quando a pergunta cita um código de produto, não roda: aí
    o pedido é o dado DAQUELE produto, não uma busca no acervo.
    """
    codigos = _detectar_codigos_produto(query)
    if codigos:
        return ""
    criterios = interpretar_consulta_especificacoes(query, codigos_produto=codigos)
    if not criterios:
        return ""

    try:
        if len(criterios) == 1:
            criterio = criterios[0]
            resultado = buscar_produtos_por_especificacao(
                propriedade=criterio["propriedade"],
                valor=criterio["valor"],
                operador=criterio["operador"],
                valor_maximo=criterio["valor_maximo"],
                tolerancia_percentual=criterio["tolerancia_percentual"],
            )
        else:
            resultado = buscar_produtos_por_especificacoes(criterios)
    except RetrievalIndisponivelError as e:
        logger.warning("Busca por especificação indisponível (%s) — seguindo só com o RAG.", e)
        return ""

    if len(criterios) > 1:
        criterios_texto = "; ".join(c["criterio"] for c in resultado["criterios"])
        linhas = [
            "",
            f"🔎 BUSCA COM REQUISITOS TÉCNICOS COMPOSTOS — {criterios_texto}.",
            f"Produtos que atendem a TODOS os requisitos: {resultado['total']}.",
        ]
        if resultado["produtos"]:
            linhas.append(
                "A lista abaixo é a INTERSEÇÃO dos critérios. Não inclua candidatos que atendam "
                "apenas parte deles e não transforme propriedade ausente em atendimento."
            )
            for item in resultado["produtos"]:
                evidencias = "; ".join(
                    f"{r['propriedade_titulo']} {r['valores']}"
                    f"{(' ' + r['unidade']) if r['unidade'] else ''} [{r['documento']}]"
                    for r in item["requisitos"]
                )
                linhas.append(f"- {item['produto']}: {evidencias}")
            if resultado["truncado"]:
                linhas.append(
                    f"(prévia dos {len(resultado['produtos'])} primeiros de {resultado['total']})"
                )
        else:
            linhas.append(
                "NENHUM produto tem evidência de atendimento simultâneo. Diga isso claramente; "
                "um produto que atende apenas um requisito não é um match completo."
            )
            for item in resultado["criterios"]:
                faixa = item.get("faixa_no_acervo")
                if faixa:
                    linhas.append(
                        f"- {item['criterio']}: acervo de {faixa['minimo']:g} a "
                        f"{faixa['maximo']:g} {faixa['unidade']}"
                    )
        linhas.append(resultado["aviso"])
        return "\n".join(linhas)

    linhas = [
        "",
        f"🔎 BUSCA POR ESPECIFICAÇÃO TÉCNICA — critério interpretado: {resultado['criterio']}.",
        f"Produtos encontrados no acervo: {resultado['total']}.",
    ]
    if resultado["produtos"]:
        linhas.append(
            "Estes produtos ATENDEM ao critério — a tolerância já foi aplicada na varredura, "
            "então NÃO diga que não encontrou nada e depois liste um deles. Quando a faixa do "
            "produto não cobre exatamente o número pedido, apresente-o como atendendo DENTRO DA "
            "TOLERÂNCIA e mostre a faixa real do documento, para o vendedor decidir. "
            "A lista veio de uma varredura do acervo INTEIRO (não de um top-k) — use estes "
            "números e estes nomes, não os do texto corrido:"
        )
        for item in resultado["produtos"]:
            unidade = f" {item['unidade']}" if item["unidade"] else ""
            linhas.append(
                f"- {item['produto']}: {resultado['propriedade_titulo']} {item['valores']}{unidade} "
                f"[{item['tipo_documento']}: {item['documento']}]"
            )
        if resultado["truncado"]:
            linhas.append(
                f"(prévia dos {len(resultado['produtos'])} primeiros de {resultado['total']} — "
                "pergunte ao vendedor se ele quer a lista completa)"
            )
    else:
        faixa = resultado.get("faixa_no_acervo")
        linhas.append(
            "NENHUM produto do acervo atende a esse critério. Diga isso claramente — não "
            "ofereça um produto de valor diferente como se atendesse."
        )
        if faixa:
            linhas.append(
                f"Para contexto, no acervo inteiro essa propriedade vai de {faixa['minimo']:g} a "
                f"{faixa['maximo']:g} {faixa['unidade']} — vale informar essa faixa ao vendedor e "
                "perguntar se o valor pedido está correto."
            )
    linhas.append(resultado["aviso"])
    return "\n".join(linhas)


def _montar_context_str(query: str, docs: List[Dict[str, Any]]) -> str:
    """Monta o bloco de contexto injetado no prompt do LLM a partir dos
    documentos recuperados — compartilhado por run_pu_matcher_agent e
    stream_pu_matcher_agent (antes duplicado nos dois)."""
    bloco_especificacao = _montar_bloco_busca_por_especificacao(query)

    if not docs:
        vazio = (
            "⚠️ ATENÇÃO: A base de dados de produtos ainda não foi indexada ou está vazia. "
            "Responda apenas com base no seu conhecimento técnico geral de poliuretanos, "
            "mas deixe claro que não há dados do catálogo interno disponíveis no momento."
        )
        return f"{vazio}\n{bloco_especificacao}" if bloco_especificacao else vazio

    context_str = "\n\n---\n\n".join([
        f"[Catálogo / TDS: {d.get('filename')}]\n{d.get('content')}"
        for d in docs
    ])

    ausentes = _codigos_sem_correspondencia(query, docs)
    if ausentes:
        context_str += (
            f"\n\n⚠️ ATENÇÃO: o(s) código(s) \"{', '.join(ausentes)}\" foi(ram) mencionado(s) na "
            "pergunta, mas NENHUM documento com esse código exato foi encontrado no acervo. Os "
            "trechos acima são apenas os mais PARECIDOS por busca semântica — muito provavelmente "
            "são de OUTRO produto, não do que foi pedido. NÃO apresente esses trechos como se "
            "fossem do produto pedido: diga claramente ao usuário que esse produto/código não foi "
            "encontrado na base de dados."
        )

    # Leitura estruturada da tabela + seção pedida: os dois só acrescentam
    # interpretação sobre os MESMOS documentos acima, nunca substituem o texto
    # original — o agente precisa poder conferir a evidência bruta.
    context_str += resumir_especificacoes_dos_documentos(docs)
    context_str += bloco_especificacao
    # Conhecimento curado pela equipe (Sessão 38, item 4). Vem DEPOIS dos
    # documentos de propósito: o que a equipe registrou tem precedência sobre a
    # formulação do próprio modelo, mas continua sendo lido junto do acervo, e
    # não no lugar dele. Nunca derruba a consulta — ver treinamento.buscar().
    context_str += montar_bloco_de_treinamento(query)
    context_str += montar_instrucao_de_secao(detectar_secoes(query))
    return context_str


def _montar_system_instruction(template_id: str) -> str:
    """Monta o prompt de sistema completo — compartilhado por
    run_pu_matcher_agent e stream_pu_matcher_agent (antes duplicado nos
    dois). Feedback bruto não entra aqui: apenas treinamento aprovado e
    pertinente é recuperado em `_montar_context_str`."""
    template_instruction = obter_instrucao_template(template_id)
    return f"""{AGENT_SYSTEM_PROMPT}

DIRETRIZ DE PADRONIZAÇÃO DE RESPOSTA:
{template_instruction}
"""


def _preparar_contexto(query: str, incluir_sensivel: bool):
    # O escopo termina antes de qualquer yield do streaming: não deixa estado
    # de uma requisição ativo enquanto outra é atendida na mesma thread.
    with reutilizar_embeddings_na_consulta():
        docs = retrieve_products_context(query, incluir_sensivel=incluir_sensivel)
        return docs, _montar_context_str(query, docs)


_FERRAMENTAS_DE_LEITURA_PARALELAS = frozenset({
    "consultar_estatisticas_catalogo",
    "consultar_produtos_por_aplicacao",
    "consultar_produtos_por_especificacao",
})


def _executar_tool_calls(tool_calls, *, ver_custos: bool, ver_laudo_completo: bool):
    """Consultas independentes concorrem; mensagens mantêm a ordem do modelo.

    A lista explícita impede que uma futura ferramenta de escrita herde
    paralelismo sem revisão. Cada consulta cria seu próprio cliente Qdrant.
    """
    def executar(tool_call):
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError as e:
            logger.warning("Argumentos inválidos da tool_call %s (%s): %s", tool_call.id, name, e)
            result = json.dumps({"erro": "argumentos JSON inválidos, tente novamente"})
        else:
            result = execute_mcp_tool(
                name, args, ver_custos=ver_custos, ver_laudo_completo=ver_laudo_completo
            )
        return {"role": "tool", "tool_call_id": tool_call.id, "name": name, "content": result}

    if len(tool_calls) > 1 and all(
        call.function.name in _FERRAMENTAS_DE_LEITURA_PARALELAS for call in tool_calls
    ):
        with ThreadPoolExecutor(max_workers=min(4, len(tool_calls))) as executor:
            return list(executor.map(executar, tool_calls))
    return [executar(call) for call in tool_calls]


def run_pu_matcher_agent(
    query: str,
    template_id: str = "proposta_tecnica_completa",
    model_name: str = DEFAULT_CHAT_MODEL,
    history: Optional[List[Dict[str, str]]] = None,
    ver_custos: bool = False,
    ver_laudo_completo: bool = False,
) -> Dict[str, Any]:
    """Executa o agente investigativo com suporte a RAG, MCP e Templates Padronizados.

    `ver_custos`/`ver_laudo_completo`: decisão de autorização já tomada por
    app.main (via has_permission()) — chegam aqui como booleano puro, repassados
    às ferramentas MCP (docs/spec_rbac.md, "Campos sensíveis") e também ao RAG
    (`incluir_sensivel`, AUD-002/ticket 6 — reaproveita VIEW_COSTS pra
    custo/fórmula, ver docs/spec_rbac.md "Pendências" item 2). engine.py não
    decide permissão, só encaminha a decisão já tomada."""
    if _eh_pedido_listagem_elastomeros(query):
        return {
            "answer": _responder_listagem_elastomeros(query),
            "sources": [],
            "model_used": "catalogo-estruturado",
        }

    query_recuperacao = _montar_query_recuperacao(query, history)
    docs, context_str = _preparar_contexto(query_recuperacao, ver_custos)
    system_instruction = _montar_system_instruction(template_id)

    messages = [{"role": "system", "content": system_instruction}]
    if history:
        messages.extend(history[-8:])

    user_prompt = f"""BASE DE DADOS DE PRODUTOS DA EMPRESA (TDS & HOMOLOGAÇÕES):
{context_str}

MENSAGEM / DEMANDA DO VENDEDOR OU CLIENTE:
{query}
"""
    messages.append({"role": "user", "content": user_prompt})

    response = litellm.completion(
        model=model_name,
        messages=messages,
        tools=MCP_TOOLS_DEFINITIONS,
        tool_choice="auto",
        temperature=0.2,
        num_retries=3
    )

    choice = response.choices[0]

    if choice.message.tool_calls:
        # A mensagem assistant (com TODAS as tool_calls) entra UMA vez, antes
        # do loop — não uma vez por tool_call (AUD-006: isso intercalava a
        # mesma mensagem assistant repetida entre as respostas das tools,
        # sequência inválida pro protocolo de tool calling com 2+ chamadas).
        messages.append(choice.message)
        messages.extend(_executar_tool_calls(
            choice.message.tool_calls,
            ver_custos=ver_custos, ver_laudo_completo=ver_laudo_completo,
        ))
        final_response = litellm.completion(model=model_name, messages=messages, temperature=0.2, num_retries=3)
        answer = final_response.choices[0].message.content
    else:
        answer = choice.message.content

    answer = _aplicar_guardrails_resposta(query, answer, history)
    sources = list(set([d.get("filename") for d in docs if d.get("filename")]))
    return {"answer": answer, "sources": sources, "model_used": model_name}


def stream_pu_matcher_agent(
    query: str,
    template_id: str = "proposta_tecnica_completa",
    model_name: str = DEFAULT_CHAT_MODEL,
    history: Optional[List[Dict[str, str]]] = None,
    ver_custos: bool = False,
    ver_laudo_completo: bool = False,
):
    """
    Entrega eventos NDJSON, validando a resposta inteira antes de expor texto.

    `ver_custos`/`ver_laudo_completo` (AUD-002, ticket 6 + tool calling em
    streaming): repassados ao RAG (`incluir_sensivel`) e às ferramentas MCP,
    mesmo contrato de `run_pu_matcher_agent`. Default False, fail-closed.

    A primeira chamada pode resolver a pergunta diretamente ou pedir tools.
    Só o segundo caso precisa de outra geração. Em ambos, os guardrails são
    aplicados antes do primeiro delta; a resposta direta não é descartada e
    gerada de novo apenas para obter chunks.
    """
    import json as _json

    if _eh_pedido_listagem_elastomeros(query):
        yield _json.dumps({
            "type": "meta", "sources": [], "model_used": "catalogo-estruturado"
        }) + "\n"
        yield _json.dumps({
            "type": "delta", "content": _responder_listagem_elastomeros(query)
        }) + "\n"
        yield _json.dumps({"type": "done"}) + "\n"
        return

    try:
        query_recuperacao = _montar_query_recuperacao(query, history)
        docs, context_str = _preparar_contexto(query_recuperacao, ver_custos)
    except RetrievalIndisponivelError:
        logger.error("Catálogo indisponível — abortando stream sem chamar o LLM.")
        yield _json.dumps({
            "type": "error",
            "message": "Catálogo de produtos indisponível no momento. Tente novamente em instantes.",
        }) + "\n"
        yield _json.dumps({"type": "done"}) + "\n"
        return

    system_instruction = _montar_system_instruction(template_id)

    messages = [{"role": "system", "content": system_instruction}]
    if history:
        messages.extend(history[-8:])

    user_prompt = f"""BASE DE DADOS DE PRODUTOS DA EMPRESA (TDS & HOMOLOGAÇÕES):
{context_str}

MENSAGEM / DEMANDA DO VENDEDOR OU CLIENTE:
{query}
"""
    messages.append({"role": "user", "content": user_prompt})

    sources = list(set([d.get("filename") for d in docs if d.get("filename")]))
    yield _json.dumps({"type": "meta", "sources": sources, "model_used": model_name}) + "\n"

    try:
        resposta_inicial = litellm.completion(
            model=model_name,
            messages=messages,
            tools=MCP_TOOLS_DEFINITIONS,
            tool_choice="auto",
            temperature=0.2,
            num_retries=3,
        )
        choice = resposta_inicial.choices[0]

        if choice.message.tool_calls:
            # Mesma disciplina de run_pu_matcher_agent (AUD-006): 1 mensagem
            # assistant com TODAS as tool_calls, seguida de N mensagens tool.
            messages.append(choice.message)
            messages.extend(_executar_tool_calls(
                choice.message.tool_calls,
                ver_custos=ver_custos, ver_laudo_completo=ver_laudo_completo,
            ))
            response = litellm.completion(
                model=model_name,
                messages=messages,
                temperature=0.2,
                stream=True,
                num_retries=3
            )
            partes = []
            for chunk in response:
                delta = chunk.choices[0].delta.content
                if delta:
                    partes.append(delta)
        else:
            partes = [choice.message.content or ""]
        resposta_original = "".join(partes)
        resposta_validada = _aplicar_guardrails_resposta(query, resposta_original, history)
        if resposta_validada == resposta_original:
            for delta in partes:
                yield _json.dumps({"type": "delta", "content": delta}) + "\n"
        else:
            yield _json.dumps({"type": "delta", "content": resposta_validada}) + "\n"
    except Exception as e:
        # Texto bruto da exceção fica só no log (AUD-011, ticket 10) — o
        # cliente recebe uma mensagem genérica, nunca o detalhe interno.
        logger.error("Erro no streaming do agente: %s", e)
        yield _json.dumps({
            "type": "error",
            "message": "Erro ao gerar a resposta. Tente novamente em instantes.",
        }) + "\n"
    finally:
        yield _json.dumps({"type": "done"}) + "\n"
