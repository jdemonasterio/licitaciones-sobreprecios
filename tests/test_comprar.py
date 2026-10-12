"""Tests de parsing offline para sobreprecios.sources.comprar (sin red).

Los fixtures en tests/fixtures/comprar/ son HTML real de comprar.gob.ar
(bajado 2026-10-11, sin login, con los <script>/<link>/viewstate recortados
para que no pesen varios MB — ver el módulo para por qué eso es seguro: el
viewstate no se necesita para parsear, solo para volver a mandarlo en un
postback). Dos procesos reales:

  - 80-0014-LPU25 (Ministerio de Salud, medicamentos oncológicos,
    adjudicado, 45 renglones): el caso rico, con cuadro comparativo y
    dictamen. El renglón 1 (MICROSULES, 480 unidades, $67.400 unitario,
    $32.352.000 total) es el MISMO ejemplo que cita el Boletín Oficial
    ("LICITACIÓN PÚBLICA Nº 80-0014-LPU25... renglón 1 por 480 unidades,
    monto total $32.352.000") — por eso estos números están hardcodeados
    en los tests como ground truth, no inventados.
  - 14/2-0031-LPU22 (CNEA, racionamiento Gendarmería, 1 renglón): caso
    simple, sirve para el gotcha del "&nbsp" sin punto y coma en el
    expediente.
  - 84/99-0651-LPU26: proceso real tomado del home ANTES de su apertura
    (sin ofertas/dictamen todavía) — el caso "proceso incompleto".
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sobreprecios.sources import comprar

FIXTURES = Path(__file__).parent / "fixtures" / "comprar"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def detalle_80_0014():
    return comprar.parse_detalle_html(_read("detalle_80-0014-LPU25.html"))


@pytest.fixture(scope="module")
def ofertas_80_0014():
    return comprar.parse_cuadro_comparativo_html(_read("cuadro_comparativo_80-0014-LPU25.html"))


@pytest.fixture(scope="module")
def adjudicaciones_80_0014():
    return comprar.parse_dictamen_html(_read("dictamen_80-0014-LPU25.html"))


# --- parse_detalle_html ------------------------------------------------

def test_parse_detalle_extrae_metadata_del_proceso(detalle_80_0014):
    d = detalle_80_0014
    assert d["numero"] == "80-0014-LPU25"
    assert d["expediente"] == "EX-2025-48989745- -APN-DCYC#MS"
    assert d["objeto"] == "Adquisición de medicamentos oncológicos CBDE"
    assert d["tipo_procedimiento"] == "Licitacion Pública"
    assert d["uoc"] == "80/000 - Dirección General de Administración - MS"


def test_parse_detalle_extrae_fechas_incluyendo_adjudicacion(detalle_80_0014):
    fechas = detalle_80_0014["fechas"]
    assert fechas["apertura"] == "11/07/2025 10:00 Hrs."
    # La fecha de adjudicación sale de "Actos administrativos", NO del
    # cronograma: ver el gotcha de la sección "Cláusulas Particulares" que
    # reusa el mismo control (y por lo tanto el mismo id) que "Actos
    # administrativos" — si se resuelve mal, esto queda vacío.
    assert fechas["adjudicacion"] == "04/03/2026"


def test_parse_detalle_extrae_45_renglones_con_codigo_y_cantidad(detalle_80_0014):
    renglones = detalle_80_0014["renglones"]
    assert len(renglones) == 45
    primero = renglones[0]
    assert primero["nro"] == 1
    assert primero["codigo_sibys"] == "2.5.2-3956.19"
    assert primero["cantidad"] == 480.0
    assert primero["unidad"] == "UNIDAD"
    assert "ZOLEDRONICO" in primero["descripcion"]


def test_parse_detalle_resuelve_links_de_acta_y_dictamen(detalle_80_0014):
    assert detalle_80_0014["_acta_url"].startswith(
        "https://comprar.gob.ar/EVALUACIONOFERTA/GenerarActaAperturaBastrap.aspx?qs=")
    assert detalle_80_0014["_dictamen_url"].startswith(
        "https://comprar.gob.ar/EVALUACIONOFERTA/PreAdjudicarVisualizarDictamen.aspx?qs=")
    assert detalle_80_0014["_cuadro_comparativo_target"] == (
        "ctl00$CPH1$UCVistaPreviaPliego$UC_OfertasProceso$lnkVerCuadroComparativo")


def test_parse_detalle_proceso_sin_apertura_no_tiene_links_de_adjudicacion():
    """Proceso tomado del home ANTES de su fecha de apertura: nada de
    acta/dictamen/cuadro comparativo todavía (no existen esos botones en el
    HTML), pero sí renglones y cronograma."""
    d = comprar.parse_detalle_html(_read("detalle_84_99-0651-LPU26_preapertura.html"))
    assert d["numero"] == "84/99-0651-LPU26"
    assert len(d["renglones"]) == 1
    assert d["fechas"]["adjudicacion"] == ""
    assert d["_acta_url"] is None
    assert d["_dictamen_url"] is None
    assert d["_cuadro_comparativo_target"] is None


def test_parse_detalle_limpia_entidad_nbsp_sin_punto_y_coma():
    """El sitio manda '&nbsp' SIN ';' (entidad inválida) para rellenar
    expedientes; html.parser no la decodifica como espacio sola."""
    d = comprar.parse_detalle_html(_read("detalle_14_2-0031-LPU22.html"))
    assert "&nbsp" not in d["expediente"]
    assert d["expediente"] == "EX-2022-87285532- -APN-GCCAE#CNEA"


def test_parse_detalle_renglon_unico_de_servicio(detalle_80_0014):
    d = comprar.parse_detalle_html(_read("detalle_14_2-0031-LPU22.html"))
    assert len(d["renglones"]) == 1
    assert d["renglones"][0]["cantidad"] == 29000.0
    assert d["renglones"][0]["unidad"] == "UNIDAD"


# --- parse_cuadro_comparativo_html --------------------------------------

def test_parse_cuadro_comparativo_trae_una_fila_por_renglon_y_oferente(ofertas_80_0014):
    assert len(ofertas_80_0014) == 100   # 45 renglones, varios oferentes cada uno
    renglon_1 = [o for o in ofertas_80_0014 if o["renglon"] == 1]
    assert len(renglon_1) == 3   # 3 oferentes compitieron el renglón 1


def test_parse_cuadro_comparativo_matchea_el_ejemplo_del_boletin_oficial(ofertas_80_0014):
    """El BO cita textualmente: LICITACIÓN PÚBLICA Nº 80-0014-LPU25,
    renglón 1 por 480 unidades, monto total $32.352.000. Microsules fue
    quien ofertó exactamente ese precio ($67.400 x 480 = $32.352.000)."""
    ganador = next(o for o in ofertas_80_0014
                    if o["renglon"] == 1 and o["proveedor"].startswith("MICROSULES"))
    assert ganador["precio_unitario"] == 67400.0
    assert ganador["total"] == 32352000.0
    assert ganador["cuit"] == "30519887335"
    assert ganador["moneda"] == "Peso Argentino"


def test_parse_cuadro_comparativo_extrae_marca_de_la_especificacion_tecnica(ofertas_80_0014):
    """La marca no es una columna propia: sale de 'MARCA: X' dentro del texto
    libre de especificaciones técnicas (o de su tooltip 'Ver más')."""
    con_marca = [o for o in ofertas_80_0014 if o["marca"]]
    assert con_marca   # al menos una oferta trae marca reconocible
    assert any(o["marca"] == "MICROSULES" for o in con_marca)


def test_parse_cuadro_comparativo_separa_proveedor_y_cuit(ofertas_80_0014):
    for oferta in ofertas_80_0014:
        assert oferta["cuit"].isdigit()
        assert oferta["cuit"] not in oferta["proveedor"]


# --- parse_dictamen_html -------------------------------------------------

def test_parse_dictamen_trae_el_ganador_por_renglon(adjudicaciones_80_0014):
    assert len(adjudicaciones_80_0014) == 30
    renglon_1 = next(a for a in adjudicaciones_80_0014 if a["renglon"] == 1)
    assert renglon_1["proveedor"] == "MICROSULES ARGENTINA SA de SCIIA"
    assert renglon_1["cantidad"] == 480.0
    assert renglon_1["precio_unitario"] == 67400.0
    assert renglon_1["total"] == 32352000.0


def test_parse_dictamen_no_trae_cuit_directamente(adjudicaciones_80_0014):
    """El dictamen no tiene columna CUIT: ComprarClient.detalle_proceso la
    completa cruzando por nombre contra el cuadro comparativo. A nivel de
    parser puro, el campo queda vacío a propósito."""
    assert all(a["cuit"] == "" for a in adjudicaciones_80_0014)


def test_cruce_de_cuit_por_nombre_resuelve_todas_las_adjudicaciones(
        adjudicaciones_80_0014, ofertas_80_0014):
    cuit_by_proveedor = {o["proveedor"].strip().upper(): o["cuit"]
                          for o in ofertas_80_0014 if o.get("cuit")}
    resueltas = [cuit_by_proveedor.get(a["proveedor"].strip().upper(), "")
                 for a in adjudicaciones_80_0014]
    assert all(resueltas)   # ninguna quedó sin CUIT


# --- parse_busqueda_html -------------------------------------------------

def test_parse_busqueda_trae_las_columnas_de_la_grilla():
    filas = comprar.parse_busqueda_html(_read("busqueda_rubro_alimentos.html"))
    assert len(filas) == 12
    primera = filas[0]
    assert primera["numero"] == "132/61-0010-CDI19"
    assert primera["estado"] == "Adjudicado"
    assert primera["tipo_procedimiento"] == "Contratación Directa"
    assert primera["fecha_apertura"] == "16/09/2019 14:00 Hrs."
    assert primera["saf"].startswith("911 -")


def test_parse_busqueda_sin_grilla_devuelve_lista_vacia():
    assert comprar.parse_busqueda_html("<html><body>sin resultados</body></html>") == []


# --- helpers internos ----------------------------------------------------

@pytest.mark.parametrize("texto,cantidad,unidad", [
    ("480,00 UNIDAD", 480.0, "UNIDAD"),
    ("29.000,00 UNIDAD", 29000.0, "UNIDAD"),
    ("1,00 SERVICIO", 1.0, "SERVICIO"),
    ("", None, ""),
])
def test_parse_cantidad_unidad(texto, cantidad, unidad):
    assert comprar._parse_cantidad_unidad(texto) == (cantidad, unidad)


@pytest.mark.parametrize("fecha_texto,desde,hasta,esperado", [
    ("11/07/2025 10:00 Hrs.", None, None, True),
    ("11/07/2025 10:00 Hrs.", __import__("datetime").date(2025, 1, 1), None, True),
    ("11/07/2025 10:00 Hrs.", __import__("datetime").date(2026, 1, 1), None, False),
    ("11/07/2025 10:00 Hrs.", None, __import__("datetime").date(2024, 1, 1), False),
    ("sin fecha", __import__("datetime").date(2024, 1, 1), None, True),  # no se puede leer -> no descarta
])
def test_fecha_en_rango(fecha_texto, desde, hasta, esperado):
    assert comprar._fecha_en_rango(fecha_texto, desde, hasta) is esperado
