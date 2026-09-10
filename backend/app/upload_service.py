"""
Fila de aprovação de documentos (Sessão 38).

Pedido do usuário: permitir que perfis autorizados enviem arquivos para o
acervo, **com aprovação antes de entrar** — decisão dele entre as três opções
que apresentei.

Por que a fila e não indexação direta: o acervo é a fonte que o agente cita
como verdade para a equipe inteira. Um PDF errado entrando sozinho contamina as
respostas de todo mundo, e o estrago só aparece quando alguém desconfia de uma
recomendação — muito depois, e sem ligação óbvia com o upload.

Enviar e aprovar são permissões SEPARADAS (`UPLOAD_DOCUMENTS` e
`APPROVE_UPLOADS`): quem está em campo é quem tem o boletim que falta, mas não
é necessariamente quem decide o que a base passa a afirmar.
"""
import os
import unicodedata
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentoEnviado, StatusDocumento, User

# Onde os arquivos ficam. Montado como volume no docker-compose — sem isso,
# tudo que estivesse na fila sumiria no primeiro rebuild da imagem.
PASTA_UPLOADS = os.getenv("UPLOAD_DIR", "/app/data/uploads")

# Formatos que o pipeline consegue LER. Imagem fica de fora de propósito: sem
# OCR instalado, um JPG entraria na fila, seria aprovado e não acrescentaria
# nada ao índice — um arquivo inútil que ninguém entenderia por que não
# aparece nas respostas. Recusar na entrada, com a explicação, é mais honesto
# que criar um cemitério de arquivos.
EXTENSOES_ACEITAS = (".pdf", ".docx", ".doc", ".txt")

# 25 MB. Boletim técnico raramente passa de 5; acima disso é quase sempre
# digitalização em alta resolução, que além de pesada não tem texto extraível.
TAMANHO_MAXIMO_BYTES = 25 * 1024 * 1024


class DocumentoInvalidoError(ValueError):
    """Arquivo recusado na entrada — formato, tamanho ou nome."""


class DocumentoNaoEncontradoError(ValueError):
    """Nenhum documento com esse id."""


class DecisaoInvalidaError(ValueError):
    """Documento já decidido — aprovar/recusar duas vezes não é idempotente:
    a segunda aprovação reindexaria o mesmo arquivo."""


class FalhaAoIndexarError(RuntimeError):
    """O arquivo foi aprovado mas não pôde ser indexado (sem texto extraível,
    Qdrant fora do ar). O documento continua PENDENTE."""


def _nome_seguro(nome: str) -> str:
    """Nome de arquivo sem acento, espaço ou separador de caminho.

    O nome vem do cliente: um "../../etc/passwd" ou um nome com barra
    escreveria fora da pasta de uploads. A defesa é allowlist de caracteres,
    não uma lista de coisas proibidas."""
    limpo = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode()
    limpo = "".join(c if (c.isalnum() or c in "._-") else "_" for c in limpo)
    limpo = limpo.strip("._") or "documento"
    return limpo[:120]


def validar(nome_arquivo: str, tamanho: int) -> None:
    extensao = os.path.splitext(nome_arquivo or "")[1].lower()
    if extensao not in EXTENSOES_ACEITAS:
        raise DocumentoInvalidoError(
            f"Formato '{extensao or 'sem extensão'}' não é aceito. "
            f"Envie {', '.join(EXTENSOES_ACEITAS)}. "
            "Imagens e PDFs digitalizados precisam de OCR, que não está instalado."
        )
    if tamanho <= 0:
        raise DocumentoInvalidoError("Arquivo vazio.")
    if tamanho > TAMANHO_MAXIMO_BYTES:
        raise DocumentoInvalidoError(
            f"Arquivo de {tamanho / 1024 / 1024:.1f} MB — o limite é "
            f"{TAMANHO_MAXIMO_BYTES // 1024 // 1024} MB."
        )


