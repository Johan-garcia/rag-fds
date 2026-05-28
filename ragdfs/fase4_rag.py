"""
Fase 4 — RAG Pipeline — SIKA FDS
=================================================
Retrieval-Augmented Generation con:
  - Retrieval : ChromaDB (coseno) + paraphrase-multilingual-MiniLM-L12-v2
  - Generación: Ollama (mistral:latest) — local, sin APIs pagas
  - Trazabilidad: cada respuesta cita los chunks fuente con metadatos completos

Uso:
    python fase4_rag.py                  # ejecuta demo de 5 preguntas
    python fase4_rag.py --interactivo    # modo consulta interactiva
"""

import argparse
import json
import time
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
import ollama

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

CHROMA_PATH = Path("output/chroma_db")
COLLECTION  = "sika_fds"
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
LLM_MODEL   = "mistral"
TOP_K       = 5    # chunks recuperados por consulta

SYSTEM_PROMPT = (
    "Eres un experto en fichas de datos de seguridad (FDS/SDS) de productos SIKA. "
    "Responde en español de forma precisa y técnica, basándote ÚNICAMENTE en el contexto "
    "proporcionado entre etiquetas [Fuente]. "
    "Si la información no está en el contexto, indícalo explícitamente — nunca inventes datos. "
    "Al final de tu respuesta incluye siempre una línea: "
    "'Fuentes: <lista de documentos y secciones citados>.'"
)


# ---------------------------------------------------------------------------
# Inicialización
# ---------------------------------------------------------------------------

def inicializar_coleccion():
    """Carga la colección ChromaDB con el mismo embedding model usado en Fase 3."""
    embed_fn = SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
        device="cpu",
        normalize_embeddings=True,
    )
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return client.get_collection(COLLECTION, embedding_function=embed_fn)


# ---------------------------------------------------------------------------
# Recuperación
# ---------------------------------------------------------------------------

def recuperar_chunks(collection, query: str, n: int = TOP_K) -> list[dict]:
    """Busca los n chunks más similares a la consulta por distancia coseno."""
    results = collection.query(
        query_texts=[query],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )
    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "content":          doc,
            "similarity":       round(1 - dist, 4),   # coseno: 1 - distancia
            "doc_stem":         meta["doc_stem"],
            "source_file":      meta["source_file"],
            "section_number":   meta["section_number"],
            "section_title":    meta["section_title"],
            "subsection_title": meta.get("subsection_title", ""),
            "chunk_id":         meta["chunk_id"],
            "strategy":         meta["strategy"],
            "char_count":       meta["char_count"],
        })
    return chunks


# ---------------------------------------------------------------------------
# Generación
# ---------------------------------------------------------------------------

def construir_contexto(chunks: list[dict]) -> str:
    """Formatea los chunks recuperados como contexto numerado para el LLM."""
    partes = []
    for i, c in enumerate(chunks, 1):
        if c["section_number"]:
            sec_label = f"Sección {c['section_number']} — {c['section_title']}"
        else:
            sec_label = "Sección no determinada"
        partes.append(
            f"[Fuente {i}] Documento: {c['doc_stem']} | {sec_label}\n"
            f"{c['content']}"
        )
    return "\n\n---\n\n".join(partes)


def generar_respuesta(query: str, contexto: str) -> str:
    """Envía el prompt al LLM local (Ollama) y retorna la respuesta."""
    prompt = (
        f"Contexto extraído de las Fichas de Datos de Seguridad SIKA:\n\n"
        f"{contexto}\n\n"
        f"Pregunta: {query}\n\n"
        f"Responde basándote exclusivamente en el contexto anterior."
    )
    response = ollama.chat(
        model=LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
    )
    return response["message"]["content"]


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def consultar(collection, query: str, verbose: bool = True) -> dict:
    """
    Pipeline completo: recuperación → generación → trazabilidad.
    Retorna dict con respuesta, chunks fuente y metadatos de ejecución.
    """
    t0 = time.time()

    # 1. Recuperar chunks relevantes
    chunks = recuperar_chunks(collection, query)

    # 2. Construir contexto y generar respuesta
    contexto  = construir_contexto(chunks)
    respuesta = generar_respuesta(query, contexto)

    elapsed = round(time.time() - t0, 2)

    resultado = {
        "query":              query,
        "respuesta":          respuesta,
        "tiempo_s":           elapsed,
        "modelo_llm":         LLM_MODEL,
        "modelo_embeddings":  EMBED_MODEL,
        "top_k":              TOP_K,
        "chunks_recuperados": chunks,
    }

    if verbose:
        _imprimir_resultado(resultado)

    return resultado


