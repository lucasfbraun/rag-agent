"""
Vínculo de usuário com o Active Directory (Sessão 36).

O modelo já previa isto desde 2026-08-24 (`origem`, `external_id` único,
`password_hash` nullable — ver docs/spec_rbac.md): nenhuma migration foi
necessária, só o Adapter e as regras de vínculo.

O que estes testes protegem, em ordem de gravidade:
  1. Vincular APAGA a senha local. Sem isso a pessoa fica com duas
     credenciais, e desligar a conta no AD não corta o acesso aqui — o
     oposto do motivo de centralizar no AD.
  2. Desvincular exige senha nova. Usuário LDAP tem `password_hash` NULL;
     desvincular sem definir senha o deixaria sem forma nenhuma de entrar.
  3. Conta desabilitada ou apagada no AD não autentica, mesmo com o vínculo
     de pé — sem depender de alguém lembrar de desativar nos dois lugares.

Seam: `ldap_service` é mockado (o diretório é um sistema de terceiros; a
suíte não pode depender da rede da empresa). A conversa real com o AD é
verificada à parte, em `test_ldap_service_real.py`.
"""
import uuid
from unittest.mock import patch

import pytest

from app.auth.security import SenhaFracaError
from app.auth.user_service import (
    AutenticacaoInvalidaError,
    UsuarioInativoError,
    VinculoLDAPInvalidoError,
    authenticate,
    create_user,
    desvincular_ldap,
    vincular_ldap,
)
from app.db import SessionLocal
from app.models import Role, User, UserOrigin, UserStatus

GUID = "68a37cfc-caa1-46e7-a70d-a9022c27fb65"
CONTA_AD = {
    "external_id": GUID, "login": "lucas.braun", "nome": "Lucas Braun - TI",
    "email": None, "dn": "CN=lucas,DC=flexivel,DC=local", "habilitado": True,
}
CONTA_DESABILITADA = {**CONTA_AD, "habilitado": False}


@pytest.fixture
def session():
    s = SessionLocal()
    criados = []
    try:
        yield s, criados
    finally:
        s.rollback()
        for user_id in criados:
            registro = s.get(User, user_id)
            if registro is not None:
                s.delete(registro)
        s.commit()
        s.close()


def _novo_usuario(s, criados, **kw):
    sufixo = uuid.uuid4().hex[:8]
    user = create_user(
        s,
        username=kw.get("username", f"teste.{sufixo}"),
        nome="Teste LDAP",
        email=f"teste.{sufixo}@grupoflexivel.com.br",
        password="SenhaLocal123",
        perfil=Role.VENDEDOR,
    )
    s.commit()
    criados.append(user.id)
    return user


# --- vínculo ---------------------------------------------------------------

def test_vincular_apaga_a_senha_local(session):
    """O ponto mais importante do recurso. Com a senha local viva, a pessoa
    teria DUAS credenciais: a TI desligaria a conta no AD achando que cortou o
    acesso, e ela continuaria entrando com a senha antiga."""
    s, criados = session
    user = _novo_usuario(s, criados)
    assert user.password_hash is not None

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    s.commit()

    assert user.password_hash is None
    assert user.origem == UserOrigin.LDAP
    assert user.external_id == GUID


def test_senha_local_antiga_para_de_funcionar_depois_do_vinculo(session):
    """A consequência prática de apagar o hash, exercitada pelo caminho real
    de login."""
    s, criados = session
    user = _novo_usuario(s, criados)

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    s.commit()

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD), \
         patch("app.auth.ldap_service.autenticar", return_value=False):
        with pytest.raises(AutenticacaoInvalidaError):
            authenticate(s, user.username, "SenhaLocal123")


def test_conta_desabilitada_no_ad_nao_pode_ser_vinculada(session):
    """Vincular alguém já desligado criaria um acesso que ninguém consegue
    explicar depois."""
    s, criados = session
    user = _novo_usuario(s, criados)

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_DESABILITADA):
        with pytest.raises(VinculoLDAPInvalidoError, match="DESABILITADA"):
            vincular_ldap(s, user.id, GUID)


def test_conta_inexistente_no_ad_nao_pode_ser_vinculada(session):
    s, criados = session
    user = _novo_usuario(s, criados)

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=None):
        with pytest.raises(VinculoLDAPInvalidoError, match="não encontrada"):
            vincular_ldap(s, user.id, GUID)


