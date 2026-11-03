# fde-medidas-catalogo

Motor LLM que normaliza las **medidas y el peso** de los productos nuevos de un catálogo de *quick-commerce*.
El proveedor manda los datos en sus propias unidades (mm, g, packs, texto libre); el motor propone los valores en
la unidad estándar del sistema (**cm y kg**), los valida contra un contrato de datos y devuelve cuánto costó y cuánto tardó.

Proyecto del curso *Forward Deployed Engineer* (Coderhouse). Caso relevado en la PE1.

- **PE2 — Motor LLM base y validación estructurada** (tag `pe2`): normaliza medidas y peso.
- **Asistente RAG del CD** (en curso, base de la PE3): responde dudas operativas con los procedimientos del centro de distribución, con permisos por rol. Tiene versión web para Vercel.

Los datos y procedimientos son ficticios.

## Estructura

```
fde-medidas-catalogo/
├── README.md
├── requirements.txt
├── .env.example          # nombres de variables, sin valores
├── .gitignore            # incluye .env
├── prompts/              # plantillas separadas del código
│   ├── sistema.md        # prompt de sistema
│   ├── usuario.md        # plantilla de la consulta
│   └── correccion.md     # mensaje de autocorrección para el reintento
├── data/consultas.json   # 5 consultas de prueba (incluye una confusa y una fuera de tema)
├── notebooks/
│   ├── pe2_motor_v2.ipynb  # notebook autocontenido (Colab): motor + reglas + medición
│   └── pe2_colab.ipynb     # corre el paquete src/ en Colab
├── resultados/           # mediciones generadas por src/medir.py
├── tests/test_contrato.py  # pruebas del contrato y del motor sin API (pytest)
└── src/
    ├── contrato.py       # Entrada / Salida (Pydantic) + resultado y errores
    ├── proveedores.py    # Gemini / Anthropic / OpenAI / mock detrás de una misma interfaz
    ├── motor.py          # el motor (también se usa desde la terminal)
    └── medir.py          # corre las 5 consultas y arma la tabla de medición
```

## Cómo correrlo (desde un clon limpio)

Requiere Python 3.10 o superior.

```bash
git clone https://github.com/OsoSuarez/fde-medidas-catalogo.git
cd fde-medidas-catalogo
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # y completar GEMINI_API_KEY
```

**Una consulta:**

```bash
python -m src.motor --sku SKU-10231 --descripcion "Galletitas 350 g" --subcategoria snacks \
  --texto "Caja 210 x 60 x 45 mm, peso neto 350 grs"
```

**Pruebas sin API key:** `pytest -q`

**Las 5 consultas de medición** (escribe `resultados/mediciones.md` y `resultados/mediciones.json`):

```bash
python -m src.medir
```

**Sin API key** (respuesta simulada): `PROVEEDOR=mock MODELO=mock python -m src.medir`.
Para ver el reintento y el error estructurado: agregar `MOCK_FALLAR=primero` o `MOCK_FALLAR=siempre`.

