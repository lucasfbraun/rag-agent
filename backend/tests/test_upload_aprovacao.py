"""
Fila de aprovação de documentos (Sessão 38).

Pedido do usuário: perfis autorizados podem enviar arquivos para o acervo,
**com aprovação antes de entrar** — escolha dele entre as três opções que
apresentei.

O que estes testes protegem, em ordem de gravidade:

  1. **Aprovar indexa ANTES de marcar.** Se marcasse primeiro e a indexação
     falhasse, o documento apareceria como aprovado sem estar no índice — e
     ninguém descobriria até reparar que o agente ignora um boletim que
     "está lá".
  2. **Aprovar duas vezes não pode reindexar.** A operação não é idempotente.
  3. **Quem só envia não enxerga a fila dos outros** — material que a empresa
     ainda não validou.
  4. **Nome de arquivo do cliente não escreve fora da pasta** de uploads.
  5. **Aprovado tem volta**: remover tira do índice sem tocar no resto.

Seam: Postgres real (a fila é estado persistido e as regras dependem dele);
`indexar_arquivo`/`remover_arquivo_do_indice` são mockados — o Qdrant é
exercitado de verdade em test_ingestion_*.py, e uma suíte que indexasse a cada
teste levaria minutos.
"""
import os
import uuid
from unittest.mock import patch

import pytest

from app.auth.permissions import Permission, has_permission
from app.auth.user_service import create_user
from app.db import SessionLocal
from app.models import DocumentoEnviado, StatusDocumento, User
from app.upload_service import (
    DecisaoInvalidaError,
    DocumentoInvalidoError,
    FalhaAoIndexarError,
    TAMANHO_MAXIMO_BYTES,
    _nome_seguro,
    aprovar,
    listar,
    recusar,
    registrar_envio,
    remover_do_acervo,
    validar,
)

CONTEUDO = b"Boletim tecnico FLEXX AG 2032. Indice de hidroxilas mgKOH/g 28,0 a 32,0."


@pytest.fixture
def session(tmp_path, monkeypatch):
    # Escreve num diretório temporário, nunca na pasta real de uploads.
    monkeypatch.setattr("app.upload_service.PASTA_UPLOADS", str(tmp_path))
    s = SessionLocal()
    lixo = {"usuarios": [], "documentos": []}
    try:
        yield s, lixo
    finally:
        s.rollback()
        for doc_id in lixo["documentos"]:
            registro = s.get(DocumentoEnviado, doc_id)
            if registro is not None:
                s.delete(registro)
        s.commit()
        for user_id in lixo["usuarios"]:
            registro = s.get(User, user_id)
            if registro is not None:
                s.delete(registro)
        s.commit()
        s.close()


def _usuario(s, lixo, perfil="vendedor"):
    sufixo = uuid.uuid4().hex[:8]
    user = create_user(
        s, username=f"upload.{sufixo}", nome=f"Usuario {sufixo}",
        email=f"upload.{sufixo}@grupoflexivel.com.br",
        password="SenhaLocal123", perfil=perfil,
    )
    s.commit()
    lixo["usuarios"].append(user.id)
    return user


def _enviar(s, lixo, usuario, nome="boletim.pdf", conteudo=CONTEUDO, observacao="revisao 03"):
    documento = registrar_envio(
        s, usuario=usuario, nome_arquivo=nome, conteudo=conteudo, observacao=observacao
    )
    s.commit()
    lixo["documentos"].append(documento.id)
    return documento


# --- validação na entrada ---------------------------------------------------

def test_formato_nao_suportado_e_recusado_com_explicacao():
    """Imagem fica de fora de propósito: sem OCR, um JPG seria aprovado e não
    acrescentaria nada ao índice — um arquivo inútil que ninguém entenderia
    por que não aparece nas respostas."""
    with pytest.raises(DocumentoInvalidoError, match="OCR"):
        validar("foto.jpg", 1000)


