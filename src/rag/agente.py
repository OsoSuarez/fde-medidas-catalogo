"""
Asistente RAG del CD.

Flujo: validar la pregunta → recuperar fragmentos (búsqueda híbrida filtrada por rol) → armar el prompt
desde plantillas → llamar al LLM → validar contra RespuestaModelo y verificar que las fuentes citadas
existan en el contexto (grounding) → un reintento con el error → resultado con tokens, costo y latencia.

  python -m src.rag.agente --rol operario --pregunta "¿A qué temperatura recibo congelados?"
"""
import argparse
import json
import os
import re
import unicodedata
import sys
import time
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from src.proveedores import ErrorProveedor, ProveedorLLM, RespuestaLLM, crear_proveedor
from src.rag.contrato import Fuente, Metricas, Pregunta, RespuestaModelo, Resultado, Rol
from src.rag.indice import Indice

RAIZ = Path(__file__).resolve().parents[2]
PROMPTS = RAIZ / "prompts"
MAX_INTENTOS = 2
PRECIOS_MTOK = {"gemini-3.1-flash-lite": (0.25, 1.50), "gemini-3.5-flash-lite": (0.30, 2.50),
                "claude-haiku-5-5": (0.10, 0.50), "mock": (0.0, 0.0)}


def _costo(modelo: str, tin: int, tout: int) -> float:
    p_in, p_out = PRECIOS_MTOK.get(modelo, (0.0, 0.0))
    return round(tin / 1e6 * p_in + tout / 1e6 * p_out, 8)


def _extraer_json(texto: str) -> dict:
    t = texto.strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return json.loads(t)


TEMAS_ROL = {
    "operario": "medidas y peso del catálogo, packs, medición física, recepción, frío, transferencias y despacho",
    "supervisor": "medidas y peso, altas de SKU, recepción, frío, transferencias, despacho y mermas",
    "gerencia": "medidas y altas de SKU, toda la operación del CD, mermas, costos del operador y condiciones comerciales",
}
SALUDOS = re.compile(r"^(hola+|buenas?( tardes| noches| dias)?|buen dia|hey|holi|que tal|como (estas|va|andas)|"
                     r"gracias|muchas gracias|genial|perfecto|ok|dale|chau|adios|nos vemos)( jopi)?$")
AYUDA = re.compile(r"(con )?(que|en que) (me )?(podes|puedes|sabes) (ayudar|hacer)|quien sos|que sabes|que haces|ayuda$")


def _normal(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t)).strip()


def charla_rapida(texto: str, rol: str):
    """Saludos y "¿con qué me ayudás?" se responden al instante, sin buscar ni llamar al LLM (0 tokens)."""
    t = _normal(texto)
    temas = TEMAS_ROL.get(rol, "los procedimientos")
    if SALUDOS.match(t):
        if t.startswith(("gracias", "muchas gracias", "genial", "perfecto", "ok", "dale")):
            return "¡De nada! Para eso estoy 💛 Si te surge otra duda, acá ando con el mate."
        if t.startswith(("chau", "adios", "nos vemos")):
            return "¡Chau chau! Que las góndolas te acompañen 👋"
        return f"¡Holaaa! Acá JOPI 👓 Preguntame lo que necesites sobre {temas}."
    if AYUDA.search(t):
        return (f"¡Me encanta esa pregunta! Te puedo ayudar con {temas}. "
                "Probá con algo como «El proveedor mandó 6x70g, ¿qué peso cargo?» o «¿A qué temperatura recibo congelados?».")
    return None


