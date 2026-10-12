"""Precios Cuidados / Precios Justos / Precios Máximos: un dato de mercado MÁS, SOLO cuando
no hay nada mejor en la fecha (decisión de jdemonasterio, 2026-10-12, ver DESIGN_sobreprecios.md
§2 y config/precios_programa.yml). En el mercado libre las empresas suelen querer cobrar MÁS, no
menos: estos precios pactados/techos funcionan más como un piso que como el mínimo real. Por eso
se guardan con canal="programa" (nunca "mayorista" ni "minorista") y el comparador
(pipeline/compare.py) los usa como FALLBACK: si hay cualquier observación real de mercado en la
fecha, las de programa se ignoran.

Ediciones: config/precios_programa.yml (id, programa, vigencia, formato, urls, notas de qué se
probó). Tres formatos de fuente, elegidos por lo que de verdad hay en prensa (no todos los
"listados completos" citados resultaron ser lo que su nombre sugiere):

  pdf_texto     PDF de texto de la Secretaría de Comercio, distribuido por la prensa via Google
                Drive. Columnas por posición (x0) de palabra, cabecera detectada por nombre
                ("Descripción"/"Producto", "Marca", "Proveedor", "Precio"): no todas las
                ediciones tienen las mismas columnas (ver notas en el YAML), así que la cantidad
                de columnas no está hardcodeada.
  infogram      Widget Infogram embebido en una nota de prensa, recuperado via Wayback Machine
                (el widget es JS puro: no hay snapshot de Wayback para la API, pero el HTML del
                iframe trae el JSON de datos embebido tal cual).
  texto_diario  Lista de productos en texto plano dentro del cuerpo de la nota: una lista
                <li>Nombre, $ Precio</li> (El Cronista) o un párrafo separado por ";"
                (El Economista). Mismo parser para los dos: un regex de "texto antes de un
                monto en $" alcanza porque el filtro real es otro (ver _mapear_fila): el texto
                tiene que matchear el catálogo Y el tamaño tiene que parsear en la unidad base
                del producto. Basura de navegación/otras notas no pasa ese filtro.

Vigencia, no solo fecha de publicación: el comparador solo usa observaciones dentro de
±ventana_directa_dias (15, config/settings.yml) de la fecha de la compra. Un precio pactado por
120 días no sirve si se guarda una sola vez el día que salió la nota. Por eso `importar` REPLICA
cada fila el día 1 y el 16 de cada mes dentro de [vigencia_desde, vigencia_hasta]: así cualquier
compra de esos meses cae dentro de la ventana de alguna réplica. `obs_key` se deriva del texto
original de la fila (hash corto), no del índice de la lista: reimportar la misma edición pisa las
mismas filas en vez de duplicarlas.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html as html_mod
import logging
import re
import sqlite3
import time
from pathlib import Path

import httpx

from .. import config
from ..catalog import Producto, load_catalog, match_rules
from ..units import parse_ars, parse_number, parse_size

logger = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (compatible; transparencia-compras/0.1)"}
DELAY_S = 1.0   # prudencia con sitios de terceros (prensa, Wayback, Google Drive)

_HEADER_ALIASES = {
    "descripcion": "descripcion", "producto": "descripcion",
    "marca": "marca", "proveedor": "proveedor", "precio": "precio",
}
# "texto antes de un $monto": un <li>Nombre x Tamaño, $ Precio</li> o un párrafo
# "Nombre Tamaño: $Precio;" caen en el mismo patrón. <> y ; cortan entre filas.
_PRICE_ROW_RE = re.compile(r"([^<>$;]{3,90}?)\s*\$\s*([0-9][0-9.,]*)")
_INFOGRAM_ROW_RE = re.compile(
    r'\[\s*"((?:[^"\\]|\\.)*)"\s*,\s*\{[^}]*?"value":\s*"?(-?[0-9][0-9.,]*)"?[^}]*\}\s*\]')


def _ediciones() -> dict:
    return config.load_yaml("precios_programa.yml").get("ediciones") or {}


# Nombres legibles para el webui (MEJORAS #49.7, item 4): nunca mostrar el id interno
# ("precios_cuidados_2021_01" o "programa:precios_cuidados:precios_cuidados_2021_01") como
# única referencia — el id sigue disponible en un tooltip para quien lo necesite.
PROGRAMA_TXT = {"precios_cuidados": "Precios Cuidados", "precios_justos": "Precios Justos",
               "precios_maximos": "Precios Máximos"}
REGION_TXT = {"nacional": "lista nacional", "amba": "AMBA", "caba": "CABA"}
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]


def etiqueta_edicion(fuente: str) -> str:
    """Nombre legible de una edición de precios de programa.

    `fuente` es "programa:<programa>:<edicion_id>" (como vive en `precios_mercado.fuente`) o
    directamente un `edicion_id` de `config/precios_programa.yml`. Devuelve algo como
    "Precios Cuidados — enero 2021 (lista nacional)" en vez del id interno.
    """
    partes = (fuente or "").split(":", 2)
    edicion_id = partes[2] if len(partes) > 2 else (partes[-1] if partes else fuente)
    ed = _ediciones().get(edicion_id) or {}
    programa_id = ed.get("programa") or (partes[1] if len(partes) > 1 else edicion_id)
    programa = PROGRAMA_TXT.get(programa_id, programa_id.replace("_", " ").title())
    vigencia = ed.get("vigencia_desde") or ed.get("fecha_publicacion")
    if vigencia and len(vigencia) >= 7:
        try:
            periodo = f"{_MESES[int(vigencia[5:7]) - 1]} {vigencia[:4]}"
        except (IndexError, ValueError):
            periodo = edicion_id
    else:
        periodo = edicion_id
    region = REGION_TXT.get(ed.get("region"), ed.get("region") or "")
    return f"{programa} — {periodo}" + (f" ({region})" if region else "")


def _descargar(edicion_id: str, urls: list[str], ext: str, progress=None) -> Path | None:
    """Descarga (con caché) la primera URL de `urls` que devuelva contenido del tipo esperado.

    Nunca levanta: un link de prensa caído (404, página de error de Drive) es la norma, no la
    excepción, acá. Devuelve None si ninguna URL sirvió (el caller lo registra y sigue).
    """
    path = config.cache_dir("precios_programa") / f"{edicion_id}{ext}"
    if path.exists() and path.stat().st_size > 0:
        return path
    if not urls:
        return None
    with httpx.Client(timeout=60, headers=UA, follow_redirects=True) as client:
        for url in urls:
            try:
                if progress:
                    progress(f"precios de programa {edicion_id}: bajando")
                resp = client.get(url)
                time.sleep(DELAY_S)
                resp.raise_for_status()
                content = resp.content
                if ext == ".pdf" and not content[:5].startswith(b"%PDF"):
                    logger.warning("edicion %s: %s no devolvio un PDF (link caido o pagina de error)",
                                   edicion_id, url)
                    continue
                path.write_bytes(content)
                return path
            except httpx.HTTPError as exc:
                logger.warning("edicion %s: fallo al bajar %s (%s)", edicion_id, url, exc)
                continue
    return None


# ── Parsers de formato: devuelven filas crudas {descripcion, marca, precio} ─────────────────

def _detectar_columnas(page) -> list[tuple[float, str]] | None:
    """[(x0, campo), ...] a partir de la fila de encabezado (primera fila de la página).

    Por posición, no por cantidad fija de columnas: la edición de 2022-01 tiene 4 columnas
    (Descripción/Marca/Proveedor/Precio) y la de 2022-10 solo 2 (Producto/Precio) — ambas se
    detectan igual, buscando las palabras de cabecera conocidas.
    """
    from ..textnorm import norm
    words = page.extract_words()
    if not words:
        return None
    top0 = min(w["top"] for w in words)
    header = [w for w in words if abs(w["top"] - top0) < 3]
    cols: list[tuple[float, str]] = []
    vistos = set()
    for w in header:
        campo = _HEADER_ALIASES.get(norm(w["text"]))
        if campo and campo not in vistos:
            cols.append((w["x0"], campo))
            vistos.add(campo)
    return sorted(cols) if len(cols) >= 2 else None


def _agrupar_por_huecos(words: list[dict], hueco_min: float = 15.0) -> list[list[dict]]:
    """Palabras de UNA fila (lo que `extract_words` devuelve para esa `top`) partidas en
    grupos de izquierda a derecha: un hueco en blanco más ancho que `hueco_min` separa una
    columna de la siguiente. Más robusto que un límite fijo por x0 de cabecera: una cabecera
    puede estar centrada sobre una columna ancha mientras el texto de datos arranca más a la
    izquierda (visto en la edición de 2022-10, "PRODUCTO" a x0=229 pero los nombres empiezan en
    x0=80) — lo que de verdad separa una columna de otra es el blanco entre los datos, no la
    posición de la palabra de cabecera.
    """
    ordenadas = sorted(words, key=lambda w: w["x0"])
    grupos: list[list[dict]] = []
    prev_x1 = None
    for w in ordenadas:
        if not grupos or (prev_x1 is not None and w["x0"] - prev_x1 > hueco_min):
            grupos.append([])
        grupos[-1].append(w)
        prev_x1 = w["x1"]
    return grupos


def _filas_pdf(path: Path, max_paginas: int = 80) -> list[dict]:
    import pdfplumber
    from ..textnorm import norm

    filas: list[dict] = []
    with pdfplumber.open(str(path)) as pdf:
        campos: list[str] = []
        for page in pdf.pages[:max_paginas]:
            nuevas = _detectar_columnas(page)
            if nuevas:
                campos = [c[1] for c in nuevas]
            if not campos:
                continue
            por_fila: dict[int, list[dict]] = {}
            for w in page.extract_words():
                por_fila.setdefault(round(w["top"]), []).append(w)
            for top in sorted(por_fila):
                grupos = _agrupar_por_huecos(por_fila[top])
                if len(grupos) != len(campos):
                    continue   # fila con menos/más columnas de las esperadas: no se adivina
                row = {campo: " ".join(w["text"] for w in grupo) for campo, grupo in zip(campos, grupos)}
                desc = (row.get("descripcion") or "").strip()
                if not desc or norm(desc) in {"descripcion", "producto"}:
                    continue   # cabecera repetida en cada página
                precio = parse_ars(row.get("precio"))
                if precio and precio > 0:
                    filas.append({"descripcion": desc, "marca": (row.get("marca") or "").strip() or None,
                                 "precio": precio})
    return filas


def _bloque_balanceado(texto: str, inicio: int) -> str | None:
    """Substring de `texto` desde `inicio` (un '[') hasta que cierra ese mismo corchete."""
    depth, i = 0, inicio
    n = len(texto)
    while i < n:
        if texto[i] == "[":
            depth += 1
        elif texto[i] == "]":
            depth -= 1
            if depth == 0:
                return texto[inicio:i + 1]
        i += 1
    return None


def _filas_infogram(html: str) -> list[dict]:
    idx = html.find('"data":[[')
    if idx == -1:
        return []
    bloque = _bloque_balanceado(html, idx + len('"data":'))
    if not bloque:
        return []
    filas = []
    for desc_raw, val_raw in _INFOGRAM_ROW_RE.findall(bloque):
        desc = re.sub(r"\s+", " ", desc_raw.replace("\\n", " ")).strip()
        precio = parse_number(val_raw)
        if desc and precio and precio > 0:
            filas.append({"descripcion": desc, "marca": None, "precio": precio})
    return filas


def _filas_texto_diario(html: str) -> list[dict]:
    filas, vistas = [], set()
    for desc_raw, val_raw in _PRICE_ROW_RE.findall(html):
        desc = html_mod.unescape(desc_raw)
        # Algunos diarios repiten la nota como JSON embebido ({"alignment":"left","content":"Aceite...")
        # además del HTML: el regex agarra la cola del JSON pegada a la descripción. Se queda con el
        # texto del último "content" y se descarta lo que siga teniendo pinta de JSON.
        if '"content":"' in desc:
            desc = desc.rsplit('"content":"', 1)[1]
        if any(c in desc for c in '{}"'):
            continue
        desc = re.sub(r"\s+", " ", desc.strip(" ,:;\n\t"))
        precio = parse_number(val_raw)
        if desc and 2 <= len(desc) <= 90 and precio and precio > 0 and (desc, precio) not in vistas:
            vistas.add((desc, precio))
            filas.append({"descripcion": desc, "marca": None, "precio": precio})
    return filas


def _filas(edicion: dict, path: Path) -> list[dict]:
    formato = edicion["formato"]
    if formato == "pdf_texto":
        return _filas_pdf(path)
    texto = path.read_text(encoding="utf-8", errors="replace")
    if formato == "infogram":
        return _filas_infogram(texto)
    if formato == "texto_diario":
        return _filas_texto_diario(texto)
    raise ValueError(f"formato desconocido: {formato!r}")


# ── Macheo al catálogo + réplica por vigencia + guardado ────────────────────────────────────

def _mapear_fila(fila: dict, catalog: dict[str, Producto]) -> dict | None:
    """Fila cruda -> fila con producto canónico, o None si no machea o el tamaño no parsea.

    Mismo criterio que precios_hoy._obs_row / sepa_archivo.EanMatcher: reglas del catálogo sobre
    la descripción completa (ya trae la marca adentro en los formatos probados) y el tamaño tiene
    que parsear en la unidad base del producto — si no, no se guarda nada (nunca se adivina).
    """
    desc, precio = fila["descripcion"], fila["precio"]
    match = match_rules(desc, catalog)
    if not match.producto_id:
        return None
    prod = catalog[match.producto_id]
    size = parse_size(desc)
    if not size:
        return None
    contenido, unidad = size.base()
    if unidad != prod.unidad_base or not contenido or contenido <= 0:
        return None
    return {"descripcion": desc, "marca": fila.get("marca"), "precio": precio, "producto_id": prod.id,
            "contenido_base": contenido, "unidad_base": unidad,
            "precio_unidad_base": round(precio / contenido, 4)}


def _fechas_vigencia(desde: str, hasta: str) -> list[str]:
    """El 1 y el 16 de cada mes dentro de [desde, hasta] (ambos inclusive)."""
    d0, d1 = dt.date.fromisoformat(desde), dt.date.fromisoformat(hasta)
    out: list[str] = []
    cur = dt.date(d0.year, d0.month, 1)
    while cur <= d1:
        for day in (1, 16):
            f = dt.date(cur.year, cur.month, day)
            if d0 <= f <= d1:
                out.append(f.isoformat())
        cur = dt.date(cur.year + 1, 1, 1) if cur.month == 12 else dt.date(cur.year, cur.month + 1, 1)
    return out


def _clave(fila_mapeada: dict) -> str:
    cruda = f"{fila_mapeada['descripcion']}|{fila_mapeada['marca']}|{fila_mapeada['precio']}"
    return hashlib.sha1(cruda.encode("utf-8")).hexdigest()[:20]


def _insertar(conn: sqlite3.Connection, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        cols = list(r)
        conn.execute(
            f"INSERT INTO precios_mercado ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            "ON CONFLICT (fuente, fecha, obs_key) DO UPDATE SET precio=excluded.precio, "
            "precio_unidad_base=excluded.precio_unidad_base", [r[c] for c in cols])
        n += 1
    conn.commit()
    return n


def importar(conn: sqlite3.Connection, edicion_id: str | None = None, progress=None) -> str:
    """Baja (con caché), parsea, machea y guarda una edición (o todas si `edicion_id` es None).

    No corre cuarentena ni re-machea el resto de la base: eso lo hace el CLI
    (`precios-programa`) o el pipeline, igual que con las otras fuentes de mercado.
    """
    ediciones = _ediciones()
    if edicion_id:
        if edicion_id not in ediciones:
            return f"edicion desconocida: {edicion_id} (ver config/precios_programa.yml)"
        objetivo = {edicion_id: ediciones[edicion_id]}
    else:
        objetivo = ediciones
    if not objetivo:
        return "sin ediciones configuradas en config/precios_programa.yml"
    catalog = load_catalog()
    resumen = []
    for eid, ed in objetivo.items():
        ext = ".pdf" if ed["formato"] == "pdf_texto" else ".html"
        path = _descargar(eid, ed.get("urls") or [], ext, progress=progress)
        if not path:
            resumen.append(f"{eid}: sin fuente descargable (ver notas en el YAML)")
            continue
        crudas = _filas(ed, path)
        mapeadas = [m for f in crudas if (m := _mapear_fila(f, catalog))]
        fechas = _fechas_vigencia(ed["vigencia_desde"], ed["vigencia_hasta"])
        fuente = f"programa:{ed['programa']}:{eid}"
        url = (ed.get("urls") or [None])[0]
        obs = []
        for m in mapeadas:
            clave = _clave(m)
            sufijo = f" [vigente {ed['vigencia_desde']} a {ed['vigencia_hasta']}]"
            for fecha in fechas:
                obs.append({
                    "fuente": fuente, "canal": "programa", "fecha": fecha, "ean": None,
                    "descripcion": m["descripcion"] + sufijo, "marca": m["marca"], "precio": m["precio"],
                    "contenido_base": m["contenido_base"], "unidad_base": m["unidad_base"],
                    "precio_unidad_base": m["precio_unidad_base"], "producto_id": m["producto_id"],
                    "match_metodo": "reglas", "match_confianza": 0.6, "region": ed.get("region"),
                    "sucursal": None, "url": url, "obs_key": clave,
                })
        n = _insertar(conn, obs)
        resumen.append(f"{eid}: {len(crudas)} filas, {len(mapeadas)} con producto, "
                       f"{n} observaciones ({len(fechas)} fechas x producto)")
        if progress:
            progress(f"precios de programa {eid}: listo ({len(mapeadas)} productos)")
    return "Precios de programa: " + " · ".join(resumen)
