"""
Fase 1 — Pipeline PDF → Markdown — SIKA FDS
Extrae texto estructurado, tablas e imágenes con trazabilidad.
"""

import fitz          # PyMuPDF
import pdfplumber
import re
import io
import hashlib
from pathlib import Path
from urllib.parse import quote
from PIL import Image

PDF_DIR  = Path("Documentos - Parcial final/SIKA")
OUT_MD   = Path("output/md")
OUT_IMGS = Path("output/images")

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

# Detecta "SECCIÓN 1:", "Sección 1.", "1. Título" — número del 1 al 16
PATRON_SECCION = re.compile(
    r"^\s*(?:secci[oó]n\s*)?(\d{1,2})\s*[:\.\-]\s*(.+)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def bbox_overlap(b1, b2, threshold=0.4):
    """True si b1 solapa en al menos threshold de su área con b2."""
    ix0, iy0 = max(b1[0], b2[0]), max(b1[1], b2[1])
    ix1, iy1 = min(b1[2], b2[2]), min(b1[3], b2[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    area = (b1[2] - b1[0]) * (b1[3] - b1[1])
    if area == 0:
        return False
    return ((ix1 - ix0) * (iy1 - iy0)) / area >= threshold


def tabla_a_markdown(rows: list) -> str:
    """Convierte filas de pdfplumber a tabla Markdown."""
    if not rows:
        return ""
    cleaned = [
        [(c or "").replace("\n", " ").strip() for c in row]
        for row in rows
    ]
    if not cleaned:
        return ""
    n_cols = max(len(r) for r in cleaned)
    header = cleaned[0] + [""] * (n_cols - len(cleaned[0]))
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * n_cols) + " |",
    ]
    for row in cleaned[1:]:
        row = row + [""] * (n_cols - len(row))
        lines.append("| " + " | ".join(row[:n_cols]) + " |")
    return "\n".join(lines)


def nivel_encabezado(line_text: str, spans: list) -> int | None:
    """Retorna nivel markdown (2 o 3) si la línea es encabezado, None si no."""
    m = PATRON_SECCION.match(line_text.strip())
    if m:
        num = int(m.group(1))
        if 1 <= num <= 16:
            return 2
    max_size = max((s.get("size", 0) for s in spans), default=0)
    is_bold  = any(
        "Bold" in s.get("font", "") or "bold" in s.get("font", "")
        for s in spans
    )
    if max_size >= 11 and is_bold and len(line_text.strip()) < 120:
        return 3
    return None


# ---------------------------------------------------------------------------
# Procesador principal
# ---------------------------------------------------------------------------

def procesar_pdf(pdf_path: Path) -> tuple[str, dict]:
    doc_stem = pdf_path.stem
    img_dir  = OUT_IMGS / doc_stem
    img_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "paginas": 0,
        "tablas": 0,
        "imagenes": 0,
        "secciones": [],
        "errores": [],
    }

    fitz_doc = fitz.open(str(pdf_path))
    stats["paginas"] = len(fitz_doc)

    partes = []

    # Frontmatter del documento
    partes.append(f"# {doc_stem}\n")
    partes.append(
        f"> **Fuente:** `{pdf_path.name}`  \n"
        f"> **Fabricante:** SIKA  \n"
        f"> **Tipo:** Ficha de Datos de Seguridad (FDS/SDS)  \n"
        f"> **Páginas:** {len(fitz_doc)}\n"
    )

    seen_hashes:  set[str] = set()
    img_global    = 0
    tabla_global  = 0
    seccion_actual: int | None = None

    with pdfplumber.open(str(pdf_path)) as plumber_doc:
        for pg_idx, (fitz_pg, plumber_pg) in enumerate(
            zip(fitz_doc, plumber_doc.pages), start=1
        ):
            # ── Tablas de esta página ──────────────────────────────────────
            found_tables  = plumber_pg.find_tables()
            table_bboxes  = [t.bbox for t in found_tables]
            table_rows    = [t.extract() for t in found_tables]
            tablas_usadas: set[int] = set()

            # ── Bloques de texto (ordenados por posición Y, X) ─────────────
            blocks = fitz_pg.get_text("dict", sort=True).get("blocks", [])

            for block in blocks:
                b_bbox = block.get("bbox", (0, 0, 0, 0))

                # Si el bloque solapa con una tabla → insertar tabla primero
                tabla_insertada = False
                for ti, t_bbox in enumerate(table_bboxes):
                    if ti not in tablas_usadas and bbox_overlap(b_bbox, t_bbox):
                        md_tbl = tabla_a_markdown(table_rows[ti])
                        if md_tbl:
                            tabla_global += 1
                            partes.append(f"\n{md_tbl}\n")
                            stats["tablas"] += 1
                        tablas_usadas.add(ti)
                        tabla_insertada = True
                        break

                if tabla_insertada:
                    continue

                if block.get("type") != 0:   # solo bloques de texto
                    continue

                texto_bloque: list[str] = []

                for line in block.get("lines", []):
                    spans     = line.get("spans", [])
                    line_text = " ".join(s.get("text", "") for s in spans).strip()
                    if not line_text:
                        continue

                    # ¿Es sección GHS?
                    m = PATRON_SECCION.match(line_text)
                    if m:
                        num = int(m.group(1))
                        if 1 <= num <= 16:
                            seccion_actual = num
                            if num not in stats["secciones"]:
                                stats["secciones"].append(num)

                    nivel = nivel_encabezado(line_text, spans)

                    if nivel == 2:
                        if texto_bloque:
                            partes.append("\n".join(texto_bloque))
                            texto_bloque = []
                        partes.append(f"\n## {line_text}\n")
                    elif nivel == 3:
                        if texto_bloque:
                            partes.append("\n".join(texto_bloque))
                            texto_bloque = []
                        partes.append(f"\n### {line_text}\n")
                    else:
                        texto_bloque.append(line_text)

                if texto_bloque:
                    partes.append("\n".join(texto_bloque))

            # Tablas que no solaparon con ningún bloque de texto
            for ti, rows in enumerate(table_rows):
                if ti not in tablas_usadas:
                    md_tbl = tabla_a_markdown(rows)
                    if md_tbl:
                        tabla_global += 1
                        partes.append(f"\n{md_tbl}\n")
                        stats["tablas"] += 1

            # ── Imágenes de esta página ────────────────────────────────────
            for img_info in fitz_pg.get_images(full=True):
                xref = img_info[0]
                try:
                    base   = fitz_doc.extract_image(xref)
                    data   = base["image"]
                    ext    = base["ext"]
                    img_h  = hashlib.md5(data).hexdigest()[:10]

                    if img_h in seen_hashes:
                        continue

                    # Descartar imágenes muy pequeñas (iconos / bullets)
                    pil = Image.open(io.BytesIO(data))
                    w, h = pil.size
                    if w < 60 or h < 60:
                        continue

                    seen_hashes.add(img_h)
                    img_global += 1
                    filename   = f"{doc_stem}_p{pg_idx}_img{img_global}.{ext}"
                    (img_dir / filename).write_bytes(data)

                    sec_ref   = f"Sección {seccion_actual}" if seccion_actual else "sección no determinada"
                    tabla_ref = f"Tabla {tabla_global}"     if tabla_global  else "sin tabla previa"
                    rel_path  = f"../images/{quote(doc_stem)}/{quote(filename)}"

                    partes.append(
                        f"\n![Figura {img_global} — {doc_stem}]({rel_path})\n\n"
                        f"> **Nota de trazabilidad:** Figura {img_global} extraída de la página {pg_idx}. "
                        f"Contexto estructural: {sec_ref}. "
                        f"Referencia tabular más próxima: {tabla_ref}. "
                        f"Dimensiones originales: {w}×{h} px.\n"
                    )
                    stats["imagenes"] += 1

                except Exception as e:
                    stats["errores"].append(f"p{pg_idx} img {xref}: {e}")

    fitz_doc.close()
    stats["secciones"].sort()
    return "\n".join(partes), stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    OUT_MD.mkdir(parents=True, exist_ok=True)
    OUT_IMGS.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    print(f"\n{'='*72}")
    print(f"  Fase 1 — Pipeline PDF → Markdown  ({len(pdfs)} documentos SIKA)")
    print(f"{'='*72}\n")

    resumen = []

    for pdf_path in pdfs:
        nombre = pdf_path.name[:48]
        print(f"  ► {nombre:<50}", end="", flush=True)
        try:
            md_text, stats = procesar_pdf(pdf_path)
            out_path = OUT_MD / (pdf_path.stem + ".md")
            out_path.write_text(md_text, encoding="utf-8")
            secc = f"{len(stats['secciones'])}/16"
            print(
                f"  ✓  {stats['paginas']}p  "
                f"tablas:{stats['tablas']}  "
                f"imgs:{stats['imagenes']}  "
                f"secc:{secc}"
            )
            if stats["errores"]:
                for e in stats["errores"][:2]:
                    print(f"       ⚠ {e}")
            resumen.append({"file": pdf_path.name, **stats})
        except Exception as e:
            print(f"  ✗ ERROR: {e}")

    print(f"\n{'='*72}")
    print(f"  TOTALES")
    print(f"{'='*72}")
    total_tablas = sum(r["tablas"]    for r in resumen)
    total_imgs   = sum(r["imagenes"]  for r in resumen)
    total_secc   = sum(len(r["secciones"]) for r in resumen)
    print(f"  Archivos .md generados : {len(resumen)}")
    print(f"  Tablas extraídas       : {total_tablas}")
    print(f"  Imágenes extraídas     : {total_imgs}")
    print(f"  Secciones detectadas   : {total_secc} / {len(resumen)*16}")
    print(f"\n  Markdown → {OUT_MD.resolve()}")
    print(f"  Imágenes → {OUT_IMGS.resolve()}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
