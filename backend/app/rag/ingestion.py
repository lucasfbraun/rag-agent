import os
import uuid
import glob
from collections import defaultdict
from typing import Dict, List, Set
from pypdf import PdfReader
import docx
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
from app.rag.embeddings import get_embedding
from app.config import QDRANT_HOST, QDRANT_PORT, COLLECTION_NAME, EMBEDDING_MODEL, VECTOR_SIZE

def get_qdrant_client() -> QdrantClient:
    """Cria e retorna um cliente Qdrant (lazy, evita falha no import-time)."""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)

def init_qdrant_collection(client: QdrantClient):
    """Garante que a coleção de produtos e TDS exista no Qdrant."""
    collections = client.get_collections().collections
    if any(c.name == COLLECTION_NAME for c in collections):
        print(f"ℹ️ Coleção '{COLLECTION_NAME}' já existe — ingestão incremental.")
        return
    try:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qmodels.VectorParams(
                size=VECTOR_SIZE,
                distance=qmodels.Distance.COSINE
            )
        )
        print(f"✅ Coleção '{COLLECTION_NAME}' inicializada.")
    except Exception as e:
        # Corrida entre duas ingestões simultâneas criando a coleção ao mesmo
        # tempo (AUD-007, achado novo). Se ela já existe agora, foi a outra
        # chamada vencendo a corrida — não é uma falha de verdade.
        colecoes_agora = client.get_collections().collections
        if any(c.name == COLLECTION_NAME for c in colecoes_agora):
            print(f"ℹ️ Coleção '{COLLECTION_NAME}' criada por outra ingestão concorrente.")
            return
        print(f"❌ Erro ao inicializar coleção Qdrant: {e}")
        raise

def extract_text_from_file(filepath: str) -> str:
    """Extrai texto e tabelas técnicas de PDFs, DOCX e TXT de produtos."""
    ext = os.path.splitext(filepath)[1].lower()
    text = ""

    if ext == ".pdf":
        reader = PdfReader(filepath)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"

    elif ext in (".docx", ".doc"):
        doc = docx.Document(filepath)
        for p in doc.paragraphs:
            text += p.text + "\n"
        for table in doc.tables:
            for row in table.rows:
                text += " | ".join(c.text.strip() for c in row.cells) + "\n"

    elif ext == ".txt":
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

    return text

# Classificação de sensibilidade do RAG não estruturado (AUD-002, ticket 6).
# Deliberadamente ESTREITA — frases específicas, não palavras genéricas como
# "custo" sozinha — pra não classificar specs técnicas legítimas (densidade,
# NCO%, viscosidade) como sensíveis por engano; Vendedor precisa continuar
# vendo essas. Sub-inclusiva de propósito: baixo recall é um débito aceitável
# aqui, falso positivo bloqueando spec real não é. Ver docs/spec_rbac.md.
_PALAVRAS_CHAVE_SENSIVEIS = (
    "custo industrial",
    "custo unitário",
    "custo unitario",
    "preço de venda",
    "preco de venda",
    "margem de contribuição",
    "margem de contribuicao",
    "r$",
    "fórmula confidencial",
    "formula confidencial",
    "composição proprietária",
    "composicao proprietaria",
    "receita de formulação",
    "receita de formulacao",
    "matéria-prima e proporção",
    "materia-prima e proporcao",
)


def _e_conteudo_sensivel(texto: str) -> bool:
    """True se o chunk contém indício de custo/fórmula confidencial —
    controla `payload["sensivel"]` na ingestão e o filtro de
    `retrieve_products_context(..., incluir_sensivel=...)`."""
    texto_lower = texto.lower()
    return any(palavra in texto_lower for palavra in _PALAVRAS_CHAVE_SENSIVEIS)


def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> List[str]:
    """Divide texto em chunks com overlap para preservar contexto entre trechos."""
    words = text.split()
    chunks = []
    step = max(chunk_size - overlap, 1)
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + chunk_size])
        if len(chunk.strip()) > 40:
            chunks.append(chunk)
    return chunks

def _pontos_existentes_por_arquivo(client: QdrantClient, collection_name: str) -> Dict[str, Set[str]]:
    """Varre a coleção inteira e devolve {filepath: {ponto_id, ...}}.

    Base da reconciliação (AUD-007, ticket 7): sem saber o que já está
    indexado por arquivo, não dá pra saber quais pontos ficaram obsoletos
    quando um arquivo encolhe, muda ou é removido do acervo."""
    existentes: Dict[str, Set[str]] = defaultdict(set)
    offset = None
    while True:
        pontos, offset = client.scroll(
            collection_name=collection_name,
            with_payload=["filepath"],
            with_vectors=False,
            limit=500,
            offset=offset,
        )
        for ponto in pontos:
            filepath = (ponto.payload or {}).get("filepath")
            if filepath:
                existentes[filepath].add(str(ponto.id))
        if offset is None:
            break
    return existentes


def _apagar_pontos(client: QdrantClient, collection_name: str, ids: Set[str]) -> None:
    if not ids:
        return
    client.delete(
        collection_name=collection_name,
        points_selector=qmodels.PointIdsList(points=list(ids)),
    )


