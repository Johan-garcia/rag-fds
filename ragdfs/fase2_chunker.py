"""
Fase 2 — Chunking estratégico — SIKA FDS
============================================================
Estrategia: sección GHS como unidad semántica base.
  - Secciones cortas (< MAX_CHARS)  → 1 chunk directo.
  - Secciones largas                → split por subsección (###).
  - Subsecciones aún largas         → ventana deslizante con overlap.

Salida: output/chunks/all_chunks.json  +  un JSON por documento.
"""

import hashlib
import json
import re
from pathlib import Path

MD_DIR    = Path("output/md")
OUT_CHUNK = Path("output/chunks")

# Umbrales de tamaño (aprox. 4 chars ≈ 1 token en español técnico)
MAX_CHARS     = 2400   # ~600 tokens  → umbral para partir una sección
OVERLAP_CHARS =  400   # ~100 tokens  → overlap en ventana deslizante
MIN_CHARS     =   80   # descartar fragmentos vacíos o muy cortos

SECCIONES_GHS = {
    1:  "Identificación del producto y de la empresa",
    2:  "Identificación de los peligros",
    3:  "Composición/información sobre los componentes",
    4:  "Primeros auxilios",
    5:  "Medidas de lucha contra incendios",
    6:  "Medidas en caso de vertido accidental",
    7:  "Manipulación y almacenamiento",
    8:  "Controles de exposición/protección personal",
    9:  "Propiedades físicas y químicas",
    10: "Estabilidad y reactividad",
    11: "Información toxicológica",
    12: "Información ecológica",
    13: "Consideraciones sobre la eliminación",
    14: "Información sobre el transporte",
    15: "Información sobre la reglamentación",
    16: "Otra información",
}

