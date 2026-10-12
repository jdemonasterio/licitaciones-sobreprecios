"""Consultas SQL del webui. Las rutas (app.py) no escriben SQL: piden datos acá.

Convenciones:
- `filtros` es un dict con claves opcionales: q, producto, organismo, semaforo,
  desde, hasta, revision, proveedor. Valores vacíos se ignoran.
- "exceso" = exceso_ars de comparaciones (solo positivo, ya ajustado por costo
  financiero). "monto" = monto_total de los renglones.
- El peor semáforo de un proceso: rojo > amarillo > verde > gris.
"""
from __future__ import annotations

import json
import sqlite3

SEMAFORO_RANK = "CASE k.semaforo WHEN 'rojo' THEN 3 WHEN 'amarillo' THEN 2 WHEN 'verde' THEN 1 ELSE 0 END"
_RANK_TO_SEM = {3: "rojo", 2: "amarillo", 1: "verde", 0: "gris"}


def _where(filtros: dict) -> tuple[str, list]:
    clauses, params = ["1=1"], []
    f = {k: v for k, v in (filtros or {}).items() if v not in (None, "", [])}
    if "q" in f:
        like = f"%{f['q']}%"
        clauses.append("(p.organismo LIKE ? OR p.objeto LIKE ? OR p.numero LIKE ? OR r.proveedor LIKE ? "
                       "OR r.descripcion LIKE ? OR r.proveedor_cuit LIKE ?)")
        params += [like] * 6
    if "producto" in f:
        clauses.append("r.producto_id = ?")
        params.append(f["producto"])
    if "organismo" in f:
        clauses.append("COALESCE(p.organismo_canonico, p.organismo) = ?")
        params.append(f["organismo"])
    if "semaforo" in f:
        clauses.append("k.semaforo = ?")
        params.append(f["semaforo"])
    if "desde" in f:
        clauses.append("COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) >= ?")
        params.append(f["desde"])
    if "hasta" in f:
        clauses.append("COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) <= ?")
        params.append(f["hasta"])
    if "revision" in f:
        clauses.append("p.estado_revision = ?")
        params.append(f["revision"])
    if "proveedor" in f:
        clauses.append("r.proveedor_cuit = ?")
        params.append(f["proveedor"])
    return " AND ".join(clauses), params


_BASE_FROM = ("FROM procesos p JOIN renglones r ON r.proceso_id = p.id "
              "LEFT JOIN comparaciones k ON k.renglon_id = r.id")


def kpis(conn: sqlite3.Connection, filtros: dict) -> dict:
    where, params = _where(filtros)
    row = conn.execute(
        f"SELECT COUNT(DISTINCT p.id) procesos, COUNT(r.id) renglones, "
        f"SUM(CASE WHEN k.semaforo IN ('rojo','amarillo','verde') THEN 1 ELSE 0 END) comparados, "
        f"SUM(CASE WHEN k.semaforo='rojo' THEN 1 ELSE 0 END) rojos, "
        f"SUM(CASE WHEN k.semaforo='amarillo' THEN 1 ELSE 0 END) amarillos, "
        f"COALESCE(SUM(r.monto_total), 0) monto, "
        f"COALESCE(SUM(CASE WHEN k.semaforo IN ('rojo','amarillo') THEN k.exceso_ars END), 0) exceso, "
        f"MIN(COALESCE(p.fecha_adjudicacion, p.fecha_publicacion)) desde, "
        f"MAX(COALESCE(p.fecha_adjudicacion, p.fecha_publicacion)) hasta "
        f"{_BASE_FROM} WHERE {where}", params).fetchone()
    return dict(row)


ORDENES = {
    "exceso": "exceso DESC, peor DESC",
    "sobreprecio": "max_sobreprecio DESC",
    "ofertas": "max_brecha_ofertas DESC",
    "fecha": "fecha DESC",
    "monto": "monto DESC",
}


