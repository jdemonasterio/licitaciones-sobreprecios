"""Módulo de revisión humana (MEJORAS #34): cola de casos grises/rojos/equivalencias/juez,
correcciones manuales por campo y el proxy de documentos para mirarlos sin salir del webui.

Por qué así:
  - El pipeline (`pipeline/compare.py`) nunca "sabe" de esto a propósito: lee las correcciones
    vigentes ANTES de calcular (`aplicar_correcciones`), pero `compare()` (el núcleo puro) sigue
    recibiendo valores ya resueltos. Así una corrección se audita igual que cualquier otro dato
    de entrada, nunca como una excepción oculta en medio de la lógica de semáforo.
  - Una corrección nueva no pisa la anterior: se agrega una fila a `correcciones_renglon` y la más
    reciente por (renglon_id, campo) es la vigente (ver `correcciones_vigentes`). "Deshacer" borra
    esa fila puntual (vuelve a mandar el dato automático), nunca un UPDATE in place: así queda el
    historial de quién tocó qué y cuándo.
  - La cola (`cola()`) sincroniza `revisiones_caso` con lo que el comparador y el juez externo
    encuentran HOY, pero nunca pisa una fila ya creada: un caso que un humano ya marcó "descartado"
    no reaparece como "pendiente" aunque siga siendo gris. Es la misma precaución que
    `sobreprecios/revision.py` aplica a "publicable" (el gate se revoca solo, pero jamás se
    reafirma solo).
  - `auditoria/juez.py` sigue siendo de solo lectura sobre la base (regla del repo): este módulo
    es el que ESCRIBE los veredictos del juez, a pedido explícito del humano
    (`importar_corrida_juez`, CLI `importar-juez` o el botón del webui), nunca automático.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import time
import urllib.parse
from pathlib import Path

from . import config, db

# ── Correcciones manuales por renglón ───────────────────────────────────────

CAMPOS_CORRECCION = ("producto_id", "variante", "contenido_base", "unidad_base", "precio_unitario",
                     "cantidad", "fecha_comparacion", "marca")
_ETIQUETA_CAMPO = {"producto_id": "producto", "variante": "variedad", "contenido_base": "envase",
                   "unidad_base": "envase", "precio_unitario": "precio unitario", "cantidad": "cantidad",
                   "fecha_comparacion": "fecha de comparación", "marca": "marca"}
# Sentinela de la variante (P17/P18, pedido de jdemonasterio): el humano miró y no pudo decidir cuál
# es. Se guarda la corrección igual (queda constancia de que alguien la revisó, con su nota) pero
# NO fuerza ningún valor al comparador: el caso sigue resolviéndose (y quedando gris) solo.
VARIANTE_NO_SE = "__no_se__"


def correcciones_vigentes(conn: sqlite3.Connection, renglon_id: int) -> dict[str, dict]:
    """Última corrección por campo para un renglón (ganadora = la de `created_at` más reciente).

    Una fila borrada (deshecha) simplemente deja de aparecer acá: no hay "tombstone", el campo
    vuelve a resolverse automático.
    """
    rows = conn.execute(
        "SELECT id, campo, valor, nota, autor, created_at FROM correcciones_renglon "
        "WHERE renglon_id = ? ORDER BY created_at", (renglon_id,)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        out[r["campo"]] = dict(r)   # ASC: la última iteración de cada campo es la más reciente
    return out


def guardar_correccion(conn: sqlite3.Connection, renglon_id: int, campo: str, valor: str | None,
                       nota: str, autor: str) -> int:
    if campo not in CAMPOS_CORRECCION:
        raise ValueError(f"campo de corrección desconocido: {campo!r}")
    cur = conn.execute(
        "INSERT INTO correcciones_renglon (renglon_id, campo, valor, nota, autor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", (renglon_id, campo, valor, nota or None, autor or None, db.now_iso()))
    conn.commit()
    return cur.lastrowid


def deshacer_correccion(conn: sqlite3.Connection, correccion_id: int) -> bool:
    """Borra UNA corrección puntual (el campo vuelve a resolverse automático). True si existía."""
    cur = conn.execute("DELETE FROM correcciones_renglon WHERE id=?", (correccion_id,))
    conn.commit()
    return cur.rowcount > 0


def _a_float(valor) -> float | None:
    """float() tolerante a coma decimal ("1,5"): quien guardó la corrección puede no haber
    pasado por el form del webui (CLI, un script), así que esto no asume dato ya limpio."""
    if valor in (None, ""):
        return None
    s = str(valor).strip()
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def aplicar_correcciones(conn: sqlite3.Connection, row) -> tuple[dict, list[str]]:
    """Copia `row` (una fila de `renglones` + su `fecha` de comparación) con las correcciones
    manuales vigentes aplicadas, y la lista de notas humanas (para sumar a `motivo`).

    No toca la base ni el `row` original: `pipeline/compare.run()` llama esto ANTES de armar las
    observaciones de mercado, así el núcleo puro (`compare()`) sigue recibiendo valores ya resueltos
    sin enterarse de que hubo una corrección manual. El dict devuelto además lleva dos claves
    internas (no persistidas, no son columnas reales): `_variante_manual` (la variedad que el humano
    confirmó) y `_campos_corregidos` (qué campos se tocaron, para que `run()` no pise un envase
    corregido a mano con el que calcularía de la descripción).
    """
    out = dict(row)
    renglon_id = out["id"]
    correcciones = correcciones_vigentes(conn, renglon_id)
    notas: list[str] = []
    tocados: set[str] = set()

    if "producto_id" in correcciones:
        out["producto_id"] = correcciones["producto_id"]["valor"]
        tocados.add("producto_id")
        notas.append(_ETIQUETA_CAMPO["producto_id"])

    envase = False
    if "contenido_base" in correcciones:
        out["contenido_base"] = _a_float(correcciones["contenido_base"]["valor"])
        tocados.add("contenido_base")
        envase = True
    if "unidad_base" in correcciones:
        out["unidad_base"] = correcciones["unidad_base"]["valor"] or None
        tocados.add("unidad_base")
        envase = True
    if envase:
        notas.append(_ETIQUETA_CAMPO["contenido_base"])

    if "precio_unitario" in correcciones:
        out["precio_unitario"] = _a_float(correcciones["precio_unitario"]["valor"])
        tocados.add("precio_unitario")
        notas.append(_ETIQUETA_CAMPO["precio_unitario"])

    if "precio_unitario" in tocados or envase:
        if out.get("precio_unitario") and out.get("contenido_base"):
            out["precio_unidad_base"] = round(out["precio_unitario"] / out["contenido_base"], 4)
        elif not out.get("contenido_base"):
            out["precio_unidad_base"] = None

    if "cantidad" in correcciones:
        out["cantidad"] = _a_float(correcciones["cantidad"]["valor"])
        tocados.add("cantidad")
        notas.append(_ETIQUETA_CAMPO["cantidad"])

    if "fecha_comparacion" in correcciones:
        out["fecha"] = correcciones["fecha_comparacion"]["valor"] or out.get("fecha")
        tocados.add("fecha_comparacion")
        notas.append(_ETIQUETA_CAMPO["fecha_comparacion"])

    if "marca" in correcciones:
        out["marca"] = correcciones["marca"]["valor"] or None
        tocados.add("marca")
        notas.append(_ETIQUETA_CAMPO["marca"])

    variante_manual = None
    if "variante" in correcciones:
        valor_variante = correcciones["variante"]["valor"] or None
        tocados.add("variante")
        notas.append(_ETIQUETA_CAMPO["variante"])
        if valor_variante and valor_variante != VARIANTE_NO_SE:
            variante_manual = valor_variante
        # VARIANTE_NO_SE (o vacío): no se fuerza nada, el comparador sigue decidiendo solo (sigue
        # pudiendo quedar gris) — la fila de corrección solo deja constancia de que un humano miró.

    out["_variante_manual"] = variante_manual
    out["_campos_corregidos"] = tocados
    # dedup conservando orden (precio y envase pueden repetir "envase")
    notas_unicas = list(dict.fromkeys(notas))
    return out, notas_unicas


# ── Validación humana de referencias de mercado (P16) ──────────────────────
# Una observación de precios_mercado no tiene un id estable visible en evidencia_json (es un
# snapshot JSON armado por pipeline/compare.py, no una FK): la clave natural para juzgar UNA
# observación en el contexto de UN renglón es (fuente, fecha, descripción original).

JUICIOS_MERCADO = ("equivalente", "no_es_mismo_producto", "precio_o_tamano_mal_leido", "no_se")
_JUICIOS_EXCLUYENTES = {"no_es_mismo_producto", "precio_o_tamano_mal_leido"}
_SUFIJO_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


def clave_observacion(fuente: str, fecha: str, descripcion: str | None) -> tuple[str, str, str]:
    """Clave natural de una observación, sacando el sufijo que compare.py le agrega al mostrarla
    ("... [llevado al mes de la compra por IPC x1.234]"): sin esto, una observación ajustada por
    IPC nunca matchearía la fila que guarda el juicio humano sobre la descripción ORIGINAL."""
    return (fuente, fecha, _SUFIJO_RE.sub("", descripcion or "").strip())


def guardar_validacion_mercado(conn: sqlite3.Connection, renglon_id: int, obs_fuente: str, obs_fecha: str,
                               obs_descripcion: str, juicio: str, nota: str, autor: str) -> None:
    if juicio not in JUICIOS_MERCADO:
        raise ValueError(f"juicio de mercado desconocido: {juicio!r}")
    _, _, desc = clave_observacion(obs_fuente, obs_fecha, obs_descripcion)
    conn.execute(
        "INSERT INTO validaciones_mercado (renglon_id, obs_fuente, obs_fecha, obs_descripcion, juicio, nota, "
        "autor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(renglon_id, obs_fuente, obs_fecha, obs_descripcion) DO UPDATE SET "
        "juicio=excluded.juicio, nota=excluded.nota, autor=excluded.autor, created_at=excluded.created_at",
        (renglon_id, obs_fuente, obs_fecha, desc, juicio, nota or None, autor or None, db.now_iso()))
    conn.commit()


def quitar_validacion_mercado(conn: sqlite3.Connection, renglon_id: int, obs_fuente: str, obs_fecha: str,
                              obs_descripcion: str) -> None:
    _, _, desc = clave_observacion(obs_fuente, obs_fecha, obs_descripcion)
    conn.execute("DELETE FROM validaciones_mercado WHERE renglon_id=? AND obs_fuente=? AND obs_fecha=? "
                "AND obs_descripcion=?", (renglon_id, obs_fuente, obs_fecha, desc))
    conn.commit()


def validaciones_mercado_vigentes(conn: sqlite3.Connection, renglon_id: int) -> dict[tuple[str, str, str], dict]:
    rows = conn.execute(
        "SELECT * FROM validaciones_mercado WHERE renglon_id = ?", (renglon_id,)).fetchall()
    return {(r["obs_fuente"], r["obs_fecha"], r["obs_descripcion"]): dict(r) for r in rows}


def exclusiones_mercado(conn: sqlite3.Connection, renglon_id: int) -> set[tuple[str, str, str]]:
    """Claves de observaciones que un humano marcó "no es el mismo producto" o "precio/tamaño mal
    leído" PARA ESTE RENGLÓN: pipeline/compare.run() las saca del pool antes de llamar a compare()."""
    return {k for k, v in validaciones_mercado_vigentes(conn, renglon_id).items()
            if v["juicio"] in _JUICIOS_EXCLUYENTES}


