"""Archivo histórico de SEPA (todos los supermercados, por EAN y sucursal) desde agosto 2024.

El dataset oficial SEPA (datos.produccion.gob.ar) se pisa todos los días. El
proyecto comunitario preciazo (github.com/catdevnull/preciazo, de @esoesnulo)
archiva cada volcado, lo recomprime en .tar.zst (~65 MB por día, ~1,5 GB
descomprimido) y publica un índice en
github.com/catdevnull/sepa-precios-metadata (index.md) con links a un bucket
público de Backblaze B2. Verificado 2026-10-11: 19/08/2024 → 09/10/2026, ~700
días descargables. Es la única fuente encontrada con precios por producto para
2024-2026 (ver _scratch/licitaciones/fuentes_precios_historicos.tsv).

Qué se guarda (no los 10 millones de filas diarias): solo EAN que machean con el
catálogo, y por cada (día, EAN, cadena) la MEDIANA entre sucursales — el precio
de góndola típico de esa cadena. Cada cadena cuenta como una fuente independiente
para corroborar el mínimo (familia "sepa:<cadena>"); el mismo dato visto por el
espejo comunitario o la API de Precios Claros NO cuenta dos veces.

Qué días se bajan: no todo el archivo. `importar_necesarios` mira las compras a
comparar (fecha de apertura/adjudicación dentro del rango del archivo) y baja el
volcado más cercano a cada una (sin repetir semana). `importar_fechas` baja días
puntuales (por ejemplo, uno por mes para los gráficos).
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
import sqlite3
import statistics
import tarfile
import zipfile
from pathlib import Path

import httpx

from .. import config
from ..catalog import Producto, load_catalog, match_rules
from ..units import parse_size

logger = logging.getLogger(__name__)

INDEX_URL = "https://raw.githubusercontent.com/catdevnull/sepa-precios-metadata/main/index.md"
FUENTE = "sepa_archivo"
_DAY_RE = re.compile(r"^\* \w+, (\d{2})/(\d{2})/(\d{4}):")
_LINK_RE = re.compile(r"\[✅ descargar\]\((https://[^)]+\.tar\.zst)\)")
# Banderas del SEPA que son formatos mayoristas (cash & carry): Carrefour Maxi, Maxi Comodín.
BANDERAS_MAYORISTAS = {"maxi", "maxi comodin"}
_UNIDADES = {"KG": "kg", "GR": "g", "GRM": "g", "G": "g", "LT": "l", "L": "l", "ML": "ml", "CC": "cc",
             "CM3": "cc", "UD": None, "UN": None, "UNI": None, "MT": None, "M": None}


def parse_index(text: str) -> dict[str, str]:
    """index.md -> {YYYY-MM-DD: url}. Por día queda el primer link descargable (el índice lista revisiones)."""
    out: dict[str, str] = {}
    current = None
    for line in text.splitlines():
        m = _DAY_RE.match(line.strip()) if line.startswith("*") else None
        if m:
            current = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            continue
        if current and current not in out:
            link = _LINK_RE.search(line)
            if link:
                out[current] = link.group(1)
    return out


def fetch_index(client: httpx.Client) -> dict[str, str]:
    return parse_index(client.get(INDEX_URL, timeout=60).text)


def fechas_mas_cercanas(disponibles: list[str], objetivos: list[str], max_dias: int = 15) -> list[str]:
    """Para cada fecha objetivo, el volcado disponible más cercano (<= max_dias); sin repetir semana ISO."""
    elegidas: dict[tuple[int, int], str] = {}
    disp = sorted(disponibles)
    for obj in sorted(set(objetivos)):
        d0 = dt.date.fromisoformat(obj[:10])
        best = min(disp, key=lambda d: abs((dt.date.fromisoformat(d) - d0).days), default=None)
        if best is None or abs((dt.date.fromisoformat(best) - d0).days) > max_dias:
            continue
        week = dt.date.fromisoformat(best).isocalendar()[:2]
        elegidas.setdefault(week, best)
    return sorted(elegidas.values())


def _size_from_fields(cantidad: str, unidad: str):
    unit = _UNIDADES.get((unidad or "").strip().upper())
    if not unit:
        return None
    return parse_size(f"x {cantidad} {unit}")


class EanMatcher:
    """EAN -> (producto, contenido_base) con cache: cada EAN aparece en miles de sucursales."""

    def __init__(self, catalog: dict[str, Producto]):
        self.catalog = catalog
        self.cache: dict[str, tuple[str, float] | None] = {}

    def __call__(self, ean: str, desc: str, cantidad: str, unidad: str):
        if ean in self.cache:
            return self.cache[ean]
        result = None
        m = match_rules(desc, self.catalog)
        if m.producto_id:
            prod = self.catalog[m.producto_id]
            by_fields = _size_from_fields(cantidad, unidad)
            by_name = parse_size(desc)
            size = by_fields or by_name
            # Si nombre y campos estructurados se contradicen, no se usa (dato sucio).
            if by_fields and by_name and abs(by_fields.base()[0] - by_name.base()[0]) > 0.1 * by_fields.base()[0]:
                size = None
            if size and size.base()[1] == prod.unidad_base and size.base()[0] > 0:
                result = (prod.id, size.base()[0])
        self.cache[ean] = result
        return result


class _RawReader(io.RawIOBase):
    """Adaptador: los miembros de un tar leído en streaming no son 'seekable' y TextIOWrapper lo exige."""

    def __init__(self, fileobj):
        self._f = fileobj

    def readable(self):
        return True

    def readinto(self, buffer):
        data = self._f.read(len(buffer))
        n = len(data)
        buffer[:n] = data
        return n


def _iter_csv_members(tar: tarfile.TarFile):
    """(nombre_carpeta, archivo, fileobj) de productos.csv / comercio.csv, también dentro de .zip anidados."""
    for member in tar:
        if not member.isfile():
            continue
        name = member.name
        if name.endswith(".zip"):
            data = tar.extractfile(member).read()
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                # El SEPA original trae zips rotos de algunas cadenas (documentado por preciazo).
                logger.warning("zip anidado ilegible en el volcado: %s", name)
                continue
            with zf:
                for zn in zf.namelist():
                    base = zn.rsplit("/", 1)[-1]
                    if base in {"productos.csv", "comercio.csv"}:
                        yield name, base, io.BytesIO(zf.read(zn))
        elif name.endswith(("/productos.csv", "/comercio.csv")):
            yield name.rsplit("/", 1)[0], name.rsplit("/", 1)[-1], tar.extractfile(member)


def procesar_volcado(path: Path, fecha: str, matcher: EanMatcher) -> list[dict]:
    """Lee un .tar.zst en streaming y devuelve observaciones agregadas por (EAN, cadena)."""
    import zstandard
    precios: dict[tuple[str, str], list[float]] = {}
    nombres: dict[str, str] = {}
    meta: dict[str, tuple[str, float, str, str]] = {}
    cadenas: dict[str, str] = {}
    with open(path, "rb") as fh:
        reader = zstandard.ZstdDecompressor().stream_reader(fh)
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for folder, base, fileobj in _iter_csv_members(tar):
                text = io.TextIOWrapper(io.BufferedReader(_RawReader(fileobj), 1 << 20),
                                        encoding="utf-8-sig", errors="replace", newline="")
                rows = csv.DictReader(text, delimiter="|")
                if base == "comercio.csv":
                    for row in rows:
                        bandera = (row.get("comercio_bandera_nombre") or "").strip()
                        if not bandera or not (row.get("id_comercio") or "").isdigit():
                            continue   # el SEPA trae filas basura ("Última actualización: ...") al final
                        cadenas[f"{row.get('id_comercio')}-{row.get('id_bandera')}"] = bandera
                    continue
                for row in rows:
                    ean = (row.get("id_producto") or "").strip()
                    if not ean:
                        continue
                    hit = matcher(ean, row.get("productos_descripcion") or "",
                                  row.get("productos_cantidad_presentacion") or "",
                                  row.get("productos_unidad_medida_presentacion") or "")
                    if not hit:
                        continue
                    try:
                        precio = float((row.get("productos_precio_lista") or "").replace(",", "."))
                    except ValueError:
                        continue
                    if precio <= 0:
                        continue
                    cadena_key = f"{row.get('id_comercio')}-{row.get('id_bandera')}"
                    precios.setdefault((ean, cadena_key), []).append(precio)
                    nombres.setdefault(ean, (row.get("productos_descripcion") or "").strip())
                    meta[ean] = (hit[0], hit[1], (row.get("productos_marca") or "").strip(), "")
    out = []
    for (ean, cadena_key), values in precios.items():
        producto_id, contenido, marca, _ = meta[ean]
        precio = float(statistics.median(values))
        cadena = cadenas.get(cadena_key, cadena_key)
        comercio = cadena_key.split("-")[0]
        canal = "mayorista" if cadena.lower() in BANDERAS_MAYORISTAS else "minorista"
        out.append({
            # c<id_comercio> en la fuente: banderas de una misma empresa (Vea/Disco/Jumbo) no se corroboran entre sí.
            "fuente": f"{FUENTE}:c{comercio}:{cadena}", "canal": canal, "fecha": fecha, "ean": ean,
            "descripcion": nombres.get(ean, ""), "marca": marca or None, "precio": precio,
            "contenido_base": contenido, "unidad_base": None, "precio_unidad_base": precio / contenido,
            "producto_id": producto_id, "match_metodo": "reglas", "match_confianza": 0.9,
            "region": "nacional", "sucursal": f"mediana de {len(values)} sucursales",
            "url": None, "obs_key": f"{ean}|{cadena}",
        })
    return out


def _insert(conn: sqlite3.Connection, rows: list[dict], catalog: dict[str, Producto]) -> int:
    for r in rows:
        r["unidad_base"] = catalog[r["producto_id"]].unidad_base
        cols = list(r)
        conn.execute(
            f"INSERT INTO precios_mercado ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            "ON CONFLICT (fuente, fecha, obs_key) DO UPDATE SET precio=excluded.precio, "
            "precio_unidad_base=excluded.precio_unidad_base", [r[c] for c in cols])
    conn.commit()
    return len(rows)


def importar_fechas(conn: sqlite3.Connection, fechas: list[str], progress=None) -> str:
    catalog = load_catalog()
    matcher = EanMatcher(catalog)
    hechas = {r[0] for r in conn.execute("SELECT DISTINCT fecha FROM precios_mercado WHERE fuente LIKE ?",
                                        (f"{FUENTE}:%",))}
    total, procesadas = 0, 0
    with httpx.Client(timeout=900, follow_redirects=True) as client:
        index = fetch_index(client)
        for fecha in fechas:
            if fecha in hechas:
                continue
            url = index.get(fecha)
            if not url:
                logger.warning("no hay volcado SEPA archivado para %s", fecha)
                continue
            path = config.cache_dir("sepa_archivo") / f"{fecha}.tar.zst"
            if not path.exists() or path.stat().st_size < 1_000_000:
                if progress:
                    progress(f"SEPA {fecha}: bajando (~65 MB)")
                tmp = path.with_suffix(".part")
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_bytes(1 << 20):
                            fh.write(chunk)
                tmp.rename(path)
            if progress:
                progress(f"SEPA {fecha}: procesando")
            rows = procesar_volcado(path, fecha, matcher)
            total += _insert(conn, rows, catalog)
            procesadas += 1
            path.unlink(missing_ok=True)   # 65 MB por día: lo agregado queda en la base
    return f"SEPA archivo: {procesadas} días procesados, {total} observaciones (EAN x cadena)"


def importar_necesarios(conn: sqlite3.Connection, max_dias: int = 15, progress=None) -> str:
    """Baja los volcados más cercanos a las compras con producto comparable dentro del rango del archivo."""
    objetivos = [r[0] for r in conn.execute(
        "SELECT DISTINCT COALESCE(p.fecha_apertura, p.fecha_adjudicacion, p.fecha_publicacion) "
        "FROM renglones r JOIN procesos p ON p.id = r.proceso_id "
        "WHERE r.producto_id IS NOT NULL AND COALESCE(p.fecha_apertura, p.fecha_adjudicacion, p.fecha_publicacion) >= '2024-08-01'")
        if r[0]]
    if not objetivos:
        return "SEPA archivo: no hay compras comparables desde agosto 2024"
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        disponibles = list(fetch_index(client))
    fechas = fechas_mas_cercanas(disponibles, objetivos, max_dias)
    return importar_fechas(conn, fechas, progress=progress)
