"""Construye data/indice_cd.json desde data/procedimientos/.

  python -m src.rag.indexar                 # embedder local (sin API key)
  python -m src.rag.indexar --embedder gemini
"""
import argparse

from dotenv import load_dotenv

from src.rag.indice import RUTA_INDICE, construir_indice, guardar_indice

if __name__ == "__main__":
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", default="hash", choices=["hash", "gemini"])
    a = ap.parse_args()
    idx = construir_indice(a.embedder)
    guardar_indice(idx)
    docs = sorted({c["documento"] for c in idx["chunks"]})
    print(f"{len(idx['chunks'])} fragmentos de {len(docs)} documentos · embedder={idx['embedder']} "
          f"· dimensión={idx['dimension']} → {RUTA_INDICE.relative_to(RUTA_INDICE.parents[1])}")
