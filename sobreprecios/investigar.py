"""Investigación a pedido: "revisame ESTA licitación" (link, número, texto o PDF).

Flujo pensado para cuando alguien del equipo recibe un caso puntual (un link de
COMPR.AR, un aviso del Boletín Oficial, un PDF que les mandaron) y quiere saber
YA si el precio está en línea con el mercado de hoy:

  1. normalizar_entrada(): reconoce qué pegó el usuario y lo lleva a una forma
     canónica (los links del BO vienen en 4-5 formatos; los de COMPR.AR traen
     tokens largos; un número de proceso "80-0014-LPU25" va sin link).
  2. traer el documento (BO: aviso HTML cacheado; COMPR.AR: módulo comprar si
     está disponible; texto/PDF: tal cual) y guardarlo en `documentos`.
  3. extraer renglones (regex -> LLM; PDF escaneado -> LLM de PDF).
  4. machear productos y relevar precios de HOY solo para esos productos.
  5. comparar: con precios de la fecha si existen; si la compra es vieja, con el
     precio de hoy llevado a la fecha de la compra por IPC (confianza baja y
     dicho explícitamente en el motivo).

Cada pedido queda en la tabla `investigaciones` con su estado, para que la
pantalla muestre el avance y el link al resultado.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import sqlite3
import urllib.parse
from pathlib import Path

from . import config, db

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS investigaciones (
    id INTEGER PRIMARY KEY,
    entrada TEXT,
    tipo TEXT,
    normalizada TEXT,
    archivo TEXT,
    estado TEXT,
    mensaje TEXT,
    proceso_id INTEGER,
    created_at TEXT,
    finished_at TEXT
);
"""

_BO_PATTERNS = [
    # https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607?busqueda=1
    re.compile(r"boletinoficial\.gob\.ar/+detalleAviso/(primera|segunda|tercera|cuarta)/(\d+)/(\d{8})", re.I),
    # https://www.boletinoficial.gob.ar/#!DetalleNorma/245330/20210607   (formato viejo, sección primera)
    re.compile(r"boletinoficial\.gob\.ar/+#!DetalleNorma(?P<sec>)/(\d+)/(\d{8})", re.I),
    # https://www.boletinoficial.gob.ar/#!DetalleNormaBusquedaAvanzada/245330/20210607
    re.compile(r"boletinoficial\.gob\.ar/+#!DetalleNormaBusquedaAvanzada(?P<sec>)/(\d+)/(\d{8})", re.I),
]
_PROCESO_RE = re.compile(r"\b(\d{1,3}(?:/\d{1,3})?-\d{4}-[A-Z]{2,4}\d{2})\b", re.I)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def normalizar_entrada(texto: str) -> dict:
    """Qué pegó el usuario, en forma canónica.

    Devuelve {"tipo": bo|comprar_url|comprar_numero|texto|vacio, ...}:
      bo             seccion, aviso_id, fecha, url canónica
      comprar_url    url (sin espacios ni fragmentos de tracking)
      comprar_numero numero en mayúsculas ("80-0014-LPU25")
      texto          el texto completo (una norma pegada a mano)
    """
    raw = (texto or "").strip()
    if not raw:
        return {"tipo": "vacio"}
    first = raw.split()[0] if len(raw) < 600 else ""
    for i, rx in enumerate(_BO_PATTERNS):
        m = rx.search(first or raw[:600])
        if m:
            groups = [g for g in m.groups() if g is not None]
            if i == 0:
                seccion, aviso_id, fecha = groups[0].lower(), groups[1], groups[2]
            else:
                seccion, aviso_id, fecha = "primera", groups[-2], groups[-1]
            return {"tipo": "bo", "seccion": seccion, "aviso_id": aviso_id, "fecha": fecha,
                    "url": f"https://www.boletinoficial.gob.ar/detalleAviso/{seccion}/{aviso_id}/{fecha}"}
    if re.search(r"comprar\.gob\.ar", first, re.I):
        url = first if first.lower().startswith("http") else "https://" + first.lstrip("/")
        url = re.sub(r"#.*$", "", url)
        # Solo se pide a comprar.gob.ar (nada de URLs que "mencionan" el dominio en un parámetro).
        host = urllib.parse.urlsplit(url).hostname or ""
        if host.lower() not in {"comprar.gob.ar", "www.comprar.gob.ar"}:
            return {"tipo": "invalido", "motivo": f"el link no es de comprar.gob.ar ({host})"}
        return {"tipo": "comprar_url", "url": urllib.parse.urlunsplit(urllib.parse.urlsplit(url)._replace(scheme="https"))}
    m = _PROCESO_RE.search(first) if first and len(raw) < 80 else None
    if m:
        return {"tipo": "comprar_numero", "numero": m.group(1).upper()}
    return {"tipo": "texto", "texto": raw}


