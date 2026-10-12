"""Normaliza renglones y observaciones de mercado al catálogo canónico.

Para cada renglón:
  1. producto canónico: reglas del catálogo; si no hay ganador único, LLM barato
     en lotes (prompts/machear_productos.md) restringido a los ids del catálogo.
  2. contenido por unidad de compra, en la unidad base del producto:
       - si la cantidad viene en kilogramos/litros, el precio ya es por kg/l;
       - si viene en unidades/paquetes/latas, el tamaño sale de la descripción.
  3. precio por unidad base = precio_unitario / contenido.

Las observaciones de mercado pasan por las mismas reglas (sin LLM por defecto:
son miles y las descripciones de góndola son limpias).

Nunca se fuerza un macheo: sin producto o sin tamaño, el renglón queda fuera de
la comparación con su motivo en `match_razon`, visible en el webui.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from .. import config, llm
from ..catalog import Producto, compatible_sibys, load_catalog, match_rules
from ..textnorm import norm
from ..units import parse_size

logger = logging.getLogger(__name__)

_MASS = {"kilogramos": 1.0, "kilogramo": 1.0, "kg": 1.0, "kgs": 1.0, "kilos": 1.0, "kilo": 1.0,
         "gramos": 0.001, "gramo": 0.001, "g": 0.001, "gr": 0.001, "grs": 0.001,
         "toneladas": 1000.0, "tonelada": 1000.0}
_VOLUME = {"litros": 1.0, "litro": 1.0, "l": 1.0, "lts": 1.0, "lt": 1.0,
           "mililitros": 0.001, "ml": 0.001, "cc": 0.001}
LLM_BATCH = 25


def _unit_key(unidad: str | None) -> str:
    return norm(unidad).split(" ")[0] if unidad else ""


def normalize_quantity(descripcion: str, unidad_compra: str | None, precio_unitario: float | None,
                       producto: Producto | None) -> dict:
    """Contenido (unidad base) por unidad de compra y precio por unidad base."""
    out = {"contenido_base": None, "unidad_base": None, "precio_unidad_base": None, "alertas": []}
    unit = _unit_key(unidad_compra)
    if unit in _MASS:
        out["contenido_base"], out["unidad_base"] = _MASS[unit], "kg"
    elif unit in _VOLUME:
        out["contenido_base"], out["unidad_base"] = _VOLUME[unit], "l"
    else:
        size = parse_size(descripcion)
        if size:
            out["contenido_base"], out["unidad_base"] = size.base()
        elif producto and producto.id == "resma_a4" and unit.startswith("resma"):
            out["contenido_base"], out["unidad_base"] = 500.0, "u"
            out["alertas"].append("tamano_supuesto: resma de 500 hojas")
    if producto and out["unidad_base"] and out["unidad_base"] != producto.unidad_base:
        out["alertas"].append(f"unidad_incompatible: {out['unidad_base']} vs {producto.unidad_base} del producto")
        out["contenido_base"] = out["unidad_base"] = None
    if out["contenido_base"] and precio_unitario:
        out["precio_unidad_base"] = round(precio_unitario / out["contenido_base"], 4)
    elif producto and not out["contenido_base"]:
        out["alertas"].append("sin_tamano: no se pudo leer el contenido del envase")
    return out


def _llm_schema(ids: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["items"],
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["idx", "producto_id", "es_comparable", "razon"],
            "properties": {
                "idx": {"type": "integer"},
                "producto_id": {"type": ["string", "null"], "enum": ids + [None]},
                "es_comparable": {"type": "boolean"},
                "razon": {"type": "string"},
            }}}},
    }


def llm_classify(descripciones: list[str], catalog: dict[str, Producto],
                 conn: sqlite3.Connection | None = None) -> list[dict]:
    """Clasifica descripciones contra el catálogo en lotes. [] si no hay LLM."""
    template = (config.PROMPTS_DIR / "machear_productos.md").read_text(encoding="utf-8")
    cat_text = "\n".join(f"- {p.id}: {p.nombre}" for p in catalog.values())
    schema = _llm_schema(list(catalog))
    results: list[dict] = []
    for start in range(0, len(descripciones), LLM_BATCH):
        chunk = descripciones[start:start + LLM_BATCH]
        items = "\n".join(f"{i}: {d}" for i, d in enumerate(chunk))
        prompt = template.replace("<<CATALOGO>>", cat_text).replace("<<ITEMS>>", items)
        data = llm.complete_json(prompt, schema, role="cheap", proposito="machear_productos",
                                 conn=conn, schema_name="macheo")
        by_idx = {it["idx"]: it for it in data.get("items", [])}
        for i, _ in enumerate(chunk):
            it = by_idx.get(i) or {"producto_id": None, "es_comparable": False, "razon": "sin respuesta"}
            results.append(it)
    return results


def match_renglones(conn: sqlite3.Connection, use_llm: bool = True, solo_pendientes: bool = True,
                    progress=None) -> str:
    catalog = load_catalog()
    where = "WHERE r.match_metodo IS NULL" if solo_pendientes else ""
    rows = conn.execute(f"SELECT r.* FROM renglones r {where}").fetchall()
    pendientes_llm = []
    n_rules = 0
    for r in rows:
        m = match_rules(r["descripcion"], catalog)
        ok, why_sibys = compatible_sibys(r["descripcion"], m.producto_id)
        if m.producto_id and ok:
            _apply(conn, r, catalog[m.producto_id], m.metodo, m.score, m.razon, True)
            n_rules += 1
        elif m.producto_id and not ok:
            _apply(conn, r, None, "sibys_incompatible", 0.0, f"reglas: {m.razon} · {why_sibys}", False)
        else:
            ok_any, why_any = compatible_sibys(r["descripcion"], "__cualquiera__")
            if not ok_any and "sin mapeo" in why_any:
                # Clase SIByS sin mapeo: ni siquiera se le pregunta al LLM (no hay producto comparable).
                _apply(conn, r, None, "sibys_sin_mapeo", 0.0, why_any, False)
            else:
                pendientes_llm.append((r, m.razon))
    n_llm = 0
    if pendientes_llm and use_llm:
        try:
            llm.reset_budget()
            answers = llm_classify([r["descripcion"] for r, _ in pendientes_llm], catalog, conn)
            for (r, why_rules), ans in zip(pendientes_llm, answers):
                pid = ans.get("producto_id")
                if pid and not compatible_sibys(r["descripcion"], pid)[0]:
                    _apply(conn, r, None, "sibys_incompatible", 0.0,
                           f"LLM: {pid} · {compatible_sibys(r['descripcion'], pid)[1]}", False)
                elif pid and pid in catalog:
                    _apply(conn, r, catalog[pid], f"llm:{llm.resolve_model('cheap')}", 0.7,
                           ans.get("razon", ""), ans.get("es_comparable", True))
                    n_llm += 1
                else:
                    _apply(conn, r, None, "llm_sin_producto", 0.0,
                           f"reglas: {why_rules} · LLM: {ans.get('razon', '')}", ans.get("es_comparable", False))
        except llm.LLMUnavailable as exc:
            logger.warning("LLM no disponible para macheo: %s", exc)
            for r, why in pendientes_llm:
                _apply(conn, r, None, None, 0.0, f"reglas: {why} · LLM no disponible", None)
    elif pendientes_llm:
        for r, why in pendientes_llm:
            _apply(conn, r, None, "reglas_sin_producto", 0.0, why, None)
    conn.commit()
    if progress:
        progress("macheo de renglones listo")
    return (f"{len(rows)} renglones: {n_rules} por reglas, {n_llm} por LLM, "
            f"{len(rows) - n_rules - n_llm} sin producto")


def _apply(conn: sqlite3.Connection, r: sqlite3.Row, producto: Producto | None, metodo: str | None,
           score: float, razon: str, es_comparable) -> None:
    norm_q = normalize_quantity(r["descripcion"], r["unidad_compra"], r["precio_unitario"], producto)
    alertas = json.loads(r["alertas"]) if r["alertas"] else []
    alertas = [a for a in alertas if not a.split(":")[0] in {"sin_tamano", "unidad_incompatible", "tamano_supuesto"}]
    alertas += norm_q["alertas"]
    conn.execute(
        "UPDATE renglones SET producto_id=?, match_metodo=?, match_confianza=?, match_razon=?, "
        "es_comparable=?, contenido_base=?, unidad_base=?, precio_unidad_base=?, alertas=? WHERE id=?",
        (producto.id if producto else None, metodo, score, razon,
         None if es_comparable is None else int(bool(es_comparable)),
         norm_q["contenido_base"], norm_q["unidad_base"], norm_q["precio_unidad_base"],
         json.dumps(alertas, ensure_ascii=False) if alertas else None, r["id"]))


def match_mercado(conn: sqlite3.Connection, solo_pendientes: bool = True) -> str:
    """Asigna producto canónico a observaciones de mercado por reglas (por descripción distinta).

    Con solo_pendientes=False re-evalúa TODO con el catálogo vigente y también DESMACHEA lo que ya
    no corresponde: antes las exclusiones nuevas no se aplicaban a filas ya macheadas (encontrado por
    la revisión del LLM prime 2026-10-11: leche descremada, tarta de atún y alimento para gatos
    seguían adentro). Las series oficiales (INDEC) no se tocan.
    """
    catalog = load_catalog()
    where = ("WHERE producto_id IS NULL AND match_metodo IS NULL" if solo_pendientes
             else "WHERE canal <> 'oficial'")
    descs = [r[0] for r in conn.execute(f"SELECT DISTINCT descripcion FROM precios_mercado {where}")]
    n = n_quitados = 0
    for desc in descs:
        m = match_rules(desc or "", catalog)
        prod = catalog.get(m.producto_id) if m.producto_id else None
        size = parse_size(desc) if prod else None
        base = size.base() if size else (None, None)
        if prod and (base[1] is None or base[1] == prod.unidad_base):
            cur = conn.execute(
                "UPDATE precios_mercado SET producto_id=?, match_metodo='reglas', match_confianza=?, "
                "contenido_base=COALESCE(?, contenido_base), unidad_base=COALESCE(?, unidad_base), "
                "precio_unidad_base=CASE WHEN ? IS NOT NULL THEN precio / ? ELSE precio_unidad_base END "
                "WHERE descripcion=? AND canal <> 'oficial'",
                (prod.id, m.score, base[0], base[1], base[0], base[0] or 1, desc))
            n += 1 if cur.rowcount else 0
        else:
            cur = conn.execute("UPDATE precios_mercado SET producto_id=NULL, match_metodo='reglas_sin_producto' "
                               "WHERE descripcion=? AND canal <> 'oficial' AND (producto_id IS NOT NULL OR match_metodo IS NULL)",
                               (desc,))
            n_quitados += cur.rowcount
    # Envase implausible para el producto: un "arroz" de 25 g es un condimento, uno de 80 g un snack. No bajan el
    # mínimo (son más caros por kg) pero arruinan medianas y gráficos. Fuera si < factor x tamaño de referencia.
    factor = float((config.settings().get("comparacion") or {}).get("contenido_minimo_factor", 0.3))
    n_chicos = 0
    for prod in catalog.values():
        if prod.tamano_referencia:
            cur = conn.execute(
                "UPDATE precios_mercado SET producto_id=NULL, match_metodo='envase_no_plausible' "
                "WHERE producto_id=? AND canal <> 'oficial' AND contenido_base > 0 AND contenido_base < ?",
                (prod.id, prod.tamano_referencia * factor))
            n_chicos += cur.rowcount
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM precios_mercado WHERE producto_id IS NOT NULL").fetchone()[0]
    from . import cuarentena
    return (f"{len(descs)} descripciones de mercado revisadas, {n} con producto, {n_quitados} observaciones "
            f"desmacheadas, {n_chicos} por envase implausible; {total} observaciones macheadas; {cuarentena.run(conn)}")
