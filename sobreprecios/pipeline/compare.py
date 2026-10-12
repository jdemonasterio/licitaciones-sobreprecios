"""Sobreprecio por renglón: precio del Estado vs referencia de mercado a la misma fecha.

Cómo se arma la referencia (todo por unidad base: ARS/kg, ARS/l, ARS/u):

1. Observaciones del MISMO producto canónico (config/productos.yml) y, si el producto
   tiene atributos críticos (variantes), de la MISMA variante que exige la compra.
   Atributo desconocido o ambiguo en la compra => gris (decisión del LLM prime P17).
2. Fecha "as-of": por serie (fuente + descripción) la última observación <= fecha
   dentro de ventana_directa_dias; una posterior solo si no hay anterior (y sin rojo).
   Cada precio se lleva al mes de la compra con el IPC del mes exacto (P20). Si no hay
   nada en la ventana, la fecha más cercana dentro de gap_maximo_dias: contexto, sin rojo.
3. Tamaño: envases dentro de la banda del comprado; si la compra exige el envase, solo
   ese envase (±15%), sin relajar (riesgo señalado por el prime). Contenido desconocido
   no es compatible.
4. Outliers: por canal, < outlier_bajo*mediana o > outlier_alto*mediana.
5. Referencia principal: misma marca si la compra la nombra y está; si no, el MÍNIMO de
   mercado de ambos canales (jdemonasterio 2026-10-11; prime P16).

Dos brechas, siempre a la vista:
  - vs mínimo observado (referencia principal): decide amarillo/verde;
  - vs mínimo CORROBORADO: el precio más bajo confirmado por dos familias de fuentes
    independientes (±2%). Solo esta puede dar rojo: un mínimo aislado ni lo crea ni lo
    borra (P16).

El rojo es deliberadamente difícil. Un rojo sale a un periodista y un rojo falso quema la
credibilidad del sistema. Exige: brecha corroborada >= umbral aun con el plazo de pago
más largo plausible (factor compuesto, P20/riesgos), observaciones dentro de la ventana y
no posteriores, mismo resultado con y sin ajuste por IPC, atributo verificado o no exigido,
referencia que no sea INDEC sola (P19), monto del aviso coherente y ratio plausible.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import statistics
from dataclasses import asdict, dataclass, field, replace

from .. import config, db
from ..catalog import Producto, load_catalog, variante as variante_de, variante_estado, variantes_aceptadas
from ..revision_humana import aplicar_correcciones, aplicar_exclusiones_mercado
from .comprar_import import actualizar_estado_documental, brecha_ofertas
from ..textnorm import norm, tokens
from ..units import parse_size


@dataclass
class Obs:
    fuente: str
    canal: str              # mayorista | minorista | oficial
    fecha: str              # YYYY-MM-DD
    precio_ub: float        # ARS por unidad base (ya llevado al mes de la compra si factor_ipc)
    contenido_base: float | None = None
    descripcion: str = ""
    url: str | None = None
    precio_original: float | None = None   # precio observado, antes del ajuste temporal
    factor_ipc: float | None = None        # None: mismo mes o índice no disponible
    posterior: bool = False                # observada DESPUÉS de la fecha de comparación
    id: int | None = None                  # precios_mercado.id (None: observación oficial/INDEC o de test)


@dataclass
class Resultado:
    precio_estado_ub: float | None
    ref_min_mayorista: float | None = None
    ref_p25_mayorista: float | None = None
    ref_mediana_mayorista: float | None = None
    ref_n_mayorista: int = 0
    ref_misma_marca: float | None = None
    ref_n_misma_marca: int = 0
    ref_min_minorista: float | None = None
    ref_mediana_minorista: float | None = None
    ref_n_minorista: int = 0
    presentacion_exacta: bool = False
    ref_indec: float | None = None
    ref_principal: str | None = None
    ref_principal_valor: float | None = None
    ref_corroborada: float | None = None            # mínimo confirmado por 2 familias independientes
    gap_dias: int | None = None                     # antigüedad máxima de las observaciones usadas
    ajuste_financiero_pct: float = 0.0
    sobreprecio_pct: float | None = None
    sobreprecio_ajustado_pct: float | None = None
    sobreprecio_corroborado_pct: float | None = None   # con el plazo más largo plausible
    sobreprecio_min_pct: float | None = None
    sobreprecio_minorista_pct: float | None = None
    sobreprecio_indec_pct: float | None = None
    exceso_ars: float | None = None
    confianza: str = "baja"
    semaforo: str = "gris"
    motivo: str = ""
    # ok | sin_precio | sin_datos | revisar_unidad | fuera_alcance | atributo_desconocido |
    # sin_referencia_variante | sin_referencia_presentacion
    motivo_codigo: str = "sin_precio"
    estado_atributo: str | None = None   # verificado | no_exigido | desconocido | ambiguo | None (sin variantes)
    fuentes_independientes_min: int = 0  # familias que confirman la referencia corroborada (±2%)
    sensibilidad: dict = field(default_factory=dict)   # brecha según plazo de pago supuesto (0-3 meses)
    evidencia: list[dict] = field(default_factory=list)
    # ids de precios_mercado que FORMAN cada referencia (jdemonasterio 2026-10-12, MEJORAS #27 parcial):
    # "minimo" = las observaciones con el precio que fijó ref_principal_valor; "par_corroborado" = las
    # 2 observaciones (familias distintas) que confirman ref_corroborada. Listas vacías: sin datos, o
    # la referencia es una mediana que no coincide con ningún id puntual (misma_marca/mediana_*).
    obs_ids: dict = field(default_factory=dict)


def _days(a: str, b: str) -> int:
    return abs((dt.date.fromisoformat(a[:10]) - dt.date.fromisoformat(b[:10])).days)


def _month(fecha: str) -> str:
    return fecha[:7] + "-01"


def _meses_disponibles(indice: dict[str, float], hasta_mes: str, publicaciones: dict[str, str] | None,
                       disponible_a: str | None) -> list[str]:
    """Meses de `indice` <= `hasta_mes`, recortados por fecha de publicación si corresponde.

    `comparacion.ipc_modo` (P20/#45, jdemonasterio 2026-10-12): "disponible_a_fecha" solo deja
    pasar meses cuya `indices.fecha_publicacion` sea <= `disponible_a` (la fecha de la compra) —
    "lo que se sabía a la fecha", no el IPC definitivo de hoy. Sin `publicaciones`
    (modo "retrospectivo", default) no se filtra nada: comportamiento idéntico al de siempre. Un
    mes sin fecha_publicacion conocida se excluye en modo estricto (no se puede probar que ya
    estaba publicado).
    """
    meses = [m for m in indice if m <= hasta_mes]
    if publicaciones is None or disponible_a is None:
        return meses
    return [m for m in meses if publicaciones.get(m) and publicaciones[m] <= disponible_a]


def index_ratio(indice: dict[str, float], desde: str, hasta: str,
                publicaciones: dict[str, str] | None = None) -> float | None:
    """Factor para llevar un precio de `desde` a `hasta` con un índice mensual.

    Usa el último mes disponible <= cada fecha (el IPC se publica con ~2 semanas
    de atraso: para una compra de este mes se usa el último publicado).
    `publicaciones` (modo "disponible_a_fecha"): ningún lookup usa un mes publicado DESPUÉS de
    `hasta` (la fecha "a la que" se quiere saber qué se sabía).
    """
    def lookup(fecha: str) -> float | None:
        candidates = _meses_disponibles(indice, _month(fecha), publicaciones, hasta)
        return indice[max(candidates)] if candidates else None
    a, b = lookup(desde), lookup(hasta)
    if not a or not b:
        return None
    return b / a


def monthly_inflation(indice: dict[str, float], fecha: str,
                      publicaciones: dict[str, str] | None = None) -> float | None:
    """Inflación mensual (0.03 = 3%) del último mes publicado <= fecha.

    `publicaciones`: en modo "disponible_a_fecha" solo usa meses ya publicados a `fecha` (ver
    `_meses_disponibles`); en modo "retrospectivo" (default, `publicaciones=None`) usa el IPC
    definitivo de hoy, igual que siempre.
    """
    months = sorted(_meses_disponibles(indice, _month(fecha), publicaciones, fecha))
    if len(months) < 2:
        return None
    return indice[months[-1]] / indice[months[-2]] - 1


def _filter_outliers(values: list[Obs], low: float, high: float) -> list[Obs]:
    if len(values) < 3:
        return values
    med = statistics.median(o.precio_ub for o in values)
    return [o for o in values if low * med <= o.precio_ub <= high * med]


def filter_presentation(obs: list[Obs], contenido_estado: float | None, exacta: bool,
                        cfg: dict) -> tuple[list[Obs], bool]:
    """Filtra por tamaño de envase. Devuelve (observaciones, se usó presentación exacta).

    Si la licitación dice el envase ("envases de 1 kg", "x 800 g") se compara contra ese
    envase (±tolerancia_presentacion, 3 %) (jdemonasterio 2026-10-11: "salvo que en la licitación se indique precisamente
    la presentación"). Con presentacion_exigida_estricta (default, riesgo del prime) no se
    relaja a envases parecidos si no está. Sin envase exigido, cualquier envase dentro de
    la banda (un bulto de 25 kg no es referencia de un paquete de 1 kg). Una observación
    sin contenido conocido no es compatible por defecto.
    """
    base = [o for o in obs if o.canal != "oficial" and o.precio_ub and o.precio_ub > 0]
    if not contenido_estado:
        return base, False
    base = [o for o in base if o.contenido_base]
    if exacta:
        tol = float(cfg.get("tolerancia_presentacion", 0.03))
        exact = [o for o in base if abs(o.contenido_base - contenido_estado) <= tol * contenido_estado]
        if exact or cfg.get("presentacion_exigida_estricta", True):
            return exact, True
    banda = float(cfg.get("banda_tamano", 2.5))
    return [o for o in base if contenido_estado / banda <= o.contenido_base <= contenido_estado * banda], False


def _as_of_por_serie(obs: list[Obs], fecha: str, ventana: int = 0) -> list[Obs]:
    """Una observación por serie (fuente + descripción), "as-of" la fecha (prime P21).

    La última observada <= fecha; si la serie no tiene ninguna anterior, la primera posterior.
    Se marca `posterior` (no corrobora, ver `referencia_corroborada`) SOLO si está a más de
    `ventana` días de la fecha de la compra — jdemonasterio 2026-10-12: una posterior a <= 15 días
    (`ventana_directa_dias`) es, a todo efecto, una observación directa más (misma góndola, un día
    de diferencia): exigirle que no corrobore sería más estricto que con una observación anterior a
    la misma distancia. Más allá de la ventana (ej. la rama de `gap_maximo_dias` en
    `select_observations`, hasta 120 días) la cautela original se mantiene: no habilita rojo. Sin
    esto, el mínimo de la ventana podía ser un precio viejo de la misma góndola que ya había subido
    (juez externo 2026-10-11, renglón 1709) o uno posterior con una promo que todavía no existía.
    """
    series: dict[tuple[str, str], list[Obs]] = {}
    for o in obs:
        series.setdefault((o.fuente, norm(o.descripcion)), []).append(o)
    out = []
    for grupo in series.values():
        previas = [o for o in grupo if o.fecha[:10] <= fecha[:10]]
        if previas:
            out.append(max(previas, key=lambda o: o.fecha))
        else:
            earliest = min(grupo, key=lambda o: o.fecha)
            out.append(replace(earliest, posterior=_days(earliest.fecha, fecha) > ventana))
    return out


def _a_la_fecha(o: Obs, fecha: str, indice: dict[str, float],
                publicaciones: dict[str, str] | None = None) -> Obs:
    """Lleva el precio al mes de la compra: factor I(mes_compra) / I(mes_observación) (prime P20).

    Mismo mes: sin ajuste. Si falta el índice de alguno de los dos meses NO se sustituye por
    otro mes: queda sin ajustar y rotulado ("ajuste no disponible" no es inflación cero).
    `publicaciones` (modo "disponible_a_fecha", P20/#45): un mes cuyo IPC se publicó DESPUÉS de
    `fecha` cuenta como "índice no disponible" — no se usa el IPC definitivo de hoy, se usa lo
    que se sabía a la fecha de la compra.
    """
    if _month(o.fecha) == _month(fecha):
        return o
    a, b = indice.get(_month(o.fecha)), indice.get(_month(fecha))
    if publicaciones is not None:
        if a is not None and not (publicaciones.get(_month(o.fecha)) and publicaciones[_month(o.fecha)] <= fecha):
            a = None
        if b is not None and not (publicaciones.get(_month(fecha)) and publicaciones[_month(fecha)] <= fecha):
            b = None
    if not a or not b:
        return replace(o, precio_original=o.precio_ub, descripcion=o.descripcion + " [sin ajuste IPC: índice no disponible]")
    factor = b / a
    return replace(o, precio_ub=o.precio_ub * factor, precio_original=o.precio_ub, factor_ipc=factor,
                   descripcion=o.descripcion + f" [llevado al mes de la compra por IPC x{factor:.3f}]")


def _aplicar_fallback_programa(obs: list[Obs]) -> list[Obs]:
    """canal "programa" (Precios Cuidados/Justos/Máximos, sources/precios_programa.py) es un
    dato de mercado MÁS, pero SOLO cuando no hay nada mejor en la fecha (jdemonasterio
    2026-10-12): en el mercado libre las empresas suelen querer cobrar MÁS que el precio
    pactado/techo, no menos, así que funciona más como un piso que como el mínimo real.

    Si hay cualquier observación mayorista o minorista entre las candidatas, se descartan las
    de programa (no deben bajar ni subir la referencia si hay un precio de góndola real). Si no
    hay ninguna, las de programa quedan y se tratan como si fueran minoristas en `compare`
    (ref_min_minorista / min_mercado) — pero nunca pueden corroborarse solas: `familia_fuente`
    agrupa todas las ediciones de un mismo programa en una sola familia.
    """
    if any(o.canal in {"mayorista", "minorista"} for o in obs):
        return [o for o in obs if o.canal != "programa"]
    return obs


def select_observations(obs: list[Obs], fecha: str, contenido_estado: float | None,
                        indice: dict[str, float], cfg: dict, exacta: bool = False,
                        gap_maximo: int | None = None, info: dict | None = None,
                        publicaciones: dict[str, str] | None = None) -> tuple[list[Obs], int | None]:
    """Observaciones de mercado usables para una compra en `fecha`.

    Devuelve (observaciones con precio llevado al mes de la compra, antigüedad máxima en
    días de las usadas). Primero la fecha, después la presentación exacta: un envase de
    1 kg de 2025 no puede desplazar a los de 800 g del mes de la compra. `info` recibe
    {"sin_presentacion": True} si había mercado en la fecha pero no del envase exigido.
    Las observaciones "oficial" (INDEC, mensuales) no pasan por acá. `publicaciones`: modo
    "disponible_a_fecha" para el ajuste temporal por IPC (ver `_a_la_fecha`).
    """
    usable, _ = filter_presentation(obs, contenido_estado, False, cfg)
    if not usable:
        return [], None
    ventana = int(cfg.get("ventana_directa_dias", 15))
    candidatas = [o for o in usable if _days(o.fecha, fecha) <= ventana]
    if not candidatas:
        gap_max = int(gap_maximo or cfg.get("gap_maximo_dias", 120))
        nearest_gap = min(_days(o.fecha, fecha) for o in usable)
        if nearest_gap <= gap_max:
            candidatas = [o for o in usable if _days(o.fecha, fecha) == nearest_gap]
        else:
            # Hueco sin datos (ene-jul 2024: el archivo SEPA empieza en ago-2024): si no hay NADA antes y sí
            # después, se infiere hacia atrás con el IPC desde lo primero que aparece (jdemonasterio 2026-10-12,
            # MEJORAS #46). Rotulado "inferido", confianza baja, nunca rojo.
            gap_inf = int(cfg.get("gap_inferencia_dias", 240))
            antes = [o for o in usable if o.fecha[:10] <= fecha[:10] and _days(o.fecha, fecha) <= gap_inf]
            despues = [o for o in usable if o.fecha[:10] > fecha[:10]]
            g = min((_days(o.fecha, fecha) for o in despues), default=None)
            if antes or g is None or g > gap_inf:
                return [], nearest_gap
            candidatas = [o for o in despues if _days(o.fecha, fecha) == g]
            if info is not None:
                info["inferido"] = candidatas[0].fecha[:10]
    elegidas = _as_of_por_serie(candidatas, fecha, ventana)
    if exacta:
        exactas, _ = filter_presentation(elegidas, contenido_estado, True, cfg)
        if not exactas and info is not None:
            info["sin_presentacion"] = True
        elegidas = exactas
    if not elegidas:
        return [], None
    elegidas = _aplicar_fallback_programa(elegidas)
    if not elegidas:
        return [], None
    return ([_a_la_fecha(o, fecha, indice, publicaciones) for o in elegidas],
            max(_days(o.fecha, fecha) for o in elegidas))


def indec_reference(obs: list[Obs], fecha: str) -> Obs | None:
    """Precio promedio oficial del mes de la compra (o del último mes previo, <= 2 meses)."""
    month = _month(fecha)
    oficiales = sorted((o for o in obs if o.canal == "oficial" and o.fecha <= month),
                       key=lambda o: o.fecha)
    if not oficiales:
        return None
    last = oficiales[-1]
    if _days(last.fecha, month) > 62:
        return None
    return last


def percentile(values: list[float], q: float) -> float:
    """Percentil con interpolación lineal (q en 0..1)."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def same_brand(obs: list[Obs], marca: str | None) -> list[Obs]:
    """Observaciones cuya descripción contiene todas las palabras de la marca."""
    words = norm(marca).split() if marca else []
    words = [w for w in words if len(w) > 2 and w not in {"marca", "sin", "s", "a", "srl"}]
    if not words:
        return []
    return [o for o in obs if all(w in tokens(o.descripcion) for w in words)]


# Empresa (id de comercio en SEPA) detrás de cada scraper directo: un scraper y el archivo SEPA
# de la misma cadena son el mismo grupo comercial (prime P16: no duplican independencia).
_SCRAPER_GRUPO = {"carrefour": "sepa:c10"}


def familia_fuente(fuente: str) -> str:
    """Grupo que fija el precio: dos observaciones del mismo grupo NO se corroboran entre sí.

    SEPA, Precios Claros y su espejo comunitario son el mismo dato replicado (decisión
    del LLM prime P1); las sucursales y banderas de una empresa tienen precio central; el
    scraper directo de una cadena es la misma empresa que su archivo SEPA (P16).
    """
    f = (fuente or "").lower()
    if f.startswith("programa:"):
        # "programa:<precios_cuidados|precios_justos|precios_maximos>:<edicion>": todas las
        # ediciones de UN programa son la misma familia (el programa no puede corroborarse a sí
        # mismo con su propia edición de otro mes); dos programas distintos sí son independientes.
        partes = f.split(":")
        return "programa:" + partes[1] if len(partes) > 1 else "programa"
    if f.startswith("sepa_archivo:c"):
        return "sepa:" + f.split(":")[1]
    if f in _SCRAPER_GRUPO:
        return _SCRAPER_GRUPO[f]
    if any(k in f for k in ("sepa", "precios_claros", "preciosclaros")):
        return "sepa"
    for name in ("maxiconsumo", "carrefour", "golomax", "tumayorista", "indec"):
        if name in f:
            return _SCRAPER_GRUPO.get(name, name)
    return f.split(":")[-1]


def _pct(value: float | None, ref: float | None) -> float | None:
    if value is None or not ref:
        return None
    return round((value / ref - 1) * 100, 2)


def referencia_corroborada(pool: list[Obs], tol: float, original: bool = False) -> tuple[float | None, list[Obs]]:
    """El precio más bajo que confirman dos familias de fuentes independientes (prime P16).

    Busca pares de observaciones de familias distintas con max/min <= 1+tol y devuelve el
    menor máximo de esos pares (y el par). Las observaciones posteriores a la fecha no
    corroboran (P21). `original=True` usa el precio sin ajuste por IPC (sensibilidad P20).
    """
    precio = (lambda o: o.precio_original or o.precio_ub) if original else (lambda o: o.precio_ub)
    obs = sorted((o for o in pool if not o.posterior), key=precio)
    best: tuple[float, list[Obs]] | None = None
    for i, a in enumerate(obs):
        pa = precio(a)
        if best and pa >= best[0]:
            break
        for b in obs[i + 1:]:
            pb = precio(b)
            if pb > pa * (1 + tol):
                break
            if familia_fuente(a.fuente) != familia_fuente(b.fuente):
                if not best or pb < best[0]:
                    best = (pb, [a, b])
                break
    return (round(best[0], 2), best[1]) if best else (None, [])


def _gris(res: Resultado, codigo: str, motivo: str) -> Resultado:
    """Bloquea la comparación: sin porcentaje ni referencia principal (las estadísticas de canal y la
    evidencia quedan como exploratorias)."""
    res.semaforo, res.motivo_codigo, res.motivo = "gris", codigo, motivo
    res.ref_principal = res.ref_principal_valor = None
    res.sobreprecio_pct = res.sobreprecio_ajustado_pct = res.sobreprecio_corroborado_pct = None
    res.ref_corroborada = None
    res.exceso_ars = None
    res.confianza = "baja"
    res.obs_ids = {"minimo": [], "par_corroborado": []}
    return res


def compare(precio_estado_ub: float | None, cantidad_base: float | None, fecha: str | None,
            contenido_estado: float | None, obs: list[Obs], indice: dict[str, float],
            cfg: dict, marca: str | None = None, presentacion_exacta: bool = False,
            gap_maximo: int | None = None, indec_principal: bool = True,
            variante: str | None = None, variante_obs=None, estado_atributo: str | None = None,
            bloquear_rojo: str | None = None, variantes_ok: set[str] | None = None,
            plazo_pago_dias: int | None = None, nota_equivalencia: str | None = None,
            marca_exigida: str | None = None, marca_equivalente_ok: bool | None = None,
            publicaciones: dict[str, str] | None = None) -> Resultado:
    """Núcleo puro (sin base): referencia, sobreprecio, confianza y semáforo.

    `plazo_pago_dias`: cuando se conoce el plazo de pago del PLIEGO (sources/comprar.
    parse_plazo_pago), reemplaza el supuesto genérico de `cfg.plazo_pago_meses` para el
    escenario default (amarillo) y se usa como piso del escenario rojo (nunca lo acorta: P2,
    jdemonasterio 2026-10-12 — "perfecto, usá el plazo de pago de la licitación"). Sigue siendo
    el caso raro: en los procesos de COMPR.AR probados en vivo casi nunca aparece (ver
    DESIGN_sobreprecios.md), así que por default esto es None y el comportamiento no cambia.

    `nota_equivalencia`: texto del catálogo (`Producto.nota_equivalencia[variante]`, config/
    productos_ajustes.yml) que se suma al motivo cuando la variante de la compra se comparó
    contra una variante de mercado distinta vía `compatibles` (ej. leche fortificada Ley 25.459
    comparada con leche en polvo común porque la fortificada no se vende al público;
    jdemonasterio 2026-10-12). El semáforo no cambia: es solo una advertencia visible.

    `marca_exigida` / `marca_equivalente_ok` (jdemonasterio 2026-10-12, `pipeline.extract.
    parse_marca_exigida`): `misma_marca` (la mediana de mercado de la marca OFRECIDA, `marca`) solo
    es la referencia PRINCIPAL cuando el pliego EXIGIÓ esa marca sin "o equivalente/similar"
    (`marca_exigida` truthy y `marca_equivalente_ok is False`). Si el pliego aceptó un equivalente,
    o no se encontró ninguna exigencia de marca en el texto, se usa el mínimo de mercado como
    siempre y la marca ofrecida queda solo como contexto (`ref_misma_marca`/`ref_n_misma_marca`
    siguen mostrándose igual). Queda explicado en el motivo.

    `publicaciones` (`comparacion.ipc_modo`, P20/#45, jdemonasterio 2026-10-12): en modo
    "disponible_a_fecha" el ajuste por IPC (financiero y temporal) solo usa meses ya publicados a
    la fecha de la compra, no el IPC definitivo de hoy (ver `_meses_disponibles`). Default
    "retrospectivo" (`publicaciones=None`): comportamiento idéntico al de siempre.
    """
    res = Resultado(precio_estado_ub=precio_estado_ub)
    if precio_estado_ub is None or not fecha:
        res.motivo = "sin precio por unidad base o sin fecha"
        res.motivo_codigo = "sin_precio"
        return res

    # ── Atributo crítico (variante): verificado / no exigido / desconocido / ambiguo (prime P17-P18)
    bloqueo: tuple[str, str] | None = None
    if variante_obs is not None:
        estado = estado_atributo or ("verificado" if variante else "desconocido")
        res.estado_atributo = estado
        if estado in {"desconocido", "ambiguo"} and cfg.get("atributo_desconocido", "gris") == "gris":
            bloqueo = ("atributo_desconocido",
                       f"atributo crítico {estado} (variedad / formulación): no se puede saber contra qué "
                       "comparar; revisar el pliego o la ficha y cargar la variante")
        elif estado in {"verificado", "por_defecto", "verificado (humano)"} and variante:
            aceptadas = variantes_ok or {variante}
            misma = [o for o in obs if o.canal == "oficial" or variante_obs(o.descripcion) in aceptadas]
            if select_observations(misma, fecha, contenido_estado, indice, cfg,
                                   exacta=presentacion_exacta, gap_maximo=gap_maximo,
                                   publicaciones=publicaciones)[0]:
                obs = misma
            elif cfg.get("variante_sin_mercado", "gris") == "gris":
                bloqueo = ("sin_referencia_variante",
                           f"no hay precios de mercado de la variante exigida ({variante}) en la fecha; "
                           "las otras variantes no son referencia")
            else:
                res.estado_atributo = "sin_mercado"

    info: dict = {}
    selected, gap = select_observations(obs, fecha, contenido_estado, indice, cfg,
                                        exacta=presentacion_exacta, gap_maximo=gap_maximo, info=info,
                                        publicaciones=publicaciones)
    if not selected and info.get("sin_presentacion") and not bloqueo:
        bloqueo = ("sin_referencia_presentacion",
                   "la compra exige un envase que el mercado no tiene en la fecha; envases distintos no son referencia")
        # Comparación exploratoria contra envases parecidos (banda): solo sirve para "equivalencia por verificar".
        selected, gap = select_observations(obs, fecha, contenido_estado, indice,
                                            dict(cfg, presentacion_exigida_estricta=False),
                                            exacta=presentacion_exacta, gap_maximo=gap_maximo,
                                            publicaciones=publicaciones)
    tol_pres = float(cfg.get("tolerancia_presentacion", 0.03))
    res.presentacion_exacta = bool(presentacion_exacta and selected and contenido_estado and all(
        o.contenido_base and abs(o.contenido_base - contenido_estado) <= tol_pres * contenido_estado for o in selected))
    ventana = int(cfg.get("ventana_directa_dias", 15))
    # "Precio de HOY" (flujo Investigar, jdemonasterio 2026-10-12): la observación usada es de HOY
    # (fecha real), no un snapshot histórico desactualizado — es la góndola de hoy trasladada hacia
    # atrás por IPC. Se detecta por la fecha real, no por un canal/fuente especial (precios_hoy.py
    # guarda con el mismo canal mayorista/minorista de siempre): funciona apenas esas filas llegan a
    # precios_mercado, sin acoplarse al código de investigar.py (otro agente). Una observación de HOY
    # SIEMPRE corrobora con otra de HOY (no hay riesgo de "promo que todavía no existía": es,
    # literalmente, la góndola de hoy) aunque esté lejísimos de la fecha de la compra — habilitar rojo
    # a esa distancia sigue exigiendo el override explícito `rojo_con_ajuste_ipc_hasta_dias`.
    es_precio_de_hoy = bool(selected) and any(
        _days(o.fecha, dt.date.today().isoformat()) <= ventana for o in selected)
    low, high = float(cfg.get("outlier_bajo", 0.4)), float(cfg.get("outlier_alto", 2.5))
    may = _filter_outliers([o for o in selected if o.canal == "mayorista"], low, high)
    # "programa" solo llega hasta acá (select_observations ya lo descartó) si NO había mayorista
    # ni minorista: se trata como minorista para las referencias (min_minorista / min_mercado).
    mino = _filter_outliers([o for o in selected if o.canal in {"minorista", "programa"}], low, high)
    oficial = indec_reference(obs, fecha)
    res.gap_dias = gap
    if may:
        res.ref_min_mayorista = round(min(o.precio_ub for o in may), 2)
        res.ref_p25_mayorista = round(percentile([o.precio_ub for o in may], 0.25), 2)
        res.ref_mediana_mayorista = round(statistics.median(o.precio_ub for o in may), 2)
        res.ref_n_mayorista = len(may)
    brand = same_brand(may + mino, marca)
    if brand:
        res.ref_misma_marca = round(statistics.median(o.precio_ub for o in brand), 2)
        res.ref_n_misma_marca = len(brand)
    if mino:
        res.ref_min_minorista = round(min(o.precio_ub for o in mino), 2)
        res.ref_mediana_minorista = round(statistics.median(o.precio_ub for o in mino), 2)
        res.ref_n_minorista = len(mino)
    indec_comparable = False
    if oficial:
        res.ref_indec = round(oficial.precio_ub, 2)
        # Envase exigido: ±15% como al mercado; si no, la banda (P19: la banda no reemplaza al envase exigido).
        tol_rel = tol_pres if presentacion_exacta else None
        banda = float(cfg.get("banda_tamano", 2.5))
        indec_comparable = bool(not contenido_estado or (oficial.contenido_base and (
            abs(oficial.contenido_base - contenido_estado) <= tol_rel * contenido_estado if tol_rel
            else contenido_estado / banda <= oficial.contenido_base <= contenido_estado * banda)))

    res.sobreprecio_min_pct = _pct(precio_estado_ub, res.ref_min_mayorista)
    res.sobreprecio_minorista_pct = _pct(precio_estado_ub, res.ref_mediana_minorista)
    res.sobreprecio_indec_pct = _pct(precio_estado_ub, res.ref_indec)

    options = {
        "misma_marca": res.ref_misma_marca,
        "min_mercado": min((v for v in (res.ref_min_mayorista, res.ref_min_minorista) if v), default=None),
        "min_minorista": res.ref_min_minorista,
        "p25_mayorista": res.ref_p25_mayorista,
        "mediana_mayorista": res.ref_mediana_mayorista,
        "min_mayorista": res.ref_min_mayorista,
        "mediana_minorista": res.ref_mediana_minorista,
        # INDEC con presentación solo inferida (yerba, leche en polvo) o de otro envase: se muestra, no manda.
        "indec": res.ref_indec if (indec_principal and indec_comparable) else None,
    }
    pools = {"misma_marca": brand, "min_mercado": may + mino, "min_mayorista": may, "min_minorista": mino,
             "p25_mayorista": may, "mediana_mayorista": may, "mediana_minorista": mino}
    # misma_marca solo es PRINCIPAL si el pliego exigió esa marca sin "o equivalente/similar"
    # (jdemonasterio 2026-10-12); si no, la marca ofrecida queda como contexto (sigue en `options`/
    # `ref_misma_marca`) y manda el mínimo de mercado de siempre.
    # `is not None and not ...` en vez de `is False`: desde la base SQLite vuelve 0/1 (INTEGER), no
    # un bool de Python, y `0 is False` da False.
    marca_requerida_sin_equivalente = bool(marca_exigida) and marca_equivalente_ok is not None \
        and not marca_equivalente_ok
    order = ([
        "misma_marca"] if marca_requerida_sin_equivalente else []) + [
        cfg.get("referencia_principal", "min_mercado"), "min_mercado", "min_mayorista",
        "min_minorista", "p25_mayorista", "mediana_mayorista", "mediana_minorista", "indec"]
    for name in order:
        if options.get(name):
            res.ref_principal, res.ref_principal_valor = name, options[name]
            break
    pool = pools.get(res.ref_principal or "", [])
    tol = float(cfg.get("corroboracion_tolerancia", 0.02))
    # Precio de HOY: no corre la exclusión de "posterior" (ver `es_precio_de_hoy` arriba) — dos
    # observaciones de HOY de familias distintas corroboran entre sí aunque la compra sea vieja.
    pool_corrob = [replace(o, posterior=False) for o in pool] if es_precio_de_hoy else pool
    corroborada, par = referencia_corroborada(pool_corrob, tol) if pool_corrob else (None, [])
    if corroborada is not None:
        res.ref_corroborada = max(corroborada, res.ref_principal_valor or 0)
    shown = par + brand + [o for o in sorted(may + mino, key=lambda o: o.precio_ub) if o not in brand and o not in par]
    res.evidencia = [asdict(o) for o in shown[:60]] + ([asdict(oficial)] if oficial else [])
    # IDs de precios_mercado que FORMAN cada referencia (jdemonasterio 2026-10-12): "minimo" son las
    # observaciones del pool con el precio que fijó ref_principal_valor (en una referencia por mínimo
    # coincide siempre; en una por mediana puede no coincidir con ningún id puntual, y queda vacío);
    # "par_corroborado" son las 2 que confirman ref_corroborada. Las observaciones oficiales/de test sin
    # id (None) se filtran: no sirven como referencia trazable a una fila concreta.
    res.obs_ids = {
        "minimo": sorted({o.id for o in pool if o.id is not None
                          and res.ref_principal_valor is not None
                          and abs(o.precio_ub - res.ref_principal_valor) < 1e-6}),
        "par_corroborado": sorted({o.id for o in par if o.id is not None}),
    }

    if (bloqueo or (not selected and info.get("sin_presentacion"))) and not res.evidencia:
        # Gris: se muestra qué había en el mercado en la fecha (otras variantes / envases) para que el
        # revisor vea que el gris no esconde una comparación válida (juez externo, renglón 1761).
        explorar, _ = select_observations(obs, fecha, contenido_estado, indice,
                                          dict(cfg, presentacion_exigida_estricta=False), exacta=False,
                                          gap_maximo=gap_maximo, publicaciones=publicaciones)
        res.evidencia = [dict(asdict(o), exploratoria=True) for o in sorted(explorar, key=lambda o: o.precio_ub)[:15]]
    # Equivalencia no verificada (variedad, composición o envase): gris, salvo que la brecha contra el mercado
    # más parecido llegue al umbral de rojo -> amarillo destacado "equivalencia por verificar", para que dispare
    # la revisión humana (jdemonasterio 2026-10-12, P7). Flag: comparacion.equivalencia_no_verificada.
    marcar_si_rojo = cfg.get("equivalencia_no_verificada", "amarillo_si_rojo") == "amarillo_si_rojo"
    if bloqueo and (not marcar_si_rojo or not res.ref_principal_valor):
        return _gris(res, *bloqueo)
    if not res.ref_principal_valor:
        res.motivo = ("sin observaciones de mercado comparables"
                      + (f" (la más cercana a {gap} días)" if gap else "")
                      + ("; el promedio INDEC es de otra presentación" if oficial and not indec_comparable else ""))
        res.motivo_codigo = "sin_datos"
        return res

    # ── Plazo de pago: escenario por defecto (decide amarillo) y el más largo plausible (decide rojo).
    inflation = max(monthly_inflation(indice, fecha, publicaciones) or 0.0, 0.0)
    plazo_fuente: str | None = None
    if plazo_pago_dias:
        plazo = plazo_pago_dias / 30.0
        # El plazo del pliego nunca ACORTA el escenario rojo (piso, no reemplazo): si encima es
        # más largo que el supuesto genérico, manda el real.
        plazo_rojo = max(plazo, float(cfg.get("plazo_escenarios_rojo_meses", 2.0)))
        plazo_fuente = f"plazo de pago del pliego: {plazo_pago_dias} días"
    else:
        plazo = float(cfg.get("plazo_pago_meses", 1.0))
        plazo_rojo = float(cfg.get("plazo_escenarios_rojo_meses", 2.0))
    factor = lambda m: (1 + inflation) ** m   # compuesto, no lineal (riesgo señalado por el prime)
    res.ajuste_financiero_pct = round((factor(plazo) - 1) * 100, 2)
    res.sobreprecio_pct = _pct(precio_estado_ub, res.ref_principal_valor)
    res.sensibilidad = {f"{m}m": _pct(precio_estado_ub / factor(m), res.ref_principal_valor) for m in (0, 1, 2, 3)}
    adjusted_price = precio_estado_ub / factor(plazo)
    res.sobreprecio_ajustado_pct = _pct(adjusted_price, res.ref_principal_valor)
    if res.ref_corroborada:
        res.sobreprecio_corroborado_pct = _pct(precio_estado_ub / factor(plazo_rojo), res.ref_corroborada)
    if cantidad_base is None:
        res.exceso_ars = None
    elif res.sobreprecio_ajustado_pct and res.sobreprecio_ajustado_pct > 0:
        res.exceso_ars = round((adjusted_price - res.ref_principal_valor) * cantidad_base, 2)
    else:
        res.exceso_ars = 0.0

    # ── Confianza: sobre las observaciones que FORMAN la referencia, no sobre todo el pool (riesgo del prime).
    # (`ventana` y `es_precio_de_hoy` ya se calcularon arriba, antes de armar may/mino/pool.)
    if res.ref_principal == "indec":
        res.confianza = "media"
    elif gap is not None and gap > ventana:
        res.confianza = "baja"
    elif res.ref_corroborada:
        res.confianza = "alta"
    else:
        res.confianza = "media"

    ratio = precio_estado_ub / res.ref_principal_valor
    max_ratio = float(cfg.get("plausibilidad_max_ratio", 4.0))
    if ratio > max_ratio or ratio < 1 / max_ratio:
        res.semaforo = "gris"
        res.motivo_codigo = "revisar_unidad"
        res.motivo = (f"revisar_unidad: el Estado pagó {ratio:.1f}x la referencia; verificar referencia y unidad "
                      "(bulto vs unidad, producto distinto o un precio real muy alto) antes de concluir nada")
        return res

    res.fuentes_independientes_min = (len({familia_fuente(o.fuente) for o in pool
                                           if res.ref_corroborada and abs(o.precio_ub - res.ref_corroborada)
                                           <= tol * res.ref_corroborada}) if res.ref_corroborada
                                      else (1 if res.ref_principal_valor else 0))

    # ── Semáforo
    rojo, amarillo = float(cfg.get("umbral_rojo_pct", 20)), float(cfg.get("umbral_amarillo_pct", 8))
    # Hasta cuántos días de antigüedad un precio trasladado por IPC puede habilitar rojo
    # (jdemonasterio 2026-10-12): default 120 (= gap_maximo_dias, todo lo que el comparador ya usa
    # como referencia puede ser rojo); 15 restaura la regla estricta original (gap > ventana_directa
    # nunca rojo, solo contexto).
    rojo_con_ipc_hasta = int(cfg.get("rojo_con_ajuste_ipc_hasta_dias", 120))
    sp = res.sobreprecio_ajustado_pct
    sin_rojo: list[str] = []
    if res.ref_principal == "indec" and not cfg.get("indec_solo_habilita_rojo", False):
        sin_rojo.append("la única referencia es el promedio INDEC")
    elif not res.ref_corroborada:
        con_posteriores, _ = referencia_corroborada([replace(o, posterior=False) for o in pool], tol)
        sin_rojo.append("el mínimo solo se confirma con precios posteriores a la compra" if con_posteriores
                        else "el mínimo no está confirmado por dos fuentes independientes")
    elif res.sobreprecio_corroborado_pct < rojo:
        sin_rojo.append(f"contra el mínimo confirmado y {plazo_rojo:g} meses de plazo la brecha es "
                        f"{res.sobreprecio_corroborado_pct:+.1f}%")
    else:
        sin_orig, _ = referencia_corroborada(pool, tol, original=True)
        if sin_orig and _pct(precio_estado_ub / factor(plazo_rojo), max(sin_orig, res.ref_principal_valor)) < rojo:
            sin_rojo.append("el resultado depende del ajuste por IPC de precios de otro mes")
    if info.get("inferido"):
        sin_rojo.append(f"precio inferido hacia atrás desde el mercado del {info['inferido']} con el IPC")
    elif gap is not None and gap > rojo_con_ipc_hasta:
        etiqueta = "precio de HOY" if es_precio_de_hoy else "precio de mercado"
        sin_rojo.append(f"{etiqueta} a {gap} días, más allá de los {rojo_con_ipc_hasta} días permitidos "
                        "con ajuste por IPC para habilitar rojo (contexto)")
    if res.estado_atributo == "sin_mercado":
        sin_rojo.append("variedad no verificada contra el mercado")
    if bloquear_rojo:
        sin_rojo.append(bloquear_rojo)
    if bloqueo:
        if sp < rojo:
            return _gris(res, *bloqueo)
        res.semaforo, res.motivo_codigo = "amarillo", "equivalencia_por_verificar"
        res.confianza = "baja"
        res.motivo = (f"posible sobreprecio: {sp:+.1f}% vs {res.ref_principal.replace('_', ' ')} de productos parecidos, "
                      f"pero la equivalencia no está verificada ({bloqueo[1]}). Revisar el pliego antes de concluir")
        return res
    res.motivo_codigo = "ok"
    if sp >= amarillo and not sin_rojo and res.sobreprecio_corroborado_pct is not None \
            and res.sobreprecio_corroborado_pct >= rojo:
        res.semaforo = "rojo"
    elif sp >= amarillo:
        res.semaforo = "amarillo"
    else:
        res.semaforo = "verde"

    notas = []
    if plazo_fuente:
        notas.append(plazo_fuente)
    if res.presentacion_exacta:
        notas.append("misma presentación")
    if variante and res.estado_atributo in {"verificado", "por_defecto"}:
        notas.append(f"misma variedad ({variante})")
    elif res.estado_atributo == "no_exigido":
        notas.append("variedad no exigida en la ficha")
    if nota_equivalencia and res.estado_atributo in {"verificado", "por_defecto", "verificado (humano)"}:
        notas.append(nota_equivalencia)
    if marca_requerida_sin_equivalente:
        notas.append(f"el pliego exige la marca {marca_exigida}: referencia = misma marca")
    elif marca_exigida and marca_equivalente_ok:
        notas.append(f"el pliego pide tipo/marca {marca_exigida} \"o equivalente\": no se exige esa "
                     "marca, se compara contra el mínimo de mercado (la ofrecida queda de contexto)")
    minimo = min(pool, key=lambda o: o.precio_ub) if pool else None
    if minimo and minimo.posterior:
        notas.append("el mínimo observado es posterior a la fecha")
    programas = sorted({tuple(o.fuente.split(":", 2)[1:3]) for o in pool if o.fuente.startswith("programa:")})
    for prog, ed in programas:
        notas.append(f"referencia: precio de programa {prog} ({ed})")
    if res.ref_corroborada:
        notas.append(f"{res.sobreprecio_corroborado_pct:+.1f}% vs el mínimo confirmado por 2 fuentes "
                     f"(${res.ref_corroborada:,.2f}) con {plazo_rojo:g} meses de plazo")
    if info.get("inferido"):
        notas.insert(0, f"PRECIO INFERIDO: no hay datos de mercado de esa fecha; se usa el del {info['inferido']} "
                        "llevado hacia atrás con el IPC")
    if sp >= rojo and res.semaforo != "rojo":
        notas.append("sin rojo: " + "; ".join(sin_rojo))
    elif gap is not None and gap > ventana and not info.get("inferido"):
        # Advertencia explícita y siempre visible (también cuando SÍ da rojo con ajuste por IPC,
        # jdemonasterio 2026-10-12): el precio de mercado no es del mismo día, se trasladó con el IPC.
        etiqueta = (f"precio de HOY trasladado por IPC a la fecha de la compra" if es_precio_de_hoy
                   else f"precio de mercado trasladado por IPC desde {gap} días")
        notas.append(f"{etiqueta} (método: I(mes compra)/I(mes observación))")
    res.motivo = (f"{sp:+.1f}% vs {res.ref_principal.replace('_', ' ')} "
                  f"(plazo supuesto {plazo:g} mes: {res.ajuste_financiero_pct:.1f}%, aprox. IPC; confianza {res.confianza}"
                  + (", " + ", ".join(notas) if notas else "") + ")")
    return res


# ── Orquestación con la base ────────────────────────────────────────────────

def load_index(conn: sqlite3.Connection, serie: str) -> dict[str, float]:
    return {r["fecha"]: r["valor"] for r in
            conn.execute("SELECT fecha, valor FROM indices WHERE serie=?", (serie,))}


def load_index_publicaciones(conn: sqlite3.Connection, serie: str) -> dict[str, str]:
    """{mes: fecha_publicacion} de una serie de `indices` (P20/#45, `sources/indec.py`). Solo se
    carga cuando `comparacion.ipc_modo` es "disponible_a_fecha" (ver `run()`): en el modo default
    no hace falta ni se toca el comportamiento de siempre."""
    return {r["fecha"]: r["fecha_publicacion"] for r in
            conn.execute("SELECT fecha, fecha_publicacion FROM indices WHERE serie=? "
                        "AND fecha_publicacion IS NOT NULL", (serie,))}


def load_observations(conn: sqlite3.Connection, producto_id: str) -> list[Obs]:
    rows = conn.execute(
        "SELECT id, fuente, canal, fecha, precio_unidad_base, contenido_base, descripcion, url "
        "FROM precios_mercado WHERE producto_id=? AND precio_unidad_base IS NOT NULL AND cuarentena IS NULL "
        # Revisión humana de precios de programa (webui /precios-programa, validaciones_programa.py):
        # un ✗ de un humano descarta la fila del comparador; el juicio del juez LLM NUNCA excluye
        # solo (jdemonasterio 2026-10-12, "un resultado falso es peor que ninguno").
        "AND NOT EXISTS (SELECT 1 FROM validaciones_programa v WHERE v.fuente = precios_mercado.fuente "
        "AND v.obs_key = precios_mercado.obs_key AND v.autor = 'humano' AND v.veredicto = 'no')",
        (producto_id,)).fetchall()
    return [Obs(r["fuente"], r["canal"], r["fecha"], r["precio_unidad_base"], r["contenido_base"],
                r["descripcion"] or "", r["url"], id=r["id"]) for r in rows]


def run(conn: sqlite3.Connection, proceso_ids: list[int] | None = None,
        gap_maximo: int | None = None, rojo_con_ajuste_ipc_hasta_dias: int | None = None) -> str:
    """Recalcula comparaciones para todos los renglones con producto y precio por unidad base.

    `rojo_con_ajuste_ipc_hasta_dias`: override puntual del tope de `comparacion.
    rojo_con_ajuste_ipc_hasta_dias` (default 120, ver `compare()`). Pensado para el flujo de
    Investigar (`investigar.py`, otro agente): una compra vieja solo tiene "precio de HOY" como
    referencia, a mucho más de 120 días — con `gap_maximo` ya overrideado ahí a 3 años, este
    parámetro deja que esa referencia también habilite rojo si corrobora (jdemonasterio 2026-10-12).
    """
    cfg = config.settings().get("comparacion") or {}
    if rojo_con_ajuste_ipc_hasta_dias is not None:
        cfg = dict(cfg, rojo_con_ajuste_ipc_hasta_dias=rojo_con_ajuste_ipc_hasta_dias)
    idx_cfg = config.settings().get("indices") or {}
    catalog: dict[str, Producto] = load_catalog()
    indices = {name: load_index(conn, serie) for name, serie in idx_cfg.items()}
    # IPC "disponible a la fecha" (P20/#45, jdemonasterio 2026-10-12): solo se carga la fecha de
    # publicación de cada mes cuando el modo la necesita — en "retrospectivo" (default) no se toca
    # nada de lo de siempre.
    publicaciones_por_indice = ({name: load_index_publicaciones(conn, serie) for name, serie in idx_cfg.items()}
                                if cfg.get("ipc_modo", "retrospectivo") == "disponible_a_fecha" else {})
    where = "r.producto_id IS NOT NULL"
    params: list = []
    if proceso_ids:
        where += f" AND r.proceso_id IN ({','.join('?' for _ in proceso_ids)})"
        params.extend(proceso_ids)
    rows = conn.execute(
        # Los precios se fijan al ofertar: se compara a la fecha de apertura cuando existe (riesgo del prime).
        f"SELECT r.*, p.plazo_pago_dias AS plazo_pago_dias, "
        f"COALESCE(p.fecha_apertura, p.fecha_adjudicacion, p.fecha_publicacion) AS fecha "
        f"FROM renglones r JOIN procesos p ON p.id = r.proceso_id WHERE {where}", params).fetchall()
    obs_cache: dict[str, list[Obs]] = {}
    counts = {"rojo": 0, "amarillo": 0, "verde": 0, "gris": 0}
    for r in rows:
        # Correcciones manuales vigentes (módulo de revisión humana): se resuelven ANTES de
        # armar la comparación, así compare() (el núcleo puro) no sabe que hubo una corrección,
        # solo recibe el dato ya corregido (ver revision_humana.aplicar_correcciones).
        renglon, notas_manuales = aplicar_correcciones(conn, r)
        pid = renglon["producto_id"]
        if pid not in obs_cache:
            obs_cache[pid] = load_observations(conn, pid)
        prod = catalog.get(pid)
        indice_nombre = "ipc_alimentos" if prod and prod.rubro in {"alimentos", "bebidas"} else "ipc_general"
        indice = indices.get(indice_nombre, {})
        publicaciones = publicaciones_por_indice.get(indice_nombre)
        cantidad_base = (renglon["cantidad"] * renglon["contenido_base"]
                        if renglon["cantidad"] and renglon["contenido_base"] else None)
        size = parse_size(renglon["descripcion"])
        exacta = size is not None
        # Pack de 50 jabones de 180 g: la presentación comparable es la barra, no el pack de 9 kg.
        # (salvo que el envase ya haya sido corregido a mano: ese valor manda tal cual).
        presentacion = renglon["contenido_base"]
        envase_manual = "contenido_base" in renglon["_campos_corregidos"] or "unidad_base" in renglon["_campos_corregidos"]
        if not envase_manual and size and size.pack_count > 1 and size.base()[1] == renglon["unidad_base"] \
                and renglon["contenido_base"]:
            presentacion = size.base()[0] / size.pack_count
        vfn = (lambda d, _p=prod: variante_de(_p, d)) if prod and prod.variantes else None
        if renglon["_variante_manual"]:
            var, estado_attr = renglon["_variante_manual"], "verificado (humano)"
        else:
            var, estado_attr = variante_estado(prod, renglon["descripcion"])
        # Una inconsistencia material del aviso bloquea el rojo, no es solo una nota (riesgo del prime).
        bloquear = ("el monto total del aviso no cierra con cantidad x precio"
                    if renglon["alertas"] and "monto_inconsistente" in renglon["alertas"] else None)
        # Observaciones que un humano marcó "no es el mismo producto" o "precio/tamaño mal leído"
        # PARA ESTE RENGLÓN (P16, revisión humana): se sacan del pool antes de comparar. compare()
        # sigue sin saber que hubo una exclusión, solo recibe un pool más chico.
        obs_renglon = aplicar_exclusiones_mercado(conn, r["id"], obs_cache[pid])
        res = compare(renglon["precio_unidad_base"], cantidad_base, renglon["fecha"], presentacion,
                      obs_renglon, indice, cfg, marca=renglon["marca"], presentacion_exacta=exacta,
                      gap_maximo=gap_maximo,
                      indec_principal=bool(prod and prod.indec.get("verificado") == "oficial"),
                      variante=var, variante_obs=vfn, estado_atributo=estado_attr, bloquear_rojo=bloquear,
                      variantes_ok=variantes_aceptadas(prod, var), plazo_pago_dias=r["plazo_pago_dias"],
                      nota_equivalencia=(prod.nota_equivalencia.get(var) if prod and var else None),
                      marca_exigida=renglon["marca_exigida"], marca_equivalente_ok=renglon["marca_equivalente_ok"],
                      publicaciones=publicaciones)
        if notas_manuales:
            res.motivo = (res.motivo + "; " if res.motivo else "") + "dato corregido a mano: " + ", ".join(notas_manuales)
        row = asdict(res)
        row["evidencia_json"] = json.dumps(row.pop("evidencia"), ensure_ascii=False)
        row["sensibilidad_json"] = json.dumps(row.pop("sensibilidad"), ensure_ascii=False)
        row["obs_ids_json"] = json.dumps(row.pop("obs_ids"), ensure_ascii=False)
        row.update(renglon_id=r["id"], fecha_compra=renglon["fecha"], computed_at=db.now_iso())
        row.update(brecha_ofertas(conn, r["proceso_id"], r["nro_renglon"], renglon["precio_unitario"]))
        # Reemplazo íntegro: un upsert parcial podía dejar porcentajes viejos de otra corrida.
        conn.execute("DELETE FROM comparaciones WHERE renglon_id=?", (r["id"],))
        db.upsert(conn, "comparaciones", row, ("renglon_id",))
        counts[res.semaforo] = counts.get(res.semaforo, 0) + 1
    # Renglones SIN producto comparable (medicamentos, servicios...) igual reciben el indicador
    # de ofertas: no necesita precios de mercado.
    where_sin = "r.producto_id IS NULL" + (f" AND r.proceso_id IN ({','.join('?' for _ in proceso_ids)})" if proceso_ids else "")
    for r in conn.execute(f"SELECT r.id, r.proceso_id, r.nro_renglon, r.precio_unitario FROM renglones r WHERE {where_sin}",
                          proceso_ids or []).fetchall():
        info = brecha_ofertas(conn, r["proceso_id"], r["nro_renglon"], r["precio_unitario"])
        # Un renglón que perdió el producto no puede conservar la comparación de mercado vieja.
        conn.execute("DELETE FROM comparaciones WHERE renglon_id=?", (r["id"],))
        if info["n_ofertas"]:
            db.upsert(conn, "comparaciones", dict(info, renglon_id=r["id"], semaforo="gris", motivo_codigo="fuera_alcance",
                                                  motivo="sin producto comparable en el catálogo; ver ofertas del renglón",
                                                  computed_at=db.now_iso()), ("renglon_id",))
    # Estado documental (dictamen/adjudicado/contratado/desconocido, MEJORAS #29): se recalcula cada
    # vez que se recalculan sobreprecios, para todos los renglones del alcance (con o sin producto
    # comparable), no solo los que entraron al loop de arriba.
    actualizar_estado_documental(conn, proceso_ids)
    conn.commit()
    from ..revision import revocar_si_cambio
    revocados = revocar_si_cambio(conn)
    return ((f"{revocados} publicables revocados por cambios · " if revocados else "") +
            f"{len(rows)} renglones comparados: {counts['rojo']} rojo, {counts['amarillo']} amarillo, "
            f"{counts['verde']} verde, {counts['gris']} gris")
