"""Compuerta de publicación: el estado "publicable" queda atado a la evidencia y al cálculo.

Decisión del LLM prime P15 (2026-10-11, pendiente de check): ningún caso sale a
periodistas sin revisión humana marcada "publicable", y esa marca se revoca sola si
cambia la evidencia o el cálculo (otro precio de mercado, otra regla, otra
extracción). La huella es un hash de lo que el revisor vio: renglones (cantidad,
precio, proveedor) y comparaciones (referencia, brecha, semáforo).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from . import db


def huella(conn: sqlite3.Connection, proceso_id: int) -> str:
    rows = conn.execute(
        "SELECT r.id, r.cantidad, r.precio_unitario, r.proveedor_cuit, r.producto_id, k.ref_principal, "
        "ROUND(k.ref_principal_valor, 2), ROUND(k.sobreprecio_ajustado_pct, 1), k.semaforo, k.brecha_oferta_min_pct "
        "FROM renglones r LEFT JOIN comparaciones k ON k.renglon_id = r.id WHERE r.proceso_id = ? ORDER BY r.id",
        (proceso_id,)).fetchall()
    return hashlib.sha1(json.dumps([tuple(r) for r in rows], default=str).encode()).hexdigest()[:16]


def marcar(conn: sqlite3.Connection, proceso_id: int, estado: str, nota: str | None) -> None:
    conn.execute("UPDATE procesos SET estado_revision=?, nota_revision=?, revision_huella=?, updated_at=? WHERE id=?",
                 (estado, nota, huella(conn, proceso_id) if estado == "publicable" else None, db.now_iso(), proceso_id))


def revocar_si_cambio(conn: sqlite3.Connection) -> int:
    """Pasa a 'revisado' los publicables cuyo cálculo cambió desde la aprobación. Devuelve cuántos."""
    n = 0
    for p in conn.execute("SELECT id, revision_huella, nota_revision FROM procesos WHERE estado_revision='publicable'").fetchall():
        if p["revision_huella"] != huella(conn, p["id"]):
            nota = ((p["nota_revision"] or "") + f"\n[auto {db.now_iso()[:10]}] publicable revocado: cambió la "
                    "evidencia o el cálculo después de la aprobación; volver a revisar.").strip()
            conn.execute("UPDATE procesos SET estado_revision='revisado', nota_revision=?, revision_huella=NULL "
                         "WHERE id=?", (nota, p["id"]))
            n += 1
    conn.commit()
    return n
