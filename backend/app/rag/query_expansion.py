"""
Tradução da pergunta leiga para o vocabulário real do acervo (2026-09-18).

O PROBLEMA QUE ISTO RESOLVE

O motor tem cinco caminhos de recuperação, e quatro deles exigem que quem
pergunta JÁ fale a língua dos Boletins Técnicos:

  - `_detectar_codigos_produto`  → precisa do código ("AG 2032")
  - `_recuperar_por_secao`       → precisa do nome da seção ("reatividade")
  - `_extrair_palavras_chave`    → precisa da palavra LITERAL que está no texto
  - os detectores `_responder_*` → precisam da formulação que alguém já corrigiu

Isso não é acidente: o motor foi construído corrigindo perguntas induzidas, ou
seja, perguntas de quem conhece o catálogo. Quem não conhece — o vendedor em
campo, que é o usuário-alvo — nunca alcança nenhum desses caminhos e cai sempre
na busca vetorial pura, que é a mais fraca do conjunto.

A busca por palavra-chave, que seria a rede de segurança, casa texto LITERAL no
campo `content`. O usuário escreve "cola", o boletim diz *adesivo*. Escreve
"colchão", o boletim diz *espuma flexível de bloco*. Escreve "borracha", o
boletim diz *elastômero*. Zero correspondências, e o caminho morre em silêncio:
não há exceção, não há log, e o resultado final parece uma resposta normal.

O QUE ESTE MÓDULO FAZ

Uma chamada barata ao LLM antes da recuperação, traduzindo a pergunta para os
termos que efetivamente aparecem em documentação técnica de poliuretano no
Brasil. Os termos entram na busca por palavra-chave e numa segunda busca
vetorial, sem substituir a pergunta original em lugar nenhum.

DUAS DISCIPLINAS DELIBERADAS

1. FAIL-OPEN. Qualquer falha (LLM fora do ar, resposta ilegível, chave ausente)
   devolve lista vazia e o motor se comporta exatamente como antes. Expansão é
   ganho de recall, nunca uma dependência do caminho principal — por isso ela
   não levanta `RetrievalIndisponivelError` como o Qdrant e o embedding fazem.

2. TERMO EXPANDIDO NÃO É EVIDÊNCIA. O termo é uma HIPÓTESE de tradução feita
   por um modelo, não uma equivalência validada pela empresa. Um documento
   encontrado por "estofamento" não comprova a aplicação "sofá" que o usuário
   pediu. Quem monta o contexto avisa isso ao agente explicitamente (ver
   `app.rag.engine._montar_context_str`), senão a expansão vira uma fonte nova
   de recomendação sem lastro — exatamente o que a regra de evidência do
   AGENT_SYSTEM_PROMPT existe para impedir.
"""
import json
import logging
import re
import threading
from typing import List

import litellm

from app.config import (
    EXPANSAO_CONSULTA_ATIVA,
    EXPANSAO_CONSULTA_MODELO,
    EXPANSAO_MAX_TERMOS,
)

logger = logging.getLogger(__name__)


# Termos que aparecem em praticamente TODO documento do acervo. Como
# discriminantes eles valem zero, e como candidatos de busca são ativamente
# nocivos: enchem o lote de `scroll` (limitado por termo) com documentos
# irrelevantes e empurram para fora o trecho certo. O modelo é instruído a não
# produzi-los, e esta lista é a rede de segurança para quando produzir mesmo
# assim.
_TERMOS_GENERICOS_DEMAIS = {
    "poliuretano", "poliuretanos", "pu", "produto", "produtos", "flexx",
    "boletim", "boletins", "tecnico", "tecnica", "tecnicos", "tecnicas",
    "sistema", "sistemas", "quimico", "quimica", "material", "materiais",
    "aplicacao", "aplicacoes", "industria", "industrial", "fabricacao",
    "componente", "componentes", "formulacao", "cliente", "empresa",
}

_PROMPT_DE_EXPANSAO = """Você traduz a pergunta de um vendedor LEIGO para o vocabulário técnico usado \
em Boletins Técnicos de poliuretano (PU) no Brasil.

Devolva SOMENTE um array JSON de strings, sem nenhum outro texto.

Regras:
- Cada item é um ÚNICO termo técnico (1 ou 2 palavras), em português do Brasil, minúsculo.
- Inclua o termo que a indústria usa no lugar da palavra leiga. Exemplos do que se espera:
  "cola" -> "adesivo"; "borracha" -> "elastomero"; "colchao" -> "espuma flexivel";
  "isopor duro" -> "espuma rigida"; "esponja" -> "espuma"; "endurecedor" -> "catalisador";
  "tinta" -> "verniz"; "enchimento" -> "sistema de vazamento".
- Inclua também o nome do PROCESSO ou da PEÇA quando for evidente na pergunta
  (ex: "laminacao", "injecao", "moldagem", "solado", "painel", "estofamento").
- NÃO inclua termos genéricos que aparecem em qualquer documento do setor:
  poliuretano, pu, produto, sistema, material, quimico, aplicacao, industria, formulacao.
- NÃO invente código de produto, número, marca, norma nem valor de especificação.
- NÃO repita palavras que já estão na pergunta.
- Se a pergunta já estiver em linguagem técnica, devolva [].
- No máximo {maximo} termos, do mais provável para o menos provável.

Pergunta: {pergunta}"""


