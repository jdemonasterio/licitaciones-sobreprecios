"""Importa el dump MySQL del sistema original de Grupo Quijote (2020-2021).

El sistema viejo (Laravel) guardó:
  compras_del_estado  ~115 renglones adjudicados publicados en el BO en 2021
                      (leche en polvo, yerba, fideos, harinas...), con link al aviso
  daily_scrappes      scrapes diarios ene-nov 2021 de 4 fuentes mayoristas
                      (Maxiconsumo, Golomax, Tu Mayorista, Precios Claros mayoristas)

Sirve como golden: con estos datos el paper reportó 3,8% de sobreprecio en
leche en polvo y ~21% en yerba. Si nuestro pipeline no reproduce órdenes de
magnitud parecidos, algo está mal en el pipeline (o en el paper).

Problemas conocidos del dump, que el importador marca en `alertas` en vez de
"arreglar": montos totales que no cierran con cantidad x precio, compras
fechadas distinto que su aviso, la columna reparticion es la misma para todo.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from .. import db
from ..textnorm import norm
from ..units import parse_ars, parse_number, parse_size, price_per_base

# Los procesos usan la misma clave que el conector del BO (fuente + "primera/<id>/<fecha>"):
# cuando el extractor relee el aviso original, enriquece este mismo proceso.
PROCESO_FUENTE = "bo_primera"
MERCADO_PREFIJO = "quijotes_2021"
# Dump MySQL del sistema 2021 (no se distribuye con el repo): variable LIC_QUIJOTE_DUMP o data/quijotes.sql.
DEFAULT_DUMP = Path(os.getenv("LIC_QUIJOTE_DUMP") or (Path(__file__).resolve().parents[2] / "data" / "quijotes.sql"))

MAYORISTAS = {1: "maxiconsumo", 2: "golomax", 3: "tumayorista", 4: "preciosclaros_mayoristas"}
_AVISO_RE = re.compile(r"detalleAviso/(\w+)/(\d+)/(\d{8})")


def parse_mysql_values(sql: str, table: str) -> list[list]:
    """Filas de todos los `INSERT INTO `table` VALUES (...), (...);` del dump.

    Tokenizador chico a mano: strings con comillas simples y escapes \\' \\" \\\\ \\n,
    NULL y números. Suficiente para dumps de mysqldump/Navicat; no es un parser SQL.
    """
    rows: list[list] = []
    marker = f"INSERT INTO `{table}` VALUES "
    pos = 0
    while True:
        start = sql.find(marker, pos)
        if start < 0:
            break
        i = start + len(marker)
        n = len(sql)
        row: list | None = None
        while i < n:
            ch = sql[i]
            if ch == "(" and row is None:
                row = []
                i += 1
                continue
            if row is None:
                if ch == ";":
                    break
                i += 1
                continue
            if ch == "'":
                i += 1
                buf = []
                while i < n:
                    c = sql[i]
                    if c == "\\" and i + 1 < n:
                        nxt = sql[i + 1]
                        buf.append({"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(nxt, nxt))
                        i += 2
                        continue
                    if c == "'":
                        if i + 1 < n and sql[i + 1] == "'":   # '' escapado
                            buf.append("'")
                            i += 2
                            continue
                        i += 1
                        break
                    buf.append(c)
                    i += 1
                row.append("".join(buf))
                continue
            if ch in " \n\t,":
                i += 1
                continue
            if ch == ")":
                rows.append(row)
                row = None
                i += 1
                continue
            j = i
            while j < n and sql[j] not in ",)":
                j += 1
            token = sql[i:j].strip()
            row.append(None if token.upper() == "NULL" else token)
            i = j
        pos = i
    return rows


def _columns(sql: str, table: str) -> list[str]:
    match = re.search(rf"CREATE TABLE `{table}` \((.*?)\n\) ENGINE", sql, re.S)
    if not match:
        raise ValueError(f"no encontré CREATE TABLE de {table}")
    return re.findall(r"^\s*`(\w+)`", match.group(1), re.M)


def table_dicts(sql: str, table: str) -> list[dict]:
    cols = _columns(sql, table)
    return [dict(zip(cols, row)) for row in parse_mysql_values(sql, table)]


def _monto_alert(cantidad: float | None, precio: float | None, monto: float | None) -> str | None:
    if not (cantidad and precio and monto):
        return None
    calc = cantidad * precio
    if abs(calc - monto) / monto > 0.02:
        return f"monto_inconsistente: cantidad x precio = {calc:,.0f} vs monto informado {monto:,.0f}"
    return None


def import_compras(conn: sqlite3.Connection, sql: str) -> tuple[int, int]:
    """Procesos (uno por aviso del BO) + renglones. Devuelve (n_procesos, n_renglones)."""
    principales = {r["id"]: r for r in table_dicts(sql, "productos_principales")}
    compras = table_dicts(sql, "compras_del_estado")
    procesos: dict[str, int] = {}
    n_renglones = 0
    for row in compras:
        if row.get("desestimada") == "1":
            continue
        link = row.get("link") or ""
        aviso = _AVISO_RE.search(link)
        external_id = "/".join(aviso.groups()) if aviso else f"sin-aviso-{row['id']}"
        if external_id not in procesos:
            pp = principales.get(row.get("producto_id")) or {}
            procesos[external_id] = db.upsert(conn, "procesos", {
                "fuente": PROCESO_FUENTE,
                "external_id": external_id,
                "numero": None,
                "organismo": None,
                "jurisdiccion": pp.get("reparticion"),
                "tipo_procedimiento": None,
                "objeto": f"Compra de {pp.get('descripcion', row.get('producto'))} (aviso BO {external_id})",
                "categoria": "bienes_comparables",
                "es_comparable": 1,
                "categoria_razon": "importado del dataset Grupo Quijote 2021 (solo alimentos)",
                "fecha_adjudicacion": row.get("fecha"),
                "fecha_publicacion": f"{aviso.group(3)[:4]}-{aviso.group(3)[4:6]}-{aviso.group(3)[6:]}" if aviso else row.get("fecha"),
                "url": link.split("?")[0] or None,
                "created_at": db.now_iso(),
                "updated_at": db.now_iso(),
            }, ("fuente", "external_id"))
        cantidad = parse_number(row.get("cantidad"))
        precio = parse_ars(row.get("precio"))
        monto = parse_number(row.get("monto_total"))
        alertas = [a for a in [_monto_alert(cantidad, precio, monto)] if a]
        if aviso and row.get("fecha"):
            pub = f"{aviso.group(3)[:4]}-{aviso.group(3)[4:6]}-{aviso.group(3)[6:]}"
            if pub != row["fecha"]:
                alertas.append(f"fecha_distinta_al_aviso: compra {row['fecha']} vs aviso {pub}")
        db.upsert(conn, "renglones", {
            "proceso_id": procesos[external_id],
            "nro_renglon": None,
            "descripcion": row.get("producto") or "",
            "cantidad": cantidad,
            "unidad_compra": "unidad",
            "precio_unitario": precio,
            "monto_total": monto,
            "marca": (row.get("marca") or "").strip() or None,
            "alertas": json.dumps(alertas, ensure_ascii=False) if alertas else None,
            "extraccion_metodo": "legacy_quijotes",
            "external_key": f"legacy:{row['id']}",
        }, ("proceso_id", "external_key"))
        n_renglones += 1
    conn.commit()
    return len(procesos), n_renglones


def import_precios(conn: sqlite3.Connection, sql: str) -> int:
    """Observaciones de mercado de daily_scrappes. Devuelve cuántas se escribieron."""
    n = 0
    for row in table_dicts(sql, "daily_scrappes"):
        fecha = (row.get("created_at") or "")[:10]
        slug = MAYORISTAS.get(int(row["mayorista_id"]), f"mayorista_{row['mayorista_id']}")
        try:
            items = json.loads(row.get("response") or "[]")
        except json.JSONDecodeError:
            continue
        for item in items if isinstance(items, list) else []:
            desc = (item.get("product_name") or "").strip()
            precio = parse_ars(item.get("product_price"))
            if not desc or not precio or precio <= 0:
                continue
            size = parse_size(desc)
            ppu, base = price_per_base(precio, size)
            conn.execute(
                "INSERT INTO precios_mercado (fuente, canal, fecha, descripcion, precio, contenido_base, "
                "unidad_base, precio_unidad_base, obs_key) VALUES (?, 'mayorista', ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (fuente, fecha, obs_key) DO UPDATE SET precio=excluded.precio, "
                "contenido_base=excluded.contenido_base, unidad_base=excluded.unidad_base, "
                "precio_unidad_base=excluded.precio_unidad_base",
                (f"{MERCADO_PREFIJO}:{slug}", fecha, desc, precio, size.base()[0] if size else None, base, ppu,
                 norm(desc)))
            n += 1
    conn.commit()
    return n


def run(conn: sqlite3.Connection, dump_path: Path | None = None) -> str:
    path = Path(dump_path or DEFAULT_DUMP)
    if not path.exists():
        raise FileNotFoundError(f"no existe el dump {path}")
    sql = path.read_text(encoding="utf-8", errors="replace")
    n_proc, n_reng = import_compras(conn, sql)
    n_obs = import_precios(conn, sql)
    return f"Quijote 2021: {n_proc} procesos, {n_reng} renglones, {n_obs} precios mayoristas"
