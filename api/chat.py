"""
Función serverless de Vercel: POST /api/chat  {"pregunta": "..."}

Acceso abierto (sin clave): todos entran con el rol ROL_PUBLICO (variable de entorno, por defecto "operario").
Opcional: CLAVES_ROLES = "supervisor:xxx,gerencia:yyy" y el header X-Clave dan acceso a otro rol.
El rol lo decide siempre el servidor, nunca el navegador.

Protección de la cuota de Gemini: como mucho LIMITE_PREGUNTAS por visitante cada 10 minutos.
La API key de Gemini vive solo en las variables de entorno de Vercel.
Conocimiento: los .md de data/procedimientos/ se indexan al arrancar la función (cada deploy).
"""
import hmac
import json
import os
import sys
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.agente import Asistente  # noqa: E402
from src.rag.contrato import Rol  # noqa: E402
from src.rag.indice import Indice, construir_indice  # noqa: E402

_asistente = None
_historial = defaultdict(deque)  # ip -> horarios de sus preguntas (por instancia de la función)
VENTANA_S = 600


def _rol_publico() -> str:
    rol = os.getenv("ROL_PUBLICO", "operario").strip()
    return rol if rol in {r.value for r in Rol} else "operario"


def _rol_desde_clave(clave: str):
    """CLAVES_ROLES = "supervisor:clave1,gerencia:clave2". Comparación en tiempo constante."""
    encontrado = None
    for par in os.getenv("CLAVES_ROLES", "").split(","):
        if ":" in par:
            rol, valor = (x.strip() for x in par.split(":", 1))
            if valor and clave and hmac.compare_digest(valor.encode(), clave.encode()):
                encontrado = rol
    return encontrado


def _excede_limite(ip: str) -> bool:
    limite = int(os.getenv("LIMITE_PREGUNTAS", "20"))
    ahora, cola = time.time(), _historial[ip]
    while cola and ahora - cola[0] > VENTANA_S:
        cola.popleft()
    if len(cola) >= limite:
        return True
    cola.append(ahora)
    return False


class handler(BaseHTTPRequestHandler):
    def _responder(self, codigo: int, cuerpo: dict):
        datos = json.dumps(cuerpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(datos)

    def do_POST(self):
        global _asistente
        rol = _rol_desde_clave(self.headers.get("X-Clave", "")) or _rol_publico()
        try:
            largo = min(int(self.headers.get("Content-Length", 0)), 4000)
            pregunta = json.loads(self.rfile.read(largo) or b"{}").get("pregunta", "")
        except (ValueError, AttributeError):
            return self._responder(400, {"ok": False, "error": {"codigo": "JSON_INVALIDO"}})
        if pregunta == "__inicio__":  # la página pide su rol para mostrar ejemplos; no gasta tokens
            return self._responder(200, {"ok": True, "rol": rol})

        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        if _excede_limite(ip):
            return self._responder(429, {"ok": False, "error": {"codigo": "DEMASIADAS_PREGUNTAS",
                                   "mensaje": "Muchas preguntas seguidas: esperá unos minutos."}})
        if _asistente is None:
            # El índice se arma al arrancar desde data/procedimientos/*.md: agregar conocimiento = subir un .md
            _asistente = Asistente(Indice(construir_indice(os.getenv("EMBEDDER", "hash"))))
        res = _asistente.responder(rol, str(pregunta))
        cuerpo = res.model_dump(mode="json")
        cuerpo["rol"] = rol
        self._responder(200 if res.ok else 422, cuerpo)
