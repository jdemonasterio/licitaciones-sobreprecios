"""Normalizadores determinísticos (sin LLM) de CUIT, razones sociales, personas y organismos."""
from __future__ import annotations

import re
from functools import lru_cache

from .. import config
from ..textnorm import strip_accents

# ── CUIT ───────────────────────────────────────────────────────────────────

_PESOS_CUIT = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def cuit_digitos(cuit: str | None) -> str:
    return re.sub(r"\D", "", cuit or "")


def cuit_valido(cuit: str | None) -> bool:
    """Dígito verificador módulo 11 de ARCA. Controla sintaxis, no que el CUIT sea de quien dice."""
    d = cuit_digitos(cuit)
    if len(d) != 11:
        return False
    total = sum(int(a) * b for a, b in zip(d[:10], _PESOS_CUIT))
    dv = 11 - total % 11
    dv = 0 if dv == 11 else 9 if dv == 10 else dv
    return dv == int(d[10])


def cuit_formato(cuit: str | None) -> str | None:
    d = cuit_digitos(cuit)
    return f"{d[:2]}-{d[2:10]}-{d[10:]}" if len(d) == 11 else (cuit.strip() if cuit else None)


def es_persona_humana(cuit: str | None) -> bool:
    return cuit_digitos(cuit)[:2] in {"20", "23", "24", "27"}


# ── Empresas ───────────────────────────────────────────────────────────────

# Forma jurídica canónica -> cómo se muestra.
FORMAS = {"SA": "S.A.", "SRL": "S.R.L.", "SAS": "S.A.S.", "SCA": "S.C.A.", "SE": "S.E.", "SH": "S.H.",
          "UTE": "U.T.E.", "COOPERATIVA": "COOPERATIVA LTDA.", "PERSONA_HUMANA": ""}
_LARGAS = [
    ("SOCIEDAD DE RESPONSABILIDAD LIMITADA", "SRL"),
    ("SOCIEDAD POR ACCIONES SIMPLIFICADA", "SAS"),
    ("SOCIEDAD EN COMANDITA POR ACCIONES", "SCA"),
    ("SOCIEDAD ANONIMA", "SA"),
    ("SOCIEDAD DEL ESTADO", "SE"),
    ("SOCIEDAD DE HECHO", "SH"),
    ("UNION TRANSITORIA DE EMPRESAS", "UTE"),
    ("UNION TRANSITORIA", "UTE"),
]
_ABREV = {"SRL": "SRL", "SAS": "SAS", "SCA": "SCA", "SE": "SE", "SH": "SH", "UTE": "UTE", "SA": "SA"}
_REEMPLAZOS = [(r"\bHNOS?\b", lambda m: "HERMANOS" if m.group(0) == "HNOS" else "HERMANO"),
               (r"\bCIA\b", "COMPAÑIA"), (r"\bCOMPANIA\b", "COMPAÑIA"), (r"\bLTDA\b", "LIMITADA"),
               (r"\bCOOP\b", "COOPERATIVA"), (r"&", " Y ")]


