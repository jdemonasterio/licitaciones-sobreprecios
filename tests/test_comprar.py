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


@pytest.fixture(scope="module")
def desestimadas_80_0014():
    return comprar.parse_ofertas_desestimadas_html(_read("dictamen_80-0014-LPU25.html"))


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


def test_parse_detalle_expone_actos_administrativos(detalle_80_0014):
    """Pedido de jdemonasterio 2026-10-12: autorización de pliego/llamado y adjudicación
    tienen que verse (con número y fecha), sin importar si es un proceso viejo o nuevo."""
    actos = detalle_80_0014["actos_administrativos"]
    assert len(actos) == 3
    tipos = {a["documento"] for a in actos}
    assert tipos == {"Autorización pliego", "Autorización llamado", "Adjudicación y OC"}
    adjudicacion = next(a for a in actos if a["documento"] == "Adjudicación y OC")
    assert adjudicacion["numero_especial"] == "RESOL-2026-346-MS"
    assert adjudicacion["numero_gde"] == "RS-2026-21322988-APN-MS"
    assert adjudicacion["fecha"] == "04/03/2026"


def test_parse_detalle_actos_administrativos_en_proceso_viejo_2018_2020():
    """Mismo parser contra un proceso viejo de verdad (14/1-0424-CDI18, CNEA, pliego
    autorizado en 2019 y adjudicado en 2020): el control `UC_ActosAdministrativos` es
    estable entre versiones del sitio, no autonumerado (verificado en vivo 2026-10-12 contra
    6 procesos 2018-2026, ver DESIGN_sobreprecios.md)."""
    d = comprar.parse_detalle_html(_read("detalle_14_1-0424-CDI18.html"))
    actos = d["actos_administrativos"]
    assert len(actos) == 3
    adjudicacion = next(a for a in actos if a["documento"] == "Adjudicación y OC")
    assert adjudicacion["fecha"] == "20/03/2020"
    assert adjudicacion["numero_especial"] == "DI-2020-116-GAYF#CNEA"
    assert d["plazo_pago_dias"] is None   # tampoco en el proceso viejo: mismo límite conocido


def test_parse_detalle_sin_plazo_de_pago_estructurado(detalle_80_0014):
    """Regresión 2026-10-12: el texto plano de la página pone "Plazo mantenimiento de la
    oferta: 60 Días corridos" justo antes de la label (no relacionada) "Requiere pago", y una
    ventana de proximidad simétrica los confundía (falso "plazo de pago: 60 días"). En los
    procesos de COMPR.AR probados no hay campo de plazo de pago estructurado: tiene que dar
    "sin dato", nunca inventar 60."""
    assert detalle_80_0014["plazo_pago_dias"] is None
    assert detalle_80_0014["plazo_pago_texto"] is None


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


# --- parse_ofertas_desestimadas_html -------------------------------------

def test_parse_desestimadas_trae_causa_por_renglon_y_proveedor(desestimadas_80_0014):
    """80-0014-LPU25 tiene 5 oferentes desestimados, 21 filas renglón+causa en total (contado
    a mano contra el fixture, pedido de jdemonasterio 2026-10-12: 'parsear las desestimadas
    con su causa')."""
    assert len(desestimadas_80_0014) == 21
    por_proveedor = {}
    for d in desestimadas_80_0014:
        por_proveedor.setdefault(d["proveedor"], []).append(d["renglon"])
    assert por_proveedor["MICROSULES ARGENTINA SA de SCIIA"] == [25]
    assert sorted(por_proveedor["DROGUERIA GENESIS S.A"]) == [4, 23, 33, 34, 37, 39]
    assert all(d["cuit"].isdigit() for d in desestimadas_80_0014)


def test_parse_desestimadas_renglon_25_microsules_causa_economica(desestimadas_80_0014):
    """Renglón 25: la grilla del dictamen da la causa económica completa (cita el informe
    de precios), no un texto vacío ni truncado."""
    fila = next(d for d in desestimadas_80_0014 if d["renglon"] == 25
                and d["proveedor"].startswith("MICROSULES"))
    assert "informe de precios" in fila["causa"].lower()
    assert fila["cuit"] == "30519887335"


def test_parse_desestimadas_sin_seccion_devuelve_vacio():
    assert comprar.parse_ofertas_desestimadas_html("<html><body>sin dictamen</body></html>") == []


def test_parse_desestimadas_usa_evaluacion_administrativa_cuando_el_renglon_no_tiene_causa_propia():
    """Cuando la causa de un renglón puntual viene vacía en la grilla (ambas columnas de
    evaluación en blanco), tiene que caer a la evaluación administrativa de TODA la oferta
    (gvEvaluacionDescartada) en vez de quedar sin motivo."""
    html = """
    <h3>Ofertas desestimadas</h3>
    <span id="x_rptOfertasNoCumplieron_ctl00_UCProveedorRenglonDescartadoBastrap_lblProveedor">ACME SA 30111111118</span>
    <table id="x_rptOfertasNoCumplieron_ctl00_UCProveedorRenglonDescartadoBastrap_gvEvaluacionDescartada">
    <tbody><tr><th>Descripción</th><th>Evaluación Técnica</th><th>Evaluación Económica</th><th>Evaluación Administrativa</th></tr>
    <tr><td>Requisito: Declaración jurada</td><td></td><td></td><td>No presenta la declaración jurada exigida.</td></tr>
    </tbody></table>
    <table id="x_rptOfertasNoCumplieron_ctl00_UCProveedorRenglonDescartadoBastrap_gvRenglonesDescartados">
    <tbody><tr><th>Renglón</th><th>Alternativa</th><th>Descripción</th><th>Tipo</th><th>Evaluación Técnica</th><th>Evaluación Económica</th></tr>
    <tr><td>3</td><td>1</td><td>producto X</td><td>Especificación</td><td></td><td></td></tr>
    </tbody></table>
    """
    out = comprar.parse_ofertas_desestimadas_html(html)
    assert len(out) == 1
    assert out[0]["renglon"] == 3 and out[0]["proveedor"] == "ACME SA" and out[0]["cuit"] == "30111111118"
    assert "declaración jurada" in out[0]["causa"].lower()


# --- parse_plazo_pago (sin red: texto libre, no hay campo estructurado en COMPR.AR) ----

@pytest.mark.parametrize("texto,dias,contiene", [
    ("El pago se efectuara dentro de los treinta (30) dias corridos de la conformidad "
     "definitiva de la factura.", 30, "30 dias corridos"),
    ("La Administracion abonara dentro de los 15 dias habiles de recibida la factura "
     "conformada.", 21, "15 dias habiles"),   # 15 * 1.4 = 21, convertido a corridos
    ("El pago sera contra entrega de la mercaderia en el domicilio indicado.", 0, "contra entrega"),
    ("Esta licitacion no menciona nada sobre el pago.", None, None),
    ("Plazo mantenimiento de la oferta 60 Dias corridos Acto de apertura Requiere pago No "
     "Otras condiciones Genera Recursos No", None, None),   # regresión: no es plazo de pago
])
def test_parse_plazo_pago(texto, dias, contiene):
    resultado_dias, resultado_texto = comprar.parse_plazo_pago(texto)
    assert resultado_dias == dias
    if contiene is None:
        assert resultado_texto is None
    else:
        assert contiene in resultado_texto


def test_parse_plazo_pago_vacio_no_rompe():
    assert comprar.parse_plazo_pago("") == (None, None)
    assert comprar.parse_plazo_pago(None) == (None, None)


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
