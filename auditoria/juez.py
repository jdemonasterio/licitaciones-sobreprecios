"""Juez LLM externo: muestra estratificada -> veredicto + regla sugerida -> TSV y resumen.

Por qué así (decisiones del LLM prime P11, 2026-10-11, pendientes de check):
- El juez NO es verdad: sus veredictos se revisan a mano (columnas "Tu veredicto"),
  y se mide el acuerdo juez-humano antes de fijar un modelo por defecto. Por eso el
  modelo se pasa explícito (--modelo) o por LIC_LLM_ROLE_JUEZ.
- Base en SOLO LECTURA (sqlite mode=ro). Nunca modifica reglas ni datos.
- Muestra estratificada por capa (extracción, comparación, macheo de mercado,
  uniones de entidades) incluyendo verdes y grises, no solo rojos.
- Tope de USD por corrida antes de cada llamada.

Uso:
  python -m auditoria.juez --modelo gpt-6-astra --n 60 --max-usd 5
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import random
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sobreprecios import config, llm  # noqa: E402

TIPOS_ERROR = ["ninguno", "parseo_cantidad", "parseo_precio", "unidad_o_tamano", "producto_distinto",
               "proveedor_o_cuit", "fecha", "referencia_inadecuada", "normalizacion_entidad", "dato_fuente_sucio",
               "otro"]
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["veredicto", "tipo_error", "explicacion", "valor_correcto", "regla_sugerida"],
    "properties": {
        "veredicto": {"type": "string", "enum": ["ok", "error", "dudoso"]},
        "tipo_error": {"type": "string", "enum": TIPOS_ERROR},
        "explicacion": {"type": "string"},
        "valor_correcto": {"type": ["string", "null"]},
        "regla_sugerida": {"type": ["string", "null"]},
    },
}
PROMPT = """Sos un auditor independiente de un sistema que detecta posibles sobreprecios en compras del Estado argentino.
Revisá UN ítem producido por el sistema y decidí si es correcto. Sé estricto con "peras con peras": mismo producto,
misma presentación, unidades bien leídas, montos que cierran. Si el dato de origen ya viene sucio, decilo
(tipo dato_fuente_sucio). Si es un error, proponé una REGLA general (no un parche para este caso) que lo evitaría.
Si te falta información para decidir, veredicto "dudoso".

Capa auditada: {capa}
Qué hace esta capa: {que_hace}

