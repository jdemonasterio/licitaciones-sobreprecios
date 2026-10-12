"""Contexto de empresas adjudicatarias: autoridades, socios, domicilios en el tiempo.

Integra el conector de la segunda sección del Boletín Oficial (sociedades) y
guarda cada dato como un hecho fechado en `empresa_hechos` (ver db.py). Nunca se
pisa un hecho viejo: el estado actual se deriva del último acto publicado.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from . import db
from .textnorm import strip_accents


def _hecho_key(h: dict) -> str:
    base = json.dumps({k: h.get(k) for k in ("tipo", "persona", "documento", "cargo", "valor", "vigente_desde",
                                             "fuente_url")}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:20]


_NO_PERSONA = {"ciudad", "buenos", "aires", "domicilio", "especial", "piso", "depto", "departamento", "calle",
               "avenida", "av", "cpa", "cp", "sociedad", "directorio", "designaron", "designado", "designados",
               "quedando", "fecha", "escritura", "asamblea", "acta", "autonoma", "argentina", "provincia",
               "sa", "srl", "s", "a", "r", "l", "dni", "cuit", "abogado", "contador", "escribano", "nombrada",
               "presidente", "director", "suplente", "titular", "sindico", "misma", "tiene", "monroe",
               "otorgada", "ante", "dentro", "quintuplo", "socie", "mandato", "ejercicio", "vencimiento"}


def parece_persona(nombre: str | None) -> bool:
    """Filtro de calidad para nombres extraídos por reglas de avisos societarios.

    Los avisos viejos (escaneados a 3 columnas) mezclan texto de sociedades vecinas y
    las reglas devuelven cosas como "Ciudad de Buenos Aires" o "CPA 1428" como si fueran
    personas. Un nombre de persona: 2 a 6 palabras, sin dígitos ni símbolos, y ninguna
    palabra de dirección / trámite societario.
    """
    if not nombre or any(ch.isdigit() for ch in nombre) or any(ch in nombre for ch in "#%@/()–:;"):
        return False
    words = [w.strip(".,").lower() for w in nombre.split() if w.strip(".,")]
    if not (2 <= len(words) <= 6) or any(w.endswith("-") for w in words):   # "socie-" = palabra cortada
        return False
    return not any(strip_accents(w) in _NO_PERSONA for w in words)


def guardar_hechos(conn, cuit: str, hechos: list[dict], fuente: str, extraido_por: str) -> int:
    n = 0
    for h in hechos:
        if h.get("tipo") in {"autoridad", "socio"} and not parece_persona(h.get("persona")):
            continue
        # Avisos del BO anteriores a ~2012 (ids "H...") son páginas escaneadas: baja confianza.
        if "/segunda/H" in (h.get("fuente_url") or "") and h.get("confianza") is None:
            h["confianza"] = 0.3
        conn.execute(
            "INSERT OR IGNORE INTO empresa_hechos (cuit, tipo, persona, documento, cargo, valor, vigente_desde, "
            "fecha_publicacion, renovacion_total, fuente, fuente_url, extraido_por, confianza, created_at, "
            "hecho_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cuit, h.get("tipo"), h.get("persona"), h.get("documento"), h.get("cargo"), h.get("valor"),
             h.get("vigente_desde"), h.get("fecha_publicacion"),
             int(bool(h.get("renovacion_total"))) if h.get("renovacion_total") is not None else None,
             fuente, h.get("fuente_url"), extraido_por, h.get("confianza"),
             db.now_iso(), _hecho_key(h)))
        n += 1
    db.close_superseded_facts(conn, cuit)
    return n


def actualizar_contexto(cuit: str, anios: int = 25) -> str:
    """Busca la empresa en la segunda sección del BO y guarda sus hechos fechados."""
    try:
        from .sources import bo_sociedades
    except ImportError as exc:
        raise RuntimeError("El conector de sociedades del BO todavía no está instalado") from exc
    with db.session() as conn:
        row = conn.execute("SELECT razon_social FROM empresas WHERE cuit=?", (cuit,)).fetchone()
        if not row or not row["razon_social"]:
            raise ValueError(f"No conozco la razón social del CUIT {cuit}")
        razon = row["razon_social"]
        hasta = dt.date.today()
        desde = hasta.replace(year=hasta.year - anios)
        avisos = bo_sociedades.buscar_avisos_sociedad(razon, desde, hasta)
        total = 0
        for ref in avisos:
            texto = bo_sociedades.texto_aviso(ref)
            hechos = bo_sociedades.extraer_hechos(texto, razon, cuit)
            url = ref.get("url") if isinstance(ref, dict) else getattr(ref, "url", None)
            for h in hechos:
                h.setdefault("fuente_url", url)
            total += guardar_hechos(conn, cuit, hechos, "bo_segunda", "bo_sociedades")
        conn.execute("UPDATE empresas SET contexto_actualizado_at=? WHERE cuit=?", (db.now_iso(), cuit))
    return f"{razon}: {len(avisos)} avisos de sociedades, {total} hechos guardados"
