"""
Testes de `tools/diagnostico_extracao.py`.

O valor do script está inteiro na CLASSIFICAÇÃO: se ele contar um .doc legado
como "corrompido", a decisão que sai dele é "resgatar arquivo a arquivo" em vez
de "converter tudo em lote com LibreOffice" — e a diferença entre as duas é
semanas de trabalho da Qualidade. Por isso cada categoria é testada contra um
arquivo DE VERDADE em pasta temporária, não contra um mock da extração.

Nada aqui depende do acervo real, de rede, do Qdrant ou do PostgreSQL.
"""
import importlib.util
import os
import sys

import pytest
from pypdf import PdfWriter

_RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODULO = os.path.join(_RAIZ, "tools", "diagnostico_extracao.py")


def _carregar_modulo():
    """Importa o script por caminho: `tools/` não é um pacote instalável, e
    transformá-lo em um só para o teste importar seria inverter a relação —
    a ferramenta é um script solto de propósito, para rodar no servidor sem
    instalação nenhuma."""
    spec = importlib.util.spec_from_file_location("diagnostico_extracao", _MODULO)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["diagnostico_extracao"] = modulo
    spec.loader.exec_module(modulo)
    return modulo


diag = _carregar_modulo()


# --- fábricas de arquivo real ---------------------------------------------


def _pdf_sem_camada_de_texto(destino: str, paginas: int = 2) -> str:
    """Um PDF válido, com páginas, sem nenhum texto extraível — que é
    exatamente o que um boletim escaneado é para o pypdf."""
    escritor = PdfWriter()
    for _ in range(paginas):
        escritor.add_blank_page(width=595, height=842)
    with open(destino, "wb") as f:
        escritor.write(f)
    return destino


def _pdf_protegido(destino: str) -> str:
    escritor = PdfWriter()
    escritor.add_blank_page(width=595, height=842)
    escritor.encrypt("senha-que-ninguem-tem")
    with open(destino, "wb") as f:
        escritor.write(f)
    return destino


def _escrever(destino: str, conteudo) -> str:
    modo = "wb" if isinstance(conteudo, bytes) else "w"
    kwargs = {} if isinstance(conteudo, bytes) else {"encoding": "utf-8"}
    with open(destino, modo, **kwargs) as f:
        f.write(conteudo)
    return destino


# --- classificação --------------------------------------------------------


def test_pdf_com_paginas_e_sem_texto_e_escaneado(tmp_path):
    caminho = _pdf_sem_camada_de_texto(str(tmp_path / "boletim_antigo.pdf"), paginas=3)

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.PDF_ESCANEADO
    assert r.paginas == 3, "a contagem de páginas dimensiona o volume de OCR"
    assert not r.aproveitavel


def test_pdf_protegido_nao_e_confundido_com_escaneado(tmp_path):
    """OCR não resolve PDF criptografado — a ação é outra (pedir o original
    sem senha), então a categoria precisa ser outra."""
    caminho = _pdf_protegido(str(tmp_path / "homologacao.pdf"))

    assert diag.classificar_arquivo(caminho).status == diag.PDF_PROTEGIDO


def test_doc_binario_legado_e_reconhecido_pela_assinatura(tmp_path):
    """python-docx só abre OOXML. Um .doc de verdade começa com a assinatura
    OLE2 — é isso, e não a mensagem da exceção, que identifica a categoria."""
    caminho = _escrever(
        str(tmp_path / "fispq_1998.doc"),
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600,
    )

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.DOC_LEGADO
    assert "OLE2" in r.detalhe


def test_docx_com_extensao_mentirosa_tambem_e_doc_legado(tmp_path):
    """Renomear .doc para .docx é prática comum em acervo antigo e não
    converte nada. A ação continua sendo conversão em lote."""
    caminho = _escrever(
        str(tmp_path / "renomeado.docx"),
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x11" * 400,
    )

    assert diag.classificar_arquivo(caminho).status == diag.DOC_LEGADO


