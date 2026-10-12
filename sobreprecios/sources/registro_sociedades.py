"""Registro Nacional de Sociedades (Ministerio de Justicia, datos.jus.gob.ar).

Es el padrón federal de personas jurídicas de ARCA, republicado cada dos meses
(Ley 26.047): CUIT, razón social, tipo societario, fecha del contrato social,
domicilio legal y fiscal, y actividades declaradas. Cubre TODAS las provincias,
a diferencia de la segunda sección del BO (solo sociedades con sede en CABA).
No trae socios ni autoridades.

Cada snapshot es un zip de ~230 MB con CSVs de ~900 MB. No se carga entero: se
lee en streaming y se guardan solo los CUIT que adjudicaron algo. Lo que cambia
entre snapshots (domicilios, actividades) queda como hechos fechados en
empresa_hechos (ver db.close_superseded_facts).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import sqlite3
import zipfile
from pathlib import Path

import httpx

from .. import config, db
from ..empresas import guardar_hechos

logger = logging.getLogger(__name__)

CKAN = "https://datos.jus.gob.ar/api/3/action/package_show?id=registro-nacional-de-sociedades"

# Palabras que esperamos ver en las actividades declaradas de quien vende cada rubro.
# Sirve para una alerta suave ("actividad no relacionada"), nunca para descartar.
RUBRO_ACTIVIDADES = {
    "alimentos": ["aliment", "lacte", "leche", "comestible", "almacen", "supermerc", "mayor", "panad", "galletit",
                  "yerba", "harina", "molin", "arroz", "azucar", "fideo", "pasta", "conserva", "bebida", "carne",
                  "frut", "hortaliz", "acopio", "cereal", "golosin", "catering", "comida"],
    "bebidas": ["bebida", "agua", "aliment", "mayor", "supermerc", "almacen"],
    "limpieza": ["limpieza", "jabon", "detergent", "quimic", "higiene", "mayor", "supermerc", "almacen", "tocador"],
    "oficina": ["papel", "libreria", "oficina", "imprenta", "mayor", "comercio"],
}


def latest_resource(client: httpx.Client) -> dict:
    """El recurso más nuevo de sociedades (no 'asociaciones sin fines de lucro')."""
    data = client.get(CKAN).json()["result"]
    resources = [r for r in data["resources"]
                 if re.match(r"registro nacional de sociedades\s*-\s*20\d\d", r.get("name", "").lower())
                 and "asociaciones" not in r.get("name", "").lower() and r.get("url", "").endswith(".zip")]
    if not resources:
        raise RuntimeError("no encontré un recurso zip del Registro Nacional de Sociedades")
    return max(resources, key=lambda r: r.get("last_modified") or r.get("created") or "")


def download(client: httpx.Client, resource: dict) -> Path:
    path = config.cache_dir("rns") / f"{resource['id']}.zip"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path
    tmp = path.with_suffix(".part")
    with client.stream("GET", resource["url"], timeout=900) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as handle:
            for chunk in resp.iter_bytes(1 << 20):
                handle.write(chunk)
    tmp.rename(path)
    return path


def iter_rows(zip_path: Path):
    """Filas del CSV más nuevo dentro del zip (el nombre lleva la fecha: ...-20260618.csv)."""
    with zipfile.ZipFile(zip_path) as zf:
        name = max(n for n in zf.namelist() if n.endswith(".csv"))
        with zf.open(name) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            for row in reader:
                yield name, row


def _digits(cuit: str | None) -> str:
    return re.sub(r"\D", "", cuit or "")


def _fecha(value: str | None) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value or "")
    return m.group(1) if m else None


def _domicilio(row: dict, prefix: str) -> str | None:
    calle = (row.get(f"{prefix}_calle") or "").strip()
    numero = (row.get(f"{prefix}_numero") or "").strip()
    loc = (row.get(f"{prefix}_localidad") or "").strip()
    prov = (row.get(f"{prefix}_provincia") or "").strip()
    parts = [" ".join(p for p in [calle, numero if numero not in {"0", ""} else ""] if p), loc, prov]
    text = ", ".join(p for p in parts if p)
    return text or None


def collect(rows, cuits: set[str]) -> dict[str, dict]:
    """Agrupa por CUIT (una fila por actividad en el CSV) solo los CUIT pedidos."""
    out: dict[str, dict] = {}
    for csv_name, row in rows:
        cuit = _digits(row.get("cuit"))
        if cuit not in cuits:
            continue
        item = out.setdefault(cuit, {"row": row, "csv": csv_name, "actividades": []})
        if row.get("actividad_estado") == "AC" and row.get("actividad_descripcion"):
            item["actividades"].append((row.get("actividad_orden") or "9", row["actividad_descripcion"].strip()))
    return out


def hechos_de(item: dict) -> list[dict]:
    row = item["row"]
    actualizado = _fecha(row.get("fecha_hora_actualizacion"))
    contrato = _fecha(row.get("fecha_hora_contrato_social"))
    hechos = []
    if contrato:
        hechos.append({"tipo": "constitucion", "valor": f"{row.get('tipo_societario') or ''} constituida".strip(),
                       "vigente_desde": contrato, "confianza": 0.9})
    legal = _domicilio(row, "dom_legal")
    if legal:
        hechos.append({"tipo": "domicilio", "valor": f"{legal} (legal)", "vigente_desde": actualizado,
                       "confianza": 0.6})
    fiscal = _domicilio(row, "dom_fiscal")
    if fiscal:
        hechos.append({"tipo": "domicilio_fiscal", "valor": fiscal, "vigente_desde": actualizado, "confianza": 0.6})
    for orden, desc in sorted(set(item["actividades"]))[:6]:
        hechos.append({"tipo": "actividad", "valor": desc, "cargo": f"orden {orden}", "vigente_desde": actualizado,
                       "confianza": 0.8})
    return hechos


def actividad_relacionada(actividades: list[str], rubros: set[str]) -> bool | None:
    """True/False si las actividades declaradas cubren algún rubro vendido; None si no hay datos."""
    if not actividades or not rubros:
        return None
    texto = " ".join(actividades).lower()
    texto = texto.translate(str.maketrans("áéíóúü", "aeiouu"))
    keys = [k for r in rubros for k in RUBRO_ACTIVIDADES.get(r, [])]
    return any(k in texto for k in keys) if keys else None


def run(conn: sqlite3.Connection, zip_path: Path | None = None, progress=None) -> str:
    db.sync_empresas(conn)
    cuits = {_digits(r[0]): r[0] for r in conn.execute("SELECT cuit FROM empresas")}
    if not cuits:
        return "Registro de sociedades: no hay adjudicatarios con CUIT todavía"
    url = None
    if zip_path is None:
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            resource = latest_resource(client)
            url = resource["url"]
            if progress:
                progress(f"bajando {resource['name']} (~230 MB, una sola vez)")
            zip_path = download(client, resource)
    if progress:
        progress("buscando los adjudicatarios en el registro")
    found = collect(iter_rows(Path(zip_path)), set(cuits))
    for digits, item in found.items():
        cuit = cuits[digits]
        row = item["row"]
        hechos = hechos_de(item)
        for h in hechos:
            h["fuente_url"] = url
        guardar_hechos(conn, cuit, hechos, "registro_nacional_sociedades", f"rns:{item['csv']}")
        conn.execute(
            "UPDATE empresas SET tipo_societario=?, fecha_constitucion=?, domicilio_actual=?, "
            "razon_social=COALESCE(razon_social, ?) WHERE cuit=?",
            (row.get("tipo_societario"), _fecha(row.get("fecha_hora_contrato_social")),
             _domicilio(row, "dom_legal"), row.get("razon_social"), cuit))
    conn.commit()
    return f"Registro de sociedades: {len(found)} de {len(cuits)} adjudicatarios encontrados"
