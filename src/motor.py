"""
Motor LLM base: recibe un alta de producto, le pide al modelo las medidas y el peso
normalizados a cm/kg, valida la respuesta contra el contrato y devuelve el resultado
con lo que costó y lo que tardó.

Flujo:
  1. Validar la Entrada (si está mal, error ENTRADA_INVALIDA sin gastar tokens).
  2. Armar el prompt desde las plantillas de /prompts.
  3. Llamar al modelo y validar la respuesta contra Salida.
     Si no valida: registrar QUÉ campo falló, devolverle el error al modelo y reintentar UNA vez.
     Si vuelve a fallar: error estructurado SALIDA_INVALIDA.
  4. Reglas deterministas (src/reglas.py): packs N x M, peso sin unidad o aproximado → "revisar";
     y peso fuera del rango de la subcategoría → "revisar".
  5. Medir tokens (campo usage), costo en USD y latencia en ms.

Uso:
  python -m src.motor --sku SKU-10234 --descripcion "Galletitas" --subcategoria snacks \
      --texto "Caja 120x80x50 mm, 350 grs"
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import ValidationError

from src.contrato import (Confianza, Entrada, ErrorMotor, Estado, Metricas, RANGO_PESO_KG,
                          Resultado, Salida)
from src.proveedores import ErrorProveedor, ProveedorLLM, crear_proveedor
from src.reglas import aplicar_reglas

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("motor")

RAIZ = Path(__file__).resolve().parent.parent
PROMPTS = RAIZ / "prompts"

# Precios publicados en USD por millón de tokens (entrada, salida), consultados el 26/10/2026:
#   Gemini: https://ai.google.dev/gemini-api/docs/pricing (tier pago; el tier gratis no cobra)
#   Claude: https://platform.claude.com/docs/en/about-claude/pricing
# Para otro modelo, definir PRECIO_INPUT_MTOK y PRECIO_OUTPUT_MTOK en .env.
PRECIOS_MTOK = {
    "gemini-3.5-flash-lite": (0.30, 2.50),  # modelo usado en la clase
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts de hasta 100.000 tokens
    "mock": (0.0, 0.0),
}

MAX_INTENTOS = 2  # 1 intento + 1 reintento


def cargar_plantilla(nombre: str) -> str:
    return (PROMPTS / nombre).read_text(encoding="utf-8")


def precio_mtok(modelo: str) -> tuple:
    if os.getenv("PRECIO_INPUT_MTOK") and os.getenv("PRECIO_OUTPUT_MTOK"):
        return float(os.environ["PRECIO_INPUT_MTOK"]), float(os.environ["PRECIO_OUTPUT_MTOK"])
    if modelo not in PRECIOS_MTOK:
        raise ValueError(f"Sin precio para {modelo}: definir PRECIO_INPUT_MTOK y PRECIO_OUTPUT_MTOK")
    return PRECIOS_MTOK[modelo]


def calcular_costo(modelo: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = precio_mtok(modelo)
    return round(input_tokens / 1_000_000 * p_in + output_tokens / 1_000_000 * p_out, 8)


def extraer_json(texto: str) -> dict:
    """Acepta el JSON solo o envuelto en ```json ... ```."""
    limpio = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip())
    return json.loads(limpio)


def campos_con_error(e: ValidationError) -> List[str]:
    return [".".join(str(p) for p in err["loc"]) or "(objeto)" for err in e.errors(include_input=False)]


def describir_errores(e: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(str(p) for p in err['loc']) or '(objeto)'}: {err['msg']}" for err in e.errors(include_input=False)
    )


class Motor:
    def __init__(self, proveedor: Optional[ProveedorLLM] = None):
        self.proveedor = proveedor or crear_proveedor(
            os.getenv("PROVEEDOR", "gemini"), os.getenv("MODELO", "gemini-3.1-flash-lite")
        )
        self.sistema = cargar_plantilla("sistema.md")
        self.plantilla_usuario = cargar_plantilla("usuario.md")
        self.plantilla_correccion = cargar_plantilla("correccion.md")

    def procesar(self, datos: dict) -> Resultado:
        # 1. Entrada
        try:
            entrada = Entrada.model_validate(datos)
        except ValidationError as e:
            campos = campos_con_error(e)
            logger.warning("Entrada inválida | campos=%s", campos)
            return Resultado(ok=False, error=ErrorMotor(
                codigo="ENTRADA_INVALIDA", mensaje=describir_errores(e), campos=campos))

        # 2. Prompt desde plantilla
        mensajes = [{"role": "user", "content": self.plantilla_usuario.format(
            sku_id=entrada.sku_id, descripcion=entrada.descripcion,
            subcategoria=entrada.subcategoria.value, texto_proveedor=entrada.texto_proveedor)}]

        tok_in = tok_out = 0
        inicio = time.perf_counter()
        ultimo_error: Optional[ErrorMotor] = None

        def metricas(intentos: int) -> Metricas:
            return Metricas(
                modelo=self.proveedor.modelo, intentos=intentos,
                input_tokens=tok_in, output_tokens=tok_out,
                costo_usd=calcular_costo(self.proveedor.modelo, tok_in, tok_out),
                latencia_ms=round((time.perf_counter() - inicio) * 1000))

        # 3. Llamada + validación + un reintento
        for intento in range(1, MAX_INTENTOS + 1):
            try:
                resp = self.proveedor.generar(self.sistema, mensajes, esquema=Salida)
            except ErrorProveedor as e:
                logger.error("Error de API (%s): %s", e.codigo, e.mensaje)
                return Resultado(ok=False, error=ErrorMotor(codigo=e.codigo, mensaje=e.mensaje),
                                 metricas=metricas(intento))
            tok_in += resp.input_tokens
            tok_out += resp.output_tokens

            try:
                salida = Salida.model_validate(extraer_json(resp.texto))
                if salida.sku_id != entrada.sku_id:
                    raise ValueError(f"sku_id devuelto ({salida.sku_id}) distinto del de entrada")
            except json.JSONDecodeError as e:
                ultimo_error = ErrorMotor(codigo="SALIDA_INVALIDA", mensaje=f"La respuesta no es JSON: {e}",
                                          campos=["(json)"])
                errores_txt = f"- (json): la respuesta no es un JSON válido ({e})"
            except ValidationError as e:
                ultimo_error = ErrorMotor(codigo="SALIDA_INVALIDA", mensaje=describir_errores(e),
                                          campos=campos_con_error(e))
                errores_txt = describir_errores(e)
            except ValueError as e:
                ultimo_error = ErrorMotor(codigo="SALIDA_INVALIDA", mensaje=str(e), campos=["sku_id"])
                errores_txt = f"- sku_id: {e}"
            else:
                # 4. Regla de negocio: peso fuera del rango de la subcategoría → medición física
                # 4a. Reglas deterministas (packs, peso sin unidad) — no dependen del LLM
                salida, reglas = aplicar_reglas(salida, entrada.texto_proveedor)
                if reglas:
                    logger.info("%s: reglas aplicadas %s", salida.sku_id, reglas)
                salida = self._aplicar_rango(salida, entrada)
                logger.info("OK %s | estado=%s | intentos=%d", salida.sku_id, salida.estado.value, intento)
                return Resultado(ok=True, salida=salida, metricas=metricas(intento))

            logger.warning("Salida inválida (intento %d/%d) | campos=%s",
                           intento, MAX_INTENTOS, ultimo_error.campos)
            if intento < MAX_INTENTOS:
                # Autocorrección: se le devuelve al modelo el error estructurado
                mensajes = mensajes + [
                    {"role": "assistant", "content": resp.texto},
                    {"role": "user", "content": self.plantilla_correccion.format(errores=errores_txt)},
                ]

        logger.error("Salida inválida tras %d intentos | campos=%s", MAX_INTENTOS, ultimo_error.campos)
        return Resultado(ok=False, error=ultimo_error, metricas=metricas(MAX_INTENTOS))

    @staticmethod
    def _aplicar_rango(salida: Salida, entrada: Entrada) -> Salida:
        if salida.peso_kg is None:
            return salida
        minimo, maximo = RANGO_PESO_KG[entrada.subcategoria]
        if minimo <= salida.peso_kg <= maximo:
            return salida
        logger.warning("%s: peso %.3f kg fuera del rango de %s (%s–%s kg) → revisar",
                       salida.sku_id, salida.peso_kg, entrada.subcategoria.value, minimo, maximo)
        return salida.model_copy(update={
            "estado": Estado.revisar,
            "confianza": Confianza.baja if salida.confianza == Confianza.baja else Confianza.media,
            "motivo": (salida.motivo + f" Peso fuera del rango esperado para {entrada.subcategoria.value} "
                       f"({minimo}–{maximo} kg): medir físicamente.")[:400],
        })


def main() -> int:
    ap = argparse.ArgumentParser(description="Motor LLM: normaliza medidas y peso de un alta de producto.")
    ap.add_argument("--sku", required=True)
    ap.add_argument("--descripcion", required=True)
    ap.add_argument("--subcategoria", required=True)
    ap.add_argument("--texto", required=True, help="Texto crudo del proveedor")
    a = ap.parse_args()

    resultado = Motor().procesar({"sku_id": a.sku, "descripcion": a.descripcion,
                                  "subcategoria": a.subcategoria, "texto_proveedor": a.texto})
    print(json.dumps(resultado.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0 if resultado.ok else 1


if __name__ == "__main__":
    sys.exit(main())
