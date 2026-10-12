"""Estado documental en reportes (MEJORAS #29, jdemonasterio 2026-10-12): el reporte nunca dice
"pagó"/"pagado" (no hay fuente de pagos reales), dice "recomendado" (dictamen), "adjudicado" o
"contratado" según de dónde salió el precio."""
from sobreprecios import db, reports


def test_fmt_estado_documental_usa_etiqueta_corta_y_nunca_pagado():
    assert reports._fmt("dictamen", "estado_documental") == "recomendado"
    assert reports._fmt("adjudicado", "estado_documental") == "adjudicado"
    assert reports._fmt("contratado", "estado_documental") == "contratado"
    assert reports._fmt(None, "estado_documental") == "desconocido"


def test_columnas_incluyen_estado_documental():
    nombres = [c for c, _ in reports.COLUMNAS]
    assert "estado_documental" in nombres


def test_filas_trae_estado_documental_de_la_base(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        db.upsert(conn, "procesos", {"fuente": "bo_primera", "external_id": "p1",
                                     "organismo": "ORGANISMO X", "fecha_publicacion": "2021-06-07",
                                     "created_at": db.now_iso(), "updated_at": db.now_iso()},
                 ("fuente", "external_id"))
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key, estado_documental) "
                    "VALUES (1, 'yerba', 'k1', 'dictamen')")
        conn.commit()
        filas = reports.filas(conn, {})
    assert filas[0]["estado_documental"] == "dictamen"
    assert reports._fmt(filas[0]["estado_documental"], "estado_documental") == "recomendado"