PATRON_GHS = re.compile(
    r"(?:secci[oó]n\s*)?(\d{1,2})\s*[:\.\-]",
    re.IGNORECASE,
)
PATRON_H2 = re.compile(r"^## (.+)$", re.MULTILINE)
PATRON_H3 = re.compile(r"^### (.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Corrección 1: filtro de chunks sin cuerpo real
# ---------------------------------------------------------------------------

def tiene_contenido_util(content: str, min_body: int = 60) -> bool:
    """
    Retorna False si el chunk es solo un header sin cuerpo significativo.
    Un chunk útil tiene al menos `min_body` chars de texto más allá de
    la primera línea (el encabezado ## o ###).
    """
    lineas = [l for l in content.strip().split("\n") if l.strip()]
    if len(lineas) <= 1:
        return False
    cuerpo = " ".join(lineas[1:]).strip()
    return len(cuerpo) >= min_body


# ---------------------------------------------------------------------------
# Ventana deslizante con overlap
# ---------------------------------------------------------------------------

def sliding_window(text: str, header: str) -> list[str]:
    """Divide text en fragmentos de MAX_CHARS con OVERLAP_CHARS de solape."""
    chunks = []
    start  = 0
    while start < len(text):
        end = start + MAX_CHARS
        fragment = text[start:end]
        if len(fragment.strip()) >= MIN_CHARS:
            chunks.append(f"{header}\n\n{fragment.strip()}" if header else fragment.strip())
        start += MAX_CHARS - OVERLAP_CHARS
    return chunks


# ---------------------------------------------------------------------------
# Parser de secciones en el markdown
# ---------------------------------------------------------------------------

def extraer_secciones(md_text: str) -> list[dict]:
    """
    Divide el markdown por ## headers.
    Retorna lista de bloques con: header, section_number, section_title, content.
    """
    matches = list(PATRON_H2.finditer(md_text))
    if not matches:
        return [{"header": "", "section_number": None,
                 "section_title": "Documento completo", "content": md_text}]

    bloques = []
    sec_actual = None
    sec_titulo = None

    for i, m in enumerate(matches):
        header  = m.group(1).strip()
        inicio  = m.start()
        fin     = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        content = md_text[inicio:fin].strip()

        # ¿Es una sección GHS principal?
        gm = PATRON_GHS.match(header)
        if gm:
            num = int(gm.group(1))
            if 1 <= num <= 16:
                sec_actual = num
                sec_titulo = SECCIONES_GHS.get(num, header)

        bloques.append({
            "header":          header,
            "section_number":  sec_actual,
            "section_title":   sec_titulo,
            "content":         content,
        })

    return bloques


# ---------------------------------------------------------------------------
# Chunker principal por sección
# ---------------------------------------------------------------------------

def chunkear_seccion(bloque: dict, doc_stem: str, chunk_global: list) -> list[dict]:
    """
    Aplica la estrategia de chunking a un bloque/sección.
    Retorna lista de chunks con metadatos.
    """
    content   = bloque["content"]
    sec_num   = bloque["section_number"]
    sec_title = bloque["section_title"] or bloque["header"]
    header    = bloque["header"]

    def make_chunk(text: str, sub: str, strategy: str) -> dict:
        idx = len(chunk_global)
        sec_tag = f"s{sec_num:02d}" if sec_num else "s00"
        return {
            "chunk_id":        f"{doc_stem}_{sec_tag}_c{idx:04d}",
            "source_file":     f"{doc_stem}.pdf",
            "doc_stem":        doc_stem,
            "section_number":  sec_num,
            "section_title":   sec_title,
            "subsection_title": sub,
            "content":         text.strip(),
            "char_count":      len(text.strip()),
            "approx_tokens":   len(text.strip()) // 4,
            "strategy":        strategy,
        }

    nuevos: list[dict] = []

    # ── Caso 1: sección corta → chunk único ───────────────────────────────
    if len(content) <= MAX_CHARS:
        if tiene_contenido_util(content):
            nuevos.append(make_chunk(content, header, "section_complete"))
        return nuevos

    # ── Caso 2: sección larga → partir por subsecciones ### ───────────────
    sub_matches = list(PATRON_H3.finditer(content))

    if sub_matches:
        posiciones = [(m.start(), m.group(1)) for m in sub_matches]
        posiciones.append((len(content), "__END__"))

        # Texto previo al primer ###
        previo = content[: posiciones[0][0]].strip()
        if tiene_contenido_util(previo):
            nuevos.append(make_chunk(previo, header, "section_pre_subsection"))

        for j, (pos, sub_title) in enumerate(posiciones[:-1]):
            sig_pos  = posiciones[j + 1][0]
            sub_text = content[pos:sig_pos].strip()

            if not tiene_contenido_util(sub_text):
                continue

            if len(sub_text) <= MAX_CHARS:
                nuevos.append(make_chunk(sub_text, sub_title, "subsection"))
            else:
                # Subsección aún muy larga → ventana deslizante
                for frag in sliding_window(sub_text, f"### {sub_title}"):
                    nuevos.append(make_chunk(frag, sub_title, "sliding_window"))
    else:
        # ── Caso 3: sin ### → ventana deslizante directo ──────────────────
        for frag in sliding_window(content, f"## {header}"):
            nuevos.append(make_chunk(frag, header, "sliding_window"))

    return nuevos


# ---------------------------------------------------------------------------
# Procesar un documento .md
# ---------------------------------------------------------------------------

def procesar_md(md_path: Path) -> list[dict]:
    doc_stem = md_path.stem
    md_text  = md_path.read_text(encoding="utf-8")

    bloques = extraer_secciones(md_text)
    chunks: list[dict] = []

    for bloque in bloques:
        nuevos = chunkear_seccion(bloque, doc_stem, chunks)
        chunks.extend(nuevos)

    return chunks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    OUT_CHUNK.mkdir(parents=True, exist_ok=True)
    mds = sorted(MD_DIR.glob("*.md"))

    print(f"\n{'='*72}")
    print(f"  Fase 2 — Chunking estratégico  ({len(mds)} documentos)")
    print(f"{'='*72}")
    print(f"  MAX_CHARS={MAX_CHARS} (~{MAX_CHARS//4} tokens)  "
          f"OVERLAP={OVERLAP_CHARS} (~{OVERLAP_CHARS//4} tokens)\n")

    todos: list[dict] = []
    resumen = []

    for md_path in mds:
        chunks = procesar_md(md_path)
        doc_stem = md_path.stem

        # Guardar JSON por documento
        out_doc = OUT_CHUNK / f"{doc_stem}.json"
        out_doc.write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Estadísticas
        estrategias = {}
        for c in chunks:
            estrategias[c["strategy"]] = estrategias.get(c["strategy"], 0) + 1
        sizes   = [c["char_count"] for c in chunks]
        avg_sz  = sum(sizes) // len(sizes) if sizes else 0
        max_sz  = max(sizes) if sizes else 0

        nombre = doc_stem[:44]
        print(
            f"  ► {nombre:<46}  "
            f"chunks:{len(chunks):>3}  "
            f"avg:{avg_sz:>4}c  "
            f"max:{max_sz:>5}c"
        )

        todos.extend(chunks)
        resumen.append({
            "doc": doc_stem,
            "total_chunks": len(chunks),
            "avg_chars": avg_sz,
            "estrategias": estrategias,
        })

    # ── Corrección 2: deduplicación por hash de contenido ─────────────────
    # all_chunks_full.json  → todos los chunks (uno por doc, para consultas doc-específicas)
    # all_chunks.json       → deduplicado (para indexar en ChromaDB sin ruido)
    (OUT_CHUNK / "all_chunks_full.json").write_text(
        json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    vistos: set[str] = set()
    deduplicados: list[dict] = []
    eliminados = 0
    for c in todos:
        h = hashlib.md5(c["content"].encode()).hexdigest()
        if h not in vistos:
            vistos.add(h)
            deduplicados.append(c)
        else:
            eliminados += 1

    (OUT_CHUNK / "all_chunks.json").write_text(
        json.dumps(deduplicados, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Resumen de estrategias globales (sobre deduplicados)
    global_strat: dict[str, int] = {}
    for c in deduplicados:
        global_strat[c["strategy"]] = global_strat.get(c["strategy"], 0) + 1

    sizes_all = [c["char_count"] for c in deduplicados]

    print(f"\n{'='*72}")
    print(f"  RESUMEN GLOBAL")
    print(f"{'='*72}")
    print(f"  Chunks brutos         : {len(todos)}")
    print(f"  Filtrados (sin cuerpo): implícito en conteo por doc")
    print(f"  Eliminados por dup    : {eliminados}")
    print(f"  Chunks finales        : {len(deduplicados)}  → all_chunks.json")
    print(f"  Tamaño promedio       : {sum(sizes_all)//len(sizes_all)} chars  "
          f"(~{sum(sizes_all)//(len(sizes_all)*4)} tokens)")
    print(f"  Tamaño mínimo         : {min(sizes_all)} chars")
    print(f"  Tamaño máximo         : {max(sizes_all)} chars")
    print(f"\n  Estrategias aplicadas (chunks finales):")
    for strat, count in sorted(global_strat.items(), key=lambda x: -x[1]):
        print(f"    {strat:<30} : {count}")
    print(f"\n  Salida → {OUT_CHUNK.resolve()}")
    print(f"{'='*72}\n")

    # Mostrar ejemplo de chunk
    ejemplo = next((c for c in todos if c["section_number"] == 9), todos[5])
    print("  EJEMPLO DE CHUNK (Sección 9 — Propiedades físicas):")
    print(f"  {'─'*68}")
    for k, v in ejemplo.items():
        if k == "content":
            preview = v[:200].replace("\n", " ")
            print(f"  {k:<18}: {preview}...")
        else:
            print(f"  {k:<18}: {v}")
    print(f"  {'─'*68}\n")


if __name__ == "__main__":
    main()
