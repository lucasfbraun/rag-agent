"""
Adapter de Active Directory / LDAP (Fase 5 — o "modelo híbrido" previsto em
docs/spec_rbac.md desde 2026-08-24, agora implementado).

O que este módulo faz e o que deliberadamente NÃO faz:

  - **Faz**: procurar contas no diretório e conferir a senha de uma conta
    fazendo bind com ela.
  - **NÃO faz**: criar usuário, decidir perfil, decidir permissão. A conta no
    PU Matcher continua sendo criada e governada aqui dentro; o AD só responde
    "esta senha é desta pessoa?". Isso é proposital — vincular perfil a grupo
    do AD amarraria a autorização da aplicação à estrutura de OUs de TI, que
    muda por motivos que não têm nada a ver com este sistema.
  - **NUNCA persiste a senha do AD.** `password_hash` fica NULL para usuário
    de origem `ldap` (ver docs/spec_rbac.md, tabela do modelo de usuário).

`external_id` guarda o **objectGUID**, não o `distinguishedName` nem o
`sAMAccountName`. Os dois últimos MUDAM: o DN muda quando a pessoa é movida de
OU (uma reorganização de TI basta), e o sAMAccountName muda quando alguém é
renomeado (casamento, correção de grafia). O objectGUID é imutável pelo tempo
de vida da conta. Se o vínculo fosse pelo DN, mover um usuário de pasta no AD
quebraria o login dele aqui — em silêncio, e sem ninguém relacionar as duas
coisas.
"""
import logging
import re
import ssl
from typing import Any, Dict, List, Optional

from app.config import (
    LDAP_BASE_DN,
    LDAP_BIND_PASSWORD,
    LDAP_BIND_USER,
    LDAP_DOMAIN,
    LDAP_PORT,
    LDAP_SERVER,
    LDAP_USE_SSL,
    LDAP_VALIDAR_CERTIFICADO,
)

logger = logging.getLogger(__name__)

# Contas desabilitadas no AD têm o bit 2 (ACCOUNTDISABLE) ligado em
# userAccountControl. Sem checar isso, alguém desligado pela TI continuaria
# aparecendo como candidato válido para vínculo.
_ACCOUNTDISABLE = 0x0002

_ATRIBUTOS = [
    "objectGUID", "sAMAccountName", "displayName", "cn", "mail",
    "userAccountControl", "distinguishedName",
]

# Só letras, números, ponto, hífen, espaço, arroba e underscore. O resto é
# descartado antes de entrar no filtro LDAP.
_CARACTERES_PERMITIDOS_NA_BUSCA = re.compile(r"[^\w.\-@ ]", re.UNICODE)


class LDAPIndisponivelError(RuntimeError):
    """O diretório não respondeu. Distinto de "credencial errada": o chamador
    precisa poder dizer ao usuário que o problema é de infraestrutura, não
    negar o acesso como se a senha estivesse errada."""


def ldap_configurado() -> bool:
    """True quando há configuração suficiente para falar com o diretório.

    Sem isso, a tela de administração ofereceria vincular ao AD numa
    instalação que não tem AD nenhum — e o erro só apareceria no clique."""
    return bool(LDAP_SERVER and LDAP_BASE_DN and LDAP_BIND_USER and LDAP_BIND_PASSWORD)


def _tls():
    from ldap3 import Tls

    # `validate` configurável porque AD corporativo costuma usar certificado de
    # uma CA interna, que não está no truststore do container. Desligar a
    # validação é aceitável numa rede interna e é o padrão aqui, mas fica
    # explícito na configuração — não escondido no código.
    return Tls(validate=ssl.CERT_REQUIRED if LDAP_VALIDAR_CERTIFICADO else ssl.CERT_NONE)


def _conectar(usuario: Optional[str] = None, senha: Optional[str] = None):
    """Abre conexão com o diretório. Sem argumentos, usa a conta de serviço.

    Levanta LDAPIndisponivelError se o servidor não responder, e devolve None
    se o bind for recusado (credencial inválida) — o chamador distingue os dois
    casos, que têm tratamentos opostos."""
    from ldap3 import ALL, Connection, Server
    from ldap3.core.exceptions import LDAPException

    try:
        servidor = Server(
            LDAP_SERVER, port=LDAP_PORT, use_ssl=LDAP_USE_SSL, tls=_tls(), get_info=ALL,
            connect_timeout=8,
        )
        conexao = Connection(
            servidor,
            user=usuario if usuario is not None else LDAP_BIND_USER,
            password=senha if senha is not None else LDAP_BIND_PASSWORD,
            auto_bind=True,
            receive_timeout=10,
        )
        return conexao
    except LDAPException as e:
        # ldap3 usa a mesma família de exceção para "servidor fora" e para
        # "credencial recusada". Só o bind da conta de SERVIÇO falhando é
        # problema de infraestrutura; o de um usuário final é senha errada.
        if usuario is None:
            logger.error("Não foi possível conectar ao LDAP: %s", e)
            raise LDAPIndisponivelError(str(e)) from e
        return None