def test_arquivo_vazio_e_recusado():
    with pytest.raises(DocumentoInvalidoError, match="vazio"):
        validar("boletim.pdf", 0)


def test_arquivo_grande_demais_e_recusado():
    with pytest.raises(DocumentoInvalidoError, match="limite"):
        validar("boletim.pdf", TAMANHO_MAXIMO_BYTES + 1)


@pytest.mark.parametrize("nome,proibido", [
    ("../../etc/passwd", ".."),
    ("pasta/arquivo.pdf", "/"),
    ("pasta\\arquivo.pdf", "\\"),
])
def test_nome_de_arquivo_do_cliente_nao_escapa_da_pasta(nome, proibido):
    """O nome vem do cliente: uma barra escreveria fora da pasta de uploads.
    A defesa é allowlist de caracteres, não lista de proibidos."""
    seguro = _nome_seguro(nome)
    assert proibido not in seguro
    assert not seguro.startswith(".")


def test_nome_com_acento_vira_ascii():
    assert _nome_seguro("boletim ç ã.pdf") == "boletim_c_a.pdf"


# --- envio ------------------------------------------------------------------

def test_envio_entra_como_pendente_e_grava_o_arquivo(session):
    s, lixo = session
    usuario = _usuario(s, lixo)
    documento = _enviar(s, lixo, usuario)

    assert documento.status == StatusDocumento.PENDENTE
    assert documento.chunks_indexados == 0
    assert os.path.isfile(documento.caminho)
    assert open(documento.caminho, "rb").read() == CONTEUDO


def test_dois_envios_com_o_mesmo_nome_nao_se_sobrescrevem(session):
    """Duas pessoas mandando "boletim.pdf" é o caso comum, não a exceção."""
    s, lixo = session
    usuario = _usuario(s, lixo)
    primeiro = _enviar(s, lixo, usuario, conteudo=b"primeiro conteudo do arquivo")
    segundo = _enviar(s, lixo, usuario, conteudo=b"segundo conteudo, diferente")

    assert primeiro.caminho != segundo.caminho
    assert open(primeiro.caminho, "rb").read() == b"primeiro conteudo do arquivo"


# --- aprovação --------------------------------------------------------------

def test_aprovar_indexa_e_registra_quantos_trechos_entraram(session):
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    documento = _enviar(s, lixo, usuario)

    with patch("app.rag.ingestion.indexar_arquivo", return_value=7) as indexar:
        aprovar(s, documento.id, aprovador=aprovador)
    s.commit()

    assert documento.status == StatusDocumento.APROVADO
    assert documento.chunks_indexados == 7
    assert documento.decidido_por_id == aprovador.id
    assert documento.decidido_em is not None
    # A procedência precisa ir para o payload, ou remover depois seria
    # impossível sem varrer o índice inteiro.
    metadados = indexar.call_args.kwargs["metadados_extra"]
    assert metadados["documento_id"] == str(documento.id)
    assert metadados["enviado_por"] == usuario.username


def test_falha_ao_indexar_deixa_o_documento_pendente(session):
    """A ordem é indexar PRIMEIRO, marcar depois. Se marcasse antes, o
    documento apareceria como aprovado sem estar no índice — e ninguém
    descobriria até reparar que o agente ignora um boletim que "está lá"."""
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    documento = _enviar(s, lixo, usuario)

    with patch("app.rag.ingestion.indexar_arquivo", side_effect=ValueError("sem texto extraível")):
        with pytest.raises(FalhaAoIndexarError, match="sem texto"):
            aprovar(s, documento.id, aprovador=aprovador)

    assert documento.status == StatusDocumento.PENDENTE
    assert documento.chunks_indexados == 0


def test_aprovar_duas_vezes_nao_reindexa(session):
    """A operação não é idempotente: a segunda aprovação mandaria o mesmo
    arquivo para o índice de novo."""
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    documento = _enviar(s, lixo, usuario)

    with patch("app.rag.ingestion.indexar_arquivo", return_value=3) as indexar:
        aprovar(s, documento.id, aprovador=aprovador)
        s.commit()
        with pytest.raises(DecisaoInvalidaError, match="já foi"):
            aprovar(s, documento.id, aprovador=aprovador)

    assert indexar.call_count == 1


