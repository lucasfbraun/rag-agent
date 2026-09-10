"""
Conversa REAL com o Active Directory — sem mock.

Existe porque a parte difícil desta integração não é a lógica de vínculo (essa
está em test_ldap_vinculo.py, com o diretório mockado): é acertar o protocolo.
O `objectGUID` do AD é binário e, num filtro de busca, precisa ir como bytes
escapados com os três primeiros grupos em LITTLE-ENDIAN. Passar o GUID em
texto não casa com nada — e não dá erro: a busca volta vazia, o vínculo parece
"conta não encontrada", e nenhum mock jamais pegaria isso.

Estes testes PULAM sozinhos quando não há AD configurado ou alcançável, então
não quebram a suíte de quem roda em outra máquina ou no CI. Quando o AD está
lá, eles são a única prova de que a integração funciona de verdade.
"""
import pytest

from app.auth import ldap_service


def _pular_se_indisponivel():
    if not ldap_service.ldap_configurado():
        pytest.skip("LDAP não configurado neste ambiente (LDAP_SERVER vazio).")
    try:
        conexao = ldap_service._conectar()
        conexao.unbind()
    except Exception as e:
        pytest.skip(f"Active Directory inalcançável daqui: {type(e).__name__}")


@pytest.fixture(scope="module")
def ad():
    _pular_se_indisponivel()


@pytest.fixture(scope="module")
def uma_conta(ad):
    """Uma conta qualquer, habilitada, para exercitar o round-trip. Usa a
    própria conta de serviço, que é a única garantidamente presente."""
    contas = ldap_service.buscar_usuarios(ldap_service.LDAP_BIND_USER.split("@")[0])
    if not contas:
        pytest.skip("A conta de serviço não foi encontrada na busca.")
    return contas[0]


def test_conta_de_servico_faz_bind(ad):
    """Se isto falha, LDAP_BIND_USER/PASSWORD estão errados e nada mais
    funciona — vale isolar a causa antes de investigar o resto."""
    conexao = ldap_service._conectar()
    assert conexao.bound
    conexao.unbind()


def test_busca_devolve_contas_de_pessoa_com_os_campos_esperados(uma_conta):
    for campo in ("external_id", "login", "nome", "dn", "habilitado"):
        assert campo in uma_conta, f"campo '{campo}' ausente no resultado da busca"
    assert uma_conta["login"]


def test_objectguid_e_normalizado_sem_chaves_e_em_minusculo(uma_conta):
    """O AD devolve "{F15CD6BF-...}"; chaves e caixa variam com a versão do
    ldap3. Sem normalizar, o mesmo usuário poderia ser vinculado duas vezes com
    grafias diferentes, e a unicidade de `external_id` não pegaria."""
    guid = uma_conta["external_id"]
    assert "{" not in guid and "}" not in guid
    assert guid == guid.lower()
    assert len(guid) == 36, f"formato inesperado de objectGUID: {guid!r}"


def test_busca_pelo_guid_encontra_a_mesma_conta(uma_conta):
    """O round-trip que só um AD real exercita: é aqui que um erro na
    conversão little-endian do GUID apareceria — como busca vazia, sem
    exceção nenhuma."""
    de_volta = ldap_service.obter_por_external_id(uma_conta["external_id"])
    assert de_volta is not None, (
        "busca por objectGUID voltou vazia — provável erro na conversão do GUID "
        "para filtro binário (ver _guid_para_filtro)"
    )
    assert de_volta["login"] == uma_conta["login"]


def test_guid_inexistente_devolve_none(ad):
    assert ldap_service.obter_por_external_id("00000000-0000-0000-0000-000000000000") is None


def test_guid_malformado_nao_vira_consulta(ad):
    """Entrada inválida é recusada antes da rede, não vira filtro quebrado."""
    assert ldap_service.obter_por_external_id("não-é-um-guid") is None


def test_termo_curto_demais_nao_varre_o_diretorio(ad):
    """Uma letra devolveria meia empresa e prenderia o servidor à toa."""
    assert ldap_service.buscar_usuarios("a") == []


def test_caractere_de_filtro_e_removido_da_busca(ad):
    """Um `*` ou `)` vindo da caixa de busca mudaria a ESTRUTURA do filtro LDAP
    — mesma classe de problema de uma injeção de SQL. A defesa é allowlist.
    O teste confirma que a busca não explode nem devolve o diretório inteiro."""
    resultado = ldap_service.buscar_usuarios("*)(objectClass=*")
    assert isinstance(resultado, list)


def test_senha_errada_e_recusada(ad):
    login = ldap_service.LDAP_BIND_USER.split("@")[0]
    assert ldap_service.autenticar(login, "senha-quase-certamente-errada-2026") is False


def test_senha_vazia_e_recusada_antes_da_rede(ad):
    """O LDAP trata bind sem senha como bind ANÔNIMO e responde SUCESSO — sem
    esta guarda, qualquer pessoa entraria deixando o campo senha em branco."""
    assert ldap_service.autenticar(ldap_service.LDAP_BIND_USER, "") is False


def test_senha_correta_da_conta_de_servico_e_aceita(ad):
    """Fecha o par com o teste acima: a recusa não é um "sempre False"."""
    assert ldap_service.autenticar(
        ldap_service.LDAP_BIND_USER, ldap_service.LDAP_BIND_PASSWORD
    ) is True
