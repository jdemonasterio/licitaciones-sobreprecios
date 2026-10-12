"""Montos comparables entre años: pesos de cada fecha, pesos reales o dólares.

Con inflación alta, $1.000 de enero 2021 y $1.000 de enero 2024 no son lo mismo (la
canasta se multiplicó varias veces). Para sumar o comparar montos de fechas distintas:

- pesos reales ("real"): monto x IPC(mes base) / IPC(mes del monto). El IPC es el nivel
  general del INDEC (serie `ipc_general`); el mes base es el último publicado. Si el mes
  del monto todavía no tiene IPC publicado se usa el último y se marca como aproximado.
- dólares ("usd"): monto / cotización de VENTA de ese día (la última disponible hasta 7
  días antes: fines de semana y feriados). Serie por defecto: contado con liqui, el tipo
  de cambio libre que mejor refleja el valor de mercado con cepo (2019-2025); el oficial
  queda disponible (`usd_oficial`, `usd_mayorista`).

Esto es para mirar y sumar montos de distintas fechas (analítica, series de precios). El
semáforo NO cambia: el comparador ya compara contra precios de la misma fecha y, si usa
uno de otro mes, lo lleva al mes de la compra con el IPC (pipeline/compare.py).
"""
from __future__ import annotations

import bisect
import datetime as dt
import sqlite3
from dataclasses import dataclass, field

from .. import config

MODOS = ("nominal", "real", "usd")
_MAX_DIAS_COTIZACION = 7


@dataclass
class Moneda:
    ipc: dict[str, float]                       # YYYY-MM-01 -> índice
    usd: dict[str, float]                       # YYYY-MM-DD -> cotización (venta)
    base_mes: str | None = None                 # YYYY-MM-01; default: último IPC publicado
    serie_usd: str = "usd_contadoconliqui"
    _fechas_usd: list[str] = field(default_factory=list, repr=False)
    _usd_mes: dict[str, float] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if not self.base_mes and self.ipc:
            self.base_mes = max(self.ipc)
        self._fechas_usd = sorted(self.usd)
        por_mes: dict[str, list[float]] = {}
        for fecha, valor in self.usd.items():
            por_mes.setdefault(fecha[:7], []).append(valor)
        self._usd_mes = {m: sum(v) / len(v) for m, v in por_mes.items()}

    # ── pesos reales
    def factor_real(self, fecha: str | None) -> tuple[float | None, bool]:
        """(factor para llevar a pesos del mes base, es_aproximado)."""
        if not fecha or not self.ipc or not self.base_mes:
            return None, False
        mes = fecha[:7] + "-01"
        aproximado = False
        if mes not in self.ipc:
            ultimo = max(self.ipc)
            if mes > ultimo:            # mes todavía sin IPC publicado: se usa el último
                mes, aproximado = ultimo, True
            else:
                return None, False      # antes del comienzo de la serie: no se inventa
        return self.ipc[self.base_mes] / self.ipc[mes], aproximado

    def a_real(self, monto: float | None, fecha: str | None) -> float | None:
        factor, _ = self.factor_real(fecha)
        return None if monto is None or factor is None else monto * factor

    # ── dólares
    def tasa_usd(self, fecha: str | None) -> float | None:
        """Cotización del día (última hasta 7 días antes); con 'YYYY-MM', promedio del mes."""
        if not fecha or not self._fechas_usd:
            return None
        if len(fecha) == 7:
            return self._usd_mes.get(fecha)
        dia = fecha[:10]
        i = bisect.bisect_right(self._fechas_usd, dia) - 1
        if i < 0:
            return None
        previa = self._fechas_usd[i]
        if (dt.date.fromisoformat(dia) - dt.date.fromisoformat(previa)).days > _MAX_DIAS_COTIZACION:
            return None
        return self.usd[previa]

    def a_usd(self, monto: float | None, fecha: str | None) -> float | None:
        tasa = self.tasa_usd(fecha)
        return None if monto is None or not tasa else monto / tasa

    # ── genérico
    def convertir(self, monto: float | None, fecha: str | None, modo: str) -> float | None:
        if modo == "real":
            return self.a_real(monto, fecha)
        if modo == "usd":
            return self.a_usd(monto, fecha)
        return monto

    def etiqueta(self, modo: str) -> str:
        if modo == "real":
            return f"pesos de {_mes_txt(self.base_mes)} (descontada la inflación)" if self.base_mes else "pesos reales"
        if modo == "usd":
            return "dólares (" + self.serie_usd.removeprefix("usd_").replace("contadoconliqui", "contado con liqui") + ")"
        return "pesos de cada fecha"


_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]


def _mes_txt(mes: str | None) -> str:
    if not mes:
        return "?"
    return f"{_MESES[int(mes[5:7]) - 1]} {mes[:4]}"


def cargar(conn: sqlite3.Connection, serie_usd: str | None = None) -> Moneda:
    cfg = config.settings().get("moneda") or {}
    serie_ipc = cfg.get("ipc", "ipc_general")
    serie_usd = serie_usd or cfg.get("serie_usd", "usd_contadoconliqui")
    ipc = {r[0]: r[1] for r in conn.execute("SELECT fecha, valor FROM indices WHERE serie=?", (serie_ipc,))}
    usd = {r[0]: r[1] for r in conn.execute("SELECT fecha, valor FROM indices WHERE serie=?", (serie_usd,))}
    return Moneda(ipc=ipc, usd=usd, base_mes=cfg.get("base_mes"), serie_usd=serie_usd)


def registrar(conn: sqlite3.Connection, modo: str, moneda: Moneda | None = None) -> Moneda:
    """Registra en la conexión la función SQL conv(monto, fecha) para el modo pedido."""
    moneda = moneda or cargar(conn)
    conn.create_function("conv", 2, lambda monto, fecha: moneda.convertir(monto, fecha, modo), deterministic=True)
    return moneda