def test_txt_vazio_e_arquivo_vazio(tmp_path):
    caminho = _escrever(str(tmp_path / "vazio.txt"), "")

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.ARQUIVO_VAZIO
    assert r.caracteres == 0


def test_txt_so_com_espacos_tambem_e_vazio(tmp_path):
    caminho = _escrever(str(tmp_path / "brancos.txt"), "   \n\n\t  \n")

    assert diag.classificar_arquivo(caminho).status == diag.ARQUIVO_VAZIO


def test_texto_curto_demais_nao_vira_chunk_e_conta_como_perda(tmp_path):
    """Menos de 40 caracteres: `chunk_text` descarta, a extração "funciona" e
    mesmo assim NADA é indexado. Contar isso como sucesso seria mentir."""
    caminho = _escrever(str(tmp_path / "carimbo.txt"), "FLEXX AG 2032")

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.SEM_CHUNK
    assert r.chunks == 0
    assert not r.aproveitavel


def test_texto_pobre_passa_mas_e_sinalizado(tmp_path):
    """Entre 40 e 200 caracteres o arquivo vira um chunk raso: entra no
    índice, ocupa vaga do top_k e nunca é um bom resultado. Passa, com
    ressalva."""
    caminho = _escrever(
        str(tmp_path / "cabecalho.txt"),
        "Boletim Tecnico FLEXX AG 2032 - revisao 4 - pagina 1 de 3",
    )

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.OK_POBRE
    assert r.aproveitavel, "é aproveitável de fato: um chunk é gerado"
    assert r.chunks == 1


def test_texto_normal_e_ok(tmp_path):
    caminho = _escrever(str(tmp_path / "boletim.txt"), "densidade shore reatividade " * 60)

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.OK
    assert r.chunks >= 1
    assert r.caracteres > diag.LIMIAR_TEXTO_POBRE
    assert r.hash_conteudo, "o hash sai da mesma extração, para não extrair duas vezes"


def test_pdf_corrompido_nao_derruba_a_varredura(tmp_path):
    caminho = _escrever(str(tmp_path / "quebrado.pdf"), b"%PDF-1.4\nlixo binario aqui")

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.CORROMPIDO
    assert r.detalhe, "a exceção original tem que sobreviver no relatório"


def test_docx_corrompido_nao_e_doc_legado(tmp_path):
    """Não é zip nem OLE2: conversão em lote não resolveria, só resgate."""
    caminho = _escrever(str(tmp_path / "quebrado.docx"), b"nada disso e um documento")

    assert diag.classificar_arquivo(caminho).status == diag.CORROMPIDO


def test_extensao_nao_suportada_nao_e_contada_como_falha(tmp_path):
    """Uma planilha nunca chegou perto do índice, mas também não é um arquivo
    "que falhou na extração" — misturar as duas coisas inflaria a fila de
    OCR com trabalho que OCR não faz."""
    caminho = _escrever(str(tmp_path / "tabela.xlsx"), b"PK\x03\x04qualquercoisa")

    r = diag.classificar_arquivo(caminho)

    assert r.status == diag.NAO_SUPORTADO
    assert r.status not in diag.STATUS_DE_FALHA


def test_arquivo_inexistente_vira_erro_e_nao_excecao(tmp_path):
    r = diag.classificar_arquivo(str(tmp_path / "nao_existe.pdf"))

    assert r.status in diag.STATUS_DE_FALHA


# --- varredura ------------------------------------------------------------


def test_varredura_sobrevive_a_excecao_inesperada(tmp_path, monkeypatch):
    """Uma varredura de horas não pode morrer no arquivo 3.000 de 12.000."""
    bom = _escrever(str(tmp_path / "bom.txt"), "espuma flexivel de bloco " * 40)
    explosivo = _escrever(str(tmp_path / "explosivo.txt"), "qualquer coisa " * 40)

    original = diag.classificar_arquivo

    def _explode(caminho):
        if caminho == explosivo:
            raise MemoryError("estouro inesperado")
        return original(caminho)

    monkeypatch.setattr(diag, "classificar_arquivo", _explode)

    resultados = diag.varrer([explosivo, bom])

    assert len(resultados) == 2
    assert resultados[0].status == diag.ERRO_DESCONHECIDO
    assert resultados[1].status == diag.OK


