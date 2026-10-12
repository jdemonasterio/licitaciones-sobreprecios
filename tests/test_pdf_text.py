"""Tests de `sobreprecios/pdf_text.py`. Todo OFFLINE: sin red, sin LLM (los
fixtures son PDF con texto digital limpio, asi que la cadena de `extraer_texto`
resuelve en el primer escalon -- `pdfplumber_columnas` -- y nunca llega a
intentar OCR ni el LLM de PDF).

Fixtures (`tests/fixtures/pdf/`, generados una vez con PyMuPDF solo para
crear estos .pdf de prueba -- el modulo en si NO depende de PyMuPDF, ver su
docstring sobre la licencia AGPL):
  una_columna.pdf   un parrafo de una columna, con un CUIT y un monto -- caso
                    feliz, calidad alta, sin pasos extra.
  dos_columnas.pdf  2 "avisos" en columnas distintas con filas a la MISMA
                    altura: `pdfplumber.extract_text()` liso los intercala
                    palabra por palabra fila a fila (el bug real que motiva
                    este modulo, ver `sources/bo_sociedades.py` gotcha 4).
                    Sirve para probar que `_texto_por_columnas` separa cada
                    aviso en su propio bloque en vez de mezclarlos.
"""
from pathlib import Path

import pdfplumber
import pytest

from sobreprecios import pdf_text

FIXTURES = Path(__file__).parent / "fixtures" / "pdf"


def test_extraer_texto_una_columna_resuelve_en_pdfplumber_columnas_sin_ocr_ni_llm():
    resultado = pdf_text.extraer_texto(FIXTURES / "una_columna.pdf")
    assert resultado["metodo"] == "pdfplumber_columnas"
    assert resultado["paginas"] == 1
    assert resultado["calidad"] >= pdf_text._UMBRAL_OK
    assert resultado["advertencias"] == []   # no se intento nada mas: calidad ok de entrada
    assert "ACME SA" in resultado["texto"]
    assert "20-12345678-9" in resultado["texto"]
    assert "1.234,56" in resultado["texto"]


def test_extraer_texto_dos_columnas_no_intercala_los_avisos():
    """Regresion del bug real: dos avisos en columnas separadas, con lineas a
    la misma altura, NO deben quedar mezclados palabra por palabra."""
    resultado = pdf_text.extraer_texto(FIXTURES / "dos_columnas.pdf")
    texto = resultado["texto"]
    # Cada aviso tiene que aparecer como bloque propio: el nombre del
    # presidente sigue inmediatamente al nombre de SU sociedad, no al de la
    # otra (que es justo lo que pasa si se lee fila por fila cruzando columnas).
    pos_uno = texto.index("AVISO UNO SOCIEDAD")
    pos_juan = texto.index("Juan Perez")
    pos_dos = texto.index("AVISO DOS SOCIEDAD")
    pos_maria = texto.index("Maria Lopez")
    assert pos_uno < pos_juan < pos_dos < pos_maria
    # Y el intercalado NO debe aparecer: "AVISO UNO SOCIEDAD" no deberia
    # tener "AVISO DOS SOCIEDAD" pegado en la misma linea (eso es lo que
    # hace pdfplumber.extract_text() liso: ver comparacion abajo).
    assert "AVISO UNO SOCIEDAD\nAVISO DOS SOCIEDAD" not in texto
    for linea in texto.splitlines():
        assert not ("AVISO UNO" in linea and "AVISO DOS" in linea)


def test_texto_por_columnas_mejora_sobre_extract_text_liso():
    """El propio pdfplumber, sin agrupar por columna, intercala las dos
    sociedades en la misma linea (columna 1 + columna 2 a la misma altura).
    Este test documenta el bug que `_texto_por_columnas` corrige."""
    with pdfplumber.open(str(FIXTURES / "dos_columnas.pdf")) as pdf:
        liso = pdf.pages[0].extract_text() or ""
        agrupado = pdf_text._texto_por_columnas(pdf.pages[0])
    assert "AVISO UNO SOCIEDAD AVISO DOS SOCIEDAD" in liso.replace("\n", " ")
    assert "AVISO UNO SOCIEDAD AVISO DOS SOCIEDAD" not in agrupado.replace("\n", " ")


def test_extraer_texto_estrategia_columnas_no_escala_aunque_la_calidad_sea_baja(monkeypatch):
    """`estrategia="columnas"` es el modo "rapido y barato": nunca debe tocar
    OCR ni LLM, ni siquiera si la calidad diera para escalar. Se fuerza una
    calidad baja con monkeypatch para no depender de un PDF escaneado real."""
    monkeypatch.setattr(pdf_text, "_heuristica_calidad", lambda texto: 0.01)

    def _reventar(*a, **k):
        raise AssertionError("no deberia intentar OCR en estrategia='columnas'")
    monkeypatch.setattr(pdf_text, "_extraer_ocr", _reventar)

    resultado = pdf_text.extraer_texto(FIXTURES / "una_columna.pdf", estrategia="columnas")
    assert resultado["metodo"] == "pdfplumber_columnas"
    assert resultado["calidad"] == 0.01


def test_extraer_texto_estrategia_sin_llm_escala_a_ocr_pero_no_al_llm(monkeypatch):
    monkeypatch.setattr(pdf_text, "_heuristica_calidad", lambda texto: 0.01)
    monkeypatch.setattr(pdf_text, "_extraer_ocr", lambda path: None)   # simula Tesseract no instalado
    # Si este modo tocara el LLM, importaria `sobreprecios.llm` (que a su vez
    # pide GEMINI_API_KEY): no mockeamos nada de LLM a proposito, para que
    # cualquier intento de usarlo explote con un error de importacion/config
    # bien visible en vez de pasar en silencio.

    resultado = pdf_text.extraer_texto(FIXTURES / "una_columna.pdf", estrategia="sin_llm")
    assert resultado["metodo"] == "pdfplumber_columnas"
    assert any("OCR no disponible" in a for a in resultado["advertencias"])


def test_extraer_texto_estrategia_invalida_levanta_valueerror():
    with pytest.raises(ValueError):
        pdf_text.extraer_texto(FIXTURES / "una_columna.pdf", estrategia="rara")


def test_extraer_texto_archivo_inexistente_no_rompe():
    resultado = pdf_text.extraer_texto(FIXTURES / "no_existe.pdf")
    assert resultado["texto"] == ""
    assert resultado["metodo"] == "ninguno"
    assert resultado["paginas"] == 0
    assert resultado["calidad"] == 0.0
    assert len(resultado["advertencias"]) == 1
    assert "no pudo abrir" in resultado["advertencias"][0]


@pytest.mark.parametrize("texto,esperado_bajo", [
    ("", True),
    ("a", True),
    ("#@$%&*()#@$%&*()#@$%&*()", True),
])
def test_heuristica_calidad_detecta_ruido_y_vacio(texto, esperado_bajo):
    calidad = pdf_text._heuristica_calidad(texto)
    if esperado_bajo:
        assert calidad < 0.3


def test_heuristica_calidad_texto_real_es_alta():
    texto = ("Adjudicase a la empresa ACME Sociedad Anonima el renglon numero uno "
             "por un monto total de pesos mil doscientos treinta y cuatro con cincuenta y seis centavos.")
    assert pdf_text._heuristica_calidad(texto) >= pdf_text._UMBRAL_OK
