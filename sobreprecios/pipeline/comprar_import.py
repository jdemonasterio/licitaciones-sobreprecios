"""Guarda procesos de COMPR.AR en la base (estructurado, sin LLM) y enriquece los del BO.

COMPR.AR trae todo lo que el BO dejó de publicar desde 2024: descripción de
cada renglón (con código SIByS), cantidad, la oferta ganadora con precio
unitario (dictamen) y TODAS las ofertas de cada renglón (cuadro comparativo).

Dos usos:
  - store_detalle(): un proceso de COMPR.AR -> procesos + renglones + ofertas.
  - enriquecer_bo(): los procesos del BO que solo citan "renglón 1 por $X" y un
    número tipo 80-0014-LPU25 se completan con COMPR.AR en el mismo proceso
    (una compra = una fila, no dos).

Las ofertas perdedoras habilitan un indicador que no depende del mercado:
cuánto más caro fue lo adjudicado que la oferta más baja del mismo renglón.
Ojo: la más baja pudo haber sido desestimada con razón (no cumplía el pliego);
el webui lo dice y remite al dictamen.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import re
import sqlite3

from .. import db
from .extract import parse_marca_exigida

logger = logging.getLogger(__name__)

OFERTAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ofertas (
    id INTEGER PRIMARY KEY,
    proceso_id INTEGER NOT NULL REFERENCES procesos(id) ON DELETE CASCADE,
    nro_renglon INTEGER,
    proveedor TEXT,
    cuit TEXT,
    cantidad REAL,
    precio_unitario REAL,
    total REAL,
    marca TEXT,
    ganadora INTEGER DEFAULT 0,
    estado TEXT,
    causa_desestimacion TEXT,
    oferta_key TEXT NOT NULL,
    UNIQUE (proceso_id, oferta_key)
);
CREATE INDEX IF NOT EXISTS ix_ofertas_proceso ON ofertas(proceso_id, nro_renglon);

CREATE TABLE IF NOT EXISTS ordenes_compra (
    id INTEGER PRIMARY KEY,
    proceso_id INTEGER NOT NULL REFERENCES procesos(id) ON DELETE CASCADE,
    numero_oc TEXT NOT NULL,
    proveedor TEXT,
    cuit TEXT,
    fecha TEXT,
    monto REAL,
    moneda TEXT,
    tipo TEXT,
    estado TEXT,
    UNIQUE (proceso_id, numero_oc)
);

CREATE TABLE IF NOT EXISTS alertas_proceso (
    id INTEGER PRIMARY KEY,
    proceso_id INTEGER NOT NULL REFERENCES procesos(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    detalle TEXT,
    monto REAL,
    proveedor TEXT,
    cuit TEXT,
    created_at TEXT,
    UNIQUE (proceso_id, tipo, cuit, detalle)
);

-- Actos administrativos del proceso (autorización de pliego/llamado, adjudicación y OC...).
-- Sin PDF descargable sin login (ver docstring de sources/comprar.py): `url` apunta al
-- proceso en COMPR.AR con un fragmento que salta a la sección, para que una persona lo abra.
CREATE TABLE IF NOT EXISTS comprar_actos (
    id INTEGER PRIMARY KEY,
    proceso_id INTEGER NOT NULL REFERENCES procesos(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL,
    numero TEXT NOT NULL,
    fecha TEXT,
    descripcion TEXT,
    url TEXT,
    UNIQUE (proceso_id, tipo, numero)
);
"""

_COMPRAR_NUM_RE = re.compile(r"^\d{1,3}(?:/\d{1,3})?-\d{4}-[A-Z]{2,4}\d{2}$")
_TIPOS = [("licitacion_publica", "licitacion publica"), ("licitacion_privada", "licitacion privada"),
          ("contratacion_directa", "contratacion directa"), ("compulsa", "compulsa"),
          ("adjudicacion_simple", "adjudicacion simple"), ("emergencia", "emergencia"),
          ("concurso", "concurso")]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(OFERTAS_SCHEMA)


def _iso(fecha: str | None) -> str | None:
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", fecha or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def _tipo(texto: str | None) -> str | None:
    from ..textnorm import norm
    t = norm(texto)
    for key, needle in _TIPOS:
        if needle in t:
            return key
    return t.replace(" ", "_") or None


