"""
Camada centralizada de autorização (Fase 5, tarefas 4-5). Fonte principal da
matriz: docs/spec_rbac.md, seção "Matriz de acesso". Toda permissão daquela
seção veio de lá sem invenção; exceção documentada: MANAGE_INGESTION (tarefa 5)
não está na matriz original — protege POST /api/ingest, que a spec não cobria
mas não podia ficar sem nenhuma permissão associada (ver comentário no enum).

Interface: Permission (o que existe pra autorizar), has_permission() e
require_permission() (a dependency que os endpoints usam). Nenhum outro módulo
deve checar perfil diretamente — sempre por aqui.

**Mudança de 2026-09-10:** quem tem o quê deixou de ser a constante
ROLE_PERMISSIONS e passou a ser DADO, na tabela `perfil_permissoes` — o Admin
TI edita pela tela, sem deploy. A matriz original virou a semente da migration
`b7e1c4d92f30`, que é onde ela fica preservada como história.

O que continua em código é a LISTA de permissões possíveis (o enum abaixo).
Cada permissão só existe porque algum endpoint a verifica; deixar criar
permissão pela tela produziria um checkbox que não protege nada.
"""
import enum

from fastapi import Depends, HTTPException, status

from app.models import User
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
    # Não vem da matriz da proposta (docs/spec_rbac.md não cobre a ingestão) — adicionada
    # na tarefa 5 porque POST /api/ingest dispara reindexação de horas do acervo inteiro
    # e não podia ficar sem nenhuma permissão associada. Só Admin TI, por ser a leitura
    # mais conservadora (mesmo padrão já usado nas pendências da matriz original).
    MANAGE_INGESTION = "manage_ingestion"
    # Sessão 38: o vendedor em campo tem o boletim que o acervo não tem. Enviar
    # e APROVAR são permissões separadas de propósito — quem contribui não é
    # necessariamente quem decide o que entra na base que todos consultam.
    UPLOAD_DOCUMENTS = "upload_documents"
    APPROVE_UPLOADS = "approve_uploads"
    # Sessão 38, item 4: contribuir com o aprendizado do agente. Treinar e
    # APROVAR treinamento são separados pela mesma razão do upload — correção e
    # conhecimento afirmam fatos que o agente repete como verdade da empresa.
    TRAIN_AGENT = "train_agent"
    APPROVE_TRAINING = "approve_training"


# Rótulo de cada permissão na tela de perfis. Fica aqui, junto do enum, porque
# um checkbox chamado "manage_ingestion" não diz a ninguém o que ele libera.
ROTULOS_DE_PERMISSAO: dict[Permission, str] = {
    Permission.VIEW_CATALOG: "Consultar o catálogo e conversar com o agente",
    Permission.VIEW_HOMOLOGATION_SUMMARY: "Ver resumo de homologação",
    Permission.VIEW_HOMOLOGATION_FULL: "Ver laudo de homologação completo",
    Permission.SELECT_TEMPLATE: "Escolher template de resposta",
    Permission.EDIT_TEMPLATE: "Editar templates",
    Permission.DELETE_TEMPLATE: "Excluir templates",
    Permission.VIEW_COSTS: "Ver custos e fórmulas (dado sensível)",
    Permission.MANAGE_USERS: "Administrador do sistema (usuários e perfis)",
    Permission.MANAGE_INGESTION: "Disparar reindexação do acervo",
    Permission.UPLOAD_DOCUMENTS: "Enviar documentos para o acervo (entram na fila)",
    Permission.APPROVE_UPLOADS: "Aprovar ou recusar documentos enviados",
    Permission.TRAIN_AGENT: "Treinar o agente (corrigir respostas, registrar conhecimento)",
    Permission.APPROVE_TRAINING: "Aprovar correções e conhecimento antes de valerem",
}

# A permissão que caracteriza um perfil "administrador". Quem a tem enxerga a
# tela de administração e pode mexer em usuários e perfis — inclusive tirar
# essa mesma permissão de outros. Por isso ela é o eixo da invariante de
# "nunca ficar sem administrador" (ver app.auth.perfil_service).
PERMISSAO_DE_ADMINISTRACAO = Permission.MANAGE_USERS


def has_permission(user: User, permission: Permission) -> bool:
    """True se o PERFIL do usuário concede a permissão.

    Uma permissão gravada no banco que o código não conhece mais (downgrade da
    aplicação depois de alguém marcá-la) simplesmente não casa aqui, em vez de
    estourar — ninguém perde o login por causa de um valor órfão."""
    perfil = getattr(user, "perfil", None)
    if perfil is None:
        return False
    return permission.value in perfil.nomes_de_permissoes()


def require_permission(permission: Permission):
    """Retorna uma dependency FastAPI: `Depends(require_permission(Permission.X))`.
    Endpoints declaram a permissão exigida, nunca o perfil — main.py não precisa
    saber como perfis mapeiam para permissões; isso é dado, na tabela
    `perfil_permissoes`."""
    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if not has_permission(current_user, permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"O perfil '{current_user.perfil.nome}' não tem permissão para esta ação.",
            )
        return current_user
    return _dependency