def aplicar_exclusiones_mercado(conn: sqlite3.Connection, renglon_id: int, obs: list) -> list:
    """Copia de `obs` (objetos `pipeline.compare.Obs`) sin las observaciones que un humano excluyó
    para este renglón. `compare()` sigue sin saber que hubo una exclusión: solo recibe un pool más
    chico, igual que con cualquier otro filtro (outliers, envase, fecha)."""
    excl = exclusiones_mercado(conn, renglon_id)
    if not excl:
        return obs
    return [o for o in obs if clave_observacion(o.fuente, o.fecha, o.descripcion) not in excl]


# ── Validación rápida in-line en /licitacion/<id> (pedido de jdemonasterio) ─────────────────
# Un vistazo por renglón sin abrir /revision/<renglon_id>: 3 partes que pueden estar mal por
# separado (la extracción leyó mal el renglón, el macheo asignó mal el producto/variedad, o la
# comparación eligió mal la referencia). Para correcciones de DATOS se abre la revisión completa.

PARTES_RENGLON = ("extraccion", "producto", "comparacion")
JUICIOS_RENGLON = ("ok", "no", "no_se")
PARTE_TXT = {"extraccion": "Extracción (cantidad / precio / proveedor)",
            "producto": "Producto y variedad asignados", "comparacion": "Referencia de mercado"}


