"""Inicializacao do backend: aplica migrations antes de iniciar a aplicacao."""

import os
import subprocess
import sys
from collections.abc import Callable, Sequence


MIGRATION_COMMAND = [
    "alembic",
    "-c",
    "/app/backend/alembic.ini",
    "upgrade",
    "head",
]


def start(
    argv: Sequence[str],
    *,
    run_command: Callable = subprocess.run,
    replace_process: Callable = os.execvp,
) -> None:
    """Aplica o schema e substitui o processo pelo comando da aplicacao."""
    if not argv:
        raise ValueError("Nenhum comando de aplicacao informado.")

    run_command(MIGRATION_COMMAND, check=True)
    replace_process(argv[0], list(argv))


def main() -> None:
    start(sys.argv[1:])


if __name__ == "__main__":
    main()