def _store_manual_document(conn: sqlite3.Connection, texto: str, titulo: str, origen: str) -> sqlite3.Row:
    ext = "manual/" + hashlib.sha1(texto.encode("utf-8")).hexdigest()[:16]
    doc_id = db.upsert(conn, "documentos", {
        "fuente": "bo_primera", "external_id": ext, "url": None,
        "fecha_publicacion": dt.date.today().isoformat(), "titulo": titulo or f"Carga manual ({origen})",
        "texto": texto, "raw_path": None, "fetched_at": db.now_iso()}, ("fuente", "external_id"))
    return conn.execute("SELECT * FROM documentos WHERE id=?", (doc_id,)).fetchone()


def _documento(conn: sqlite3.Connection, entrada: dict, archivo: Path | None, progress) -> sqlite3.Row:
    from .sources import boletin_oficial
    if archivo:
        progress("leyendo el PDF")
        # Cadena del benchmark de PDFs (2026-10-11): pdfplumber por columnas -> OCR -> LLM de PDFs.
        from .pdf_text import extraer_texto
        res = extraer_texto(archivo, conn=conn)
        texto = res.get("texto") or ""
        if len(texto.strip()) < 100:
            raise RuntimeError("No se pudo leer texto del PDF (" + "; ".join(res.get("advertencias") or []) + ")")
        return _store_manual_document(conn, texto, f"PDF {archivo.name} ({res.get('metodo')})", "pdf")
    if entrada["tipo"] == "bo":
        progress("bajando el aviso del Boletín Oficial")
        ref = boletin_oficial.AvisoRef(entrada["seccion"], entrada["aviso_id"], entrada["fecha"])
        client = boletin_oficial.BOClient()
        doc_id = boletin_oficial.store_document(conn, ref, client.fetch_html(ref))
        return conn.execute("SELECT * FROM documentos WHERE id=?", (doc_id,)).fetchone()
    if entrada["tipo"] in {"comprar_url", "comprar_numero"}:
        progress("consultando COMPR.AR")
        try:
            from .sources import comprar   # módulo en construcción (ver docs/guia_comprar.md)
        except ImportError as exc:
            raise RuntimeError("La lectura automática de COMPR.AR todavía no está disponible: "
                               "pegá el texto del proceso o subí el PDF del acta/dictamen.") from exc
        detalle = comprar.detalle_proceso(entrada.get("url") or entrada.get("numero"))
        # COMPR.AR ya viene estructurado: se guarda directo, sin LLM. Se devuelve un marcador.
        return {"_comprar_detalle": detalle}
    return _store_manual_document(conn, entrada["texto"], "Texto pegado", "texto")


