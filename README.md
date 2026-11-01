# fde-medidas-catalogo

Motor LLM que normaliza las **medidas y el peso** de los productos nuevos de un catálogo de *quick-commerce*.
El proveedor manda los datos en sus propias unidades (mm, g, packs, texto libre); el motor propone los valores en
la unidad estándar del sistema (**cm y kg**), los valida contra un contrato de datos y devuelve cuánto costó y cuánto tardó.

Proyecto del curso *Forward Deployed Engineer* (Coderhouse). Caso relevado en la PE1; esta es la **PE2: motor LLM base y validación estructurada**. Los datos son ficticios.

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

**En Google Colab:** [abrir `notebooks/pe2_motor_v2.ipynb`](https://colab.research.google.com/github/OsoSuarez/fde-medidas-catalogo/blob/main/notebooks/pe2_motor_v2.ipynb), cargar `GEMINI_API_KEY` en Secrets y correr las celdas.

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

> La consigna menciona OpenAI o Anthropic; este motor usa Gemini (como la clase) y también soporta Anthropic
> (`PROVEEDOR=anthropic`). Confirmar con la cátedra si Gemini se acepta para la entrega formal.
Resultados en `resultados/mediciones.md`.
