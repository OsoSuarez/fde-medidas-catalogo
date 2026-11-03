"""
Función serverless de Vercel: POST /api/chat  {"pregunta": "..."}  con el header X-Clave.

Seguridad:
  - El rol sale de la clave de acceso (variable CLAVES_ROLES en Vercel), nunca de lo que mande el navegador.
  - Sin CLAVES_ROLES configurada, la función no responde (falla cerrada).
  - La API key de Gemini vive solo en las variables de entorno de Vercel.

Conocimiento: los .md de data/procedimientos/ se indexan al arrancar la función (cada deploy).
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rag.agente import Asistente  # noqa: E402
from src.rag.indice import Indice, construir_indice  # noqa: E402

_asistente = None


def _rol_desde_clave(clave: str):
    """CLAVES_ROLES = "operario:clave1,supervisor:clave2,gerencia:clave3". Comparación en tiempo constante."""
    rol_encontrado = None
    for par in os.getenv("CLAVES_ROLES", "").split(","):
        if ":" in par:
            rol, valor = (x.strip() for x in par.split(":", 1))
            if valor and hmac.compare_digest(valor.encode(), clave.encode()):
                rol_encontrado = rol
    return rol_encontrado


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
        if not os.getenv("CLAVES_ROLES"):
            return self._responder(503, {"ok": False, "error": {"codigo": "SIN_CONFIGURAR",
                                   "mensaje": "Falta la variable CLAVES_ROLES en Vercel."}})
        rol = _rol_desde_clave(self.headers.get("X-Clave", ""))
        if not rol:
            return self._responder(401, {"ok": False, "error": {"codigo": "CLAVE_INVALIDA",
                                   "mensaje": "Clave de acceso incorrecta."}})
        try:
            largo = min(int(self.headers.get("Content-Length", 0)), 4000)
            pregunta = json.loads(self.rfile.read(largo) or b"{}").get("pregunta", "")
        except (ValueError, AttributeError):
            return self._responder(400, {"ok": False, "error": {"codigo": "JSON_INVALIDO"}})
        if pregunta == "__login__":  # el navegador valida la clave y conoce su rol sin gastar tokens
            return self._responder(200, {"ok": True, "rol": rol})
        if _asistente is None:
            # El índice se arma al arrancar desde data/procedimientos/*.md: agregar conocimiento = subir un .md
            _asistente = Asistente(Indice(construir_indice(os.getenv("EMBEDDER", "hash"))))
        res = _asistente.responder(rol, str(pregunta))
        cuerpo = res.model_dump(mode="json")
        cuerpo["rol"] = rol
        self._responder(200 if res.ok else 422, cuerpo)