def list_procesos(conn: sqlite3.Connection, filtros: dict, orden: str = "exceso",
                  limit: int = 200, offset: int = 0) -> list[dict]:
    where, params = _where(filtros)
    order_by = ORDENES.get(orden, ORDENES["exceso"])
    rows = conn.execute(
        f"SELECT p.id, p.numero, p.organismo, p.objeto, p.tipo_procedimiento, p.url, p.estado_revision, "
        f"COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha, "
        f"COUNT(r.id) n_renglones, COALESCE(SUM(r.monto_total), 0) monto, "
        f"COALESCE(SUM(CASE WHEN k.semaforo IN ('rojo','amarillo') THEN k.exceso_ars END), 0) exceso, "
        f"MAX({SEMAFORO_RANK}) peor, MAX(k.sobreprecio_ajustado_pct) max_sobreprecio, "
        f"MAX(k.brecha_oferta_min_pct) max_brecha_ofertas, "
        f"GROUP_CONCAT(DISTINCT r.producto_id) productos, GROUP_CONCAT(DISTINCT r.proveedor) proveedores, "
        f"d.titulo norma "
        f"{_BASE_FROM} LEFT JOIN documentos d ON d.id = p.documento_id "
        f"WHERE {where} GROUP BY p.id ORDER BY {order_by} LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["peor_semaforo"] = _RANK_TO_SEM.get(item.pop("peor") or 0, "gris")
        item["productos"] = [p for p in (item["productos"] or "").split(",") if p]
        item["proveedores"] = [p for p in (item["proveedores"] or "").split(",") if p]
        out.append(item)
    return out


def proceso(conn: sqlite3.Connection, proceso_id: int) -> dict | None:
    row = conn.execute(
        "SELECT p.*, d.titulo norma, d.texto texto, d.url doc_url FROM procesos p "
        "LEFT JOIN documentos d ON d.id = p.documento_id WHERE p.id = ?", (proceso_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["ordenes"] = [dict(r) for r in conn.execute(
        "SELECT * FROM ordenes_compra WHERE proceso_id=? ORDER BY fecha, numero_oc", (proceso_id,))]
    data["alertas_proceso"] = [dict(r) for r in conn.execute(
        "SELECT * FROM alertas_proceso WHERE proceso_id=? ORDER BY monto DESC", (proceso_id,))]
    return data


def renglones(conn: sqlite3.Connection, proceso_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT r.*, k.*, pr.nombre producto_nombre, r.id id FROM renglones r "
        "LEFT JOIN comparaciones k ON k.renglon_id = r.id LEFT JOIN productos pr ON pr.id = r.producto_id "
        f"WHERE r.proceso_id = ? ORDER BY {SEMAFORO_RANK.replace('k.', 'k.')} DESC, k.exceso_ars DESC, r.nro_renglon",
        (proceso_id,)).fetchall()
    ofertas: dict[int | None, list[dict]] = {}
    for o in conn.execute("SELECT * FROM ofertas WHERE proceso_id=? ORDER BY nro_renglon, precio_unitario",
                          (proceso_id,)):
        ofertas.setdefault(o["nro_renglon"], []).append(dict(o))
    out = []
    for r in rows:
        item = dict(r)
        item["alertas_list"] = json.loads(item["alertas"]) if item.get("alertas") else []
        item["evidencia"] = json.loads(item["evidencia_json"]) if item.get("evidencia_json") else []
        item["ofertas"] = ofertas.get(item.get("nro_renglon"), [])
        out.append(item)
    return out


def opciones_filtros(conn: sqlite3.Connection) -> dict:
    return {
        "organismos": [r[0] for r in conn.execute(
            "SELECT DISTINCT COALESCE(organismo_canonico, organismo) o FROM procesos WHERE o IS NOT NULL ORDER BY o")],
        "productos": [dict(r) for r in conn.execute(
            "SELECT DISTINCT pr.id, pr.nombre FROM productos pr JOIN renglones r ON r.producto_id = pr.id "
            "ORDER BY pr.nombre")],
    }


def agrupado(conn: sqlite3.Connection, filtros: dict, dimension: str, limit: int = 25) -> list[dict]:
    """Totales por organismo / producto / proveedor / procedimiento / mes."""
    dims = {
        "organismo": "COALESCE(p.organismo_canonico, p.organismo, '(sin organismo)')",
        "producto": "COALESCE(pr.nombre, r.producto_id, '(sin producto)')",
        # Por clave (CUIT válido); sin CUIT, cada texto queda separado (decisión del prime P14).
        "proveedor": "COALESCE((SELECT e.razon_social FROM empresas e WHERE e.cuit = r.proveedor_cuit), r.proveedor, '(sin proveedor)')",
        "procedimiento": "COALESCE(p.tipo_procedimiento, '(sin dato)')",
        "mes": "substr(COALESCE(p.fecha_adjudicacion, p.fecha_publicacion), 1, 7)",
    }
    expr = dims[dimension]
    where, params = _where(filtros)
    order = "clave" if dimension == "mes" else "exceso DESC, monto DESC"
    rows = conn.execute(
        f"SELECT {expr} clave, COUNT(DISTINCT p.id) procesos, COUNT(r.id) renglones, "
        f"COALESCE(SUM(r.monto_total), 0) monto, "
        f"COALESCE(SUM(CASE WHEN k.semaforo IN ('rojo','amarillo') THEN k.exceso_ars END), 0) exceso, "
        f"SUM(CASE WHEN k.semaforo='rojo' THEN 1 ELSE 0 END) rojos, "
        f"SUM(CASE WHEN k.semaforo='amarillo' THEN 1 ELSE 0 END) amarillos, "
        f"SUM(CASE WHEN k.semaforo IN ('rojo','amarillo','verde') THEN 1 ELSE 0 END) comparados, "
        f"ROUND(AVG(CASE WHEN k.semaforo IN ('rojo','amarillo','verde') THEN k.sobreprecio_ajustado_pct END), 1) sp_prom, "
        f"MAX(r.proveedor_cuit) cuit "
        f"{_BASE_FROM} LEFT JOIN productos pr ON pr.id = r.producto_id WHERE {where} "
        f"GROUP BY clave ORDER BY {order} LIMIT ?", params + [limit]).fetchall()
    return [dict(r) for r in rows]


def peores_renglones(conn: sqlite3.Connection, filtros: dict, limit: int = 15) -> list[dict]:
    where, params = _where(filtros)
    rows = conn.execute(
        f"SELECT p.id proceso_id, p.organismo, COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha, "
        f"r.descripcion, r.proveedor, r.proveedor_cuit, r.precio_unidad_base, r.unidad_base, k.ref_principal, "
        f"k.ref_principal_valor, k.sobreprecio_ajustado_pct, k.exceso_ars, k.semaforo, k.confianza "
        f"{_BASE_FROM} WHERE {where} AND k.semaforo IN ('rojo','amarillo','verde') "
        f"ORDER BY k.sobreprecio_ajustado_pct DESC LIMIT ?", params + [limit]).fetchall()
    return [dict(r) for r in rows]


def serie_producto(conn: sqlite3.Connection, producto_id: str) -> dict:
    """Precio pagado por el Estado (puntos) vs mercado (p25 mayorista mensual y INDEC)."""
    estado = [dict(r) for r in conn.execute(
        "SELECT COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha, r.precio_unidad_base precio, "
        "r.proveedor, p.id proceso_id, k.semaforo FROM renglones r JOIN procesos p ON p.id = r.proceso_id "
        "LEFT JOIN comparaciones k ON k.renglon_id = r.id "
        "WHERE r.producto_id = ? AND r.precio_unidad_base IS NOT NULL ORDER BY fecha", (producto_id,))]
    mercado_rows = conn.execute(
        "SELECT substr(fecha, 1, 7) mes, canal, precio_unidad_base FROM precios_mercado "
        "WHERE producto_id = ? AND precio_unidad_base IS NOT NULL", (producto_id,)).fetchall()
    by_key: dict[tuple, list[float]] = {}
    for r in mercado_rows:
        by_key.setdefault((r["mes"], r["canal"]), []).append(r["precio_unidad_base"])

    def p25(values):
        values = sorted(values)
        pos = (len(values) - 1) * 0.25
        lo = int(pos)
        hi = min(lo + 1, len(values) - 1)
        return values[lo] + (values[hi] - values[lo]) * (pos - lo)

    def robust_min(values):
        # Mismo criterio que el comparador: mínimo después de sacar outliers (< 0.4 x mediana).
        values = sorted(values)
        med = values[len(values) // 2]
        kept = [v for v in values if v >= 0.4 * med] or values
        return kept[0]

    mayorista = sorted(((m, round(p25(v), 2), len(v)) for (m, c), v in by_key.items() if c == "mayorista"))
    mayorista_min = sorted(((m, round(robust_min(v), 2), len(v)) for (m, c), v in by_key.items() if c == "mayorista"))
    oficial = sorted(((m, round(sum(v) / len(v), 2), len(v)) for (m, c), v in by_key.items() if c == "oficial"))
    minorista = sorted(((m, round(p25(v), 2), len(v)) for (m, c), v in by_key.items() if c == "minorista"))
    # Eje de meses CONTINUO (un mes sin datos es un hueco, nunca una recta que lo cruza),
    # centrado en las compras del Estado ±6 meses para que se lea la comparación.
    meses_estado = sorted({e["fecha"][:7] for e in estado if e.get("fecha")})
    todos = sorted({m for m, _, _ in mayorista + oficial + minorista} | set(meses_estado))
    if meses_estado:
        lo, hi = _shift_month(meses_estado[0], -6), _shift_month(meses_estado[-1], 6)
    elif todos:
        lo, hi = todos[0], todos[-1]
    else:
        lo = hi = None
    meses = []
    if lo:
        cur = lo
        while cur <= hi:
            meses.append(cur)
            cur = _shift_month(cur, 1)
    def aligned(series):
        values = {m: v for m, v, _ in series}
        return [values.get(m) for m in meses]
    estado_por_mes: dict[str, list[float]] = {}
    for e in estado:
        estado_por_mes.setdefault(e["fecha"][:7], []).append(e["precio"])
    return {"estado": estado, "mayorista_p25": mayorista, "indec": oficial, "minorista_p25": minorista,
            "mayorista_min": mayorista_min,
            "meses": meses, "chart_mayorista": aligned(mayorista_min), "chart_indec": aligned(oficial),
            "chart_minorista": aligned(minorista),
            "chart_estado": [[m, p] for m in meses for p in estado_por_mes.get(m, [])]}


def _shift_month(ym: str, delta: int) -> str:
    year, month = int(ym[:4]), int(ym[5:7])
    total = year * 12 + (month - 1) + delta
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def empresas(conn: sqlite3.Connection, q: str = "", limit: int = 300) -> list[dict]:
    params: list = []
    where = "1=1"
    if q:
        where = "(e.razon_social LIKE ? OR e.cuit LIKE ?)"
        params = [f"%{q}%", f"%{q}%"]
    rows = conn.execute(
        f"SELECT e.*, COUNT(DISTINCT r.proceso_id) procesos, COALESCE(SUM(r.monto_total), 0) monto, "
        f"COALESCE(SUM(CASE WHEN k.semaforo IN ('rojo','amarillo') THEN k.exceso_ars END), 0) exceso, "
        f"(SELECT COUNT(*) FROM empresa_hechos h WHERE h.cuit = e.cuit) n_hechos "
        f"FROM empresas e LEFT JOIN renglones r ON r.proveedor_cuit = e.cuit "
        f"LEFT JOIN comparaciones k ON k.renglon_id = r.id WHERE {where} "
        f"GROUP BY e.cuit ORDER BY monto DESC LIMIT ?", params + [limit]).fetchall()
    return [dict(r) for r in rows]


def empresa(conn: sqlite3.Connection, cuit: str) -> dict | None:
    row = conn.execute("SELECT * FROM empresas WHERE cuit = ?", (cuit,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["hechos"] = [dict(r) for r in conn.execute(
        "SELECT * FROM empresa_hechos WHERE cuit = ? ORDER BY tipo, vigente_hasta IS NOT NULL, "
        "vigente_desde DESC", (cuit,))]
    data["adjudicaciones"] = [dict(r) for r in conn.execute(
        "SELECT p.id proceso_id, p.organismo, p.numero, COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha, "
        "r.descripcion, r.cantidad, r.unidad_compra, r.precio_unitario, r.monto_total, k.semaforo, "
        "k.sobreprecio_ajustado_pct FROM renglones r JOIN procesos p ON p.id = r.proceso_id "
        "LEFT JOIN comparaciones k ON k.renglon_id = r.id WHERE r.proveedor_cuit = ? ORDER BY fecha DESC",
        (cuit,))]
    data["alertas"] = alertas_empresa(conn, data)
    # Personas de esta empresa que aparecen también en otras empresas adjudicatarias.
    data["vinculos"] = [dict(r) for r in conn.execute(
        "SELECT h2.persona, h2.cuit, e2.razon_social, h2.tipo, h2.cargo, h2.vigente_desde, "
        "CASE WHEN h1.persona_clave LIKE 'DNI:%' THEN 'mismo DNI' ELSE 'mismo nombre (posible homónimo)' END coincidencia "
        "FROM empresa_hechos h1 JOIN empresa_hechos h2 ON h2.persona_clave = h1.persona_clave AND h2.cuit <> h1.cuit "
        "LEFT JOIN empresas e2 ON e2.cuit = h2.cuit "
        "WHERE h1.cuit = ? AND h1.persona_clave IS NOT NULL GROUP BY h2.persona_clave, h2.cuit",
        (cuit,))]
    return data


def alertas_empresa(conn: sqlite3.Connection, e: dict) -> list[str]:
    """Señales para mirar (no conclusiones): empresa nueva al ganar, actividad no relacionada."""
    import datetime as dt
    from ..sources.registro_sociedades import actividad_relacionada
    out = []
    if e.get("fecha_constitucion") and e.get("primera_adjudicacion"):
        try:
            dias = (dt.date.fromisoformat(e["primera_adjudicacion"][:10])
                    - dt.date.fromisoformat(e["fecha_constitucion"][:10])).days
            if 0 <= dias < 365:
                out.append(f"Se constituyó {dias} días antes de su primera adjudicación registrada acá.")
        except ValueError:
            pass
    actividades = [h["valor"] for h in e.get("hechos", []) if h["tipo"] == "actividad" and not h.get("vigente_hasta")]
    rubros = {r[0] for r in conn.execute(
        "SELECT DISTINCT pr.rubro FROM renglones r JOIN productos pr ON pr.id = r.producto_id "
        "WHERE r.proveedor_cuit = ?", (e["cuit"],))}
    if actividad_relacionada(actividades, rubros) is False:
        out.append("Sus actividades declaradas (Registro Nacional de Sociedades) no incluyen el rubro de lo que vendió: "
                   + ", ".join(sorted(rubros)) + ".")
    return out


def corridas(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM corridas ORDER BY id DESC LIMIT ?", (limit,))]


def fuentes_resumen(conn: sqlite3.Connection) -> dict:
    return {
        "documentos": [dict(r) for r in conn.execute(
            "SELECT fuente, COUNT(*) n, MIN(fecha_publicacion) desde, MAX(fecha_publicacion) hasta "
            "FROM documentos GROUP BY fuente")],
        "precios": [dict(r) for r in conn.execute(
            "SELECT fuente, canal, COUNT(*) n, SUM(producto_id IS NOT NULL) macheados, MIN(fecha) desde, "
            "MAX(fecha) hasta FROM precios_mercado GROUP BY fuente, canal ORDER BY canal, fuente")],
        "llm": dict(conn.execute(
            "SELECT COUNT(*) llamadas, SUM(cache_hit) cache, ROUND(COALESCE(SUM(costo_usd), 0), 4) usd, "
            "SUM(error IS NOT NULL) errores FROM llm_calls").fetchone()),
    }
