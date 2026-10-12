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
from pathlib import Path

from . import config, db, llm
from .web import queries

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
        "k.sobreprecio_ajustado_pct, k.exceso_ars, k.semaforo, k.confianza, k.motivo "
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
    no_publicables = sum(1 for r in rows if r.get("estado_revision") != "publicable")
    borrador = (f'<div style="border:3px solid #b42318;color:#b42318;padding:10px 14px;font-family:Arial;font-weight:bold">'
                f'BORRADOR INTERNO — {no_publicables} de {len(rows)} renglones son de licitaciones que no están marcadas '
                f'"publicable". No compartir fuera del equipo.</div>') if no_publicables else ""
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
    no_pub = sum(1 for r in rows if r.get("estado_revision") != "publicable")
    if no_pub:
        meta.append(["BORRADOR INTERNO", f"{no_pub} renglones de licitaciones no marcadas publicable. No compartir."])
    wb.save(path)


def crear(titulo: str, filtros: dict, con_resumen: bool = False) -> str:
    cfg = config.settings().get("comparacion") or {}
    with db.session() as conn:
        rows = filas(conn, filtros)
        kpis = queries.kpis(conn, filtros)
        cur = conn.execute("INSERT INTO reportes (titulo, filtros_json, created_at) VALUES (?, ?, ?)",
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


def archivo(row: sqlite3.Row, formato: str) -> Path | None:
    """Ruta del archivo del reporte; solo dentro de data/reportes (nada de rutas arbitrarias)."""
    base = row["html_path"]
    if not base:
        return None
    path = Path(base).with_suffix({"html": ".html", "xlsx": ".xlsx", "csv": ".csv"}.get(formato, ".none"))
    root = reportes_dir().resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    return path
