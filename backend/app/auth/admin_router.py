"""
Administração de usuários (Fase 5, tarefa 7). Único jeito de provisionar
usuário até aqui era um script/CLI batendo direto no banco (ver PROGRESS.md,
Sessão 16-19, criação do admin real lucas.braun) — esta tarefa expõe as
mesmas operações que backend/app/auth/user_service.py já tinha desde a
tarefa 2 (create/list/get/update/set_password/deactivate) via HTTP, atrás de
Permission.MANAGE_USERS (só Admin TI, ver docs/spec_rbac.md).

Toda rota exige a mesma permissão, declarada via `dependencies=[...]` por rota
(mesmo padrão já usado em main.py) — exceto a de desativação, que recebe
require_permission() como parâmetro de verdade porque é a única que precisa
do usuário logado (guarda de autodesativação abaixo). Não dá pra combinar as
duas formas na mesma rota: cada `Depends(require_permission(...))` é uma
closure nova, então declará-lo tanto no router quanto na rota executaria
get_current_user() duas vezes por request.
"""
import uuid
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.permissions import Permission, require_permission
from app.auth.schemas import UsuarioResponse
from app.auth.security import SenhaFracaError
from app.auth.user_service import (
    UltimoAdminError,
    UsuarioJaExisteError,
    UsuarioNaoEncontradoError,
    VinculoLDAPInvalidoError,
    activate_user,
    create_user,
    deactivate_user,
    get_user_by_id,
    list_users,
    desvincular_ldap,
    set_password,
    update_user,
    vincular_ldap,
)
from app.db import get_session
from app.models import Role, User

router = APIRouter(prefix="/api/auth/users", tags=["admin-usuarios"])


@contextmanager
def _commit_traduzindo_erros(session: Session):
    """Confirma a transação e traduz os erros de domínio de user_service.py pro
    HTTP correspondente — único lugar que sabe esse mapeamento, em vez de
    repetir o mesmo try/commit/except/rollback em cada rota."""
    try:
        yield
        session.commit()
    except UsuarioNaoEncontradoError as e:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e))
    except UsuarioJaExisteError as e:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except UltimoAdminError as e:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except VinculoLDAPInvalidoError as e:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except SenhaFracaError as e:
        session.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))


class CriarUsuarioRequest(BaseModel):
    username: str
    nome: str
    email: str
    password: str
    perfil: Role


class EditarUsuarioRequest(BaseModel):
    """Nenhum campo obrigatório — só altera o que vier preenchido. Não inclui
    senha (ver RedefinirSenhaRequest) nem origem/external_id, mesmo limite já
    documentado em user_service.update_user()."""
    nome: str | None = None
    email: str | None = None
    perfil: Role | None = None


class RedefinirSenhaRequest(BaseModel):
    new_password: str


class VincularLDAPRequest(BaseModel):
    """`external_id` é o objectGUID da conta no AD, obtido em
    GET /api/auth/ldap/search — nunca digitado à mão."""
    external_id: str


class DesvincularLDAPRequest(BaseModel):
    """A senha vem junto porque usuário de origem LDAP tem `password_hash`
    NULL: desvincular sem definir senha o deixaria sem forma nenhuma de
    entrar."""
    new_password: str


