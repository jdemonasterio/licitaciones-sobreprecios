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
from sobreprecios import db as db_mod
from sobreprecios import reports
from sobreprecios.pipeline import compare as compare_mod
from sobreprecios.pipeline import match as match_mod

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
    # codifique "cómo"/"calculó" en la plantilla. Se busca el <details> que CONTIENE ese summary
    # (no "el primero de la pagina": la sintesis/#49 agrego otros <details> de visor de
    # documentos mas arriba, con menos prioridad que esta evidencia).
    opened = page.locator("details", has=page.locator("summary", has_text="evidencia de mercado")).first
    opened.locator("summary").click()
    assert opened.get_attribute("open") is not None
    detalle_texto = opened.inner_text()
    assert "mayorista" in detalle_texto.lower()
    assert "seed_mayorista_a" in detalle_texto or "seed_mayorista_b" in detalle_texto

    conftest.assert_clean(page)


# ── 3. Revision humana: sin recargar, conserva scroll y nota ────────────────

def test_marcar_revision_no_recarga_pagina_conserva_estado(page, seeded_app):
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    # La sintesis de arriba (#49.1) movio la tarjeta de revision mas abajo: se lleva el boton a
    # la vista primero (como haria una persona) para que el click no tenga que auto-scrollear y
    # mover el "antes" que se compara abajo.
    page.locator('form[action$="/revision"] button:has-text("Guardar")').scroll_into_view_if_needed()
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
    # Nombre legible del tipo de entrada (MEJORAS #49.7): nunca el id interno "texto" solo.
    assert "texto pegado" in primera_fila.inner_text().lower()
    # "Ver resultado" es un <a> comun sin target=_blank: navega en la misma pestania
    # (a diferencia de "Ver / imprimir" de Reportes, que si abre una nueva).
    primera_fila.locator('a:has-text("Ver resultado")').click()
    page.wait_for_url("**/licitacion/*")
    cuerpo = page.locator("body").inner_text()
    for proveedor in PROVEEDORES_MDS:
        assert proveedor in cuerpo, f"falta el proveedor {proveedor!r} en el resultado de investigar"
    # Qué se importó y qué prueba (MEJORAS #49.5, item 1a): nunca decir "pagó".
    assert "Qué se importó" in cuerpo and "pagó" not in cuerpo.lower()

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


# ── 6b. Impacto de un cambio: foto antes/después de un recálculo ───────────

def test_impacto_lista_la_corrida_de_recalcular_y_abre_su_detalle(page, seeded_app):
    """MEJORAS #49.8: el botón "Recalcular sobreprecios" de Fuentes dispara un recálculo que
    queda registrado en /impacto con una foto de antes. El journey: disparar el recálculo,
    entrar a /impacto, ver la corrida más reciente y abrir su detalle sin que rompa (haya o no
    cambios: el seed no modifica reglas, así que lo esperable es "sin impacto visible")."""
    page.goto("/fuentes")
    page.select_option('select[name="accion"]', "comparar")
    page.click('button:has-text("Ejecutar")')
    assert "arranc" in _toast_text(page).lower()
    _wait_job_done(seeded_app.base_url, "datos")

    page.goto("/impacto")
    primera_fila = page.locator("table tbody tr").first
    primera_fila.wait_for(timeout=10000)
    assert "fuentes:comparar" in primera_fila.inner_text()

    primera_fila.locator('a:has-text("Ver qué cambió")').click()
    page.wait_for_url("**/impacto/*")
    cuerpo = page.locator("body").inner_text()
    assert "Renglones en la foto" in cuerpo
    # Recalcular sin tocar reglas ni catálogo no debería inventar cambios.
    assert "Ningún renglón" in cuerpo or "Publicables revocados" in cuerpo

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


# ── 13. Sintesis arriba del detalle + compuerta de publicacion (MEJORAS #49.1/#49.4) ────────

def test_licitacion_muestra_sintesis_y_que_falta_para_publicar(page, seeded_app):
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    sintesis = page.locator('[data-soft-key="resumen"]')
    assert sintesis.count() == 1
    texto = sintesis.inner_text()
    assert "Monto total del proceso" in texto
    assert "Monto de los renglones analizados" in texto
    assert "Qué falta para publicar" in texto
    # La licitacion sembrada arranca "pendiente": tiene que listar ESO como pendiente.
    assert "publicable" in texto.lower()

    cuerpo = page.locator("body").inner_text()
    assert "Guardar" in cuerpo and "Validé esta licitación" in cuerpo and "revoca sola" in cuerpo

    conftest.assert_clean(page)