def test_duplicata_de_conteudo_nao_conta_duas_vezes(tmp_path):
    """Mesmo filtro da ingestão: dois caminhos com o mesmo texto rendem UM
    documento indexado. Sem isso o diagnóstico contaria como acervo algo que
    a ingestão descarta."""
    texto = "adesivo de poliuretano para rolha de cortica " * 20
    (tmp_path / "atual").mkdir()
    (tmp_path / "restaurado").mkdir()
    primeiro = _escrever(str(tmp_path / "atual" / "boletim.txt"), texto)
    segundo = _escrever(str(tmp_path / "restaurado" / "boletim.txt"), texto)

    resultados = diag.varrer([primeiro, segundo])

    assert resultados[0].status == diag.OK
    assert resultados[1].status == diag.DUPLICATA_CONTEUDO
    assert primeiro in resultados[1].detalhe


def test_progresso_e_chamado_durante_a_varredura(tmp_path):
    """Sem sinal de vida o usuário conclui que travou e mata o processo."""
    arquivos = [
        _escrever(str(tmp_path / f"a{i}.txt"), "densidade e shore " * 40) for i in range(4)
    ]
    chamadas = []

    diag.varrer(arquivos, intervalo_progresso=2, escrever_progresso=lambda *a: chamadas.append(a))

    assert len(chamadas) == 2
    assert chamadas[-1][0] == 4 and chamadas[-1][1] == 4


# --- seleção e amostragem -------------------------------------------------


def test_selecao_aplica_os_filtros_de_deduplicacao_da_ingestao(tmp_path):
    """Mesmo nome, mesma pasta, só muda a extensão: a ingestão indexa um só
    (PDF ganha). Contar os dois inflaria o denominador do diagnóstico."""
    pdf = _pdf_sem_camada_de_texto(str(tmp_path / "boletim.pdf"))
    docx = _escrever(str(tmp_path / "boletim.docx"), b"PK\x03\x04")
    planilha = _escrever(str(tmp_path / "ensaio.xlsx"), b"PK\x03\x04")

    a_varrer, por_formato, nao_suportados = diag.selecionar_arquivos([pdf, docx, planilha])

    assert a_varrer == [pdf]
    assert por_formato == [docx]
    assert nao_suportados == [planilha]


def test_amostra_atravessa_a_arvore_em_vez_de_pegar_os_primeiros(tmp_path):
    """Os N primeiros de uma lista ordenada por caminho são uma família de
    produto só — a amostra descreveria uma pasta, não o acervo."""
    arquivos = [f"/acervo/fam{i:03d}/doc.pdf" for i in range(100)]

    amostra = diag.amostrar(arquivos, 5)

    assert len(amostra) == 5
    assert amostra[0] == arquivos[0]
    assert amostra[-1] != arquivos[4], "não pode ser só o começo da árvore"
    assert arquivos.index(amostra[-1]) >= 75


def test_amostra_sem_limite_devolve_tudo():
    arquivos = ["a.pdf", "b.pdf"]

    assert diag.amostrar(arquivos, None) == arquivos
    assert diag.amostrar(arquivos, 99) == arquivos


def test_listagem_pega_arquivo_sem_ponto_no_nome(tmp_path):
    """`glob("**/*.*")` da ingestão ignora arquivo sem extensão. Para o censo
    isso importa: um arquivo que a ingestão nem enxerga ainda é um arquivo do
    acervo."""
    _escrever(str(tmp_path / "LEIAME"), "sem extensao")
    _escrever(str(tmp_path / "boletim.txt"), "com extensao")

    assert len(diag.listar_arquivos(str(tmp_path))) == 2


# --- agregação ------------------------------------------------------------


