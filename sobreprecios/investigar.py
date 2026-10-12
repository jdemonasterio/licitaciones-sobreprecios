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

# Motivos cortos para agrupar en el mensaje final (MEJORAS #49.5, item 1c): un trabajo terminado
# con cero renglones comparables tiene que decir "Procesado; sin comparación" con el POR QUÉ
# agrupado, nunca un "listo" verde que insinúe que se encontró algo.
_MOTIVO_CORTO = {
    "sin_datos": "sin precio de mercado en esa fecha", "sin_precio": "no se pudo calcular el precio por unidad",
    "fuera_alcance": "sin producto del catálogo", "atributo_desconocido": "no se sabe qué variedad exige",
    "sin_referencia_variante": "sin precio de ESA variedad en la fecha",
    "sin_referencia_presentacion": "sin precio de ESE envase en la fecha",
    "revisar_unidad": "precio fuera de rango (revisar unidad)",
}


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


def _resumen_comparacion(conn: sqlite3.Connection, pid: int, n_renglones: int, n_productos: int) -> tuple[str, str]:
    """`(estado, mensaje)` para el final de `investigar()` (MEJORAS #49.5, item 1c).

    Si NINGÚN renglón quedó con semáforo (rojo/amarillo/verde), el trabajo terminó bien
    (se importó y se procesó) pero sin comparación: nunca un "listo" verde que insinúe que se
    encontró algo. Se agrupan los motivos y se agregan próximos pasos concretos.
    """
    rows = conn.execute(
        "SELECT COALESCE(k.semaforo, 'gris') semaforo, COALESCE(k.motivo_codigo, 'fuera_alcance') codigo, "
        "COUNT(*) n FROM renglones r LEFT JOIN comparaciones k ON k.renglon_id = r.id "
        "WHERE r.proceso_id=? GROUP BY semaforo, codigo", (pid,)).fetchall()
    comparables = sum(r["n"] for r in rows if r["semaforo"] in ("rojo", "amarillo", "verde"))
    if comparables:
        return "ok", f"Listo: {n_renglones} renglón(es), {n_productos} producto(s) comparable(s)"
    motivos = sorted(((_MOTIVO_CORTO.get(r["codigo"], r["codigo"]), r["n"]) for r in rows),
                     key=lambda t: -t[1])
    detalle = "; ".join(f"{n} {m}" for m, n in motivos) or "sin renglones"
    primer_gris = conn.execute(
        "SELECT r.id FROM renglones r LEFT JOIN comparaciones k ON k.renglon_id=r.id "
        "WHERE r.proceso_id=? ORDER BY r.id LIMIT 1", (pid,)).fetchone()
    siguiente = (f"Próximo paso: revisar renglón por renglón en /revision/{primer_gris['id']}"
                if primer_gris else "Próximo paso: revisar el proceso")
    if any(r["codigo"] == "fuera_alcance" for r in rows):
        siguiente += "; si falta el producto, sumalo a config/productos.yml y volvé a investigar."
    return "sin_comparacion", f"Procesado; sin comparación ({detalle}). {siguiente}"