class MockRAG(ProveedorLLM):
    """Sin API key: responde con el primer fragmento del contexto (para pruebas y para el deploy sin clave)."""
    nombre = "mock"

    def generar(self, sistema, mensajes, esquema=None):
        prompt = mensajes[0]["content"]
        if "[C1]" not in prompt:
            r = {"respuesta": "No encontré esto en los procedimientos.", "fuentes": [], "confianza": "baja",
                 "derivar_a_humano": True, "motivo_derivacion": "Sin fragmentos relevantes: consultar al supervisor."}
        else:
            primero = prompt.split("[C1]", 1)[1].split("[C2]", 1)[0].strip()
            r = {"respuesta": "¡JOPI en modo demo (sin LLM)! Según el procedimiento: " + primero[:600],
                 "fuentes": ["C1"], "confianza": "media", "derivar_a_humano": False, "motivo_derivacion": None}
        return RespuestaLLM(json.dumps(r, ensure_ascii=False), len(prompt) // 4, 120)


class Asistente:
    def __init__(self, indice: Optional[Indice] = None, proveedor: Optional[ProveedorLLM] = None, k: int = 4):
        self.indice = indice or Indice.cargar()
        if proveedor is None:
            nombre = os.getenv("PROVEEDOR", "gemini")
            proveedor = MockRAG("mock") if nombre == "mock" else crear_proveedor(
                nombre, os.getenv("MODELO", "gemini-3.1-flash-lite"))
            proveedor.max_tokens = 600
        self.proveedor = proveedor
        self.k = k
        self.sistema = (PROMPTS / "rag_sistema.md").read_text(encoding="utf-8")
        self.plantilla = (PROMPTS / "rag_usuario.md").read_text(encoding="utf-8")
        self.correccion = (PROMPTS / "rag_correccion.md").read_text(encoding="utf-8")

    def responder(self, rol: str, texto: str) -> Resultado:
        inicio = time.perf_counter()
        try:
            p = Pregunta(rol=rol, texto=texto)
        except ValidationError as e:
            campos = [".".join(map(str, x["loc"])) for x in e.errors(include_input=False)]
            return Resultado(ok=False, error={"codigo": "ENTRADA_INVALIDA", "campos": campos})

        ms = lambda: round((time.perf_counter() - inicio) * 1000)
        rapida = charla_rapida(p.texto, p.rol.value)
        if rapida:
            return Resultado(ok=True, respuesta=rapida, confianza="alta", metricas=Metricas(
                modelo="sin LLM", intentos=0, input_tokens=0, output_tokens=0, costo_usd=0,
                latencia_ms=ms(), fragmentos_recuperados=0))

        encontrados = self.indice.buscar(p.texto, p.rol, self.k)
        ids = {f"C{n}": e.chunk for n, e in enumerate(encontrados, 1)}

        if not encontrados:  # el rol no ve ningún documento: no se gasta un token
            return Resultado(ok=True, respuesta="No tengo procedimientos disponibles para tu rol sobre este tema.",
                             derivar_a_humano=True, motivo_derivacion="Sin documentos visibles para el rol.",
                             confianza="baja", metricas=Metricas(modelo=self.proveedor.modelo, intentos=0,
                             input_tokens=0, output_tokens=0, costo_usd=0, latencia_ms=ms(), fragmentos_recuperados=0))

        contexto = "\n\n".join(f"[{cid}] {c.texto}" for cid, c in ids.items())
        mensajes = [{"role": "user", "content": self.plantilla.format(rol=p.rol.value, contexto=contexto, pregunta=p.texto)}]
        tin = tout = 0
        error = None
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                r = self.proveedor.generar(self.sistema, mensajes, esquema=RespuestaModelo)
            except ErrorProveedor as e:
                return Resultado(ok=False, error={"codigo": e.codigo, "mensaje": e.mensaje})
            tin, tout = tin + r.input_tokens, tout + r.output_tokens
            try:
                salida = RespuestaModelo.model_validate(_extraer_json(r.texto))
                inventadas = [f for f in salida.fuentes if f not in ids]
                if inventadas:
                    raise ValueError(f"fuentes que no están en el contexto: {inventadas}")
            except (ValidationError, ValueError) as e:  # json.JSONDecodeError es ValueError
                error = str(e)[:500]
                mensajes = mensajes + [{"role": "assistant", "content": r.texto},
                                       {"role": "user", "content": self.correccion.format(errores=error)}]
                continue
            fuentes = []
            for f in dict.fromkeys(salida.fuentes):
                c = ids[f]
                fuentes.append(Fuente(documento=c.documento, titulo=c.titulo, seccion=c.seccion))
            return Resultado(ok=True, respuesta=salida.respuesta, fuentes=fuentes, confianza=salida.confianza,
                             derivar_a_humano=salida.derivar_a_humano, motivo_derivacion=salida.motivo_derivacion,
                             metricas=Metricas(modelo=self.proveedor.modelo, intentos=intento, input_tokens=tin,
                                               output_tokens=tout, costo_usd=_costo(self.proveedor.modelo, tin, tout),
                                               latencia_ms=ms(), fragmentos_recuperados=len(encontrados)))
        return Resultado(ok=False, error={"codigo": "SALIDA_INVALIDA", "mensaje": error},
                         metricas=Metricas(modelo=self.proveedor.modelo, intentos=MAX_INTENTOS, input_tokens=tin,
                                           output_tokens=tout, costo_usd=_costo(self.proveedor.modelo, tin, tout),
                                           latencia_ms=ms(), fragmentos_recuperados=len(encontrados)))


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Asistente de procedimientos del CD (RAG)")
    ap.add_argument("--rol", required=True, choices=[r.value for r in Rol])
    ap.add_argument("--pregunta", required=True)
    a = ap.parse_args()
    res = Asistente().responder(a.rol, a.pregunta)
    print(json.dumps(res.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