def _cuit_fmt(cuit: str | None) -> str | None:
    digits = re.sub(r"\D", "", cuit or "")
    return f"{digits[:2]}-{digits[2:10]}-{digits[10:]}" if len(digits) == 11 else (cuit or None)


def _norm_prov(name: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def store_detalle(conn: sqlite3.Connection, detalle: dict, proceso_id: int | None = None) -> int:
    """Guarda un proceso de COMPR.AR. Si se pasa proceso_id (un proceso del BO), lo completa."""
    ensure_schema(conn)
    fechas = detalle.get("fechas") or {}
    desestimadas = detalle.get("ofertas_desestimadas") or []
    row = {
        "numero": detalle.get("numero"),
        "organismo": detalle.get("organismo") or detalle.get("uoc") or None,
        "jurisdiccion": detalle.get("uoc") or None,
        "tipo_procedimiento": _tipo(detalle.get("tipo_procedimiento")),
        "objeto": detalle.get("objeto"),
        "fecha_apertura": _iso(fechas.get("apertura")),
        "fecha_adjudicacion": _iso(fechas.get("adjudicacion")),
        "plazo_pago_dias": detalle.get("plazo_pago_dias"),
        "plazo_pago_texto": detalle.get("plazo_pago_texto"),
        "ofertas_desestimadas_n": len(desestimadas),
        "updated_at": db.now_iso(),
    }
    if proceso_id:
        # Un proceso del BO conserva su organismo (el de la norma); COMPR.AR completa el resto.
        sets = ", ".join(f"{k}=COALESCE({k}, ?)" if k in {"organismo"} else f"{k}=?" for k in row)
        conn.execute(f"UPDATE procesos SET {sets} WHERE id=?", list(row.values()) + [proceso_id])
    else:
        row.update(fuente="comprar", external_id=detalle.get("numero") or detalle.get("url"),
                   fecha_publicacion=_iso(fechas.get("publicacion")), url=detalle.get("url"),
                   created_at=db.now_iso())
        proceso_id = db.upsert(conn, "procesos", row, ("fuente", "external_id"))
    conn.execute("UPDATE procesos SET origen=COALESCE(origen, 'comprar') WHERE id=?", (proceso_id,))
    store_actos(conn, proceso_id, detalle.get("actos_administrativos") or [])

    renglones = {r.get("nro"): r for r in detalle.get("renglones") or []}
    adjudicaciones = detalle.get("adjudicaciones") or []
    ofertas = detalle.get("ofertas") or []
    store_ordenes(conn, proceso_id, detalle.get("ordenes_compra") or [], adjudicaciones, ofertas)
    # Completitud (duda del prime): si hay renglones con ofertas que el dictamen no menciona, o
    # faltaron páginas, o quedaron desiertos/fracasados. Para revisar, no es una conclusión.
    con_ofertas = {o.get("renglon") for o in ofertas if o.get("renglon") is not None}
    con_dictamen = {a.get("renglon") for a in adjudicaciones if a.get("renglon") is not None}
    faltan = sorted(con_ofertas - con_dictamen)
    if adjudicaciones and faltan:
        conn.execute("INSERT OR IGNORE INTO alertas_proceso (proceso_id, tipo, detalle, monto, proveedor, cuit, created_at) "
                     "VALUES (?, 'dictamen_incompleto', ?, NULL, NULL, '', ?)",
                     (proceso_id, f"{len(faltan)} renglones con ofertas sin recomendación en el dictamen: "
                                  f"{', '.join(str(x) for x in faltan[:20])}{'…' if len(faltan) > 20 else ''} "
                                  f"(pliego: {len(renglones)} renglones)", db.now_iso()))
    if not adjudicaciones:
        logger.info("proceso %s sin dictamen/adjudicación todavía", detalle.get("numero"))
        conn.commit()
        return proceso_id

    conn.execute("DELETE FROM ofertas WHERE proceso_id=?", (proceso_id,))
    ganadoras, pendientes = ofertas_ganadoras_y_pendientes(adjudicaciones, ofertas)
    # (renglón, proveedor normalizado) -> causa, para marcar cada oferta admitida/desestimada
    # (jdemonasterio 2026-10-12: la más baja pudo estar desestimada con razón; mostrar por qué).
    causa_por_oferta: dict[tuple, str] = {}
    for d in desestimadas:
        causa_por_oferta.setdefault((d.get("renglon"), _norm_prov(d.get("proveedor"))), d.get("causa") or "")
    for i, o in enumerate(ofertas):
        key = hashlib.sha1(f"{o.get('renglon')}|{o.get('cuit')}|{o.get('proveedor')}|{o.get('precio_unitario')}"
                           .encode("utf-8")).hexdigest()[:16]
        causa = causa_por_oferta.get((o.get("renglon"), _norm_prov(o.get("proveedor"))))
        conn.execute(
            "INSERT OR IGNORE INTO ofertas (proceso_id, nro_renglon, proveedor, cuit, cantidad, precio_unitario, "
            "total, marca, ganadora, estado, causa_desestimacion, oferta_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (proceso_id, o.get("renglon"), o.get("proveedor"), _cuit_fmt(o.get("cuit")), o.get("cantidad"),
             o.get("precio_unitario"), o.get("total"), o.get("marca") or None,
             None if i in pendientes else int(i in ganadoras),
             "desestimada" if causa else "admitida", causa, key))
    # Los renglones se actualizan en el lugar (ids estables: comparaciones y revisiones siguen apuntando
    # bien); solo se borran los que el dictamen ya no trae.
    previos = {r["external_key"]: r for r in conn.execute(
        "SELECT id, external_key, descripcion FROM renglones WHERE proceso_id=?", (proceso_id,)).fetchall()}
    vistos: set[str] = set()
    total = 0.0
    for a in adjudicaciones:
        reng = renglones.get(a.get("renglon")) or {}
        marca = next((o.get("marca") for o in ofertas if o.get("renglon") == a.get("renglon")
                      and _norm_prov(o.get("proveedor")) == _norm_prov(a.get("proveedor")) and o.get("marca")), None)
        cuit = a.get("cuit") or next((o.get("cuit") for o in ofertas
                                      if _norm_prov(o.get("proveedor")) == _norm_prov(a.get("proveedor"))), None)
        desc = reng.get("descripcion") or f"renglón {a.get('renglon')}"
        # Marca EXIGIDA por el PLIEGO (jdemonasterio 2026-10-12): se busca en el texto del pliego
        # (reng, lo que escribió el organismo), nunca en `desc` si ese cayó al placeholder genérico
        # "renglón N" (ahí no hay nada que parsear). Distinta de `marca` (la OFRECIDA por el ganador,
        # ya extraída arriba de las ofertas/cuadro comparativo).
        marca_exigida, marca_equivalente_ok = parse_marca_exigida(reng.get("descripcion"))
        ext_key = f"comprar:{a.get('renglon')}:{_norm_prov(a.get('proveedor'))}"
        vistos.add(ext_key)
        extra = {}
        if ext_key in previos and previos[ext_key]["descripcion"] != desc:
            extra = {"producto_id": None, "match_metodo": None}   # cambió el texto: hay que volver a machear
        db.upsert(conn, "renglones", {
            **extra,
            "fuente_fragmento": json.dumps({"dictamen": a, "pliego": reng or None}, ensure_ascii=False),
            "proceso_id": proceso_id,
            "nro_renglon": a.get("renglon"),
            "descripcion": desc,
            "cantidad": a.get("cantidad") or reng.get("cantidad"),
            "unidad_compra": (reng.get("unidad") or "unidad").lower(),
            "precio_unitario": a.get("precio_unitario"),
            "monto_total": a.get("total"),
            "proveedor": a.get("proveedor"),
            "proveedor_cuit": _cuit_fmt(cuit),
            "marca": marca,
            "marca_exigida": marca_exigida,
            "marca_equivalente_ok": marca_equivalente_ok,
            "extraccion_metodo": "comprar",
            "alertas": None,
            "external_key": ext_key,
        }, ("proceso_id", "external_key"))
        total += a.get("total") or 0
    for key, r in previos.items():
        if key not in vistos:
            conn.execute("DELETE FROM comparaciones WHERE renglon_id=?", (r["id"],))
            conn.execute("DELETE FROM renglones WHERE id=?", (r["id"],))
    conn.execute("UPDATE procesos SET monto_total=? WHERE id=?", (total or None, proceso_id))
    conn.commit()
    return proceso_id


