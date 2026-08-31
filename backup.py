"""
Backup do Qdrant (snapshot) e do Postgres (pg_dump) - Fase 8, item
"Configurar backup do Qdrant e PostgreSQL" do CRONOGRAMA.

Motivacao real, nao hipotetica: a Sessao 30 apagou os 11273 pontos reais
da colecao pu_products_catalog durante o desenvolvimento do ticket 7
(docs/incidente_2026-08-26_reingestao_apagou_colecao.md) - sem nenhum
snapshot configurado, nao havia caminho de recuperacao automatica, so a
sorte dos documentos-fonte originais ainda estarem intactos na pasta de
rede. Este script nao evita o proximo bug de reconciliacao ou operacao
destrutiva - evita que o proximo seja irrecuperavel.

Roda do HOST (nao de dentro de um container), pelos mesmos dois motivos
que ja levam ingest_network.py a rodar assim: precisa do Qdrant pela porta
publicada em localhost (QDRANT_HOST do .env e "qdrant", nome de servico so
resolvivel de dentro da rede do Compose) e do binario pg_dump, que so
existe na imagem do container postgres - chamado via `docker exec`, nao
instalado no host nem no container backend.

Uso:
    python backup.py                  # Qdrant + Postgres
    python backup.py --qdrant-only
    python backup.py --postgres-only
    python backup.py --manter 30      # retencao (default: 14 backups mais recentes de cada tipo)

Restaurar:
    Qdrant:   copiar o .snapshot para dentro do container e rodar
              client.recover_snapshot(collection_name, "file:///caminho")
              (ou POST /collections/{name}/snapshots/recover via REST).
    Postgres: docker exec -i pu_matcher_postgres psql -U pu_matcher pu_matcher < backup.sql
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "pu_products_catalog")
POSTGRES_CONTAINER = "pu_matcher_postgres"
POSTGRES_USER = os.getenv("POSTGRES_USER", "pu_matcher")
POSTGRES_DB = os.getenv("POSTGRES_DB", "pu_matcher")

BACKUP_DIR = Path(__file__).resolve().parent / "data" / "backups"
MANTER_PADRAO = 14


def criar_snapshot_qdrant(qdrant_url: str, collection_name: str, *, post=requests.post) -> str:
    """Pede ao Qdrant pra criar um snapshot da colecao; chamada bloqueante
    (sincrona ate o snapshot terminar, comportamento do Qdrant 1.9 - sem
    necessidade de polling). Devolve o nome do arquivo gerado no servidor."""
    resp = post(f"{qdrant_url}/collections/{collection_name}/snapshots", timeout=300)
    resp.raise_for_status()
    return resp.json()["result"]["name"]


def baixar_snapshot_qdrant(
    qdrant_url: str, collection_name: str, snapshot_name: str, destino: Path, *, get=requests.get
) -> Path:
    """Baixa o snapshot ja criado no servidor Qdrant pro filesystem local -
    o arquivo vive dentro do volume do container qdrant, nao do host."""
    resp = get(
        f"{qdrant_url}/collections/{collection_name}/snapshots/{snapshot_name}",
        stream=True,
        timeout=300,
    )
    resp.raise_for_status()
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    return destino


def backup_qdrant(
    qdrant_url: str,
    collection_name: str,
    destino_dir: Path,
    *,
    timestamp: str,
    post=requests.post,
    get=requests.get,
) -> Path:
    snapshot_name = criar_snapshot_qdrant(qdrant_url, collection_name, post=post)
    destino = destino_dir / f"{collection_name}_{timestamp}.snapshot"
    return baixar_snapshot_qdrant(qdrant_url, collection_name, snapshot_name, destino, get=get)


def comando_pg_dump(usuario: str, banco: str, container: str) -> list[str]:
    return ["docker", "exec", container, "pg_dump", "-U", usuario, banco]


def backup_postgres(
    destino_dir: Path,
    *,
    usuario: str,
    banco: str,
    container: str,
    timestamp: str,
    run_command=subprocess.run,
) -> Path:
    """`pg_dump` so existe na imagem do container postgres - rodado via
    `docker exec` a partir do host, saida (stdout) gravada em disco aqui."""
    resultado = run_command(
        comando_pg_dump(usuario, banco, container),
        check=True,
        capture_output=True,
    )
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"{banco}_{timestamp}.sql"
    destino.write_bytes(resultado.stdout)
    return destino


def podar_antigos(diretorio: Path, manter: int) -> list[Path]:
    """Mantem os `manter` arquivos mais recentes do diretorio (por mtime) e
    apaga o resto - sem isso, backup diario/manual cresce sem limite."""
    if manter < 0 or not diretorio.exists():
        return []
    arquivos = sorted(
        (p for p in diretorio.iterdir() if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    apagados = []
    for arquivo in arquivos[manter:]:
        arquivo.unlink()
        apagados.append(arquivo)
    return apagados


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Backup do Qdrant e/ou Postgres do PU Matcher")
    parser.add_argument("--qdrant-only", action="store_true")
    parser.add_argument("--postgres-only", action="store_true")
    parser.add_argument("--manter", type=int, default=MANTER_PADRAO, help="Backups recentes a manter por tipo")
    args = parser.parse_args(argv)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    fazer_qdrant = not args.postgres_only
    fazer_postgres = not args.qdrant_only

    if fazer_qdrant:
        destino_dir = BACKUP_DIR / "qdrant"
        print(f"Criando snapshot da colecao '{COLLECTION_NAME}'...")
        try:
            caminho = backup_qdrant(QDRANT_URL, COLLECTION_NAME, destino_dir, timestamp=timestamp)
            print(f"OK: {caminho}")
            for apagado in podar_antigos(destino_dir, args.manter):
                print(f"Removido (retencao): {apagado.name}")
        except Exception as e:
            print(f"ERRO no backup do Qdrant: {e}")
            sys.exit(1)

    if fazer_postgres:
        destino_dir = BACKUP_DIR / "postgres"
        print(f"Rodando pg_dump em '{POSTGRES_DB}'...")
        try:
            caminho = backup_postgres(
                destino_dir,
                usuario=POSTGRES_USER,
                banco=POSTGRES_DB,
                container=POSTGRES_CONTAINER,
                timestamp=timestamp,
            )
            print(f"OK: {caminho}")
            for apagado in podar_antigos(destino_dir, args.manter):
                print(f"Removido (retencao): {apagado.name}")
        except subprocess.CalledProcessError as e:
            print(f"ERRO no backup do Postgres: {e.stderr.decode(errors='replace')}")
            sys.exit(1)


if __name__ == "__main__":
    main()
