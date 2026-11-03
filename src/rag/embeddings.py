"""
Dos embedders con la misma interfaz:
  - "gemini": gemini-embedding-001 (necesita GEMINI_API_KEY / GOOGLE_API_KEY). Mejor calidad.
  - "hash":   local, sin API ni costo (bolsa de palabras con hashing). Sirve para probar y como respaldo.
El índice guarda qué embedder lo construyó: la pregunta se vectoriza siempre con el mismo.
"""
import hashlib
import math
import os
import re
import unicodedata
from typing import List

STOPWORDS = set("""a al algo como con cual de del el en es esta este hay la las le lo los mas me mi no o
para pero por que se si sin su sus un una uno y ya hace hacer tiene tengo cuando donde qué cómo""".split())


def normalizar(texto: str) -> List[str]:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return [p for p in re.findall(r"[a-z0-9]+", t) if p not in STOPWORDS]


def _raiz(p: str) -> str:
    """Raíz muy simple para español (camiones→camion, vencidos→vencid)."""
    for suf in ("aciones", "acion", "ciones", "cion", "es", "os", "as", "s"):
        if len(p) > len(suf) + 3 and p.endswith(suf):
            return p[: -len(suf)]
    return p


def _unitario(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class HashEmbedder:
    nombre = "hash"

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension

    def _vector(self, texto: str) -> List[float]:
        v = [0.0] * self.dimension
        palabras = [_raiz(p) for p in normalizar(texto)]
        rasgos = palabras + [a + "_" + b for a, b in zip(palabras, palabras[1:])]
        for r in rasgos:
            h = int(hashlib.md5(r.encode()).hexdigest(), 16)
            v[h % self.dimension] += 1.0 if (h >> 64) % 2 else -1.0
        return _unitario(v)

    def documentos(self, textos: List[str]) -> List[List[float]]:
        return [self._vector(t) for t in textos]

    def consulta(self, texto: str) -> List[float]:
        return self._vector(texto)


class GeminiEmbedder:
    nombre = "gemini"
    modelo = "gemini-embedding-001"

    def __init__(self, dimension: int = 768):
        from google import genai
        from google.genai import types
        self._types = types
        self.dimension = dimension
        self._cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    def _embed(self, textos: List[str], tarea: str) -> List[List[float]]:
        vectores = []
        for i in range(0, len(textos), 50):  # lotes chicos para el nivel gratuito
            r = self._cliente.models.embed_content(
                model=self.modelo, contents=textos[i:i + 50],
                config=self._types.EmbedContentConfig(task_type=tarea, output_dimensionality=self.dimension))
            vectores += [_unitario(list(e.values)) for e in r.embeddings]
        return vectores

    def documentos(self, textos: List[str]) -> List[List[float]]:
        return self._embed(textos, "RETRIEVAL_DOCUMENT")

    def consulta(self, texto: str) -> List[float]:
        return self._embed([texto], "RETRIEVAL_QUERY")[0]


def crear_embedder(nombre: str):
    if nombre == "hash":
        return HashEmbedder()
    if nombre == "gemini":
        return GeminiEmbedder()
    raise ValueError(f"embedder desconocido: {nombre} (opciones: hash, gemini)")