# ── 14. Comprobante de calculo por renglon, imprimible (MEJORAS #49.2) ─────────────────────

def test_comprobante_de_renglon_muestra_dato_normalizacion_y_escenarios(page, seeded_app):
    renglon_id = conftest.read_db(
        seeded_app.db_path, "SELECT id FROM renglones WHERE proceso_id=? ORDER BY id LIMIT 1",
        (seeded_app.proceso_leche_id,))[0]["id"]

    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    with page.context.expect_page() as nueva_info:
        page.locator('a[href="/renglon/%d/comprobante"]' % renglon_id).first.click()
    comp = nueva_info.value
    comp.wait_for_load_state()
    cuerpo = comp.locator("body").inner_text()
    assert "Comprobante de cálculo" in cuerpo
    assert "Apertura de ofertas" in cuerpo
    assert "Escenario de 1 mes de plazo" in cuerpo and "Escenario de 2 meses de plazo" in cuerpo and "habilita rojo" in cuerpo
    assert "Explicaciones alternativas revisadas" in cuerpo
    comp.close()


# ── 15. Armar expediente para periodista (MEJORAS #49.5) ───────────────────────────────────

def test_armar_expediente_para_periodista_se_arma_y_se_descarga(page, seeded_app):
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    page.fill('form[action$="/expediente"] input[name="titulo"]', "Expediente e2e leche")
    page.click('form[action$="/expediente"] button:has-text("Armar expediente para periodista")')
    assert "armando" in _toast_text(page).lower() or "expediente" in _toast_text(page).lower()

    estado = _wait_job_done(seeded_app.base_url, f"expediente_{seeded_app.proceso_leche_id}")
    assert not estado.get("error"), estado
    _force_soft_refresh(page, estado.get("message"))

    fila = page.locator("tr", has_text="Expediente e2e leche")
    fila.locator('a:has-text("Ver")').wait_for(timeout=10000)
    with page.context.expect_page() as nueva_info:
        fila.locator('a:has-text("Ver")').click()
    exp_page = nueva_info.value
    exp_page.wait_for_load_state()
    cuerpo = exp_page.locator("body").inner_text()
    assert "Qué se compró y qué se encontró" in cuerpo
    assert "Cómo se construyó esta comparación" in cuerpo
    exp_page.close()

    with page.expect_download() as dl_info:
        fila.locator('a:has-text("ZIP")').click()
    zip_dl = dl_info.value
    zip_path = zip_dl.path()
    assert zip_path and zip_path.stat().st_size > 0

    conftest.assert_clean(page)


# ── 16. Checklist de explicaciones alternativas por caso (MEJORAS #49.6) ───────────────────

def test_explicaciones_alternativas_se_guardan_sin_recargar(page, seeded_app):
    renglon_id = conftest.read_db(
        seeded_app.db_path, "SELECT id FROM renglones WHERE proceso_id=?",
        (seeded_app.proceso_yerba_id,))[0]["id"]

    page.goto(f"/revision/{renglon_id}")
    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")

    fila = page.locator("tr", has_text="Logística o lugar de entrega distinto")
    fila.locator('select[name="estado"]').select_option("verificado")
    nota = f"se entrego en otra provincia {time.time()}"
    fila.locator('input[name="nota"]').fill(nota)
    fila.locator('button:has-text("Guardar")').click()

    assert "guardada" in _toast_text(page).lower()
    assert page.evaluate("window.__e2e_marker") == marker, (
        "la pagina de revision recargo completo en vez de refrescar en el lugar")

    page.reload()
    fila = page.locator("tr", has_text="Logística o lugar de entrega distinto")
    assert nota in fila.inner_text()
    assert "Verificado" in fila.inner_text()

    conftest.assert_clean(page)


# ── 17. Cola de revision: colores en vivo coinciden con la home ────────────────────────────

