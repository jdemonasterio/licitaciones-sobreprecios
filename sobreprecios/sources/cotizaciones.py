"""Cotizaciones diarias del dólar (oficial, mayorista, blue, MEP, contado con liqui).

Para qué: con la inflación y las devaluaciones argentinas, un monto en pesos de 2021 y
uno de 2025 no se pueden comparar ni sumar tal cual. El webui permite verlos en pesos de
cada fecha, en pesos reales (descontando IPC, ver normalize/moneda.py) o en dólares.

Fuente: ArgentinaDatos (api.argentinadatos.com, proyecto abierto que consolida BCRA, BNA
y cotizaciones de mercado), un solo pedido con todas las casas desde 2011. Respaldo:
Bluelytics (oficial y blue) si ArgentinaDatos no responde.

Se guarda en `indices` con serie `usd_<casa>` (precio de VENTA, un valor por día hábil).
"""
from __future__ import annotations

import logging
import sqlite3

import httpx

from .. import config

logger = logging.getLogger(__name__)

API = "https://api.argentinadatos.com/v1/cotizaciones/dolares"
RESPALDO = "https://api.bluelytics.com.ar/v2/evolution.json"
CASAS_DEFAULT = ["oficial", "mayorista", "blue", "bolsa", "contadoconliqui"]
_RESPALDO_CASAS = {"Oficial": "oficial", "Blue": "blue"}


def parse_argentinadatos(payload: list[dict], casas: list[str], desde: str) -> dict[str, dict[str, float]]:
    """{casa: {YYYY-MM-DD: venta}} solo con valores positivos."""
    out: dict[str, dict[str, float]] = {c: {} for c in casas}
    for row in payload or []:
        casa, fecha, venta = row.get("casa"), str(row.get("fecha") or "")[:10], row.get("venta")
        if casa in out and fecha >= desde and isinstance(venta, (int, float)) and venta > 0:
            out[casa][fecha] = float(venta)
    return out


def parse_bluelytics(payload: list[dict], desde: str) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {c: {} for c in _RESPALDO_CASAS.values()}
    for row in payload or []:
        casa = _RESPALDO_CASAS.get(row.get("source"))
        fecha, venta = str(row.get("date") or "")[:10], row.get("value_sell")
        if casa and fecha >= desde and isinstance(venta, (int, float)) and venta > 0:
            out[casa][fecha] = float(venta)
    return out


def fetch(casas: list[str], desde: str = "2016-01-01") -> tuple[dict[str, dict[str, float]], str]:
    """Descarga las cotizaciones. Devuelve (datos, fuente usada)."""
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        try:
            resp = client.get(API)
            resp.raise_for_status()
            return parse_argentinadatos(resp.json(), casas, desde), "argentinadatos"
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("ArgentinaDatos no respondió (%s); uso Bluelytics (solo oficial y blue)", exc)
            resp = client.get(RESPALDO)
            resp.raise_for_status()
            return parse_bluelytics(resp.json(), desde), "bluelytics"


def guardar(conn: sqlite3.Connection, datos: dict[str, dict[str, float]]) -> int:
    n = 0
    for casa, serie in datos.items():
        for fecha, valor in serie.items():
            conn.execute("INSERT INTO indices (serie, fecha, valor) VALUES (?, ?, ?) "
                         "ON CONFLICT (serie, fecha) DO UPDATE SET valor=excluded.valor", (f"usd_{casa}", fecha, valor))
            n += 1
    conn.commit()
    return n


def run(conn: sqlite3.Connection) -> str:
    cfg = config.settings().get("moneda") or {}
    casas = cfg.get("casas") or CASAS_DEFAULT
    datos, fuente = fetch(casas, cfg.get("desde", "2016-01-01"))
    n = guardar(conn, datos)
    rangos = ", ".join(f"{c} {min(s)}→{max(s)}" for c, s in datos.items() if s)
    return f"{n} cotizaciones diarias del dólar ({fuente}): {rangos}"