def _arquivo_esta_no_escopo(filepath: str, dir_path_abs: str) -> bool:
    """True se `filepath` está dentro da árvore de `dir_path_abs`.

    Incidente real (Sessão 30): rodar a ingestão numa pasta PARCIAL (ex:
    `ingest_network.py --test`, que indexa só 1 família de produto, contra
    `--full`, o acervo inteiro) apagou a coleção inteira — tudo que não
    estava na pasta parcial foi tratado como "arquivo removido do acervo".
    A reconciliação de "arquivo removido" só pode considerar arquivos que
    ESTAVAM dentro do escopo desta execução; um filepath de fora nunca é
    candidato, não importa se apareceu ou não em `supported` desta vez."""
    try:
        file_abs = os.path.abspath(filepath)
        return os.path.commonpath([dir_path_abs, file_abs]) == dir_path_abs
    except ValueError:
        # Windows: caminhos em drives/raízes UNC diferentes levantam ValueError
        # no commonpath — nunca no mesmo escopo; na dúvida, não apaga.
        return False


def ingest_catalog_directory(dir_path: str, embedding_model: str = EMBEDDING_MODEL):
    """
    Indexa Boletins Técnicos (TDS), Catálogos e Homologações no Qdrant.

    - Idempotente: usa UUID como ID de ponto (não gera duplicatas em execuções repetidas
      para o mesmo conteúdo — IDs são hash determinístico do filepath+chunk_index).
    - Reconciliável (AUD-007, ticket 7): arquivo que encolheu, mudou ou saiu do
      acervo não deixa chunks obsoletos — a diferença entre o que já estava
      indexado por arquivo e o que a versão atual produz é apagada.
    - Suporte a PDF, DOCX e TXT.
    """
    client = get_qdrant_client()
    init_qdrant_collection(client)

    dir_path_abs = os.path.abspath(dir_path)
    existentes_por_arquivo = {
        filepath: ids
        for filepath, ids in _pontos_existentes_por_arquivo(client, COLLECTION_NAME).items()
        if _arquivo_esta_no_escopo(filepath, dir_path_abs)
    }

    files = glob.glob(os.path.join(dir_path, "**/*.*"), recursive=True)
    supported = [f for f in files if f.lower().endswith((".pdf", ".docx", ".doc", ".txt"))]
    print(f"🚀 Iniciando indexação de {len(supported)} arquivos de produtos (de {len(files)} total)...")

    points = []
    indexed_chunks = 0
    skipped_files = 0
    removed_chunks = 0

    for file_path in supported:
        filename = os.path.basename(file_path)
        # Pontos deste arquivo ficam numa lista própria até o arquivo inteiro
        # terminar com sucesso — uma falha no meio (embedding, extração) não
        # pode deixar chunks parciais gravados enquanto o arquivo é contado
        # como "não indexado" no relatório final (achado novo, ticket 7).
        pontos_do_arquivo = []
        ids_novos: Set[str] = set()
        try:
            raw_text = extract_text_from_file(file_path)
            if not raw_text.strip():
                print(f"⚠️ Arquivo vazio ou sem texto extraível: {filename}")
                skipped_files += 1
                continue

            chunks = chunk_text(raw_text)

            for chunk_idx, chunk in enumerate(chunks):
                # ID determinístico: evita duplicatas se a ingestão for reexecutada
                point_uuid = str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{file_path}::{chunk_idx}"
                ))

                vector = get_embedding(chunk, embedding_model)

                pontos_do_arquivo.append(
                    qmodels.PointStruct(
                        id=point_uuid,
                        vector=vector,
                        payload={
                            "filename": filename,
                            "filepath": file_path,
                            "chunk_index": chunk_idx,
                            "content": chunk,
                            "sensivel": _e_conteudo_sensivel(chunk),
                        }
                    )
                )
                ids_novos.add(point_uuid)

        except Exception as e:
            print(f"⚠️ Erro ao processar '{filename}': {e}")
            skipped_files += 1
            continue

        points.extend(pontos_do_arquivo)
        indexed_chunks += len(pontos_do_arquivo)

        ids_antigos = existentes_por_arquivo.pop(file_path, set())
        obsoletos = ids_antigos - ids_novos
        if obsoletos:
            _apagar_pontos(client, COLLECTION_NAME, obsoletos)
            removed_chunks += len(obsoletos)

        if len(points) >= 100:
            client.upsert(collection_name=COLLECTION_NAME, points=points)
            points = []
            print(f"💾 {indexed_chunks} trechos indexados até agora...")

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)

    # Arquivos que estavam indexados mas não estão mais em `supported`
    # (removidos do disco, movidos, ou extensão deixou de ser suportada) —
    # os pontos restantes em existentes_por_arquivo são todos órfãos.
    for ids_orfaos in existentes_por_arquivo.values():
        _apagar_pontos(client, COLLECTION_NAME, ids_orfaos)
        removed_chunks += len(ids_orfaos)

    print(
        f"🎉 Indexação concluída! "
        f"{indexed_chunks} trechos de {len(supported) - skipped_files} arquivos indexados. "
        f"{skipped_files} arquivo(s) ignorado(s). "
        f"{removed_chunks} chunk(s) obsoleto(s) removido(s)."
    )
