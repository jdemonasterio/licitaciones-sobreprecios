"""Boletín Oficial (boletinoficial.gob.ar): búsqueda y descarga de avisos.

Qué sirve de cada sección (verificado 2026-10-11):
  - PRIMERA sección (resoluciones / disposiciones): las que aprueban y
    adjudican una compra detallan proveedor + CUIT + renglón + cantidad + monto,
    y muchas veces descripción, marca y precio unitario. Es la fuente de precios
    unitarios. Abundante en 2020-2022 (emergencia COVID), escasa desde 2024
    (hoy la adjudicación vive en COMPR.AR y el BO solo trae el número de proceso).
  - TERCERA sección (contrataciones): convocatorias y adjudicaciones con
    totales por proveedor, sin renglones. Sirve para descubrir procesos.

Endpoints (no documentados; los usa el frontend del BO):
  POST /busquedaAvanzada/realizarBusqueda  form: params=<json>, array_volver=[]
       -> {"error":0,"content":{"html": ..., "sig_pag", "ult_seccion", "ult_rubro",
           "cantidad_result_seccion": {"1": N}}}
  GET  /detalleAviso/<seccion>/<id>/<YYYYMMDD>   HTML del aviso (inmutable: se cachea)

Sin anti-bot a la fecha; igual vamos despacio (DELAY_S entre requests) porque
es infraestructura pública y no queremos que nos bloqueen a nosotros ni a nadie.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from .. import config, db

logger = logging.getLogger(__name__)

BASE = "https://www.boletinoficial.gob.ar"
DELAY_S = 0.6
FUENTE = "bo_primera"
USER_AGENT = "Mozilla/5.0 (compatible; transparencia-compras/0.1; investigacion de precios publicos)"

# Búsquedas por defecto para descubrir resoluciones que adjudican bienes con detalle.
# El buscador del BO es por palabras (todas presentes) y sensible a tildes: van
# variantes con y sin tilde.
DEFAULT_QUERIES = [
    "adjudícase precio unitario",
    "adjudicase precio unitario",
    "adjudícanse precio unitario",
    "adjudícase renglón marca",
    "adjudicase renglon marca",
    "adjudícase alimentos",
    "adjudícase leche",
    "adjudícase yerba",
    "adjudícase kilogramos",
]

_AVISO_RE = re.compile(r"/detalleAviso/(primera|segunda|tercera)/(\d+)/(\d{8})")


@dataclass
class AvisoRef:
    seccion: str
    aviso_id: str
    fecha: str          # YYYYMMDD
    organismo: str = ""
    norma: str = ""

    @property
    def external_id(self) -> str:
        return f"{self.seccion}/{self.aviso_id}/{self.fecha}"

    @property
    def url(self) -> str:
        return f"{BASE}/detalleAviso/{self.seccion}/{self.aviso_id}/{self.fecha}"

    @property
    def fecha_iso(self) -> str:
        return f"{self.fecha[:4]}-{self.fecha[4:6]}-{self.fecha[6:]}"


class BOClient:
    def __init__(self, delay_s: float = DELAY_S):
        self.delay_s = delay_s
        self._client = httpx.Client(timeout=45, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT})
        self._warm = False

    def _sleep(self):
        time.sleep(self.delay_s)

    def _ensure_session(self):
        if not self._warm:
            self._client.get(BASE + "/")   # cookie de sesión que pide el buscador
            self._warm = True

    def search(self, texto: str, desde: dt.date, hasta: dt.date, seccion: int = 1,
               max_paginas: int = 20) -> list[AvisoRef]:
        """Avisos que contienen TODAS las palabras de `texto` entre dos fechas."""
        self._ensure_session()
        params = {
            "texto": texto, "seccion": [seccion], "rubros": [], "nroNorma": "", "anioNorma": "",
            "ordenamiento": "fecha", "denominacion": "", "tipoContratacion": "",
            "anioContratacion": "", "nroContratacion": "",
            "fechaDesde": desde.strftime("%d/%m/%Y"), "fechaHasta": hasta.strftime("%d/%m/%Y"),
            "fecha": "", "tipoBusqueda": "Avanzada", "numeroPagina": 1, "ultimoRubro": "",
            "busquedaRubro": False, "hayMasResultadosBusqueda": True,
            "ejecutandoLlamadaAsincronicaBusqueda": False, "ultimaSeccion": "",
            "todasLasPalabras": True, "filtroPorRubrosSeccion": False,
            "filtroPorRubroBusqueda": False, "filtroPorSeccionBusqueda": False,
            "busquedaOriginal": True, "comienzaDenominacion": False, "ordenamientoSegunda": True,
            "seccionesOriginales": [seccion], "ultimoItemExterno": None, "ultimoItemInterno": None,
        }
        found: dict[str, AvisoRef] = {}
        for _ in range(max_paginas):
            resp = self._client.post(
                BASE + "/busquedaAvanzada/realizarBusqueda",
                data={"params": json.dumps(params), "array_volver": "[]"},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/busquedaAvanzada"})
            self._sleep()
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("error"):
                raise RuntimeError(f"BO búsqueda falló: {payload.get('mensajes')}")
            content = payload.get("content") or {}
            html = content.get("html") or ""
            if not html:
                break
            for ref in parse_search_html(html):
                found.setdefault(ref.external_id, ref)
            params.update(numeroPagina=content.get("sig_pag"), ultimaSeccion=content.get("ult_seccion", ""),
                          ultimoRubro=content.get("ult_rubro", ""), busquedaOriginal=False)
        return list(found.values())

    def fetch_html(self, ref: AvisoRef) -> str:
        """HTML del aviso, desde cache si ya se bajó (los avisos publicados no cambian)."""
        path = config.cache_dir("bo", ref.seccion) / f"{ref.fecha}_{ref.aviso_id}.html"
        if path.exists() and path.stat().st_size > 2000:
            return path.read_text(encoding="utf-8", errors="replace")
        resp = self._client.get(ref.url)
        self._sleep()
        resp.raise_for_status()
        path.write_text(resp.text, encoding="utf-8")
        return resp.text


def parse_search_html(html: str) -> list[AvisoRef]:
    """Fragmento HTML de resultados -> referencias (sección, id, fecha, organismo, norma)."""
    soup = BeautifulSoup(html, "html.parser")
    refs = []
    for link in soup.find_all("a", href=True):
        match = _AVISO_RE.search(link["href"])
        if not match:
            continue
        items = [p.get_text(" ", strip=True) for p in link.find_all("p")]
        organismo = items[0] if items else ""
        norma = items[1] if len(items) > 1 else ""
        refs.append(AvisoRef(match.group(1), match.group(2), match.group(3),
                             re.sub(r"\s+", " ", organismo).strip(), re.sub(r"\s+", " ", norma).strip()))
    return refs


def parse_aviso_html(html: str) -> dict:
    """Texto plano del aviso + organismo y norma (del <title>)."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = re.sub(r"\s+", " ", title.replace("\xa0", " "))   # el BO mezcla espacios duros
    parts = [p.strip() for p in title.split(" - ")]
    organismo = parts[1] if len(parts) > 2 else ""
    norma = parts[-1] if len(parts) > 2 else ""
    body = soup.find(id="cuerpoDetalleAviso") or soup.find(class_="cuerpoDetalleAviso")
    text = body.get_text("\n", strip=True) if body else soup.get_text("\n", strip=True)
    return {"organismo": organismo, "norma": norma, "texto": text}


