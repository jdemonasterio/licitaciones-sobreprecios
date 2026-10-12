"""Tests de la vista de impacto antes/después de un recálculo (MEJORAS #49.8)."""
from __future__ import annotations

import pytest

from sobreprecios import db, impacto


def _proceso_con_renglon(conn, proceso_id, renglon_id, estado_revision="pendiente"):
    db.upsert(conn, "procesos", {
        "fuente": "bo_primera", "external_id": f"p{proceso_id}", "estado_revision": estado_revision,
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }, ("fuente", "external_id"))
    pid = conn.execute("SELECT id FROM procesos WHERE fuente='bo_primera' AND external_id=?",
                       (f"p{proceso_id}",)).fetchone()[0]
    conn.execute("INSERT OR IGNORE INTO renglones (id, proceso_id, descripcion, external_key) "
                "VALUES (?, ?, 'yerba 1 kg', ?)", (renglon_id, pid, f"k{renglon_id}"))
    conn.commit()
    return pid


def _set_comparacion(conn, renglon_id, semaforo, sobreprecio=None, exceso=None, motivo_codigo="ok"):
    db.upsert(conn, "comparaciones", {
        "renglon_id": renglon_id, "semaforo": semaforo, "sobreprecio_ajustado_pct": sobreprecio,
        "exceso_ars": exceso, "motivo_codigo": motivo_codigo, "computed_at": db.now_iso(),
    }, ("renglon_id",))
    conn.commit()


def test_recalcular_con_impacto_registra_corrida_y_foto(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglon(conn, 1, 101)
        _set_comparacion(conn, 101, "amarillo", 12.0, 5000.0)

        mensaje = impacto.recalcular_con_impacto(conn, "revision:101", [pid], lambda: "2 renglones comparados")
        assert mensaje == "2 renglones comparados"

        corridas = impacto.corridas(conn)
        assert len(corridas) == 1 and corridas[0]["tipo"] == "recalculo"
        assert corridas[0]["origen"] == "revision:101" and corridas[0]["estado"] == "ok"

        foto = [dict(r) for r in conn.execute(
            "SELECT * FROM comparaciones_snapshot WHERE corrida_id=?", (corridas[0]["id"],))]
        assert len(foto) == 1 and foto[0]["renglon_id"] == 101 and foto[0]["semaforo"] == "amarillo"


def test_recalcular_con_impacto_re_levanta_la_excepcion_y_deja_la_corrida_en_error(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglon(conn, 1, 101)
        _set_comparacion(conn, 101, "verde", 1.0, 0.0)

        def falla():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            impacto.recalcular_con_impacto(conn, "fuentes:comparar", [pid], falla)
        corridas = impacto.corridas(conn)
        assert corridas[0]["estado"] == "error" and "boom" in corridas[0]["mensaje"]


def test_diff_detecta_cambio_de_color_exceso_y_revocacion_de_publicable(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglon(conn, 1, 101, estado_revision="publicable")
        pid2 = _proceso_con_renglon(conn, 2, 102)
        _set_comparacion(conn, 101, "amarillo", 12.0, 5000.0)
        _set_comparacion(conn, 102, "verde", 1.0, 0.0)

        run_id = db.start_run(conn, "recalculo", {"origen": "test"})
        impacto._snapshot(conn, run_id, [pid, pid2])

        # El recálculo "real": el 101 sube a rojo (y por eso se revoca el publicable), el 102
        # no cambia (no debe aparecer en el diff).
        _set_comparacion(conn, 101, "rojo", 25.0, 12000.0)
        conn.execute("UPDATE procesos SET estado_revision='revisado' WHERE id=?", (pid,))
        conn.commit()

        datos = impacto.diff(conn, run_id)
        assert datos["n_renglones"] == 2 and datos["n_cambiados"] == 1 and datos["n_revocados"] == 1
        cambio = datos["cambios"][0]
        assert cambio["renglon_id"] == 101
        assert cambio["semaforo_antes"] == "amarillo" and cambio["semaforo_despues"] == "rojo"
        assert cambio["cambio_exceso"] == pytest.approx(7000.0)
        assert cambio["publicable_revocado"] is True


def test_diff_ignora_renglones_borrados_desde_la_foto(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglon(conn, 1, 101)
        _set_comparacion(conn, 101, "verde", 1.0, 0.0)
        run_id = db.start_run(conn, "recalculo", {"origen": "test"})
        impacto._snapshot(conn, run_id, [pid])
        conn.execute("DELETE FROM renglones WHERE id=101")
        conn.commit()
        datos = impacto.diff(conn, run_id)
        assert datos["n_renglones"] == 1 and datos["n_cambiados"] == 0
