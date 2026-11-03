"""
Proveedores de LLM detrás de una interfaz común (extra opcional de la PE2).

El motor solo conoce `ProveedorLLM.generar(...)`: cambiar de Anthropic a OpenAI
es cambiar la variable de entorno PROVEEDOR, sin tocar el resto del código.
Cada proveedor traduce sus propios errores de API a `ErrorProveedor` con un código común.
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class RespuestaLLM:
    texto: str
    input_tokens: int   # tomados del campo `usage` de la respuesta de la API
    output_tokens: int


class ErrorProveedor(Exception):
    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo  # RATE_LIMIT | CONEXION | AUTENTICACION | API_ERROR
        self.mensaje = mensaje


class ProveedorLLM:
    nombre = "base"

    def __init__(self, modelo: str, timeout_s: float = 30.0, max_tokens: int = 400):
        self.modelo = modelo
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    def generar(self, sistema: str, mensajes: List[Dict[str, str]], esquema=None) -> RespuestaLLM:
        """esquema: clase Pydantic de salida; los proveedores que lo soportan lo usan como salida estructurada."""
        raise NotImplementedError


class AnthropicProveedor(ProveedorLLM):
    nombre = "anthropic"

    def __init__(self, modelo: str, **kw):
        super().__init__(modelo, **kw)
        import anthropic  # import tardío: solo se necesita si se usa este proveedor
        self._sdk = anthropic
        # max_retries=0: el reintento lo controla el motor, para poder medirlo y registrarlo
        self._cliente = anthropic.Anthropic(timeout=self.timeout_s, max_retries=0)

    def generar(self, sistema, mensajes, esquema=None):
        a = self._sdk
        try:
            r = self._cliente.messages.create(
                model=self.modelo,
                max_tokens=self.max_tokens,
                temperature=0,
                system=sistema,
                messages=mensajes,
            )
        except a.RateLimitError as e:
            raise ErrorProveedor("RATE_LIMIT", f"Límite de uso de la API alcanzado: {e}")
        except (a.APIConnectionError, a.APITimeoutError) as e:
            raise ErrorProveedor("CONEXION", f"No se pudo conectar con la API: {e}")
        except a.AuthenticationError:
            raise ErrorProveedor("AUTENTICACION", "API key inválida o ausente (revisar ANTHROPIC_API_KEY en .env)")
        except a.APIStatusError as e:
            raise ErrorProveedor("API_ERROR", f"La API respondió {e.status_code}: {e.message}")
        texto = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        return RespuestaLLM(texto, r.usage.input_tokens, r.usage.output_tokens)


class OpenAIProveedor(ProveedorLLM):
    nombre = "openai"

    def __init__(self, modelo: str, **kw):
        super().__init__(modelo, **kw)
        import openai
        self._sdk = openai
        self._cliente = openai.OpenAI(timeout=self.timeout_s, max_retries=0)

    def generar(self, sistema, mensajes, esquema=None):
        o = self._sdk
        try:
            r = self._cliente.chat.completions.create(
                model=self.modelo,
                max_tokens=self.max_tokens,
                temperature=0,
                messages=[{"role": "system", "content": sistema}, *mensajes],
            )
        except o.RateLimitError as e:
            raise ErrorProveedor("RATE_LIMIT", f"Límite de uso de la API alcanzado: {e}")
        except (o.APIConnectionError, o.APITimeoutError) as e:
            raise ErrorProveedor("CONEXION", f"No se pudo conectar con la API: {e}")
        except o.AuthenticationError:
            raise ErrorProveedor("AUTENTICACION", "API key inválida o ausente (revisar OPENAI_API_KEY en .env)")
        except o.APIStatusError as e:
            raise ErrorProveedor("API_ERROR", f"La API respondió {e.status_code}: {e.message}")
        return RespuestaLLM(r.choices[0].message.content or "",
                            r.usage.prompt_tokens, r.usage.completion_tokens)


class GeminiProveedor(ProveedorLLM):
    nombre = "gemini"

    def __init__(self, modelo: str, **kw):
        super().__init__(modelo, **kw)
        from google import genai
        from google.genai import errors, types
        self._errors = errors
        self._types = types
        # Clave: GEMINI_API_KEY (o GOOGLE_API_KEY). En Colab se carga desde Secrets.
        clave = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self._cliente = genai.Client(api_key=clave,
                                     http_options=types.HttpOptions(timeout=int(self.timeout_s * 1000)))

    def generar(self, sistema, mensajes, esquema=None):
        t, err = self._types, self._errors
        contenidos = [{"role": "model" if m["role"] == "assistant" else "user",
                       "parts": [{"text": m["content"]}]} for m in mensajes]
        def config(con_pensamiento_minimo: bool):
            extra = {}
            if con_pensamiento_minimo:
                # Menos "razonamiento" interno = respuesta más rápida y más barata. Desactivable con GEMINI_THINKING=default.
                extra["thinking_config"] = t.ThinkingConfig(thinking_level=t.ThinkingLevel.MINIMAL)
            return t.GenerateContentConfig(
                system_instruction=sistema,
                temperature=0,
                max_output_tokens=self.max_tokens,
                response_mime_type="application/json",
                response_schema=esquema,  # salida estructurada: Gemini respeta la forma de Salida
                automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
                **extra,
            )

        minimo = os.getenv("GEMINI_THINKING", "minimal") == "minimal" and not getattr(self, "_sin_thinking", False)
        try:
            try:
                r = self._cliente.models.generate_content(model=self.modelo, contents=contenidos, config=config(minimo))
            except err.ClientError as e:
                if not (minimo and e.code == 400 and "think" in str(e.message).lower()):
                    raise
                self._sin_thinking = True  # este modelo no acepta el nivel mínimo: se sigue sin configurarlo
                r = self._cliente.models.generate_content(model=self.modelo, contents=contenidos, config=config(False))
        except err.ClientError as e:
            if e.code == 402:
                raise ErrorProveedor("SIN_CREDITO", "El proyecto de Gemini no tiene crédito (revisar facturación en AI Studio)")
            if e.code == 429:
                raise ErrorProveedor("RATE_LIMIT", f"Límite de uso de la API alcanzado: {e.message}")
            if e.code in (401, 403) or "API key" in str(e.message):
                raise ErrorProveedor("AUTENTICACION", "API key inválida o ausente (revisar GEMINI_API_KEY)")
            raise ErrorProveedor("API_ERROR", f"La API respondió {e.code}: {e.message}")
        except err.ServerError as e:
            raise ErrorProveedor("API_ERROR", f"Error del servidor ({e.code}): {e.message}")
        except Exception as e:  # httpx: sin conexión o timeout
            if type(e).__module__.startswith(("httpx", "httpcore")) or isinstance(e, (TimeoutError, ConnectionError)):
                raise ErrorProveedor("CONEXION", f"No se pudo conectar con la API: {e}")
            raise
        u = r.usage_metadata
        # Los tokens de razonamiento (thinking) se cobran como salida
        salida = (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        return RespuestaLLM(r.text or "", u.prompt_token_count or 0, salida)


class MockProveedor(ProveedorLLM):
    """Respuesta simulada para probar sin API key (como la plantilla del aula).
    MOCK_FALLAR=primero → salida inválida solo en el primer intento (muestra el reintento).
    MOCK_FALLAR=siempre → salida inválida en todos los intentos (muestra el error estructurado)."""
    nombre = "mock"

    def __init__(self, modelo: str = "mock", **kw):
        super().__init__(modelo, **kw)
        self._llamadas = 0

    def generar(self, sistema, mensajes, esquema=None):
        self._llamadas += 1
        prompt = mensajes[0]["content"]
        sku = re.search(r"SKU: (\S+)", prompt).group(1)
        tokens_in = len((sistema + prompt).split()) * 2
        modo = os.getenv("MOCK_FALLAR", "")
        if modo == "siempre" or (modo == "primero" and self._llamadas == 1):
            malo = {"sku_id": sku, "estado": "convertido", "largo_cm": 12, "alto_cm": None,
                    "ancho_cm": 8, "peso_kg": 0.35, "unidades_origen": ["mm", "g"],
                    "confianza": "alta", "motivo": "corto"}
            return RespuestaLLM(json.dumps(malo), tokens_in, 60)
        bueno = {"sku_id": sku, "estado": "convertido", "largo_cm": 12.0, "alto_cm": 5.0,
                 "ancho_cm": 8.0, "peso_kg": 0.35, "unidades_origen": ["mm", "g"],
                 "confianza": "alta",
                 "motivo": "Simulado: medidas en mm convertidas a cm y peso en g convertido a kg."}
        return RespuestaLLM(json.dumps(bueno, ensure_ascii=False), tokens_in, 80)


PROVEEDORES = {"gemini": GeminiProveedor, "anthropic": AnthropicProveedor,
               "openai": OpenAIProveedor, "mock": MockProveedor}


def crear_proveedor(nombre: str, modelo: str) -> ProveedorLLM:
    if nombre not in PROVEEDORES:
        raise ValueError(f"PROVEEDOR desconocido: {nombre}. Opciones: {sorted(PROVEEDORES)}")
    return PROVEEDORES[nombre](modelo)
