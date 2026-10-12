"""Catálogo canónico de productos y matcher por reglas.

El matcher por reglas es la primera pasada (gratis, determinística, auditable):
cada producto de config/productos.yml declara frases que lo identifican y
frases que lo descartan. Si exactamente un producto gana, listo; si empatan
dos o ninguno matchea, el renglón queda para el LLM (pipeline/match.py).

Las reglas nunca "adivinan": un empate es una señal de que el texto es ambiguo
("aceite girasol y oliva"), no algo para desempatar al azar.
"""
from __future__ import annotations

import re

import json
import sqlite3
from dataclasses import dataclass, field

from . import abreviaturas, config
from .textnorm import norm, tokens
from .units import parse_size


@dataclass(frozen=True)
class Producto:
    id: str
    nombre: str
    rubro: str
    unidad_base: str
    tamano_referencia: float | None      # en unidad_base (0.8 = 800 g si unidad_base es kg)
    incluye: tuple[tuple[str, ...], ...]
    excluye: tuple[tuple[str, ...], ...]
    indec: dict = field(default_factory=dict)
    variantes: dict = field(default_factory=dict)        # nombre -> tuple de frases
    variante_default: str | None = None
    # Variante de la compra -> variantes del mercado que la satisfacen ("guisero" acepta cualquier forma corta).
    compatibles: dict = field(default_factory=dict)
    # El atributo no puede quedar "no exigido": sin dato en la compra, gris (lavandina: cloro activo).
    atributo_obligatorio: bool = False

    def indec_contenido_base(self) -> float | None:
        """Contenido (en unidad_base) de la presentación que releva INDEC."""
        pres = (self.indec or {}).get("presentacion")
        size = parse_size(f"x {pres}") if pres else None
        return size.base()[0] if size else None


@dataclass(frozen=True)
class Match:
    producto_id: str | None
    score: float
    metodo: str
    razon: str


def _phrases(items) -> tuple[tuple[str, ...], ...]:
    out = []
    for item in items or []:
        words = tuple(norm(str(item)).split())
        if words:
            out.append(words)
    return tuple(out)


def _ajustes() -> dict:
    try:
        return (config.load_yaml("productos_ajustes.yml").get("productos") or {})
    except FileNotFoundError:
        return {}


def load_catalog(raw: dict | None = None, con_ajustes: bool = True) -> dict[str, Producto]:
    """Catálogo de config/productos.yml + ajustes de config/productos_ajustes.yml (si existe).

    Los ajustes suman frases de excluir/incluir y reemplazan variantes: es una capa aparte para
    poder revisar (y deshacer) lo que propuso la auditoría sin tocar el catálogo base.
    """
    data = raw if raw is not None else config.load_yaml("productos.yml")
    ajustes = _ajustes() if (con_ajustes and raw is None) else {}
    globales = _phrases(data.get("exclusiones_globales"))
    catalog = {}
    for pid, spec in (data.get("productos") or {}).items():
        aj = ajustes.get(pid) or {}
        if aj:
            spec = dict(spec)
            spec["excluye"] = list(spec.get("excluye") or []) + list(aj.get("excluye_extra") or [])
            spec["incluye"] = list(spec.get("incluye") or []) + list(aj.get("incluye_extra") or [])
            if aj.get("variantes"):
                spec["variantes"] = aj["variantes"]
                spec["variante_default"] = aj.get("variante_default")
            for k in ("compatibles", "atributo_obligatorio"):
                if k in aj:
                    spec[k] = aj[k]
        ref = spec.get("tamano_referencia")
        size = parse_size(f"x {ref}") if ref else None
        catalog[pid] = Producto(
            id=pid,
            nombre=spec["nombre"],
            rubro=spec.get("rubro", "otros"),
            unidad_base=spec["unidad_base"],
            tamano_referencia=size.base()[0] if size else None,
            incluye=_phrases(spec.get("incluye")),
            excluye=_phrases(spec.get("excluye")) + globales,
            indec=spec.get("indec") or {},
            variantes={k: _phrases(v) for k, v in (spec.get("variantes") or {}).items()},
            variante_default=spec.get("variante_default"),
            compatibles={k: tuple(v) for k, v in (spec.get("compatibles") or {}).items()},
            atributo_obligatorio=bool(spec.get("atributo_obligatorio")),
        )
    return catalog


