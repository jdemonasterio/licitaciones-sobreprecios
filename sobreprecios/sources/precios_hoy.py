"""Relevamiento diario de precios de HOY: supermercados y mayoristas.

No existe un archivo público de precios históricos por producto para 2023-2026
(el SEPA oficial se sobreescribe todos los días). La única forma de tener
historia propia es guardar una foto por día desde ahora: este módulo la saca y
la deja en precios_mercado con fecha = hoy. Correrlo una vez por día (botón en
/fuentes o actualizar_datos.bat con el Programador de tareas de Windows).

Fuentes (verificadas 2026-10-11):
  precios_claros  API pública de Precios Claros (SEPA): supermercados cercanos a
                  los puntos configurados; precio = promedio entre min y max de
                  las sucursales. Sin mayoristas hoy.
  carrefour       API VTEX pública (ft= con %20, si no responde 400).
  maxiconsumo     GraphQL de Magento sin login (precios de la sucursal Moreno).
                  Única fuente MAYORISTA viva encontrada.
  sepa_gondolar   espejo diario del SEPA completo (proyecto comunitario
                  fermanzolido/gondolar, CC-BY 4.0) en GitHub: anda aunque
                  datos.produccion.gob.ar no responda. Un precio por EAN, cadena
                  y provincia; guardamos la mediana entre cadenas por provincia.

Solo se guardan observaciones que machean con el catálogo (no 75.000 productos
por día): se buscan los términos `busqueda` de config/productos.yml.
"""
from __future__ import annotations

import datetime as dt
import html
import io
import json
import logging
import sqlite3
import statistics
import tarfile
import time
import urllib.parse

import httpx

from .. import config
from ..catalog import Producto, load_catalog, match_rules
from ..textnorm import norm
from ..units import parse_size, price_per_base

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (compatible; transparencia-compras/0.1)"}
PRECIOS_CLAROS = "https://d3e6htiiul5ek9.cloudfront.net/prod"
GONDOLAR_URL = "https://github.com/fermanzolido/gondolar/releases/download/datos/datos.tar.gz"
# Puntos desde donde se toman las sucursales cercanas (lat, lng, región).
PUNTOS = [(-34.6037, -58.3816, "CABA")]
DELAY_S = 0.4


def _busquedas(catalog: dict[str, Producto]) -> list[tuple[str, str]]:
    raw = config.load_yaml("productos.yml").get("productos") or {}
    out = []
    for pid in catalog:   # catalog puede venir recortado a pocos productos (run_productos)
        for term in raw.get(pid, {}).get("busqueda") or [catalog[pid].nombre]:
            out.append((pid, term))
    return out


def _obs_row(fuente: str, canal: str, fecha: str, descripcion: str, precio: float, catalog,
             ean: str | None = None, marca: str | None = None, region: str | None = None,
             sucursal: str | None = None, url: str | None = None, presentacion: str | None = None) -> dict | None:
    """Observación lista para insertar, o None si no machea con el catálogo o no tiene tamaño."""
    if not precio or precio <= 0:
        return None
    descripcion = html.unescape(descripcion or "").strip()   # Maxiconsumo: HARINA MORIXE &quot;000&quot;
    match = match_rules(descripcion, catalog)
    if not match.producto_id:
        return None
    prod = catalog[match.producto_id]
    # El nombre manda: la "presentación" de algunas fuentes trae errores ("0.5 gr." para
    # un paquete de 500 g en el espejo SEPA). Si ambos existen y no coinciden, se descarta.
    by_name = parse_size(descripcion)
    by_pres = parse_size(f"x {presentacion}") if presentacion else None
    if by_name and by_pres and abs(by_name.base()[0] - by_pres.base()[0]) > 0.1 * by_name.base()[0]:
        return None
    size = by_name or by_pres
    ppu, base = price_per_base(precio, size)
    if base != prod.unidad_base:
        return None
    return {"fuente": fuente, "canal": canal, "fecha": fecha, "ean": ean, "descripcion": descripcion,
            "marca": marca, "precio": precio, "contenido_base": size.base()[0], "unidad_base": base,
            "precio_unidad_base": ppu, "producto_id": prod.id, "match_metodo": "reglas",
            "match_confianza": match.score, "region": region, "sucursal": sucursal, "url": url,
            "obs_key": f"{ean or norm(descripcion)}|{region or ''}|{sucursal or ''}"}