**En Google Colab:** [abrir `notebooks/pe2_motor_v2.ipynb`](https://colab.research.google.com/github/OsoSuarez/fde-medidas-catalogo/blob/main/notebooks/pe2_motor_v2.ipynb), cargar `GOOGLE_API_KEY` en Secrets y correr las celdas.

**Cambiar de proveedor:** `PROVEEDOR=anthropic` (con `MODELO=claude-haiku-5-5` y `ANTHROPIC_API_KEY`) u `openai`
(con sus precios en `PRECIO_INPUT_MTOK` / `PRECIO_OUTPUT_MTOK`). El resto del código no cambia.

## Contrato de datos

| Clase | Campo | Regla |
|---|---|---|
| Entrada | `sku_id` | patrón `^SKU-\d{4,8}$` |
| Entrada | `descripcion` | 3 a 200 caracteres |
| Entrada | `subcategoria` | Enum (snacks, bebidas, almacen, carnes, lacteos, limpieza, perfumeria) |
| Entrada | `texto_proveedor` | 5 a 2000 caracteres |
| Salida | `estado` | Enum: `ok`, `convertido`, `revisar`, `fuera_de_tema` |
| Salida | `largo_cm`, `alto_cm`, `ancho_cm` | rango 0 < x ≤ 200 |
| Salida | `peso_kg` | rango 0 < x ≤ 50 |
| Salida | `unidades_origen` | lista de Enum (mm, cm, m, g, kg, ml, l, desconocida), al menos 1 |
| Salida | `confianza` | Enum: `alta`, `media`, `baja` |
| Salida | `motivo` | 15 a 400 caracteres |
| Salida | (cruzada) | `ok`/`convertido` exigen las 4 medidas; `fuera_de_tema` no admite medidas; `revisar` no admite confianza `alta` |

Después de validar corre una regla de negocio determinista: si el peso queda fuera del rango de su subcategoría,
el SKU pasa a `revisar` (va a medición física).

## Qué pasa cuando algo falla

- **Entrada inválida:** no se llama al modelo; se devuelve `ENTRADA_INVALIDA` con los campos que fallaron.
- **Salida inválida:** se registra qué campo falló, se le devuelve el error al modelo y se reintenta **una vez**.
  Si vuelve a fallar, se devuelve `SALIDA_INVALIDA` con los campos. Nada se carga en el catálogo.
- **Errores de la API:** `RATE_LIMIT`, `CONEXION`, `AUTENTICACION` o `API_ERROR`, con el mismo formato de respuesta.

Toda respuesta tiene la forma `{ok, salida, error, metricas}`; `metricas` trae modelo, intentos, tokens
(del campo `usage` de la API), costo en USD y latencia en ms.

## Medición

Modelo: `gemini-3.1-flash-lite`. Precio publicado del tier pago: USD 0,25 por millón de tokens de entrada y USD 1,50 de salida
([precios de Gemini](https://ai.google.dev/gemini-api/docs/pricing), consultado el 26/10/2026). Con el tier gratis no se cobra;
el costo informado es **hipotético**: lo que costaría con la tarifa paga.

> El motor usa Gemini (aceptado por la cátedra) y también soporta Anthropic (`PROVEEDOR=anthropic`) y OpenAI.

Resultados en `resultados/mediciones.md`.

---

## Asistente RAG del CD

Responde preguntas del personal ("¿a qué temperatura recibo congelados?") usando **solo** los procedimientos de `data/procedimientos/`, cita de dónde sacó la respuesta y deriva al supervisor cuando no está escrito.

```
documentos → chunking (por sección, con prefijo de contexto) → embeddings → índice / ChromaDB
pregunta → filtro por rol → búsqueda híbrida (vectores + BM25, fusión RRF) → LLM → validación Pydantic + fuentes verificadas
```

| Pieza | Archivo |
|---|---|
| Procedimientos ficticios con nivel de acceso: 6 de operación del CD + 5 del caso de medidas (estándar cm/kg, alta de SKU, packs y casos dudosos, medición física, condiciones comerciales) | `data/procedimientos/*.md` |
| Contrato (Pregunta, RespuestaModelo, roles y permisos) | `src/rag/contrato.py` |
| Chunking estructural con solapamiento | `src/rag/chunking.py` |
| Embeddings: Gemini o local sin API | `src/rag/embeddings.py` |
| Índice, búsqueda híbrida, ChromaDB | `src/rag/indice.py` |
| Asistente (prompt, reintento, grounding de fuentes) | `src/rag/agente.py`, `prompts/rag_*.md` |
| Personalidad de JOPI (tono) | `prompts/rag_sistema.md` |
| Web: chat + función serverless | `public/index.html`, `api/chat.py`, `vercel.json` |
| Notebook Colab | `notebooks/pe3_asistente_cd.ipynb` |

**Permisos por rol (RBAC):** operario ve medidas, recepción, frío, transferencias y despacho; supervisor suma altas de SKU y mermas; gerencia suma costos del operador y condiciones comerciales con proveedores.
El filtro se aplica **antes** de buscar, así que un operario nunca recupera el documento de costos, aunque lo pida.
En la web el acceso es abierto: todos entran con el rol de `ROL_PUBLICO`, que decide el servidor (no el navegador). Hay un límite de preguntas por visitante para cuidar la cuota de Gemini.

```bash
python -m src.rag.indexar                       # arma data/indice_cd.json (sin API key)
python -m src.rag.indexar --embedder gemini     # con embeddings de Gemini
python -m src.rag.agente --rol operario --pregunta "¿A qué temperatura recibo congelados?"
ROL_PUBLICO=gerencia python servidor_local.py   # web en localhost:8000
```

### Agregar conocimiento a JOPI

1. Copiá `data/procedimientos/_plantilla.md`, renombralo (ej. `07-inventario-ciclico.md`) y completalo. Elegí `acceso`: operario, supervisor o gerencia.
2. Subilo a GitHub (desde la web: **Add file → Upload files** dentro de `data/procedimientos`, o con `git push`).
3. Vercel vuelve a publicar solo y JOPI arma su índice con el archivo nuevo al arrancar.

### Publicar en Vercel

1. En [vercel.com](https://vercel.com), **Add New → Project** e importar este repo de GitHub. Framework: *Other*.
2. En **Environment Variables** cargar:
   - `GEMINI_API_KEY`: tu clave de Gemini.
   - `ROL_PUBLICO`: `gerencia` para que JOPI responda sobre todos los procedimientos (por defecto `operario`).
   - Opcionales: `LIMITE_PREGUNTAS` (por defecto 20 cada 10 minutos por visitante), `MODELO` (por defecto `gemini-3.1-flash-lite`). Con `PROVEEDOR=mock` funciona sin API key, en modo demo.
3. **Deploy.** Cada `git push` vuelve a publicar.

ChromaDB no entra en una función serverless: en Vercel el índice se arma en memoria desde los `.md` al arrancar y la búsqueda corre en Python puro. En Colab se usa ChromaDB con los mismos fragmentos y vectores.
