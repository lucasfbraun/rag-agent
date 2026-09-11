from types import SimpleNamespace

from chat_upload import enviar_anexos_chat, separar_entrada_chat


def test_entrada_com_anexos_separa_observacao_e_arquivos():
    arquivos = [SimpleNamespace(name="boletim.pdf"), SimpleNamespace(name="fispq.docx")]

    texto, recebidos = separar_entrada_chat(
        SimpleNamespace(text="  revisão 03  ", files=arquivos)
    )

    assert texto == "revisão 03"
    assert recebidos == arquivos


def test_entrada_sem_anexo_preserva_mensagem_normal_do_chat():
    assert separar_entrada_chat("  qual a densidade?  ") == ("qual a densidade?", [])


def test_envio_multiplo_informa_sucessos_e_falhas_separadamente():
    arquivos = [SimpleNamespace(name="bom.pdf"), SimpleNamespace(name="ruim.pdf")]
    chamadas = []

    def enviar(arquivo, observacao):
        chamadas.append((arquivo.name, observacao))
        return (True, {}) if arquivo.name == "bom.pdf" else (False, "formato inválido")

    enviados, falhas = enviar_anexos_chat(arquivos, "revisão nova", enviar)

    assert chamadas == [("bom.pdf", "revisão nova"), ("ruim.pdf", "revisão nova")]
    assert enviados == ["bom.pdf"]
    assert falhas == [("ruim.pdf", "formato inválido")]
