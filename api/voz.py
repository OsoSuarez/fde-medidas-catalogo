"""
Función serverless de Vercel: POST /api/voz  {"texto": "..."}  →  audio WAV con la voz de JOPI (Gemini TTS).

Variables de entorno (Vercel):
  GEMINI_API_KEY   la misma clave que usa el chat
  TTS_MODELO       por defecto gemini-3.8-flash-lite-tts (rápido); gemini-3.8-flash-tts = más expresivo
  JOPI_VOZ         voz prediseñada (por defecto "Leda", juvenil) o el ID "voice_..." de una voz creada
                   con Voice design en AI Studio
  JOPI_ESTILO      cómo habla (se manda como estilo, no se lee en voz alta)

Si algo falla, responde un JSON de error y la página usa la voz del navegador.
"""
import base64
import json
import os
import re
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler

_cliente = None
_historial = defaultdict(deque)
VENTANA_S = 600
EMOJIS = re.compile("[\U0001F300-\U0001FAFF☀-➿️]")
ESTILO_DEFECTO = "Rioplatense de Buenos Aires, cálida, alegre y con energía, como una compañera de trabajo copada"


def _excede_limite(ip: str) -> bool:
    limite = int(os.getenv("LIMITE_VOZ", "30"))
    ahora, cola = time.time(), _historial[ip]
    while cola and ahora - cola[0] > VENTANA_S:
        cola.popleft()
    if len(cola) >= limite:
        return True
    cola.append(ahora)
    return False


def sintetizar(texto: str) -> bytes:
    global _cliente
    from google import genai
    if _cliente is None:
        _cliente = genai.Client(api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    r = _cliente.interactions.create(
        model=os.getenv("TTS_MODELO", "gemini-3.8-flash-lite-tts"),
        input=[{"type": "user_input", "content": [{
            "type": "text",
            "text": texto,
            "annotations": [{"type": "speech_metadata", "style": os.getenv("JOPI_ESTILO", ESTILO_DEFECTO)}],
        }]}],
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": os.getenv("JOPI_VOZ", "Leda")}]},
    )
    datos = r.output_audio.data
    return base64.b64decode(datos) if isinstance(datos, str) else bytes(datos)


class handler(BaseHTTPRequestHandler):
    def _json(self, codigo: int, cuerpo: dict):
        datos = json.dumps(cuerpo, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(datos)

    def do_POST(self):
        try:
            largo = min(int(self.headers.get("Content-Length", 0)), 6000)
            texto = str(json.loads(self.rfile.read(largo) or b"{}").get("texto", ""))
        except (ValueError, AttributeError):
            return self._json(400, {"ok": False, "error": "JSON_INVALIDO"})
        texto = EMOJIS.sub("", texto).strip()[:1200]
        if not texto:
            return self._json(400, {"ok": False, "error": "SIN_TEXTO"})
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        if _excede_limite(ip):
            return self._json(429, {"ok": False, "error": "DEMASIADOS_AUDIOS"})
        try:
            audio = sintetizar(texto)
        except Exception as e:  # cualquier falla → la página usa la voz del navegador
            return self._json(502, {"ok": False, "error": "TTS_ERROR", "detalle": str(e)[:300]})
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(audio)))
        self.end_headers()
        self.wfile.write(audio)
