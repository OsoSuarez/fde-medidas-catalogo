"""
Contrato de datos del motor (Pydantic).

Sale de la historia de usuario HU1 de la PE1:
  "Como analista de Assortment, quiero que el asistente me proponga las medidas y el peso
   de cada SKU nuevo a partir de la ficha del proveedor, para medir físicamente solo los
   casos dudosos."

Entrada = lo que llega del proveedor (texto crudo, en sus propias unidades).
Salida  = la propuesta normalizada a la unidad estándar del sistema (cm y kg).
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


# --- Valores permitidos (regla 1: Enum) ---
class Subcategoria(str, Enum):
    snacks = "snacks"
    bebidas = "bebidas"
    almacen = "almacen"
    carnes = "carnes"
    lacteos = "lacteos"
    limpieza = "limpieza"
    perfumeria = "perfumeria"


class Estado(str, Enum):
    ok = "ok"                      # el proveedor ya mandó cm y kg
    convertido = "convertido"      # hubo que convertir unidades (mm→cm, g→kg, packs)
    revisar = "revisar"            # faltan datos, son ambiguos o quedan fuera de rango → medición física
    fuera_de_tema = "fuera_de_tema"  # el texto no describe medidas ni peso de un producto


class Confianza(str, Enum):
    alta = "alta"
    media = "media"
    baja = "baja"


class Unidad(str, Enum):
    mm = "mm"
    cm = "cm"
    m = "m"
    g = "g"
    kg = "kg"
    ml = "ml"
    l = "l"
    desconocida = "desconocida"


SKU_PATTERN = r"^SKU-\d{4,8}$"  # regla 2: formato con patrón


class Entrada(BaseModel):
    """Alta que llega del proveedor."""
    sku_id: str = Field(pattern=SKU_PATTERN, description="Código interno, ej. SKU-10234")
    descripcion: str = Field(min_length=3, max_length=200, description="Nombre del producto")
    subcategoria: Subcategoria
    texto_proveedor: str = Field(min_length=5, max_length=2000,
                                 description="Texto crudo del mail o la ficha, tal cual llega")


class Salida(BaseModel):
    """Propuesta normalizada que el motor devuelve (y que después aprueba una persona)."""
    sku_id: str = Field(pattern=SKU_PATTERN)
    estado: Estado
    # regla 3: rangos físicos posibles para un producto de dark store
    largo_cm: Optional[float] = Field(default=None, ge=0.01, le=200)
    alto_cm: Optional[float] = Field(default=None, ge=0.01, le=200)
    ancho_cm: Optional[float] = Field(default=None, ge=0.01, le=200)
    peso_kg: Optional[float] = Field(default=None, ge=0.001, le=50)
    unidades_origen: List[Unidad] = Field(min_length=1, description="Unidades en que vino el dato")
    confianza: Confianza
    # regla 4: largo mínimo, para que el motivo explique algo de verdad
    motivo: str = Field(min_length=15, max_length=400)

    # regla 5: coherencia entre campos (validación cruzada)
    @model_validator(mode="after")
    def coherencia_estado(self) -> "Salida":
        medidas = [self.largo_cm, self.alto_cm, self.ancho_cm, self.peso_kg]
        if self.estado in (Estado.ok, Estado.convertido) and any(v is None for v in medidas):
            raise ValueError(f"estado '{self.estado.value}' exige las 4 medidas (largo, alto, ancho, peso)")
        if self.estado == Estado.fuera_de_tema and any(v is not None for v in medidas):
            raise ValueError("estado 'fuera_de_tema' no puede traer medidas")
        if self.estado == Estado.revisar and self.confianza == Confianza.alta:
            raise ValueError("un SKU a 'revisar' no puede tener confianza 'alta'")
        return self


# Rango esperado de peso por subcategoría (kg). Regla de negocio determinista que corre
# DESPUÉS de validar: si el peso propuesto cae afuera, el SKU va a medición física.
RANGO_PESO_KG = {
    Subcategoria.snacks: (0.01, 1.0),
    Subcategoria.bebidas: (0.1, 10.0),
    Subcategoria.almacen: (0.05, 10.0),
    Subcategoria.carnes: (0.1, 5.0),
    Subcategoria.lacteos: (0.05, 5.0),
    Subcategoria.limpieza: (0.05, 10.0),
    Subcategoria.perfumeria: (0.01, 2.0),
}


# --- Contrato del resultado (éxito y error con la misma forma) ---
class Metricas(BaseModel):
    modelo: str
    intentos: int
    input_tokens: int
    output_tokens: int
    costo_usd: float
    latencia_ms: int


class ErrorMotor(BaseModel):
    codigo: str  # ENTRADA_INVALIDA | SALIDA_INVALIDA | RATE_LIMIT | CONEXION | AUTENTICACION | API_ERROR
    mensaje: str
    campos: List[str] = []  # qué campo falló (loc de Pydantic)


class Resultado(BaseModel):
    ok: bool
    salida: Optional[Salida] = None
    error: Optional[ErrorMotor] = None
    metricas: Optional[Metricas] = None