def _contains(toks: set[str], phrase: tuple[str, ...]) -> bool:
    return all(word in toks for word in phrase)


def match_rules(descripcion: str, catalog: dict[str, Producto]) -> Match:
    """Producto canónico por reglas. producto_id=None si no hay ganador único."""
    # MEJORAS #1 (P22): suma tokens de abreviaturas APROBADAS (config/abreviaturas.yml) antes de
    # matchear — aditivo, nunca borra texto (ver abreviaturas.expandir).
    toks = tokens(abreviaturas.expandir(descripcion))
    if not toks:
        return Match(None, 0.0, "reglas", "descripción vacía")
    scored: list[tuple[int, str, str]] = []
    excluded: list[str] = []
    for prod in catalog.values():
        hits = [p for p in prod.incluye if _contains(toks, p)]
        if not hits:
            continue
        blockers = [p for p in prod.excluye if _contains(toks, p)]
        if blockers:
            excluded.append(f"{prod.id} (excluye '{' '.join(blockers[0])}')")
            continue
        best = max(hits, key=len)
        scored.append((len(best), prod.id, " ".join(best)))
    if not scored:
        why = "ninguna regla matchea" + (f"; descartados: {', '.join(excluded)}" if excluded else "")
        return Match(None, 0.0, "reglas", why)
    scored.sort(reverse=True)
    top_len = scored[0][0]
    winners = [s for s in scored if s[0] == top_len]
    if len(winners) > 1:
        names = ", ".join(w[1] for w in winners)
        return Match(None, 0.0, "reglas", f"empate entre {names}")
    length, pid, phrase = scored[0]
    # Frase de 2+ palabras es bastante específica; una palabra sola ("arroz"), menos.
    score = 0.9 if length >= 2 else 0.75
    return Match(pid, score, "reglas", f"frase '{phrase}'")


def _segmentos(descripcion: str | None) -> list[list[str]]:
    """Palabras de la descripción por segmento: en una ficha SIByS cada campo "CLAVE: valor" es un segmento
    (así "TIPO: CON PALO" no choca con el "SIN" de otro campo); en texto libre, uno solo."""
    if not descripcion:
        return []
    if clase_sibys(descripcion):
        _, _, resto = descripcion.partition(";")
        partes = re.split(r",\s*(?=[^,:]{1,40}:)", resto)
        return [norm(x).split() for x in partes if norm(x)]
    return [norm(descripcion).split()]


def _frase_en(seg: list[str], frase: tuple[str, ...]) -> bool:
    """La frase aparece SEGUIDA (palabras contiguas y en orden) dentro del segmento (prime P22)."""
    n = len(frase)
    return any(tuple(seg[i:i + n]) == frase for i in range(len(seg) - n + 1))


def _resolver_variante(prod: Producto, descripcion: str | None) -> tuple[str | None, str]:
    """(variante, estado) con estado verificado | ambiguo | desconocido, solo por frases encontradas."""
    segs = _segmentos(descripcion)
    hits = [name for name, phrases in prod.variantes.items()
            if any(_frase_en(seg, p) for seg in segs for p in phrases)]
    # Una variante "*_sin_detalle" ("fortificada" sola) pierde contra una específica ("hierro zinc y
    # vitamina c"). Cualquier otro choque queda ambiguo: no se elige por largo de frase (prime P22).
    if len(hits) > 1:
        hits = [n for n in hits if not n.endswith("_sin_detalle")] or hits
    if len(hits) == 1:
        return hits[0], "verificado"
    return None, ("ambiguo" if hits else "desconocido")