def _imprimir_resultado(r: dict):
    sep  = "=" * 72
    dash = "─" * 72

    print(f"\n{sep}")
    print(f"  PREGUNTA")
    print(f"{dash}")
    print(f"  {r['query']}")
    print(f"\n{sep}")
    print(f"  RESPUESTA  (Mistral vía Ollama)")
    print(f"{dash}")
    for linea in r["respuesta"].split("\n"):
        print(f"  {linea}")

    print(f"\n{dash}")
    print(f"  TRAZABILIDAD — Top {r['top_k']} chunks recuperados")
    print(f"{dash}")
    for i, c in enumerate(r["chunks_recuperados"], 1):
        sec   = f"Sec {c['section_number']:02d}" if c["section_number"] else "Sec ??"
        sim   = c["similarity"]
        strat = c["strategy"][:16]
        src   = c["doc_stem"][:38]
        prev  = c["content"][:110].replace("\n", " ")
        print(f"  [{i}] [{sim:.3f}] {src:<38} | {sec} | {strat}")
        print(f"       {prev}...")

    print(f"\n  Tiempo total: {r['tiempo_s']}s")
    print(f"{sep}\n")


# ---------------------------------------------------------------------------
# Demo con preguntas de referencia
# ---------------------------------------------------------------------------

PREGUNTAS_DEMO = [
    "¿Cuál es el punto de inflamación del Esmalte Epóxico?",
    "¿Qué equipo de protección personal (EPP) se requiere para manipular los productos SIKA?",
    "¿Cómo se debe actuar en caso de derrame accidental de un producto SIKA?",
    "¿Cuáles son los primeros auxilios en caso de ingestión accidental?",
    "¿Cuáles son los riesgos de incendio o explosión y cómo combatirlos?",
]


def ejecutar_demo(collection):
    print(f"\n{'='*72}")
    print(f"  Fase 4 — Demo RAG  ({len(PREGUNTAS_DEMO)} consultas de referencia)")
    print(f"{'='*72}")

    resultados = []
    for i, pregunta in enumerate(PREGUNTAS_DEMO, 1):
        print(f"\n  [{i}/{len(PREGUNTAS_DEMO)}] Procesando...", end=" ", flush=True)
        r = consultar(collection, pregunta, verbose=True)
        resultados.append(r)

    # Persistir resultados para Fase 5 (evaluación)
    out = Path("output/rag_demo_resultados.json")
    out.write_text(
        json.dumps(resultados, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  Resultados guardados → {out.resolve()}\n")
    return resultados


# ---------------------------------------------------------------------------
# Modo interactivo
# ---------------------------------------------------------------------------

def modo_interactivo(collection):
    print(f"\n{'='*72}")
    print(f"  Modo interactivo — RAG SIKA FDS")
    print(f"  LLM: {LLM_MODEL} | Embeddings: {EMBED_MODEL} | Top-K: {TOP_K}")
    print(f"  Escribe 'salir' o 'exit' para terminar.")
    print(f"{'='*72}\n")

    historial = []
    while True:
        try:
            query = input("  Pregunta: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Sesión terminada.")
            break

        if query.lower() in ("salir", "exit", "q", "quit"):
            print("  Sesión terminada.")
            break
        if not query:
            continue

        r = consultar(collection, query, verbose=True)
        historial.append(r)

    if historial:
        out = Path("output/rag_interactivo.json")
        out.write_text(
            json.dumps(historial, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  Historial guardado → {out.resolve()}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fase 4 — RAG Pipeline SIKA FDS")
    parser.add_argument(
        "--interactivo", "-i",
        action="store_true",
        help="Modo consulta interactiva (por defecto: demo con preguntas de referencia)",
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        default=None,
        help="Ejecutar una sola consulta y salir",
    )
    args = parser.parse_args()

    print(f"\n{'='*72}")
    print(f"  Fase 4 — RAG Pipeline — SIKA FDS")
    print(f"{'='*72}")
    print(f"  LLM          : {LLM_MODEL} (Ollama — local)")
    print(f"  Embeddings   : {EMBED_MODEL} (sentence-transformers)")
    print(f"  Vector store : ChromaDB — {CHROMA_PATH.resolve()}")
    print(f"  Top-K chunks : {TOP_K}")

    print("\n  Inicializando vector store...", end=" ", flush=True)
    collection = inicializar_coleccion()
    print(f"OK ({collection.count()} chunks indexados)\n")

    if args.query:
        consultar(collection, args.query, verbose=True)
    elif args.interactivo:
        modo_interactivo(collection)
    else:
        ejecutar_demo(collection)


if __name__ == "__main__":
    main()