def registrar_envio(
    session: Session, *, usuario: User, nome_arquivo: str, conteudo: bytes,
    observacao: str | None = None,
) -> DocumentoEnviado:
    """Grava o arquivo em disco e o põe na fila como PENDENTE."""
    validar(nome_arquivo, len(conteudo))

    documento_id = uuid.uuid4()
    # O id no nome evita colisão quando duas pessoas mandam "boletim.pdf" — e
    # mantém o nome original legível para quem vai aprovar.
    os.makedirs(PASTA_UPLOADS, exist_ok=True)
    caminho = os.path.join(PASTA_UPLOADS, f"{documento_id}_{_nome_seguro(nome_arquivo)}")
    with open(caminho, "wb") as arquivo:
        arquivo.write(conteudo)

    documento = DocumentoEnviado(
        id=documento_id, nome_arquivo=nome_arquivo, caminho=caminho,
        tamanho_bytes=len(conteudo), status=StatusDocumento.PENDENTE,
        observacao=(observacao or "").strip() or None, enviado_por_id=usuario.id,
    )
    session.add(documento)
    session.flush()
    return documento


def listar(session: Session, *, status: StatusDocumento | None = None,
           enviado_por: User | None = None) -> list[DocumentoEnviado]:
    consulta = select(DocumentoEnviado).order_by(DocumentoEnviado.created_at.desc())
    if status is not None:
        consulta = consulta.where(DocumentoEnviado.status == status)
    if enviado_por is not None:
        consulta = consulta.where(DocumentoEnviado.enviado_por_id == enviado_por.id)
    return list(session.execute(consulta).scalars())


def _obter_pendente(session: Session, documento_id) -> DocumentoEnviado:
    documento = session.get(DocumentoEnviado, documento_id)
    if documento is None:
        raise DocumentoNaoEncontradoError(f"Documento {documento_id} não encontrado.")
    if documento.status != StatusDocumento.PENDENTE:
        raise DecisaoInvalidaError(
            f"Este documento já foi {documento.status.value}. "
            "Uma segunda aprovação reindexaria o mesmo arquivo."
        )
    return documento


def aprovar(session: Session, documento_id, *, aprovador: User) -> DocumentoEnviado:
    """Indexa o arquivo e marca como aprovado.

    A ordem importa: **indexa PRIMEIRO, marca depois.** Se marcasse antes e a
    indexação falhasse, o documento apareceria como aprovado sem estar no
    índice — e ninguém descobriria até alguém reparar que o agente ignora um
    boletim que "está lá"."""
    from app.rag.ingestion import indexar_arquivo

    documento = _obter_pendente(session, documento_id)
    try:
        chunks = indexar_arquivo(
            documento.caminho,
            metadados_extra={
                "origem": "upload",
                "documento_id": str(documento.id),
                "enviado_por": documento.enviado_por.username,
            },
        )
    except Exception as e:
        raise FalhaAoIndexarError(str(e)) from e

    documento.status = StatusDocumento.APROVADO
    documento.decidido_por_id = aprovador.id
    documento.decidido_em = datetime.now(timezone.utc)
    documento.chunks_indexados = chunks
    session.flush()
    return documento


def recusar(session: Session, documento_id, *, aprovador: User,
            motivo: str) -> DocumentoEnviado:
    """Recusa com motivo OBRIGATÓRIO — quem enviou precisa saber o que
    corrigir, senão vai reenviar o mesmo arquivo."""
    if not (motivo or "").strip():
        raise DecisaoInvalidaError("Informe o motivo da recusa.")

    documento = _obter_pendente(session, documento_id)
    documento.status = StatusDocumento.REJEITADO
    documento.decidido_por_id = aprovador.id
    documento.decidido_em = datetime.now(timezone.utc)
    documento.motivo_decisao = motivo.strip()
    session.flush()
    return documento


def remover_do_acervo(session: Session, documento_id, *, aprovador: User,
                      motivo: str) -> DocumentoEnviado:
    """Tira do índice um documento já aprovado.

    Sem isto, aprovar um arquivo errado não teria volta a não ser reindexando o
    acervo inteiro. Os pontos são localizados pelo `filepath` gravado no
    payload, então a remoção não toca em mais nada."""
    from app.rag.ingestion import remover_arquivo_do_indice

    documento = session.get(DocumentoEnviado, documento_id)
    if documento is None:
        raise DocumentoNaoEncontradoError(f"Documento {documento_id} não encontrado.")
    if documento.status != StatusDocumento.APROVADO:
        raise DecisaoInvalidaError("Só documentos aprovados estão no índice.")

    remover_arquivo_do_indice(documento.caminho)
    documento.status = StatusDocumento.REJEITADO
    documento.decidido_por_id = aprovador.id
    documento.decidido_em = datetime.now(timezone.utc)
    documento.motivo_decisao = f"Removido do acervo: {motivo.strip()}"
    documento.chunks_indexados = 0
    session.flush()
    return documento