def ofertas_ganadoras(adjudicaciones: list[dict], ofertas: list[dict]) -> set[int]:
    """Índices de las ofertas que el dictamen recomienda.

    Un proveedor puede presentar varias alternativas en el mismo renglón: gana la que tiene
    el precio del dictamen (±0,5 %), no todas las del proveedor (juez externo 2026-10-11). Si
    ninguna coincide en precio, se marcan todas las del proveedor en ese renglón. Si DOS o más
    alternativas coinciden en precio, se elige UNA (jdemonasterio 2026-10-12, ver
    `ofertas_ganadoras_y_pendientes`).
    """
    out, _ = ofertas_ganadoras_y_pendientes(adjudicaciones, ofertas)
    return out


def ofertas_ganadoras_y_pendientes(adjudicaciones: list[dict], ofertas: list[dict]) -> tuple[set[int], set[int]]:
    """`pendientes` queda siempre vacío (se conserva en la firma por compatibilidad: algún llamador
    todavía puede desarmar la tupla). Decisión de jdemonasterio 2026-10-12, que revierte la cautela
    original del LLM prime: "si hay empate, elegís una; si está adjudicado, está adjudicado" — nunca
    se deja la ganadora en NULL/pendiente por un empate de precio entre alternativas del MISMO
    proveedor. Se elige la de menor índice (la primera alternativa del cuadro comparativo de
    COMPR.AR para ese proveedor y renglón): es arbitrario pero determinístico, y el renglón ya tiene
    su `fuente_fragmento` con el dictamen completo para que un humano vea las alternativas."""
    ganadoras: set[int] = set()
    pendientes: set[int] = set()
    for a in adjudicaciones:
        prov, pu = _norm_prov(a.get("proveedor")), a.get("precio_unitario")
        del_prov = [i for i, o in enumerate(ofertas)
                    if o.get("renglon") == a.get("renglon") and _norm_prov(o.get("proveedor")) == prov]
        mismas = [i for i in del_prov if pu and ofertas[i].get("precio_unitario")
                  and abs(ofertas[i]["precio_unitario"] - pu) <= 0.005 * pu]
        if len(mismas) > 1:
            ganadoras.add(min(mismas))
        else:
            ganadoras.update(mismas or del_prov)
    return ganadoras, pendientes


