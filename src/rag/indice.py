"""
Índice del asistente: fragmentos + vectores en un JSON (liviano, corre en Vercel) y búsqueda híbrida.

  1. Pre-filtering por rol: se descartan los fragmentos que el rol no puede ver ANTES de buscar.
     La seguridad está en la capa de recuperación, no en el prompt.
  2. Búsqueda vectorial (similitud coseno) + léxica (BM25) en paralelo.
  3. Fusión con Reciprocal Rank Fusion (RRF, k=60): suma 1/(k+posición) de cada ranking.

En Colab (PE3) los mismos fragmentos y vectores se cargan en ChromaDB con `a_chroma()`.
"""
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from src.rag.chunking import trocear_carpeta
from src.rag.contrato import NIVELES_VISIBLES, Chunk, Rol
from src.rag.embeddings import _raiz, crear_embedder, normalizar

RAIZ = Path(__file__).resolve().parents[2]
CARPETA_DOCS = RAIZ / "data" / "procedimientos"
RUTA_INDICE = RAIZ / "data" / "indice_cd.json"
K_RRF = 60


@dataclass
class Encontrado:
    chunk: Chunk
    puntaje: float          # puntaje RRF
    rank_vector: int
    rank_bm25: int


def construir_indice(embedder_nombre: str = "hash", carpeta: Path = CARPETA_DOCS) -> Dict:
    chunks = trocear_carpeta(carpeta)
    embedder = crear_embedder(embedder_nombre)
    vectores = embedder.documentos([c.texto for c in chunks])
    return {"embedder": embedder.nombre, "dimension": len(vectores[0]),
            "chunks": [c.model_dump() for c in chunks], "vectores": vectores}


def guardar_indice(indice: Dict, ruta: Path = RUTA_INDICE) -> None:
    ruta.write_text(json.dumps(indice, ensure_ascii=False), encoding="utf-8")


class BM25:
    def __init__(self, textos: List[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [[_raiz(p) for p in normalizar(t)] for t in textos]
        self.k1, self.b = k1, b
        self.prom = sum(map(len, self.docs)) / max(len(self.docs), 1)
        df = Counter(p for d in self.docs for p in set(d))
        n = len(self.docs)
        self.idf = {p: math.log(1 + (n - f + 0.5) / (f + 0.5)) for p, f in df.items()}

    def puntajes(self, consulta: str) -> List[float]:
        terminos = [_raiz(p) for p in normalizar(consulta)]
        res = []
        for d in self.docs:
            tf, s = Counter(d), 0.0
            for t in terminos:
                if t in tf:
                    s += self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * len(d) / self.prom))
            res.append(s)
        return res


class Indice:
    def __init__(self, datos: Dict):
        self.embedder_nombre = datos["embedder"]
        self.chunks = [Chunk(**c) for c in datos["chunks"]]
        self.vectores = datos["vectores"]
        self._embedder = None

    @classmethod
    def cargar(cls, ruta: Path = RUTA_INDICE) -> "Indice":
        return cls(json.loads(ruta.read_text(encoding="utf-8")))

    @property
    def embedder(self):
        if self._embedder is None:  # mismo embedder que construyó el índice
            self._embedder = crear_embedder(self.embedder_nombre)
        return self._embedder

    def buscar(self, consulta: str, rol: Rol, k: int = 4) -> List[Encontrado]:
        visibles = [i for i, c in enumerate(self.chunks) if c.acceso in NIVELES_VISIBLES[rol]]
        if not visibles:
            return []
        q = self.embedder.consulta(consulta)
        sim = {i: sum(a * b for a, b in zip(q, self.vectores[i])) for i in visibles}
        bm = dict(zip(visibles, BM25([self.chunks[i].texto for i in visibles]).puntajes(consulta)))

        orden_v = sorted(visibles, key=lambda i: -sim[i])
        orden_b = [i for i in sorted(visibles, key=lambda i: -bm[i]) if bm[i] > 0]
        rank_v = {i: r for r, i in enumerate(orden_v, 1)}
        rank_b = {i: r for r, i in enumerate(orden_b, 1)}
        rrf = {i: 1 / (K_RRF + rank_v[i]) + (1 / (K_RRF + rank_b[i]) if i in rank_b else 0) for i in visibles}

        mejores = sorted(visibles, key=lambda i: -rrf[i])[:k]
        return [Encontrado(self.chunks[i], rrf[i], rank_v[i], rank_b.get(i, 0)) for i in mejores]


def a_chroma(indice: Indice, nombre: str = "procedimientos_cd"):
    """Carga los fragmentos y sus vectores en una colección de ChromaDB (en memoria). Para Colab."""
    import chromadb
    col = chromadb.Client().get_or_create_collection(nombre, metadata={"hnsw:space": "cosine"})
    col.add(ids=[c.id for c in indice.chunks], embeddings=indice.vectores,
            documents=[c.texto for c in indice.chunks],
            metadatas=[{"documento": c.documento, "seccion": c.seccion, "acceso": c.acceso,
                        "hash_documento": c.hash_documento} for c in indice.chunks])
    return col


def buscar_chroma(col, indice: Indice, consulta: str, rol: Rol, k: int = 4):
    """Búsqueda vectorial en ChromaDB con pre-filtering por rol (filtro `where` sobre el metadato acceso)."""
    return col.query(query_embeddings=[indice.embedder.consulta(consulta)], n_results=k,
                     where={"acceso": {"$in": sorted(NIVELES_VISIBLES[rol])}})
