"""Reportes para periodistas: HTML imprimible + Excel + CSV, con metodología y advertencias.

Un reporte es una foto: se guarda en data/reportes/ con los datos del momento
en que se armó (si después se re-procesa la base, el reporte viejo no cambia).
El nombre de archivo lleva id + slug del título, así dos reportes nunca se pisan.

El resumen con LLM es opcional y usa un prompt que prohíbe acusaciones y obliga
a citar la referencia y las limitaciones: lo que sale de acá puede terminar
publicado, y un adjetivo de más es un problema legal para quien lo firma.
"""
from __future__ import annotations

import csv
import html
import json
import re
import sqlite3
import zipfile
from pathlib import Path

from . import config, db, llm, revision_humana
from .web import format as fmt, queries

COLUMNAS = [
    ("fecha", "Fecha"), ("organismo", "Organismo"), ("numero", "Proceso"), ("norma", "Norma"),
    ("proveedor", "Proveedor"), ("proveedor_cuit", "CUIT"), ("descripcion", "Descripción"),
    ("marca", "Marca"), ("cantidad", "Cantidad"), ("unidad_compra", "Unidad"),
    ("precio_unitario", "Precio unitario $"), ("precio_unidad_base", "Precio por kg/l/u $"),
    ("unidad_base", "Unidad base"), ("producto", "Producto canónico"),
    ("ref_principal", "Referencia"), ("ref_principal_valor", "Valor referencia $"),
    ("ref_min_mayorista", "Mín. mayorista $"), ("ref_p25_mayorista", "P25 mayorista $"),
    ("ref_mediana_mayorista", "Mediana mayorista $"), ("ref_mediana_minorista", "Mediana supermercado $"),
    ("ref_indec", "INDEC $"), ("ajuste_financiero_pct", "Ajuste financiero %"),
    ("sobreprecio_ajustado_pct", "Sobreprecio ajustado %"), ("exceso_ars", "Exceso estimado $"),
    ("semaforo", "Semáforo"), ("confianza", "Confianza"), ("motivo", "Motivo"), ("alertas", "Alertas"),
    ("url", "Fuente"), ("estado_revision", "Revisión"),
]


def _slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:50] or "reporte"


def reportes_dir() -> Path:
    # Junto a la base (respeta LIC_DB_PATH: los tests no escriben en data/ del repo).
    path = config.db_path().parent / "reportes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def filas(conn: sqlite3.Connection, filtros: dict) -> list[dict]:
    where, params = queries._where(filtros)   # mismos filtros que el webui
    rows = conn.execute(
        "SELECT COALESCE(p.fecha_adjudicacion, p.fecha_publicacion) fecha, COALESCE(p.organismo_canonico, p.organismo) organismo, "
        "p.numero, p.url, p.estado_revision, "
        "d.titulo norma, r.proveedor, r.proveedor_cuit, r.descripcion, r.marca, r.cantidad, r.unidad_compra, "
        "r.precio_unitario, r.precio_unidad_base, r.unidad_base, pr.nombre producto, r.alertas, "
        "k.ref_principal, k.ref_principal_valor, k.ref_min_mayorista, k.ref_p25_mayorista, "
        "k.ref_mediana_mayorista, k.ref_mediana_minorista, k.ref_indec, k.ajuste_financiero_pct, "
        "k.sobreprecio_ajustado_pct, k.exceso_ars, k.semaforo, k.confianza, k.motivo, k.motivo_codigo "
        "FROM procesos p JOIN renglones r ON r.proceso_id = p.id "
        "LEFT JOIN comparaciones k ON k.renglon_id = r.id LEFT JOIN documentos d ON d.id = p.documento_id "
        "LEFT JOIN productos pr ON pr.id = r.producto_id "
        f"WHERE {where} ORDER BY COALESCE(k.exceso_ars, 0) DESC, k.sobreprecio_ajustado_pct DESC", params).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        alertas = json.loads(item["alertas"]) if item.get("alertas") else []
        item["alertas"] = "; ".join(alertas)
        out.append(item)
    return out


