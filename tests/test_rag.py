"""Pruebas del asistente RAG sin API key (embedder local + proveedor simulado)."""
import json

import pytest
from pydantic import ValidationError

from src.proveedores import ProveedorLLM, RespuestaLLM
from src.rag.agente import Asistente, MockRAG
from src.rag.chunking import trocear_carpeta
from src.rag.contrato import RespuestaModelo, Rol
from src.rag.indice import CARPETA_DOCS, Indice, construir_indice


@pytest.fixture(scope="module")
def indice():
    return Indice(construir_indice("hash"))


def test_chunks_tienen_contexto_y_acceso():
    chunks = trocear_carpeta(CARPETA_DOCS)
    assert len(chunks) >= 15
    assert all(" › " in c.texto.split("\n")[0] for c in chunks)          # prefijo Documento › Sección
    assert {c.acceso for c in chunks} == {"operario", "supervisor", "gerencia"}


def test_recupera_el_procedimiento_correcto(indice):
    top = indice.buscar("¿A qué temperatura recibo congelados?", Rol.operario, 3)
    assert top[0].chunk.documento == "02-recepcion-frio"


def test_operario_no_ve_costos(indice):
    for e in indice.buscar("cuánto cobra el operador logístico por línea", Rol.operario, 10):
        assert e.chunk.acceso == "operario"


def test_gerencia_si_ve_costos(indice):
    top = indice.buscar("cuánto cobra el operador logístico por línea", Rol.gerencia, 3)
    assert top[0].chunk.documento == "06-costos-operador"


def test_supervisor_ve_mermas_pero_no_costos(indice):
    docs = {e.chunk.documento for e in indice.buscar("merma vencidos costo por línea", Rol.supervisor, 22)}
    assert "05-mermas-devoluciones" in docs and "06-costos-operador" not in docs


def test_contrato_exige_fuente_si_no_deriva():
    with pytest.raises(ValidationError):
        RespuestaModelo(respuesta="Respuesta sin citar nada.", fuentes=[], confianza="alta", derivar_a_humano=False)


def test_contrato_derivacion_necesita_motivo():
    with pytest.raises(ValidationError):
        RespuestaModelo(respuesta="No lo sé, consultá.", confianza="baja", derivar_a_humano=True)


def test_asistente_mock_responde_con_fuente(indice):
    r = Asistente(indice, MockRAG("mock")).responder("operario", "¿A qué temperatura recibo congelados?")
    assert r.ok and r.fuentes[0].documento == "02-recepcion-frio" and r.metricas.intentos == 1


def test_pregunta_invalida_no_llama_al_modelo(indice):
    r = Asistente(indice, MockRAG("mock")).responder("director", "x")
    assert not r.ok and r.error["codigo"] == "ENTRADA_INVALIDA" and set(r.error["campos"]) == {"rol", "texto"}


class Inventor(ProveedorLLM):
    """Cita un fragmento que no existe en el contexto: el grounding lo tiene que rechazar."""
    def __init__(self):
        super().__init__("mock")
        self.llamadas = 0

    def generar(self, sistema, mensajes, esquema=None):
        self.llamadas += 1
        r = {"respuesta": "Inventado por el modelo.", "fuentes": ["C9"], "confianza": "alta",
             "derivar_a_humano": False, "motivo_derivacion": None}
        return RespuestaLLM(json.dumps(r), 100, 30)


def test_fuente_inventada_reintenta_y_falla(indice):
    p = Inventor()
    r = Asistente(indice, p).responder("operario", "¿Cómo armo un pallet?")
    assert not r.ok and r.error["codigo"] == "SALIDA_INVALIDA" and p.llamadas == 2


def test_caso_medidas_pack(indice):
    docs = [e.chunk.documento for e in indice.buscar("el proveedor mandó 6x70g, qué peso cargo", Rol.operario, 4)]
    assert "09-packs-y-casos-dudosos" in docs


def test_condiciones_comerciales_solo_gerencia(indice):
    assert all(e.chunk.acceso == "operario"
               for e in indice.buscar("descuento por volumen del proveedor", Rol.operario, 10))
    assert indice.buscar("descuento por volumen del proveedor", Rol.gerencia, 1)[0].chunk.documento == "11-condiciones-comerciales"
