"""Cuarentena de precios de mercado sospechosos (no se borran: se apartan con el motivo).

El archivo SEPA y los scrapers traen precios imposibles que el filtro por canal del
comparador no ve cuando una cadena entera los repite: Comodín reportó $1,00 en TODOS sus
productos el 19/08/2024; La Gallega publica fideos a $11,95 (un precio de hace años);
Maxiconsumo, "azúcar suelta 1 kg" a $1,45. Con el criterio "mínimo de mercado", uno solo
de esos fija una referencia falsa.

Regla: para cada producto, mes y TAMAÑO de envase, la mediana del precio por unidad base
de todas las fuentes (sin INDEC); por tamaño porque el grupo de un producto puede mezclar
envases de precio por kg muy distinto. Una observación < cuarentena_bajo x mediana o > cuarentena_alto x
mediana queda en cuarentena; también todo precio de envase < cuarentena_precio_minimo
pesos (placeholders). Se recalcula entera cada vez (si llegan más datos, un precio puede
volver). Los apartados no entran al comparador ni a los gráficos; siguen en la base para
revisarlos (decisión del LLM prime, riesgos ronda 2: "cuarentena, no borrado").
"""
from __future__ import annotations

import sqlite3
import statistics

from .. import config


def run(conn: sqlite3.Connection) -> str:
    cfg = config.settings().get("comparacion") or {}
    bajo = float(cfg.get("cuarentena_bajo", 0.3))
    alto = float(cfg.get("cuarentena_alto", 5.0))
    minimo = float(cfg.get("cuarentena_precio_minimo", 5.0))
    rows = conn.execute(
        "SELECT id, producto_id || '|' || COALESCE(ROUND(contenido_base, 2), '?'), substr(fecha, 1, 7) mes, precio, "
        "precio_unidad_base FROM precios_mercado "
        "WHERE producto_id IS NOT NULL AND canal <> 'oficial' AND precio_unidad_base > 0").fetchall()
    grupos: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        grupos.setdefault((r[1], r[2]), []).append(r[4])
    medianas = {k: statistics.median(v) for k, v in grupos.items() if len(v) >= 5}
    marcas: list[tuple[str, int]] = []
    for rid, pid, mes, precio, pub in rows:
        motivo = None
        if precio is not None and precio < minimo:
            motivo = f"precio de envase ${precio:g}: parece un valor de relleno"
        else:
            med = medianas.get((pid, mes))
            if med and pub < bajo * med:
                motivo = f"{pub / med:.2f}x la mediana del mes (${med:,.0f}): precio viejo o mal cargado"
            elif med and pub > alto * med:
                motivo = f"{pub / med:.1f}x la mediana del mes (${med:,.0f})"
        if motivo:
            marcas.append((motivo, rid))
    conn.execute("UPDATE precios_mercado SET cuarentena = NULL WHERE cuarentena IS NOT NULL")
    conn.executemany("UPDATE precios_mercado SET cuarentena = ? WHERE id = ?", marcas)
    conn.commit()
    return f"{len(marcas)} precios de mercado en cuarentena (de {len(rows)})"