@router.post(
    "", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def criar_usuario(req: CriarUsuarioRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        user = create_user(
            session, username=req.username, nome=req.nome, email=req.email,
            password=req.password, perfil=req.perfil,
        )
    return UsuarioResponse.from_user(user)


@router.get("", response_model=list[UsuarioResponse], dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def listar_usuarios(session: Session = Depends(get_session)):
    return [UsuarioResponse.from_user(u) for u in list_users(session)]


@router.get("/ldap/status", dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def status_do_ldap():
    """Se esta instalação tem Active Directory configurado.

    A tela usa isto para decidir se MOSTRA a seção de vínculo. Sem a checagem,
    uma instalação sem AD ofereceria o recurso e só falharia no clique."""
    from app.auth import ldap_service

    return {"configurado": ldap_service.ldap_configurado()}


@router.get("/ldap/search", dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def buscar_no_ldap(q: str):
    """Procura contas no Active Directory para o administrador escolher.

    Rota de LEITURA do diretório, atrás de MANAGE_USERS: a lista de
    funcionários da empresa não é informação para qualquer perfil."""
    from app.auth import ldap_service

    if not ldap_service.ldap_configurado():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Integração com Active Directory não configurada nesta instalação.",
        )
    try:
        return {"contas": ldap_service.buscar_usuarios(q)}
    except ldap_service.LDAPIndisponivelError:
        # Detalhe da falha só no log (mesma disciplina do AUD-011): a mensagem
        # do ldap3 traz host, porta e DN da conta de serviço.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Não foi possível falar com o Active Directory. Verifique a rede ou a configuração.",
        )


# ATENÇÃO À ORDEM: esta rota precisa vir ANTES de "/{user_id}". O FastAPI casa
# na ordem de declaração, e "/ldap/search" bate no padrão "/{user_id}" — se
# viesse depois, o "ldap" seria lido como um UUID e a busca no diretório
# devolveria 422 sem nunca chegar aqui.
@router.get("/{user_id}", response_model=UsuarioResponse, dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def obter_usuario(user_id: uuid.UUID, session: Session = Depends(get_session)):
    user = get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuário não encontrado.")
    return UsuarioResponse.from_user(user)


@router.patch("/{user_id}", response_model=UsuarioResponse, dependencies=[Depends(require_permission(Permission.MANAGE_USERS))])
def editar_usuario(user_id: uuid.UUID, req: EditarUsuarioRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        user = update_user(session, user_id, nome=req.nome, email=req.email, perfil=req.perfil)
    return UsuarioResponse.from_user(user)


@router.post(
    "/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def redefinir_senha(user_id: uuid.UUID, req: RedefinirSenhaRequest, session: Session = Depends(get_session)):
    with _commit_traduzindo_erros(session):
        set_password(session, user_id, req.new_password)


@router.post(
    "/{user_id}/link-ldap", response_model=UsuarioResponse,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def vincular_usuario_ao_ldap(
    user_id: uuid.UUID, req: VincularLDAPRequest, session: Session = Depends(get_session)
):
    """Passa o usuário a autenticar pelo AD. A senha local é APAGADA — ver
    user_service.vincular_ldap para o porquê."""
    from app.auth import ldap_service

    try:
        with _commit_traduzindo_erros(session):
            user = vincular_ldap(session, user_id, req.external_id)
    except ldap_service.LDAPIndisponivelError:
        session.rollback()
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Não foi possível falar com o Active Directory para confirmar a conta.",
        )
    return UsuarioResponse.from_user(user)


@router.post(
    "/{user_id}/unlink-ldap", response_model=UsuarioResponse,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def desvincular_usuario_do_ldap(
    user_id: uuid.UUID, req: DesvincularLDAPRequest, session: Session = Depends(get_session)
):
    """Volta o usuário para senha local, definindo-a no mesmo passo."""
    with _commit_traduzindo_erros(session):
        user = desvincular_ldap(session, user_id, req.new_password)
    return UsuarioResponse.from_user(user)


@router.post(
    "/{user_id}/activate", response_model=UsuarioResponse,
    dependencies=[Depends(require_permission(Permission.MANAGE_USERS))],
)
def reativar_usuario(user_id: uuid.UUID, session: Session = Depends(get_session)):
    """Contrapartida de /deactivate. Sem ela, desativar por engano trancava a
    conta para sempre — risco que passa a ser real agora que a desativação
    está a um clique na tela de administração, não mais só no CLI."""
    with _commit_traduzindo_erros(session):
        user = activate_user(session, user_id)
    return UsuarioResponse.from_user(user)


@router.post("/{user_id}/deactivate", response_model=UsuarioResponse)
def desativar_usuario(
    user_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_permission(Permission.MANAGE_USERS)),
):
    """'Excluir' = desativar (user_service.deactivate_user), nunca apagar a linha.

    Guarda extra (decisão de engenharia desta tarefa, não um requisito de negócio
    documentado): ninguém pode desativar a própria conta por aqui. Hoje só existe
    um Admin TI real (lucas.braun) — sem essa guarda, um clique errado travaria a
    administração inteira do sistema sem caminho de recuperação a não ser acesso
    direto ao banco. Essa rota recebe require_permission() como parâmetro, não
    como dependencies=[...] como as outras, porque precisa do User de volta para
    essa comparação (ver docstring do módulo)."""
    if user_id == current_user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Não é possível desativar a própria conta.",
        )
    with _commit_traduzindo_erros(session):
        user = deactivate_user(session, user_id)
    return UsuarioResponse.from_user(user)