def test_mesma_conta_do_ad_nao_vincula_a_dois_usuarios(session):
    """`external_id` é único no schema justamente para isso: duas contas locais
    para a mesma identidade externa seria uma porta lateral de acesso."""
    s, criados = session
    primeiro = _novo_usuario(s, criados)
    segundo = _novo_usuario(s, criados)

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, primeiro.id, GUID)
        s.commit()
        from app.auth.user_service import UsuarioJaExisteError

        with pytest.raises(UsuarioJaExisteError):
            vincular_ldap(s, segundo.id, GUID)


# --- autenticação ----------------------------------------------------------

def test_usuario_ldap_autentica_pelo_diretorio(session):
    s, criados = session
    user = _novo_usuario(s, criados)
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    s.commit()

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD), \
         patch("app.auth.ldap_service.autenticar", return_value=True) as autenticar_ad:
        autenticado = authenticate(s, user.username, "SenhaDoAD")

    assert autenticado.id == user.id
    # O login enviado ao AD é o resolvido pelo GUID, não o username local —
    # é isso que faz um usuário RENOMEADO no AD continuar entrando.
    autenticar_ad.assert_called_once_with("lucas.braun", "SenhaDoAD")


def test_conta_apagada_do_ad_nao_autentica_mesmo_com_vinculo_de_pe(session):
    """Sem isto, apagar a conta no AD não cortaria o acesso — dependeria de
    alguém lembrar de desativar o usuário aqui também."""
    s, criados = session
    user = _novo_usuario(s, criados)
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    s.commit()

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=None):
        with pytest.raises(AutenticacaoInvalidaError):
            authenticate(s, user.username, "SenhaDoAD")


def test_conta_desabilitada_no_ad_nao_autentica(session):
    s, criados = session
    user = _novo_usuario(s, criados)
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    s.commit()

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_DESABILITADA), \
         patch("app.auth.ldap_service.autenticar", return_value=True) as autenticar_ad:
        with pytest.raises(AutenticacaoInvalidaError):
            authenticate(s, user.username, "SenhaDoAD")
    autenticar_ad.assert_not_called()


def test_desativar_aqui_corta_o_acesso_de_usuario_ldap(session):
    """A conta pode estar viva no AD; o status local vale mesmo assim."""
    s, criados = session
    user = _novo_usuario(s, criados)
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    user.status = UserStatus.INATIVO
    s.commit()

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD), \
         patch("app.auth.ldap_service.autenticar", return_value=True):
        with pytest.raises(UsuarioInativoError):
            authenticate(s, user.username, "SenhaDoAD")


def test_usuario_manual_continua_autenticando_por_senha_local(session):
    """A rota nova não pode ter mexido no caminho de quem não usa AD."""
    s, criados = session
    user = _novo_usuario(s, criados)
    assert authenticate(s, user.username, "SenhaLocal123").id == user.id
    with pytest.raises(AutenticacaoInvalidaError):
        authenticate(s, user.username, "senha-errada")


# --- desvínculo ------------------------------------------------------------

def test_desvincular_exige_senha_e_devolve_o_login_local(session):
    s, criados = session
    user = _novo_usuario(s, criados)
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    s.commit()

    desvincular_ldap(s, user.id, "NovaSenhaLocal123")
    s.commit()

    assert user.origem == UserOrigin.MANUAL
    assert user.external_id is None
    assert authenticate(s, user.username, "NovaSenhaLocal123").id == user.id


def test_desvincular_com_senha_fraca_e_recusado(session):
    """Se a senha fraca passasse, o usuário ficaria com uma credencial pior
    que a do AD — e a política de senha do projeto seria contornável por aqui."""
    s, criados = session
    user = _novo_usuario(s, criados)
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_AD):
        vincular_ldap(s, user.id, GUID)
    s.commit()

    with pytest.raises(SenhaFracaError):
        desvincular_ldap(s, user.id, "123")


def test_desvincular_usuario_que_nunca_foi_vinculado_e_recusado(session):
    s, criados = session
    user = _novo_usuario(s, criados)
    with pytest.raises(VinculoLDAPInvalidoError):
        desvincular_ldap(s, user.id, "NovaSenhaLocal123")


