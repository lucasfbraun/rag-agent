"""
Repository/service de usuários (Fase 5 — RBAC & Governança).

Interface pequena por propósito: create/get/list/update/deactivate/set_password.
Esconde: hashing de senha, tradução de erro de unicidade do banco em exceção de
domínio, e a regra de que "excluir" um usuário é desativar (status), não apagar
a linha — perda de histórico de quem fez o quê não é aceitável para auditoria.

Nenhum outro módulo deve fazer session.add(User(...)) diretamente — sempre passar
por aqui, para que hashing e checagem de duplicidade fiquem garantidos num só lugar.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.models import Perfil, PerfilPermissao, Role, User, UserOrigin, UserStatus


class UsuarioJaExisteError(ValueError):
    """username ou email já cadastrado."""


class UsuarioNaoEncontradoError(ValueError):
    """Nenhum usuário com o id/username informado."""


class AutenticacaoInvalidaError(ValueError):
    """username ou senha incorretos. Mensagem sempre genérica — não revela qual dos dois errou."""


class UsuarioInativoError(ValueError):
    """Credenciais corretas, mas a conta está desativada."""


class VinculoLDAPInvalidoError(ValueError):
    """Vínculo com o Active Directory recusado — conta inexistente, desabilitada
    ou já vinculada a outro usuário."""


class PerfilInexistenteError(ValueError):
    """O perfil informado não existe no catálogo."""


class UltimoAdminError(ValueError):
    """A operação deixaria zero Admin TI ativos — nenhuma mudança de perfil
    ou desativação pode zerar esse número (ver AUD-004,
    docs/auditoria_2026-08-25.md)."""


def resolver_perfil(session: Session, perfil) -> Perfil:
    """Aceita o registro de `Perfil`, o slug em texto, ou o enum `Role`.

    Os chamadores naturalmente têm coisas diferentes em mãos: a API recebe
    slug, a semente e os testes usam o enum `Role` (que sobrevive como catálogo
    dos perfis semeados), e o serviço de perfis já tem o registro. Resolver num
    lugar só evita que cada chamador faça a própria consulta — e que um deles
    esqueça de tratar "perfil não existe"."""
    if isinstance(perfil, Perfil):
        return perfil
    slug = perfil.value if isinstance(perfil, Role) else str(perfil)
    encontrado = session.execute(
        select(Perfil).where(Perfil.slug == slug)
    ).scalar_one_or_none()
    if encontrado is None:
        raise PerfilInexistenteError(f"Perfil '{slug}' não existe.")
    return encontrado


def _get_user_or_raise(session: Session, user_id) -> User:
    user = get_user_by_id(session, user_id)
    if user is None:
        raise UsuarioNaoEncontradoError(f"Usuário {user_id} não encontrado.")
    return user


def _flush_or_raise_duplicate(session: Session, mensagem: str) -> None:
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise UsuarioJaExisteError(mensagem) from e


def _usuario_administra(user: User) -> bool:
    from app.auth.permissions import PERMISSAO_DE_ADMINISTRACAO

    return (
        user.perfil is not None
        and PERMISSAO_DE_ADMINISTRACAO.value in user.perfil.nomes_de_permissoes()
    )


def _contar_admins_ativos_exceto(session: Session, user_id) -> int:
    """Usuários ATIVOS cujo perfil administra, ignorando um id.

    Desde 2026-09-10 a checagem é por PERMISSÃO, não pelo perfil "admin_ti":
    com perfis editáveis pela tela, o administrador pode ser um perfil criado
    hoje de manhã, e amarrar a invariante a um slug fixo a tornaria mentira."""
    from app.auth.permissions import PERMISSAO_DE_ADMINISTRACAO

    return session.execute(
        select(func.count())
        .select_from(User)
        .join(Perfil, User.perfil_id == Perfil.id)
        .join(PerfilPermissao, PerfilPermissao.perfil_id == Perfil.id)
        .where(
            User.status == UserStatus.ATIVO,
            User.id != user_id,
            PerfilPermissao.permissao == PERMISSAO_DE_ADMINISTRACAO.value,
        )
    ).scalar_one()


def _garantir_que_nao_zera_admins_ativos(session: Session, user: User) -> None:
    """Chamar ANTES de aplicar uma mudança que tire `user` da condição
    "administrador ativo" (mudar de perfil ou desativar). Só levanta erro se
    `user` administra hoje E não sobra nenhum outro."""
    if not _usuario_administra(user) or user.status != UserStatus.ATIVO:
        return
    if _contar_admins_ativos_exceto(session, user.id) == 0:
        raise UltimoAdminError(
            "Esta operação deixaria o sistema sem nenhum administrador ativo — "
            "dê perfil de administração a outro usuário antes de continuar."
        )


def create_user(session: Session, *, username: str, nome: str, email: str, password: str, perfil) -> User:
    """Cria um usuário de origem manual. Levanta SenhaFracaError (ver security.py)
    se a senha não atender ao mínimo, ou UsuarioJaExisteError se username/email já existem."""
    user = User(
        username=username,
        nome=nome,
        email=email,
        password_hash=hash_password(password),
        perfil=resolver_perfil(session, perfil),
        origem=UserOrigin.MANUAL,
    )
    session.add(user)
    _flush_or_raise_duplicate(session, f"username '{username}' ou email '{email}' já cadastrado.")
    return user


def get_user_by_id(session: Session, user_id) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.execute(select(User).where(User.username == username)).scalar_one_or_none()


def list_users(session: Session) -> list[User]:
    return list(session.execute(select(User).order_by(User.nome)).scalars())


def update_user(session: Session, user_id, *, nome: str | None = None, email: str | None = None,
                 perfil=None) -> User:
    """Atualiza campos mutáveis de negócio. NÃO mexe em senha/origem/external_id —
    troca de senha é set_password(); origem/external_id não são editáveis por aqui
    (mudar a origem de um usuário depois de criado é uma decisão que ainda não tem
    requisito definido — ver docs/spec_rbac.md)."""
    user = _get_user_or_raise(session, user_id)
    if perfil is not None:
        perfil = resolver_perfil(session, perfil)
        if perfil.id != user.perfil_id:
            _garantir_que_nao_zera_admins_ativos(session, user)
    if nome is not None:
        user.nome = nome
    if email is not None:
        user.email = email
    if perfil is not None:
        user.perfil = perfil
    _flush_or_raise_duplicate(session, f"email '{email}' já usado por outro usuário.")
    return user


def set_password(session: Session, user_id, new_password: str) -> User:
    user = _get_user_or_raise(session, user_id)
    user.password_hash = hash_password(new_password)
    session.flush()
    return user


def deactivate_user(session: Session, user_id) -> User:
    """'Excluir' um usuário = desativar (status=INATIVO), nunca apagar a linha."""
    user = _get_user_or_raise(session, user_id)
    _garantir_que_nao_zera_admins_ativos(session, user)
    user.status = UserStatus.INATIVO
    session.flush()
    return user


def activate_user(session: Session, user_id) -> User:
    """Reativa uma conta desativada — contrapartida de deactivate_user.

    "Excluir = desativar" só é uma decisão segura se der para desfazer: sem
    isto, um usuário desativado por engano ficava trancado para sempre, e a
    única saída era mexer no banco à mão. Não precisa da guarda de último
    Admin TI: reativar nunca reduz o número de admins ativos."""
    user = _get_user_or_raise(session, user_id)
    user.status = UserStatus.ATIVO
    session.flush()
    return user


def create_user_ldap(
    session: Session, *, external_id: str, perfil,
    username: str | None = None, nome: str | None = None, email: str | None = None,
) -> User:
    """Cria um usuário já vinculado ao Active Directory, sem senha local.

    Existe porque o caminho "criar com senha e vincular depois" grava um hash
    bcrypt que é apagado segundos depois — trabalho inútil e, pior, uma janela
    em que a conta tem senha local válida.

    Os dados vêm do diretório por padrão, mas os parâmetros permitem
    sobrescrever: nem toda conta de AD tem `mail` preenchido, e `email` é
    NOT NULL aqui. Quando o AD não informa, quem cadastra digita."""
    from app.auth import ldap_service

    conta = ldap_service.obter_por_external_id(external_id)
    if conta is None:
        raise VinculoLDAPInvalidoError(
            "Conta não encontrada no Active Directory. Refaça a busca — ela pode ter sido removida."
        )
    if not conta["habilitado"]:
        raise VinculoLDAPInvalidoError(
            f"A conta '{conta['login']}' está DESABILITADA no Active Directory."
        )

    email_final = (email or conta.get("email") or "").strip()
    if not email_final:
        raise VinculoLDAPInvalidoError(
            f"A conta '{conta['login']}' não tem e-mail no Active Directory. Informe um."
        )

    user = User(
        username=(username or conta["login"]).strip(),
        nome=(nome or conta["nome"] or conta["login"]).strip(),
        email=email_final,
        password_hash=None,  # nunca há senha local para usuário de AD
        perfil=resolver_perfil(session, perfil),
        origem=UserOrigin.LDAP,
        external_id=conta["external_id"],
    )
    session.add(user)
    _flush_or_raise_duplicate(
        session,
        f"já existe usuário com o login '{user.username}', o e-mail '{email_final}' "
        f"ou vinculado à conta '{conta['login']}' do AD.",
    )
    return user


def vincular_ldap(session: Session, user_id, external_id: str) -> User:
    """Passa o usuário a autenticar pelo Active Directory.

    APAGA o `password_hash` local, e isso é o ponto principal desta função, não
    um efeito colateral: manter a senha local viva daria à pessoa DUAS
    credenciais válidas. A TI desligaria a conta no AD achando que cortou o
    acesso, e ela continuaria entrando aqui com a senha antiga — exatamente o
    risco que centralizar no AD deveria eliminar.

    Recusa conta desabilitada no AD: vincular alguém já desligado criaria um
    acesso que ninguém consegue explicar depois."""
    from app.auth import ldap_service

    user = _get_user_or_raise(session, user_id)
    conta = ldap_service.obter_por_external_id(external_id)
    if conta is None:
        raise VinculoLDAPInvalidoError(
            "Conta não encontrada no Active Directory. Refaça a busca — ela pode ter sido removida."
        )
    if not conta["habilitado"]:
        raise VinculoLDAPInvalidoError(
            f"A conta '{conta['login']}' está DESABILITADA no Active Directory e não pode ser vinculada."
        )

    user.origem = UserOrigin.LDAP
    user.external_id = conta["external_id"]
    user.password_hash = None
    _flush_or_raise_duplicate(
        session, f"a conta '{conta['login']}' do AD já está vinculada a outro usuário."
    )
    return user


def desvincular_ldap(session: Session, user_id, nova_senha: str) -> User:
    """Volta o usuário para senha local.

    Exige a senha nova no mesmo passo porque um usuário de origem LDAP tem
    `password_hash` NULL: desvincular sem definir senha o deixaria sem
    NENHUMA forma de entrar — conta viva, dono trancado do lado de fora."""
    user = _get_user_or_raise(session, user_id)
    if user.origem != UserOrigin.LDAP:
        raise VinculoLDAPInvalidoError("Este usuário não está vinculado ao Active Directory.")

    user.password_hash = hash_password(nova_senha)  # valida a política de senha
    user.origem = UserOrigin.MANUAL
    user.external_id = None
    session.flush()
    return user


def _autenticar_no_ldap(user: User, password: str) -> bool:
    """Confere a senha de um usuário de origem LDAP contra o diretório.

    O login do AD é resolvido pelo objectGUID a cada autenticação, em vez de
    guardado aqui: é isso que faz um usuário RENOMEADO no AD continuar
    entrando. Guardar o `sAMAccountName` seria mais rápido e quebraria em
    silêncio no dia em que a TI corrigisse a grafia de um nome."""
    from app.auth import ldap_service

    if not user.external_id:
        return False
    conta = ldap_service.obter_por_external_id(user.external_id)
    if conta is None or not conta["habilitado"]:
        # Conta apagada ou desligada no AD: nega aqui também, sem depender de
        # alguém lembrar de desativar o usuário nos dois lugares.
        return False
    return ldap_service.autenticar(conta["login"], password)


def authenticate(session: Session, username: str, password: str) -> User:
    """Confere username+senha. Levanta AutenticacaoInvalidaError (credencial errada
    ou usuário inexistente — mesma mensagem pros dois casos, para não revelar quais
    usernames existem) ou UsuarioInativoError (credenciais certas, conta desativada).

    verify_password() roda sempre, mesmo com username inexistente (contra um
    hash dummy fixo) — sem isso, a checagem de bcrypt (~100ms) só acontecia
    quando o username existia, e o tempo de resposta virava um canal de
    enumeração de usuário mesmo com a mensagem de erro sendo uniforme."""
    user = get_user_by_username(session, username)

    if user is not None and user.origem == UserOrigin.LDAP:
        # A senha vai ao Active Directory; nada é conferido localmente, porque
        # `password_hash` é NULL para este usuário por construção (vincular_ldap
        # apaga a senha local justamente para não existirem duas credenciais).
        if not _autenticar_no_ldap(user, password):
            raise AutenticacaoInvalidaError("Usuário ou senha incorretos.")
    else:
        password_hash = user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
        senha_confere = verify_password(password, password_hash)
        if user is None or user.password_hash is None or not senha_confere:
            raise AutenticacaoInvalidaError("Usuário ou senha incorretos.")

    # A checagem de status vale para as duas origens: desativar aqui corta o
    # acesso mesmo de quem continua ativo no AD.
    if user.status != UserStatus.ATIVO:
        raise UsuarioInativoError("Esta conta está desativada. Contate o administrador.")
    return user