def _insert(conn: sqlite3.Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        if not r:
            continue
        cols = list(r)
        conn.execute(
            f"INSERT INTO precios_mercado ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            "ON CONFLICT (fuente, fecha, obs_key) DO UPDATE SET precio=excluded.precio, "
            "precio_unidad_base=excluded.precio_unidad_base", [r[c] for c in cols])
        n += 1
    conn.commit()
    return n


def precios_claros(client: httpx.Client, catalog, fecha: str) -> list[dict]:
    rows = []
    for lat, lng, region in PUNTOS:
        suc = client.get(f"{PRECIOS_CLAROS}/sucursales", params={"lat": lat, "lng": lng, "limit": 30}).json()
        ids = ",".join(s["id"] for s in suc.get("sucursales", [])[:30])
        for pid, term in _busquedas(catalog):
            resp = client.get(f"{PRECIOS_CLAROS}/productos",
                              params={"string": term, "array_sucursales": ids, "limit": 50})
            time.sleep(DELAY_S)
            if resp.status_code != 200:
                continue
            for p in resp.json().get("productos", []):
                lo, hi = p.get("precioMin"), p.get("precioMax")
                precio = (lo + hi) / 2 if lo and hi else (lo or hi)
                rows.append(_obs_row("precios_claros", "minorista", fecha, p.get("nombre", ""), precio, catalog,
                                     ean=p.get("id"), marca=p.get("marca"), region=region,
                                     presentacion=p.get("presentacion")))
    return [r for r in rows if r]


def carrefour(client: httpx.Client, catalog, fecha: str) -> list[dict]:
    rows = []
    for pid, term in _busquedas(catalog):
        url = ("https://www.carrefour.com.ar/api/catalog_system/pub/products/search?ft="
               + urllib.parse.quote(term) + "&_from=0&_to=49")
        resp = client.get(url)
        time.sleep(DELAY_S)
        if resp.status_code >= 300:
            continue
        for p in resp.json():
            for item in p.get("items", [])[:1]:
                offer = (item.get("sellers") or [{}])[0].get("commertialOffer") or {}
                if not offer.get("IsAvailable", True):
                    continue
                rows.append(_obs_row("carrefour", "minorista", fecha, p.get("productName", ""), offer.get("Price"),
                                     catalog, ean=item.get("ean"), marca=p.get("brand"), region="online",
                                     url=p.get("link")))
    return [r for r in rows if r]


def maxiconsumo(client: httpx.Client, catalog, fecha: str) -> list[dict]:
    rows = []
    query = ('query($q: String!){products(search: $q, pageSize: 50){items{name sku '
             'price_range{minimum_price{regular_price{value} final_price{value}}}}}}')
    for pid, term in _busquedas(catalog):
        resp = client.post("https://www.maxiconsumo.com/graphql", json={"query": query, "variables": {"q": term}})
        time.sleep(DELAY_S)
        if resp.status_code != 200:
            continue
        items = ((resp.json().get("data") or {}).get("products") or {}).get("items") or []
        for it in items:
            price = ((it.get("price_range") or {}).get("minimum_price") or {})
            precio = (price.get("final_price") or {}).get("value") or (price.get("regular_price") or {}).get("value")
            rows.append(_obs_row("maxiconsumo", "mayorista", fecha, it.get("name", ""), precio, catalog,
                                 ean=it.get("sku"), region="Moreno (sin login)"))
    return [r for r in rows if r]


def sepa_gondolar(client: httpx.Client, catalog, fecha_hoy: str) -> list[dict]:
    """Espejo SEPA: mediana entre cadenas por EAN y provincia, solo productos del catálogo."""
    resp = client.get(GONDOLAR_URL, follow_redirects=True, timeout=180)
    resp.raise_for_status()
    tar = tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz")

    def load(name):
        return json.load(tar.extractfile(f"data/{name}"))

    meta, names = load("meta.json"), load("names.json")
    fecha = meta.get("fecha") or fecha_hoy
    keep = {}
    for i, ean in enumerate(names["e"]):
        desc = f"{names['n'][i]}"
        match = match_rules(desc, catalog)
        if match.producto_id:
            keep[ean] = (desc, names["m"][i], names["q"][i])
    rows = []
    for member in tar.getmembers():
        if not (member.name.startswith("data/prices/") and member.name.endswith(".json")):
            continue
        provincia = member.name.rsplit("/", 1)[-1][:-5]
        prices = json.load(tar.extractfile(member))
        for ean, (desc, marca, qty) in keep.items():
            values = [v for v in (prices.get(ean) or []) if v]
            if not values:
                continue
            rows.append(_obs_row("sepa_gondolar", "minorista", fecha, desc, float(statistics.median(values)),
                                 catalog, ean=ean, marca=marca, region=provincia, presentacion=qty))
    return [r for r in rows if r]


FUENTES = {"precios_claros": precios_claros, "carrefour": carrefour, "maxiconsumo": maxiconsumo,
           "sepa_gondolar": sepa_gondolar}


def run(conn: sqlite3.Connection, fuentes: list[str] | None = None, progress=None) -> str:
    catalog = load_catalog()
    fecha = dt.date.today().isoformat()
    resumen = []
    with httpx.Client(timeout=60, headers=UA, follow_redirects=True) as client:
        for name in fuentes or list(FUENTES):
            if progress:
                progress(f"precios: {name}")
            try:
                n = _insert(conn, FUENTES[name](client, catalog, fecha))
                resumen.append(f"{name} {n}")
            except (httpx.HTTPError, ValueError, KeyError, tarfile.TarError) as exc:
                logger.warning("fuente %s falló: %s", name, exc)
                resumen.append(f"{name} ERROR ({type(exc).__name__})")
    return "Precios de hoy: " + ", ".join(resumen)


def run_productos(conn: sqlite3.Connection, producto_ids: list[str]) -> str:
    """Precios de hoy SOLO para algunos productos (investigación puntual): rápido.

    Usa las fuentes con búsqueda (Precios Claros, Carrefour, Maxiconsumo); el
    espejo SEPA completo es un archivo grande y queda para la corrida diaria.
    """
    full = load_catalog()
    subset = {pid: full[pid] for pid in producto_ids if pid in full}
    fecha = dt.date.today().isoformat()
    resumen = []
    with httpx.Client(timeout=60, headers=UA, follow_redirects=True) as client:
        for name in ("precios_claros", "carrefour", "maxiconsumo"):
            try:
                rows = FUENTES[name](client, subset, fecha)
                resumen.append(f"{name} {_insert(conn, rows)}")
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                resumen.append(f"{name} ERROR ({type(exc).__name__})")
    return "Precios de hoy (" + ", ".join(producto_ids) + "): " + ", ".join(resumen)