def store_actos(conn: sqlite3.Connection, proceso_id: int, actos: list[dict]) -> int:
    """Guarda los actos administrativos del proceso (autorización de pliego/llamado, adjudicación...).

    `numero` es el que una persona reconoce (numero_especial, ej. "RESOL-2026-346-MS"); si no
    vino, se usa el numero_gde. Sin eso no hay fila (no hay con qué identificar el acto). `url`
    ya trae el fragmento a la sección (ver ComprarClient.detalle_proceso / ACTOS_ANCHOR_ID): no
    hay descarga directa del PDF sin sesión (gotcha documentado en sources/comprar.py).
    """
    ensure_schema(conn)
    conn.execute("DELETE FROM comprar_actos WHERE proceso_id=?", (proceso_id,))
    n = 0
    for a in actos:
        numero = (a.get("numero_especial") or a.get("numero_gde") or "").strip()
        if not numero:
            continue
        descripcion = a.get("documento") or ""
        if a.get("numero_gde") and a.get("numero_gde") != numero:
            descripcion += f" (expediente GDE {a['numero_gde']})"
        conn.execute(
            "INSERT OR IGNORE INTO comprar_actos (proceso_id, tipo, numero, fecha, descripcion, url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (proceso_id, a.get("documento") or "acto", numero, _iso(a.get("fecha")), descripcion, a.get("url")))
        n += 1
    return n


def store_ordenes(conn: sqlite3.Connection, proceso_id: int, ordenes: list[dict], adjudicaciones: list[dict],
                  ofertas: list[dict]) -> list[dict]:
    """Guarda las órdenes de compra y calcula alertas de coherencia con el dictamen.

    - oc_sin_recomendacion: el proveedor tiene orden de compra pero el dictamen no lo
      recomendó para ningún renglón (la contratación final no es la evaluada).
    - oc_supera_dictamen: el monto de las OC "Original" de un proveedor supera en más de
      5 % lo que el dictamen le asignó.
    - prorrogas: suma de prórrogas / ampliaciones por proveedor (un contrato que se
      duplica sin nueva licitación).
    """
    ensure_schema(conn)
    conn.execute("DELETE FROM alertas_proceso WHERE proceso_id=? AND tipo='dictamen_incompleto'", (proceso_id,))
    if not ordenes:
        return []
    conn.execute("DELETE FROM ordenes_compra WHERE proceso_id=?", (proceso_id,))
    conn.execute("DELETE FROM alertas_proceso WHERE proceso_id=? AND tipo <> 'dictamen_incompleto'", (proceso_id,))
    for o in ordenes:
        conn.execute(
            "INSERT OR REPLACE INTO ordenes_compra (proceso_id, numero_oc, proveedor, cuit, fecha, monto, moneda, tipo, "
            "estado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (proceso_id, o.get("numero_oc"), o.get("proveedor"), _cuit_fmt(o.get("cuit")), o.get("fecha"),
             o.get("monto"), o.get("moneda"), o.get("tipo"), o.get("estado")))
    # Montos del dictamen por proveedor (por CUIT si se puede cruzar con las ofertas, si no por nombre).
    cuit_por_nombre = {_norm_prov(o.get("proveedor")): re.sub(r"\D", "", o.get("cuit") or "") for o in ofertas}
    dictamen: dict[str, float] = {}
    for a in adjudicaciones:
        key = re.sub(r"\D", "", a.get("cuit") or "") or cuit_por_nombre.get(_norm_prov(a.get("proveedor"))) \
            or _norm_prov(a.get("proveedor"))
        dictamen[key] = dictamen.get(key, 0.0) + (a.get("total") or 0.0)
    alertas = []
    por_prov: dict[str, dict] = {}
    for o in ordenes:
        key = re.sub(r"\D", "", o.get("cuit") or "") or _norm_prov(o.get("proveedor"))
        item = por_prov.setdefault(key, {"proveedor": o.get("proveedor"), "cuit": _cuit_fmt(o.get("cuit")),
                                         "original": 0.0, "extra": 0.0, "ocs": []})
        tipo = (o.get("tipo") or "").lower()
        if "original" in tipo or not tipo:
            item["original"] += o.get("monto") or 0.0
        else:
            item["extra"] += o.get("monto") or 0.0
        item["ocs"].append(o.get("numero_oc"))
    for key, item in por_prov.items():
        rec = dictamen.get(key)
        if adjudicaciones and not rec:
            alertas.append({"tipo": "oc_sin_recomendacion", "monto": item["original"] + item["extra"],
                            "detalle": f"OC {', '.join(item['ocs'])} sin renglones recomendados en el dictamen",
                            "proveedor": item["proveedor"], "cuit": item["cuit"]})
        elif rec and item["original"] > rec * 1.05:
            alertas.append({"tipo": "oc_supera_dictamen", "monto": item["original"] - rec,
                            "detalle": f"OC original ${item['original']:,.0f} vs dictamen ${rec:,.0f}",
                            "proveedor": item["proveedor"], "cuit": item["cuit"]})
        if item["extra"] > 0:
            alertas.append({"tipo": "prorroga_ampliacion", "monto": item["extra"],
                            "detalle": f"Prórrogas/ampliaciones por ${item['extra']:,.0f} (original ${item['original']:,.0f})",
                            "proveedor": item["proveedor"], "cuit": item["cuit"]})
    for a in alertas:
        conn.execute("INSERT OR IGNORE INTO alertas_proceso (proceso_id, tipo, detalle, monto, proveedor, cuit, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (proceso_id, a["tipo"], a["detalle"], a["monto"], a["proveedor"],
                                                     a["cuit"], db.now_iso()))
    return alertas


