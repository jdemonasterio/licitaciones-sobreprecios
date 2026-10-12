"""Tests de sobreprecios/reports.py: avisos antes de publicar (MEJORAS #49.4) y el expediente
para periodista (MEJORAS #49.5). Las bases viven en tmp_path vía LIC_DB_PATH (nunca la real)."""
from __future__ import annotations

import json

import pytest

from sobreprecios import db, reports


def _fila(semaforo="verde", motivo_codigo="ok", motivo="", estado_revision="publicable"):
    return {"semaforo": semaforo, "motivo_codigo": motivo_codigo, "motivo": motivo,
            "estado_revision": estado_revision}


# ── advertencias() ──────────────────────────────────────────────────────────

def test_advertencias_vacio_sin_filas():
    assert reports.advertencias([]) == []


def test_advertencias_detecta_no_publicable_gris_equivalencia_inferido_y_programa():
    rows = [
        _fila(estado_revision="revisado"),
        _fila(semaforo="gris", motivo_codigo="sin_datos"),
        _fila(semaforo="amarillo", motivo_codigo="equivalencia_por_verificar"),
        _fila(semaforo="amarillo", motivo="PRECIO INFERIDO: ..."),
        _fila(semaforo="amarillo", motivo="referencia: precio de programa precios_cuidados (ed1)"),
    ]
    out = reports.advertencias(rows)
    texto = " ".join(out)
    assert "no están" in texto and "marcadas" in texto
    assert "sin comparación (gris)" in texto
    assert "equivalencia de producto" in texto
    assert "INFERIDO" in texto
    assert "precio de programa" in texto


def test_advertencias_todo_publicable_y_comparado_no_avisa_nada():
    rows = [_fila(), _fila(semaforo="rojo")]
    assert reports.advertencias(rows) == []


# ── crear_expediente() ───────────────────────────────────────────────────────

def _seed_proceso_con_renglon(conn, semaforo="rojo", monto_proceso=5000.0, con_par_corroborado=True):
    proceso_id = db.upsert(conn, "procesos", {
        "fuente": "bo_primera", "external_id": "exp1", "numero": "1-2026-LPU",
        "organismo": "ORGANISMO EXPEDIENTE", "fecha_apertura": "2021-06-01",
        "fecha_adjudicacion": "2021-06-07", "fecha_publicacion": "2021-06-05",
        "monto_total": monto_proceso, "estado_revision": "pendiente",
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }, ("fuente", "external_id"))
    conn.execute(
        "INSERT INTO renglones (id, proceso_id, nro_renglon, descripcion, cantidad, unidad_compra, "
        "precio_unitario, monto_total, proveedor, proveedor_cuit, contenido_base, unidad_base, "
        "precio_unidad_base, external_key) "
        "VALUES (1, ?, 1, 'Yerba mate 1 kg', 1000, 'kilogramos', 5000, 5000000, 'PROVEEDOR SRL', "
        "'30-11111111-1', 1.0, 'kg', 5000, 'exp:1')", (proceso_id,))
    evidencia = [{"fuente": "seed_a", "canal": "mayorista", "fecha": "2021-06-07", "precio_ub": 457.0,
                 "contenido_base": 1.0, "descripcion": "obs a", "url": None, "precio_original": None,
                 "factor_ipc": None, "posterior": False},
                {"fuente": "seed_b", "canal": "mayorista", "fecha": "2021-06-07", "precio_ub": 460.0,
                 "contenido_base": 1.0, "descripcion": "obs b", "url": None, "precio_original": None,
                 "factor_ipc": None, "posterior": False}]
    conn.execute(
        "INSERT INTO comparaciones (renglon_id, fecha_compra, semaforo, motivo_codigo, motivo, "
        "ref_principal, ref_principal_valor, ref_corroborada, sobreprecio_ajustado_pct, "
        "sobreprecio_corroborado_pct, exceso_ars, evidencia_json) "
        "VALUES (1, '2021-06-01', ?, 'ok', '+20.0% vs min mercado', 'min_mercado', 460.0, "
        + ("460.0" if con_par_corroborado else "NULL") +
        ", 20.0, 20.0, 100000, ?)", (semaforo, json.dumps(evidencia)))
    conn.commit()
    return proceso_id


def test_crear_expediente_arma_html_y_zip_y_registra_en_reportes(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "t.db"))
    with db.session() as conn:
        proceso_id = _seed_proceso_con_renglon(conn)

    msg = reports.crear_expediente(proceso_id)
    assert "1 renglón" in msg or "1 renglon" in msg

    with db.session() as conn:
        row = conn.execute("SELECT * FROM reportes WHERE tipo='expediente'").fetchone()
    assert row is not None
    assert row["proceso_id"] == proceso_id
    html_path = reports.archivo(row, "html")
    zip_path = reports.archivo(row, "zip")
    assert html_path.exists() and zip_path.exists()

    contenido = html_path.read_text(encoding="utf-8")
    assert "ORGANISMO EXPEDIENTE" in contenido
    assert "Apertura de ofertas" in contenido
    assert "Yerba mate 1 kg" in contenido
    assert "Cómo se construyó esta comparación" in contenido
    assert "seed_a" in contenido   # fuente de mercado con su fecha, en la sección de construcción

    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        assert "expediente.html" in zf.namelist()
        assert zf.read("expediente.html").decode("utf-8") == contenido


def test_crear_expediente_sin_renglon_id_usa_rojo_y_amarillo_por_defecto(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "t.db"))
    with db.session() as conn:
        proceso_id = _seed_proceso_con_renglon(conn, semaforo="rojo")
    msg = reports.crear_expediente(proceso_id, renglon_ids=None)
    assert "1" in msg


def test_crear_expediente_proceso_inexistente_levanta(tmp_path, monkeypatch):
    monkeypatch.setenv("LIC_DB_PATH", str(tmp_path / "t.db"))
    with db.session():
        pass
    with pytest.raises(ValueError):
        reports.crear_expediente(99999)
