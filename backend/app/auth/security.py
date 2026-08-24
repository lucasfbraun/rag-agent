"""
Hashing de senha (Fase 5 — RBAC & Governança).

Interface mínima de propósito: hash_password / verify_password. Nenhum outro
módulo deve chamar bcrypt diretamente — se o algoritmo mudar um dia, muda só aqui.
"""
import bcrypt

MIN_PASSWORD_LENGTH = 8


class SenhaFracaError(ValueError):
    """Senha não atende ao mínimo exigido (hoje: só comprimento — ver docs/spec_rbac.md)."""


def hash_password(plain_password: str) -> str:
    """Gera o hash a persistir. Nunca guardar plain_password em lugar nenhum."""
    if not plain_password or len(plain_password) < MIN_PASSWORD_LENGTH:
        raise SenhaFracaError(f"Senha deve ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Confere a senha digitada contra o hash armazenado. Nunca lança em senha errada."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Hash malformado/vazio (ex: usuário de origem LDAP sem password_hash) — sempre nega.
        return False