def enriquecer_bo(conn: sqlite3.Connection, client=None, limit: int = 50, progress=None) -> str:
    """Completa con COMPR.AR los procesos del BO que citan un número de proceso y no describen renglones."""
    from ..sources import comprar
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT p.id, p.numero FROM procesos p WHERE p.fuente='bo_primera' AND p.numero IS NOT NULL "
        "AND EXISTS (SELECT 1 FROM renglones r WHERE r.proceso_id=p.id AND (r.descripcion LIKE 'renglón % (sin descripción%' "
        "OR r.precio_unitario IS NULL)) LIMIT ?", (limit,)).fetchall()
    ok = fail = 0
    for i, r in enumerate(rows, 1):
        numero = r["numero"].strip().upper()
        if not _COMPRAR_NUM_RE.match(numero):
            continue
        if progress:
            progress(f"COMPR.AR {numero} ({i}/{len(rows)})")
        try:
            detalle = comprar.detalle_proceso(numero, client=client)
            store_detalle(conn, detalle, proceso_id=r["id"])
            conn.execute("UPDATE renglones SET match_metodo=NULL WHERE proceso_id=?", (r["id"],))
            ok += 1
        except Exception as exc:   # un proceso que falla no frena al resto
            logger.warning("no se pudo enriquecer %s: %s", numero, exc)
            fail += 1
    conn.commit()
    return f"COMPR.AR: {ok} procesos del BO completados, {fail} con error, {len(rows) - ok - fail} sin número válido"


