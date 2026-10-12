"""Fixtures del simulador end-to-end (ver docs/simulador.md).

Por que existe: `.venv/bin/python -m pytest -q` (~175 tests) prueba funciones
puras y rutas con un `TestClient`, pero ningun test actual abre un navegador
real. Siguiendo el patron del simulador de telegram-transcribe-bot
(ops/simulator/post_deploy.py: asertar sobre estado OBSERVABLE, nunca sobre
texto exacto; limpiar lo que se crea; separar casos con nombres claros) y las
reglas de soft-nav (\"el backend nunca
resetea la pagina"; tests/test_webui_soft_nav_rule.py), esta suite:

  - Levanta un uvicorn real en un puerto libre, en un subproceso (tal como lo
    ve un usuario real: con Jinja2 ya renderizado, JS corriendo en un browser
    de verdad, Chart.js, soft-nav.js).
  - Usa una base y un cache TEMPORALES (LIC_DB_PATH / LIC_CACHE_DIR), nunca
    data/sobreprecios.db. La semilla se carga OFFLINE con el propio codigo del
    repo (sin red, sin LLM: LIC_LLM_OFFLINE=1).
  - Bloquea cualquier llamada de red real con un proxy que no escucha en nadie
    (HTTP_PROXY/HTTPS_PROXY a un puerto cerrado): las fuentes de
    sources/precios_hoy.py ya atrapan httpx.HTTPError por diseno, asi que la
    corrida sigue "degradada" (sin precios de hoy) en vez de trabarse o de
    pegarle a internet durante un test.
  - Solo se activa con LIC_E2E=1 (igual que el post_deploy del bot necesita
    sus propias env vars): un `pytest` normal del repo no la toca.

Toda esta suite es de SOLO LECTURA sobre el codigo de la app: no se edito
ningun archivo existente para que esto funcionara.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

# Gate: nada de lo de abajo corre (ni siquiera se importa Playwright) salvo
# que alguien pida explicitamente la corrida end-to-end.
if os.environ.get("LIC_E2E") != "1":
    pytest.skip(
        "suite end-to-end deshabilitada: corre con LIC_E2E=1 (ver docs/simulador.md "
        "o scripts/simular.py)",
        allow_module_level=True,
    )

import httpx  # noqa: E402  (ya es dependencia del repo; se usa solo en 127.0.0.1)
from playwright.sync_api import sync_playwright  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sobreprecios import catalog, db  # noqa: E402
from sobreprecios.pipeline import compare, extract, match  # noqa: E402

FIXTURES = REPO / "tests" / "fixtures"
MDS_TEXTO = (FIXTURES / "bo_mds_res735_2021.txt").read_text(encoding="utf-8")

# Puerto local sin listener: cualquier intento de salir a "internet" durante la
# corrida offline pega aca y falla rapido (ECONNREFUSED), nunca sale de la PC.
_BLACKHOLE_PROXY = "http://127.0.0.1:9"


@dataclass(frozen=True)
class Seeded:
    base_url: str
    db_path: Path
    proceso_leche_id: int   # 5 proveedores, semaforos rojo/amarillo/verde (ver _seed)
    proceso_yerba_id: int   # producto sin precios de mercado -> gris


# ── Semilla determinista y offline ──────────────────────────────────────────

def _seed(db_path: Path) -> tuple[int, int]:
    with db.session(db_path) as conn:
        catalog.sync_to_db(conn)

        doc_id = db.upsert(conn, "documentos", {
            "fuente": "bo_primera", "external_id": "primera/245330/20210607",
            "url": "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607",
            "fecha_publicacion": "2021-06-07",
            "titulo": "MINISTERIO DE DESARROLLO SOCIAL - Resolucion 735/2021",
            "texto": MDS_TEXTO, "raw_path": None, "fetched_at": db.now_iso(),
        }, ("fuente", "external_id"))
        doc = conn.execute("SELECT * FROM documentos WHERE id=?", (doc_id,)).fetchone()

        extraction = extract.extract_document(
            MDS_TEXTO, "MINISTERIO DE DESARROLLO SOCIAL", "Resolucion 735/2021",
            conn=conn, use_llm=False)
        if extraction["metodo"] != "regex" or len(extraction["renglones"]) != 5:
            raise RuntimeError(
                f"semilla e2e: la extraccion regex del fixture cambio (metodo={extraction['metodo']!r}, "
                f"{len(extraction['renglones'])} renglones); revisar tests/fixtures/bo_mds_res735_2021.txt")
        extract.store_extraction(conn, doc, extraction)
        proceso_leche_id = conn.execute(
            "SELECT id FROM procesos WHERE fuente=? AND external_id=?",
            ("bo_primera", "primera/245330/20210607")).fetchone()[0]

        # Segundo proceso, sintetico: mismo dia, producto SIN precios de mercado
        # seedeados -> compare.run() debe dejarlo en gris ("sin observaciones").
        proceso_yerba_id = db.upsert(conn, "procesos", {
            "fuente": "bo_primera", "external_id": "seed/yerba/1",
            "numero": "99-9999-XXXX", "organismo": "ORGANISMO SEED YERBA",
            "tipo_procedimiento": "licitacion_publica",
            "fecha_adjudicacion": "2021-06-07", "fecha_publicacion": "2021-06-07",
            "monto_total": 5000000.0, "estado_revision": "pendiente",
            "created_at": db.now_iso(), "updated_at": db.now_iso(),
        }, ("fuente", "external_id"))
        db.upsert(conn, "renglones", {
            "proceso_id": proceso_yerba_id, "nro_renglon": 1,
            "descripcion": "Yerba mate 1 kg", "cantidad": 1000.0, "unidad_compra": "kilogramos",
            "precio_unitario": 5000.0, "monto_total": 5000000.0,
            "proveedor": "PROVEEDOR YERBA SRL", "proveedor_cuit": "30-11111111-1",
            "external_key": "seed:yerba:1",
        }, ("proceso_id", "external_key"))

        match.match_renglones(conn, use_llm=False)   # reglas alcanzan: leche y yerba matchean

        # 4 observaciones mayoristas, 2 fuentes/familias, mismo dia de la compra
        # (gap=0) -> confianza alta. min_mayorista=457, y DOS familias
        # (seed_mayorista_a=457, seed_mayorista_b=460) caen dentro del +-2%
        # del minimo (pipeline/compare.py, `corroboracion_tolerancia` /
        # `familia_fuente`): sin eso el semaforo nunca llega a rojo, queda
        # tapado en amarillo con la nota "sin rojo: el minimo no esta
        # confirmado por dos fuentes independientes" aunque el % supere el
        # umbral (lo aprendimos corriendo este seed: pipeline/compare.py
        # cambio esta regla mientras se armaba este simulador).
        # Resultado con los 5 precios reales del fixture (490 / 519,74 / 532
        # / 547 / 553): verde / amarillo / amarillo / amarillo / rojo. Desde las
        # decisiones del LLM prime P16 el rojo se mide contra el minimo CORROBORADO
        # (el par 457/460 de dos familias -> 460): 553/460 = +20,2%.
        for i, (fuente, precio) in enumerate([
            ("seed_mayorista_a", 457.0), ("seed_mayorista_b", 460.0),
            ("seed_mayorista_a", 480.0), ("seed_mayorista_b", 495.0),
        ]):
            db.upsert(conn, "precios_mercado", {
                "fuente": fuente, "canal": "mayorista", "fecha": "2021-06-07",
                # Misma variante que la compra (fortificada con hierro, zinc y vitamina C) y una serie
                # distinta por observación: el comparador exige variante verificada y toma una por serie.
                "descripcion": f"leche en polvo entera fortificada con hierro, zinc y vitamina C marca{i} 1 kg",
                "precio": precio,
                "contenido_base": 1.0, "unidad_base": "kg", "precio_unidad_base": precio,
                "producto_id": "leche_polvo_entera", "match_metodo": "seed",
                "obs_key": f"seed-leche-{i}",
            }, ("fuente", "fecha", "obs_key"))

        compare.run(conn)
        db.sync_empresas(conn)
    return proceso_leche_id, proceso_yerba_id


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_up(proc: subprocess.Popen, base_url: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"uvicorn murio antes de levantar:\n{out}")
        try:
            r = httpx.get(base_url + "/", timeout=1.0)
            if r.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"uvicorn no respondio en {timeout}s en {base_url}")


@pytest.fixture(scope="session")
def seeded_app(tmp_path_factory):
    """Semilla + servidor real para toda la sesion (un solo levantamiento)."""
    work = tmp_path_factory.mktemp("lic_e2e")
    db_path = work / "sobreprecios.db"
    cache_dir = work / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    proceso_leche_id, proceso_yerba_id = _seed(db_path)

    port = _free_port()
    env = dict(os.environ)
    env.update({
        "LIC_DB_PATH": str(db_path),
        "LIC_CACHE_DIR": str(cache_dir),
        "LIC_LLM_OFFLINE": "1",
        "LIC_LLM_PERFIL": "gratis",
        "HTTP_PROXY": _BLACKHOLE_PROXY, "HTTPS_PROXY": _BLACKHOLE_PROXY,
        "ALL_PROXY": _BLACKHOLE_PROXY, "NO_PROXY": "",
        "PYTHONUNBUFFERED": "1",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sobreprecios.web.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_up(proc, base_url)
        yield Seeded(base_url, db_path, proceso_leche_id, proceso_yerba_id)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, seeded_app):
    """Una pestania limpia por test, con Origin = base_url y errores capturados."""
    context = browser.new_context(base_url=seeded_app.base_url)
    pg = context.new_page()
    pg.sim_console_errors = []
    pg.sim_5xx = []

    def _on_console(msg):
        if msg.type == "error":
            pg.sim_console_errors.append(msg.text)

    def _on_pageerror(exc):
        pg.sim_console_errors.append(str(exc))

    def _on_response(resp):
        if resp.status >= 500:
            pg.sim_5xx.append((resp.status, resp.url))

    pg.on("console", _on_console)
    pg.on("pageerror", _on_pageerror)
    pg.on("response", _on_response)
    yield pg
    context.close()


# ── Aserciones compartidas ───────────────────────────────────────────────────

def assert_clean(page) -> None:
    """Sin errores de consola/JS y sin 5xx: la base de todo escenario."""
    assert not page.sim_console_errors, f"errores de consola: {page.sim_console_errors}"
    assert not page.sim_5xx, f"respuestas 5xx: {page.sim_5xx}"


def read_db(db_path: Path, sql: str, params: tuple = ()) -> list:
    """Lectura puntual de la base temporal desde el proceso de test (solo SELECT).

    `sqlite3.Connection` no cierra la conexion al salir de un `with` (solo
    commitea/rollbackea), asi que se cierra a mano para no acumular handles.
    """
    conn = db.connect(db_path)
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ── Screenshot automatico en cada falla (para scripts/simular.py) ───────────
#
# El screenshot se toma DENTRO del hook de reporte de la fase "call", no en el
# teardown de una fixture: una fixture autouse que no declara `page` como
# dependencia explicita no tiene garantizado correr ANTES del teardown de
# `page` (orden LIFO de pytest), y el contexto/pagina de Playwright ya estan
# cerrados para cuando le toca correr ("Target page, context or browser has
# been closed"). El hook de reporte corre apenas termina el cuerpo del test,
# antes de que arranque CUALQUIER teardown de fixtures.

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
    if rep.when != "call" or not rep.failed:
        return
    pg = item.funcargs.get("page")
    if pg is None:
        return
    out_dir = Path(os.environ.get("LIC_E2E_SHOTS") or (REPO / "_scratch" / "licitaciones" / "simulador" / "ad-hoc"))
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in item.name)
    try:
        pg.screenshot(path=str(out_dir / f"{safe}.png"), full_page=True)
    except Exception as exc:  # el screenshot es un extra, nunca debe tapar la falla real
        print(f"(no se pudo guardar screenshot de {safe}: {exc})")
