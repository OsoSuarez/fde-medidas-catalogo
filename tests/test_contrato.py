"""Pruebas sin API key: contrato de datos y caminos del motor (con el proveedor simulado)."""
import os

import pytest
from pydantic import ValidationError

from src.contrato import Entrada, Estado, Salida
from src.motor import Motor
from src.proveedores import MockProveedor

ENTRADA_OK = {"sku_id": "SKU-10231", "descripcion": "Galletitas 350 g",
              "subcategoria": "snacks", "texto_proveedor": "Caja 210x60x45 mm, 350 grs"}
SALIDA_OK = {"sku_id": "SKU-10231", "estado": "convertido", "largo_cm": 21, "alto_cm": 4.5,
             "ancho_cm": 6, "peso_kg": 0.35, "unidades_origen": ["mm", "g"], "confianza": "alta",
             "motivo": "Medidas en mm convertidas a cm y peso en g a kg."}


def test_entrada_valida():
    assert Entrada.model_validate(ENTRADA_OK).sku_id == "SKU-10231"


def test_entrada_invalida_informa_los_campos():
    with pytest.raises(ValidationError) as e:
        Entrada.model_validate({"sku_id": "10231", "descripcion": "Ga",
                                "subcategoria": "golosinas", "texto_proveedor": "Caja"})
    campos = {err["loc"][0] for err in e.value.errors()}
    assert campos == {"sku_id", "descripcion", "subcategoria", "texto_proveedor"}


def test_peso_fuera_de_rango():  # 350 en vez de 0,35: el error típico de unidades
    with pytest.raises(ValidationError):
        Salida.model_validate({**SALIDA_OK, "peso_kg": 350})


def test_validacion_cruzada_convertido_exige_medidas():
    with pytest.raises(ValidationError):
        Salida.model_validate({**SALIDA_OK, "alto_cm": None})


def test_fuera_de_tema_sin_medidas():
    with pytest.raises(ValidationError):
        Salida.model_validate({**SALIDA_OK, "estado": "fuera_de_tema"})


def test_motor_ok(monkeypatch):
    monkeypatch.delenv("MOCK_FALLAR", raising=False)
    r = Motor(MockProveedor()).procesar(ENTRADA_OK)
    assert r.ok and r.salida.estado == Estado.convertido and r.metricas.intentos == 1


def test_motor_reintenta_una_vez(monkeypatch):
    monkeypatch.setenv("MOCK_FALLAR", "primero")
    r = Motor(MockProveedor()).procesar(ENTRADA_OK)
    assert r.ok and r.metricas.intentos == 2


def test_motor_error_estructurado(monkeypatch):
    monkeypatch.setenv("MOCK_FALLAR", "siempre")
    r = Motor(MockProveedor()).procesar(ENTRADA_OK)
    assert not r.ok and r.error.codigo == "SALIDA_INVALIDA" and r.error.campos


def test_motor_entrada_invalida_no_llama_al_modelo():
    r = Motor(MockProveedor()).procesar({**ENTRADA_OK, "sku_id": "10231"})
    assert not r.ok and r.error.codigo == "ENTRADA_INVALIDA" and r.metricas is None


# --- Reglas deterministas: reproducen las 2 fallas reales de la medición PE2 ---
from src.reglas import aplicar_reglas, peso_pack_kg  # noqa: E402

TEXTO_PACK = "Pack 6x70g papas clásicas. Bolsa contenedora 30 x 22 x 8 cm."
TEXTO_CONFUSO = "bife chorizo al vacio aprox 500, bandeja 20x15x4. el peso varia segun la pieza"


def test_peso_pack():
    assert peso_pack_kg(TEXTO_PACK) == 0.42
    assert peso_pack_kg("Caja 210 x 60 x 45 mm, 350 grs") is None  # medidas no son pack


def test_falla_real_pack_marcado_ok():
    # Gemini devolvió "ok" para el pack (consulta 3)
    s = Salida.model_validate({**SALIDA_OK, "sku_id": "SKU-10233", "estado": "ok", "peso_kg": 0.42,
                               "unidades_origen": ["cm", "g"]})
    corregida, reglas = aplicar_reglas(s, TEXTO_PACK)
    assert corregida.estado == Estado.convertido and corregida.peso_kg == 0.42 and reglas


def test_pack_con_peso_mal_calculado():
    s = Salida.model_validate({**SALIDA_OK, "estado": "convertido", "peso_kg": 0.07})
    corregida, _ = aplicar_reglas(s, TEXTO_PACK)
    assert corregida.peso_kg == 0.42


def test_falla_real_confusa_dada_por_convertida():
    # Gemini devolvió "convertido" con peso 0,5 kg inventado (consulta 4)
    s = Salida.model_validate({**SALIDA_OK, "sku_id": "SKU-10234", "estado": "convertido", "largo_cm": 20,
                               "alto_cm": 4, "ancho_cm": 15, "peso_kg": 0.5})
    corregida, _ = aplicar_reglas(s, TEXTO_CONFUSO)
    assert corregida.estado == Estado.revisar and corregida.confianza.value == "baja"


def test_casos_correctos_no_se_tocan():
    s = Salida.model_validate(SALIDA_OK)
    corregida, reglas = aplicar_reglas(s, "Caja 210 x 60 x 45 mm. Peso neto 350 g.")
    assert corregida == s and reglas == []
    agua = Salida.model_validate({**SALIDA_OK, "estado": "ok", "peso_kg": 1.55, "unidades_origen": ["cm", "kg"]})
    assert aplicar_reglas(agua, "Botella: alto 31 cm, largo 9 cm, ancho 9 cm. Peso unitario 1,55 kg.")[1] == []