def brecha_ofertas(conn: sqlite3.Connection, proceso_id: int, nro_renglon: int | None,
                   precio_ganador: float | None, max_ratio: float | None = None) -> dict:
    """Ganadora vs oferta más baja PLAUSIBLE del mismo renglón (mismo proceso).

    Una oferta a menos de 1/max_ratio del precio ganador casi siempre está cotizada en
    otra unidad (unidad vs caja) o es parcial: no cuenta como "más baja" y se informa
    aparte (mismo guarda que plausibilidad_max_ratio en la comparación con el mercado).
    """
    from .. import config
    ensure_schema(conn)
    if max_ratio is None:
        max_ratio = float((config.settings().get("comparacion") or {}).get("plausibilidad_max_ratio", 4.0))
    rows = conn.execute(
        "SELECT proveedor, precio_unitario, marca, estado, causa_desestimacion FROM ofertas "
        "WHERE proceso_id=? AND nro_renglon IS ? AND precio_unitario > 0 ORDER BY precio_unitario",
        (proceso_id, nro_renglon)).fetchall()
    out = {"n_ofertas": len(rows), "oferta_min": None, "oferta_min_proveedor": None, "brecha_oferta_min_pct": None,
           "ofertas_implausibles": 0, "oferta_min_desestimada": False, "oferta_min_causa": None}
    if not rows or not precio_ganador:
        return out
    plausibles = [r for r in rows if r["precio_unitario"] >= precio_ganador / max_ratio]
    out["ofertas_implausibles"] = len(rows) - len(plausibles)
    if not plausibles:
        return out
    low = plausibles[0]
    out.update(oferta_min=low["precio_unitario"], oferta_min_proveedor=low["proveedor"],
               brecha_oferta_min_pct=round((precio_ganador / low["precio_unitario"] - 1) * 100, 2),
               # La más baja pudo estar desestimada con razón (pedido de jdemonasterio
               # 2026-10-12): si lo estuvo, se informa la causa para que el webui diga
               # "la más baja fue desestimada por: <causa>" en vez de insinuar sobreprecio.
               oferta_min_desestimada=bool(low["estado"] == "desestimada"),
               oferta_min_causa=low["causa_desestimacion"])
    return out


# Estado documental de la fuente del precio pagado (MEJORAS #29, jdemonasterio 2026-10-12): nunca
# decimos "pagó" (no tenemos pagos, solo recomendaciones/actos/contratos).
ESTADOS_DOCUMENTALES = ("contratado", "adjudicado", "dictamen", "desconocido")
ESTADO_DOCUMENTAL_TXT = {"dictamen": "recomendado (dictamen de COMPR.AR, no decidido todavía)",
                         "adjudicado": "adjudicado (acto/resolución)", "contratado": "contratado (hay orden de compra)",
                         "desconocido": "estado documental desconocido"}


