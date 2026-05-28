"""
Fase 3 — Embeddings + Vector Store — SIKA FDS
=======================================================
Modelo de embeddings : paraphrase-multilingual-MiniLM-L12-v2
  → multilingüe (ES/EN), 471 MB, sin servidor externo.
Vector store         : ChromaDB (persiste en disco en output/chroma_db/).
Fuente               : output/chunks/all_chunks.json (351 chunks deduplicados).
"""

import json
import time
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

CHUNKS_PATH = Path("output/chunks/all_chunks.json")
CHROMA_PATH = Path("output/chroma_db")
COLLECTION  = "sika_fds"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

BATCH_SIZE  = 50   # chunks por lote para no saturar memoria


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cargar_chunks() -> list[dict]:
    return json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))


def preparar_lote(chunks: list[dict], offset: int) -> tuple[list, list, list]:
    """
    Devuelve (ids, documentos, metadatas) listos para Chroma.
    Usa id secuencial (sika_{n:05d}) para garantizar unicidad,
    guardando el chunk_id original como campo de metadatos.
    """
    ids, docs, metas = [], [], []
    for i, c in enumerate(chunks):
        ids.append(f"sika_{offset + i:05d}")
        docs.append(c["content"])
        metas.append({
            "chunk_id":         c["chunk_id"],
            "source_file":      c["source_file"],
            "doc_stem":         c["doc_stem"],
            "section_number":   c["section_number"] or 0,
            "section_title":    c["section_title"]  or "",
            "subsection_title": c["subsection_title"] or "",
            "char_count":       c["char_count"],
            "approx_tokens":    c["approx_tokens"],
            "strategy":         c["strategy"],
        })
    return ids, docs, metas


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"\n{'='*70}")
    print(f"  Fase 3 — Indexación en ChromaDB")
    print(f"{'='*70}")

    # ── 1. Cargar chunks ───────────────────────────────────────────────────
    chunks = cargar_chunks()
    print(f"\n  Chunks a indexar : {len(chunks)}")
    print(f"  Modelo embeddings: {EMBED_MODEL}")
    print(f"  Vector store     : {CHROMA_PATH.resolve()}\n")

    # ── 2. Modelo de embeddings ────────────────────────────────────────────
    print("  [1/3] Cargando modelo de embeddings...", end=" ", flush=True)
    t0 = time.time()
    embed_fn = SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
        device="cpu",           # cambiar a "cuda" si hay GPU disponible
        normalize_embeddings=True,
    )
    print(f"OK ({time.time()-t0:.1f}s)")

    # ── 3. Inicializar ChromaDB ────────────────────────────────────────────
    print("  [2/3] Inicializando ChromaDB...", end=" ", flush=True)
    CHROMA_PATH.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    # Borrar colección anterior si existe (para re-indexar limpio)
    existing = [c.name for c in client.list_collections()]
    if COLLECTION in existing:
        client.delete_collection(COLLECTION)
        print("(colección anterior eliminada)", end=" ")

    collection = client.create_collection(
        name=COLLECTION,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},   # distancia coseno para semántica
    )
    print("OK")

    # ── 4. Indexar por lotes ───────────────────────────────────────────────
    print(f"  [3/3] Indexando {len(chunks)} chunks en lotes de {BATCH_SIZE}...")
    t_idx = time.time()
    total_indexados = 0

    for i in range(0, len(chunks), BATCH_SIZE):
        lote = chunks[i : i + BATCH_SIZE]
        ids, docs, metas = preparar_lote(lote, offset=i)
        collection.add(documents=docs, metadatas=metas, ids=ids)
        total_indexados += len(lote)
        pct = total_indexados / len(chunks) * 100
        print(f"    lote {i//BATCH_SIZE+1:>2}  [{total_indexados:>3}/{len(chunks)}]  {pct:.0f}%")

    elapsed = time.time() - t_idx
    print(f"\n  Indexación completada en {elapsed:.1f}s")
    print(f"  Chunks indexados : {collection.count()}")

    # ── 5. Verificación con consulta de prueba ─────────────────────────────
    print(f"\n{'─'*70}")
    print("  VERIFICACIÓN — consultas de prueba")
    print(f"{'─'*70}")

    pruebas = [
        ("¿Cuál es el punto de inflamación del Esmalte Epóxico?",        2),
        ("¿Qué equipo de protección personal se requiere?",               3),
        ("¿Cómo se debe actuar en caso de derrame accidental?",           3),
        ("¿Cuáles son los primeros auxilios en caso de ingestión?",       3),
    ]

    for pregunta, n_results in pruebas:
        resultados = collection.query(
            query_texts=[pregunta],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        print(f"\n  Q: {pregunta}")
        for doc, meta, dist in zip(
            resultados["documents"][0],
            resultados["metadatas"][0],
            resultados["distances"][0],
        ):
            sim = 1 - dist   # distancia coseno → similaridad
            sec = meta["section_number"]
            src = meta["doc_stem"][:35]
            preview = doc[:100].replace("\n", " ")
            print(f"    [{sim:.3f}] Sección {sec} | {src}")
            print(f"           {preview}...")

    # ── 6. Estadísticas finales ────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  ESTADÍSTICAS DE LA COLECCIÓN")
    print(f"{'='*70}")
    print(f"  Nombre colección : {COLLECTION}")
    print(f"  Total documentos : {collection.count()}")
    print(f"  Modelo embeddings: {EMBED_MODEL}")
    print(f"  Dimensión vector : 384  (MiniLM-L12)")
    print(f"  Métrica distancia: coseno")
    print(f"  Persistencia     : {CHROMA_PATH.resolve()}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