def test_revision_cola_colores_en_vivo_coinciden_con_home(page, seeded_app):
    page.goto("/")
    rojos_home = page.locator(".kpis .kpi .sem-rojo").first.inner_text()

    page.goto("/revision")
    rojo_tile = page.locator('.kpis .kpi:has-text("Rojo") .sem-rojo').first.inner_text()
    assert rojo_tile == rojos_home

    cuerpo = page.locator("body").inner_text()
    assert "Resolver" in cuerpo and "Publicable" in cuerpo
    assert "1 kg" in cuerpo or "1,0 kg" in cuerpo   # resumen corto con el envase

    conftest.assert_clean(page)


# ── 18. Backup / restauracion sin consola (MEJORAS #49.8) ──────────────────────────────────

def test_fuentes_descarga_copia_de_seguridad(page, seeded_app):
    page.goto("/fuentes")
    assert page.locator('a:has-text("Descargar copia de seguridad")').count() == 1
    with page.expect_download() as dl_info:
        page.click('a:has-text("Descargar copia de seguridad")')
    dl = dl_info.value
    path = dl.path()
    assert path and path.stat().st_size > 0
    assert open(path, "rb").read(16) == b"SQLite format 3\x00"


# ── 19. Tablas interactivas: orden, filtro y persistencia (tablas.js) ──────────────────────

def _valores_columna_licitaciones(page, idx):
    """data-valor (numero crudo) de la columna `idx` para cada fila, en el orden actual del DOM.
    None para las celdas vacias. Se lee el dato crudo (nunca una fila puntual por posicion):
    otros tests de esta misma corrida comparten la base (session-scoped, ver conftest.seeded_app)
    y pueden agregar procesos o recalcular los existentes antes de que corra este test."""
    return page.evaluate(
        "(idx) => Array.from(document.querySelectorAll('table[data-tabla-id=\"licitaciones\"] tbody tr'))"
        ".map(tr => { const v = tr.children[idx].getAttribute('data-valor'); "
        "return (v === '' || v === null) ? null : parseFloat(v); })", idx)


def test_tabla_licitaciones_ordena_asc_desc_y_deja_vacios_al_final(page, seeded_app):
    """Click en un encabezado numerico ordena por el dato crudo (nunca el texto formateado) y
    alterna asc/desc; en "Peor brecha", "sin comparacion" (yerba, sin dato) queda SIEMPRE al
    final en los dos sentidos (regla "empty sorts last" de tablas.js)."""
    page.goto("/")
    tabla = page.locator('table[data-tabla-id="licitaciones"]')

    # Monto (columna 6): nunca es nulo (COALESCE a 0 en la consulta): sirve para probar que el
    # orden de verdad se invierte, sin depender de datos que OTRO test de esta corrida pueda
    # haber recalculado (p.ej. "marcar precio de programa como no corresponde" deja a azucar
    # sin comparacion, lo que le borraria su "Peor brecha").
    boton_monto = tabla.locator("thead th", has_text="Monto").locator("button.tabla-th-btn")
    boton_monto.click()
    asc = _valores_columna_licitaciones(page, 6)
    assert asc == sorted(asc), f"Monto ascendente no quedo ordenado: {asc}"
    boton_monto.click()
    desc = _valores_columna_licitaciones(page, 6)
    assert desc == sorted(desc, reverse=True), f"Monto descendente no quedo ordenado: {desc}"
    assert len(set(asc)) >= 2, "la semilla deberia tener montos distintos (leche vs azucar/yerba)"

    # Peor brecha (columna 7): puede tener vacios (gris/sin comparacion) — yerba siempre es uno,
    # pase lo que pase con otros procesos de la corrida.
    boton_brecha = tabla.locator("thead th", has_text="Peor brecha").locator("button.tabla-th-btn")
    boton_brecha.click()
    assert tabla.locator("thead th", has_text="Peor brecha").get_attribute("aria-sort") == "ascending"
    fila_yerba = tabla.locator("tbody tr", has_text="ORGANISMO SEED YERBA")
    assert fila_yerba.locator("td").nth(7).get_attribute("data-valor") == ""
    valores_asc = _valores_columna_licitaciones(page, 7)
    no_nulos = [v for v in valores_asc if v is not None]
    assert valores_asc == no_nulos + [None] * (len(valores_asc) - len(no_nulos)), (
        f"los vacios no quedaron al final (ascendente): {valores_asc}")

    boton_brecha.click()
    assert tabla.locator("thead th", has_text="Peor brecha").get_attribute("aria-sort") == "descending"
    valores_desc = _valores_columna_licitaciones(page, 7)
    no_nulos2 = [v for v in valores_desc if v is not None]
    assert valores_desc == no_nulos2 + [None] * (len(valores_desc) - len(no_nulos2)), (
        f"los vacios no quedaron al final (descendente): {valores_desc}")

    conftest.assert_clean(page)