# --- recusa e remoção -------------------------------------------------------

def test_recusa_exige_motivo(session):
    """Sem motivo, quem enviou reenvia o mesmo arquivo."""
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    documento = _enviar(s, lixo, usuario)

    with pytest.raises(DecisaoInvalidaError, match="motivo"):
        recusar(s, documento.id, aprovador=aprovador, motivo="   ")


def test_recusa_registra_o_motivo_e_nao_indexa(session):
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    documento = _enviar(s, lixo, usuario)

    with patch("app.rag.ingestion.indexar_arquivo") as indexar:
        recusar(s, documento.id, aprovador=aprovador, motivo="Boletim desatualizado, rev 02.")
    s.commit()

    assert documento.status == StatusDocumento.REJEITADO
    assert "rev 02" in documento.motivo_decisao
    indexar.assert_not_called()


def test_documento_aprovado_pode_ser_removido_do_indice(session):
    """Sem a volta, aprovar um arquivo errado exigiria reindexar o acervo
    inteiro."""
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    documento = _enviar(s, lixo, usuario)

    with patch("app.rag.ingestion.indexar_arquivo", return_value=5):
        aprovar(s, documento.id, aprovador=aprovador)
    s.commit()

    with patch("app.rag.ingestion.remover_arquivo_do_indice", return_value=5) as remover:
        remover_do_acervo(s, documento.id, aprovador=aprovador, motivo="Versão errada.")
    s.commit()

    remover.assert_called_once_with(documento.caminho)
    assert documento.status == StatusDocumento.REJEITADO
    assert documento.chunks_indexados == 0
    assert "Removido do acervo" in documento.motivo_decisao


def test_remover_documento_que_nunca_foi_aprovado_e_recusado(session):
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    documento = _enviar(s, lixo, usuario)

    with pytest.raises(DecisaoInvalidaError, match="aprovados"):
        remover_do_acervo(s, documento.id, aprovador=aprovador, motivo="x")


# --- quem vê o quê ----------------------------------------------------------

def test_listagem_filtra_por_quem_enviou(session):
    """Quem só envia não pode enxergar a fila dos colegas: é material que a
    empresa ainda não validou."""
    s, lixo = session
    um = _usuario(s, lixo)
    outro = _usuario(s, lixo)
    _enviar(s, lixo, um)
    _enviar(s, lixo, outro)

    so_do_um = listar(s, enviado_por=um)
    assert all(d.enviado_por_id == um.id for d in so_do_um)
    assert len(so_do_um) == 1


def test_listagem_filtra_por_status(session):
    s, lixo = session
    usuario = _usuario(s, lixo)
    aprovador = _usuario(s, lixo, perfil="admin_ti")
    pendente = _enviar(s, lixo, usuario)
    decidido = _enviar(s, lixo, usuario)
    recusar(s, decidido.id, aprovador=aprovador, motivo="não serve")
    s.commit()

    ids_pendentes = {d.id for d in listar(s, status=StatusDocumento.PENDENTE)}
    assert pendente.id in ids_pendentes
    assert decidido.id not in ids_pendentes


# --- as permissões novas ----------------------------------------------------

def test_enviar_e_aprovar_sao_permissoes_separadas(session):
    """Quem está em campo tem o boletim que falta, mas não é necessariamente
    quem decide o que a base passa a afirmar para a equipe."""
    s, lixo = session
    vendedor = _usuario(s, lixo, perfil="vendedor")
    admin = _usuario(s, lixo, perfil="admin_ti")

    assert has_permission(vendedor, Permission.UPLOAD_DOCUMENTS) is True
    assert has_permission(vendedor, Permission.APPROVE_UPLOADS) is False
    assert has_permission(admin, Permission.APPROVE_UPLOADS) is True