def variante_estado(prod: Producto | None, descripcion: str | None) -> tuple[str | None, str | None]:
    """Variante de una COMPRA y su estado (decisión del LLM prime P17).

    - verificado: la descripción dice la variante;
    - ambiguo: dice dos a la vez (o una negación que el macheo por palabras no resuelve);
    - no_exigido: es una ficha SIByS (la especificación completa del renglón) y no menciona
      ninguna variante: el Estado no la exigió (interpretación de Claude, pendiente de check);
    - desconocido: texto libre que no la dice (puede ser un título abreviado).
    (None, None) si el producto no tiene variantes.
    """
    if not prod or not prod.variantes:
        return None, None
    valor, estado = _resolver_variante(prod, descripcion)
    if estado == "desconocido":
        if prod.variante_default:
            return prod.variante_default, "por_defecto"
        if clase_sibys(descripcion) and not prod.atributo_obligatorio:
            return None, "no_exigido"
    return valor, estado


def variantes_aceptadas(prod: Producto | None, variante_compra: str | None) -> set[str]:
    """Variantes de mercado que satisfacen la de la compra: ella misma + las compatibles declaradas."""
    if not variante_compra:
        return set()
    return {variante_compra, *((prod.compatibles.get(variante_compra) or ()) if prod else ())}


def variante(prod: Producto | None, descripcion: str | None) -> str | None:
    """Variante según las frases de la descripción ('largos', 'fortificada'...) o None si no se sabe.

    Para observaciones de mercado: sin frase (o ambigua) no se asume ninguna variante.
    """
    if not prod or not prod.variantes:
        return None
    valor, estado = _resolver_variante(prod, descripcion)
    if estado == "desconocido":
        return prod.variante_default
    return valor


def clase_sibys(descripcion: str | None) -> str | None:
    """'FIDEOS SECOS; VARIEDAD: ...' -> 'FIDEOS SECOS' (solo si tiene forma de descripción SIByS)."""
    if not descripcion or ";" not in descripcion:
        return None
    clase, _, resto = descripcion.partition(";")
    clase = clase.strip()
    if not clase or clase.upper() != clase or ":" not in resto:
        return None
    from .textnorm import strip_accents
    return strip_accents(clase).upper().strip()


def compatible_sibys(descripcion: str | None, producto_id: str | None) -> tuple[bool, str]:
    """(ok, motivo). Descripción sin clase SIByS: ok. Con clase: debe estar mapeada y permitir el producto."""
    clase = clase_sibys(descripcion)
    if clase is None or producto_id is None:
        return True, ""
    mapa = (config.load_yaml("sibys_productos.yml").get("clases") or {})
    mapa = {k.upper(): v or [] for k, v in mapa.items()}
    if clase not in mapa:
        return False, f"clase SIByS '{clase}' sin mapeo al catálogo (no se compara)"
    if producto_id not in mapa[clase]:
        return False, f"clase SIByS '{clase}' no permite {producto_id}"
    return True, ""


def sync_to_db(conn: sqlite3.Connection, catalog: dict[str, Producto] | None = None) -> int:
    """Vuelca el catálogo a la tabla productos (la usa el webui para nombres/filtros)."""
    catalog = catalog or load_catalog()
    for prod in catalog.values():
        conn.execute(
            "INSERT INTO productos (id, nombre, rubro, unidad_base, tamano_referencia, ipc_serie, definicion_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET nombre=excluded.nombre, "
            "rubro=excluded.rubro, unidad_base=excluded.unidad_base, "
            "tamano_referencia=excluded.tamano_referencia, ipc_serie=excluded.ipc_serie, "
            "definicion_json=excluded.definicion_json",
            (prod.id, prod.nombre, prod.rubro, prod.unidad_base, prod.tamano_referencia,
             "ipc_alimentos" if prod.rubro in {"alimentos", "bebidas"} else "ipc_general",
             json.dumps({"incluye": [" ".join(p) for p in prod.incluye],
                         "excluye": [" ".join(p) for p in prod.excluye],
                         "indec": prod.indec}, ensure_ascii=False)))
    conn.commit()
    return len(catalog)
