"""
Chunking estructural: corta cada procedimiento por sus secciones (##) y, si una sección es larga,
por párrafos con solapamiento de una línea. A cada fragmento se le antepone "Documento › Sección"
(Contextual Retrieval) para que el vector sepa de qué habla aunque el fragmento sea corto.
"""
import hashlib
import re
from pathlib import Path
from typing import Dict, List, Tuple

from src.rag.contrato import Chunk

MAX_CARACTERES = 700


def leer_front_matter(texto: str) -> Tuple[Dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n", texto, re.S)
    if not m:
        raise ValueError("el procedimiento no tiene front matter (titulo, acceso)")
    meta = dict(linea.split(":", 1) for linea in m.group(1).splitlines() if ":" in linea)
    return {k.strip(): v.strip() for k, v in meta.items()}, texto[m.end():]


def _partir_largo(cuerpo: str) -> List[str]:
    """Si la sección supera MAX_CARACTERES, agrupa líneas y repite la última (overlap) en el siguiente trozo."""
    if len(cuerpo) <= MAX_CARACTERES:
        return [cuerpo]
    lineas, trozos, actual = cuerpo.splitlines(), [], []
    for linea in lineas:
        if actual and len("\n".join(actual + [linea])) > MAX_CARACTERES:
            trozos.append("\n".join(actual))
            actual = [actual[-1]]  # solapamiento: la última línea pasa al trozo siguiente
        actual.append(linea)
    if actual:
        trozos.append("\n".join(actual))
    return trozos


def trocear_documento(ruta: Path) -> List[Chunk]:
    crudo = ruta.read_text(encoding="utf-8")
    meta, cuerpo = leer_front_matter(crudo)
    titulo, acceso = meta["titulo"], meta["acceso"]
    hash_doc = hashlib.sha256(crudo.encode()).hexdigest()[:16]

    secciones = re.split(r"^## ", cuerpo, flags=re.M)
    intro = re.sub(r"^# .*\n", "", secciones[0]).strip()
    bloques = ([("Introducción", intro)] if intro else []) + [
        (s.split("\n", 1)[0].strip(), s.split("\n", 1)[1].strip() if "\n" in s else "")
        for s in secciones[1:]
    ]

    chunks: List[Chunk] = []
    for seccion, texto in bloques:
        for trozo in _partir_largo(texto):
            chunks.append(Chunk(
                id=f"{ruta.stem}#{len(chunks):02d}",
                documento=ruta.stem, titulo=titulo, seccion=seccion, acceso=acceso,
                texto=f"{titulo} › {seccion}\n{trozo}", hash_documento=hash_doc,
            ))
    return chunks


def trocear_carpeta(carpeta: Path) -> List[Chunk]:
    return [c for ruta in sorted(carpeta.glob("*.md")) if not ruta.name.startswith("_")
            for c in trocear_documento(ruta)]
