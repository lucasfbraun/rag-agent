import os
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

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

def init_qdrant_collection():
    """Garante que a coleção de produtos e TDS exista no Qdrant."""
    collections = client.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=qmodels.VectorParams(
                size=1536,
                distance=qmodels.Distance.COSINE
            )
        )
        print(f"✅ Coleção '{COLLECTION_NAME}' inicializada.")

def extract_text_from_file(filepath: str) -> str:
    """Extrai texto e tabelas técnicas de PDFs e DOCX de produtos."""
    ext = os.path.splitext(filepath)[1].lower()
    text = ""
    if ext == ".pdf":
        reader = PdfReader(filepath)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    elif ext in [".docx", ".doc"]:
        doc = docx.Document(filepath)
        for p in doc.paragraphs:
            text += p.text + "\n"
        for table in doc.tables:
            for row in table.rows:
                text += " | ".join(c.text.strip() for c in row.cells) + "\n"
    return text

def chunk_text(text: str, chunk_size: int = 700, overlap: int = 120) -> List[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = " ".join(words[i:i + chunk_size])
        if len(chunk.strip()) > 40:
            chunks.append(chunk)
    return chunks

def ingest_catalog_directory(dir_path: str):
    """Indexa milhares de Boletins Técnicos (TDS), Catálogos e Homologações."""
    init_qdrant_collection()
    files = glob.glob(os.path.join(dir_path, "**/*.*"), recursive=True)
    print(f"🚀 Iniciando indexação de {len(files)} arquivos de produtos...")

    points = []
    point_id = 1

    for file_path in files:
        if not file_path.lower().endswith(('.pdf', '.docx', '.txt')):
            continue

        filename = os.path.basename(file_path)
        try:
            raw_text = extract_text_from_file(file_path)
            chunks = chunk_text(raw_text)

            for chunk_idx, chunk in enumerate(chunks):
                emb_res = litellm.embedding(model="text-embedding-3-small", input=[chunk])
                vector = emb_res.data[0]['embedding']

                points.append(
                    qmodels.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "filename": filename,
                            "filepath": file_path,
                            "chunk_index": chunk_idx,
                            "content": chunk
                        }
                    )
                )
                point_id += 1

                if len(points) >= 100:
                    client.upsert(collection_name=COLLECTION_NAME, points=points)
                    points = []
                    print(f"💾 {point_id} trechos de catálogo indexados...")

        except Exception as e:
            print(f"⚠️ Erro ao processar {filename}: {e}")

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
    print(f"🎉 Catálogo completo indexado com sucesso!")