def test_tabla_licitaciones_filtra_semaforo_por_checklist(page, seeded_app):
    """El boton-embudo del encabezado "Semaforo" abre un menu de checkboxes; destildar un valor
    oculta esas filas y actualiza "Mostrando X de Y filas". Usa la cuenta que el propio panel
    muestra por valor (en vez de un total fijo): otros tests de esta corrida pueden haber
    agregado procesos antes de este."""
    page.goto("/")
    tabla = page.locator('table[data-tabla-id="licitaciones"]')
    total = tabla.locator("tbody tr").count()
    fila_yerba = tabla.locator("tbody tr", has_text="ORGANISMO SEED YERBA")
    assert fila_yerba.is_visible()

    page.get_by_role("button", name="Filtrar por Semáforo").click()
    panel = page.locator(".tabla-filtro-panel")
    assert panel.is_visible()
    item_gris = panel.locator(".tabla-filtro-item", has_text="Sin comparación")
    cuenta_gris = int(item_gris.locator(".muted.small").inner_text().strip(" ()"))
    item_gris.locator("input[type=checkbox]").uncheck()

    esperado = total - cuenta_gris
    assert tabla.locator("tbody tr:visible").count() == esperado
    assert f"Mostrando {esperado} de {total} filas" in page.locator(".tabla-status").inner_text()
    assert not fila_yerba.is_visible()

    page.keyboard.press("Escape")
    assert not panel.is_visible()
    page.get_by_role("button", name="Limpiar filtros").click()
    assert tabla.locator("tbody tr:visible").count() == total
    assert fila_yerba.is_visible()

    conftest.assert_clean(page)


def test_tabla_renglones_orden_sobrevive_a_refresco_softnav(page, seeded_app):
    """Ordenar la tabla de renglones y despues guardar la revision (soft-nav, misma pagina): el
    orden elegido se vuelve a aplicar solo tras el refresco en el lugar."""
    page.goto(f"/licitacion/{seeded_app.proceso_leche_id}")
    tabla = page.locator('table[data-tabla-id="renglones"]')
    tabla.locator("thead th", has_text="Exceso").locator("button.tabla-th-btn").click()
    assert tabla.locator("thead th", has_text="Exceso").get_attribute("aria-sort") == "ascending"
    orden_antes = tabla.locator("tbody tr[data-soft-key^='reng:']").all_inner_texts()

    marker = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker!r}")
    page.locator('form[action$="/revision"] textarea[name="nota"]').fill(f"nota soft-nav {time.time()}")
    page.locator('form[action$="/revision"] button:has-text("Guardar")').click()
    _toast_text(page)
    assert page.evaluate("window.__e2e_marker") == marker, "la pagina recargo completo (no fue soft-nav)"

    tabla = page.locator('table[data-tabla-id="renglones"]')
    orden_despues = tabla.locator("tbody tr[data-soft-key^='reng:']").all_inner_texts()
    assert orden_antes == orden_despues, "el orden de los renglones no sobrevivio al refresco en el lugar"
    assert tabla.locator("thead th", has_text="Exceso").get_attribute("aria-sort") == "ascending"

    conftest.assert_clean(page)