def _resultado(caminho, status):
    r = diag.ResultadoArquivo(caminho, status)
    r.produto = diag._produto_do_filepath(caminho)
    return r


def test_produto_sem_nenhum_documento_aproveitavel_e_cego():
    """O número que decide a prioridade: não "arquivos perdidos", mas
    produtos que o agente é incapaz de encontrar, faça o que fizer."""
    resultados = [
        _resultado("/acervo/FLEXX AG/FLEXX AG 2032/boletim.pdf", diag.OK),
        _resultado("/acervo/FLEXX AG/FLEXX AG 2032/fispq.pdf", diag.PDF_ESCANEADO),
        _resultado("/acervo/FLEXX BT/FLEXX BT 900/boletim.pdf", diag.PDF_ESCANEADO),
        _resultado("/acervo/FLEXX BT/FLEXX BT 900/homologacao.doc", diag.DOC_LEGADO),
    ]

    cegos, servidos = diag.produtos_sem_documento(resultados)

    assert cegos == ["FLEXX BT 900"]
    assert servidos == ["FLEXX AG 2032"], "um documento aproveitável já salva o produto"


def test_produto_cego_por_texto_curto_demais_conta_igual():
    """Extraiu 20 caracteres e não gerou chunk: o produto está tão invisível
    quanto o escaneado."""
    resultados = [_resultado("/acervo/Linha/FLEXX CL 10/capa.pdf", diag.SEM_CHUNK)]

    cegos, servidos = diag.produtos_sem_documento(resultados)

    assert cegos == ["FLEXX CL 10"] and servidos == []


def test_relatorio_traz_os_cinco_blocos_e_uma_leitura(tmp_path):
    v = diag.Varredura(
        resultados=[
            _resultado("/acervo/L/P1/a.pdf", diag.OK),
            _resultado("/acervo/L/P2/b.pdf", diag.PDF_ESCANEADO),
        ],
        total_na_arvore=2,
        extensoes=diag.Counter({".pdf": 2}),
    )
    v.resultados[0].caracteres = 3000
    v.resultados[0].chunks = 2

    texto = diag.montar_relatorio(v, str(tmp_path))

    for esperado in ("1. O QUE EXISTE", "2. QUANTO RENDE", "3. POR QUE OS PERDIDOS",
                     "4. QUALIDADE DO QUE PASSA", "5. PRODUTOS SEM NENHUM", "RESUMO:"):
        assert esperado in texto
    assert "OCR" in texto, "a ação sugerida é o que torna o número acionável"


def test_relatorio_avisa_quando_e_amostra(tmp_path):
    """Confundir amostra com censo faria a Qualidade dimensionar o OCR pelo
    número errado."""
    v = diag.Varredura(
        resultados=[_resultado("/acervo/L/P1/a.pdf", diag.OK)],
        total_na_arvore=500,
        extensoes=diag.Counter({".pdf": 500}),
        amostrada=True,
    )

    assert "AMOSTRA" in diag.montar_relatorio(v, str(tmp_path))


def test_relatorio_de_varredura_interrompida_avisa_que_e_parcial(tmp_path):
    v = diag.Varredura(
        resultados=[_resultado("/acervo/L/P1/a.pdf", diag.OK)],
        total_na_arvore=1,
        extensoes=diag.Counter({".pdf": 1}),
        interrompida=True,
    )

    assert "INTERROMPIDA" in diag.montar_relatorio(v, str(tmp_path))


# --- CSV ------------------------------------------------------------------


def test_csv_lista_so_os_problemas_com_acao_sugerida(tmp_path):
    destino = str(tmp_path / "problemas.csv")
    resultados = [
        _resultado("/acervo/L/P1/bom.pdf", diag.OK),
        _resultado("/acervo/L/P2/escaneado.pdf", diag.PDF_ESCANEADO),
        _resultado("/acervo/L/P3/legado.doc", diag.DOC_LEGADO),
    ]

    quantidade = diag.escrever_csv(destino, resultados)

    with open(destino, encoding="utf-8-sig") as f:
        linhas = f.read().splitlines()
    assert quantidade == 2
    assert linhas[0].split(";")[:4] == ["caminho", "produto", "extensao", "status"]
    assert "escaneado.pdf" in linhas[1] and "OCR" in linhas[1]
    assert "bom.pdf" not in "\n".join(linhas)