def _fmt(value, col: str) -> str:
    if value is None or value == "":
        return "—"
    if col.endswith("_pct"):
        return f"{float(value):+.1f}%".replace(".", ",")
    if isinstance(value, float):
        text = f"{value:,.2f}"
        return text.replace(",", "X").replace(".", ",").replace("X", ".")
    return str(value)


def advertencias(rows: list[dict]) -> list[str]:
    """Avisos en castellano para imprimir en un reporte o expediente (MEJORAS #49.4, pedido de
    jdemonasterio: "no bloquear exportar; dejar avisos claros"). Nunca impide exportar — un
    reporte sigue saliendo con todo, pero con estos avisos bien visibles arriba."""
    if not rows:
        return []
    grises = sum(1 for r in rows if (r.get("semaforo") or "gris") == "gris")
    equivalencia = sum(1 for r in rows if r.get("motivo_codigo") == "equivalencia_por_verificar")
    inferidos = sum(1 for r in rows if r.get("motivo") and "PRECIO INFERIDO" in r["motivo"])
    de_programa = sum(1 for r in rows if r.get("motivo") and "precio de programa" in r["motivo"])
    no_publicable = sum(1 for r in rows if r.get("estado_revision") != "publicable")
    out = []
    if no_publicable:
        out.append(f'{no_publicable} de {len(rows)} renglones son de licitaciones que todavía no están '
                   'marcadas "publicable": revisadas o no, falta ese último paso.')
    if grises:
        out.append(f"{grises} renglón(es) sin comparación (gris): no hay evidencia de sobreprecio ni de lo contrario.")
    if equivalencia:
        out.append(f"{equivalencia} renglón(es) con una brecha alta pero la equivalencia de producto todavía sin confirmar.")
    if inferidos:
        out.append(f"{inferidos} renglón(es) con un precio INFERIDO hacia atrás por IPC (sin dato real de mercado en esa fecha).")
    if de_programa:
        out.append(f"{de_programa} renglón(es) comparados contra un precio de programa (Precios Cuidados/Justos/Máximos), no contra el mercado libre.")
    return out


def _resumen_llm(titulo: str, filtros: dict, kpis: dict, rows: list[dict]) -> str | None:
    template = (config.PROMPTS_DIR / "resumen_reporte.md").read_text(encoding="utf-8")
    casos = [r for r in rows if r.get("semaforo") in {"rojo", "amarillo"}][:10]
    casos_txt = "\n".join(
        f"- {r['fecha']} {r['organismo']} · {r['descripcion']} · proveedor {r['proveedor']} · "
        f"pagó {r['precio_unidad_base']:.2f}/{r['unidad_base']} vs {r['ref_principal']} {r['ref_principal_valor']:.2f} "
        f"({r['sobreprecio_ajustado_pct']:+.1f}% ajustado) · exceso ${r['exceso_ars']:,.0f}"
        for r in casos if r.get("precio_unidad_base") and r.get("ref_principal_valor")) or "(ninguno)"
    prompt = (template.replace("<<TITULO>>", titulo)
              .replace("<<FILTROS>>", json.dumps({k: v for k, v in filtros.items() if v}, ensure_ascii=False))
              .replace("<<KPIS>>", json.dumps(kpis, ensure_ascii=False, default=str))
              .replace("<<CASOS>>", casos_txt))
    schema = {"type": "object", "additionalProperties": False, "required": ["resumen"],
              "properties": {"resumen": {"type": "string"}}}
    try:
        return llm.complete_json(prompt, schema, role="cheap", proposito="resumen_reporte")["resumen"]
    except llm.LLMUnavailable:
        return None


