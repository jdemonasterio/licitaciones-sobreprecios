"""Normalización de texto para comparar descripciones de productos.

"Puré de Tomate x 530 Gr." y "PURE DE TOMATE X530GR" tienen que verse iguales
para las reglas del catálogo: minúsculas, sin tildes, sin puntuación.
"""
from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^a-z0-9ñ]+")
_DIGIT_LETTER = re.compile(r"(?<=\d)(?=[a-zñ])|(?<=[a-zñ])(?=\d)")


def strip_accents(text: str) -> str:
    # La ñ se preserva: "año" y "ano" no son lo mismo, y "ñoquis" es un producto.
    out = []
    for char in unicodedata.normalize("NFD", text):
        if unicodedata.category(char) == "Mn" and out and out[-1] in "nN" and char == "̃":
            out[-1] = "ñ" if out[-1] == "n" else "Ñ"
            continue
        if unicodedata.category(char) != "Mn":
            out.append(char)
    return "".join(out)


def norm(text: str | None) -> str:
    """Minúsculas, sin tildes, solo [a-z0-9ñ] separados por un espacio.

    También separa número de letra ("x530gr" -> "x 530 gr") para que las
    reglas por palabra no dependan de cómo se tipeó el tamaño.
    """
    if not text:
        return ""
    text = strip_accents(str(text)).lower()
    text = _DIGIT_LETTER.sub(" ", text)
    return _NON_ALNUM.sub(" ", text).strip()


def tokens(text: str | None) -> set[str]:
    return set(norm(text).split())
