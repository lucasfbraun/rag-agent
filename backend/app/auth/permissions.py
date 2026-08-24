"""
Camada centralizada de autorização (Fase 5, tarefa 4). Fonte da matriz:
docs/spec_rbac.md, seção "Matriz de acesso" — nenhuma permissão aqui foi
inventada sem evidência nesse documento.

Interface: Permission (o que existe pra autorizar), ROLE_PERMISSIONS (quem
tem o quê — único lugar que muda se a matriz mudar), has_permission() e
require_permission() (a dependency que os endpoints usam). Nenhum outro
módulo deve checar `user.perfil == ...` diretamente — sempre por aqui.
"""
import enum

from fastapi import Depends, HTTPException, status

from app.models import Role, User
from app.auth.dependencies import get_current_user


class Permission(str, enum.Enum):
    VIEW_CATALOG = "view_catalog"
    VIEW_HOMOLOGATION_SUMMARY = "view_homologation_summary"
    VIEW_HOMOLOGATION_FULL = "view_homologation_full"
    SELECT_TEMPLATE = "select_template"
    EDIT_TEMPLATE = "edit_template"
    DELETE_TEMPLATE = "delete_template"
    VIEW_COSTS = "view_costs"
    MANAGE_USERS = "manage_users"


# Matriz de acesso — docs/spec_rbac.md, "Matriz de acesso (formato CRUD pedido)".
# Onde a spec marcou "Pendência" (sem decisão de negócio), o padrão aqui é NEGAR
# por segurança (deny-by-default), não conceder. Ver docs/spec_rbac.md, seção
# "Pendências", para a lista de decisões que ainda faltam confirmar com o negócio:
#   - Técnico ver custos ("Opcional" na proposta, sem definir a regra) -> negado
#   - Gestor/Químico-PD excluir templates (proposta não distingue editar de excluir) -> negado
ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VENDEDOR: {
        Permission.VIEW_CATALOG,
        Permission.VIEW_HOMOLOGATION_SUMMARY,
        Permission.SELECT_TEMPLATE,
    },
    Role.TECNICO: {
        Permission.VIEW_CATALOG,
        Permission.VIEW_HOMOLOGATION_SUMMARY,
        Permission.VIEW_HOMOLOGATION_FULL,
        Permission.SELECT_TEMPLATE,
    },
    Role.GESTOR: {
        Permission.VIEW_CATALOG,
        Permission.VIEW_HOMOLOGATION_SUMMARY,
        Permission.VIEW_HOMOLOGATION_FULL,
        Permission.SELECT_TEMPLATE,
        Permission.EDIT_TEMPLATE,
        Permission.VIEW_COSTS,
    },
    Role.QUIMICO_PD: {
        Permission.VIEW_CATALOG,
        Permission.VIEW_HOMOLOGATION_SUMMARY,
        Permission.VIEW_HOMOLOGATION_FULL,
        Permission.SELECT_TEMPLATE,
        Permission.EDIT_TEMPLATE,
        Permission.VIEW_COSTS,
    },
    Role.ADMIN_TI: set(Permission),  # Admin TI = "Total" em toda a matriz da proposta
}


def has_permission(user: User, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(user.perfil, set())


def require_permission(permission: Permission):
    """Retorna uma dependency FastAPI: `Depends(require_permission(Permission.X))`.
    Endpoints declaram a permissão exigida, nunca o perfil — main.py não precisa
    saber como perfis mapeiam para permissões, só ROLE_PERMISSIONS sabe disso."""
    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if not has_permission(current_user, permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Perfil '{current_user.perfil.value}' não tem permissão para esta ação.",
            )
        return current_user
    return _dependency