def guardar_validacion_renglon(conn: sqlite3.Connection, renglon_id: int, parte: str, juicio: str,
                               nota: str, autor: str) -> None:
    if parte not in PARTES_RENGLON:
        raise ValueError(f"parte desconocida: {parte!r}")
    if juicio not in JUICIOS_RENGLON:
        raise ValueError(f"juicio desconocido: {juicio!r}")
    conn.execute(
        "INSERT INTO validaciones_renglon (renglon_id, parte, juicio, nota, autor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(renglon_id, parte) DO UPDATE SET "
        "juicio=excluded.juicio, nota=excluded.nota, autor=excluded.autor, created_at=excluded.created_at",
        (renglon_id, parte, juicio, nota or None, autor or None, db.now_iso()))
    conn.commit()


def validaciones_renglones(conn: sqlite3.Connection, renglon_ids: list[int]) -> dict[int, dict]:
    """Por renglon_id: `{"partes": {parte: fila}, "resumen": "texto corto"}`, para pintar en la
    lista de renglones de /licitacion/<id> sin abrir cada caso. "con objeciones" si alguna parte es
    "no"; "validado por <autor>" si las 3 partes están en "ok"; "revisión parcial" en el resto."""
    if not renglon_ids:
        return {}
    placeholders = ",".join("?" for _ in renglon_ids)
    rows = conn.execute(
        f"SELECT * FROM validaciones_renglon WHERE renglon_id IN ({placeholders})", renglon_ids).fetchall()
    por_renglon: dict[int, dict] = {}
    for r in rows:
        por_renglon.setdefault(r["renglon_id"], {})[r["parte"]] = dict(r)
    out: dict[int, dict] = {}
    for rid, partes in por_renglon.items():
        juicios = {p["juicio"] for p in partes.values()}
        autores = [p["autor"] for p in partes.values() if p.get("autor")]
        if "no" in juicios:
            resumen = "con objeciones"
        elif len(partes) >= len(PARTES_RENGLON) and juicios <= {"ok"}:
            resumen = f"validado por {autores[0]}" if autores else "validado"
        else:
            resumen = "revisión parcial"
        out[rid] = {"partes": partes, "resumen": resumen}
    return out


# ── Checklist de explicaciones alternativas por caso (MEJORAS #49.6) ───────────
# Una brecha de precio no prueba una irregularidad: logística/lugar de entrega, impuestos,
# especificaciones técnicas, plazo de pago distinto del supuesto, o un error de la publicación
# original pueden explicarla. El humano marca cada una como pendiente/verificado/no aplica con
# una nota; se muestra en /revision/<renglon_id>, el comprobante de cálculo y el expediente para
# periodista. A diferencia de `correcciones_renglon`, NUNCA dispara un recálculo: es una
# anotación para quien lee, no un dato de entrada del comparador.