# --- cadastro já vinculado ao AD -------------------------------------------

def test_cadastro_pelo_ad_puxa_os_dados_do_diretorio_e_nao_grava_senha(session):
    """O caminho "criar com senha e vincular depois" gravaria um hash bcrypt
    apagado segundos depois — trabalho inútil e, pior, uma janela em que a
    conta tem senha local válida."""
    from app.auth.user_service import create_user_ldap

    s, criados = session
    # Login e e-mail únicos: o banco de teste é o REAL, e um login fixo colide
    # com quem já está cadastrado (o `lucas.braun` de verdade, por exemplo).
    sufixo = uuid.uuid4().hex[:6]
    conta = {**CONTA_AD, "login": f"ad.novo.{sufixo}", "nome": "Pessoa Do AD",
             "email": f"ad.novo.{sufixo}@grupoflexivel.com.br",
             "external_id": uuid.uuid4().hex[:8] + "-0000-0000-0000-000000000000"}

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=conta):
        user = create_user_ldap(s, external_id=conta["external_id"], perfil=Role.VENDEDOR)
    s.commit()
    criados.append(user.id)

    assert user.password_hash is None
    assert user.origem == UserOrigin.LDAP
    assert user.username == conta["login"]
    assert user.nome == conta["nome"]
    assert user.email == conta["email"]


def test_cadastro_pelo_ad_sem_email_no_diretorio_pede_um(session):
    """`email` é NOT NULL aqui, e nem toda conta de AD tem `mail` preenchido —
    a conta de serviço da própria Flexível não tem. Sem esta checagem o erro
    viria do banco, ilegível para quem está cadastrando."""
    from app.auth.user_service import create_user_ldap

    s, _ = session
    conta = {**CONTA_AD, "email": None}
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=conta):
        with pytest.raises(VinculoLDAPInvalidoError, match="e-mail"):
            create_user_ldap(s, external_id=conta["external_id"], perfil=Role.VENDEDOR)


def test_cadastro_pelo_ad_aceita_email_informado_quando_o_diretorio_nao_tem(session):
    from app.auth.user_service import create_user_ldap

    s, criados = session
    sufixo = uuid.uuid4().hex[:6]
    conta = {**CONTA_AD, "email": None, "login": f"ad.semmail.{sufixo}",
             "external_id": uuid.uuid4().hex[:8] + "-1111-1111-1111-111111111111"}
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=conta):
        user = create_user_ldap(
            s, external_id=conta["external_id"], perfil=Role.VENDEDOR,
            email=f"informado.{sufixo}@grupoflexivel.com.br",
        )
    s.commit()
    criados.append(user.id)
    assert user.email == f"informado.{sufixo}@grupoflexivel.com.br"


def test_cadastro_pelo_ad_recusa_conta_desabilitada(session):
    from app.auth.user_service import create_user_ldap

    s, _ = session
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=CONTA_DESABILITADA):
        with pytest.raises(VinculoLDAPInvalidoError, match="DESABILITADA"):
            create_user_ldap(s, external_id=GUID, perfil=Role.VENDEDOR)


def test_cadastro_pelo_ad_usa_a_senha_da_rede_no_login(session):
    """Fecha o ciclo: cadastrado pelo AD, autentica pelo AD."""
    from app.auth.user_service import create_user_ldap

    s, criados = session
    conta = {**CONTA_AD, "email": "ciclo@grupoflexivel.com.br",
             "login": f"ciclo.{uuid.uuid4().hex[:6]}",
             "external_id": uuid.uuid4().hex[:8] + "-2222-2222-2222-222222222222"}
    with patch("app.auth.ldap_service.obter_por_external_id", return_value=conta):
        user = create_user_ldap(s, external_id=conta["external_id"], perfil=Role.VENDEDOR)
    s.commit()
    criados.append(user.id)

    with patch("app.auth.ldap_service.obter_por_external_id", return_value=conta), \
         patch("app.auth.ldap_service.autenticar", return_value=True) as autenticar_ad:
        assert authenticate(s, user.username, "SenhaDaRede").id == user.id
    autenticar_ad.assert_called_once_with(conta["login"], "SenhaDaRede")