def store_document(conn: sqlite3.Connection, ref: AvisoRef, html: str) -> int:
    parsed = parse_aviso_html(html)
    path = config.cache_dir("bo", ref.seccion) / f"{ref.fecha}_{ref.aviso_id}.html"
    return db.upsert(conn, "documentos", {
        "fuente": f"bo_{ref.seccion}",
        "external_id": ref.external_id,
        "url": ref.url,
        "fecha_publicacion": ref.fecha_iso,
        "titulo": f"{parsed['organismo'] or ref.organismo} - {parsed['norma'] or ref.norma}".strip(" -"),
        "texto": parsed["texto"],
        "raw_path": str(path),
        "fetched_at": db.now_iso(),
    }, ("fuente", "external_id"))


def discover(conn: sqlite3.Connection, desde: dt.date, hasta: dt.date,
             queries: list[str] | None = None, client: BOClient | None = None,
             progress=None) -> str:
    """Busca resoluciones de adjudicación en el rango y guarda su texto en `documentos`.

    Parte el rango en meses (el buscador pagina mal con rangos largos).
    """
    client = client or BOClient()
    queries = queries or DEFAULT_QUERIES
    refs: dict[str, AvisoRef] = {}
    cursor = desde
    while cursor <= hasta:
        month_end = min(hasta, (cursor.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
                        - dt.timedelta(days=1))
        for query in queries:
            try:
                for ref in client.search(query, cursor, month_end):
                    refs.setdefault(ref.external_id, ref)
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                logger.warning("búsqueda '%s' %s..%s falló: %s", query, cursor, month_end, exc)
        if progress:
            progress(f"buscando {cursor:%Y-%m}: {len(refs)} avisos")
        cursor = month_end + dt.timedelta(days=1)
    stored = 0
    for i, ref in enumerate(refs.values(), 1):
        try:
            html = client.fetch_html(ref)
        except httpx.HTTPError as exc:
            logger.warning("no se pudo bajar %s: %s", ref.url, exc)
            continue
        store_document(conn, ref, html)
        stored += 1
        if progress and i % 5 == 0:
            progress(f"bajando avisos {i}/{len(refs)}")
    conn.commit()
    return f"BO {desde}..{hasta}: {len(refs)} avisos encontrados, {stored} guardados"


def fetch_known(conn: sqlite3.Connection, external_ids: list[str],
                client: BOClient | None = None) -> int:
    """Baja avisos ya conocidos por id ('primera/245330/20210607')."""
    client = client or BOClient()
    n = 0
    for ext in external_ids:
        seccion, aviso_id, fecha = ext.split("/")
        ref = AvisoRef(seccion, aviso_id, fecha)
        try:
            store_document(conn, ref, client.fetch_html(ref))
            n += 1
        except httpx.HTTPError as exc:
            logger.warning("no se pudo bajar %s: %s", ref.url, exc)
    conn.commit()
    return n
