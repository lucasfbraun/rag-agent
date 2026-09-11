"""Interpretação e envio dos anexos recebidos pelo campo de chat."""


EXTENSOES_DOCUMENTO = ("pdf", "docx", "doc", "txt")


def separar_entrada_chat(entrada):
    """Normaliza o retorno antigo (texto) e o retorno com anexos do Streamlit."""
    if isinstance(entrada, str):
        return entrada.strip(), []
    texto = (getattr(entrada, "text", "") or "").strip()
    arquivos = list(getattr(entrada, "files", None) or [])
    return texto, arquivos


def enviar_anexos_chat(arquivos, observacao, enviar):
    """Envia todos os anexos, preservando sucesso parcial e erro por arquivo."""
    enviados = []
    falhas = []
    for arquivo in arquivos:
        ok, retorno = enviar(arquivo, observacao)
        if ok:
            enviados.append(arquivo.name)
        else:
            falhas.append((arquivo.name, retorno))
    return enviados, falhas
