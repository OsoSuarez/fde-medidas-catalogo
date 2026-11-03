"""Prueba local de lo mismo que corre en Vercel: http://localhost:8000

  CLAVES_ROLES="operario:op1,supervisor:sup1,gerencia:ger1" python servidor_local.py
  (con PROVEEDOR=mock funciona sin API key)
"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
from api.chat import handler as ChatHandler  # noqa: E402

PUBLIC = str(Path(__file__).parent / "public")


class Local(ChatHandler, SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        SimpleHTTPRequestHandler.__init__(self, *a, directory=PUBLIC, **kw)

    def do_POST(self):
        if self.path.startswith("/api/chat"):
            return ChatHandler.do_POST(self)
        self.send_error(404)


if __name__ == "__main__":
    print("Asistente del CD en http://localhost:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Local).serve_forever()
