"""Tests de rutas nuevas de sobreprecios/web/app.py (MEJORAS #49): comprobante de cálculo,
expediente para periodista, checklist de explicaciones alternativas, y el modo público que
tiene que deshabilitar backup/restore (coordinación con el deploy en la nube, LIC_MODO_PUBLICO)."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from sobreprecios import db


def _seed_simple(conn) -> tuple[int, int]:
    proceso_id = db.upsert(conn, "procesos", {
        "fuente": "bo_primera", "external_id": "w1", "numero": "1-2026-LPU",
        "organismo": "ORGANISMO WEB", "fecha_apertura": "2021-06-01", "fecha_adjudicacion": "2021-06-07",
        "monto_total": 5000000.0, "estado_revision": "pendiente",
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }, ("fuente", "external_id"))
    conn.execute(
        "INSERT INTO renglones (id, proceso_id, nro_renglon, descripcion, cantidad, unidad_compra, "
        "precio_unitario, monto_total, contenido_base, unidad_base, precio_unidad_base, external_key) "
        "VALUES (1, ?, 1, 'Yerba mate 1 kg', 1000, 'kilogramos', 5000, 5000000, 1.0, 'kg', 5000, 'w:1')",
        (proceso_id,))
    conn.execute(
        "INSERT INTO comparaciones (renglon_id, fecha_compra, semaforo, motivo_codigo, motivo, "
        "ref_principal, ref_principal_valor, sobreprecio_ajustado_pct, evidencia_json, sensibilidad_json) "
        "VALUES (1, '2021-06-01', 'amarillo', 'ok', '+10.0% vs min mercado', 'min_mercado', 460.0, 10.0, "
        "'[]', '{\"1m\": 8.0, \"2m\": 9.0}')")
    conn.commit()
    return proceso_id, 1


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "web.db"))
    from sobreprecios.web.app import app
    return TestClient(app)


# ── Comprobante de cálculo ──────────────────────────────────────────────────

def test_comprobante_renderiza_para_un_renglon_existente(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        _seed_simple(conn)
    resp = client.get("/renglon/1/comprobante")
    assert resp.status_code == 200
    assert "Comprobante de cálculo" in resp.text
    assert "Apertura de ofertas" in resp.text


def test_comprobante_renglon_inexistente_redirige(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session():
        pass
    resp = client.get("/renglon/9999/comprobante", follow_redirects=False)
    assert resp.status_code == 303


# ── Síntesis en /licitacion/<id> ─────────────────────────────────────────────

def test_licitacion_muestra_sintesis_y_formulario_de_expediente(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        proceso_id, _ = _seed_simple(conn)
    resp = client.get(f"/licitacion/{proceso_id}")
    assert resp.status_code == 200
    assert "Síntesis de esta licitación" in resp.text
    assert "Armar expediente para periodista" in resp.text
    assert "Monto total del proceso" in resp.text
    assert "Monto de los renglones analizados" in resp.text


# ── Expediente para periodista ───────────────────────────────────────────────

def test_licitacion_expediente_arranca_job_y_aparece_en_reportes(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        proceso_id, renglon_id = _seed_simple(conn)
    resp = client.post(f"/licitacion/{proceso_id}/expediente",
                       data={"titulo": "Expediente de prueba", "renglon_ids": [str(renglon_id)]},
                       follow_redirects=False)
    assert resp.status_code == 303

    from sobreprecios.web import _background
    import time
    deadline = time.time() + 5
    while _background.status(f"expediente_{proceso_id}")["running"] and time.time() < deadline:
        time.sleep(0.05)
    estado = _background.status(f"expediente_{proceso_id}")
    assert estado["error"] is None, estado

    with db.session() as conn:
        row = conn.execute("SELECT * FROM reportes WHERE tipo='expediente' AND proceso_id=?",
                           (proceso_id,)).fetchone()
    assert row is not None and row["titulo"] == "Expediente de prueba"


def test_licitacion_expediente_proceso_inexistente_redirige(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session():
        pass
    resp = client.post("/licitacion/9999/expediente", data={}, follow_redirects=False)
    assert resp.status_code == 303
    assert "message=" in resp.headers["location"]


# ── Checklist de explicaciones alternativas ─────────────────────────────────

def test_revision_explicacion_guarda_y_se_ve_en_el_caso(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        _seed_simple(conn)
    resp = client.post("/revision/1/explicacion",
                       data={"item": "logistica", "estado": "verificado", "nota": "entrega lejos",
                             "revisor": "mateo"},
                       follow_redirects=False)
    assert resp.status_code == 303
    caso_resp = client.get("/revision/1")
    assert "entrega lejos" in caso_resp.text


def test_revision_explicacion_item_invalido_redirige_con_mensaje(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        _seed_simple(conn)
    resp = client.post("/revision/1/explicacion", data={"item": "no_existe", "estado": "verificado"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "message=" in resp.headers["location"]


# ── Modo público: backup/restore deshabilitados ────────────────────────────

def test_fuentes_backup_bloqueado_en_modo_publico(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_MODO_PUBLICO", "1")
    client = _client(tmp_path, monkeypatch)
    with db.session():
        pass
    resp = client.get("/fuentes/backup", follow_redirects=False)
    assert resp.status_code == 303   # nunca el archivo de la base
    assert "fuentes" in resp.headers["location"]


def test_fuentes_restaurar_bloqueado_en_modo_publico(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_MODO_PUBLICO", "1")
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        conn.execute("INSERT INTO procesos (fuente, external_id, created_at, updated_at) "
                    "VALUES ('bo_primera', 'original', ?, ?)", (db.now_iso(), db.now_iso()))
        conn.commit()
    resp = client.post("/fuentes/restaurar", data={"confirmo": "si"},
                       files={"archivo": ("x.db", b"SQLite format 3\x00" + b"x" * 50)},
                       follow_redirects=False)
    # El middleware de seguridad pública corta cualquier POST con 403 antes de llegar a la ruta (que además
    # tiene su propio bloqueo, por si el middleware se desactiva).
    assert resp.status_code in {403, 303}
    with db.session() as conn:
        rows = [dict(r) for r in conn.execute("SELECT external_id FROM procesos")]
    assert {r["external_id"] for r in rows} == {"original"}   # no se tocó


def test_fuentes_oculta_el_panel_de_backup_en_modo_publico(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_MODO_PUBLICO", "1")
    client = _client(tmp_path, monkeypatch)
    with db.session():
        pass
    resp = client.get("/fuentes")
    assert resp.status_code == 200
    assert "Descargar copia de seguridad" not in resp.text


def test_fuentes_muestra_el_panel_de_backup_sin_modo_publico(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session():
        pass
    resp = client.get("/fuentes")
    assert resp.status_code == 200
    assert "Descargar copia de seguridad" in resp.text


def test_mes_ipc_usado_renderiza_sin_error_en_las_3_pantallas(tmp_path, monkeypatch):
    """Regresión: cuando `factor_ipc` NO es None (la observación es de otro mes), las 3 tablas de
    evidencia (licitación, revisión y comprobante) usan el filtro Jinja `format` — el seed del
    simulador nunca ejercita esta rama porque sus observaciones son todas del mismo mes."""
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        proceso_id, renglon_id = _seed_simple(conn)
        evidencia = [{"fuente": "seed_a", "canal": "mayorista", "fecha": "2021-05-01", "precio_ub": 480.0,
                     "contenido_base": 1.0, "descripcion": "obs mes anterior [llevado al mes de la compra "
                     "por IPC x1.030]", "url": None, "precio_original": 466.0, "factor_ipc": 1.03,
                     "posterior": False}]
        conn.execute("UPDATE comparaciones SET evidencia_json=? WHERE renglon_id=?",
                    (json.dumps(evidencia), renglon_id))
        conn.commit()

    for url in (f"/licitacion/{proceso_id}", f"/revision/{renglon_id}", f"/renglon/{renglon_id}/comprobante"):
        resp = client.get(url)
        assert resp.status_code == 200, url
        assert "2021-05 → 2021-06" in resp.text or "2021-05 → 2021-06" in resp.text, url
        assert "x1.030" in resp.text, url


def test_fuentes_backup_descarga_un_archivo_sqlite_sin_modo_publico(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with db.session() as conn:
        conn.execute("INSERT INTO procesos (fuente, external_id, created_at, updated_at) "
                    "VALUES ('bo_primera', 'x', ?, ?)", (db.now_iso(), db.now_iso()))
        conn.commit()
    resp = client.get("/fuentes/backup")
    assert resp.status_code == 200
    assert resp.content.startswith(b"SQLite format 3\x00")