def _nota_extrapolado(conn: sqlite3.Connection, pid: int) -> str | None:
    """Si la referencia de algún renglón viene de una observación muy alejada de la fecha de la
    compra (en la práctica, el precio de HOY llevado hacia atrás por IPC), lo dice EXPLÍCITO en
    el resultado de Investigar (decisión del owner 2026-10-12): esa referencia ahora puede
    habilitar un color igual que cualquiera otra, así que la advertencia y cómo se construyó el
    número tienen que estar siempre a la vista, nunca escondidas en un tooltip."""
    cfg = config.settings().get("comparacion") or {}
    ventana = int(cfg.get("ventana_directa_dias", 15))
    row = conn.execute(
        "SELECT k.gap_dias, k.evidencia_json, k.fecha_compra FROM renglones r "
        "JOIN comparaciones k ON k.renglon_id=r.id "
        "WHERE r.proceso_id=? AND k.gap_dias IS NOT NULL ORDER BY k.gap_dias DESC LIMIT 1", (pid,)).fetchone()
    if not row or (row["gap_dias"] or 0) <= ventana:
        return None
    try:
        evidencia = json.loads(row["evidencia_json"] or "[]")
    except (TypeError, ValueError):
        evidencia = []
    obs_lejana = next((o for o in evidencia if o.get("factor_ipc")), None)
    if not obs_lejana:
        return None
    return (f"ADVERTENCIA — referencia: precio de hoy ({obs_lejana['fecha']}) llevado a "
           f"{row['fecha_compra']} con IPC (×{obs_lejana['factor_ipc']:.3f}); puede marcar rojo igual que "
           "un precio de la fecha real, revisar antes de concluir")


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
            # sea vieja (llevado a la fecha por IPC). Decisión del owner (2026-10-12): esa
            # referencia puede habilitar rojo igual que cualquier otra (la ajusta otro agente en
            # pipeline/compare.py); acá solo se rotula bien explícito en el resultado
            # (`_nota_extrapolado`, más abajo) para que nunca se lea como un precio de la fecha.
            compare.run(conn, [pid], gap_maximo=3 * 365)
            db.sync_empresas(conn)
            from .normalize import aplicar
            aplicar.run(conn)
            estado, msg = _resumen_comparacion(conn, pid, n, len(productos))
            nota = _nota_extrapolado(conn, pid)
            if nota:
                msg = f"{msg} — {nota}"
            conn.execute("UPDATE investigaciones SET estado=?, mensaje=?, proceso_id=?, finished_at=? WHERE id=?",
                         (estado, msg, pid, db.now_iso(), inv_id))
            return f"{msg}. Ver /licitacion/{pid}"
        except Exception as exc:
            conn.execute("UPDATE investigaciones SET estado='error', mensaje=?, finished_at=? WHERE id=?",
                         (f"{type(exc).__name__}: {exc}"[:1000], db.now_iso(), inv_id))
            conn.commit()
            raise


def _clave_caso(row: dict) -> str:
    """Clave canónica de "es el mismo caso" (MEJORAS #49.1, item 1d): un aviso del BO pegado con
    y sin `?busqueda=1` ya normaliza a la MISMA tupla (seccion, aviso_id, fecha) en
    `normalizar_entrada` — eso es la clave. Texto pegado y PDFs no se agrupan (no hay forma
    barata de saber si dos textos distintos son "el mismo caso" sin comparar contenido byte a
    byte, y dos intentos legítimos distintos no deberían fusionarse)."""
    try:
        normalizada = json.loads(row.get("normalizada") or "{}")
    except (TypeError, ValueError):
        normalizada = {}
    tipo = normalizada.get("tipo")
    if tipo == "bo":
        return f"bo:{normalizada.get('seccion')}:{normalizada.get('aviso_id')}:{normalizada.get('fecha')}"
    if tipo in {"comprar_url", "comprar_numero"}:
        return f"{tipo}:{normalizada.get('url') or normalizada.get('numero')}"
    return f"row:{row['id']}"   # sin agrupar: cada intento es su propio grupo


def lista(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """Investigaciones recientes, agrupando reintentos del MISMO caso (MEJORAS #49.1): pegar el
    mismo aviso del BO con y sin `?busqueda=1` no debe aparecer como dos filas sueltas. Se mira
    un poco más atrás que `limit` para no cortar un grupo a la mitad; la fila más reciente de
    cada grupo manda el estado/mensaje, pero si ESA no tiene `proceso_id` (p. ej. un reintento que
    dio error) se usa el de un intento anterior del mismo grupo si existe.
    """
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM investigaciones ORDER BY id DESC LIMIT ?", (max(limit, 1) * 3,))]
    grupos: dict[str, dict] = {}
    orden: list[str] = []
    for r in rows:   # ya viene DESC: la primera vez que se ve una clave es la más reciente
        clave = _clave_caso(r)
        if clave not in grupos:
            grupos[clave] = dict(r, intentos=[r["id"]])
            orden.append(clave)
        else:
            g = grupos[clave]
            g["intentos"].append(r["id"])
            if not g.get("proceso_id") and r.get("proceso_id"):
                g["proceso_id"] = r["proceso_id"]
    out = []
    for clave in orden[:limit]:
        g = grupos[clave]
        g["n_intentos"] = len(g["intentos"])
        out.append(g)
    return out


def guardar_upload(nombre: str, contenido: bytes) -> Path:
    """Guarda un PDF subido en data/uploads con nombre seguro (sin rutas del cliente)."""
    folder = config.db_path().parent / "uploads"   # junto a la base (respeta LIC_DB_PATH)
    folder.mkdir(parents=True, exist_ok=True)
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(nombre or "archivo.pdf").name)[:80] or "archivo.pdf"
    digest = hashlib.sha1(contenido).hexdigest()[:10]
    path = folder / f"{digest}_{base}"
    path.write_bytes(contenido)
    return path
