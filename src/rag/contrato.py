"""
Contrato de datos del asistente RAG (Pydantic).

El rol NO lo elige el usuario en la pregunta: lo resuelve el servidor a partir de la clave de acceso
(ver api/chat.py). Así un operario no puede "pedir" ser gerencia.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class Rol(str, Enum):
    operario = "operario"
    supervisor = "supervisor"
    gerencia = "gerencia"


# Qué niveles de documento puede leer cada rol (RBAC). Se aplica ANTES de buscar (pre-filtering).
NIVELES_VISIBLES = {
    Rol.operario: {"operario"},
    Rol.supervisor: {"operario", "supervisor"},
    Rol.gerencia: {"operario", "supervisor", "gerencia"},
}


class Chunk(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+#\d{2}$")      # ej. 02-recepcion-frio#03
    documento: str
    titulo: str
    seccion: str
    acceso: str = Field(pattern=r"^(operario|supervisor|gerencia)$")
    texto: str = Field(min_length=20)                   # con prefijo de contexto (Contextual Retrieval)
    hash_documento: str                                 # para saber qué vectores invalidar si cambia el doc


class Pregunta(BaseModel):
    rol: Rol
    texto: str = Field(min_length=3, max_length=500)


class Confianza(str, Enum):
    alta = "alta"
    media = "media"
    baja = "baja"


class RespuestaModelo(BaseModel):
    """Lo que tiene que devolver el LLM. Las fuentes son IDs de fragmento (C1, C2…) del contexto."""
    respuesta: str = Field(min_length=10, max_length=1200)
    fuentes: List[str] = Field(default_factory=list, max_length=5)
    confianza: Confianza
    derivar_a_humano: bool
    motivo_derivacion: Optional[str] = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def coherencia(self):
        if not self.derivar_a_humano and not self.fuentes:
            raise ValueError("si no deriva a un humano, tiene que citar al menos una fuente")
        if self.derivar_a_humano and not self.motivo_derivacion:
            raise ValueError("si deriva a un humano, tiene que explicar el motivo")
        if self.derivar_a_humano and self.confianza == Confianza.alta:
            raise ValueError("una derivación no puede tener confianza alta")
        return self


class Fuente(BaseModel):
    documento: str
    titulo: str
    seccion: str


class Metricas(BaseModel):
    modelo: str
    intentos: int
    input_tokens: int
    output_tokens: int
    costo_usd: float
    latencia_ms: int
    fragmentos_recuperados: int


class Resultado(BaseModel):
    ok: bool
    respuesta: Optional[str] = None
    fuentes: List[Fuente] = Field(default_factory=list)
    confianza: Optional[Confianza] = None
    derivar_a_humano: bool = False
    motivo_derivacion: Optional[str] = None
    error: Optional[dict] = None
    metricas: Optional[Metricas] = None
