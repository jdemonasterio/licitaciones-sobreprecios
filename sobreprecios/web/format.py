"""Formato de números y textos compartidos entre el webui (app.py, Jinja) y los
reportes/expedientes (reports.py, HTML armado a mano): así el "comprobante de cálculo",
el expediente para periodista y la página normal muestran EXACTAMENTE el mismo número y
la misma frase para el mismo dato, sin duplicar la lógica en dos lugares que se puedan
desincronizar.
"""
from __future__ import annotations

import re


def ars(value, decimals=0, moneda="nominal"):
    if value in (None, ""):
        return "—"
    try:
        text = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"
    return ("US$ " if moneda == "usd" else "$ ") + text.replace(",", "X").replace(".", ",").replace("X", ".")


def num(value, decimals=0):
    if value in (None, ""):
        return "—"
    text = f"{float(value):,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(value):
    if value in (None, ""):
        return "—"
    return f"{float(value):+.1f}%".replace(".", ",")


def millones(value, moneda="nominal"):
    pref = "US$" if moneda == "usd" else "$"
    if not value:
        return f"{pref} 0"
    value = float(value)
    if abs(value) >= 1e9:
        return f"{pref} {value / 1e9:,.2f} mil M".replace(".", ",")
    if abs(value) >= 1e6:
        return f"{pref} {value / 1e6:,.1f} M".replace(".", ",")
    return ars(value, 0, moneda)


SEMAFORO_TXT = {"rojo": "Sobreprecio alto", "amarillo": "Sobreprecio moderado", "verde": "En línea con mercado",
                "gris": "Sin comparación"}
REF_TXT = {"misma_marca": "misma marca", "p25_mayorista": "mayorista (P25)",
           "mediana_mayorista": "mayorista (mediana)", "min_mayorista": "mayorista (mínimo)",
           "mediana_minorista": "supermercado (mediana)", "indec": "INDEC (promedio)",
           "min_mercado": "mínimo de mercado", "min_minorista": "supermercado (mínimo)"}
ATRIBUTO_TXT = {"verificado": "variedad verificada", "no_exigido": "variedad no exigida en la ficha",
                "desconocido": "variedad desconocida", "ambiguo": "variedad ambigua", "sin_mercado": "variedad sin mercado",
                "verificado (humano)": "variedad confirmada a mano por el revisor"}
MOTIVO_CODIGO_TXT = {
    "sin_precio": "no se pudo calcular el precio por unidad base o falta la fecha de la compra",
    "sin_datos": "no hay precios de mercado de este producto para comparar en esa fecha",
    "revisar_unidad": "el Estado pagó una cantidad de veces la referencia tan grande o chica que probablemente "
                      "haya un error de unidad (bulto vs. unidad, producto distinto) antes que un sobreprecio real",
    "fuera_alcance": "este renglón no tiene un producto del catálogo para comparar (no es un error, es un bien "
                     "que todavía no está catalogado)",
    "atributo_desconocido": "no se puede saber qué variedad exige la compra (o dice dos a la vez): sin eso no "
                            "se puede elegir contra qué precio de mercado comparar",
    "sin_referencia_variante": "la compra exige una variedad puntual y el mercado no tiene precios de ESA "
                               "variedad en la fecha: las otras variedades no son referencia válida",
    "sin_referencia_presentacion": "la compra exige un envase puntual y el mercado no tiene ESE envase en la "
                                   "fecha: un envase distinto no es referencia válida",
    "equivalencia_por_verificar": "el sistema aceptó una variedad de mercado como equivalente a la exigida; "
                                  "un humano todavía no confirmó que esa equivalencia sea correcta",
    "ok": "se pudo calcular una referencia de mercado comparable",
}
# Legenda corta de #35: un caso es una investigación para que un humano decida, nunca una conclusión publicada sola.
LEGENDA_INVESTIGACION = ("Un color (sobre todo rojo) es una investigación pendiente de revisión humana, no una "
                         "conclusión: puede explicarse por logística, plazos de pago, especificaciones o un "
                         "error de publicación. Nada sale a prensa sin pasar por Revisión y quedar \"publicable\".")


_SIN_ROJO_RE = re.compile(r"sin rojo:\s*([^)]+)")
_EQUIV_RE = re.compile(r"no está verificada \(([^)]+)\)")


def explicar_semaforo(row: dict) -> str:
    """Una frase corta de por qué ESTE renglón tiene este semáforo (MEJORAS #49.3): si la brecha
    llega al umbral de rojo pero el semáforo no es rojo, dice qué lo bloquea.

    Se deriva de motivo / motivo_codigo / los campos ya calculados por pipeline/compare.py —
    nunca repite ni reinventa el cálculo, solo lo resume en una línea para la persona que mira
    la pantalla. `row` es cualquier dict con esas claves (una fila de renglones+comparaciones).
    """
    semaforo = row.get("semaforo") or "gris"
    motivo = row.get("motivo") or ""
    sp = row.get("sobreprecio_ajustado_pct")

    if semaforo == "gris":
        return MOTIVO_CODIGO_TXT.get(row.get("motivo_codigo"), motivo) or "todavía sin calcular"

    if semaforo == "rojo":
        corrob = row.get("sobreprecio_corroborado_pct")
        if corrob is not None:
            return (f"Rojo: paga {pct(corrob)} sobre el mínimo confirmado por dos fuentes independientes, "
                    "aun con el plazo de pago más largo plausible.")
        return f"Rojo: paga {pct(sp)} sobre la referencia de mercado." if sp is not None else "Rojo."

    if row.get("motivo_codigo") == "equivalencia_por_verificar":
        m = _EQUIV_RE.search(motivo)
        detalle = m.group(1).strip() if m else "la equivalencia con el producto de mercado todavía no está confirmada"
        prefijo = f"{pct(sp)} vs un producto parecido, " if sp is not None else ""
        return f"{prefijo}pero amarillo: {detalle}."

    if semaforo == "amarillo":
        m = _SIN_ROJO_RE.search(motivo)
        if m:
            prefijo = f"{pct(sp)} sobre la referencia, " if sp is not None else ""
            return f"{prefijo}pero amarillo: {m.group(1).strip()}."
        return f"Amarillo: paga {pct(sp)} sobre la referencia de mercado." if sp is not None else "Amarillo."

    # verde
    return (f"Verde: paga {pct(sp)} sobre la referencia de mercado (dentro de lo esperado)."
            if sp is not None else "Verde: dentro de lo esperado.")