EXPLICACIONES_ITEMS = ("logistica", "impuestos", "especificaciones", "plazo_pago", "error_publicacion")
EXPLICACION_TXT = {
    "logistica": "Logística o lugar de entrega distinto",
    "impuestos": "Impuestos o alícuotas distintas",
    "especificaciones": "Especificaciones técnicas distintas",
    "plazo_pago": "Plazo de pago real distinto del supuesto",
    "error_publicacion": "Error en la publicación original (monto, cantidad, unidad)",
}
EXPLICACION_ESTADOS = ("pendiente", "verificado", "no_aplica")
EXPLICACION_ESTADO_TXT = {"pendiente": "Pendiente", "verificado": "Verificado: explica la brecha",
                          "no_aplica": "No aplica a este caso"}


def guardar_explicacion(conn: sqlite3.Connection, renglon_id: int, item: str, estado: str, nota: str,
                        autor: str) -> None:
    if item not in EXPLICACIONES_ITEMS:
        raise ValueError(f"explicación desconocida: {item!r}")
    if estado not in EXPLICACION_ESTADOS:
        raise ValueError(f"estado desconocido: {estado!r}")
    conn.execute(
        "INSERT INTO explicaciones_caso (renglon_id, item, estado, nota, autor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(renglon_id, item) DO UPDATE SET "
        "estado=excluded.estado, nota=excluded.nota, autor=excluded.autor, created_at=excluded.created_at",
        (renglon_id, item, estado, nota or None, autor or None, db.now_iso()))
    conn.commit()


def explicaciones_vigentes(conn: sqlite3.Connection, renglon_id: int) -> dict[str, dict]:
    """Las 5 explicaciones para `renglon_id`, con las que todavía no se tocaron en 'pendiente'
    (nunca None: así la plantilla siempre tiene las 5 filas del checklist)."""
    rows = conn.execute("SELECT * FROM explicaciones_caso WHERE renglon_id = ?", (renglon_id,)).fetchall()
    out = {r["item"]: dict(r) for r in rows}
    for item in EXPLICACIONES_ITEMS:
        out.setdefault(item, {"item": item, "estado": "pendiente", "nota": None, "autor": None})
    return out


# ── Cola de revisión ─────────────────────────────────────────────────────────

GRIS_MOTIVOS_REVISAR = ("atributo_desconocido", "sin_referencia_variante", "sin_referencia_presentacion",
                        "sin_datos", "revisar_unidad")
# "monto" (jdemonasterio 2026-10-12): el renglón tiene la alerta `monto_inconsistente` (cantidad x
# precio no cierra con el monto del aviso) — entra a la cola aunque el semáforo sea amarillo/verde,
# porque mientras no se complete el dato el rojo queda bloqueado (ver pipeline/compare.run()) y nadie
# se entera salvo que abra el caso. El color sigue mandando sobre "monto" (un rojo con esta alerta
# rarísimo, pero si pasara, sigue siendo "rojo" en la cola).
ORIGENES = ("gris", "equivalencia", "rojo", "juez", "manual", "monto")
# Etiqueta CORTA del motivo (distinta de format.MOTIVO_CODIGO_TXT, que es la explicación larga):
# para el resumen de una línea de la cola ("Aceite de girasol 1,5 L — revisar unidad", pedido de
# jdemonasterio tras la revisión de journeys). Vive acá (no en web/format.py) porque la arma
# `cola()`, que no es solo del webui (también la usa el CLI).
MOTIVO_CODIGO_CORTO = {
    "sin_precio": "sin precio", "sin_datos": "sin datos de mercado", "revisar_unidad": "revisar unidad",
    "fuera_alcance": "fuera de catálogo", "atributo_desconocido": "variedad desconocida",
    "sin_referencia_variante": "variedad sin referencia", "sin_referencia_presentacion": "envase sin referencia",
    "equivalencia_por_verificar": "equivalencia por verificar", "ok": "comparación ok",
}


def _detectar(conn: sqlite3.Connection) -> list[dict]:
    """Casos que el comparador o el juez externo dejaron para revisar HOY (ver docstring del módulo)."""
    placeholders = ",".join("?" for _ in GRIS_MOTIVOS_REVISAR)
    rows = conn.execute(
        "SELECT r.id renglon_id, r.alertas, k.semaforo, k.motivo_codigo, jv.veredicto juez_veredicto "
        "FROM renglones r LEFT JOIN comparaciones k ON k.renglon_id = r.id "
        "LEFT JOIN ("
        "  SELECT jv1.item_id, jv1.veredicto FROM juez_veredictos jv1 "
        "  JOIN (SELECT item_id, MAX(id) mx FROM juez_veredictos GROUP BY item_id) last "
        "    ON last.item_id = jv1.item_id AND last.mx = jv1.id"
        ") jv ON jv.item_id = 'renglon:' || r.id "
        f"WHERE (k.semaforo = 'gris' AND k.motivo_codigo IN ({placeholders})) "
        "   OR k.motivo_codigo = 'equivalencia_por_verificar' "
        "   OR k.semaforo = 'rojo' "
        "   OR jv.veredicto IN ('error', 'dudoso') "
        "   OR r.alertas LIKE '%monto_inconsistente%'",
        GRIS_MOTIVOS_REVISAR).fetchall()
    out = []
    for r in rows:
        # El color manda (un rojo con veredicto del juez o con monto_inconsistente sigue contando
        # como rojo; revisión del prime #48).
        if r["semaforo"] == "rojo":
            origen = "rojo"
        elif r["alertas"] and "monto_inconsistente" in r["alertas"]:
            origen = "monto"
        elif r["motivo_codigo"] == "equivalencia_por_verificar":
            origen = "equivalencia"
        elif r["juez_veredicto"] in ("error", "dudoso"):
            origen = "juez"
        else:
            origen = "gris"
        out.append({"renglon_id": r["renglon_id"], "origen": origen})
    return out


