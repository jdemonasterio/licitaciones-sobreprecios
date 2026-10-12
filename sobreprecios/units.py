"""Tamaños de envase y montos en pesos, sacados de texto libre.

Toda comparación del repo es precio por unidad base (ARS por kg, por litro o
por unidad). Este parser decide si "Leche en polvo x 800 Gr." del Boletín
Oficial y "Leche en Polvo La Lechera 800 Gr" de un mayorista quedan en la misma
escala. Un tamaño mal leído es un 100% de sobreprecio falso en la mesa de un
periodista, así que el parser es conservador: si duda, devuelve None y el
renglón va al LLM o a revisión manual en vez de adivinar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .textnorm import strip_accents

# unidad escrita -> (unidad canónica, factor a la unidad chica: g / ml / u)
_UNIT_ALIASES = {
    "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "grm": ("g", 1), "grms": ("g", 1),
    "gramo": ("g", 1), "gramos": ("g", 1),
    "kg": ("g", 1000), "kgs": ("g", 1000), "k": ("g", 1000), "kilo": ("g", 1000),
    "kilos": ("g", 1000), "kilogramo": ("g", 1000), "kilogramos": ("g", 1000),
    "ml": ("ml", 1), "cc": ("ml", 1), "cm3": ("ml", 1), "c c": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000), "ltr": ("ml", 1000),
    "ltrs": ("ml", 1000), "litro": ("ml", 1000), "litros": ("ml", 1000),
}
_UNIT_RE = (r"(?:kilogramos?|kilos?|kgs?|k|gramos?|grms?|grs?|grm|g|"
            r"litros?|ltrs?|lts?|lt|l|ml|cm3|c\.?\s?c\.?)")
_NUM_RE = r"(?:\d+\s*/\s*\d+|\d+(?:[.,]\d+)*)"

# "12 x 1 lt", "6 unidades de 2,25 L", "caja x 12 u. x 1 l". Exige "x" o "de"
# entre la cantidad y el tamaño: "Morixe 20 Un 1 Kg" NO es un pack de 20 kg.
# La cantidad no puede venir pegada a letras: "Tirabuzón N34 x500g" es el fideo
# número 34, no 34 paquetes; "NM6x1K" (SEPA) tampoco es un pack.
_MULTI_RE = re.compile(
    rf"(?<![A-Za-z\d.,/])(\d{{1,3}})\s*(?:u\.?|un\.?|uni\.?|unid\.?|unidades|paq\.?|paquetes|sobres|latas|botellas|sachets?)?\s*"
    rf"(?:x|de)\s*({_NUM_RE})\s*({_UNIT_RE})(?![a-z])",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(
    rf"(?<![\d.,/])(x\s*)?({_NUM_RE})\s*({_UNIT_RE})(?![a-z])(?!\s*/\s*m)",
    re.IGNORECASE,
)
_HOJAS_RE = re.compile(r"(?<![\d.,])(\d{2,4})\s*(?:hojas|hjs|h\.)", re.IGNORECASE)


@dataclass(frozen=True)
class Size:
    """Contenido de UN envase. amount está en la unidad chica (g, ml, u)."""
    amount: float
    unit: str          # "g" | "ml" | "u"
    pack_count: int = 1
    raw: str = ""

    @property
    def total(self) -> float:
        return self.amount * self.pack_count

    def base(self) -> tuple[float, str]:
        """(cantidad, unidad_base) en kg / l / u: 800 g -> (0.8, "kg")."""
        if self.unit == "g":
            return self.total / 1000.0, "kg"
        if self.unit == "ml":
            return self.total / 1000.0, "l"
        return self.total, "u"


def parse_number(text: str) -> float | None:
    """Número con convención argentina o anglo, según lo que sea inequívoco.

    "1.234,56" -> 1234.56 · "41,49" -> 41.49 · "1.500" -> 1500 (punto + 3 dígitos
    = miles) · "75.07" -> 75.07 · "38,3435" -> 38.3435 · "1/2" -> 0.5.
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    if "/" in raw:
        num, _, den = raw.partition("/")
        try:
            return float(num.strip()) / float(den.strip())
        except (ValueError, ZeroDivisionError):
            return None
    cleaned = re.sub(r"[^\d.,\-]", "", raw)
    # Formato legal "($519,74.-)": el punto/coma/guion final no es parte del número.
    cleaned = cleaned.strip(".,-") if not cleaned.startswith("-") else "-" + cleaned[1:].strip(".,-")
    if not cleaned or cleaned in {"-", ".", ","}:
        return None
    if "." in cleaned and "," in cleaned:
        decimal = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        cleaned = cleaned.replace(thousands, "").replace(decimal, ".")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts) > 2:
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[0] not in {"0", "-0"}):
            cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_ars(text) -> float | None:
    """Monto en pesos: "$ 1.234.567,89", "41,49", 47, "ARS 98.000.000"."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = re.sub(r"(?i)\b(ars|pesos?)\b|\$", " ", str(text))
    return parse_number(cleaned)


def _unit(raw_unit: str) -> tuple[str, int] | None:
    key = re.sub(r"[.\s]", "", raw_unit.lower())
    if key == "cc":
        key = "cc"
    return _UNIT_ALIASES.get(key)


def parse_size(text: str | None) -> Size | None:
    """Contenido por envase de una descripción, o None si no es inequívoco.

    Prioridad: pack explícito ("12 x 1 lt") > tamaño precedido por "x"
    ("Leche en polvo x 800 Gr.") > único tamaño mencionado. Si hay dos tamaños
    distintos sin "x" que desempate, devuelve None (que lo resuelva el LLM).
    """
    if not text:
        return None
    clean = strip_accents(str(text))
    # Formato SEPA "PAQ-500-gr." / "Bot-1500-cc": el guion pegado al número separa.
    clean = re.sub(r"(?<=\d)-(?=[A-Za-z])|(?<=[A-Za-z])-(?=\d)", " ", clean)
    # Lo que va entre paréntesis suele ser el bulto mayorista "(24)" o una aclaración.
    clean_noparen = re.sub(r"\([^)]*\)", " ", clean)

    multi = _MULTI_RE.search(clean_noparen)
    if multi:
        count = int(multi.group(1))
        amount = parse_number(multi.group(2))
        unit = _unit(multi.group(3))
        if unit and amount and 1 < count <= 200:
            return Size(amount * unit[1], unit[0], count, multi.group(0).strip())

    found = []
    for match in _SINGLE_RE.finditer(clean_noparen):
        amount = parse_number(match.group(2))
        unit = _unit(match.group(3))
        if not unit or not amount or amount <= 0:
            continue
        found.append((bool(match.group(1)), amount * unit[1], unit[0], match.group(0).strip()))

    if not found:
        hojas = _HOJAS_RE.search(clean_noparen)
        if hojas:
            return Size(float(hojas.group(1)), "u", 1, hojas.group(0))
        return None

    with_x = [f for f in found if f[0]]
    candidates = with_x or found
    distinct = {(round(c[1], 6), c[2]) for c in candidates}
    if len(distinct) > 1:
        return None
    _, amount, unit, raw = candidates[0]
    return Size(amount, unit, 1, raw)


def price_per_base(price: float | None, size: Size | None) -> tuple[float | None, str | None]:
    """Precio por kg / l / u. (None, None) si falta algo."""
    if price is None or size is None:
        return None, None
    qty, base = size.base()
    if qty <= 0:
        return None, None
    return price / qty, base
