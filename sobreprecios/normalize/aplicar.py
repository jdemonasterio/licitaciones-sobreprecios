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

from .. import config, db
from ..textnorm import strip_accents
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


def _puntaje_variante(texto: str) -> tuple:
    """Para elegir QUÉ texto se muestra entre variantes que agrupan igual (MEJORAS #53): gana la
    que tiene más tildes/ñ (la mejor escrita), después la más corta, después alfabética — siempre
    la misma elección para el mismo conjunto de variantes, sin heuristica de "la fuente más
    repetida" (que aparezca más veces no la hace más confiable, solo más repetida)."""
    acentuadas = sum(1 for c in texto if c != strip_accents(c))
    return (-acentuadas, len(texto), texto)


def _mejor_variante(candidatos: list[str]) -> str:
    return min(candidatos, key=_puntaje_variante)


def _clave_y_display_organismo(o: dict, agrupar_subunidades: bool) -> tuple[str, str]:
    """(clave para AGRUPAR, texto candidato para MOSTRAR) de un `normalizar_organismo()`.

    `agrupar_subunidades=True` (default, config/settings.yml -> organismos.agrupar_subunidades):
    la clave es solo la jurisdicción (ministerio/organismo padre) — una subunidad como "...
    Secretaría de Gestión Administrativa" se agrupa en "Ministerio de Desarrollo Social" para
    Analítica/filtros/rankings; el detalle de la subunidad sigue viéndose en `procesos.organismo`
    (nunca se toca). `False`: la clave incluye la subunidad, cada una en su propio balde (sigue
    deduplicada por mayúsculas/acentos/SAF, pero no sube a la jurisdicción madre).
    """
    jur_clave, jur_display = o["jurisdiccion_clave"], titulo(o["jurisdiccion"])
    if agrupar_subunidades or not o["unidad"]:
        return jur_clave, jur_display
    unidad_clave = strip_accents(o["unidad"]).upper()
    return f"{jur_clave}::{unidad_clave}", f"{jur_display} - {titulo(o['unidad'])}"


def run(conn: sqlite3.Connection) -> str:
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM entidades_alias")   # se reconstruye entero (barato y sin estados raros)
    n_org = n_prov = n_pers = n_mal = 0
    agrupar_subunidades = config.agrupar_subunidades_organismo()
    # Dos pasadas (MEJORAS #53): 1) normalizar cada proceso y juntar candidatos por clave; 2) elegir
    # UN solo texto canónico por clave (`_mejor_variante`) y aplicarlo a TODOS los procesos de esa
    # clave — así dos variantes que agrupan igual (con o sin tilde, con o sin código SAF adelante)
    # siempre terminan mostrando el mismo nombre, no el que le tocó a cada fila por separado. Es una
    # sola pasada extra sobre `procesos` (barato: no hay join ni N²).
    filas = []   # (proceso_id, organismo_raw, nombre_norm, fuente, clave, saf)
    candidatos: dict[str, list[str]] = {}
    for p in conn.execute("SELECT id, organismo, jurisdiccion, fuente FROM procesos").fetchall():
        o = normalizar_organismo(p["organismo"] or p["jurisdiccion"])
        if not o["jurisdiccion"]:
            conn.execute("UPDATE procesos SET organismo_canonico=NULL WHERE id=?", (p["id"],))
            continue
        clave, display = _clave_y_display_organismo(o, agrupar_subunidades)
        filas.append((p["id"], p["organismo"], o["nombre"], p["fuente"], clave, o.get("saf")))
        candidatos.setdefault(clave, []).append(display)
        n_org += 1
    mejor = {clave: _mejor_variante(cands) for clave, cands in candidatos.items()}
    for proceso_id, organismo_raw, nombre_norm, fuente, clave, saf in filas:
        canon = mejor[clave]
        conn.execute("UPDATE procesos SET organismo_canonico=?, saf=COALESCE(?, saf) WHERE id=?",
                     (canon, saf, proceso_id))
        _alias(conn, "organismo", canon, organismo_raw, nombre_norm, fuente)
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