def _guid_normalizado(valor: Any) -> str:
    """objectGUID chega do AD como "{f15cd6bf-...-aa820fa28e38}". As chaves e a
    caixa variam conforme a versão do ldap3 e o servidor, então a forma
    guardada é sempre a mesma: minúscula e sem chaves. Sem normalizar, o mesmo
    usuário poderia ser vinculado duas vezes com grafias diferentes — e a
    constraint de unicidade de `external_id` não pegaria."""
    return str(valor).strip().strip("{}").lower()


def _entrada_para_dict(entrada) -> Dict[str, Any]:
    def campo(nome):
        valor = entrada[nome].value if nome in entrada else None
        return valor

    controle = campo("userAccountControl") or 0
    return {
        "external_id": _guid_normalizado(campo("objectGUID")),
        "login": campo("sAMAccountName"),
        "nome": campo("displayName") or campo("cn"),
        "email": campo("mail"),
        "dn": campo("distinguishedName") or entrada.entry_dn,
        "habilitado": not bool(int(controle) & _ACCOUNTDISABLE),
    }


def _escapar(termo: str) -> str:
    """Remove o que não é caractere de nome antes de montar o filtro.

    Um `*` ou `)` vindo da caixa de busca mudaria a estrutura do filtro LDAP —
    a mesma classe de problema de uma injeção de SQL. Aqui a defesa é
    allowlist: o que não é letra, número ou pontuação de nome simplesmente
    não entra."""
    return _CARACTERES_PERMITIDOS_NA_BUSCA.sub("", termo or "").strip()


def buscar_usuarios(termo: str, limite: int = 20) -> List[Dict[str, Any]]:
    """Procura contas de PESSOA no diretório por login, nome ou e-mail.

    `(objectCategory=person)` junto de `(objectClass=user)` exclui contas de
    computador, que também são `objectClass=user` no AD — sem isso, buscar por
    um prefixo comum devolve dezenas de máquinas no meio das pessoas."""
    from ldap3 import SUBTREE

    termo = _escapar(termo)
    if len(termo) < 2:
        return []

    filtro = (
        "(&(objectCategory=person)(objectClass=user)"
        f"(|(sAMAccountName=*{termo}*)(displayName=*{termo}*)(cn=*{termo}*)(mail=*{termo}*)))"
    )
    conexao = _conectar()
    try:
        conexao.search(
            LDAP_BASE_DN, filtro, search_scope=SUBTREE,
            attributes=_ATRIBUTOS, size_limit=limite,
        )
        encontrados = [_entrada_para_dict(e) for e in conexao.entries]
    finally:
        conexao.unbind()

    # Habilitados primeiro; os desabilitados continuam visíveis, mas marcados,
    # para o administrador entender por que não deveria vincular aquele.
    return sorted(encontrados, key=lambda u: (not u["habilitado"], (u["login"] or "").lower()))


def obter_por_external_id(external_id: str) -> Optional[Dict[str, Any]]:
    """A conta correspondente ao objectGUID, ou None se não existir mais.

    Usado para mostrar na tela a quem o usuário está vinculado, e para detectar
    vínculo órfão — conta apagada no AD com o vínculo ainda de pé aqui."""
    from ldap3 import SUBTREE

    guid = _guid_normalizado(external_id)
    if not re.fullmatch(r"[0-9a-f\-]{36}", guid):
        return None

    conexao = _conectar()
    try:
        conexao.search(
            LDAP_BASE_DN, f"(objectGUID={_guid_para_filtro(guid)})",
            search_scope=SUBTREE, attributes=_ATRIBUTOS, size_limit=1,
        )
        if not conexao.entries:
            return None
        return _entrada_para_dict(conexao.entries[0])
    finally:
        conexao.unbind()


def _guid_para_filtro(guid: str) -> str:
    """objectGUID é binário no AD: num filtro de busca ele precisa ir como
    bytes escapados (\\xx), na ordem que o Windows usa — os três primeiros
    grupos em little-endian, os dois últimos como estão. Passar o GUID em
    texto não casa com nada, e sem erro nenhum."""
    partes = guid.split("-")
    if len(partes) != 5:
        return guid
    ordenado = (
        partes[0][6:8] + partes[0][4:6] + partes[0][2:4] + partes[0][0:2]
        + partes[1][2:4] + partes[1][0:2]
        + partes[2][2:4] + partes[2][0:2]
        + partes[3] + partes[4]
    )
    return "".join(f"\\{ordenado[i:i + 2]}" for i in range(0, len(ordenado), 2))


def autenticar(login: str, senha: str) -> bool:
    """Confere a senha fazendo bind no diretório COM a conta do usuário.

    É o único jeito honesto de validar senha em AD: não existe API que devolva
    o hash, e não deveria existir. Senha vazia é recusada antes da rede porque
    o LDAP trata bind sem senha como "bind anônimo" e responde SUCESSO — um
    usuário entraria com a senha em branco."""
    if not senha or not login:
        return False

    principal = login if "@" in login or "\\" in login else f"{login}@{LDAP_DOMAIN}"
    conexao = _conectar(usuario=principal, senha=senha)
    if conexao is None:
        return False
    conexao.unbind()
    return True