def _html(titulo: str, filtros: dict, kpis: dict, rows: list[dict], resumen: str | None, cfg: dict) -> str:
    esc = html.escape
    filtros_txt = ", ".join(f"{k}: {v}" for k, v in filtros.items() if v) or "sin filtros"
    principales = [r for r in rows if r.get("semaforo") in {"rojo", "amarillo"}]
    cols = [("fecha", "Fecha"), ("organismo", "Organismo"), ("proveedor", "Proveedor"),
            ("descripcion", "Bien"), ("precio_unidad_base", "Pagó $/u. base"), ("ref_principal_valor", "Referencia $"),
            ("ref_principal", "Tipo de referencia"), ("sobreprecio_ajustado_pct", "Brecha ajustada"),
            ("exceso_ars", "Exceso estimado $"), ("confianza", "Confianza")]
    def table(items):
        head = "".join(f"<th>{esc(t)}</th>" for _, t in cols)
        body = "".join(
            "<tr class='" + esc(r.get("semaforo") or "") + "'>" +
            "".join(f"<td>{esc(_fmt(r.get(c), c))}</td>" for c, _ in cols) +
            (f"<td><a href='{esc(r['url'])}'>BO</a></td>" if r.get("url") else "<td></td>") + "</tr>"
            for r in items)
        return f"<table><thead><tr>{head}<th>Fuente</th></tr></thead><tbody>{body}</tbody></table>"
    resumen_html = "".join(f"<p>{esc(p)}</p>" for p in (resumen or "").split("\n") if p.strip())
    avisos = advertencias(rows)
    borrador = ('<div style="border:3px solid #b42318;color:#b42318;padding:10px 14px;font-family:Arial">'
                '<b>BORRADOR INTERNO — avisos antes de publicar</b><ul style="margin:6px 0 0 18px">'
                + "".join(f"<li>{esc(a)}</li>" for a in avisos) + "</ul></div>") if avisos else ""
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><title>{esc(titulo)}</title>
<style>
body{{font-family:Georgia,'Times New Roman',serif;max-width:1100px;margin:32px auto;padding:0 16px;color:#1b1b1b;line-height:1.5}}
h1{{font-size:26px;margin-bottom:4px}} h2{{font-size:18px;margin-top:28px;border-bottom:1px solid #ddd;padding-bottom:4px}}
.meta{{color:#555;font-size:13px}} .kpis{{display:flex;gap:24px;flex-wrap:wrap;margin:16px 0}}
.kpi b{{display:block;font-size:22px}} table{{border-collapse:collapse;width:100%;font-size:12px;font-family:Arial,sans-serif}}
th,td{{border:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}} th{{background:#f4f4f2}}
tr.rojo td:nth-child(8){{color:#b42318;font-weight:bold}} tr.amarillo td:nth-child(8){{color:#9a6700;font-weight:bold}}
.nota{{font-size:12px;color:#444;background:#f7f7f5;padding:10px 14px;border-left:3px solid #999}}
@media print{{a{{color:inherit}} body{{margin:0}}}}
</style></head><body>
{borrador}
<h1>{esc(titulo)}</h1>
<div class="meta">Generado el {db.now_iso()[:10]} · Filtros: {esc(filtros_txt)} · Período de los datos: {esc(str(kpis.get('desde') or '—'))} a {esc(str(kpis.get('hasta') or '—'))}</div>
<div class="kpis">
<div class="kpi">Procesos<b>{kpis.get('procesos') or 0}</b></div>
<div class="kpi">Renglones comparados<b>{kpis.get('comparados') or 0}</b></div>
<div class="kpi">Con brecha alta / moderada<b>{kpis.get('rojos') or 0} / {kpis.get('amarillos') or 0}</b></div>
<div class="kpi">Exceso estimado<b>{esc(_fmt(float(kpis.get('exceso') or 0), 'x'))}</b></div>
</div>
{('<h2>Resumen</h2>' + resumen_html + '<p class="nota">Resumen redactado automáticamente a partir de la tabla; verificar antes de publicar.</p>') if resumen_html else ''}
<h2>Casos con brecha alta o moderada ({len(principales)})</h2>
{table(principales[:200]) if principales else '<p>No hay casos con brecha alta o moderada con estos filtros.</p>'}
<h2>Cómo se calculó</h2>
<ul>
<li>Fuente de las compras: normas publicadas en el Boletín Oficial (resoluciones que adjudican), con proveedor, CUIT, cantidad y precio. Cada fila enlaza al aviso original.</li>
<li>Cada bien se lleva a precio por unidad base (kg, litro o unidad) y se compara solo contra el mismo producto ("peras con peras"), en envases de tamaño similar (hasta {cfg.get('banda_tamano', 2.5)}x).</li>
<li>Referencia: si la norma nombra la marca y esa marca está en el mercado, se compara contra la misma marca; si no, contra el percentil 25 de los precios mayoristas del mismo producto (una "segunda marca típica"), relevados a ±{cfg.get('ventana_directa_dias', 15)} días de la compra. Si no hay precios cercanos, se usa el más próximo (hasta {cfg.get('gap_maximo_dias', 120)} días) actualizado por IPC.</li>
<li>Se descuenta un costo financiero: inflación mensual × {cfg.get('plazo_pago_meses', 1)} mes(es) de plazo de pago del Estado.</li>
<li>Semáforo: brecha ajustada ≥ {cfg.get('umbral_rojo_pct', 20)}% con confianza media o alta = alta; ≥ {cfg.get('umbral_amarillo_pct', 8)}% = moderada. Diferencias mayores a {cfg.get('plausibilidad_max_ratio', 4)}x se marcan para revisar (casi siempre son errores de unidad) y no cuentan como sobreprecio.</li>
</ul>
<p class="nota"><b>Limitaciones.</b> Un color (sobre todo rojo) es una investigación pendiente de revisión humana, no una
conclusión publicada: una brecha no prueba irregularidad. Pueden explicarla costos de logística y distribución, plazos de pago más largos, presentaciones o especificaciones distintas (por ejemplo, leche fortificada) o errores en la publicación original. Cada caso debe verificarse con el expediente y, si corresponde, pedir explicaciones al organismo. Los precios de mercado provienen de relevamientos públicos de mayoristas, supermercados (SEPA / Precios Claros) e INDEC.</p>
<h2>Todos los renglones ({len(rows)})</h2>
{table(rows[:1000])}
</body></html>"""


def _xlsx(path: Path, rows: list[dict], titulo: str, filtros: dict) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Renglones"
    ws.append([t for _, t in COLUMNAS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([r.get(c) for c, _ in COLUMNAS])
    ws.freeze_panes = "A2"
    meta = wb.create_sheet("Notas")
    meta.append(["Título", titulo])
    meta.append(["Generado", db.now_iso()])
    meta.append(["Filtros", json.dumps({k: v for k, v in filtros.items() if v}, ensure_ascii=False)])
    meta.append(["Advertencia", "Una brecha de precio no prueba irregularidad; verificar con el expediente."])
    for aviso in advertencias(rows):
        meta.append(["Aviso antes de publicar", aviso])
    wb.save(path)


def crear(titulo: str, filtros: dict, con_resumen: bool = False) -> str:
    cfg = config.settings().get("comparacion") or {}
    with db.session() as conn:
        rows = filas(conn, filtros)
        kpis = queries.kpis(conn, filtros)
        cur = conn.execute("INSERT INTO reportes (titulo, filtros_json, created_at, tipo) VALUES (?, ?, ?, 'reporte')",
                           (titulo, json.dumps(filtros, ensure_ascii=False), db.now_iso()))
        rid = cur.lastrowid
        conn.commit()
        resumen = _resumen_llm(titulo, filtros, kpis, rows) if con_resumen else None
        base = reportes_dir() / f"{rid:04d}_{_slug(titulo)}"
        html_path, xlsx_path, csv_path = base.with_suffix(".html"), base.with_suffix(".xlsx"), base.with_suffix(".csv")
        html_path.write_text(_html(titulo, filtros, kpis, rows, resumen, cfg), encoding="utf-8")
        _xlsx(xlsx_path, rows, titulo, filtros)
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as handle:   # BOM: Excel abre bien los acentos
            writer = csv.writer(handle)
            writer.writerow([t for _, t in COLUMNAS])
            for r in rows:
                writer.writerow([r.get(c) for c, _ in COLUMNAS])
        conn.execute("UPDATE reportes SET resumen=?, html_path=?, xlsx_path=? WHERE id=?",
                     (resumen, str(html_path), str(xlsx_path), rid))
    return f"Reporte '{titulo}' listo: {len(rows)} renglones" + (" con resumen" if resumen else "")


def _mes_ipc_html(esc, fecha_obs: str | None, fecha_compra: str | None, factor: float | None) -> str:
    """"abr-2024 → jun-2024 (x1.030)": a qué mes se llevó cada precio de mercado y con qué
    factor (MEJORAS #49, pedido de jdemonasterio: "las fechas de cada observación y el mes de
    IPC usado, bien explícitos, para que se vea todo alineado al mismo mes calendario")."""
    if not fecha_obs:
        return "—"
    if factor:
        destino = esc(fecha_compra[:7]) if fecha_compra else "?"
        return f"{esc(fecha_obs[:7])} → {destino} (x{factor:.3f})"
    if fecha_compra and fecha_obs[:7] == fecha_compra[:7]:
        return "mismo mes"
    return "sin ajuste"


def _expediente_html(titulo: str, proc: dict, resumen: dict, comprobantes: list[dict]) -> str:
    """HTML autocontenido del expediente para periodista (MEJORAS #49.5, redefinido a pedido de
    jdemonasterio tras la revisión de journeys: "el que lo revisa y el que lo analiza es la
    misma persona; lo que llega al periodista es UN html con primero la licitación y los
    resultados, y abajo cómo se construyeron").

    Dos partes, en ese orden:
      1. Qué se compró y qué se encontró: datos de la licitación (con las 3 fechas explícitas),
         montos y una tabla de resultados por renglón (precio pagado, referencia, brecha, semáforo).
      2. Cómo se construyó esta comparación, por renglón: fuentes con sus URLs (el aviso/proceso
         original y las observaciones de mercado), las decisiones de normalización (variedad,
         equivalencias, correcciones humanas), el manejo de inflación/IPC (mes a mes, o el aviso de
         "precio inferido" cuando no había dato real en la fecha), el supuesto de precio de programa
         cuando aplica, y las preguntas abiertas (explicaciones alternativas sin verificar).

    Nunca repite el comprobante completo de cada renglón (eso vive aparte, en
    `/renglon/<id>/comprobante`, pensado para un caso puntual): este documento es el que se manda
    o se imprime entero, así que prioriza que se lea de arriba a abajo sin tener que volver a abrir
    la app."""
    esc = html.escape

    fechas_html = (f"Apertura de ofertas: <b>{esc(proc.get('fecha_apertura') or 'sin dato')}</b>"
                   f"{' (fecha de comparación)' if proc.get('fecha_apertura') else ''} · "
                   f"Adjudicación: {esc(proc.get('fecha_adjudicacion') or 'sin dato')} · "
                   f"Publicación: {esc(proc.get('fecha_publicacion') or 'sin dato')}")

    resumen_html = f"""
    <div class="kpis">
      <div class="kpi">Renglones<b>{resumen['total']}</b><span>{resumen['comparados']} comparados</span></div>
      <div class="kpi">Rojo / amarillo / verde / gris<b>{resumen['rojos']} / {resumen['amarillos']} / {resumen['verdes']} / {resumen['grises']}</b></div>
      <div class="kpi">Monto total del proceso<b>{esc(fmt.ars(resumen['monto_total_proceso']))}</b></div>
      <div class="kpi">Monto de los renglones analizados<b>{esc(fmt.ars(resumen['monto_renglones']))}</b></div>
      <div class="kpi">Exceso estimado<b>{esc(fmt.ars(resumen['exceso']))}</b></div>
      <div class="kpi">Cobertura<b>{resumen['cobertura_pct'] if resumen['cobertura_pct'] is not None else '—'}%</b></div>
    </div>"""

    cols_res = [("nro_renglon", "Renglón"), ("descripcion", "Bien"), ("proveedor", "Proveedor"),
                ("precio_unidad_base", "Pagó $/u. base"), ("ref_principal_valor", "Referencia $"),
                ("sobreprecio_ajustado_pct", "Brecha"), ("exceso_ars", "Exceso $")]
    filas_res = []
    for c in comprobantes:
        semaforo = c.get("semaforo") or "gris"
        filas_res.append(f"""<tr class="{esc(semaforo)}">
            <td>{c.get('nro_renglon') or c.get('id')}</td>
            <td>{esc(c.get('descripcion') or '')}{f" <span class='muted'>({esc(c['marca'])})</span>" if c.get('marca') else ''}</td>
            <td>{esc(c.get('proveedor') or '—')}</td>
            <td class="num">{esc(fmt.ars(c.get('precio_unidad_base'), 2))}/{esc(c.get('unidad_base') or '')}</td>
            <td class="num">{esc(fmt.ars(c.get('ref_principal_valor'), 2))} <span class="muted">({esc(fmt.REF_TXT.get(c.get('ref_principal'), c.get('ref_principal') or '—'))})</span></td>
            <td class="num"><span class="sem {esc(semaforo)}">{esc(fmt.SEMAFORO_TXT.get(semaforo, semaforo))}</span><br>{esc(fmt.pct(c.get('sobreprecio_ajustado_pct')))}</td>
            <td class="num">{esc(fmt.ars(c.get('exceso_ars')))}</td>
        </tr>
        <tr class="porque"><td></td><td colspan="6">{esc(fmt.explicar_semaforo(c))}</td></tr>""")
    tabla_resultados = (f"""<table><thead><tr>{''.join(f'<th>{esc(t)}</th>' for _, t in cols_res)}</tr></thead>
    <tbody>{''.join(filas_res)}</tbody></table>""" if filas_res else "<p>No se eligió ningún renglón.</p>")

    construccion = []
    for c in comprobantes:
        fuente_doc = c.get("proceso_url") or c.get("doc_url")
        fuentes_html = "".join(
            f"<li>{esc(o.get('fuente',''))} ({esc(o.get('canal',''))}) · {esc(o.get('fecha',''))}"
            + (f" · <a href='{esc(o['url'])}'>{esc(o.get('descripcion') or 'fuente')} ↗</a>" if o.get("url")
               else f" · {esc(o.get('descripcion') or '')}")
            + f" · {_mes_ipc_html(esc, o.get('fecha'), c.get('fecha_compra'), o.get('factor_ipc'))}</li>"
            for o in (c.get("evidencia") or [])[:12])
        par_html = "".join(
            f"<li>{esc(o.get('fuente',''))} ({esc(o.get('familia',''))}) · {esc(o.get('fecha',''))} · "
            f"{esc(fmt.ars(o.get('precio_ub'), 2))}/{esc(c.get('unidad_base') or 'u.')}</li>"
            for o in c.get("par_corroborado") or [])
        normalizacion = [
            f"Producto asignado: {esc(c.get('producto_nombre') or 'sin producto comparable')}"
            + (f" — {esc(c['match_razon'])}" if c.get("match_razon") else ""),
            f"Variedad: {esc(fmt.ATRIBUTO_TXT.get(c.get('estado_atributo'), c.get('estado_atributo') or 'sin variantes para este producto'))}",
            f"Envase: {esc(fmt.num(c.get('contenido_base'), 3))} {esc(c.get('unidad_base') or '')} "
            f"(precio unitario {esc(fmt.ars(c.get('precio_unitario'), 2))} ÷ contenido = {esc(fmt.ars(c.get('precio_unidad_base'), 2))}/{esc(c.get('unidad_base') or '')})",
        ]
        correcciones_html = "".join(
            f"<li>{esc(campo)}: {esc(str(corr.get('valor')))}"
            + (f" ({esc(corr['nota'])})" if corr.get("nota") else "") + f" — {esc(corr.get('autor') or 'sin autor')}</li>"
            for campo, corr in (c.get("correcciones") or {}).items())
        ipc_notas = []
        if c.get("motivo") and "PRECIO INFERIDO" in c["motivo"]:
            ipc_notas.append("PRECIO INFERIDO: no había dato de mercado real en la fecha de la compra; se usó el "
                            "primer dato posterior disponible, llevado hacia atrás con el IPC (confianza baja, nunca rojo).")
        if c.get("motivo") and "precio de programa" in c["motivo"]:
            ipc_notas.append("Referencia de precio de programa (Precios Cuidados/Justos/Máximos): se usó como "
                            "piso de mercado porque no había ningún precio mayorista/minorista real en la fecha; "
                            "en el mercado libre suelen cobrarse precios MÁS altos que el programa, no más bajos.")
        ipc_notas.append(f"Ajuste financiero por el plazo de pago del Estado: {esc(fmt.num(c.get('ajuste_financiero_pct'), 1))}%.")
        explic_pendientes = [revision_humana.EXPLICACION_TXT[item] for item in revision_humana.EXPLICACIONES_ITEMS
                            if (c.get("explicaciones") or {}).get(item, {}).get("estado", "pendiente") == "pendiente"]
        preguntas = list(explic_pendientes)
        if c.get("semaforo") == "gris":
            preguntas.append(fmt.MOTIVO_CODIGO_TXT.get(c.get("motivo_codigo"), c.get("motivo") or "sin calcular"))

        construccion.append(f"""
        <div class="comprobante">
        <h3>Renglón {c.get('nro_renglon') or c.get('id')} — {esc(c.get('descripcion') or '')}</h3>
        <p><b>Fuentes</b> (dato original{f": <a href='{esc(fuente_doc)}'>abrir ↗</a>" if fuente_doc else " sin link"}):</p>
        <ul>{fuentes_html or '<li>sin observaciones de mercado para este renglón</li>'}</ul>
        {f'<p><b>Par que confirma el mínimo corroborado:</b></p><ul>{par_html}</ul>' if par_html else ''}
        <p><b>Consideraciones de normalización</b></p>
        <ul>{''.join(f'<li>{n}</li>' for n in normalizacion)}</ul>
        {f'<p><b>Correcciones humanas aplicadas</b></p><ul>{correcciones_html}</ul>' if correcciones_html else ''}
        <p><b>Inflación / IPC</b></p>
        <ul>{''.join(f'<li>{n}</li>' for n in ipc_notas)}</ul>
        {f'<p><b>Preguntas abiertas</b></p><ul>{"".join(f"<li>{esc(p)}</li>" for p in preguntas)}</ul>' if preguntas else ''}
        </div>""")

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8"><title>{esc(titulo)}</title>
<style>
body{{font-family:Georgia,'Times New Roman',serif;max-width:1050px;margin:28px auto;padding:0 16px;color:#1b1b1b;line-height:1.5}}
h1{{font-size:24px}} h2{{font-size:17px;border-bottom:1px solid #ddd;padding-bottom:4px;margin-top:26px}}
h3{{font-size:15px}} .kpis{{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0}}
.kpi{{background:#f7f7f5;border:1px solid #ddd;border-radius:6px;padding:8px 12px;font-size:12px}}
.kpi b{{display:block;font-size:18px}} .kpi span{{color:#777}}
table{{border-collapse:collapse;width:100%;font-size:13px}} th,td{{border:1px solid #ddd;padding:5px 7px;text-align:left;vertical-align:top}}
th{{background:#f4f4f2}} td.num,th.num{{text-align:right}} tr.porque td{{border-top:none;color:#555;font-size:12px;font-style:italic}}
.comprobante{{border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:14px 0;background:#fdfdfc}}
.sem{{padding:1px 8px;border-radius:999px;font-size:12px;font-weight:bold}}
.sem.rojo{{background:#fbe7e7;color:#b42318}} .sem.amarillo{{background:#fdf3dc;color:#9a6700}}
.sem.verde{{background:#e6f5e6;color:#0a7a0a}} .sem.gris{{background:#efeeeb;color:#555}}
.muted{{color:#777}} .nota{{font-size:12px;color:#444;background:#f7f7f5;padding:10px 14px;border-left:3px solid #999;margin-top:20px}}
@media print{{body{{margin:0}}}}
</style></head><body>
<h1>{esc(titulo)}</h1>
<p class="nota">Expediente armado el {db.now_iso()[:10]} para revisión periodística. {esc(fmt.LEGENDA_INVESTIGACION)}</p>

<h2>1. Qué se compró y qué se encontró</h2>
<p>{esc(proc.get('organismo') or 'Organismo sin identificar')} — {esc(proc.get('numero') or '')}
{f"· {esc(proc.get('objeto'))}" if proc.get('objeto') else ''}</p>
<p>{fechas_html}</p>
{resumen_html}
<p><b>Qué falta para publicar / preguntas abiertas de todo el proceso:</b></p>
<ul>{''.join(f'<li>{esc(b)}</li>' for b in resumen['bloqueantes']) or '<li>nada pendiente detectado automáticamente</li>'}</ul>
<h3>Resultados por renglón</h3>
{tabla_resultados}

<h2>2. Cómo se construyó esta comparación</h2>
<p class="nota">Fuentes de datos con sus links, las decisiones de normalización (variedad, equivalencias,
correcciones humanas), cómo se trató la inflación cuando no había precio de mercado en la fecha exacta, y las
preguntas que todavía quedan abiertas — renglón por renglón.</p>
{''.join(construccion) or '<p>No se eligió ningún renglón.</p>'}

<h2>Limitaciones</h2>
<p class="nota">Un color (sobre todo rojo) es una investigación pendiente de revisión humana, no una conclusión
publicada: una brecha no prueba irregularidad. Pueden explicarla costos de logística y distribución, impuestos,
especificaciones técnicas distintas, un plazo de pago real distinto del supuesto, o un error en la publicación
original. Los precios de mercado provienen de relevamientos públicos de mayoristas, supermercados (SEPA / Precios
Claros), INDEC y, cuando no hay otro dato, listas de precios de programa (Precios Cuidados/Justos/Máximos), que
funcionan como un piso, no como el mínimo real de góndola.</p>
</body></html>"""


def crear_expediente(proceso_id: int, titulo: str = "", renglon_ids: list[int] | None = None) -> str:
    """"Armar expediente para periodista" (MEJORAS #49.5), a pedido desde /licitacion/<id>: un
    export autocontenido (HTML + ZIP) con la síntesis del proceso y el comprobante de cálculo de
    los renglones elegidos (todos los comparables si no se elige ninguno). Reusa la tabla
    `reportes` (aparece en /reportes junto a los reportes normales, con `tipo='expediente'`)."""
    with db.session() as conn:
        proc = queries.proceso(conn, proceso_id)
        if not proc:
            raise ValueError(f"no existe la licitación {proceso_id}")
        renglones = queries.renglones(conn, proceso_id)
        disponibles = [r["id"] for r in renglones]
        elegidos = [i for i in (renglon_ids or []) if i in disponibles] or [
            r["id"] for r in renglones if r.get("semaforo") in ("rojo", "amarillo")] or disponibles
        validaciones = revision_humana.validaciones_renglones(conn, disponibles)
        resumen = queries.resumen_proceso(proc, renglones, validaciones)
        comprobantes = [c for c in (revision_humana.comprobante(conn, rid) for rid in elegidos) if c]

        titulo = titulo.strip() or (f"Expediente — {proc.get('organismo') or 'organismo sin identificar'} — "
                                    f"{proc.get('numero') or proc['id']}")
        cur = conn.execute(
            "INSERT INTO reportes (titulo, filtros_json, created_at, tipo, proceso_id) VALUES (?, ?, ?, 'expediente', ?)",
            (titulo, json.dumps({"proceso_id": proceso_id, "renglon_ids": elegidos}, ensure_ascii=False),
             db.now_iso(), proceso_id))
        rid = cur.lastrowid
        conn.commit()
        base = reportes_dir() / f"{rid:04d}_{_slug(titulo)}"
        html_path, zip_path = base.with_suffix(".html"), base.with_suffix(".zip")
        contenido = _expediente_html(titulo, proc, resumen, comprobantes)
        html_path.write_text(contenido, encoding="utf-8")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("expediente.html", contenido)
        conn.execute("UPDATE reportes SET html_path=? WHERE id=?", (str(html_path), rid))
    return f"Expediente '{titulo}' listo: {len(comprobantes)} renglón(es)"


def archivo(row: sqlite3.Row, formato: str) -> Path | None:
    """Ruta del archivo del reporte; solo dentro de data/reportes (nada de rutas arbitrarias)."""
    base = row["html_path"]
    if not base:
        return None
    path = Path(base).with_suffix({"html": ".html", "xlsx": ".xlsx", "csv": ".csv", "zip": ".zip"}.get(formato, ".none"))
    root = reportes_dir().resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    return path
