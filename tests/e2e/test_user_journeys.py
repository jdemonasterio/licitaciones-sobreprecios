"""Simulador end-to-end: cada test es UN journey de un usuario real del webui.

Patrones heredados (ver docs/simulador.md para el detalle):
  - telegram-transcribe-bot/ops/simulator: nombres de escenario claros, asertar
    sobre estado OBSERVABLE (DOM, HTTP, la base), no sobre texto exacto del LLM;
    limpiar lo que el propio test crea (ver `_borrar_reporte` mas abajo).
  - "el backend nunca resetea la pagina" + la revision de
    journeys (PLAN_user_journeys_review_20261010.md, R3-R8): una accion de
    background nunca debe anunciar exito sin chequear el resultado, nunca debe
    recargar la pagina de taquito, y un fallo debe mostrarse, no tragarse.

Correr: LIC_E2E=1 .venv/bin/python -m pytest tests/e2e -v   (o scripts/simular.py)
Sin LIC_E2E=1 esta suite entera se skippea (ver tests/e2e/conftest.py).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlsplit, parse_qs

import httpx
import pytest

import conftest  # mismo directorio: fixtures ya puestas por pytest en sys.path
from sobreprecios import reports

PROVEEDORES_MDS = [
    "COMPAÑÍA AMERICANA DE ALIMENTOS",
    "MAFRALAC",
    "S.A. LA SIBILA",
    "ALIMENTOS VIDA",
    "MILKAUT",
]


def _toast_text(page) -> str:
    page.wait_for_selector("#soft-toast", state="visible", timeout=15000)
    return page.locator("#soft-toast").inner_text()


def _no_scroll_horizontal(page) -> bool:
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def _wait_job_done(base_url: str, name: str, timeout: float = 20.0) -> dict:
    """Polea /jobs/<name> por FUERA del navegador hasta que termine.

    Hallazgo (ver docs/simulador.md / reporte final): el poller de base.html
    guarda `lastRunning[name]` para detectar la transicion corriendo->listo,
    pero un job offline de este tamano (6 renglones, sin red) puede arrancar
    Y terminar entre dos ciclos de poll (2-6s) sin que el poller lo vea nunca
    "corriendo": nunca pasa por `lastRunning[name] = true`, asi que el `else
    if` que dispara el refresco no se activa jamas y la pagina se queda
    mostrando el estado viejo hasta que alguien la recargue a mano. No se
    toca ese codigo (no es un test-side issue); se lo puentea sembrando la
    espera afuera del navegador y forzando el mismo refresh que haria el
    poller (`_force_soft_refresh`), para poder seguir probando el resultado
    real sin depender de esa carrera.
    """
    deadline = time.time() + timeout
    with httpx.Client(timeout=5) as c:
        while time.time() < deadline:
            st = c.get(f"{base_url}/jobs/{name}").json()
            if not st.get("running"):
                return st
            time.sleep(0.1)
    raise TimeoutError(f"el job {name!r} sigue corriendo despues de {timeout}s")


def _force_soft_refresh(page, mensaje: str | None) -> None:
    """El mismo refresh in-place que dispara el poller de base.html al ver un
    job terminado (`SoftNav.load(SoftNav.urlWithMessage(msg))`), invocado a
    mano para no depender de su timing (ver `_wait_job_done`)."""
    page.evaluate(
        "m => window.SoftNav.load(window.SoftNav.urlWithMessage(m))", mensaje or "Listo")


# ── 1. Inicio: KPIs, filtros, orden ──────────────────────────────────────────

def test_inicio_lista_licitaciones_con_kpis_y_filtros(page, seeded_app):
    page.goto("/")
    procesos_kpi = page.locator(".kpis .kpi").nth(0).locator(".value").inner_text()
    assert int(procesos_kpi) >= 2, f"KPI de procesos no refleja la semilla: {procesos_kpi!r}"
    assert page.locator("table tbody tr").count() == 3   # leche, azucar (programa) y yerba (gris)

    page.select_option('select[name="producto"]', "leche_polvo_entera")
    page.click('button:has-text("Filtrar")')
    page.wait_for_url("**/?*producto=leche_polvo_entera*")
    rows = page.locator("table tbody tr")
    assert rows.count() == 1
    assert "MINISTERIO DE DESARROLLO SOCIAL" in rows.first.inner_text()

    page.click('a:has-text("Limpiar")')
    page.wait_for_url("**/")
    assert page.locator("table tbody tr").count() == 3   # leche, azucar (programa) y yerba (gris)

    page.select_option('select[name="orden"]', "fecha")
    page.click('button:has-text("Filtrar")')
    page.wait_for_load_state("load")
    assert page.locator('select[name="orden"]').input_value() == "fecha"

    conftest.assert_clean(page)


# ── 2. Detalle: evidencia de como se calculo ────────────────────────────────

def test_detalle_licitacion_muestra_evidencia_y_calculo(page, seeded_app):
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    assert "MINISTERIO DE DESARROLLO SOCIAL" in page.locator("h1").inner_text()

    renglon_rows = page.locator('tr[data-soft-key^="reng:"]')
    assert renglon_rows.count() == 5
    # Orden: peor semaforo primero -> MILKAUT (553, el mas caro) sale rojo primero.
    assert "sem-rojo" in renglon_rows.first.inner_html()

    # "evidencia de mercado" (sin tildes) para no depender de como el editor/fuente
    # codifique "cómo"/"calculó" en la plantilla.
    page.locator("details summary", has_text="evidencia de mercado").first.click()
    opened = page.locator("details").first
    assert opened.get_attribute("open") is not None
    detalle_texto = opened.inner_text()
    assert "mayorista" in detalle_texto.lower()
    assert "seed_mayorista_a" in detalle_texto or "seed_mayorista_b" in detalle_texto

    conftest.assert_clean(page)


# ── 3. Revision humana: sin recargar, conserva scroll y nota ────────────────

def test_marcar_revision_no_recarga_pagina_conserva_estado(page, seeded_app):
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    page.evaluate("window.scrollTo(0, 250)")
    scroll_antes = page.evaluate("window.scrollY")
    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")

    nota = f"revisado por el simulador e2e {time.time()}"
    page.select_option('form[action$="/revision"] select[name="estado"]', "revisado")
    page.fill('form[action$="/revision"] textarea[name="nota"]', nota)
    page.click('form[action$="/revision"] button:has-text("Guardar")')

    assert "Marcada como revisado" in _toast_text(page)
    # El marcador de JS solo sobrevive si NO hubo un location.reload()/navegacion completa.
    assert page.evaluate("window.__e2e_marker") == marker, (
        "window.__e2e_marker se perdio: la pagina hizo un reload completo en vez de soft-nav")
    assert abs(page.evaluate("window.scrollY") - scroll_antes) < 150, "el scroll salto"
    assert page.locator('textarea[name="nota"]').input_value() == nota
    assert page.locator('select[name="estado"]').input_value() == "revisado"

    # Persistencia real (no solo una ilusion del lado del cliente): recargar de verdad.
    page.reload()
    assert page.locator('select[name="estado"]').input_value() == "revisado"
    assert page.locator('textarea[name="nota"]').input_value() == nota

    conftest.assert_clean(page)


# ── 4. Reportes: job en background, HTML + Excel + CSV ──────────────────────

def test_reportes_crea_reporte_background_y_descarga_html_xlsx_csv(page, seeded_app):
    titulo = f"E2E simulador {time.time():.0f}"
    html_path = xlsx_path = csv_path = None
    try:
        page.goto("/reportes")
        page.fill('input[name="titulo"]', titulo)
        page.select_option('select[name="producto"]', "leche_polvo_entera")
        page.click('button:has-text("Armar reporte")')
        assert "Armando el reporte" in _toast_text(page)

        estado = _wait_job_done(seeded_app.base_url, "reporte")
        _force_soft_refresh(page, estado.get("message"))

        fila = page.locator("tr", has_text=titulo)
        fila.locator('a:has-text("Ver / imprimir")').wait_for(timeout=10000)

        with page.context.expect_page() as nueva_info:
            fila.locator('a:has-text("Ver / imprimir")').click()
        reporte_page = nueva_info.value
        reporte_page.wait_for_load_state()
        cuerpo = reporte_page.locator("body").inner_text()
        assert "Como se calculo" in cuerpo or "Cómo se calculó" in cuerpo
        assert "Limitaciones" in cuerpo
        reporte_page.close()

        with page.expect_download() as dl_info:
            fila.locator('a:has-text("Excel")').click()
        xlsx_dl = dl_info.value
        xlsx_path = xlsx_dl.path()
        assert xlsx_path and xlsx_path.stat().st_size > 0

        with page.expect_download() as dl_info:
            fila.locator('a:has-text("CSV")').click()
        csv_dl = dl_info.value
        csv_path = csv_dl.path()
        assert csv_path and csv_path.stat().st_size > 0

        conftest.assert_clean(page)
    finally:
        _borrar_reporte_creado(seeded_app, titulo)


def _borrar_reporte_creado(seeded_app, titulo: str) -> None:
    """Limpieza (patron del simulador del bot: no dejar debris de lo que el test creo).

    reports.reportes_dir() NO respeta LIC_DB_PATH/LIC_CACHE_DIR (ver hallazgo en el
    reporte final): un reporte armado durante el e2e cae en el data/reportes/ REAL
    del repo. Se borra por su propio path (nunca construido a mano) y solo si cae
    adentro de reportes_dir(), igual que valida reports.archivo().
    """
    filas = conftest.read_db(seeded_app.db_path, "SELECT html_path FROM reportes WHERE titulo=?", (titulo,))
    root = reports.reportes_dir().resolve()
    for fila in filas:
        base = fila.get("html_path")
        if not base:
            continue
        from pathlib import Path
        for suf in (".html", ".xlsx", ".csv"):
            p = Path(base).with_suffix(suf)
            try:
                if p.resolve().is_relative_to(root) and p.exists():
                    p.unlink()
            except (OSError, ValueError):
                pass


# ── 5. Investigar ────────────────────────────────────────────────────────────

def test_investigar_texto_pegado_offline_extrae_los_5_proveedores(page, seeded_app):
    page.goto("/investigar")
    page.fill("#entrada", conftest.MDS_TEXTO)
    page.click('button:has-text("Investigar")')
    assert "Investigando" in _toast_text(page)

    estado = _wait_job_done(seeded_app.base_url, "investigar")
    _force_soft_refresh(page, estado.get("message"))

    primera_fila = page.locator("table tbody tr").first
    primera_fila.locator('a:has-text("Ver resultado")').wait_for(timeout=10000)
    # "Ver resultado" es un <a> comun sin target=_blank: navega en la misma pestania
    # (a diferencia de "Ver / imprimir" de Reportes, que si abre una nueva).
    primera_fila.locator('a:has-text("Ver resultado")').click()
    page.wait_for_url("**/licitacion/*")
    cuerpo = page.locator("body").inner_text()
    for proveedor in PROVEEDORES_MDS:
        assert proveedor in cuerpo, f"falta el proveedor {proveedor!r} en el resultado de investigar"

    conftest.assert_clean(page)


def test_investigar_entrada_invalida_muestra_mensaje_amigable(page, seeded_app):
    page.goto("/investigar")
    page.fill("#entrada", "Esto no es una licitacion, es solo un texto cualquiera de prueba "
                          "para ver que el sistema no se rompe con una entrada sin sentido.")
    page.click('button:has-text("Investigar")')
    assert "Investigando" in _toast_text(page)

    estado = _wait_job_done(seeded_app.base_url, "investigar")
    assert estado.get("error"), f"se esperaba que el job terminara en error, estado={estado}"
    _force_soft_refresh(page, f"Error: {estado['error']}")

    mensaje = page.locator("table tbody tr").first.inner_text()
    assert "error" in mensaje.lower()
    assert "Traceback" not in mensaje, "el mensaje de error muestra un stacktrace crudo, no un mensaje amigable"

    conftest.assert_clean(page)   # el fallo del job nunca debe traducirse en un 500 ni en consola rota


def test_investigar_subir_archivo_no_pdf_es_rechazado(page, seeded_app, tmp_path):
    falso_pdf = tmp_path / "no_es_pdf.txt"
    falso_pdf.write_text("hola, esto no es un pdf")

    page.goto("/investigar")
    page.set_input_files('input[name="archivo"]', str(falso_pdf))
    page.click('button:has-text("Investigar")')

    assert "tiene que ser un PDF" in _toast_text(page)
    conftest.assert_clean(page)


# ── 6. Datos y fuentes: recalcular sobreprecios offline ─────────────────────

def test_fuentes_recalcular_sobreprecios_offline_corre_en_background(page, seeded_app):
    page.goto("/fuentes")
    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")
    page.select_option('select[name="accion"]', "comparar")
    page.fill('input[name="desde"]', "2020-01-01")
    page.click('button:has-text("Ejecutar")')
    assert "arranc" in _toast_text(page).lower()

    # El toast final normalmente lo dispara el poller de base.html al ver el job
    # terminado; con un job offline tan corto (6 renglones, sin red) puede
    # terminar entre dos ciclos de poll y el poller nunca lo ve "corriendo"
    # (ver _wait_job_done). Se polea aparte y se fuerza el mismo refresh.
    estado = _wait_job_done(seeded_app.base_url, "datos")
    _force_soft_refresh(page, estado.get("message"))
    assert "renglones comparados" in page.locator("#soft-toast").inner_text()

    assert page.evaluate("window.__e2e_marker") == marker, (
        "la pagina de Fuentes recargo completo en vez de refrescar in place")
    # data-soft-keep: el campo "desde" que el usuario toco debe sobrevivir al swap
    assert page.locator('input[name="desde"]').input_value() == "2020-01-01"

    conftest.assert_clean(page)


# ── 7. Empresas, Analitica, Producto ────────────────────────────────────────

def test_empresas_lista_y_detalle_renderizan(page, seeded_app):
    page.goto("/empresas")
    assert "30-70779374-7" in page.locator("body").inner_text()

    page.goto("/empresa/30-70779374-7")
    assert page.locator("h1").inner_text().strip() != ""
    assert page.locator("text=Adjudicaciones").count() >= 1
    conftest.assert_clean(page)


def test_analitica_y_producto_renderizan_graficos_sin_errores(page, seeded_app):
    page.goto("/analitica")
    page.wait_for_selector("#ch-organismo")
    page.wait_for_selector("#ch-mes")

    page.goto("/producto/leche_polvo_entera")
    page.wait_for_selector("#ch-prod")

    conftest.assert_clean(page)


# ── 7b. Precios de programa: revisión humana excluye del comparador ────────

def test_precios_programa_marcar_no_excluye_del_comparador(page, seeded_app):
    # Antes de revisar nada: el único precio del renglón es el de "programa" (fallback), así
    # que el semáforo es amarillo y la nota dice de dónde viene la referencia.
    page.goto(f"/licitacion/{seeded_app.proceso_programa_id}")
    fila_reng = page.locator('tr[data-soft-key^="reng:"]').first
    assert "sem-amarillo" in fila_reng.inner_html()
    page.locator("details summary", has_text="evidencia de mercado").first.click()
    detalle_antes = page.locator("details").first.inner_text().lower()
    assert "precio de programa" in detalle_antes

    # En /precios-programa aparece esa misma fila, con el botón de revisión humana.
    page.goto(f"/precios-programa?edicion={seeded_app.programa_fuente}")
    fila = page.locator(f'tr[data-soft-key="fila:{seeded_app.programa_obs_key}"]')
    assert fila.count() == 1
    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")

    comentario = f"no corresponde, revisado por el simulador {time.time()}"
    fila.locator('textarea[name="nota"]').fill(comentario)
    fila.locator('button[value="no"]').click()

    assert "Marcada como no" in _toast_text(page)
    assert page.evaluate("window.__e2e_marker") == marker, (
        "window.__e2e_marker se perdió: /precios-programa recargó completo en vez de soft-nav")
    fila = page.locator(f'tr[data-soft-key="fila:{seeded_app.programa_obs_key}"]')
    assert "✗ No" in fila.inner_text()
    assert comentario in fila.inner_text()

    # Persiste de verdad (no solo del lado del cliente).
    page.reload()
    fila = page.locator(f'tr[data-soft-key="fila:{seeded_app.programa_obs_key}"]')
    assert "✗ No" in fila.inner_text() and comentario in fila.inner_text()

    # El POST ya recalculó los sobreprecios: sin esa fila no queda ninguna observación de
    # mercado para el renglón, así que vuelve a "sin datos" (gris), no a otro precio inventado.
    page.goto(f"/licitacion/{seeded_app.proceso_programa_id}")
    fila_reng = page.locator('tr[data-soft-key^="reng:"]').first
    assert "sem-gris" in fila_reng.inner_html()
    page.locator("details summary", has_text="evidencia de mercado").first.click()
    detalle_despues = page.locator("details").first.inner_text().lower()
    assert "sin observaciones de mercado" in detalle_despues

    conftest.assert_clean(page)


# ── 8. Seguridad: POST de otro origen rechazado ─────────────────────────────

def test_seguridad_post_de_otro_origen_es_rechazado(page, seeded_app):
    resp = page.request.post(
        seeded_app.base_url + "/fuentes/run",
        form={"accion": "comparar"},
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status == 403
    assert "error" in resp.json()


# ── 9. Layout responsive + dark mode ────────────────────────────────────────

def test_layout_mobile_390px_sin_scroll_horizontal(page, seeded_app):
    page.set_viewport_size({"width": 390, "height": 844})
    for ruta in ("/", f"/licitacion/{seeded_app.proceso_leche_id}", "/analitica", "/fuentes"):
        page.goto(ruta)
        assert _no_scroll_horizontal(page), f"scroll horizontal en {ruta} a 390px"
    conftest.assert_clean(page)


def test_dark_mode_mantiene_contraste(page, seeded_app):
    page.emulate_media(color_scheme="dark")
    page.goto("/")
    colores = page.evaluate(
        "() => { const b = getComputedStyle(document.body); return {bg: b.backgroundColor, fg: b.color}; }")
    assert colores["bg"] != colores["fg"], "fondo y texto quedan con el mismo color en modo oscuro"
    conftest.assert_clean(page)


# ── 10. Regresion: doble submit no duplica el job en background ────────────

def test_doble_post_concurrente_no_duplica_el_job_en_background(seeded_app):
    base = seeded_app.base_url
    with httpx.Client(timeout=15) as client:
        deadline = time.time() + 10
        while client.get(f"{base}/jobs/datos").json().get("running") and time.time() < deadline:
            time.sleep(0.2)

        antes = len(conftest.read_db(seeded_app.db_path,
                                     "SELECT id FROM corridas WHERE tipo='comparar'"))

        def post():
            r = client.post(f"{base}/fuentes/run", data={"accion": "comparar"}, follow_redirects=False)
            loc = r.headers.get("location", "")
            return unquote(parse_qs(urlsplit(loc).query).get("message", [""])[0])

        with ThreadPoolExecutor(max_workers=2) as pool:
            mensajes = list(pool.map(lambda _: post(), range(2)))

        arrancaron = [m for m in mensajes if "Arrancó" in m or "arranc" in m.lower()]
        ya_corria = [m for m in mensajes if m not in arrancaron]
        assert len(arrancaron) == 1, f"debia arrancar exactamente 1 corrida, arrancaron: {mensajes}"
        assert len(ya_corria) == 1, f"la segunda request debia avisar 'ya esta corriendo', mensajes: {mensajes}"

        deadline = time.time() + 15
        while client.get(f"{base}/jobs/datos").json().get("running") and time.time() < deadline:
            time.sleep(0.2)

        despues = len(conftest.read_db(seeded_app.db_path,
                                       "SELECT id FROM corridas WHERE tipo='comparar'"))
        assert despues - antes == 1, "el guard de _background.start no evito una segunda corrida"


# ── 11. Revision humana (MEJORAS #34) ───────────────────────────────────────

def test_revision_cola_lista_grises_y_rojos_y_filtra_por_origen(page, seeded_app):
    page.goto("/revision")
    cuerpo = page.locator("body").inner_text()
    assert "Yerba mate" in cuerpo   # el renglon gris de la semilla (sin referencia de variedad)
    assert "MILKAUT" in cuerpo or "553" in cuerpo or page.locator(".sem-rojo").count() >= 1

    page.select_option('form[action="/revision"] select[name="origen"]', "rojo")
    page.click('form[action="/revision"] button:has-text("Filtrar")')
    page.wait_for_url("**/revision?*origen=rojo*")
    assert page.locator("table tbody .sem-gris").count() == 0   # la cola filtrada, no el KPI de arriba

    conftest.assert_clean(page)


def test_revision_caso_corrige_producto_y_recalcula_en_el_lugar(page, seeded_app):
    """Caso gris (yerba, sin referencia de variedad confirmada): corregir el producto asignado a
    uno SIN esa traba de variedad (galletitas de agua) tiene que recalcular y reflejarse en el
    lugar, sin recargar la pagina (motivo_codigo pasa de atributo_desconocido a sin_datos: sigue
    gris, pero por otra razon — demuestra que el dato corregido se usa de verdad al comparar)."""
    renglon_id = conftest.read_db(
        seeded_app.db_path, "SELECT id FROM renglones WHERE proceso_id=?",
        (seeded_app.proceso_yerba_id,))[0]["id"]

    page.goto(f"/revision/{renglon_id}")
    por_que_antes = page.locator("dt:has-text('Por qué') + dd").inner_text()
    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")

    page.select_option('#form-revision select[name="producto_id"]', "galletitas_agua")
    page.fill("#form-revision textarea[name='nota']", "e2e: producto mal asignado, era otro")
    page.fill("#form-revision input[name='revisor']", "e2e")
    page.click('#form-revision button:has-text("Guardar y recalcular")')

    assert "recalculando" in _toast_text(page).lower()
    estado = _wait_job_done(seeded_app.base_url, f"revision_{renglon_id}")
    _force_soft_refresh(page, estado.get("message"))

    assert page.evaluate("window.__e2e_marker") == marker, (
        "la pagina de revision recargo completo en vez de refrescar en el lugar")
    assert page.locator('#form-revision select[name="producto_id"]').input_value() == "galletitas_agua"
    assert "Producto" in page.locator("body").inner_text()   # fila en "Correcciones vigentes"
    por_que_despues = page.locator("dt:has-text('Por qué') + dd").inner_text()
    assert por_que_despues != por_que_antes, "el cambio de producto no recalculo la comparacion"

    # Persistencia real: recargar de verdad.
    page.reload()
    assert page.locator('#form-revision select[name="producto_id"]').input_value() == "galletitas_agua"
    conftest.assert_clean(page)


def test_revision_validar_mercado_no_es_el_mismo_producto_degrada_el_rojo(page, seeded_app):
    """Marcar la observacion que forma el minimo confirmado como "no es el mismo producto" saca
    ESE renglon del rojo (ya no hay par corroborado) sin tocar los demas renglones del proceso."""
    renglon_id = conftest.read_db(
        seeded_app.db_path,
        "SELECT r.id FROM renglones r JOIN comparaciones k ON k.renglon_id=r.id "
        "WHERE r.proceso_id=? AND k.semaforo='rojo'", (seeded_app.proceso_leche_id,))[0]["id"]

    page.goto(f"/revision/{renglon_id}")
    assert "sem-rojo" in (page.locator(".sem").first.get_attribute("class") or "")
    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")

    fila = page.locator("tr", has_text="marca0")   # seed_mayorista_a, $457: forma el minimo confirmado
    fila.locator('select[name="juicio"]').select_option("no_es_mismo_producto")
    fila.locator('button:has-text("Guardar")').click()

    assert "recalculando" in _toast_text(page).lower()
    estado = _wait_job_done(seeded_app.base_url, f"revision_{renglon_id}")
    _force_soft_refresh(page, estado.get("message"))

    assert page.evaluate("window.__e2e_marker") == marker, "la pagina recargo completo"
    assert "sem-rojo" not in (page.locator(".sem").first.get_attribute("class") or ""), (
        "sin el par corroborado (excluido) el renglon ya no deberia dar rojo")
    assert "No es el mismo producto" in page.locator("body").inner_text()

    conftest.assert_clean(page)


def test_doc_host_no_permitido_muestra_error_en_castellano(page, seeded_app):
    resp = page.request.get(seeded_app.base_url + "/doc", params={"url": "https://evil.example.com/x.pdf"})
    assert resp.status == 400
    assert "no está autorizado" in resp.text()


# ── 12. Validacion in-line de renglones en /licitacion/<id> ────────────────

def test_licitacion_validacion_inline_persiste_tras_navegar(page, seeded_app):
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    fila_detalle = page.locator('tr[data-soft-key^="reng-det:"]').first
    fila_detalle.locator("summary").click()

    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")
    fila_detalle.locator('form[action^="/renglon/"] button:has-text("✓ OK")').first.click()

    assert "Validación guardada" in _toast_text(page)
    assert page.evaluate("window.__e2e_marker") == marker, (
        "la pagina de licitacion recargo completo en vez de refrescar en el lugar")

    page.goto("/")
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    cuerpo = page.locator("body").inner_text()
    assert "validado por" in cuerpo or "revisión parcial" in cuerpo, (
        "la validacion in-line no persistio tras navegar")

    conftest.assert_clean(page)