def test_csv_todos_inclui_os_aprovados(tmp_path):
    destino = str(tmp_path / "tudo.csv")
    resultados = [_resultado("/acervo/L/P1/bom.pdf", diag.OK)]

    assert diag.escrever_csv(destino, resultados, todos=True) == 1


def test_csv_preserva_acento_no_caminho(tmp_path):
    """O acervo inteiro é escrito em português; um CSV que quebre o acento é
    inútil para achar o arquivo depois."""
    destino = str(tmp_path / "acento.csv")
    resultados = [_resultado("/acervo/Documentação/Produção/ficha.pdf", diag.PDF_ESCANEADO)]

    diag.escrever_csv(destino, resultados)

    with open(destino, encoding="utf-8-sig") as f:
        assert "Documentação" in f.read()


# --- CLI ------------------------------------------------------------------


def test_pasta_inacessivel_sai_com_codigo_de_erro(tmp_path, capsys):
    """No servidor, o modo de falha mais provável é o compartilhamento não
    estar montado — e isso precisa ser dito, não virar "0 arquivos"."""
    codigo = diag.main([str(tmp_path / "nao_existe")])

    assert codigo == 2
    assert "montado" in capsys.readouterr().out


def test_execucao_ponta_a_ponta_em_arvore_temporaria(tmp_path, capsys):
    (tmp_path / "FLEXX AG" / "FLEXX AG 2032").mkdir(parents=True)
    (tmp_path / "FLEXX BT" / "FLEXX BT 900").mkdir(parents=True)
    _escrever(str(tmp_path / "FLEXX AG" / "FLEXX AG 2032" / "b.txt"), "densidade shore " * 40)
    _pdf_sem_camada_de_texto(str(tmp_path / "FLEXX BT" / "FLEXX BT 900" / "b.pdf"))
    destino = str(tmp_path / "saida" / "problemas.csv")

    codigo = diag.main([str(tmp_path), "--csv", destino])

    saida = capsys.readouterr().out
    assert codigo == 0
    assert "DIAGNÓSTICO DE EXTRAÇÃO" in saida
    assert "FLEXX BT 900" in saida, "o produto cego precisa aparecer nominalmente"
    assert os.path.exists(destino)


def test_limite_reduz_a_varredura(tmp_path, capsys):
    for i in range(6):
        _escrever(str(tmp_path / f"doc{i}.txt"), "espuma rigida " * 40)

    diag.main([str(tmp_path), "--limite", "2"])

    saida = capsys.readouterr().out
    assert "varrendo 2" in saida
    assert "AMOSTRA" in saida


def test_diagnostico_nao_abre_conexao_com_qdrant(tmp_path, monkeypatch):
    """A garantia central da ferramenta: ela roda em produção, no acervo, sem
    encostar no índice. Se algum dia alguém importar uma função que conecta,
    este teste quebra antes de o script rodar no servidor."""
    from app.rag import ingestion

    def _proibido(*a, **k):
        raise AssertionError("o diagnóstico não pode falar com o Qdrant")

    monkeypatch.setattr(ingestion, "get_qdrant_client", _proibido)
    _escrever(str(tmp_path / "b.txt"), "densidade " * 40)

    assert diag.main([str(tmp_path)]) == 0


@pytest.mark.parametrize("status", diag.STATUS_DE_FALHA + (diag.OK_POBRE,))
def test_toda_categoria_acionavel_tem_acao_sugerida(status):
    """Uma categoria sem ação é uma linha de relatório que não decide nada —
    exatamente o defeito que este script existe para corrigir."""
    assert diag.ACAO_POR_STATUS.get(status)
