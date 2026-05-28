"""
Fase 0 — Diagnóstico documental SIKA
Analiza cada PDF: tipo, tablas, imágenes y secciones GHS detectadas.
"""
import fitz          # PyMuPDF
import pdfplumber
import os
import re
from pathlib import Path

PDF_DIR = Path("Documentos - Parcial final/SIKA")

# 16 secciones normativas GHS/SDS
SECCIONES_GHS = {
    1:  "Identificación del producto",
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

# Patrones para detectar cada sección en el texto
PATRONES_SECCION = {
    n: re.compile(
        rf"(?:secci[oó]n|section|^\s*)\s*{n}[\s\.\:\-]",
        re.IGNORECASE | re.MULTILINE
    )
    for n in SECCIONES_GHS
}

def analizar_pdf(pdf_path: Path) -> dict:
    resultado = {
        "archivo": pdf_path.name,
        "paginas": 0,
        "tipo": "desconocido",
        "chars_por_pagina": 0,
        "tiene_tablas": "N",
        "num_tablas": 0,
        "tiene_imagenes": "N",
        "num_imagenes": 0,
        "secciones_detectadas": [],
        "secciones_faltantes": [],
        "total_secciones": 0,
    }

    # --- Análisis con PyMuPDF ---
    doc = fitz.open(str(pdf_path))
    resultado["paginas"] = len(doc)

    texto_total = ""
    total_imagenes = 0

    for page in doc:
        texto_total += page.get_text()
        total_imagenes += len(page.get_images(full=True))

    chars = len(texto_total.strip())
    resultado["chars_por_pagina"] = chars // max(len(doc), 1)

    # Clasificar: si hay poco texto por página → probablemente escaneado
    if resultado["chars_por_pagina"] < 100:
        resultado["tipo"] = "escaneado (imagen)"
    else:
        resultado["tipo"] = "texto seleccionable"

    if total_imagenes > 0:
        resultado["tiene_imagenes"] = "S"
        resultado["num_imagenes"] = total_imagenes

    doc.close()

    # --- Detección de tablas con pdfplumber ---
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            total_tablas = sum(
                len(page.extract_tables()) for page in pdf.pages
            )
            if total_tablas > 0:
                resultado["tiene_tablas"] = "S"
                resultado["num_tablas"] = total_tablas
    except Exception:
        pass

    # --- Detección de las 16 secciones GHS ---
    encontradas = []
    faltantes = []
    for n, patron in PATRONES_SECCION.items():
        if patron.search(texto_total):
            encontradas.append(n)
        else:
            faltantes.append(n)

    resultado["secciones_detectadas"] = encontradas
    resultado["secciones_faltantes"] = faltantes
    resultado["total_secciones"] = len(encontradas)

    return resultado


def main():
    pdfs = sorted(PDF_DIR.glob("*.pdf"))
    if not pdfs:
        print(f"No se encontraron PDFs en {PDF_DIR}")
        return

    resultados = [analizar_pdf(p) for p in pdfs]

    # --- Tabla resumen ---
    print("\n" + "=" * 100)
    print("DIAGNÓSTICO DOCUMENTAL — SIKA (Fase 0)")
    print("=" * 100)

    header = f"{'Archivo':<45} {'Tipo':<25} {'Págs':>4}  {'Tablas':>6}  {'Imgs':>4}  {'Secc GHS':>8}"
    print(header)
    print("-" * 100)

    for r in resultados:
        tablas = f"S ({r['num_tablas']})" if r["tiene_tablas"] == "S" else "N"
        imgs   = f"S ({r['num_imagenes']})" if r["tiene_imagenes"] == "S" else "N"
        secc   = f"{r['total_secciones']}/16"
        print(f"{r['archivo']:<45} {r['tipo']:<25} {r['paginas']:>4}  {tablas:>6}  {imgs:>4}  {secc:>8}")

    # --- Detalle de secciones por documento ---
    print("\n" + "=" * 100)
    print("DETALLE DE SECCIONES GHS DETECTADAS")
    print("=" * 100)
    for r in resultados:
        print(f"\n  {r['archivo']}")
        encontradas_str = ", ".join(str(n) for n in r["secciones_detectadas"]) or "ninguna"
        faltantes_str   = ", ".join(str(n) for n in r["secciones_faltantes"])  or "ninguna"
        print(f"    Detectadas ({r['total_secciones']}): {encontradas_str}")
        if r["secciones_faltantes"]:
            print(f"    Faltantes  ({16 - r['total_secciones']}): {faltantes_str}")

    # --- Resumen global ---
    print("\n" + "=" * 100)
    print("RESUMEN GLOBAL")
    print("=" * 100)
    escaneados = sum(1 for r in resultados if "escaneado" in r["tipo"])
    con_tablas = sum(1 for r in resultados if r["tiene_tablas"] == "S")
    con_imgs   = sum(1 for r in resultados if r["tiene_imagenes"] == "S")
    secc_prom  = sum(r["total_secciones"] for r in resultados) / len(resultados)
    print(f"  Total PDFs analizados : {len(resultados)}")
    print(f"  Texto seleccionable   : {len(resultados) - escaneados}")
    print(f"  Escaneados (imagen)   : {escaneados}")
    print(f"  Con tablas detectadas : {con_tablas}")
    print(f"  Con imágenes          : {con_imgs}")
    print(f"  Secciones GHS (prom.) : {secc_prom:.1f}/16")
    print("=" * 100)


if __name__ == "__main__":
    main()
