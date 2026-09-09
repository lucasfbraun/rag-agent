"""Contrato de inicializacao da imagem do backend."""

import unittest

from app.startup import start


class StartupTest(unittest.TestCase):
    def test_start_aplica_migrations_antes_de_iniciar_aplicacao(self):
        events = []

        def run_command(command, *, check):
            events.append(("migration", command, check))

        def replace_process(program, argv):
            events.append(("application", program, argv))

        start(
            ["uvicorn", "app.main:app"],
            run_command=run_command,
            replace_process=replace_process,
        )

        self.assertEqual(
            events,
            [
                (
                    "migration",
                    ["alembic", "-c", "/app/backend/alembic.ini", "upgrade", "head"],
                    True,
                ),
                ("application", "uvicorn", ["uvicorn", "app.main:app"]),
            ],
        )


if __name__ == "__main__":
    unittest.main()


# --- nome da coleção configurável (2026-09-09) -----------------------------

def test_nome_da_colecao_vem_do_ambiente_com_padrao():
    """Restaurar um snapshot cria a coleção com o nome que ela tinha na ORIGEM,
    e o Qdrant não renomeia coleção. Com o nome fixo no código, um snapshot
    restaurado sob outro nome deixava a aplicação reportando "base vetorial
    vazia" — tudo saudável, o dado ali do lado, e nenhuma pista de que era só
    divergência de nome."""
    import importlib
    import os
    from unittest.mock import patch

    import app.config

    with patch.dict(os.environ, {"COLLECTION_NAME": "documentacaoAD"}):
        recarregado = importlib.reload(app.config)
        assert recarregado.COLLECTION_NAME == "documentacaoAD"

    # Sem a variável, mantém o nome histórico — nenhuma instalação existente
    # quebra por causa desta mudança.
    ambiente_sem_a_variavel = {k: v for k, v in os.environ.items() if k != "COLLECTION_NAME"}
    with patch.dict(os.environ, ambiente_sem_a_variavel, clear=True):
        recarregado = importlib.reload(app.config)
        assert recarregado.COLLECTION_NAME == "pu_products_catalog"

    importlib.reload(app.config)  # devolve o módulo ao estado real
