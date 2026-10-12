"""Revisión humana de precios de programa (Precios Cuidados/Justos/Máximos) y juicio de un LLM
externo cuando existe, mostrados juntos en el webui (`/precios-programa`).

Dos autores, dos roles distintos (jdemonasterio 2026-10-12):
  humano     la persona que revisa en el webui (✓ Ok / ✗ No / ? No sé + comentario). Es la ÚNICA
             marca que excluye un dato del comparador: `pipeline/compare.load_observations`
             descarta las filas con autor="humano" y veredicto="no".
  juez_llm   un juicio automático ya hecho fuera del programa (ver `auditoria/juez.py` y, para
             este lote puntual, el TSV de `_scratch/licitaciones/historico_precios/`), que se
             muestra como referencia junto al formulario pero NUNCA excluye nada por sí solo —
             "un resultado falso es peor que ninguno": sin confirmación humana no se descarta.

Clave: (fuente, obs_key), las mismas columnas que identifican una fila en `precios_mercado`
(ver sources/precios_programa.py). Guardar de nuevo con el mismo autor pisa la marca anterior
(un cambio de opinión no deja un historial de marcas viejas).
"""
from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

from . import db
from .textnorm import norm

VEREDICTOS = {"ok", "no", "no_se"}
# "Veredicto juez" del TSV -> nuestra escala. "dudoso" se muestra como "no_se" (ninguno de los
# dos extremos: ni confirma ni descarta) para no inventar una exclusión que nadie pidió.
_JUEZ_A_VEREDICTO = {"ok": "ok", "error": "no", "dudoso": "no_se", "no aplica": "no_se"}


def guardar(conn: sqlite3.Connection, fuente: str, obs_key: str, veredicto: str, nota: str | None,
           autor: str = "humano") -> None:
    if veredicto not in VEREDICTOS:
        raise ValueError(f"veredicto inválido: {veredicto!r} (válidos: {sorted(VEREDICTOS)})")
    conn.execute(
        "INSERT INTO validaciones_programa (fuente, obs_key, autor, veredicto, nota, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (fuente, obs_key, autor) DO UPDATE SET "
        "veredicto=excluded.veredicto, nota=excluded.nota, created_at=excluded.created_at",
        (fuente, obs_key, autor, veredicto, (nota or "").strip() or None, db.now_iso()))
    conn.commit()


def por_fuente(conn: sqlite3.Connection, fuente: str) -> dict[str, dict[str, dict]]:
    """{obs_key: {autor: {"veredicto":..., "nota":...}}} de una fuente (edición)."""
    out: dict[str, dict[str, dict]] = {}
    for r in conn.execute("SELECT obs_key, autor, veredicto, nota FROM validaciones_programa WHERE fuente=?",
                          (fuente,)):
        out.setdefault(r["obs_key"], {})[r["autor"]] = {"veredicto": r["veredicto"], "nota": r["nota"]}
    return out


def _texto_sin_precio(texto: str) -> str:
    """"Nombre x Tamaño, $ Precio" -> "Nombre x Tamaño": el texto guardado en precios_mercado
    no lleva el precio (se parseó aparte), así que hay que recortarlo igual para poder
    comparar con `norm()` (ver sources/precios_programa.py: el "[vigente ... a ...]" también se
    recorta, se agrega DESPUÉS de guardar y el TSV del juez nunca lo vio)."""
    return re.split(r"\$", texto)[0].strip(" ,:;")


def importar_juez_tsv(conn: sqlite3.Connection, path: str | Path) -> str:
    """Carga un TSV de auditoría externa (columnas: fecha_publicacion, producto_texto_original,
    "Veredicto juez", "Tipo de error", "Explicación juez", "Verificación contra la fuente", ...)
    como autor="juez_llm". Empareja por fecha_publicacion -> edición (config/precios_programa.yml)
    y por texto normalizado (sin precio, sin el sufijo de vigencia) contra `precios_mercado`. Las
    filas del TSV que no mapean a ningún producto del catálogo (se descartaron en la importación)
    no tienen con qué emparejar: quedan afuera, no es un error.
    """
    from . import config
    ediciones = config.load_yaml("precios_programa.yml").get("ediciones") or {}
    por_fecha = {ed["fecha_publicacion"]: (eid, ed["programa"]) for eid, ed in ediciones.items()}

    with open(path, newline="", encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh, delimiter="\t"))
    n_match = n_sin_edicion = n_sin_fila = n_sin_veredicto = 0
    for fila in filas:
        veredicto_juez = (fila.get("Veredicto juez") or "").strip().lower()
        if veredicto_juez not in _JUEZ_A_VEREDICTO:
            n_sin_veredicto += 1
            continue
        edicion = por_fecha.get((fila.get("fecha_publicacion") or "").strip())
        if not edicion:
            n_sin_edicion += 1
            continue
        eid, programa = edicion
        fuente = f"programa:{programa}:{eid}"
        objetivo = norm(_texto_sin_precio(fila.get("producto_texto_original") or ""))
        if not objetivo:
            n_sin_fila += 1
            continue
        fila_db = None
        for r in conn.execute("SELECT obs_key, descripcion FROM precios_mercado WHERE fuente=?", (fuente,)):
            desc = (r["descripcion"] or "").split(" [vigente")[0]
            if norm(desc) == objetivo:
                fila_db = r
                break
        if not fila_db:
            n_sin_fila += 1
            continue
        nota = " · ".join(x for x in (
            fila.get("Tipo de error"), fila.get("Explicación juez"), fila.get("Verificación contra la fuente"))
            if x and x.strip())
        guardar(conn, fuente, fila_db["obs_key"], _JUEZ_A_VEREDICTO[veredicto_juez], nota or None, autor="juez_llm")
        n_match += 1
    return (f"{n_match}/{len(filas)} veredictos del juez importados "
           f"({n_sin_edicion} sin edición registrada, {n_sin_fila} sin fila para emparejar, "
           f"{n_sin_veredicto} sin veredicto en el TSV)")
