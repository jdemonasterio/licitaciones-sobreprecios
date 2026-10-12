"""Aplica la normalización a la base (idempotente): organismos, proveedores, personas y alias.

Se corre después de cada ingesta (pipeline, investigar) y a mano con
`python -m sobreprecios.cli normalizar`. Nunca borra el texto original: escribe
columnas normalizadas al lado y registra cada alias en `entidades_alias` para que
una persona pueda auditar qué se unió con qué (golden validation_entidades).
"""
from __future__ import annotations

import json
import re
import sqlite3

from .. import db
from .entidades import cuit_valido, normalizar_empresa, normalizar_organismo, normalizar_persona

SCHEMA = """
CREATE TABLE IF NOT EXISTS entidades_alias (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    clave TEXT NOT NULL,
    alias_raw TEXT NOT NULL,
    alias_norm TEXT,
    fuente TEXT,
    veces INTEGER DEFAULT 1,
    UNIQUE (tipo, clave, alias_raw)
);
"""
_MINUSCULAS = {"de", "del", "la", "las", "los", "y", "e", "el", "en", "para", "por"}


def titulo(texto: str | None) -> str | None:
    """'MINISTERIO DE DESARROLLO SOCIAL' -> 'Ministerio de Desarrollo Social'."""
    if not texto:
        return texto
    words = texto.lower().split()
    return " ".join(w if (i and w in _MINUSCULAS) else w[:1].upper() + w[1:] for i, w in enumerate(words))


def _alias(conn, tipo: str, clave: str, raw: str | None, norm: str | None, fuente: str) -> None:
    if not raw:
        return
    conn.execute("INSERT INTO entidades_alias (tipo, clave, alias_raw, alias_norm, fuente) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(tipo, clave, alias_raw) DO UPDATE SET veces = veces + 1",
                 (tipo, clave, raw, norm, fuente))


def run(conn: sqlite3.Connection) -> str:
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM entidades_alias")   # se reconstruye entero (barato y sin estados raros)
    n_org = n_prov = n_pers = n_mal = 0
    for p in conn.execute("SELECT id, organismo, jurisdiccion, fuente FROM procesos").fetchall():
        o = normalizar_organismo(p["organismo"] or p["jurisdiccion"])
        saf = o.get("saf")
        canon = titulo(o["jurisdiccion"]) if o["jurisdiccion"] else None
        conn.execute("UPDATE procesos SET organismo_canonico=?, saf=COALESCE(?, saf) WHERE id=?", (canon, saf, p["id"]))
        if canon:
            _alias(conn, "organismo", canon, p["organismo"], o["nombre"], p["fuente"])
            n_org += 1
    for r in conn.execute("SELECT id, proveedor, proveedor_cuit, alertas, extraccion_metodo FROM renglones "
                          "WHERE proveedor IS NOT NULL").fetchall():
        e = normalizar_empresa(r["proveedor"], r["proveedor_cuit"])
        alertas = json.loads(r["alertas"]) if r["alertas"] else []
        alertas = [a for a in alertas if not a.startswith("cuit_invalido")]
        if r["proveedor_cuit"] and not cuit_valido(r["proveedor_cuit"]):
            alertas.append(f"cuit_invalido: {r['proveedor_cuit']} no pasa el dígito verificador (¿mal leído?)")
            n_mal += 1
        conn.execute("UPDATE renglones SET proveedor_clave=?, alertas=? WHERE id=?",
                     (e["clave"], json.dumps(alertas, ensure_ascii=False) if alertas else None, r["id"]))
        _alias(conn, "empresa", e["clave"], r["proveedor"], e["nombre_display"], r["extraccion_metodo"])
        n_prov += 1
    for o in conn.execute("SELECT DISTINCT proveedor, cuit FROM ofertas WHERE proveedor IS NOT NULL").fetchall():
        e = normalizar_empresa(o["proveedor"], o["cuit"])
        _alias(conn, "empresa", e["clave"], o["proveedor"], e["nombre_display"], "comprar_oferta")
    for h in conn.execute("SELECT id, persona, documento FROM empresa_hechos WHERE persona IS NOT NULL").fetchall():
        pn = normalizar_persona(h["persona"], h["documento"])
        conn.execute("UPDATE empresa_hechos SET persona_clave=? WHERE id=?", (pn["clave"], h["id"]))
        _alias(conn, "persona", pn["clave"], h["persona"], pn["nombre_display"], "empresa_hechos")
        n_pers += 1
    # Empresas: nombre para mostrar normalizado (el original queda en entidades_alias).
    for e in conn.execute("SELECT cuit, razon_social FROM empresas").fetchall():
        ne = normalizar_empresa(e["razon_social"], e["cuit"])
        if ne["nombre_display"]:
            conn.execute("UPDATE empresas SET razon_social=? WHERE cuit=?", (ne["nombre_display"], e["cuit"]))
    conn.commit()
    return (f"Normalización: {n_org} procesos con organismo, {n_prov} renglones con proveedor "
            f"({n_mal} CUIT inválidos), {n_pers} hechos de personas")


def candidatos_union(conn: sqlite3.Connection) -> list[dict]:
    """Empresas SIN CUIT válido cuyo nombre normalizado coincide con otra: para revisión humana."""
    rows = conn.execute(
        "SELECT a.alias_norm, GROUP_CONCAT(DISTINCT a.clave) claves, GROUP_CONCAT(DISTINCT a.alias_raw) alias, "
        "COUNT(DISTINCT a.clave) n FROM entidades_alias a WHERE a.tipo='empresa' GROUP BY a.alias_norm "
        "HAVING n > 1").fetchall()
    return [dict(r) for r in rows]
