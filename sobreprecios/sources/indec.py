"""IPC y precios promedio al consumidor de INDEC, vía apis.datos.gob.ar/series.

- IPC (base dic-2016, mensual): para llevar un precio de mercado a la fecha de
  la compra y para estimar el costo financiero del plazo de pago.
- Precios promedio (dataset 105 GBA / 348 regional, jun-2017 en adelante): la
  referencia oficial para productos con presentación definida (fideos guiseros
  500 g, arroz 1 kg, aceite girasol 1,5 l, lavandina 1 l...). Es precio
  MINORISTA promedio: si el Estado paga más que esto, es una señal fuerte.

Las series que se cargan salen de config/settings.yml (indices) y de
config/productos.yml (indec.serie), salvo las marcadas verificado: desconocido.

Nota: datos.gob.ar (CKAN) no responde desde algunas redes; esta API
(apis.datos.gob.ar) es otro host y anduvo siempre en las pruebas.
"""
from __future__ import annotations

import logging
import sqlite3

import httpx

from .. import config
from ..catalog import load_catalog

logger = logging.getLogger(__name__)

API = "https://apis.datos.gob.ar/series/api/series/"


def fetch_series(ids: list[str], start: str = "2016-01-01") -> dict[str, dict[str, float]]:
    """{serie_id: {YYYY-MM-01: valor}} para hasta ~40 series por llamada."""
    out: dict[str, dict[str, float]] = {i: {} for i in ids}
    with httpx.Client(timeout=60) as client:
        for chunk_start in range(0, len(ids), 20):
            chunk = ids[chunk_start:chunk_start + 20]
            resp = client.get(API, params={"ids": ",".join(chunk), "start_date": start,
                                           "limit": 5000, "format": "json"})
            resp.raise_for_status()
            payload = resp.json()
            for row in payload.get("data", []):
                fecha = row[0]
                for sid, value in zip(chunk, row[1:]):
                    if value is not None:
                        out[sid][fecha] = float(value)
    return out


def run(conn: sqlite3.Connection) -> str:
    settings = config.settings()
    index_ids = list((settings.get("indices") or {}).values())
    catalog = load_catalog()
    price_specs = {p.id: p for p in catalog.values()
                   if p.indec.get("serie") and p.indec.get("verificado") != "desconocido"}
    ids = index_ids + [p.indec["serie"] for p in price_specs.values()]
    data = fetch_series(ids)

    n_idx = 0
    for sid in index_ids:
        for fecha, valor in data.get(sid, {}).items():
            conn.execute("INSERT INTO indices (serie, fecha, valor) VALUES (?, ?, ?) "
                         "ON CONFLICT(serie, fecha) DO UPDATE SET valor=excluded.valor", (sid, fecha, valor))
            n_idx += 1
    # Los índices se guardan también con su alias (ipc_alimentos / ipc_general) para el comparador.
    for alias, sid in (settings.get("indices") or {}).items():
        conn.execute("DELETE FROM indices WHERE serie=?", (alias,))
        conn.execute("INSERT INTO indices (serie, fecha, valor) SELECT ?, fecha, valor FROM indices WHERE serie=?",
                     (alias, sid))

    n_px = 0
    for prod in price_specs.values():
        contenido = prod.indec_contenido_base()
        if not contenido:
            logger.warning("presentación INDEC ilegible para %s: %s", prod.id, prod.indec)
            continue
        verif = prod.indec.get("verificado")
        for fecha, valor in data.get(prod.indec["serie"], {}).items():
            conn.execute(
                "INSERT INTO precios_mercado (fuente, canal, fecha, descripcion, precio, contenido_base, "
                "unidad_base, precio_unidad_base, producto_id, match_metodo, match_confianza, region, obs_key) "
                "VALUES ('indec_gba', 'oficial', ?, ?, ?, ?, ?, ?, ?, 'serie_indec', ?, 'GBA', ?) "
                "ON CONFLICT (fuente, fecha, obs_key) DO UPDATE SET precio=excluded.precio, "
                "precio_unidad_base=excluded.precio_unidad_base, contenido_base=excluded.contenido_base",
                (fecha, f"INDEC precio promedio {prod.nombre} ({prod.indec.get('presentacion')}, {verif})",
                 valor, contenido, prod.unidad_base, valor / contenido, prod.id,
                 0.9 if verif == "oficial" else 0.6, prod.indec["serie"]))
            n_px += 1
    conn.commit()
    return f"INDEC: {n_idx} valores de IPC, {n_px} precios promedio de {len(price_specs)} productos"
