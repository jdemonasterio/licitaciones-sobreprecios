"""Test de `goldens.organismos()` (MEJORAS #53): el golden que el owner revisa para aprobar (o
corregir) como se agrupan los organismos crudo -> canonico."""
from __future__ import annotations

import csv

from sobreprecios import config, db, goldens
from sobreprecios.normalize import aplicar


def test_organismos_golden_tiene_una_fila_por_crudo_y_marca_el_motivo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "scratch_path", lambda project, filename: tmp_path / filename)
    with db.session(tmp_path / "t.db") as conn:
        for eid, organismo in [
            ("1", "311 - Ministerio de Desarrollo Social"),
            ("2", "MINISTERIO DE DESARROLLO SOCIAL"),
            ("3", "MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA"),
            ("4", "MINISTERIO DE SALUD"),
        ]:
            db.upsert(conn, "procesos", {"fuente": "bo_primera", "external_id": eid, "organismo": organismo,
                                         "created_at": db.now_iso(), "updated_at": db.now_iso()},
                     ("fuente", "external_id"))
        aplicar.run(conn)
        path = goldens.organismos(conn)

    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))

    assert {"organismo_crudo", "codigo_saf", "n_procesos", "canonico", "jurisdiccion_agrupada", "motivo",
            "Tu veredicto", "Tu comentario"} <= set(rows[0].keys())
    assert len(rows) == 4   # una fila por texto CRUDO distinto, no por canonico
    por_crudo = {r["organismo_crudo"]: r for r in rows}
    assert por_crudo["311 - Ministerio de Desarrollo Social"]["codigo_saf"] == "311"
    assert por_crudo["311 - Ministerio de Desarrollo Social"]["canonico"] == "Ministerio de Desarrollo Social"
    assert por_crudo["MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA"]["canonico"] == \
        "Ministerio de Desarrollo Social"
    assert por_crudo["MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA"]["jurisdiccion_agrupada"] == \
        "Ministerio de Desarrollo Social"
    # Las 3 variantes que se unieron en "Ministerio de Desarrollo Social" van ANTES que "Ministerio
    # de Salud" (1 sola variante): lo que mas junta queda arriba para revisar primero.
    canonicos_en_orden = [r["canonico"] for r in rows]
    assert canonicos_en_orden.index("Ministerio de Salud") == 3