def cola(conn: sqlite3.Connection, filtros: dict | None = None) -> dict:
    """Cola de revisión humana: sincroniza `revisiones_caso` con lo detectado hoy (sin pisar
    decisiones humanas ya tomadas) y devuelve `{"items": [...], "counts": {...}}`.

    `filtros` admite `origen`, `estado`, `producto` (vacíos se ignoran, igual que `web/queries.py`).
    """
    filtros = {k: v for k, v in (filtros or {}).items() if v}
    ahora = db.now_iso()
    for caso in _detectar(conn):
        conn.execute(
            "INSERT INTO revisiones_caso (renglon_id, origen, estado, created_at, updated_at) "
            "VALUES (?, ?, 'pendiente', ?, ?) ON CONFLICT(renglon_id) DO UPDATE SET origen=excluded.origen, "
            "updated_at=excluded.updated_at WHERE revisiones_caso.estado = 'pendiente' AND revisiones_caso.origen <> excluded.origen",
            (caso["renglon_id"], caso["origen"], ahora, ahora))
    conn.commit()

    where, params = ["1=1"], []
    if filtros.get("origen"):
        where.append("rc.origen = ?")
        params.append(filtros["origen"])
    if filtros.get("estado"):
        where.append("rc.estado = ?")
        params.append(filtros["estado"])
    if filtros.get("producto"):
        where.append("r.producto_id = ?")
        params.append(filtros["producto"])
    rows = conn.execute(
        "SELECT rc.id revision_id, rc.renglon_id, rc.origen, rc.estado, rc.veredicto, rc.nota, rc.revisor, "
        "rc.updated_at, r.proceso_id, r.descripcion, r.proveedor, r.contenido_base, r.unidad_base, r.alertas, "
        "COALESCE(pr.nombre, r.producto_id) producto, "
        "COALESCE(p.organismo_canonico, p.organismo) organismo, "
        "COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha, "
        "k.semaforo, k.motivo, k.motivo_codigo, k.sobreprecio_ajustado_pct brecha, "
        "k.sobreprecio_ajustado_pct, k.sobreprecio_corroborado_pct, k.exceso_ars "
        "FROM revisiones_caso rc JOIN renglones r ON r.id = rc.renglon_id "
        "JOIN procesos p ON p.id = r.proceso_id LEFT JOIN comparaciones k ON k.renglon_id = r.id "
        "LEFT JOIN productos pr ON pr.id = r.producto_id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY (rc.estado = 'pendiente') DESC, (k.semaforo = 'rojo') DESC, rc.updated_at DESC",
        params).fetchall()
    counts = {o: 0 for o in ORIGENES}
    for r in conn.execute("SELECT origen, COUNT(*) n FROM revisiones_caso GROUP BY origen"):
        counts[r["origen"]] = r["n"]

    # Colores EN VIVO (MEJORAS #49.4, pedido de jdemonasterio: "tienen que coincidir con la home,
    # 9 rojos"): directo de `comparaciones`, igual que `web.queries.kpis()` sin filtros — nunca el
    # tally de `counts` (que es por ORIGEN de la cola, acumulado histórico, y puede quedar
    # desactualizado si un caso ya resuelto cambió de color después).
    total_renglones = conn.execute("SELECT COUNT(*) n FROM renglones").fetchone()["n"]
    colores = {"rojo": 0, "amarillo": 0, "verde": 0}
    for r in conn.execute(
            "SELECT semaforo, COUNT(*) n FROM comparaciones WHERE semaforo IN ('rojo','amarillo','verde') "
            "GROUP BY semaforo"):
        colores[r["semaforo"]] = r["n"]
    colores["gris"] = max(total_renglones - sum(colores.values()), 0)

    items = []
    for r in rows:
        d = dict(r)
        d["alertas_list"] = json.loads(d["alertas"]) if d.get("alertas") else []
        d["monto_inconsistente"] = "monto_inconsistente" in d["alertas_list"]
        partes = [(d.get("producto") or "(sin producto)").replace("_", " ")]
        if d.get("contenido_base") and d.get("unidad_base"):
            partes.append(f"{d['contenido_base']:g} {d['unidad_base']}")
        corto = " ".join(partes)
        motivo_corto = MOTIVO_CODIGO_CORTO.get(d.get("motivo_codigo"), "")
        d["resumen_corto"] = f"{corto} — {motivo_corto}" if motivo_corto else corto
        items.append(d)
    return {"items": items, "counts": counts, "colores": colores}


