"""
Nova capacidade: responder pergunta sobre ESPECIFICAÇÃO TÉCNICA NUMÉRICA
("quero um produto com hidroxila de 180"). O RAG puro nunca respondeu isso —
o embedding não compara grandezas, então o top-k trazia trechos que FALAM de
hidroxila com o valor errado, e o agente apresentava aquilo como resposta.

As amostras de documento abaixo são TEXTO REAL do acervo indexado (coleção
`pu_products_catalog`), com os quatro layouts de tabela que convivem lá — a
ordem das colunas muda de template para template, e é justamente isso que o
parser precisa aguentar sem "adivinhar" qual número é o mínimo.

Seam: `extrair_especificacoes` / `interpretar_consulta_especificacao` puras
(sem I/O) e `buscar_produtos_por_especificacao` com o cliente Qdrant mockado.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.rag.exceptions import RetrievalIndisponivelError
from app.rag.spec_search import (
    _atende_criterio,
    buscar_produtos_por_especificacao,
    buscar_produtos_por_especificacoes,
    extrair_especificacoes,
    interpretar_consulta_especificacao,
    interpretar_consulta_especificacoes,
    resumir_especificacoes_dos_documentos,
)


def test_interpreta_requisitos_compostos_sem_trocar_os_valores():
    criterios = interpretar_consulta_especificacoes(
        "densidade de 35 kg/m³ e dureza Shore A 80"
    )
    assert [(c["propriedade"], c["valor"]) for c in criterios] == [
        ("densidade", 35.0), ("dureza", 80.0),
    ]
    assert criterios[0]["unidade"] == "kg/m³"
    assert criterios[1]["unidade"] == "Shore A"


def test_interpreta_operadores_independentes_em_requisitos_compostos():
    criterios = interpretar_consulta_especificacoes(
        "viscosidade acima de 5000 cPs e NCO entre 12 e 13%"
    )
    assert criterios[0]["operador"] == "maior"
    assert criterios[1]["operador"] == "entre"
    assert criterios[1]["valor_maximo"] == 13.0


@pytest.mark.parametrize(
    ("faixa", "operador", "valor", "valor_maximo", "esperado"),
    [
        ((18.6, 32.9), "menor", 32.0, None, False),
        ((29.0, 31.0), "menor", 32.0, None, True),
        ((4900.0, 5100.0), "maior", 5000.0, None, False),
        ((5100.0, 5500.0), "maior", 5000.0, None, True),
        ((11.5, 12.5), "entre", 12.0, 13.0, False),
        ((12.1, 12.9), "entre", 12.0, 13.0, True),
    ],
)
def test_limites_exigem_faixa_inteira_compativel(
    faixa, operador, valor, valor_maximo, esperado,
):
    especificacao = {"minimo": faixa[0], "maximo": faixa[1]}
    assert _atende_criterio(
        especificacao, operador, valor, valor_maximo, 0.0,
    ) is esperado


def _por_propriedade(content, propriedade):
    return [e for e in extrair_especificacoes(content) if e["propriedade"] == propriedade]


# --- leitura da tabela: os layouts reais do acervo --------------------------

def test_layout_minimo_maximo_resultado():
    """"Índice de hidroxila(mg koh/g) 32,1 34,9 34,0" — colunas Mínimo/Máximo/
    Resultado, unidade colada no rótulo entre parênteses."""
    conteudo = (
        "PARÂMETROS DE CONTROLE Especificação Mínimo Máximo Resultado "
        "Índice de hidroxila(mg koh/g) 32,1 34,9 34,0 Teor de água % ---- 0,10 0,03"
    )
    (spec,) = _por_propriedade(conteudo, "indice_hidroxila")
    assert (spec["minimo"], spec["maximo"]) == (32.1, 34.9)
    assert spec["unidade"] == "mgKOH/g"


def test_layout_resultado_unidade_especificacao():
    """"Número de hidroxilas 34 mgKOH/g 31 - 34" — o resultado vem ANTES da
    unidade. O parser não tenta adivinhar qual coluna é qual; trabalha com a
    faixa observada na célula."""
    (spec,) = _por_propriedade("Número de hidroxilas 34 mgKOH/g 31 - 34 Viscosidade", "indice_hidroxila")
    assert (spec["minimo"], spec["maximo"]) == (31.0, 34.0)


def test_layout_com_travessao_e_resultado_no_fim():
    conteudo = "Índice de hidroxilas mgKOH/g 54,0 – 58,0 56,80 Viscosidade Brookfield a 25 °C cPs 7500 – 8500 7810"
    (hidroxila,) = _por_propriedade(conteudo, "indice_hidroxila")
    (viscosidade,) = _por_propriedade(conteudo, "viscosidade")
    assert (hidroxila["minimo"], hidroxila["maximo"]) == (54.0, 58.0)
    assert (viscosidade["minimo"], viscosidade["maximo"]) == (7500.0, 8500.0)


def test_layout_mais_menos_vira_faixa():
    (spec,) = _por_propriedade("Índice de hidroxilas mgKOH/g 25,5 ± 2,5", "indice_hidroxila")
    assert (spec["minimo"], spec["maximo"]) == (23.0, 28.0)


def test_valores_acima_de_cem_nao_sao_confundidos_com_lote():
    """Poliol de cadeia curta tem hidroxila na casa das centenas ("385 415
    401,5") — o corte por magnitude não pode descartar isso."""
    (spec,) = _por_propriedade("Número de hidroxila mgKOH/g 385 415 401,5 Teor de água % ---- 0,10", "indice_hidroxila")
    assert (spec["minimo"], spec["maximo"]) == (385.0, 415.0)


# --- os dois bugs que custaram caro no parser -------------------------------

def test_temperatura_do_ensaio_nao_vira_valor_da_propriedade():
    """Bug real: "Viscosidade, 25°C cPs 800 a 900" era lido como viscosidade
    25 — e aí QUALQUER produto do acervo "atendia" a uma busca por viscosidade
    baixa. O 25 qualifica a condição do ensaio, não a propriedade.

    A ordem importa e é a parte frágil: `°C` também é unidade válida, então
    detectar unidade ANTES de tirar o qualificador apagava a marca de
    temperatura e deixava o 25 órfão, indistinguível de um valor real."""
    (spec,) = _por_propriedade("Viscosidade, 25°C 830,0 cps 800,0 a 900,0", "viscosidade")
    assert spec["minimo"] == 800.0
    assert spec["unidade"] == "cPs"


def test_temperatura_como_propriedade_pedida_continua_sendo_lida():
    """O filtro acima não pode cegar a leitura de temperatura de verdade."""
    (spec,) = _por_propriedade("Temperatura do molde °C 50 - 60", "temperatura")
    assert (spec["minimo"], spec["maximo"]) == (50.0, 60.0)


def test_expoente_da_unidade_nao_entra_como_valor():
    """"g/cm³" normalizado vira "g/cm3" — sem remover a unidade antes de
    procurar números, esse 3 entrava como se fosse valor de densidade."""
    (spec,) = _por_propriedade("Densidade 1,010 gr/cm³ 1,005 a 1,025 Observações", "densidade")
    assert (spec["minimo"], spec["maximo"]) == (1.005, 1.025)
    assert spec["unidade"] == "g/cm³"


# --- bugs achados rodando o parser sobre o acervo real ---------------------

def test_separador_de_milhar_nao_parte_o_numero_ao_meio():
    """Achado real: "Viscosidade 20.000,00 ± 6.000,00" era lido a partir do
    MEIO do número ("000,00 ± 6.000"), virando 0 ± 6000 — uma faixa de -6000 a
    6000 para uma viscosidade real de 14.000 a 26.000 cPs."""
    (spec,) = _por_propriedade("Viscosidade 20.000,00 ± 6.000,00 Teor de Sólidos 75,5 ± 2,5", "viscosidade")
    assert (spec["minimo"], spec["maximo"]) == (14000.0, 26000.0)


def test_tolerancia_do_tamanho_do_nominal_e_descartada():
    """"Viscosidade (cPs) 12,5 ± 12,5" existe no acervo, e não é uma
    tolerância: é ruído de extração com colunas coladas. Lida ao pé da letra
    viraria a faixa "0 a 25", que casa com quase qualquer valor pedido —
    exatamente o falso positivo que esta busca existe para evitar.

    Uma tolerância legítima continua passando."""
    assert _por_propriedade("Viscosidade (cPs) 12,5 ± 12,5", "viscosidade") == []
    (spec,) = _por_propriedade("Viscosidade (cPs) 2.000,00 ± 500,00", "viscosidade")
    assert (spec["minimo"], spec["maximo"]) == (1500.0, 2500.0)


def test_numeracao_de_secao_da_fispq_nao_vira_valor():
    """Achado real: "Densidade (g/cm³): 1,05 9.10 Solubilidade em água..." — o
    9.10 é o índice da seção seguinte da FISPQ, e virava o máximo da faixa."""
    conteudo = (
        "Densidade (g/cm³): 1,05 9.10 Solubilidade em água: Parcialmente solúvel "
        "9.11 Viscosidade (cPs): 3136 10. ESTABILIDADE E REATIVIDADE"
    )
    (densidade,) = _por_propriedade(conteudo, "densidade")
    (viscosidade,) = _por_propriedade(conteudo, "viscosidade")
    assert (densidade["minimo"], densidade["maximo"]) == (1.05, 1.05)
    assert (viscosidade["minimo"], viscosidade["maximo"]) == (3136.0, 3136.0)


def test_celula_nao_invade_coluna_com_etiqueta_desconhecida():
    """Achado real: "Alongamento (%) | 700,0 Consumo Médio por Demão | 0,4
    Kg/m2*" virava um alongamento de 0,4 a 700%. "Consumo Médio por Demão" não
    está no vocabulário de propriedades nem na lista de marcadores de fim de
    célula — o corte precisa funcionar para etiqueta desconhecida também."""
    (spec,) = _por_propriedade(
        "Alongamento (%) | 700,0 Consumo Médio por Demão | 0,4 Kg/m2*", "alongamento"
    )
    assert (spec["minimo"], spec["maximo"]) == (700.0, 700.0)


def test_propriedade_citada_em_prosa_nao_vira_leitura():
    """Achado rodando a busca contra a coleção real: o boletim do FLEXX AG
    20103 diz "densidade do bloco desejada ... pesar a quantidade de FLEXX A G
    20 103 necessária" — a extração de PDF ainda parte o código do produto em
    "20 103". O parser lia uma densidade de "20 a 103" a partir do NOME DO
    PRODUTO, e o mesmo acontecia em toda a família AG (20106, 20108, 20133...),
    enchendo a busca por densidade de produtos que nem declaram densidade."""
    prosa = (
        "com a quantidade de flocos de acordo com a densidade do bloco desejada; "
        "Em um recipiente pesar a quantidade de FLEXX A G 20 103 necessária"
    )
    assert _por_propriedade(prosa, "densidade") == []


def test_celula_de_tabela_com_unidade_entre_o_rotulo_e_o_numero_continua_valendo():
    """A checagem de prosa não pode derrubar a tabela real: entre o rótulo e o
    número costuma haver unidade, pipe e pontuação — nunca palavra de frase."""
    assert _por_propriedade("Índice de hidroxila(mg koh/g) 32,1 34,9 34,0", "indice_hidroxila")
    assert _por_propriedade("Teor de água % ---- 0,10 0,03", "teor_agua")
    assert _por_propriedade("Alongamento (%) | 700,0", "alongamento")


def test_faixa_larga_demais_e_descartada_em_vez_de_casar_com_tudo():
    """Achado na varredura real: quando a janela atravessa a coluna vizinha sem
    um ponto de corte reconhecível, sai "hidroxila 60 a 16500 mgKOH/g" — e o
    produto passa a casar com QUALQUER valor pedido dentro dessa faixa. Foi o
    que fez "hidroxila de 180" devolver 5 produtos, 4 deles falsos.

    Especificação é faixa de tolerância, não ordem de grandeza: sem leitura
    confiável é melhor não ter leitura."""
    assert _por_propriedade("Índice de hidroxilas mgKOH/g 60 16500 180", "indice_hidroxila") == []
    # A faixa de tolerância larga porém plausível continua valendo.
    (spec,) = _por_propriedade("Teor de água % ---- 0,10 0,03", "teor_agua")
    assert (spec["minimo"], spec["maximo"]) == (0.03, 0.1)


def test_rotulo_longo_vence_o_curto():
    """"Índice de hidroxilas" e "hidroxilas" competem pelo mesmo texto; se o
    curto vencesse, o rótulo guardado como evidência seria o errado."""
    (spec,) = _por_propriedade("Índice de hidroxilas mgKOH/g 28,0 a 32,0", "indice_hidroxila")
    assert spec["rotulo_no_documento"].lower().startswith("índice")


def test_celula_nao_invade_a_propriedade_seguinte():
    conteudo = "Teor de NCO 14,26 % 14,20 a 14,40 Viscosidade cPs 1200 - 1800"
    (nco,) = _por_propriedade(conteudo, "teor_nco")
    assert nco["maximo"] == 14.4


# --- perfil de reatividade: tempos no formato mm`ss`` -----------------------

def test_tempo_no_formato_minuto_segundo_vira_segundos():
    """O acervo escreve "00`09``" / "00’09’’" (minuto`segundo``) e alterna
    entre crase, apóstrofo e aspa curva no MESMO documento. Lido como número
    solto daria 0 e 9 em vez de 9 segundos."""
    conteudo = "Tempo de Creme 00`10`` Min. 00’09’’ a 00’12’’ Tempo de Reação 01`21``"
    (creme,) = _por_propriedade(conteudo, "tempo_creme")
    (reacao,) = _por_propriedade(conteudo, "tempo_reacao")
    assert (creme["minimo"], creme["maximo"]) == (9, 12)
    assert (reacao["minimo"], reacao["maximo"]) == (81, 81)


def test_tempo_longo_em_minutos_nao_e_confundido_com_segundos():
    """"Tempo de Cura 120`00``" são 120 minutos, não 120 segundos."""
    (spec,) = _por_propriedade("Tempo de Cura 120`00`` Min. 90`00`` a 120`00``", "tempo_cura")
    assert (spec["minimo"], spec["maximo"]) == (5400, 7200)


def test_tempo_declarado_em_segundos_puros_tambem_e_lido():
    (spec,) = _por_propriedade("Tempo de Creme seg 10,0 a 15,0", "tempo_creme")
    assert (spec["minimo"], spec["maximo"]) == (10.0, 15.0)


def test_coluna_min_do_cabecalho_nao_vira_unidade_do_tempo():
    """A coluna se chama "Min." mas o dado é minuto`segundo``: herdar "min"
    como unidade daria número certo com rótulo errado."""
    (spec,) = _por_propriedade("Tempo de Fio 04`01`` Min. 04`00`` a 04`30``", "tempo_gel")
    assert spec["unidade"] == "s"


# --- interpretação da pergunta do vendedor ---------------------------------

def test_pedido_pontual_usa_tolerancia():
    criterio = interpretar_consulta_especificacao("quero um produto com hidroxila de 180")
    assert criterio["propriedade"] == "indice_hidroxila"
    assert criterio["operador"] == "igual"
    assert criterio["valor"] == 180.0
    assert criterio["tolerancia_percentual"] > 0


@pytest.mark.parametrize(
    "pergunta,operador,valor",
    [
        ("viscosidade acima de 5000 cPs", "maior", 5000.0),
        ("preciso de NCO abaixo de 10%", "menor", 10.0),
        ("densidade a partir de 35", "maior", 35.0),
    ],
)
def test_operadores_de_comparacao(pergunta, operador, valor):
    criterio = interpretar_consulta_especificacao(pergunta)
    assert (criterio["operador"], criterio["valor"]) == (operador, valor)


def test_faixa_explicita_vira_operador_entre():
    criterio = interpretar_consulta_especificacao("algum sistema com NCO entre 12 e 13%")
    assert criterio["propriedade"] == "teor_nco"
    assert (criterio["operador"], criterio["valor"], criterio["valor_maximo"]) == ("entre", 12.0, 13.0)


def test_tempo_em_minutos_e_convertido_para_segundos():
    criterio = interpretar_consulta_especificacao("quero tempo de reação de 2 minutos")
    assert (criterio["propriedade"], criterio["valor"]) == ("tempo_reacao", 120.0)


def test_faixa_de_tempo_converte_os_dois_extremos():
    """A unidade aparece uma vez só, no fim ("entre 1 e 2 minutos") — aplicar
    o fator só no último extremo deixaria o mínimo em segundos."""
    criterio = interpretar_consulta_especificacao("tempo de cura entre 1 e 2 minutos")
    assert (criterio["valor"], criterio["valor_maximo"]) == (60.0, 120.0)


def test_codigo_de_produto_nao_vira_valor_de_especificacao():
    """Bug esperado se o número do código escapasse: "qual a hidroxila do AG
    2032" viraria uma busca por hidroxila = 2032, que não existe em lugar
    nenhum, e o vendedor receberia "não encontrei" para uma pergunta simples
    de consulta de ficha."""
    assert interpretar_consulta_especificacao(
        "qual a hidroxila do AG 2032", codigos_produto=["ag 2032"]
    ) is None


def test_pergunta_sem_numero_nao_dispara_busca_por_especificacao():
    """Sem número não há critério — e a varredura do acervo inteiro é cara
    demais para rodar em pergunta que não é dela."""
    assert interpretar_consulta_especificacao("qual a viscosidade desse produto") is None


def test_pergunta_sem_propriedade_conhecida_nao_dispara():
    assert interpretar_consulta_especificacao("quero 200 kg de produto para colchão") is None


# --- varredura do acervo ----------------------------------------------------

def _ponto(filepath, filename, content):
    ponto = MagicMock()
    ponto.payload = {"filepath": filepath, "filename": filename, "content": content}
    return ponto


_BASE = r"//10.1.1.205/flexivel/GRUPOS/Qualidade/Documentação de Produto"


def _acervo_fake():
    return [
        _ponto(
            rf"{_BASE}\FLEXX POL\FLEXX POL 1180\Boletim FLEXX POL 1180.pdf",
            "Boletim FLEXX POL 1180.pdf",
            "Índice de hidroxilas mgKOH/g 175,0 – 185,0 Viscosidade cPs 300 a 400",
        ),
        _ponto(
            rf"{_BASE}\FLEXX POL\FLEXX POL 2200\Boletim FLEXX POL 2200.pdf",
            "Boletim FLEXX POL 2200.pdf",
            "Índice de hidroxilas mgKOH/g 28,0 a 32,0",
        ),
        _ponto(
            rf"{_BASE}\FLEXX POL\FLEXX POL 1180\CERTIFICADOS\Certificado 5359.pdf",
            "Certificado FLEXX POL 1180 5359.pdf",
            "Índice de hidroxila (mg koh/g) 176,0 184,0 179,5",
        ),
    ]


def _client_com_acervo():
    client = MagicMock()
    client.scroll.return_value = (_acervo_fake(), None)
    return client


def test_busca_agrupa_por_produto_e_conta_o_acervo_inteiro():
    with patch("app.rag.spec_search.get_qdrant_client", return_value=_client_com_acervo()):
        resultado = buscar_produtos_por_especificacao("indice_hidroxila", 180)

    assert resultado["total"] == 1
    assert resultado["produtos"][0]["produto"] == "FLEXX POL 1180"


def test_boletim_vence_certificado_como_evidencia_do_mesmo_produto():
    """Os dois documentos são do mesmo produto e casam com o critério. O
    Boletim é a especificação de referência; o Certificado vale só para o
    lote analisado (regra já fixada no prompt do agente)."""
    with patch("app.rag.spec_search.get_qdrant_client", return_value=_client_com_acervo()):
        resultado = buscar_produtos_por_especificacao("indice_hidroxila", 180)

    assert resultado["produtos"][0]["tipo_documento"] == "Boletim Técnico"


def test_produto_fora_da_faixa_nao_entra():
    with patch("app.rag.spec_search.get_qdrant_client", return_value=_client_com_acervo()):
        resultado = buscar_produtos_por_especificacao("indice_hidroxila", 180)

    assert all(p["produto"] != "FLEXX POL 2200" for p in resultado["produtos"])


def test_faixa_do_acervo_acompanha_a_resposta_vazia():
    """Sem isso o vendedor recebe um "não encontrei" seco e não sabe se errou
    o número ou se o acervo não tem aquilo. Os valores já foram lidos na
    varredura — informar a faixa não custa nada."""
    with patch("app.rag.spec_search.get_qdrant_client", return_value=_client_com_acervo()):
        resultado = buscar_produtos_por_especificacao("indice_hidroxila", 900)

    assert resultado["total"] == 0
    assert resultado["faixa_no_acervo"]["minimo"] == 28.0
    assert resultado["faixa_no_acervo"]["maximo"] == 185.0


def test_previa_de_dez_por_padrao_e_lista_completa_sob_demanda():
    pontos = [
        _ponto(
            rf"{_BASE}\FLEXX POL\FLEXX POL {i}\Boletim {i}.pdf",
            f"Boletim {i}.pdf",
            "Índice de hidroxilas mgKOH/g 175,0 – 185,0",
        )
        for i in range(20)
    ]
    client = MagicMock()
    client.scroll.return_value = (pontos, None)

    with patch("app.rag.spec_search.get_qdrant_client", return_value=client):
        previa = buscar_produtos_por_especificacao("indice_hidroxila", 180)
        completa = buscar_produtos_por_especificacao("indice_hidroxila", 180, listar_todos=True)

    assert (previa["total"], len(previa["produtos"]), previa["truncado"]) == (20, 10, True)
    assert (len(completa["produtos"]), completa["truncado"]) == (20, False)


def test_propriedade_desconhecida_devolve_erro_legivel_sem_varrer_o_acervo():
    """O argumento vem de um LLM: nome inventado precisa virar um erro que ele
    consegue corrigir, não uma varredura completa devolvendo zero."""
    client = MagicMock()
    with patch("app.rag.spec_search.get_qdrant_client", return_value=client):
        resultado = buscar_produtos_por_especificacao("cor_do_produto", 5)

    assert "erro" in resultado
    assert "indice_hidroxila" in resultado["propriedades_suportadas"]
    client.scroll.assert_not_called()


def test_qdrant_fora_do_ar_levanta_erro_tipado():
    """Mesmo contrato de retrieve_products_context: falha real não pode virar
    "nenhum produto atende", que o agente apresentaria como fato."""
    with patch("app.rag.spec_search.get_qdrant_client", side_effect=ConnectionError("fora do ar")):
        with pytest.raises(RetrievalIndisponivelError):
            buscar_produtos_por_especificacao("indice_hidroxila", 180)


def test_paginacao_percorre_todas_as_paginas():
    """Uma página só cobriria 1000 chunks — o acervo real tem milhares, e
    parar na primeira devolveria uma contagem errada com cara de certa."""
    client = MagicMock()
    client.scroll.side_effect = [
        (_acervo_fake()[:1], "cursor"),
        (_acervo_fake()[1:], None),
    ]
    with patch("app.rag.spec_search.get_qdrant_client", return_value=client):
        resultado = buscar_produtos_por_especificacao("indice_hidroxila", 180)

    assert client.scroll.call_count == 2
    assert resultado["faixa_no_acervo"]["maximo"] == 185.0


def test_busca_composta_faz_uma_varredura_e_exige_todos_os_requisitos():
    pontos = [
        _ponto(
            rf"{_BASE}\FLEXX SIST\FLEXX SIST 100\Boletim 100.pdf",
            "Boletim FLEXX SIST 100.pdf",
            "Densidade kg/m³ 33 a 37 Dureza Shore A 78 a 82",
        ),
        _ponto(
            rf"{_BASE}\FLEXX SIST\FLEXX SIST 200\Boletim 200.pdf",
            "Boletim FLEXX SIST 200.pdf",
            "Densidade kg/m³ 33 a 37 Dureza Shore A 60 a 65",
        ),
        _ponto(
            rf"{_BASE}\FLEXX SIST\FLEXX SIST 300\Boletim 300.pdf",
            "Boletim FLEXX SIST 300.pdf",
            "Densidade kg/m³ 33 a 37",
        ),
    ]
    client = MagicMock()
    client.scroll.return_value = (pontos, None)
    criterios = interpretar_consulta_especificacoes(
        "densidade de 35 kg/m³ e dureza Shore A 80"
    )
    with patch("app.rag.spec_search.get_qdrant_client", return_value=client):
        resultado = buscar_produtos_por_especificacoes(criterios)

    assert client.scroll.call_count == 1
    assert resultado["total"] == 1
    assert resultado["produtos"][0]["produto"] == "FLEXX SIST 100"
    assert len(resultado["produtos"][0]["requisitos"]) == 2


def test_busca_composta_nao_mistura_shore_a_com_shore_d():
    ponto = _ponto(
        rf"{_BASE}\FLEXX SIST\FLEXX SIST 400\Boletim 400.pdf",
        "Boletim FLEXX SIST 400.pdf",
        "Densidade kg/m³ 33 a 37 Dureza Shore D 78 a 82",
    )
    client = MagicMock()
    client.scroll.return_value = ([ponto], None)
    criterios = interpretar_consulta_especificacoes(
        "densidade de 35 kg/m³ e dureza Shore A 80"
    )
    with patch("app.rag.spec_search.get_qdrant_client", return_value=client):
        resultado = buscar_produtos_por_especificacoes(criterios)
    assert resultado["total"] == 0


def test_requisitos_abaixo_exigem_faixa_inteira_e_intersecao():
    """Reprodução da consulta real que listava produtos por apenas um critério
    e aceitava uma faixa cujo máximo ultrapassava o limite solicitado."""
    pontos = [
        _ponto(
            rf"{_BASE}\FLEXX ESP\FLEXX ESP 1\Boletim 1.pdf",
            "Boletim 1.pdf",
            "Densidade livre kg/m³ 18,6 a 32,9 Tempo de pega livre seg 180 a 210",
        ),
        _ponto(
            rf"{_BASE}\FLEXX ESP\FLEXX ESP 2\Boletim 2.pdf",
            "Boletim 2.pdf",
            "Densidade livre kg/m³ 29 a 31 Tempo de pega livre seg 200 a 230",
        ),
        _ponto(
            rf"{_BASE}\FLEXX ESP\FLEXX ESP 3\Boletim 3.pdf",
            "Boletim 3.pdf",
            "Densidade livre kg/m³ 29 a 31 Tempo de pega livre seg 180 a 210",
        ),
        _ponto(
            rf"{_BASE}\FLEXX ESP\FLEXX ESP 4\Boletim 4.pdf",
            "Boletim 4.pdf",
            "Densidade livre kg/m³ 29 a 31",
        ),
    ]
    client = MagicMock()
    client.scroll.return_value = (pontos, None)
    criterios = interpretar_consulta_especificacoes(
        "preciso de um produto com densidade livre abaixo de 32 kg/m³ "
        "e tempo de pega livre abaixo de 220 segundos"
    )

    assert [(c["propriedade"], c["operador"], c["valor"]) for c in criterios] == [
        ("densidade", "menor", 32.0),
        ("tempo_pega", "menor", 220.0),
    ]
    with patch("app.rag.spec_search.get_qdrant_client", return_value=client):
        resultado = buscar_produtos_por_especificacoes(criterios)

    assert resultado["total"] == 1
    assert [item["produto"] for item in resultado["produtos"]] == ["FLEXX ESP 3"]
    assert len(resultado["produtos"][0]["requisitos"]) == 2


# --- ficha estruturada injetada no contexto do LLM --------------------------

def test_resumo_traduz_tabela_embaralhada_em_pares_propriedade_valor():
    docs = [{
        "filename": "Boletim FLEXX POL 1180.pdf",
        "content": "Índice de hidroxilas mgKOH/g 175,0 – 185,0 Viscosidade cPs 300 a 400",
    }]
    resumo = resumir_especificacoes_dos_documentos(docs)
    assert "Boletim FLEXX POL 1180.pdf" in resumo
    assert "Índice de hidroxila: 175 a 185 mgKOH/g" in resumo
    assert "Viscosidade: 300 a 400 cPs" in resumo


def test_resumo_de_documento_sem_tabela_e_vazio():
    """Bloco vazio não pode ir para o prompt: cabeçalho anunciando uma leitura
    estruturada sem nenhuma linha é convite a alucinação."""
    docs = [{"filename": "FISPQ.pdf", "content": "Usar luvas de borracha e óculos de proteção."}]
    assert resumir_especificacoes_dos_documentos(docs) == ""


def test_tempo_no_resumo_sai_legivel_e_sem_unidade_duplicada():
    docs = [{"filename": "Boletim.pdf", "content": "Tempo de Reação 01`21`` Min. 01`15`` a 01`30``"}]
    resumo = resumir_especificacoes_dos_documentos(docs)
    assert "Tempo de reação: 1 min 15 s a 1 min 30 s" in resumo
    assert " s s" not in resumo