def _base_texto(texto: str) -> str:
    t = strip_accents(texto or "").upper()
    t = t.replace("\xa0", " ")
    t = re.sub(r"[.,;:\"'´`()\[\]]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Siglas separadas por espacios ("S A C I F I A" o "C E I") -> juntas.
    t = re.sub(r"\b(?:[A-Z] ){1,7}[A-Z]\b", lambda m: m.group(0).replace(" ", ""), t)
    for patron, repl in _REEMPLAZOS:
        t = re.sub(patron, repl, t)
    return re.sub(r"\s+", " ", t).strip()


def normalizar_empresa(razon_social: str | None, cuit: str | None = None) -> dict:
    """{clave, nombre_base, forma, nombre_display, cuit, cuit_valido, original}.

    clave = "CUIT:<11 dígitos>" si el CUIT es válido; si no, "NOMBRE:<nombre_base forma>"
    (solo sirve para proponer candidatos de unión, no para unir).
    """
    t = _base_texto(razon_social or "")
    forma = None
    for larga, corta in _LARGAS:
        if t.endswith(" " + larga) or t == larga:
            forma, t = corta, t[: -len(larga)].strip()
            break
        if t.startswith(larga + " "):
            forma, t = corta, t[len(larga):].strip()
            break
    if forma is None:
        tokens = t.split()
        # "MICROSULES ARGENTINA SA DE SCIIA" / "... SACIFIA" / "... SAIC": forma SA con apellido de siglas.
        m = re.search(r"\b(SA|SRL|SAS|SCA|SE|SH|UTE|SA[A-Z]{1,6})\b(?: DE [A-Z]{2,8})?$", t)
        if m:
            sig = m.group(1)
            forma = _ABREV.get(sig, "SA" if sig.startswith("SA") else sig)
            t = t[: m.start()].strip()
        elif tokens and tokens[0] in _ABREV:
            forma, t = _ABREV[tokens[0]], " ".join(tokens[1:])
    if "COOPERATIVA" in t.split() and forma is None:
        forma = "COOPERATIVA"
        t = re.sub(r"\s+LIMITADA$", "", t).strip()
    if forma is None and es_persona_humana(cuit):
        forma = "PERSONA_HUMANA"
    t = re.sub(r"\s+(DE|Y)$", "", t).strip()
    valido = cuit_valido(cuit)
    display = (t + (" " + FORMAS[forma] if forma and FORMAS.get(forma) else "")).strip()
    return {
        "clave": f"CUIT:{cuit_digitos(cuit)}" if valido else f"NOMBRE:{t}|{forma or ''}",
        "nombre_base": t, "forma": forma, "nombre_display": display,
        "cuit": cuit_formato(cuit) if cuit else None, "cuit_valido": valido, "original": razon_social,
    }


# ── Personas ───────────────────────────────────────────────────────────────

def normalizar_persona(nombre: str | None, documento: str | None = None) -> dict:
    """{clave, nombre_display, tokens, documento, original}. Clave por DNI; sin DNI, 'posible'."""
    t = strip_accents(nombre or "").upper().replace("\xa0", " ")
    t = re.sub(r"[.;:()\"']", " ", t)
    if "," in t:   # "PEREZ, JUAN" -> "JUAN PEREZ"
        apellido, _, nombres = t.partition(",")
        t = f"{nombres} {apellido}"
    t = re.sub(r"\s+", " ", t.replace(",", " ")).strip()
    doc = cuit_digitos(documento)
    if len(doc) == 11:      # CUIL -> DNI (8 dígitos del medio)
        doc = doc[2:10]
    doc = doc.lstrip("0") if 6 <= len(doc) <= 8 else ""
    tokens = " ".join(sorted(t.split()))
    return {"clave": f"DNI:{doc}" if doc else f"POSIBLE:{tokens}", "nombre_display": t.title(), "tokens": tokens,
            "documento": doc or None, "original": nombre}


# ── Organismos ─────────────────────────────────────────────────────────────

_UNIDADES = r"(?:SECRETARIA|SUBSECRETARIA|DIRECCION|COORDINACION|UNIDAD|AGENCIA|SERVICIO ADMINISTRATIVO|DEPARTAMENTO)"


@lru_cache(maxsize=1)
def _saf_tabla() -> dict[str, dict]:
    data = config.load_yaml("organismos.yml").get("organismos") or {}
    return {str(k): v for k, v in data.items()}


def normalizar_organismo(texto: str | None) -> dict:
    """{nombre (de la fecha, limpio), jurisdiccion, unidad, jurisdiccion_clave, saf, saf_nombre, original}.

    Acepta el título del BO ("MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA"),
    el SAF de COMPR.AR ("310 - Ministerio de Salud") o la UOC ("80/000 - Dirección General ... - MS").

    `jurisdiccion`/`unidad`/`nombre` CONSERVAN los acentos del texto de origen: el corte entre
    jurisdiccion y unidad se ubica sobre una copia sin acentos/en mayúsculas (`up`, para que
    _UNIDADES matchee sin depender de cómo vino tipeado), pero el resultado se recorta del texto
    ORIGINAL en esa misma posición — `strip_accents()` nunca suma ni resta caracteres (una tilde
    es 1 char de entrada -> 1 char de salida), así que la posición vale para los dos strings por
    igual. Antes el resultado se mostraba siempre sin tildes (MEJORAS #53: "Ministerio de
    Economia" en vez de "Ministerio de Economía") porque se devolvía el trozo YA pelado de `up`.

    `jurisdiccion_clave` es la comparación insensible a mayúsculas/acentos/espacios: usala para
    AGRUPAR (dos variantes con o sin tilde tienen que caer en el mismo balde); nunca para mostrar
    — lo legible con tildes correctas es `jurisdiccion`/`unidad`.
    """
    raw = re.sub(r"\s+", " ", (texto or "").replace("\xa0", " ")).strip()
    saf = None
    m = re.match(r"^(\d{2,3})\s*-\s*(.+)$", raw)
    if m:
        saf, raw_nombre = m.group(1), m.group(2)
    else:
        raw_nombre = raw
        m2 = re.match(r"^(\d{1,3})/\d{1,3}\s*-\s*(.+)$", raw)   # UOC "80/000 - ...": el prefijo NO es el SAF
        if m2:
            raw_nombre = m2.group(2)
    raw_nombre = raw_nombre.strip()
    up = strip_accents(raw_nombre).upper()
    partes_up = re.split(rf"\s+(?={_UNIDADES}\b)", up, maxsplit=1)
    if len(partes_up) > 1 and len(up) == len(raw_nombre):
        corte = len(partes_up[0])
        jurisdiccion, unidad = raw_nombre[:corte].strip(), raw_nombre[corte:].strip()
    else:
        jurisdiccion, unidad = raw_nombre, ""
    saf_nombre = None
    if saf and saf in _saf_tabla():
        saf_nombre = (_saf_tabla()[saf].get("nombres") or [None])[0]
    return {
        "nombre": raw_nombre, "jurisdiccion": jurisdiccion.title() if jurisdiccion else None,
        "unidad": unidad.title() if unidad else None,
        "jurisdiccion_clave": strip_accents(jurisdiccion).upper() if jurisdiccion else None,
        "saf": saf, "saf_nombre": saf_nombre, "original": texto,
    }
