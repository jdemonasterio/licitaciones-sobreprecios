"""Vista de impacto antes/después de un cambio de reglas o catálogo (MEJORAS #49.8).

Por qué existe: cambiar un umbral en `config/settings.yml`, un ajuste en
`config/productos_ajustes.yml`, corregir un dato a mano o excluir una referencia de mercado
dispara un recálculo (`pipeline/compare.run()`). Hoy ese recálculo reemplaza las comparaciones
sin dejar rastro de qué cambió. Esta vista saca una foto de las comparaciones vigentes ANTES de
pedirle a `compare.run()` que recalcule, y después compara esa foto contra el estado actual: qué
renglón cambió de color, cuánto cambió el exceso estimado y qué marcas "publicable" quedaron
revocadas (el mismo gate de `sobreprecios/revision.py`).

Dónde se usa: `recalcular_con_impacto()` envuelve las llamadas a `compare.run()` que dispara el
webui — las acciones de "Datos y fuentes" (`web/app.py:_job_runner`), el guardado/deshacer de una
corrección y la validación de una referencia de mercado en el módulo de revisión humana
(`web/app.py:_recalcular_proceso`), y un veredicto "No" sobre un precio de programa
(`web/app.py:precios_programa_veredicto`). Una ingesta nueva (pipeline normal sobre renglones que
todavía no tenían comparación, o `investigar.py` armando un caso por primera vez) no tiene "antes"
útil para comparar, así que esos caminos no se envuelven.

Se guarda como una corrida más de `corridas` (tipo='recalculo'), para reusar esa tabla en vez de
inventar otro log; la foto renglón por renglón vive en `comparaciones_snapshot`, atada a esa
`corrida_id`.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS comparaciones_snapshot (
    id INTEGER PRIMARY KEY,
    corrida_id INTEGER NOT NULL,
    renglon_id INTEGER NOT NULL,
    proceso_id INTEGER NOT NULL,
    semaforo TEXT,
    sobreprecio_ajustado_pct REAL,
    exceso_ars REAL,
    motivo_codigo TEXT,
    estado_revision TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_comp_snap_corrida ON comparaciones_snapshot(corrida_id);
CREATE INDEX IF NOT EXISTS ix_comp_snap_renglon ON comparaciones_snapshot(renglon_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _snapshot(conn: sqlite3.Connection, corrida_id: int, proceso_ids: list[int] | None) -> int:
    ensure_schema(conn)
    where, params = "1=1", []
    if proceso_ids:
        where = f"r.proceso_id IN ({','.join('?' for _ in proceso_ids)})"
        params = list(proceso_ids)
    rows = conn.execute(
        "SELECT r.id renglon_id, r.proceso_id, k.semaforo, k.sobreprecio_ajustado_pct, k.exceso_ars, "
        "k.motivo_codigo, p.estado_revision "
        "FROM renglones r JOIN procesos p ON p.id = r.proceso_id LEFT JOIN comparaciones k ON k.renglon_id = r.id "
        f"WHERE {where}", params).fetchall()
    ahora = db.now_iso()
    for r in rows:
        conn.execute(
            "INSERT INTO comparaciones_snapshot (corrida_id, renglon_id, proceso_id, semaforo, "
            "sobreprecio_ajustado_pct, exceso_ars, motivo_codigo, estado_revision, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (corrida_id, r["renglon_id"], r["proceso_id"], r["semaforo"], r["sobreprecio_ajustado_pct"],
             r["exceso_ars"], r["motivo_codigo"], r["estado_revision"], ahora))
    conn.commit()
    return len(rows)


def recalcular_con_impacto(conn: sqlite3.Connection, origen: str, proceso_ids: list[int] | None,
                           run_fn: Callable[[], str]) -> str:
    """Envuelve una llamada que recalcula comparaciones (`run_fn`, normalmente
    `lambda: compare.run(conn, ...)`) con una foto de ANTES y queda registrada como una corrida
    de tipo 'recalculo' que `/impacto` puede listar y abrir. `origen` es un texto corto para
    identificar qué la disparó (p. ej. "revision:123", "fuentes:comparar", "precios_programa").

    Si `run_fn` explota, la corrida queda con estado 'error' igual (con la foto de antes ya
    guardada, por si ayuda a entender qué se estaba recalculando) y la excepción se re-levanta:
    esto nunca debe silenciar un error del recálculo real.
    """
    run_id = db.start_run(conn, "recalculo", {"origen": origen, "proceso_ids": proceso_ids})
    n_antes = _snapshot(conn, run_id, proceso_ids)
    try:
        mensaje = run_fn()
    except Exception as exc:   # noqa: BLE001 — se re-levanta tal cual, solo se registra antes
        db.finish_run(conn, run_id, "error", n_antes, f"{type(exc).__name__}: {exc}")
        raise
    db.finish_run(conn, run_id, "ok", n_antes, mensaje)
    return mensaje


def diff(conn: sqlite3.Connection, corrida_id: int) -> dict:
    """Qué cambió desde la foto de una corrida 'recalculo': color, exceso o un "publicable"
    revocado. Un renglón de la foto que ya no existe (reimportación que borró renglones viejos)
    se ignora: no hay nada vigente con que compararlo."""
    ensure_schema(conn)
    antes_rows = conn.execute(
        "SELECT * FROM comparaciones_snapshot WHERE corrida_id=?", (corrida_id,)).fetchall()
    cambios = []
    n_revocados = 0
    for a in antes_rows:
        actual = conn.execute(
            "SELECT k.semaforo, k.sobreprecio_ajustado_pct, k.exceso_ars, k.motivo_codigo, "
            "p.estado_revision, r.descripcion, r.proceso_id "
            "FROM renglones r JOIN procesos p ON p.id=r.proceso_id LEFT JOIN comparaciones k ON k.renglon_id=r.id "
            "WHERE r.id=?", (a["renglon_id"],)).fetchone()
        if not actual:
            continue
        revocado = a["estado_revision"] == "publicable" and actual["estado_revision"] != "publicable"
        if revocado:
            n_revocados += 1
        cambio_color = (a["semaforo"] or "gris") != (actual["semaforo"] or "gris")
        cambio_exceso = round((actual["exceso_ars"] or 0) - (a["exceso_ars"] or 0), 2)
        if cambio_color or revocado or abs(cambio_exceso) > 0.01:
            cambios.append({
                "renglon_id": a["renglon_id"], "proceso_id": a["proceso_id"], "descripcion": actual["descripcion"],
                "semaforo_antes": a["semaforo"] or "gris", "semaforo_despues": actual["semaforo"] or "gris",
                "sobreprecio_antes": a["sobreprecio_ajustado_pct"], "sobreprecio_despues": actual["sobreprecio_ajustado_pct"],
                "exceso_antes": a["exceso_ars"], "exceso_despues": actual["exceso_ars"], "cambio_exceso": cambio_exceso,
                "motivo_antes": a["motivo_codigo"], "motivo_despues": actual["motivo_codigo"],
                "publicable_revocado": revocado,
            })
    cambios.sort(key=lambda c: abs(c["cambio_exceso"] or 0), reverse=True)
    return {"n_renglones": len(antes_rows), "n_cambiados": len(cambios), "n_revocados": n_revocados,
            "cambios": cambios}


def corridas(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM corridas WHERE tipo='recalculo' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            params = json.loads(d.get("parametros") or "{}")
        except (TypeError, ValueError):
            params = {}
        d["origen"] = params.get("origen")
        out.append(d)
    return out