Ítem (JSON):
{item}
"""
CAPAS = {
    "extraccion": "Lee la norma del Boletín Oficial (texto) o COMPR.AR y extrae por renglón: proveedor, CUIT, cantidad, "
                  "unidad, precio unitario, monto. Verificá contra el fragmento de texto fuente.",
    "comparacion": "Lleva el precio pagado a $/kg, $/l o $/u y lo compara con la referencia de mercado (misma marca, o "
                   "mínimo de mercado del mismo producto y envase similar, a la fecha), descuenta inflación por plazo "
                   "de pago y asigna semáforo. Verificá que la referencia sea el mismo producto y la cuenta tenga sentido.",
    "mercado": "Asigna un producto canónico y un contenido (kg/l/u) a una publicación de supermercado/mayorista. "
               "Verificá que sea exactamente ese producto y que el tamaño esté bien leído.",
    "entidades": "Une variantes de nombre de una empresa bajo una misma clave (CUIT válido) o las deja como candidatas. "
                 "Verificá que los alias sean la misma entidad.",
}


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _snippet(texto: str, needle: str | None, width: int = 900) -> str:
    if not texto:
        return ""
    i = texto.find(needle) if needle else -1
    if i < 0:
        i = max(texto.find("RESUELVE"), 0)
    return texto[max(0, i - 300): i + width]


def muestra(conn: sqlite3.Connection, n: int, seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    cuota = {"extraccion": round(n * 0.3), "comparacion": round(n * 0.35), "mercado": round(n * 0.2)}
    cuota["entidades"] = max(n - sum(cuota.values()), 0)
    items: list[dict] = []

    ext = conn.execute(
        "SELECT r.id, r.proceso_id, r.nro_renglon, r.descripcion, r.proveedor, r.proveedor_cuit, r.cantidad, "
        "r.unidad_compra, r.precio_unitario, r.monto_total, r.extraccion_metodo, r.alertas, d.texto, p.url, "
        "r.fuente_fragmento "
        "FROM renglones r JOIN procesos p ON p.id = r.proceso_id LEFT JOIN documentos d ON d.id = p.documento_id").fetchall()
    for r in rnd.sample(ext, min(cuota["extraccion"], len(ext))):
        first_word = (r["proveedor"] or "").split(" ")[0] if r["proveedor"] else None
        evidencia: dict = {"texto_fuente": _snippet(r["texto"] or "", first_word)}
        if r["extraccion_metodo"] == "comprar":
            # COMPR.AR no tiene texto: la evidencia es el cuadro comparativo del renglón y las OC del proveedor.
            try:
                evidencia = {
                    "fragmento_fuente_dictamen_y_pliego": json.loads(r["fuente_fragmento"]) if r["fuente_fragmento"] else None,
                    "ofertas_del_renglon_cuadro_comparativo": [dict(o) for o in conn.execute(
                        "SELECT proveedor, cuit, cantidad, precio_unitario, total, marca, ganadora FROM ofertas "
                        "WHERE proceso_id=? AND nro_renglon IS ? ORDER BY precio_unitario", (r["proceso_id"], r["nro_renglon"]))],
                    "ordenes_de_compra_del_proveedor": [dict(o) for o in conn.execute(
                        "SELECT numero_oc, fecha, monto, tipo FROM ordenes_compra WHERE proceso_id=? AND cuit IS ?",
                        (r["proceso_id"], r["proveedor_cuit"]))],
                    "nota": "Los renglones de COMPR.AR salen del dictamen (recomendación). Verificá coherencia con el "
                            "cuadro comparativo: el ganador debe tener una oferta con ese precio y cantidad.",
                }
            except sqlite3.OperationalError:
                pass
        items.append({"capa": "extraccion", "id": f"renglon:{r['id']}", "url": r["url"], "item": {
            "extraido": {k: r[k] for k in ("nro_renglon", "descripcion", "proveedor", "proveedor_cuit", "cantidad",
                                           "unidad_compra", "precio_unitario", "monto_total", "extraccion_metodo")},
            "alertas_del_sistema": r["alertas"],
            **evidencia,
        }})

    comps = conn.execute(
        "SELECT r.id, r.descripcion, r.marca, r.cantidad, r.unidad_compra, r.precio_unitario, r.precio_unidad_base, "
        "r.unidad_base, r.contenido_base, pr.nombre producto, k.semaforo, k.motivo, k.motivo_codigo, k.ref_principal, "
        "k.ref_principal_valor, k.sobreprecio_ajustado_pct, k.evidencia_json, k.fecha_compra, p.url, "
        "k.ref_corroborada, k.sobreprecio_corroborado_pct, k.estado_atributo, k.gap_dias, k.presentacion_exacta, "
        "k.ajuste_financiero_pct "
        "FROM renglones r JOIN procesos p ON p.id = r.proceso_id JOIN comparaciones k ON k.renglon_id = r.id "
        "LEFT JOIN productos pr ON pr.id = r.producto_id WHERE r.producto_id IS NOT NULL").fetchall()
    estratos = {"rojo": [], "amarillo": [], "verde": [], "gris": []}
    for c in comps:
        estratos.setdefault(c["semaforo"] or "gris", []).append(c)
    reparto = {"rojo": 0.35, "amarillo": 0.3, "verde": 0.2, "gris": 0.15}
    for sem, frac in reparto.items():
        pool = estratos.get(sem, [])
        for c in rnd.sample(pool, min(len(pool), max(1, round(cuota["comparacion"] * frac)))):
            evid = json.loads(c["evidencia_json"] or "[]")
            items.append({"capa": "comparacion", "id": f"renglon:{c['id']}", "url": c["url"], "item": {
                "reglas_del_comparador": REGLAS_COMPARADOR,
                "renglon": {k: c[k] for k in ("descripcion", "marca", "cantidad", "unidad_compra", "precio_unitario",
                                              "precio_unidad_base", "unidad_base", "contenido_base", "fecha_compra")},
                "producto_canonico": c["producto"],
                "resultado": {k: c[k] for k in ("semaforo", "motivo", "motivo_codigo", "estado_atributo",
                                                "ref_principal", "ref_principal_valor", "ref_corroborada",
                                                "sobreprecio_ajustado_pct", "sobreprecio_corroborado_pct",
                                                "ajuste_financiero_pct", "gap_dias", "presentacion_exacta")},
                "evidencia_constitutiva_y_mas_barata": evid[:2] + sorted(evid[2:], key=lambda o: o.get("precio_ub") or 0)[:6],
            }})

    obs = conn.execute(
        "SELECT p.id, p.fuente, p.fecha, p.descripcion, p.precio, p.contenido_base, p.unidad_base, "
        "p.precio_unidad_base, pr.nombre producto FROM precios_mercado p JOIN productos pr ON pr.id = p.producto_id "
        "WHERE p.canal <> 'oficial' AND p.fecha >= '2024-01-01'").fetchall()
    for o in rnd.sample(obs, min(cuota["mercado"], len(obs))):
        items.append({"capa": "mercado", "id": f"obs:{o['id']}", "url": None, "item": dict(o)})

    try:
        grupos = conn.execute(
            "SELECT clave, GROUP_CONCAT(DISTINCT alias_raw) alias, COUNT(DISTINCT alias_raw) n FROM entidades_alias "
            "WHERE tipo='empresa' GROUP BY clave HAVING n > 1").fetchall()
    except sqlite3.OperationalError:
        grupos = []
    for g in rnd.sample(grupos, min(cuota["entidades"], len(grupos))):
        items.append({"capa": "entidades", "id": f"clave:{g['clave']}", "url": None,
                      "item": {"clave": g["clave"], "alias": (g["alias"] or "").split(",")}})
    return items


REGLAS_COMPARADOR = (
    "Reglas vigentes del comparador (decisiones del revisor, no las audites como error salvo que estén mal aplicadas): "
    "referencia principal = mínimo de mercado (mayorista o minorista) del mismo producto, variante y envase, a la fecha; "
    "el ROJO se mide contra el 'mínimo confirmado': entre los pares de observaciones de dos grupos comerciales distintos "
    "a ±2%, el par más barato, tomando el MAYOR de sus dos precios (criterio conservador decidido por el revisor); debe "
    "superar +20% aun suponiendo 2 meses de plazo de pago (inflación mensual compuesta); amarillo >= 8% contra el mínimo "
    "observado con 1 mes de plazo; precio_ub y precio_original están SIEMPRE por unidad base ($/kg, $/l o $/u), no por "
    "envase; precios de otro mes se llevan al mes de la compra con el IPC (alimentos y bebidas para alimentos, nivel "
    "general para el resto, INDEC nacional) del mes exacto: factor_ipc = I(mes compra) / I(mes observación); "
    "'posterior' = observado después de la compra (no habilita rojo); envase exigido = contenido a ±3%; "
    "INDEC solo nunca da rojo; atributo crítico desconocido o variante sin mercado = gris. La compra se compara a la fecha "
    "de apertura de ofertas. Los renglones de COMPR.AR salen del dictamen de evaluación (recomendación), no del contrato.")


def casos_candidatos(conn: sqlite3.Connection, n: int, ids_extra: list[int] | None = None) -> list[dict]:
    """Lo que un periodista vería primero + lo que cambió de color: todos los rojos, los renglones pedidos
    (rojos que cayeron, transiciones de la última simulación, errores de rondas anteriores) y amarillos por
    monto en exceso. Con la evidencia constitutiva completa (decisión del prime P23)."""
    base = ("SELECT r.id, r.descripcion, r.marca, r.cantidad, r.unidad_compra, r.precio_unitario, r.precio_unidad_base, "
            "r.unidad_base, r.contenido_base, r.proveedor, r.fuente_fragmento, pr.nombre producto, k.* , p.url, p.numero, "
            "COALESCE(p.organismo_canonico, p.organismo) organismo "
            "FROM renglones r JOIN procesos p ON p.id = r.proceso_id JOIN comparaciones k ON k.renglon_id = r.id "
            "LEFT JOIN productos pr ON pr.id = r.producto_id ")
    elegidos: dict[int, sqlite3.Row] = {}
    for c in conn.execute(base + "WHERE k.semaforo = 'rojo' ORDER BY k.sobreprecio_ajustado_pct DESC"):
        elegidos.setdefault(c["id"], c)
    for rid in ids_extra or []:
        c = conn.execute(base + "WHERE r.id = ?", (rid,)).fetchone()
        if c:
            elegidos.setdefault(c["id"], c)
    for c in conn.execute(base + "WHERE k.semaforo = 'amarillo' ORDER BY k.exceso_ars DESC"):
        if len(elegidos) >= n:
            break
        elegidos.setdefault(c["id"], c)
    items = []
    for c in list(elegidos.values())[:n]:
        evid = json.loads(c["evidencia_json"] or "[]")
        constitutivas = evid[:2] + sorted(evid[2:], key=lambda o: o.get("precio_ub") or 0)[:8]
        items.append({"capa": "caso", "id": f"renglon:{c['id']}", "url": c["url"], "item": {
            "reglas_del_comparador": REGLAS_COMPARADOR,
            "proceso": {"numero": c["numero"], "organismo": c["organismo"], "fecha_comparacion": c["fecha_compra"]},
            "renglon": {k: c[k] for k in ("descripcion", "marca", "cantidad", "unidad_compra", "precio_unitario",
                                          "precio_unidad_base", "unidad_base", "contenido_base", "proveedor")},
            "fragmento_fuente": json.loads(c["fuente_fragmento"]) if c["fuente_fragmento"] else None,
            "producto_canonico": c["producto"],
            "resultado": {k: c[k] for k in ("semaforo", "motivo", "motivo_codigo", "estado_atributo", "ref_principal",
                                            "ref_principal_valor", "ref_corroborada", "sobreprecio_ajustado_pct",
                                            "sobreprecio_corroborado_pct", "ajuste_financiero_pct", "gap_dias",
                                            "presentacion_exacta", "fuentes_independientes_min", "ref_min_mayorista",
                                            "ref_min_minorista", "ref_indec", "confianza")},
            "sensibilidad_plazo": json.loads(c["sensibilidad_json"] or "{}"),
            "evidencia_constitutiva_y_mas_barata": constitutivas,
        }})
    return items


CAPAS["caso"] = ("Es un CANDIDATO a mostrar a un periodista o un renglón que cambió de color. Decidí si el resultado "
                 "(semáforo y motivo) es defendible: mismo producto, variante y presentación, referencia de mercado de "
                 "la fecha, unidades bien leídas, cuenta correcta, reglas del comparador bien aplicadas. Si es gris, "
                 "decidí si el gris está justificado o si esconde un caso que debería verse. 'ok' = defendible para "
                 "revisión humana; 'error' = no debería mostrarse así; 'dudoso' = falta información crítica.")


def run(modelo: str, n: int, max_usd: float, seed: int = 7, modo: str = "muestra",
        ids_extra: list[int] | None = None) -> Path:
    config.load_env()
    import os
    os.environ["LIC_LLM_ROLE_JUEZ"] = modelo
    out_dir = config.scratch_path("licitaciones", "auditoria").parent / "auditoria" / dt.datetime.now().strftime("%Y%m%d-%H%M")
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = _ro(config.db_path())
    items = casos_candidatos(conn, n, ids_extra) if modo == "casos" else muestra(conn, n, seed)
    llm.reset_budget()
    resultados = []
    for i, it in enumerate(items, 1):
        prompt = PROMPT.format(capa=it["capa"], que_hace=CAPAS[it["capa"]],
                               item=json.dumps(it["item"], ensure_ascii=False, default=str, indent=1)[:12000])
        try:
            v = llm.complete_json(prompt, SCHEMA, role="juez", proposito=f"juez_{it['capa']}", max_usd=max_usd,
                                  schema_name="veredicto", reasoning_effort="medium")
        except llm.LLMUnavailable as exc:
            v = {"veredicto": "sin_juicio", "tipo_error": "otro", "explicacion": str(exc)[:300], "valor_correcto": None,
                 "regla_sugerida": None}
            if isinstance(exc, llm.BudgetExceeded):
                resultados.append({**it, **v})
                break
        resultados.append({**it, **v})
        print(f"{i}/{len(items)} {it['capa']} {v['veredicto']} {v['tipo_error']}", flush=True)

    with open(out_dir / "items.tsv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(["Capa", "Veredicto juez", "Tipo de error", "id", "Link", "Explicación", "Valor correcto",
                    "Regla sugerida", "Ítem (JSON)", "Tu veredicto", "Tu comentario"])
        for r in resultados:
            w.writerow([r["capa"], r["veredicto"], r["tipo_error"], r["id"], r.get("url") or "",
                        re.sub(r"\s+", " ", r["explicacion"] or ""), r.get("valor_correcto") or "",
                        re.sub(r"\s+", " ", r.get("regla_sugerida") or ""),
                        json.dumps(r["item"], ensure_ascii=False, default=str)[:3000].replace("\t", " ").replace("\n", " "),
                        "", ""])
    por_capa: dict[str, Counter] = {}
    for r in resultados:
        por_capa.setdefault(r["capa"], Counter())[r["veredicto"]] += 1
    tipos = Counter(r["tipo_error"] for r in resultados if r["veredicto"] == "error")
    lines = [f"# Auditoría del juez ({modelo}) — {dt.datetime.now():%Y-%m-%d %H:%M}", "",
             f"Ítems: {len(resultados)} · gasto LLM aprox. USD {llm.run_spend():.2f} (tope {max_usd})", "",
             "El juez puede equivocarse: revisar items.tsv y completar 'Tu veredicto'. Medir acuerdo juez-humano antes de",
             "usarlo como métrica. La muestra es estratificada (incluye verdes y grises): las tasas no son globales.", "",
             "## Veredictos por capa", "", "| Capa | ok | error | dudoso | sin juicio |", "|---|---|---|---|---|"]
    for capa, cnt in por_capa.items():
        lines.append(f"| {capa} | {cnt['ok']} | {cnt['error']} | {cnt['dudoso']} | {cnt['sin_juicio']} |")
    lines += ["", "## Errores por tipo", ""] + [f"- {t}: {c}" for t, c in tipos.most_common()]
    lines += ["", "## Reglas sugeridas (candidatas a backlog)", ""]
    for r in resultados:
        if r["veredicto"] in {"error", "dudoso"} and r.get("regla_sugerida"):
            lines.append(f"- [{r['capa']} / {r['tipo_error']}] {r['regla_sugerida']} ({r['id']})")
    (out_dir / "resumen.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Registro acumulado del gasto del juez (para controlar el presupuesto de auditoría).
    ledger = out_dir.parent / "gasto_prime.tsv"
    nuevo = not ledger.exists()
    with open(ledger, "a", encoding="utf-8") as fh:
        if nuevo:
            fh.write("fecha\tuso\tmodelo\titems\tusd\n")
        fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}\tjuez {modo} seed={seed}\t{modelo}\t{len(resultados)}\t{llm.run_spend():.3f}\n")
    return out_dir


def main():
    ap = argparse.ArgumentParser(description="Juez LLM externo (solo lectura)")
    ap.add_argument("--modelo", required=True, help="modelo del juez (ej. gpt-6-astra); elegirlo con benchmark humano")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--max-usd", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--ids", default="", help="modo casos: renglones extra a auditar (ids separados por coma)")
    ap.add_argument("--modo", choices=["muestra", "casos"], default="muestra",
                    help="muestra estratificada o casos candidatos (rojos + amarillos más fuertes)")
    args = ap.parse_args()
    ids = [int(x) for x in args.ids.split(",") if x.strip()] if args.ids else None
    print(run(args.modelo, args.n, args.max_usd, args.seed, args.modo, ids))


if __name__ == "__main__":
    main()
