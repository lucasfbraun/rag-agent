"""
Contrato de backup.py (Fase 8: "Configurar backup do Qdrant e PostgreSQL").

Roda do host, igual o proprio backup.py (nao dentro de um container) - por
isso fica na raiz do repo, ao lado dele, e nao em backend/tests (que roda
com PYTHONPATH=/app/backend dentro do container backend).

Nenhum teste aqui bate em Qdrant/Postgres/docker de verdade - todo I/O
(HTTP, subprocess, filesystem) e injetado, mesmo padrao ja usado em
backend/tests/test_startup.py para o contrato de inicializacao.
"""
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from backup import (
    backup_postgres,
    backup_qdrant,
    baixar_snapshot_qdrant,
    comando_pg_dump,
    criar_snapshot_qdrant,
    podar_antigos,
)


class CriarSnapshotQdrantTest(unittest.TestCase):
    def test_devolve_o_nome_do_snapshot_criado(self):
        resp = MagicMock()
        resp.json.return_value = {"result": {"name": "pu_products_catalog-123.snapshot"}}
        post = MagicMock(return_value=resp)

        nome = criar_snapshot_qdrant("http://localhost:6333", "pu_products_catalog", post=post)

        self.assertEqual(nome, "pu_products_catalog-123.snapshot")
        post.assert_called_once_with(
            "http://localhost:6333/collections/pu_products_catalog/snapshots", timeout=300
        )
        resp.raise_for_status.assert_called_once()


class BaixarSnapshotQdrantTest(unittest.TestCase):
    def test_grava_o_conteudo_baixado_no_destino(self):
        resp = MagicMock()
        resp.iter_content.return_value = [b"abc", b"def"]
        get = MagicMock(return_value=resp)

        with TemporaryDirectory() as tmp:
            destino = Path(tmp) / "sub" / "arquivo.snapshot"
            caminho = baixar_snapshot_qdrant(
                "http://localhost:6333", "colecao", "colecao-1.snapshot", destino, get=get
            )

            self.assertEqual(caminho, destino)
            self.assertEqual(destino.read_bytes(), b"abcdef")
        get.assert_called_once_with(
            "http://localhost:6333/collections/colecao/snapshots/colecao-1.snapshot",
            stream=True,
            timeout=300,
        )
        resp.raise_for_status.assert_called_once()


class BackupQdrantTest(unittest.TestCase):
    def test_cria_e_baixa_o_snapshot_com_nome_baseado_no_timestamp(self):
        post_resp = MagicMock()
        post_resp.json.return_value = {"result": {"name": "srv-snapshot-xyz.snapshot"}}
        get_resp = MagicMock()
        get_resp.iter_content.return_value = [b"dado"]

        with TemporaryDirectory() as tmp:
            destino_dir = Path(tmp)
            caminho = backup_qdrant(
                "http://localhost:6333",
                "pu_products_catalog",
                destino_dir,
                timestamp="20260831_120000",
                post=MagicMock(return_value=post_resp),
                get=MagicMock(return_value=get_resp),
            )

            self.assertEqual(
                caminho, destino_dir / "pu_products_catalog_20260831_120000.snapshot"
            )
            self.assertEqual(caminho.read_bytes(), b"dado")


class ComandoPgDumpTest(unittest.TestCase):
    def test_monta_comando_via_docker_exec(self):
        comando = comando_pg_dump("pu_matcher", "pu_matcher", "pu_matcher_postgres")

        self.assertEqual(
            comando,
            ["docker", "exec", "pu_matcher_postgres", "pg_dump", "-U", "pu_matcher", "pu_matcher"],
        )


class BackupPostgresTest(unittest.TestCase):
    def test_grava_stdout_do_pg_dump_no_arquivo_de_destino(self):
        run_command = MagicMock(
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=b"-- dump sql")
        )

        with TemporaryDirectory() as tmp:
            destino_dir = Path(tmp)
            caminho = backup_postgres(
                destino_dir,
                usuario="pu_matcher",
                banco="pu_matcher",
                container="pu_matcher_postgres",
                timestamp="20260831_120000",
                run_command=run_command,
            )

            self.assertEqual(caminho, destino_dir / "pu_matcher_20260831_120000.sql")
            self.assertEqual(caminho.read_bytes(), b"-- dump sql")

        run_command.assert_called_once_with(
            ["docker", "exec", "pu_matcher_postgres", "pg_dump", "-U", "pu_matcher", "pu_matcher"],
            check=True,
            capture_output=True,
        )

    def test_propaga_erro_do_pg_dump_sem_criar_arquivo(self):
        run_command = MagicMock(
            side_effect=subprocess.CalledProcessError(1, ["pg_dump"], stderr=b"erro")
        )

        with TemporaryDirectory() as tmp:
            destino_dir = Path(tmp)
            with self.assertRaises(subprocess.CalledProcessError):
                backup_postgres(
                    destino_dir,
                    usuario="pu_matcher",
                    banco="pu_matcher",
                    container="pu_matcher_postgres",
                    timestamp="20260831_120000",
                    run_command=run_command,
                )

            self.assertEqual(list(destino_dir.iterdir()), [])


class PodarAntigosTest(unittest.TestCase):
    def test_mantem_so_os_n_mais_recentes(self):
        with TemporaryDirectory() as tmp:
            diretorio = Path(tmp)
            caminhos = []
            for i in range(5):
                p = diretorio / f"backup_{i}.snapshot"
                p.write_text("x")
                caminhos.append(p)

            # Força mtimes distintos e crescentes (backup_4 é o mais recente).
            import os as _os
            for i, p in enumerate(caminhos):
                _os.utime(p, (i, i))

            apagados = podar_antigos(diretorio, manter=2)

            restantes = {p.name for p in diretorio.iterdir()}
            self.assertEqual(restantes, {"backup_4.snapshot", "backup_3.snapshot"})
            self.assertEqual({p.name for p in apagados}, {"backup_0.snapshot", "backup_1.snapshot", "backup_2.snapshot"})

    def test_diretorio_inexistente_nao_gera_erro(self):
        self.assertEqual(podar_antigos(Path("/caminho/que/nao/existe"), manter=5), [])

    def test_manter_maior_que_a_quantidade_existente_nao_apaga_nada(self):
        with TemporaryDirectory() as tmp:
            diretorio = Path(tmp)
            (diretorio / "unico.sql").write_text("x")

            apagados = podar_antigos(diretorio, manter=10)

            self.assertEqual(apagados, [])
            self.assertEqual(len(list(diretorio.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
