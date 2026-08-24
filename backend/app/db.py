"""
Conexão com o banco relacional (Fase 5 — RBAC & Governança).

Interface pequena de propósito: get_session() para uso em endpoints/services,
Base para os models declararem suas tabelas. Nenhum outro módulo deve
instanciar engine/sessão diretamente — sempre passar por aqui.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_session():
    """Dependency do FastAPI: uma sessão por requisição, sempre fechada ao final."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
