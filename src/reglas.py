"""
Reglas deterministas que corren DESPUÉS de validar la salida del modelo.

El formato correcto no garantiza que el contenido sea verdadero: en la medición de la PE2
el modelo validó el 100% de las veces pero acertó el estado en el 60%. Las dos fallas
(pack marcado "ok" y peso sin unidad dado por "convertido") se detectan con código,
sin depender del LLM:

  R1. Pack "N x M g/kg" en el texto → el peso tiene que ser N × M (en kg) y el estado,
      "convertido". Si el modelo puso otro peso, se corrige con el cálculo.
  R2. Peso sin unidad, o con palabras de incertidumbre ("aprox", "varía") → nunca se
      acepta como "ok"/"convertido": pasa a "revisar" con confianza baja.
"""
import re
from typing import List, Optional, Tuple

from src.contrato import Confianza, Estado, Salida

# "6x70g", "6 x 70 g", "3×96 Gr", "pack x 4 de 1,5 kg" no (solo NxM explícito)
PATRON_PACK = re.compile(
    r"(?<![\d.,])(\d{1,3})\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(kg|kilos?|g|gr|grs|gramos)\b", re.I)
# Algún número seguido de unidad de peso
PATRON_PESO_CON_UNIDAD = re.compile(r"\d+(?:[.,]\d+)?\s*(kg|kilos?|g|gr|grs|gramos)\b", re.I)
PATRON_INCERTIDUMBRE = re.compile(r"\b(aprox\w*|aproximad\w*|var[ií]a\w*|variable|estimad\w*|alrededor)\b", re.I)

A_KG = {"kg": 1.0, "kilo": 1.0, "kilos": 1.0, "g": 0.001, "gr": 0.001, "grs": 0.001, "gramos": 0.001}


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def peso_pack_kg(texto: str) -> Optional[float]:
    m = PATRON_PACK.search(texto)
    if not m:
        return None
    n, valor, unidad = int(m.group(1)), _num(m.group(2)), m.group(3).lower()
    return round(n * valor * A_KG[unidad], 4)


def aplicar_reglas(salida: Salida, texto_proveedor: str) -> Tuple[Salida, List[str]]:
    """Devuelve la salida corregida y la lista de reglas que se aplicaron (para el log)."""
    aplicadas: List[str] = []
    cambios = {}

    # R1: packs
    total = peso_pack_kg(texto_proveedor)
    if total is not None and salida.estado in (Estado.ok, Estado.convertido):
        if salida.peso_kg is None or abs(salida.peso_kg - total) / total > 0.02:
            cambios["peso_kg"] = total
        if salida.estado == Estado.ok:
            cambios["estado"] = Estado.convertido
        if cambios:
            aplicadas.append(f"R1_pack: peso del pack = {total} kg, estado convertido")

    # R2: peso sin unidad o dato aproximado
    estado_final = cambios.get("estado", salida.estado)
    sin_unidad = total is None and not PATRON_PESO_CON_UNIDAD.search(texto_proveedor)
    incierto = bool(PATRON_INCERTIDUMBRE.search(texto_proveedor))
    if estado_final in (Estado.ok, Estado.convertido) and (sin_unidad or incierto):
        cambios["estado"] = Estado.revisar
        cambios["confianza"] = Confianza.baja
        motivo = "peso sin unidad" if sin_unidad else "dato aproximado o variable"
        aplicadas.append(f"R2_revisar: {motivo}")

    if not cambios:
        return salida, aplicadas
    nota = " [Reglas: " + "; ".join(aplicadas) + "]"
    cambios["motivo"] = (salida.motivo + nota)[:400]
    # model_validate (no model_copy) para que el contrato se vuelva a verificar
    return Salida.model_validate({**salida.model_dump(), **cambios}), aplicadas
