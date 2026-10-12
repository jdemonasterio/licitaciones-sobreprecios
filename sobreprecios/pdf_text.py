"""Texto de PDF para licitaciones-sobreprecios: una función, una cadena de
fallback con heurística de calidad (no una librería más para elegir a mano).

Por qué esta cadena y no "pdfplumber default" a secas (ver benchmark en
`_scratch/licitaciones/pdf_bench/`, corpus real del Boletín Oficial, 2026-10-11):

1. **Columnas antes que nada.** `pdfplumber.Page.extract_text()` liso (el que
   usa hoy `investigar.py._pdf_text` y `bo_sociedades._texto_desde_pdf_embebido`)
   anda bien en los PDF de resolución MODERNOS (BO 1ª/3ª sección: una sola
   columna, generados digitalmente). Pero en páginas escaneadas VIEJAS (BO 2ª
   sección pre-2012, ids "H...") que vienen a 3 columnas, `extract_text()` lee
   de izquierda a derecha CRUZANDO columnas y mezcla 2-3 avisos de sociedades
   distintas palabra por palabra en la misma "línea" de salida (ver
   `sources/bo_sociedades.py` gotcha 4, con fixture real en
   `tests/fixtures/bo_segunda/pagina_ocr_2010_columnas_mezcladas.txt`). El
   benchmark confirma que esto es un problema de CÓMO LO LLAMAMOS (falta
   agrupar por columna), no del parser: agrupando `page.extract_words()` por
   banda de `x0` antes de concatenar (`_texto_por_columnas` acá abajo) la
   métrica de orden de lectura sube sin cambiar de librería ni de licencia.
   Por eso esta función SIEMPRE arranca por ahí, para PDF modernos y viejos
   (en una sola columna el agrupado es un no-op: da el mismo resultado que
   `extract_text()`).
2. **OCR solo si no hay capa de texto.** Si después de 1) el texto es
   demasiado corto o parece ruido (`_heuristica_calidad` baja), lo más
   probable es que el PDF sea una imagen escaneada sin texto embebido: ahí
   ninguna librería de extracción ayuda, hace falta OCR. Se usa Tesseract
   (motor gratis, local) sobre un render de la página — el render lo hace
   `pdfplumber.Page.to_image()` (pypdfium2 por debajo, Apache/BSD) para NO
   sumar PyMuPDF como dependencia: PyMuPDF es más rápido y con mejor "modo
   bloques", pero es **AGPL-3.0** (o licencia comercial de pago a Artifex) —
   mala elección para una herramienta que puede terminar en manos de un
   organismo público (ver informe del benchmark). Tesseract necesita el
   binario del sistema (`brew install tesseract tesseract-lang` en Mac;
   fricción real en Windows, ver abajo), por eso es un import opcional: si no
   está, se salta directo al LLM.
3. **LLM como último escalón, no el primero.** Si OCR tampoco deja un texto
   de calidad aceptable (o no está instalado), se escala al mismo LLM de PDF
   que ya usa `investigar.py` (`llm.complete_json(..., role="pdf")`, perfil
   `gratis` por default) — más lento y, en perfil `pago`, con costo real por
   página. El benchmark mostró que Gemini transcribe razonablemente bien
   hasta páginas escaneadas a 3 columnas, pero no es gratis en tiempo (varios
   segundos por PDF) ni en cuota, así que no tiene sentido pagarlo para el
   80% de los PDF (avisos modernos de una columna) que ya resuelve el paso 1.

Fricción de Windows (para la próxima vez que alguien agregue OCR a este
repo, que es Windows-first: ver `docs/guia_instalacion_windows.md`): Tesseract
no se instala con `pip install` — en Mac es `brew install tesseract
tesseract-lang`; en Windows hace falta el instalador de UB-Mannheim
(https://github.com/UB-Mannheim/tesseract/wiki) y agregarlo al PATH a mano,
un paso manual que no entra en `instalar.bat`. Por eso el import de
`pytesseract` es opcional y degrada con una advertencia, no con una excepción:
una máquina sin Tesseract instalado sigue funcionando (salta a columnas-only
o al LLM), solo pierde el escalón de OCR.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# Heurística de calidad (no es NLP real, es un filtro barato para decidir si
# vale la pena escalar al siguiente método): por debajo de esto, OCR; por
# debajo de esto incluso después de OCR, LLM.
_UMBRAL_OK = 0.55
_PISO_CHARS_CONFIABLE = 60       # por debajo, se desconfía aunque las pocas palabras parezcan limpias
_VOCAL_RE = re.compile(r"[aeiouáéíóúAEIOUÁÉÍÓÚ]")
_PALABRA_RE = re.compile(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]{2,}")
_RUIDO_RE = re.compile(r"[^\sA-Za-zÁÉÍÓÚÑáéíóúñ0-9.,;:$%()/-]")


def _heuristica_calidad(texto: str) -> float:
    """0..1: qué tan "texto real" parece, sin diccionario ni modelo.

    Combina tres señales baratas: cuánto del contenido son palabras
    reconocibles (letras, no símbolos sueltos de OCR roto), que las palabras
    tengan largo plausible (ni fragmentos de una letra ni un bloque sin
    espacios), y que no esté dominado por caracteres de ruido típicos de un
    OCR fallido (símbolos raros, control chars decodificados como texto).
    """
    texto = (texto or "").strip()
    if not texto:
        return 0.0
    palabras = _PALABRA_RE.findall(texto)
    chars_no_ws = re.sub(r"\s+", "", texto)
    if not chars_no_ws:
        return 0.0
    cobertura_palabras = len("".join(palabras)) / len(chars_no_ws)
    largos = [len(p) for p in palabras]
    if largos:
        razonables = sum(1 for l in largos if 2 <= l <= 15)
        ratio_largo_ok = razonables / len(largos)
    else:
        ratio_largo_ok = 0.0
    con_vocal = sum(1 for p in palabras if _VOCAL_RE.search(p))
    ratio_vocal = con_vocal / len(palabras) if palabras else 0.0
    ruido = len(_RUIDO_RE.findall(texto))
    ratio_ruido_ok = max(0.0, 1 - ruido / max(1, len(chars_no_ws)) * 20)
    base = 0.4 * cobertura_palabras + 0.25 * ratio_largo_ok + 0.2 * ratio_vocal + 0.15 * ratio_ruido_ok
    # Amortiguar textos muy cortos: pocas palabras "limpias" pueden dar un
    # puntaje perfecto por casualidad (p.ej. una sola palabra bien OCRizada
    # en una pagina que en realidad no tiene capa de texto usable).
    amortiguador = min(1.0, len(chars_no_ws) / _PISO_CHARS_CONFIABLE)
    return round(base * amortiguador, 3)


def _agrupar_lineas(tokens: list[tuple[float, float, str]], tol_y: float = 3.0) -> list[str]:
    """tokens: (top, x0, texto) ya filtrados a una columna -> líneas de texto,
    ordenadas top-down y, dentro de cada línea, izquierda-derecha."""
    tokens = sorted(tokens, key=lambda t: (round(t[0] / tol_y), t[1]))
    lineas: list[list[tuple[float, float, str]]] = []
    for tok in tokens:
        if lineas and abs(lineas[-1][0][0] - tok[0]) < tol_y:
            lineas[-1].append(tok)
        else:
            lineas.append([tok])
    return [" ".join(t[2] for t in sorted(linea, key=lambda t: t[1])) for linea in lineas]


def _texto_por_columnas(page, max_columnas: int = 3) -> str:
    """Texto de una página de pdfplumber, agrupando palabras por columna
    antes de leerlas (ver punto 1 del docstring del módulo).

    Heurística: probar 1..max_columnas divisiones iguales del ancho de
    página y quedarse con la que minimiza palabras que "cruzan" el límite
    entre columnas, exigiendo un piso de palabras por columna (si no, una
    página de una sola columna con un margen ancho se vería como "2 columnas
    con una casi vacía"). En una página real de una sola columna esto da el
    mismo resultado que `extract_text()` liso: no hay downside en avisos
    modernos, por eso se aplica siempre y no solo cuando "se sospecha" un
    PDF viejo.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return ""
    ancho = float(page.width)
    boxes = [(w["x0"], w["x1"], w["top"], w["text"]) for w in words]
    mejor_n, mejor_score = 1, -1.0
    for n in range(1, max_columnas + 1):
        cuentas = [0] * n
        cruces = 0
        for x0, x1, _, _ in boxes:
            c0 = min(n - 1, max(0, int(x0 / ancho * n)))
            c1 = min(n - 1, max(0, int((x1 - 0.01) / ancho * n)))
            cuentas[c0] += 1
            if c0 != c1:
                cruces += 1
        piso = max(5, len(boxes) * 0.05 / n)
        if min(cuentas) < piso:
            continue
        score = 1 - cruces / len(boxes)
        if score > mejor_score or (n > mejor_n and score >= mejor_score - 0.02):
            mejor_n, mejor_score = n, score
    columnas: list[list[tuple[float, float, str]]] = [[] for _ in range(mejor_n)]
    for x0, x1, top, texto in boxes:
        c = min(mejor_n - 1, max(0, int(x0 / ancho * mejor_n)))
        columnas[c].append((top, x0, texto))
    partes = []
    for col in columnas:
        partes.extend(_agrupar_lineas(col))
    return "\n".join(partes)


def _extraer_pdfplumber(path: Path, max_paginas: int = 60) -> tuple[str, int]:
    import pdfplumber
    with pdfplumber.open(str(path)) as pdf:
        paginas = pdf.pages[:max_paginas]
        texto = "\n".join(_texto_por_columnas(p) for p in paginas)
        return texto, len(pdf.pages)


def _extraer_ocr(path: Path, max_paginas: int = 60) -> str | None:
    """Tesseract sobre un render de cada página (best-effort: devuelve None
    si Tesseract no está instalado, nunca levanta — ver fricción de Windows
    en el docstring del módulo)."""
    try:
        import pytesseract
        import pdfplumber
    except ImportError:
        logger.info("pytesseract no instalado: se salta el escalón de OCR")
        return None
    try:
        with pdfplumber.open(str(path)) as pdf:
            textos = []
            for page in pdf.pages[:max_paginas]:
                imagen = page.to_image(resolution=250).original
                textos.append(pytesseract.image_to_string(imagen, lang="spa"))
            return "\n".join(textos)
    except Exception as exc:   # binario ausente, idioma no instalado, pdf corrupto: degradar
        logger.warning("OCR no disponible/falló para %s: %s", path, exc)
        return None


def extraer_texto(path: str | Path, estrategia: str = "auto",
                  conn: sqlite3.Connection | None = None) -> dict:
    """Texto de un PDF con la cadena de fallback del módulo (ver docstring).

    `estrategia`:
      auto       columnas -> OCR si hace falta -> LLM si hace falta (default).
      columnas   solo el paso 1 (rápido, sin OCR ni LLM; para batch barato).
      sin_llm    columnas -> OCR, nunca escala al LLM (para no gastar cuota).

    Devuelve dict: texto, metodo (el que ganó), paginas, calidad (0..1,
    heurística de `_heuristica_calidad` del texto devuelto), advertencias
    (lista de str: qué se intentó y por qué se siguió al próximo escalón).
    Nunca levanta por un PDF ilegible: en el peor caso devuelve texto vacío,
    calidad 0.0 y la advertencia correspondiente — el caller decide qué hacer
    (igual que ya hace `investigar.py` con `LLMUnavailable`).
    """
    if estrategia not in {"auto", "columnas", "sin_llm"}:
        raise ValueError(f"estrategia desconocida: {estrategia!r}")
    path = Path(path)
    advertencias: list[str] = []

    try:
        texto, paginas = _extraer_pdfplumber(path)
    except Exception as exc:
        return {"texto": "", "metodo": "ninguno", "paginas": 0, "calidad": 0.0,
                "advertencias": [f"pdfplumber no pudo abrir el archivo: {exc}"]}

    mejor = {"texto": texto, "metodo": "pdfplumber_columnas", "calidad": _heuristica_calidad(texto)}
    if estrategia == "columnas" or mejor["calidad"] >= _UMBRAL_OK:
        return {**mejor, "paginas": paginas, "advertencias": advertencias}

    advertencias.append(f"pdfplumber_columnas con calidad {mejor['calidad']}: se intenta OCR")
    texto_ocr = _extraer_ocr(path)
    if texto_ocr is None:
        advertencias.append("OCR no disponible (Tesseract no instalado o falló)")
    else:
        calidad_ocr = _heuristica_calidad(texto_ocr)
        if calidad_ocr > mejor["calidad"]:
            mejor = {"texto": texto_ocr, "metodo": "tesseract_ocr", "calidad": calidad_ocr}
        if calidad_ocr >= _UMBRAL_OK:
            return {**mejor, "paginas": paginas, "advertencias": advertencias}
        advertencias.append(f"OCR con calidad {calidad_ocr}: {'se intenta LLM' if estrategia == 'auto' else 'se deja así (sin_llm)'}")

    if estrategia == "sin_llm":
        return {**mejor, "paginas": paginas, "advertencias": advertencias}

    from . import llm
    schema = {"type": "object", "additionalProperties": False, "required": ["texto"],
              "properties": {"texto": {"type": "string"}}}
    try:
        resultado = llm.complete_json(
            "Transcribi el texto completo de este documento, tal cual esta escrito, "
            "sin resumir, sin traducir, sin agregar comentarios.",
            schema, role="pdf", proposito="pdf_text_extraer_texto", file_path=path, conn=conn)
        texto_llm = resultado["texto"]
        calidad_llm = _heuristica_calidad(texto_llm)
        if calidad_llm > mejor["calidad"]:
            mejor = {"texto": texto_llm, "metodo": "gemini_pdf", "calidad": calidad_llm}
        else:
            advertencias.append(f"LLM devolvio calidad {calidad_llm}, no mejor que {mejor['metodo']}")
    except llm.LLMUnavailable as exc:
        advertencias.append(f"LLM no disponible: {exc}")

    return {**mejor, "paginas": paginas, "advertencias": advertencias}
