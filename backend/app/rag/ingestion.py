import os
import uuid
import glob
from typing import List
from pypdf import PdfReader
import docx
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels
import litellm

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
COLLECTION_NAME = "pu_products_catalog"
# text-embedding-004 (Google/Gemini) = 768 dims | text-embedding-3-small (OpenAI) = 1536 dims
VECTOR_SIZE = int(os.getenv("VECTOR_SIZE", "768"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini/text-embedding-004")

def get_qdrant_client() -> QdrantClient:
    """Cria e retorna um cliente Qdrant (lazy, evita falha no import-time)."""
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10)

def init_qdrant_collection(client: QdrantClient):
    """Garante que a coleção de produtos e TDS exista no Qdrant."""
    try:
        collections = client.get_collections().collections
        if not any(c.name == COLLECTION_NAME for c in collections):
            client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=qmodels.VectorParams(
                    size=VECTOR_SIZE,
                    distance=qmodels.Distance.COSINE
                )
            )
            print(f"✅ Coleção '{COLLECTION_NAME}' inicializada.")
        else:
            print(f"ℹ️ Coleção '{COLLECTION_NAME}' já existe — ingestão incremental.")
    except Exception as e:
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

def ingest_catalog_directory(dir_path: str, embedding_model: str = EMBEDDING_MODEL):
    """
    Indexa Boletins Técnicos (TDS), Catálogos e Homologações no Qdrant.

    - Idempotente: usa UUID como ID de ponto (não gera duplicatas em execuções repetidas
      para o mesmo conteúdo — IDs são hash determinístico do filepath+chunk_index).
    - Suporte a PDF, DOCX e TXT.
    """
    client = get_qdrant_client()
    init_qdrant_collection(client)

    files = glob.glob(os.path.join(dir_path, "**/*.*"), recursive=True)
    supported = [f for f in files if f.lower().endswith((".pdf", ".docx", ".doc", ".txt"))]
    print(f"🚀 Iniciando indexação de {len(supported)} arquivos de produtos (de {len(files)} total)...")

    points = []
    indexed_chunks = 0
    skipped_files = 0

    for file_path in supported:
        filename = os.path.basename(file_path)
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

                emb_res = litellm.embedding(model=embedding_model, input=[chunk])
                vector = emb_res.data[0]["embedding"]

                points.append(
                    qmodels.PointStruct(
                        id=point_uuid,
                        vector=vector,
                        payload={
                            "filename": filename,
                            "filepath": file_path,
                            "chunk_index": chunk_idx,
                            "content": chunk
                        }
                    )
                )
                indexed_chunks += 1

                if len(points) >= 100:
                    client.upsert(collection_name=COLLECTION_NAME, points=points)
                    points = []
                    print(f"💾 {indexed_chunks} trechos indexados até agora...")

        except Exception as e:
            print(f"⚠️ Erro ao processar '{filename}': {e}")
            skipped_files += 1

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)

    print(
        f"🎉 Indexação concluída! "
        f"{indexed_chunks} trechos de {len(supported) - skipped_files} arquivos indexados. "
        f"{skipped_files} arquivo(s) ignorado(s)."
    )