# Cache de processo. Serve a dois propósitos, e o segundo não é otimização:
# `_montar_context_str` precisa saber quais termos a recuperação usou para
# avisar o agente, e lê essa informação do cache em vez de chamar o LLM de
# novo. Sem o cache, ou pagaríamos a chamada duas vezes ou passaríamos o dado
# por parâmetro através de funções que não têm nada a ver com isso.
_cache: dict[str, List[str]] = {}
_cache_lock = threading.Lock()

# O processo do backend é longo (uvicorn) e atende perguntas distintas o dia
# inteiro; sem teto, o cache cresce sem fim. 512 cobre com folga a repetição
# real de perguntas num turno de trabalho.
_LIMITE_DO_CACHE = 512


def _normalizar(query: str) -> str:
    return " ".join((query or "").lower().split())


def _extrair_lista_json(texto: str) -> List[str]:
    """Lê o array JSON da resposta do modelo, tolerando cerca de ```json.

    Deliberadamente não usa `response_format={"type": "json_object"}`: o
    parâmetro não é suportado por todos os provedores da allowlist deste
    projeto, e um erro de parâmetro aqui derrubaria a expansão em vez de
    degradá-la.
    """
    if not texto:
        return []
    match = re.search(r"\[.*\]", texto, re.DOTALL)
    if not match:
        return []
    try:
        dados = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    if not isinstance(dados, list):
        return []
    return [item for item in dados if isinstance(item, str)]


def _limpar_termos(termos: List[str], query: str) -> List[str]:
    """Descarta o que não ajuda a discriminar: termo genérico demais, termo
    curto demais para o índice de texto do Qdrant (min_token_len=3 em
    `content`) e termo que já estava na pergunta."""
    from app.rag.engine import _normalizar_para_regra  # import tardio: evita ciclo

    ja_na_pergunta = set(re.findall(r"[a-zà-öø-ÿ]{3,}", _normalizar_para_regra(query)))
    limpos = []
    for termo in termos:
        candidato = " ".join(str(termo).lower().split())
        if not candidato or len(candidato) > 40:
            continue
        palavras = candidato.split()
        if len(palavras) > 2:
            continue
        for palavra in palavras:
            normalizada = _normalizar_para_regra(palavra)
            if len(normalizada) < 3:
                continue
            if normalizada in _TERMOS_GENERICOS_DEMAIS:
                continue
            if normalizada in ja_na_pergunta:
                continue
            limpos.append(normalizada)
    return list(dict.fromkeys(limpos))


def termos_expandidos_em_cache(query: str) -> List[str]:
    """Só lê o cache — nunca chama o LLM. É o que `_montar_context_str` usa
    para avisar o agente de que termos traduzidos participaram da busca, sem
    pagar uma segunda chamada nem alterar o custo da consulta."""
    with _cache_lock:
        return list(_cache.get(_normalizar(query), []))


def limpar_cache_de_expansao() -> None:
    """Existe para os testes: sem isso, um caso contamina o seguinte."""
    with _cache_lock:
        _cache.clear()


def expandir_termos_do_dominio(query: str) -> List[str]:
    """Traduz a pergunta leiga em termos do acervo. Lista vazia = sem tradução.

    Nunca levanta exceção: falha de expansão devolve [] e a recuperação segue
    idêntica ao comportamento anterior a este módulo.
    """
    if not EXPANSAO_CONSULTA_ATIVA:
        return []
    chave = _normalizar(query)
    if not chave:
        return []

    with _cache_lock:
        if chave in _cache:
            return list(_cache[chave])

    try:
        resposta = litellm.completion(
            model=EXPANSAO_CONSULTA_MODELO,
            messages=[{
                "role": "user",
                "content": _PROMPT_DE_EXPANSAO.format(
                    maximo=EXPANSAO_MAX_TERMOS, pergunta=query
                ),
            }],
            temperature=0,
            max_tokens=200,
            num_retries=1,
        )
        conteudo = resposta.choices[0].message.content
    except Exception as e:
        # WARNING, não ERROR: o motor continua funcionando sem isto. Mas
        # continua sendo registrado, porque uma expansão silenciosamente morta
        # devolve o sistema ao comportamento que motivou este módulo.
        logger.warning("Expansão de consulta indisponível (%s) — seguindo sem tradução.", e)
        return []

    termos = _limpar_termos(_extrair_lista_json(conteudo), query)[:EXPANSAO_MAX_TERMOS]

    with _cache_lock:
        if len(_cache) >= _LIMITE_DO_CACHE:
            _cache.clear()
        _cache[chave] = list(termos)
    if termos:
        logger.info("Consulta expandida: %r -> %s", query, termos)
    return list(termos)
