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