def investigar(entrada_texto: str, archivo: str | None = None, progress=None) -> str:
    """Corre la investigación completa y devuelve un mensaje con el link al resultado."""
    from . import llm
    from .pipeline import compare, extract, match
    from .sources import precios_hoy
    progress = progress or (lambda _msg: None)
    entrada = normalizar_entrada(entrada_texto) if entrada_texto else {"tipo": "pdf"}
    path = Path(archivo) if archivo else None
    with db.session() as conn:
        ensure_schema(conn)
        inv_id = conn.execute(
            "INSERT INTO investigaciones (entrada, tipo, normalizada, archivo, estado, created_at) "
            "VALUES (?, ?, ?, ?, 'corriendo', ?)",
            ((entrada_texto or "")[:2000], entrada["tipo"], json.dumps(entrada, ensure_ascii=False)[:2000],
             path.name if path else None, db.now_iso())).lastrowid
        conn.commit()
        try:
            if entrada["tipo"] == "vacio" and not path:
                raise ValueError("No pegaste nada: un link, un número de proceso, el texto o un PDF.")
            if entrada["tipo"] == "invalido":
                raise ValueError(f"Link no aceptado: {entrada['motivo']}")
            doc = _documento(conn, entrada, path, progress)
            if isinstance(doc, dict) and "_comprar_detalle" in doc:
                from .pipeline import comprar_import
                pid = comprar_import.store_detalle(conn, doc["_comprar_detalle"])
                n = conn.execute("SELECT COUNT(*) FROM renglones WHERE proceso_id=?", (pid,)).fetchone()[0]
                if not n:
                    raise RuntimeError("El proceso de COMPR.AR todavía no tiene adjudicación (dictamen) publicada.")
            else:
                progress("extrayendo renglones")
                llm.reset_budget()
                titulo = doc["titulo"] or ""
                organismo, _, norma = titulo.partition(" - ")
                extraction = extract.extract_document(doc["texto"] or "", organismo, norma, conn)
                n = extract.store_extraction(conn, doc, extraction)
                proceso = conn.execute("SELECT id FROM procesos WHERE fuente=? AND external_id=?",
                                       (doc["fuente"], doc["external_id"])).fetchone()
                if not proceso or not n:
                    raise RuntimeError(f"No encontré renglones adjudicados en el documento ({extraction.get('metodo')}).")
                pid = proceso["id"]
                # Si el BO solo cita el número de COMPR.AR, completar desde COMPR.AR.
                numero = conn.execute("SELECT numero FROM procesos WHERE id=?", (pid,)).fetchone()[0]
                sin_desc = conn.execute("SELECT COUNT(*) FROM renglones WHERE proceso_id=? AND "
                                        "(descripcion LIKE 'renglón % (sin descripción%' OR precio_unitario IS NULL)",
                                        (pid,)).fetchone()[0]
                if numero and sin_desc:
                    from .pipeline import comprar_import
                    from .sources import comprar
                    progress(f"completando desde COMPR.AR ({numero})")
                    try:
                        comprar_import.store_detalle(conn, comprar.detalle_proceso(numero), proceso_id=pid)
                        n = conn.execute("SELECT COUNT(*) FROM renglones WHERE proceso_id=?", (pid,)).fetchone()[0]
                    except Exception as exc:   # sin COMPR.AR queda lo del BO
                        logger.warning("no se pudo completar %s desde COMPR.AR: %s", numero, exc)
            conn.execute("UPDATE procesos SET origen='investigacion' WHERE id=?", (pid,))
            conn.execute("UPDATE renglones SET match_metodo=NULL WHERE proceso_id=?", (pid,))
            conn.commit()
            progress("identificando productos")
            match.match_renglones(conn)
            productos = [r[0] for r in conn.execute(
                "SELECT DISTINCT producto_id FROM renglones WHERE proceso_id=? AND producto_id IS NOT NULL", (pid,))]
            if productos:
                progress(f"buscando precios de hoy para {len(productos)} producto(s)")
                precios_hoy.run_productos(conn, productos)
            progress("comparando")
            # Para un caso puntual se acepta comparar contra el precio de hoy aunque la compra
            # sea vieja (llevado a la fecha por IPC, confianza baja y avisado en el motivo).
            compare.run(conn, [pid], gap_maximo=3 * 365)
            db.sync_empresas(conn)
            from .normalize import aplicar
            aplicar.run(conn)
            msg = f"Listo: {n} renglón(es), {len(productos)} producto(s) comparable(s)"
            conn.execute("UPDATE investigaciones SET estado='ok', mensaje=?, proceso_id=?, finished_at=? WHERE id=?",
                         (msg, pid, db.now_iso(), inv_id))
            return f"{msg}. Ver /licitacion/{pid}"
        except Exception as exc:
            conn.execute("UPDATE investigaciones SET estado='error', mensaje=?, finished_at=? WHERE id=?",
                         (f"{type(exc).__name__}: {exc}"[:1000], db.now_iso(), inv_id))
            conn.commit()
            raise


def lista(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute("SELECT * FROM investigaciones ORDER BY id DESC LIMIT ?", (limit,))]


def guardar_upload(nombre: str, contenido: bytes) -> Path:
    """Guarda un PDF subido en data/uploads con nombre seguro (sin rutas del cliente)."""
    folder = config.db_path().parent / "uploads"   # junto a la base (respeta LIC_DB_PATH)
    folder.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(nombre or "archivo.pdf").name)[:80] or "archivo.pdf"
    digest = hashlib.sha1(contenido).hexdigest()[:10]
    path = folder / f"{digest}_{base}"
    path.write_bytes(contenido)
    return path
