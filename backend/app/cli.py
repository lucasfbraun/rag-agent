"""
CLI de ingestão do PU Matcher.

Uso:
    python -m app.cli ingest
    python -m app.cli ingest --dir /app/data/raw_documents
    python -m app.cli ingest --dir /app/data/raw_documents --model text-embedding-3-small

    python -m app.cli health
"""
import argparse
import sys


def cmd_ingest(args):
    """Indexa o diretório de documentos no Qdrant."""
    import os
    if not os.path.isdir(args.dir):
        print(f"❌ Diretório não encontrado: {args.dir}")
        sys.exit(1)

    from app.rag.ingestion import ingest_catalog_directory
    print(f"📂 Diretório: {args.dir}")
    print(f"🤖 Modelo de embedding: {args.model}")
    ingest_catalog_directory(args.dir, embedding_model=args.model)


def cmd_health(args):
    """Verifica conectividade com o Qdrant e status da coleção."""
    import os
    from qdrant_client import QdrantClient
    from app.rag.ingestion import COLLECTION_NAME

    qdrant_host = os.getenv("QDRANT_HOST", "localhost")
    qdrant_port = int(os.getenv("QDRANT_PORT", 6333))

    try:
        client = QdrantClient(host=qdrant_host, port=qdrant_port, timeout=5)
        collections = client.get_collections().collections
        col_names = [c.name for c in collections]
        print(f"✅ Qdrant online em {qdrant_host}:{qdrant_port}")

        if COLLECTION_NAME in col_names:
            col_info = client.get_collection(COLLECTION_NAME)
            print(f"✅ Coleção '{COLLECTION_NAME}': {col_info.points_count} pontos indexados")
        else:
            print(f"⚠️ Coleção '{COLLECTION_NAME}' ainda não existe. Execute: python -m app.cli ingest")
    except Exception as e:
        print(f"❌ Qdrant inacessível: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="PU Matcher — CLI de administração"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcomando: ingest
    p_ingest = subparsers.add_parser("ingest", help="Indexar documentos técnicos no Qdrant")
    p_ingest.add_argument(
        "--dir",
        default="/app/data/raw_documents",
        help="Caminho do diretório com PDFs, DOCXs e TXTs (default: /app/data/raw_documents)"
    )
    p_ingest.add_argument(
        "--model",
        default="text-embedding-3-small",
        help="Modelo de embedding a usar (default: text-embedding-3-small)"
    )
    p_ingest.set_defaults(func=cmd_ingest)

    # Subcomando: health
    p_health = subparsers.add_parser("health", help="Verificar conectividade com Qdrant")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
