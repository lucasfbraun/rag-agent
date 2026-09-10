"""
CRUD de perfis de acesso (Sessão 37).

Até 2026-09-10 os perfis eram um enum fixo em código: criar um novo exigia
migration e deploy. Agora são linhas em `perfis`, editáveis pela tela pelo
Admin TI.

**A parte que importa deste módulo são as invariantes**, não o CRUD. Um sistema
onde o administrador pode editar os próprios poderes tem quatro formas de se
trancar para fora, e todas passam por aqui:

  1. excluir o perfil que administra;
  2. desmarcar a permissão de administração do último perfil que a tem;
  3. mover o último administrador para outro perfil (em user_service);
  4. desativar o último administrador (em user_service).

As duas primeiras são responsabilidade daqui. Todas checam a mesma coisa —
sobra ALGUÉM ATIVO que administra? — e não "sobra algum perfil admin": um
perfil de administração sem nenhum usuário ativo não abre a porta de ninguém.
"""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.permissions import PERMISSAO_DE_ADMINISTRACAO, Permission
from app.models import Perfil, PerfilPermissao, User, UserStatus


class PerfilNaoEncontradoError(ValueError):
    """Nenhum perfil com o id/slug informado."""


class PerfilJaExisteError(ValueError):
    """Já existe perfil com esse slug."""


class PerfilProtegidoError(ValueError):
    """Perfil semeado pelo sistema — não pode ser excluído nem ter o slug
    trocado. As permissões continuam editáveis."""


class PerfilEmUsoError(ValueError):
    """Há usuários vinculados — excluir deixaria linhas órfãs."""


class SemAdministradorError(ValueError):
    """A operação deixaria o sistema sem nenhum administrador ativo."""


def _get_or_raise(session: Session, perfil_id) -> Perfil:
    perfil = session.get(Perfil, perfil_id)
    if perfil is None:
        raise PerfilNaoEncontradoError(f"Perfil {perfil_id} não encontrado.")
    return perfil


def listar_perfis(session: Session) -> list[Perfil]:
    return list(session.execute(select(Perfil).order_by(Perfil.nome)).scalars())


def obter_por_slug(session: Session, slug: str) -> Perfil | None:
    return session.execute(select(Perfil).where(Perfil.slug == slug)).scalar_one_or_none()


def contar_usuarios(session: Session, perfil_id) -> int:
    return session.execute(
        select(func.count()).select_from(User).where(User.perfil_id == perfil_id)
    ).scalar_one()


def _permissoes_validas(permissoes) -> set[str]:
    """Descarta o que o código não reconhece.

    A tela só oferece permissões conhecidas, mas a API aceita JSON de qualquer
    cliente: gravar uma string arbitrária encheria a tabela de linhas que nunca
    autorizam nada e confundiriam a próxima pessoa a ler o banco."""
    conhecidas = {p.value for p in Permission}
    return {p for p in (permissoes or []) if p in conhecidas}


def _outros_administradores_ativos(session: Session, perfil_id_excluido=None) -> int:
    """Usuários ATIVOS que administram, ignorando o perfil sob alteração.

    Conta gente, não perfis: um perfil de administração sem nenhum usuário
    ativo não deixa ninguém entrar para consertar as coisas."""
    consulta = (
        select(func.count())
        .select_from(User)
        .join(Perfil, User.perfil_id == Perfil.id)
        .join(PerfilPermissao, PerfilPermissao.perfil_id == Perfil.id)
        .where(
            User.status == UserStatus.ATIVO,
            PerfilPermissao.permissao == PERMISSAO_DE_ADMINISTRACAO.value,
        )
    )
    if perfil_id_excluido is not None:
        consulta = consulta.where(Perfil.id != perfil_id_excluido)
    return session.execute(consulta).scalar_one()


def _garantir_que_sobra_administrador(session: Session, perfil: Perfil, permissoes_novas: set[str]):
    """Chamar ANTES de tirar a administração de um perfil (ou de excluí-lo)."""
    perde_a_administracao = (
        PERMISSAO_DE_ADMINISTRACAO.value in perfil.nomes_de_permissoes()
        and PERMISSAO_DE_ADMINISTRACAO.value not in permissoes_novas
    )
    if not perde_a_administracao:
        return
    if _outros_administradores_ativos(session, perfil_id_excluido=perfil.id) == 0:
        raise SemAdministradorError(
            "Esta mudança deixaria o sistema sem nenhum administrador ativo. "
            "Dê a permissão de administração a outro perfil (com pelo menos um "
            "usuário ativo) antes de retirá-la deste."
        )


def criar_perfil(session: Session, *, slug: str, nome: str, descricao: str | None,
                 permissoes: list[str]) -> Perfil:
    perfil = Perfil(
        slug=slug.strip().lower(),
        nome=nome.strip(),
        descricao=(descricao or "").strip() or None,
        protegido=False,
    )
    perfil.permissoes = [
        PerfilPermissao(permissao=p) for p in sorted(_permissoes_validas(permissoes))
    ]
    session.add(perfil)
    try:
        session.flush()
    except IntegrityError as e:
        session.rollback()
        raise PerfilJaExisteError(f"Já existe um perfil com o identificador '{slug}'.") from e
    return perfil


def atualizar_perfil(session: Session, perfil_id, *, nome: str | None = None,
                     descricao: str | None = None,
                     permissoes: list[str] | None = None) -> Perfil:
    """Nome, descrição e permissões são editáveis em qualquer perfil.

    O `slug` NÃO é editável: ele é o identificador estável usado por integração
    e pelos testes; renomeá-lo quebraria referências sem aviso. Quem quer outro
    identificador cria outro perfil."""
    perfil = _get_or_raise(session, perfil_id)

    if permissoes is not None:
        validas = _permissoes_validas(permissoes)
        _garantir_que_sobra_administrador(session, perfil, validas)
        perfil.permissoes = [PerfilPermissao(permissao=p) for p in sorted(validas)]

    if nome is not None:
        perfil.nome = nome.strip()
    if descricao is not None:
        perfil.descricao = descricao.strip() or None

    session.flush()
    return perfil


def excluir_perfil(session: Session, perfil_id) -> None:
    """Exclusão de verdade, não desativação — ao contrário de usuário.

    A diferença é histórico: apagar um usuário perderia o rastro de quem gerou
    qual recomendação; um perfil sem usuários não carrega histórico nenhum. E
    justamente por isso a exclusão só é permitida quando não há ninguém
    vinculado: senão a FK deixaria usuários órfãos."""
    perfil = _get_or_raise(session, perfil_id)
    if perfil.protegido:
        raise PerfilProtegidoError(
            f"O perfil '{perfil.nome}' é do sistema e não pode ser excluído. "
            "As permissões dele continuam editáveis."
        )
    em_uso = contar_usuarios(session, perfil_id)
    if em_uso:
        raise PerfilEmUsoError(
            f"{em_uso} usuário(s) ainda usam o perfil '{perfil.nome}'. "
            "Mova essas pessoas para outro perfil antes de excluí-lo."
        )
    _garantir_que_sobra_administrador(session, perfil, set())
    session.delete(perfil)
    session.flush()
