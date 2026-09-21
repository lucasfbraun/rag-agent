"""
CLI de ingestão do PU Matcher.

Uso:
    python -m app.cli ingest
    python -m app.cli ingest --dir /app/data/raw_documents
    python -m app.cli ingest --dir /app/data/raw_documents --model text-embedding-3-small

    python -m app.cli health

    python -m app.cli relatorio-validacao
    python -m app.cli relatorio-validacao --desde 2026-09-01
    python -m app.cli relatorio-validacao --formato json > relatorio.json
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
    from qdrant_client import QdrantClient
    from app.config import QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME

    try:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=5)
        collections = client.get_collections().collections
        col_names = [c.name for c in collections]
        print(f"✅ Qdrant online em {QDRANT_HOST}:{QDRANT_PORT}")

        if COLLECTION_NAME in col_names:
            col_info = client.get_collection(COLLECTION_NAME)
            print(f"✅ Coleção '{COLLECTION_NAME}': {col_info.points_count} pontos indexados")
        else:
            print(f"⚠️ Coleção '{COLLECTION_NAME}' ainda não existe. Execute: python -m app.cli ingest")
    except Exception as e:
        print(f"❌ Qdrant inacessível: {e}")
        sys.exit(1)


def _percentual(taxa) -> str:
    """`None` não vira 0%. Sem julgamento não existe taxa, e mostrar 0% seria
    afirmar que o sistema erra tudo — coisa que o dado não diz."""
    return "—" if taxa is None else f"{taxa * 100:.1f}%"


def formatar_relatorio_validacao(relatorio: dict) -> str:
    """Renderização em texto do relatório de validação. Pura, para ser testável
    sem banco: a suíte deste projeto roda sem PostgreSQL."""
    linhas = ["", "=" * 72, "RELATÓRIO DE VALIDAÇÃO TÉCNICA DO PU MATCHER", "=" * 72]
    if relatorio.get("desde"):
        linhas.append(f"Período: a partir de {relatorio['desde']}")

    linhas += [
        "",
        f"Respostas do agente no período : {relatorio['total_respostas_no_periodo']}",
        f"Validadas por um técnico       : {relatorio['total_respostas_validadas']}"
        f"  (cobertura: {_percentual(relatorio.get('cobertura'))})",
        f"Corretas / incorretas / incompletas: "
        f"{relatorio['corretas']} / {relatorio['incorretas']} / {relatorio['incompletas']}",
        f"Vereditos divergentes          : {relatorio['divergentes']}",
        "",
        f"TAXA DE ACERTO GERAL           : {_percentual(relatorio['taxa_acerto'])}",
    ]

    for aviso in relatorio.get("avisos", []):
        linhas.append(f"  ⚠️  {aviso}")

    if relatorio["por_caminho"]:
        linhas += [
            "",
            "-" * 72,
            "TAXA POR CAMINHO DO MOTOR (do pior para o melhor)",
            "-" * 72,
            "É esta a coluna que diz ONDE o sistema erra — a taxa geral sozinha",
            "mistura mecanismos que não têm nada a ver um com o outro.",
            "",
        ]
        for c in relatorio["por_caminho"]:
            marca = "" if c["amostra_suficiente"] else "  (amostra insuficiente)"
            linhas.append(
                f"  {_percentual(c['taxa_acerto']):>7}  n={c['total']:<4} "
                f"{c['rotulo']}{marca}"
            )
            linhas.append(
                f"           corretas {c['corretas']}, incorretas {c['incorretas']}, "
                f"incompletas {c['incompletas']}, divergentes {c['divergentes']}"
            )

    if relatorio["regressao"]:
        linhas += [
            "",
            "-" * 72,
            f"CONJUNTO DE REGRESSÃO — {len(relatorio['regressao'])} pergunta(s) que erraram",
            "-" * 72,
            "São perguntas REAIS. Toda mudança no motor deve ser verificada contra",
            "esta lista antes de ser considerada uma melhora.",
            "",
        ]
        for item in relatorio["regressao"]:
            linhas.append(f"  [{item['veredito']}] ({item['caminho_rotulo']})")
            linhas.append(f"    P: {item['pergunta'][:200]}")
            for correcao in item.get("correcoes", []):
                if correcao.get("resposta_correta"):
                    linhas.append(f"    R certa: {correcao['resposta_correta'][:300]}")
                if correcao.get("justificativa"):
                    linhas.append(f"    Porquê : {correcao['justificativa'][:300]}")
            linhas.append("")

    linhas.append("")
    return "\n".join(linhas)


def cmd_relatorio_validacao(args):
    """Taxa de acerto medida pelos vereditos técnicos — geral e por caminho.

    Existe como CLI além do endpoint porque quem opera o servidor precisa
    conseguir o número sem passar pela tela e sem um token, e porque o
    `--formato json` permite guardar o histórico e comparar duas semanas."""
    import json
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.validacao_service import gerar_relatorio

    desde = None
    if args.desde:
        try:
            desde = datetime.fromisoformat(args.desde).replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"❌ Data inválida: {args.desde} (use AAAA-MM-DD)")
            sys.exit(1)

    session = SessionLocal()
    try:
        relatorio = gerar_relatorio(session, desde=desde)
    finally:
        session.close()

    if args.formato == "json":
        print(json.dumps(relatorio, ensure_ascii=False, indent=2, default=str))
    else:
        print(formatar_relatorio_validacao(relatorio))


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
    from app.config import EMBEDDING_MODEL
    p_ingest.add_argument(
        "--model",
        default=EMBEDDING_MODEL,
        help=f"Modelo de embedding a usar (default: {EMBEDDING_MODEL})"
    )
    p_ingest.set_defaults(func=cmd_ingest)

    # Subcomando: health
    p_health = subparsers.add_parser("health", help="Verificar conectividade com Qdrant")
    p_health.set_defaults(func=cmd_health)

    # Subcomando: relatorio-validacao
    p_relatorio = subparsers.add_parser(
        "relatorio-validacao",
        help="Taxa de acerto medida pelos vereditos técnicos (geral, por caminho e regressão)",
    )
    p_relatorio.add_argument(
        "--desde",
        default=None,
        help="Considerar apenas vereditos a partir desta data (AAAA-MM-DD)",
    )
    p_relatorio.add_argument(
        "--formato",
        choices=("texto", "json"),
        default="texto",
        help="texto para ler na hora; json para guardar e comparar semanas (default: texto)",
    )
    p_relatorio.set_defaults(func=cmd_relatorio_validacao)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