def actualizar_estado_documental(conn: sqlite3.Connection, proceso_ids: list[int] | None = None) -> None:
    """Deriva `renglones.estado_documental` y el agregado `procesos.estado_documental` a partir de
    datos que YA tenemos (sin bajar nada nuevo):

      - **contratado**: existe una orden de compra (`ordenes_compra`) para ese proveedor en ese
        proceso — el contrato está en marcha, más allá de lo recomendado/adjudicado.
      - **dictamen**: el renglón vino de COMPR.AR (`extraccion_metodo='comprar'`) — es una
        RECOMENDACIÓN del dictamen, todavía no una decisión de la autoridad.
      - **adjudicado**: el renglón vino de una resolución del Boletín Oficial que adjudica
        (`procesos.fuente='bo_primera'`, extraído por regex o LLM, no del dataset legacy) — eso SÍ
        es un acto administrativo ya decidido.
      - **desconocido**: ninguno de los anteriores (ej. dataset legacy 2021 sin metadata de origen).

    Se llama al final de `pipeline.compare.run()` (se recalcula cada vez que se recalculan
    sobreprecios, sin un comando aparte). Nunca escribe "pagado": no hay fuente de pagos reales.
    """
    ensure_schema(conn)
    row_where, proc_where, params = "1=1", "1=1", []
    if proceso_ids:
        placeholders = ",".join("?" for _ in proceso_ids)
        row_where, proc_where = f"proceso_id IN ({placeholders})", f"id IN ({placeholders})"
        params = list(proceso_ids)
    conn.execute(
        "UPDATE renglones SET estado_documental = ("
        "  SELECT CASE "
        "    WHEN EXISTS (SELECT 1 FROM ordenes_compra oc WHERE oc.proceso_id = renglones.proceso_id "
        "                 AND ((oc.cuit IS NOT NULL AND oc.cuit = renglones.proveedor_cuit) "
        "                      OR (oc.proveedor IS NOT NULL AND oc.proveedor = renglones.proveedor))) "
        "      THEN 'contratado' "
        "    WHEN renglones.extraccion_metodo = 'comprar' THEN 'dictamen' "
        "    WHEN p.fuente = 'bo_primera' AND renglones.extraccion_metodo IS NOT NULL "
        "         AND renglones.extraccion_metodo <> 'legacy_quijotes' "
        "         AND renglones.extraccion_metodo NOT LIKE 'sin_extraccion%' THEN 'adjudicado' "
        "    ELSE 'desconocido' END "
        "  FROM procesos p WHERE p.id = renglones.proceso_id"
        f") WHERE {row_where}", params)
    conn.execute(
        "UPDATE procesos SET estado_documental = ("
        "  SELECT CASE "
        "    WHEN SUM(CASE WHEN r.estado_documental='contratado' THEN 1 ELSE 0 END) > 0 THEN 'contratado' "
        "    WHEN SUM(CASE WHEN r.estado_documental='adjudicado' THEN 1 ELSE 0 END) > 0 THEN 'adjudicado' "
        "    WHEN SUM(CASE WHEN r.estado_documental='dictamen' THEN 1 ELSE 0 END) > 0 THEN 'dictamen' "
        "    ELSE 'desconocido' END "
        "  FROM renglones r WHERE r.proceso_id = procesos.id"
        f") WHERE {proc_where}", params)
    conn.commit()


def descubrir(conn: sqlite3.Connection, desde: dt.date | None = None, hasta: dt.date | None = None,
              rubros: list[str] | None = None, max_paginas: int = 2, client=None, progress=None) -> str:
    """Busca en COMPR.AR procesos ADJUDICADOS de rubros de góndola y guarda los nuevos.

    Es el canal principal para 2024-2026 (el BO ya no describe lo que se compra).
    Rubros por defecto: alimentos y limpieza. Va despacio: ~5 s por proceso.
    """
    from ..sources import comprar
    ensure_schema(conn)
    rubros = rubros or [comprar.RUBRO_ALIMENTOS, comprar.RUBRO_LIMPIEZA]
    client = client or comprar.ComprarClient()
    conocidos = {r[0] for r in conn.execute("SELECT numero FROM procesos WHERE numero IS NOT NULL")}
    encontrados, nuevos, errores = 0, 0, 0
    for rubro in rubros:
        for estado in (comprar.ESTADO_ADJUDICADO, comprar.ESTADO_ADJUDICADO_CON_OC):
            try:
                filas = client.buscar_procesos(rubro=rubro, estado=estado, desde=desde, hasta=hasta,
                                               max_paginas=max_paginas, progress=progress)
            except Exception as exc:
                logger.warning("búsqueda COMPR.AR rubro %s estado %s falló: %s", rubro, estado, exc)
                errores += 1
                continue
            for fila in filas:
                numero = (fila.get("numero") or "").strip()
                encontrados += 1
                if not numero or numero in conocidos:
                    continue
                if progress:
                    progress(f"COMPR.AR: bajando {numero}")
                try:
                    store_detalle(conn, client.detalle_proceso(numero))
                    conocidos.add(numero)
                    nuevos += 1
                except Exception as exc:
                    logger.warning("detalle %s falló: %s", numero, exc)
                    errores += 1
    return f"COMPR.AR: {encontrados} procesos listados, {nuevos} nuevos guardados, {errores} errores"
