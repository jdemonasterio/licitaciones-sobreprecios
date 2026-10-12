"""Goldens de validación manual (TSV para pegar en Google Sheets), en _scratch/licitaciones/.

Convención (CLAUDE.md del repo de origen): una tabla para revisar = TSV; ids
estables en las columnas 4-5; al final lo que se lee para decidir y dos columnas
vacías para el revisor ("Tu veredicto", "Tu comentario"). El código que relee un
golden revisado lo hace por nombre de columna, nunca por posición.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

from . import config, db
from .sources import legacy_quijotes

REVIEW = ["Tu veredicto", "Tu comentario"]


def _write(name: str, header: list[str], rows: list[list]) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    path = config.scratch_path("licitaciones", f"{name}_{stamp}.tsv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        writer.writerow(header + REVIEW)
        for row in rows:
            clean = ["" if v is None else re.sub(r"[\t\r\n]+", " ", str(v)) for v in row]
            writer.writerow(clean + ["", ""])
    return path


def extraccion_vs_quijote(conn: sqlite3.Connection, dump: Path | None = None) -> Path:
    """Renglones extraídos del aviso original vs lo que había cargado Quijote 2021 a mano."""
    legacy: dict[str, list[str]] = {}
    path = Path(dump or legacy_quijotes.DEFAULT_DUMP)
    if path.exists():
        sql = path.read_text(encoding="utf-8", errors="replace")
        for row in legacy_quijotes.table_dicts(sql, "compras_del_estado"):
            m = legacy_quijotes._AVISO_RE.search(row.get("link") or "")
            if m:
                legacy.setdefault("/".join(m.groups()), []).append(
                    f"{row.get('producto')} x{row.get('cantidad')} a ${row.get('precio')}")
    rows = conn.execute(
        "SELECT p.external_id, p.organismo, p.id proceso_id, r.id renglon_id, d.titulo, p.numero, "
        "r.nro_renglon, r.proveedor, r.proveedor_cuit, r.descripcion, r.marca, r.cantidad, r.unidad_compra, "
        "r.precio_unitario, r.monto_total, r.precio_unidad_base, r.unidad_base, r.extraccion_metodo, r.alertas, p.url "
        "FROM renglones r JOIN procesos p ON p.id=r.proceso_id LEFT JOIN documentos d ON d.id=p.documento_id "
        "ORDER BY p.fecha_publicacion, p.id, r.nro_renglon").fetchall()
    out = []
    for r in rows:
        alertas = "; ".join(json.loads(r["alertas"])) if r["alertas"] else ""
        out.append([r["organismo"], r["titulo"], r["numero"], r["proceso_id"], r["renglon_id"], r["nro_renglon"],
                    r["proveedor"], r["proveedor_cuit"], r["descripcion"], r["marca"], r["cantidad"], r["unidad_compra"],
                    r["precio_unitario"], r["monto_total"], r["precio_unidad_base"], r["unidad_base"],
                    r["extraccion_metodo"], alertas, r["url"],
                    "; ".join(legacy.get(r["external_id"], [])), "¿Coincide con el aviso? ¿y con Quijote?"])
    header = ["Organismo", "Norma", "Proceso", "proceso_id", "renglon_id", "Renglón", "Proveedor", "CUIT",
              "Descripción extraída", "Marca", "Cantidad", "Unidad", "Precio unitario", "Monto", "Precio por u. base",
              "U. base", "Método", "Alertas", "Link BO", "Quijote 2021 (mismo aviso)", "Qué mirar"]
    return _write("validation_extraccion_bo_vs_quijote", header, out)


def comparaciones(conn: sqlite3.Connection) -> Path:
    rows = conn.execute(
        "SELECT p.organismo, COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha, p.numero, p.id proceso_id, "
        "r.id renglon_id, r.descripcion, r.marca, pr.nombre producto, r.cantidad, r.unidad_compra, "
        "r.precio_unitario, r.precio_unidad_base, r.unidad_base, k.ref_min_mayorista, k.ref_p25_mayorista, "
        "k.ref_mediana_mayorista, k.ref_n_mayorista, k.ref_min_minorista, k.ref_mediana_minorista, k.ref_indec, "
        "k.ref_misma_marca, k.ajuste_financiero_pct, k.ref_principal, k.ref_principal_valor, "
        "k.sobreprecio_min_pct, k.sobreprecio_ajustado_pct, k.exceso_ars, k.semaforo, k.confianza, k.motivo, "
        "k.evidencia_json, r.proveedor, p.url, k.ref_corroborada, k.sobreprecio_corroborado_pct, k.estado_atributo, "
        "k.motivo_codigo "
        "FROM renglones r JOIN procesos p ON p.id=r.proceso_id LEFT JOIN comparaciones k ON k.renglon_id=r.id "
        "LEFT JOIN productos pr ON pr.id=r.producto_id ORDER BY k.sobreprecio_ajustado_pct DESC").fetchall()
    out = []
    for r in rows:
        evid = json.loads(r["evidencia_json"]) if r["evidencia_json"] else []
        minimo = min((o for o in evid if o.get("canal") in {"mayorista", "minorista"}), key=lambda o: o["precio_ub"],
                     default=None)
        out.append([r["organismo"], r["fecha"], r["numero"], r["proceso_id"], r["renglon_id"], r["descripcion"],
                    r["marca"], r["producto"], r["cantidad"], r["unidad_compra"], r["precio_unitario"],
                    r["precio_unidad_base"], r["unidad_base"], r["ref_min_mayorista"],
                    f"{minimo['descripcion']} ({minimo['fuente']}, {minimo['fecha']})" if minimo else "",
                    r["ref_p25_mayorista"], r["ref_mediana_mayorista"], r["ref_n_mayorista"], r["ref_min_minorista"],
                    r["ref_mediana_minorista"], r["ref_indec"], r["ref_misma_marca"], r["ajuste_financiero_pct"],
                    r["sobreprecio_min_pct"], r["exceso_ars"], r["url"], r["proveedor"], r["ref_principal"],
                    r["ref_principal_valor"], r["sobreprecio_ajustado_pct"], r["ref_corroborada"],
                    r["sobreprecio_corroborado_pct"], r["estado_atributo"], r["motivo_codigo"], r["semaforo"],
                    r["confianza"], r["motivo"]])
    header = ["Organismo", "Fecha", "Proceso", "proceso_id", "renglon_id", "Descripción", "Marca", "Producto canónico",
              "Cantidad", "Unidad", "Precio unitario", "Precio adjudicado por u. base", "U. base", "Mín. mayorista",
              "De dónde sale el mínimo", "P25 mayorista", "Mediana mayorista", "N obs. mayorista", "Mín. súper",
              "Mediana súper", "INDEC", "Misma marca", "Ajuste financiero %", "Brecha vs mínimo (sin ajuste) %",
              "Exceso $", "Link BO", "Proveedor", "Referencia usada", "Valor referencia", "Brecha ajustada %",
              "Mínimo confirmado (2 fuentes)", "Brecha vs confirmado, plazo largo %", "Variedad", "Código motivo",
              "Semáforo", "Confianza", "Motivo"]
    return _write("validation_comparaciones", header, out)


def precios_hoy(conn: sqlite3.Connection) -> Path:
    fecha = conn.execute("SELECT MAX(fecha) FROM precios_mercado WHERE canal <> 'oficial'").fetchone()[0]
    rows = conn.execute(
        "SELECT producto_id, fuente, canal, COUNT(*) n, MIN(precio_unidad_base) mn, MAX(precio_unidad_base) mx "
        "FROM precios_mercado WHERE fecha=? AND producto_id IS NOT NULL GROUP BY producto_id, fuente ORDER BY producto_id, canal, fuente",
        (fecha,)).fetchall()
    out = []
    for r in rows:
        vals = sorted(v[0] for v in conn.execute(
            "SELECT precio_unidad_base FROM precios_mercado WHERE fecha=? AND producto_id=? AND fuente=?",
            (fecha, r["producto_id"], r["fuente"])))
        med = vals[len(vals) // 2]
        kept = [v for v in vals if v >= 0.4 * med]
        robust_min = kept[0] if kept else None
        ej_min = conn.execute(
            "SELECT descripcion FROM precios_mercado WHERE fecha=? AND producto_id=? AND fuente=? AND precio_unidad_base=?",
            (fecha, r["producto_id"], r["fuente"], robust_min)).fetchone()
        ej_bajo = conn.execute(
            "SELECT descripcion, precio_unidad_base FROM precios_mercado WHERE fecha=? AND producto_id=? AND fuente=? "
            "ORDER BY precio_unidad_base LIMIT 1", (fecha, r["producto_id"], r["fuente"])).fetchone()
        out.append([r["producto_id"], r["canal"], fecha, r["fuente"], r["n"], round(r["mn"], 2), round(med, 2),
                    round(r["mx"], 2), round(robust_min, 2) if robust_min else "",
                    f"{ej_bajo[0]} ({ej_bajo[1]:.2f})" if ej_bajo else "",
                    ej_min[0] if ej_min else "", "¿El mínimo usado es el mismo producto y tamaño bien leído?"])
    header = ["Producto", "Canal", "Fecha", "Fuente", "N obs.", "Mínimo crudo", "Mediana", "Máximo",
              "Mínimo usado (sin outliers)", "Observación más baja (descartada si es outlier)",
              "Producto del mínimo usado", "Qué mirar"]
    return _write("validation_precios_hoy", header, out)


def comprar(conn: sqlite3.Connection) -> Path:
    """Procesos de COMPR.AR: lo guardado vs lo que muestra la pantalla (link para chequear)."""
    rows = conn.execute(
        "SELECT p.organismo, p.numero, p.objeto, p.id proceso_id, r.id renglon_id, r.nro_renglon, r.descripcion, "
        "r.cantidad, r.unidad_compra, r.precio_unitario, r.monto_total, r.marca, pr.nombre producto, "
        "k.n_ofertas, k.oferta_min, k.oferta_min_proveedor, k.brecha_oferta_min_pct, k.semaforo, "
        "k.sobreprecio_ajustado_pct, p.url, r.proveedor, r.proveedor_cuit, p.fecha_adjudicacion "
        "FROM renglones r JOIN procesos p ON p.id=r.proceso_id LEFT JOIN comparaciones k ON k.renglon_id=r.id "
        "LEFT JOIN productos pr ON pr.id=r.producto_id WHERE r.extraccion_metodo='comprar' "
        "ORDER BY k.brecha_oferta_min_pct DESC").fetchall()
    out = [[r["organismo"], r["numero"], r["fecha_adjudicacion"], r["proceso_id"], r["renglon_id"], r["objeto"],
            r["nro_renglon"], r["descripcion"], r["cantidad"], r["unidad_compra"], r["precio_unitario"], r["monto_total"],
            r["marca"], r["producto"], r["n_ofertas"], r["oferta_min"], r["oferta_min_proveedor"], r["url"],
            r["proveedor"], r["proveedor_cuit"], r["brecha_oferta_min_pct"], r["semaforo"], r["sobreprecio_ajustado_pct"]]
           for r in rows]
    header = ["Organismo", "Proceso", "Fecha adjudicación", "proceso_id", "renglon_id", "Objeto", "Renglón",
              "Descripción (SIByS)", "Cantidad", "Unidad", "Precio unitario ganador", "Total", "Marca", "Producto canónico",
              "N ofertas", "Oferta más baja", "Quién ofertó más bajo", "Link COMPR.AR", "Ganador", "CUIT ganador",
              "Ganador vs más baja %", "Semáforo mercado", "Brecha mercado %"]
    return _write("validation_comprar", header, out)


def entidades(conn: sqlite3.Connection) -> Path:
    """Cada alias crudo -> clave canónica (empresa / persona / organismo) para revisar las uniones."""
    try:
        rows = conn.execute(
            "SELECT tipo, clave, alias_raw, alias_norm, fuente, veces, "
            "(SELECT COUNT(DISTINCT a2.alias_raw) FROM entidades_alias a2 WHERE a2.tipo=a.tipo AND a2.clave=a.clave) n_alias "
            "FROM entidades_alias a ORDER BY tipo, n_alias DESC, clave").fetchall()
    except sqlite3.OperationalError:
        rows = []
    out = [[r["tipo"], r["alias_norm"], r["fuente"], r["clave"], r["n_alias"], r["alias_raw"], r["veces"],
            "unión por CUIT válido" if r["clave"].startswith("CUIT:") else
            "unión por DNI" if r["clave"].startswith("DNI:") else "candidato (sin CUIT/DNI): NO se une",
            "¿Es la misma entidad que los otros alias de esta clave?"] for r in rows]
    header = ["Tipo", "Nombre normalizado", "Fuente", "clave", "Alias en la clave", "Texto original", "Veces",
              "Cómo se unió", "Qué mirar"]
    return _write("validation_entidades", header, out)


def organismos(conn: sqlite3.Connection) -> Path:
    """Universo de `procesos.organismo` crudo -> `organismo_canonico` (MEJORAS #53): una fila por
    texto crudo distinto, con su código SAF, cuántos procesos tiene, el canónico que calculó
    `normalize.aplicar.run()` y, aparte, la jurisdicción madre (ministerio/organismo padre) a la
    que pertenecería aunque `config/settings.yml -> organismos.agrupar_subunidades` esté en false
    (para que el owner vea el roll-up posible aunque hoy no se esté aplicando). Ordenado por
    cuántas variantes crudas comparten el mismo canónico: lo que más junta es lo más dudoso de
    revisar primero (si juntó de más, el error pesa más variantes a la vez)."""
    from .normalize.aplicar import titulo
    from .normalize.entidades import normalizar_organismo
    rows = conn.execute(
        "SELECT organismo, saf, organismo_canonico, COUNT(*) n FROM procesos WHERE organismo IS NOT NULL "
        "AND organismo <> '' GROUP BY organismo ORDER BY organismo").fetchall()
    n_variantes: dict[str, int] = {}
    for r in rows:
        clave = r["organismo_canonico"] or ""
        n_variantes[clave] = n_variantes.get(clave, 0) + 1
    out = []
    for r in rows:
        o = normalizar_organismo(r["organismo"])
        jurisdiccion_madre = titulo(o["jurisdiccion"]) if o["jurisdiccion"] else (r["organismo_canonico"] or "")
        if r["saf"]:
            motivo = f"código SAF {r['saf']} + mismo nombre normalizado (mayúsculas/acentos/espacios)"
        elif o["unidad"]:
            motivo = f"subunidad: se agrupa en la jurisdicción madre '{jurisdiccion_madre}'"
        else:
            motivo = "mismo nombre normalizado (sin mayúsculas/acentos/espacios)"
        out.append([r["organismo"], r["saf"] or "", r["n"], r["organismo_canonico"] or "", jurisdiccion_madre,
                    motivo])
    out.sort(key=lambda row: (-n_variantes.get(row[3], 1), row[3], row[0]))
    header = ["organismo_crudo", "codigo_saf", "n_procesos", "canonico", "jurisdiccion_agrupada", "motivo"]
    return _write("validation_organismos", header, out)


def moneda(conn: sqlite3.Connection, desde: str = "2019-01") -> Path:
    """IPC y dólar mes a mes: para chequear contra valores conocidos las conversiones a pesos reales y dólares."""
    from .normalize import moneda as moneda_mod
    m = moneda_mod.cargar(conn)
    alimentos = {r[0]: r[1] for r in conn.execute("SELECT fecha, valor FROM indices WHERE serie='ipc_alimentos'")}
    series = {casa: moneda_mod.cargar(conn, f"usd_{casa}") for casa in ("contadoconliqui", "oficial", "mayorista", "blue")}
    meses = sorted({f[:7] for f in m.usd} | {f[:7] for f in m.ipc})
    out = []
    for mes in (x for x in meses if x >= desde):
        factor, aprox = m.factor_real(mes + "-01")
        tasas = {c: s.tasa_usd(mes) for c, s in series.items()}
        ccl, ofi = tasas["contadoconliqui"], tasas["oficial"]
        out.append([mes, m.ipc.get(mes + "-01"), alimentos.get(mes + "-01"),
                    round(factor, 4) if factor else None, "sí" if aprox else "",
                    round(1000 * factor) if factor else None,
                    *(round(tasas[c], 2) if tasas[c] else None for c in ("contadoconliqui", "oficial", "mayorista", "blue")),
                    round((ccl / ofi - 1) * 100, 1) if ccl and ofi else None,
                    round(1000 / ccl, 2) if ccl else None])
    header = ["Mes", "IPC general (dic-2016=100)", "IPC alimentos", f"Factor a {m.etiqueta('real')}",
              "IPC aproximado (mes sin publicar)", "$1.000 de ese mes en pesos de hoy", "Dólar CCL (prom. mes)",
              "Dólar oficial (prom. mes)", "Dólar mayorista (prom. mes)", "Dólar blue (prom. mes)",
              "Brecha CCL vs oficial %", "$1.000 en US$ (CCL)"]
    return _write("validation_moneda", header, out)


def precios_programa(conn: sqlite3.Connection) -> Path:
    """Precios Cuidados/Justos/Máximos (sources/precios_programa.py): una fila por producto de
    la fuente de prensa (no una por fecha repetida), con lo que ya se sabe (juez LLM, si corrió)
    para que el humano decida más rápido. Alternativa a revisar en el webui /precios-programa."""
    rows = conn.execute(
        "SELECT pm.fuente, pm.obs_key, pm.descripcion, pm.marca, pm.contenido_base, pm.unidad_base, "
        "pm.precio, pm.precio_unidad_base, pm.producto_id, pr.nombre producto_nombre, pm.url "
        "FROM precios_mercado pm LEFT JOIN productos pr ON pr.id = pm.producto_id "
        "WHERE pm.canal = 'programa' GROUP BY pm.fuente, pm.obs_key "
        "ORDER BY pm.fuente, pm.producto_id IS NULL, pr.nombre, pm.descripcion").fetchall()
    out = []
    for r in rows:
        partes = r["fuente"].split(":", 2)
        programa, edicion_id = (partes[1], partes[2]) if len(partes) > 2 else ("", r["fuente"])
        desc = (r["descripcion"] or "").split(" [vigente")[0]
        vigencia = (r["descripcion"] or "")
        vigencia = vigencia[vigencia.find("[vigente") + 1:-1] if "[vigente" in vigencia else ""
        presentacion = f"{r['contenido_base']:g} {r['unidad_base']}" if r["contenido_base"] else ""
        juez = conn.execute(
            "SELECT veredicto, nota FROM validaciones_programa WHERE fuente=? AND obs_key=? AND autor='juez_llm'",
            (r["fuente"], r["obs_key"])).fetchone()
        humano = conn.execute(
            "SELECT veredicto, nota FROM validaciones_programa WHERE fuente=? AND obs_key=? AND autor='humano'",
            (r["fuente"], r["obs_key"])).fetchone()
        out.append([programa, edicion_id, vigencia, r["fuente"], r["obs_key"], desc, r["marca"], presentacion,
                    r["precio"], r["precio_unidad_base"], r["producto_nombre"] or "(sin producto del catálogo)",
                    r["url"], juez["veredicto"] if juez else "", juez["nota"] if juez else "",
                    humano["veredicto"] if humano else ""])
    header = ["Programa", "Edición", "Vigencia", "fuente", "obs_key", "Texto original", "Marca", "Presentación",
              "Precio", "Precio por u. base", "Producto asignado", "URL de la fuente",
              "Veredicto del juez (automático)", "Nota del juez", "Tu veredicto actual (webui)"]
    return _write("validation_precios_programa", header, out)


def run(conn: sqlite3.Connection) -> list[Path]:
    return [extraccion_vs_quijote(conn), comparaciones(conn), precios_hoy(conn), comprar(conn), entidades(conn),
            organismos(conn), precios_programa(conn),
            moneda(conn)]