def test_tabla_filtro_mobile_390px_sin_scroll_horizontal(page, seeded_app):
    """El menu desplegable de un filtro categorico, abierto a 390px, no genera scroll horizontal
    (el panel es position:fixed y se acomoda al ancho de pantalla)."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("/")
    page.get_by_role("button", name="Filtrar por Semáforo").click()
    assert page.locator(".tabla-filtro-panel").is_visible()
    assert _no_scroll_horizontal(page), "scroll horizontal a 390px con el filtro de Semaforo abierto"
    conftest.assert_clean(page)


# ── 20. Journey de punta a punta: corregir → recalcular → publicar → revocar solo (MEJORAS #50) ──
#
# Sembrado A MITAD DE SESION (no en conftest._seed): otros journeys de este archivo dependen de
# conteos fijos sobre la semilla original (p.ej. "3 filas en la home", "5 renglones de leche") y
# agregar un 4to proceso ahi arriba los rompe. En cambio, este test crea su propio proceso+renglon
# en caliente (misma base/servidor de la sesion, con el mismo codigo real que usa conftest._seed:
# db.upsert + match.match_renglones + compare.run) DESPUES de que todos los demas tests ya corrieron
# (es el ultimo del archivo, y esta suite no usa un plugin de orden aleatorio: pytest.ini no declara
# ninguno y la corrida es siempre top-to-bottom), asi que no le cambia el piso a nadie. Todo se busca
# por id/texto propio (nunca por conteos), como indica el docstring de arriba.

def _sembrar_caso_variedad_mal_asignada(db_path) -> tuple[int, int]:
    """Un renglon de "leche en polvo entera" SIN ninguna palabra de variedad en la descripcion
    (la compra real exige la fortificada por Ley 25.459, pero el renglon no lo dice): el
    comparador no puede decidir sola la variedad, cae a la variante_default "comun" y, como el
    mercado sembrado para leche_polvo_entera (conftest._seed, 2021-06-07) es todo "fortificada",
    queda gris "sin_referencia_variante" — el mismo tipo de error (producto/variedad mal
    asignada) que ya cubre el journey de la yerba, pero en un caso propio y aislado.

    Precio 490 (el mismo que ya usa conftest._seed para COMPAÑÍA AMERICANA en el proceso de
    leche real: ahi da "verde" contra el minimo corroborado 460): elegido A PROPOSITO por debajo
    del umbral de rojo (20%) para que el gris de "variedad sin referencia" NO escale solo a
    "amarillo: equivalencia por verificar" (pipeline/compare.py, bloqueo + sp>=umbral_rojo_pct ->
    amarillo aunque la variedad no este verificada) — con un precio mas alto el gris inicial de
    este journey hubiera sido amarillo, no gris, por esa regla aparte.

    Misma fecha (2021-06-07) que esas 4 observaciones mayoristas YA sembradas (seed_mayorista_a/b,
    457/460/480/495) para poder comparar gap=0 en cuanto se corrija la variedad, sin inventar
    datos de mercado nuevos. Devuelve (proceso_id, renglon_id)."""
    with db_mod.session(db_path) as conn:
        proceso_id = db_mod.upsert(conn, "procesos", {
            "fuente": "bo_primera", "external_id": "e2e/journey/variedad1",
            "numero": "99-7777-XXXX", "organismo": "ORGANISMO SEED E2E JOURNEY",
            "tipo_procedimiento": "licitacion_publica",
            "fecha_adjudicacion": "2021-06-07", "fecha_publicacion": "2021-06-07",
            "monto_total": 98000000.0, "estado_revision": "pendiente",
            "created_at": db_mod.now_iso(), "updated_at": db_mod.now_iso(),
        }, ("fuente", "external_id"))
        renglon_id = db_mod.upsert(conn, "renglones", {
            "proceso_id": proceso_id, "nro_renglon": 1,
            "descripcion": "Leche en polvo entera instantanea, libre de gluten",
            "cantidad": 200000.0, "unidad_compra": "kilogramos",
            "precio_unitario": 490.0, "monto_total": 98000000.0,
            "proveedor": "PROVEEDOR E2E JOURNEY SRL", "proveedor_cuit": "30-99999999-4",
            "external_key": "e2e:journey:variedad:1",
        }, ("proceso_id", "external_key"))
        match_mod.match_renglones(conn, use_llm=False)   # solo_pendientes=True: no toca lo ya matcheado
        compare_mod.run(conn, [proceso_id])
    return proceso_id, renglon_id


def test_journey_corregir_recalcular_publicar_y_revocar_solo(page, seeded_app):
    """De punta a punta (MEJORAS #50): abrir un caso con un error sembrado (variedad mal
    asignada), corregirlo SIN recargar la pagina, ver que se recalculo en el lugar (color/brecha
    cambian a lo esperado), marcar la licitacion "publicable" y verla como tal en la lista y en
    /impacto, y despues invalidar el calculo (otra correccion) para comprobar que el "publicable"
    se revoca SOLO — y que, aunque el calculo vuelva a ser idéntico al aprobado, nunca se reafirma
    solo (sobreprecios/revision.py, sobreprecios/impacto.py)."""
    proceso_id, renglon_id = _sembrar_caso_variedad_mal_asignada(seeded_app.db_path)

    # a) Abrir el caso: gris, por una variedad que el comparador no puede decidir solo.
    page.goto(f"/revision/{renglon_id}")
    assert "sem-gris" in (page.locator(".sem").first.get_attribute("class") or "")
    por_que_antes = page.locator("dt:has-text('Por qué') + dd").inner_text()

    # b) Corregir la variedad EN EL LUGAR (soft-nav: nunca una navegacion completa).
    marker1 = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker1!r}")
    page.select_option('#form-revision select[name="variante"]', "fortificada_hierro_zinc_c")
    page.fill("#form-revision textarea[name='nota']", "e2e: variedad mal asignada, era fortificada (Ley 25.459)")
    page.fill("#form-revision input[name='revisor']", "e2e")
    page.click('#form-revision button:has-text("Guardar y recalcular")')
    assert "recalculando" in _toast_text(page).lower()

    estado1 = _wait_job_done(seeded_app.base_url, f"revision_{renglon_id}")
    assert not estado1.get("error"), estado1
    assert "revocad" not in (estado1.get("message") or "").lower(), (
        "nada deberia revocarse todavia: el caso nunca estuvo publicable")
    _force_soft_refresh(page, estado1.get("message"))

    # c) El recalculo se ve EN EL LUGAR: el marcador de JS sobrevive (nunca hubo reload completo),
    # la correccion quedo precargada, y el color pasa de gris a verde (490 esta en linea con el
    # minimo corroborado de mercado, 460).
    assert page.evaluate("window.__e2e_marker") == marker1, (
        "la pagina de revision recargo completo en vez de refrescar en el lugar")
    assert page.locator('#form-revision select[name="variante"]').input_value() == "fortificada_hierro_zinc_c"
    assert "sem-verde" in (page.locator(".sem").first.get_attribute("class") or "")
    por_que_despues = page.locator("dt:has-text('Por qué') + dd").inner_text()
    assert por_que_despues != por_que_antes, "el cambio de variedad no recalculo la comparacion"

    # d1) Esa corrida aparece en /impacto, identificada por el renglon propio (nunca por posicion:
    # otros journeys de esta sesion ya agregaron corridas antes que esta).
    page.goto("/impacto")
    fila_impacto = page.locator("tr", has_text=f"revision:{renglon_id}")
    fila_impacto.wait_for(timeout=10000)
    fila_impacto.locator('a:has-text("Ver qué cambió")').click()
    page.wait_for_url("**/impacto/*")
    fila_detalle = page.locator("tr", has=page.locator(f'a[href="/revision/{renglon_id}"]'))
    assert fila_detalle.count() == 1
    assert "sem-gris" in (fila_detalle.locator(".sem").nth(0).get_attribute("class") or "")
    assert "sem-verde" in (fila_detalle.locator(".sem").nth(1).get_attribute("class") or "")
    assert "revocado" not in fila_detalle.inner_text().lower(), "todavia no era publicable: nada que revocar"

    # d2) Marcar la licitacion "publicable" (el mismo gate de sobreprecios/revision.py).
    page.goto(f"/licitacion/{proceso_id}")
    page.locator('form[action$="/revision"] button:has-text("Guardar")').scroll_into_view_if_needed()
    page.select_option('form[action$="/revision"] select[name="estado"]', "publicable")
    page.fill('form[action$="/revision"] textarea[name="nota"]', "e2e: caso revisado, publicable")
    page.click('form[action$="/revision"] button:has-text("Guardar")')
    toast_publicable = _toast_text(page).lower()
    assert "publicable" in toast_publicable and "revoca sola" in toast_publicable

    page.reload()   # persistencia real, no solo del lado del cliente
    assert page.locator('form[action$="/revision"] select[name="estado"]').input_value() == "publicable"

    # d3) Aparece como tal en la lista (filtro "Revisión" de la home), por texto propio.
    page.goto("/?revision=publicable")
    assert page.locator("tr", has_text="ORGANISMO SEED E2E JOURNEY").count() == 1

    # e) Invalidar el calculo (otra correccion: el precio corregido a un valor muy por encima del
    # minimo de mercado, rojo) y comprobar que el "publicable" se REVOCA SOLO.
    page.goto(f"/revision/{renglon_id}")
    marker2 = f"nav-check-{time.time()}"
    page.evaluate(f"window.__e2e_marker = {marker2!r}")
    page.fill("#form-revision input[name='precio_unitario']", "650")
    page.fill("#form-revision textarea[name='nota']", "e2e: precio mal cargado, dato corregido")
    page.click('#form-revision button:has-text("Guardar y recalcular")')
    assert "recalculando" in _toast_text(page).lower()

    estado2 = _wait_job_done(seeded_app.base_url, f"revision_{renglon_id}")
    assert not estado2.get("error"), estado2
    assert "publicable" in (estado2.get("message") or "").lower() and \
        "revocad" in (estado2.get("message") or "").lower(), (
        f"el mensaje del job tendria que avisar el publicable revocado: {estado2}")
    _force_soft_refresh(page, estado2.get("message"))

    assert page.evaluate("window.__e2e_marker") == marker2, (
        "la pagina de revision recargo completo en vez de refrescar en el lugar")
    assert "sem-rojo" in (page.locator(".sem").first.get_attribute("class") or "")

    page.goto(f"/licitacion/{proceso_id}")
    assert page.locator('form[action$="/revision"] select[name="estado"]').input_value() == "revisado", (
        "el publicable deberia haberse revocado solo al cambiar el calculo")
    nota_proceso = page.locator('form[action$="/revision"] textarea[name="nota"]').input_value()
    assert "revocado" in nota_proceso.lower()

    page.goto("/?revision=publicable")
    assert page.locator("tr", has_text="ORGANISMO SEED E2E JOURNEY").count() == 0, (
        "sigue apareciendo como publicable despues de la revocacion automatica"
    )

    # La misma corrida (ahora SI habia algo publicable que perder) queda marcada "revocado" en
    # /impacto, sin ambiguedad sobre cual corrida es (de nuevo, por el renglon propio).
    page.goto("/impacto")
    fila_impacto2 = page.locator("tr", has_text=f"revision:{renglon_id}").first
    fila_impacto2.wait_for(timeout=10000)
    fila_impacto2.locator('a:has-text("Ver qué cambió")').click()
    page.wait_for_url("**/impacto/*")
    fila_detalle2 = page.locator("tr", has=page.locator(f'a[href="/revision/{renglon_id}"]'))
    assert fila_detalle2.count() == 1
    assert "sem-verde" in (fila_detalle2.locator(".sem").nth(0).get_attribute("class") or "")
    assert "sem-rojo" in (fila_detalle2.locator(".sem").nth(1).get_attribute("class") or "")
    assert "revocado" in fila_detalle2.inner_text().lower()

    # f) Nunca se reafirma solo: deshacer la correccion de precio deja el calculo IDENTICO al que
    # se habia aprobado como publicable (verde, 490 de nuevo) — y el estado tiene que seguir en
    # "revisado", no volver solo a "publicable" (esa reafirmacion exige un humano).
    page.goto(f"/revision/{renglon_id}")
    page.locator("tr", has_text="Precio unitario").locator('button:has-text("Deshacer")').click()
    assert "recalculando" in _toast_text(page).lower()
    estado3 = _wait_job_done(seeded_app.base_url, f"revision_{renglon_id}")
    assert not estado3.get("error"), estado3
    _force_soft_refresh(page, estado3.get("message"))
    assert "sem-verde" in (page.locator(".sem").first.get_attribute("class") or "")

    page.goto(f"/licitacion/{proceso_id}")
    estado_final = page.locator('form[action$="/revision"] select[name="estado"]').input_value()
    assert estado_final == "revisado", (
        f"el publicable NUNCA debe reaparecer solo, aunque el calculo vuelva a ser identico al "
        f"aprobado (sobreprecios/revision.py): quedo en {estado_final!r}")

    conftest.assert_clean(page)
