"""Smoke test de las páginas tocadas en esta ronda (Investigar, Analítica, Precios de programa,
Impacto, fecha dd/mm/aaaa): un TestClient real contra una base temporal, sin Playwright — más
rápido que el simulador E2E, para atrapar errores de Jinja (filtro mal usado, variable faltante)
en cada `pytest -q` normal en vez de solo con `LIC_E2E=1`."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from sobreprecios import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "sobreprecios.db"))
    monkeypatch.setenv("LIC_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("LIC_LLM_OFFLINE", "1")
    from sobreprecios.web.app import app
    return TestClient(app)


@pytest.fixture
def seed(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "sobreprecios.db"))
    with db.session(tmp_path / "sobreprecios.db") as conn:
        conn.execute("INSERT INTO productos (id, nombre, rubro, unidad_base) VALUES "
                    "('yerba_mate', 'Yerba mate', 'alimentos', 'kg')")
        db.upsert(conn, "procesos", {
            "fuente": "bo_primera", "external_id": "p1", "organismo": "MINISTERIO DE PRUEBA",
            "numero": "80-0014-LPU25", "tipo_procedimiento": "licitacion_publica",
            "fecha_adjudicacion": "2021-06-07", "fecha_publicacion": "2021-06-07",
            "monto_total": 5000000.0, "estado_revision": "pendiente",
            "created_at": db.now_iso(), "updated_at": db.now_iso(),
        }, ("fuente", "external_id"))
        (pid,) = conn.execute("SELECT id FROM procesos WHERE external_id='p1'").fetchone()
        conn.execute(
            "INSERT INTO renglones (proceso_id, nro_renglon, descripcion, cantidad, unidad_compra, "
            "precio_unitario, monto_total, proveedor, proveedor_cuit, producto_id, contenido_base, "
            "unidad_base, precio_unidad_base, extraccion_metodo, external_key) "
            "VALUES (?, 1, 'Yerba mate 1 kg', 1000, 'kilogramos', 5000, 5000000, 'PROVEEDOR SRL', "
            "'30-11111111-1', 'yerba_mate', 1.0, 'kg', 5000, 'regex', 'k1')", (pid,))
        (rid,) = conn.execute("SELECT id FROM renglones WHERE proceso_id=?", (pid,)).fetchone()
        db.upsert(conn, "comparaciones", {
            "renglon_id": rid, "semaforo": "amarillo", "motivo_codigo": "ok",
            "sobreprecio_ajustado_pct": 12.0, "exceso_ars": 100000.0, "ref_principal": "min_mercado",
            "ref_principal_valor": 4500.0, "gap_dias": 2, "fecha_compra": "2021-06-07",
            "evidencia_json": "[]", "computed_at": db.now_iso(),
        }, ("renglon_id",))
        db.upsert(conn, "precios_mercado", {
            "fuente": "seed_mayorista", "canal": "mayorista", "fecha": "2021-06-07",
            "descripcion": "Yerba mate 1 kg", "precio": 4500.0, "contenido_base": 1.0, "unidad_base": "kg",
            "precio_unidad_base": 4500.0, "producto_id": "yerba_mate", "obs_key": "obs1",
        }, ("fuente", "fecha", "obs_key"))
        conn.commit()
        run_id = db.start_run(conn, "recalculo", {"origen": "test:smoke"})
        from sobreprecios import impacto
        impacto._snapshot(conn, run_id, [pid])
        db.finish_run(conn, run_id, "ok", 1, "1 renglón")
    return {"proceso_id": pid, "renglon_id": rid, "corrida_id": run_id}


def _assert_ok(resp):
    assert resp.status_code == 200, resp.text[:2000]
    assert "Traceback" not in resp.text and "jinja2.exceptions" not in resp.text


def test_licitaciones_lista_y_detalle(client, seed):
    _assert_ok(client.get("/"))
    _assert_ok(client.get("/", params={"orden": "monto", "moneda": "real"}))
    resp = client.get(f"/licitacion/{seed['proceso_id']}")
    _assert_ok(resp)
    assert "Qué se importó" in resp.text
    assert "07/06/2021" in resp.text   # dd/mm/aaaa, no 2021-06-07


def test_analitica_y_producto(client, seed):
    _assert_ok(client.get("/analitica"))
    _assert_ok(client.get("/analitica", params={"moneda": "real"}))
    _assert_ok(client.get("/producto/yerba_mate"))


def test_investigar_page(client, seed):
    _assert_ok(client.get("/investigar"))


def test_precios_programa_page(client, seed):
    _assert_ok(client.get("/precios-programa"))


def test_impacto_lista_y_detalle(client, seed):
    _assert_ok(client.get("/impacto"))
    resp = client.get(f"/impacto/{seed['corrida_id']}")
    _assert_ok(resp)
    assert str(seed["renglon_id"]) in resp.text or "Ningún renglón" in resp.text


def test_impacto_corrida_inexistente_redirige(client, seed):
    resp = client.get("/impacto/999999", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/impacto")


def test_revision_y_fuentes(client, seed):
    _assert_ok(client.get("/revision"))
    _assert_ok(client.get("/fuentes"))
    _assert_ok(client.get("/empresas"))
    _assert_ok(client.get(f"/revision/{seed['renglon_id']}"))
    _assert_ok(client.get("/revision/juez"))
    _assert_ok(client.get("/reportes"))
    _assert_ok(client.get(f"/empresa/30-11111111-1"))