def guardar_revision(conn: sqlite3.Connection, renglon_id: int, veredicto: str | None, nota: str,
                     revisor: str, estado: str = "resuelto") -> None:
    ahora = db.now_iso()
    conn.execute(
        "INSERT INTO revisiones_caso (renglon_id, origen, estado, veredicto, nota, revisor, created_at, updated_at) "
        "VALUES (?, 'manual', ?, ?, ?, ?, ?, ?) ON CONFLICT(renglon_id) DO UPDATE SET "
        "estado=excluded.estado, veredicto=excluded.veredicto, nota=excluded.nota, revisor=excluded.revisor, "
        "updated_at=excluded.updated_at",
        (renglon_id, estado, veredicto, nota or None, revisor or None, ahora, ahora))
    conn.commit()


def caso(conn: sqlite3.Connection, renglon_id: int) -> dict | None:
    """Contexto completo de un caso para `/revision/<renglon_id>` (compra, comparación, veredictos
    del juez importados y las correcciones vigentes, para precargar el formulario)."""
    row = conn.execute(
        "SELECT r.*, k.*, pr.nombre producto_nombre, p.id proceso_id, p.numero, p.fuente, "
        "COALESCE(p.organismo_canonico, p.organismo) organismo, p.url proceso_url, "
        "COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha_proceso, d.url doc_url, "
        # Las 3 fechas por separado (MEJORAS #49.2, pedido de jdemonasterio): apertura/adjudicación/
        # publicación nunca deben quedar escondidas detrás de un solo COALESCE en el comprobante.
        "p.fecha_apertura fecha_apertura, p.fecha_adjudicacion fecha_adjudicacion, "
        "p.fecha_publicacion fecha_publicacion, "
        "r.id id "
        "FROM renglones r JOIN procesos p ON p.id = r.proceso_id LEFT JOIN comparaciones k ON k.renglon_id = r.id "
        "LEFT JOIN productos pr ON pr.id = r.producto_id LEFT JOIN documentos d ON d.id = p.documento_id "
        "WHERE r.id = ?", (renglon_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["fuente_fragmento"] = json.loads(data["fuente_fragmento"]) if data.get("fuente_fragmento") else None
    data["evidencia"] = json.loads(data["evidencia_json"]) if data.get("evidencia_json") else []
    data["alertas_list"] = json.loads(data["alertas"]) if data.get("alertas") else []
    # Brecha segun el plazo de pago supuesto (0/1/2/3 meses, prime P20): las dos escenarios
    # financieros del comprobante de calculo (1 mes = amarillo, 2 meses = rojo) salen de aca.
    data["sensibilidad"] = json.loads(data["sensibilidad_json"]) if data.get("sensibilidad_json") else {}
    data["explicaciones"] = explicaciones_vigentes(conn, renglon_id)
    validaciones = validaciones_mercado_vigentes(conn, renglon_id)
    for o in data["evidencia"]:
        clave = clave_observacion(o.get("fuente", ""), o.get("fecha", ""), o.get("descripcion"))
        o["clave"] = clave[2]
        o["validacion"] = validaciones.get(clave)
        # Precio del envase (sin convertir a unidad base) y contexto de góndola: evidencia_json
        # solo trae precio_ub ($/kg·l·u); precios_mercado tiene el dato crudo con el que el humano
        # puede juzgar si la observación es realmente comparable.
        pm = conn.execute(
            "SELECT precio, region, sucursal, canal FROM precios_mercado "
            "WHERE fuente=? AND fecha=? AND descripcion=? ORDER BY id DESC LIMIT 1",
            (o.get("fuente"), o.get("fecha"), clave[2])).fetchone()
        o["precio_envase"] = pm["precio"] if pm else None
        o["region"] = pm["region"] if pm else None
        o["sucursal"] = pm["sucursal"] if pm else None
    data["ofertas"] = [dict(o) for o in conn.execute(
        "SELECT * FROM ofertas WHERE proceso_id=? AND nro_renglon IS ? ORDER BY precio_unitario",
        (data["proceso_id"], data.get("nro_renglon")))]
    data["revision"] = conn.execute(
        "SELECT * FROM revisiones_caso WHERE renglon_id = ?", (renglon_id,)).fetchone()
    data["revision"] = dict(data["revision"]) if data["revision"] else None
    data["correcciones"] = correcciones_vigentes(conn, renglon_id)
    data["juez_veredictos"] = [dict(v) for v in conn.execute(
        "SELECT jv.*, jc.carpeta, jc.modelo FROM juez_veredictos jv JOIN juez_corridas jc ON jc.id = jv.corrida_id "
        "WHERE jv.item_id = ? ORDER BY jv.id DESC", (f"renglon:{renglon_id}",))]
    from .catalog import load_catalog, variante_estado as _variante_estado
    catalogo = load_catalog()
    prod = catalogo.get(data.get("producto_id"))
    auto_var, auto_estado = _variante_estado(prod, data.get("descripcion"))
    data["variante_auto"] = auto_var
    data["variante_estado_auto"] = auto_estado
    data["variantes_producto"] = sorted(prod.variantes.keys()) if prod and prod.variantes else []
    data["productos_catalogo"] = sorted(((p.id, p.nombre) for p in catalogo.values()), key=lambda t: t[1])
    data["variantes_por_producto"] = {p.id: sorted(p.variantes.keys()) for p in catalogo.values() if p.variantes}
    # P17/P18: una línea por variante con las frases que la identifican, para que el humano entienda
    # qué significa cada opción del select sin tener que ir a mirar config/productos.yml.
    data["variantes_detalle"] = ([
        {"nombre": nombre, "frases": ", ".join(" ".join(p) for p in frases[:6])}
        for nombre, frases in sorted((prod.variantes or {}).items())
    ] if prod and prod.variantes else [])
    return data


def comprobante(conn: sqlite3.Connection, renglon_id: int) -> dict | None:
    """Datos para el "comprobante de cálculo" de un renglón (MEJORAS #49.2): todo lo que
    `caso()` ya junta (dato original, normalización, evidencia, correcciones, explicaciones
    alternativas) más el par de observaciones que forma el mínimo CORROBORADO (con su grupo
    comercial, `familia_fuente`) y el estado de publicación del proceso al que pertenece — para
    que el documento se pueda imprimir o mandar solo, sin tener que volver a abrir la app.
    """
    data = caso(conn, renglon_id)
    if not data:
        return None
    from .pipeline.compare import familia_fuente
    pool_no_oficial = [o for o in data["evidencia"] if o.get("canal") != "oficial"]
    par = pool_no_oficial[:2] if data.get("ref_corroborada") else []
    for o in par:
        o["familia"] = familia_fuente(o.get("fuente", ""))
    data["par_corroborado"] = par
    proc = conn.execute(
        "SELECT id, numero, organismo, estado_revision, revision_huella, nota_revision "
        "FROM procesos WHERE id = ?", (data["proceso_id"],)).fetchone()
    data["proceso_estado"] = dict(proc) if proc else {}
    return data


def formulario(data: dict) -> dict:
    """Valores EFECTIVOS (corrección vigente, o el dato automático si no hay) para precargar el
    formulario de `/revision/<renglon_id>` y para detectar qué campos cambió el humano al guardar
    (ver `procesar_formulario`)."""
    def s(v) -> str:
        return "" if v is None else str(v)
    correcciones = data.get("correcciones") or {}

    def efectivo(campo: str, auto):
        return (correcciones[campo]["valor"] if campo in correcciones else auto)
    return {
        "producto_id": s(efectivo("producto_id", data.get("producto_id"))),
        "variante": s(efectivo("variante", data.get("variante_auto"))),
        "contenido_base": s(efectivo("contenido_base", data.get("contenido_base"))),
        "unidad_base": s(efectivo("unidad_base", data.get("unidad_base"))),
        "precio_unitario": s(efectivo("precio_unitario", data.get("precio_unitario"))),
        "cantidad": s(efectivo("cantidad", data.get("cantidad"))),
        "fecha_comparacion": s(efectivo("fecha_comparacion", data.get("fecha_compra") or data.get("fecha_proceso"))),
        "marca": s(efectivo("marca", data.get("marca"))),
    }


_NUMERICOS = {"contenido_base", "precio_unitario", "cantidad"}


def _normalizar_numero(s: str) -> str:
    """Acepta coma decimal ("1,5") además de punto: el público de esta herramienta no es técnico."""
    s = s.strip()
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    return s


def _valor_cambio(campo: str, actual: str, nuevo: str) -> bool:
    if campo in _NUMERICOS:
        try:
            n = float(nuevo)
        except (TypeError, ValueError):
            return False   # entrada inválida: no se guarda, el form vuelve a mostrar el valor vigente
        try:
            a = float(actual)
        except (TypeError, ValueError):
            a = None
        return a is None or abs(a - n) > 1e-9
    return bool(nuevo) and nuevo != (actual or "")


def procesar_formulario(conn: sqlite3.Connection, renglon_id: int, actuales: dict, entrada: dict,
                        nota: str, autor: str) -> list[str]:
    """Guarda una corrección por cada campo que el humano efectivamente cambió (compara contra
    `actuales`, los valores con que el form estaba precargado). Campos vacíos o iguales al valor
    vigente no generan una fila nueva (evita correcciones "no-op" que solo ensucian el historial)."""
    cambiados = []
    for campo in CAMPOS_CORRECCION:
        nuevo = (entrada.get(campo) or "").strip()
        if not nuevo:
            continue
        if campo in _NUMERICOS:
            nuevo = _normalizar_numero(nuevo)
        if _valor_cambio(campo, actuales.get(campo, ""), nuevo):
            guardar_correccion(conn, renglon_id, campo, nuevo, nota, autor)
            cambiados.append(campo)
    return cambiados


# ── Importar corridas del juez externo (escribe este módulo, no auditoria/juez.py) ─────────────

def importar_corrida_juez(conn: sqlite3.Connection, carpeta: str | Path) -> str:
    """Importa `items.tsv` (+ `resumen.md`) de una corrida de `auditoria/juez.py` a
    `juez_corridas` / `juez_veredictos`. Re-importar la misma carpeta reemplaza sus veredictos
    enteros (mismo criterio que `pipeline/compare.run()` con `comparaciones`: nunca deja un
    veredicto viejo de otra corrida mezclado).
    """
    folder = Path(carpeta)
    if not folder.is_dir():
        candidato = config.SCRATCH_DIR / "licitaciones" / "auditoria" / str(carpeta)
        if candidato.is_dir():
            folder = candidato
    items_path = folder / "items.tsv"
    if not items_path.exists():
        raise FileNotFoundError(f"no se encontró items.tsv en {folder}")
    resumen_text = (folder / "resumen.md").read_text(encoding="utf-8") if (folder / "resumen.md").exists() else ""
    m_modelo = re.search(r"juez \(([^)]+)\)", resumen_text)
    m_usd = re.search(r"USD\s+([\d.]+)", resumen_text)
    modelo = m_modelo.group(1) if m_modelo else None
    usd = float(m_usd.group(1)) if m_usd else None

    with open(items_path, encoding="utf-8", newline="") as fh:
        filas = list(csv.DictReader(fh, delimiter="\t"))
    modo = "casos" if any(f.get("Capa") == "caso" for f in filas) else "muestra"
    ahora = db.now_iso()
    corrida_id = db.upsert(conn, "juez_corridas", {
        "carpeta": folder.name, "modelo": modelo, "modo": modo, "n_items": len(filas),
        "usd": usd, "importada_at": ahora,
    }, ("carpeta",))
    conn.execute("DELETE FROM juez_veredictos WHERE corrida_id=?", (corrida_id,))
    for f in filas:
        conn.execute(
            "INSERT INTO juez_veredictos (corrida_id, item_id, capa, veredicto, tipo_error, explicacion, "
            "valor_correcto, regla_sugerida) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (corrida_id, f.get("id"), f.get("Capa"), f.get("Veredicto juez"), f.get("Tipo de error"),
             f.get("Explicación") or None, f.get("Valor correcto") or None, f.get("Regla sugerida") or None))
    conn.commit()
    return f"{len(filas)} veredictos importados de {folder.name} (modelo {modelo or '?'}, modo {modo})"


def corridas_juez(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM juez_corridas ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        cnt = conn.execute(
            "SELECT capa, veredicto, COUNT(*) n FROM juez_veredictos WHERE corrida_id=? GROUP BY capa, veredicto",
            (r["id"],)).fetchall()
        d["conteos"] = [dict(c) for c in cnt]
        out.append(d)
    return out


def items_corrida_juez(conn: sqlite3.Connection, corrida_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM juez_veredictos WHERE corrida_id=? ORDER BY "
        "CASE veredicto WHEN 'error' THEN 0 WHEN 'dudoso' THEN 1 ELSE 2 END, id", (corrida_id,))]


# ── Documentos: proxy de solo lectura con allowlist de host ────────────────

DOC_HOSTS = {"boletinoficial.gob.ar", "www.boletinoficial.gob.ar", "comprar.gob.ar", "www.comprar.gob.ar"}
DOC_MAX_BYTES = 30 * 1024 * 1024
DOC_TIMEOUT_S = 30.0
DOC_CACHE_TTL_S = 24 * 3600   # 1 día: es solo para mirar el documento, no un archivo permanente
_USER_AGENT = "sobreprecios-revision-humana/1.0 (uso interno, transparencia)"


def _host_permitido(url: str) -> str | None:
    """Host en minúsculas si `url` es https y está en el allowlist; None si no."""
    partes = urllib.parse.urlsplit(url)
    if partes.scheme != "https" or not partes.hostname:
        return None
    host = partes.hostname.lower()
    return host if host in DOC_HOSTS else None


def obtener_documento(url: str, client=None) -> tuple[bytes, str]:
    """Baja un documento (PDF/HTML) de un host permitido para mostrarlo en un <iframe>.

    Sigue redirects UNO POR UNO (follow_redirects=False) y re-chequea el host en CADA salto: un
    primer host permitido que redirige a otro dominio se rechaza igual que uno directamente
    prohibido (nunca confiar en el host inicial de la URL pedida). Cachea el resultado 1 día en
    `config.cache_dir("docs")` (no es un archivo permanente del proyecto, es solo para no volver
    a pedirlo mientras el revisor lo tiene abierto).
    """
    if not _host_permitido(url):
        host = urllib.parse.urlsplit(url).hostname or "(sin host)"
        raise ValueError(f"no se puede mostrar: el host '{host}' no está autorizado "
                         "(solo Boletín Oficial y COMPR.AR)")

    cache_key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    cache_dir = config.cache_dir("docs")
    cache_file, meta_file = cache_dir / cache_key, cache_dir / f"{cache_key}.ctype"
    if cache_file.exists() and meta_file.exists():
        if time.time() - cache_file.stat().st_mtime < DOC_CACHE_TTL_S:
            return cache_file.read_bytes(), meta_file.read_text(encoding="utf-8").strip()

    import httpx
    own_client = client is None
    client = client or httpx.Client(timeout=DOC_TIMEOUT_S, follow_redirects=False,
                                     headers={"User-Agent": _USER_AGENT})
    try:
        actual = url
        for _ in range(6):
            if not _host_permitido(actual):
                host = urllib.parse.urlsplit(actual).hostname or "(sin host)"
                raise ValueError(f"el documento redirige a un host no autorizado ('{host}')")
            resp = client.get(actual)
            if resp.has_redirect_location:
                actual = str(httpx.URL(actual).join(resp.headers["location"]))
                continue
            resp.raise_for_status()
            contenido = resp.content
            if len(contenido) > DOC_MAX_BYTES:
                raise ValueError("el documento pesa más de 30 MB: no se muestra acá")
            ctype = resp.headers.get("content-type", "application/octet-stream")
            cache_file.write_bytes(contenido)
            meta_file.write_text(ctype, encoding="utf-8")
            return contenido, ctype
        raise ValueError("demasiados redirects siguiendo el documento")
    finally:
        if own_client:
            client.close()
