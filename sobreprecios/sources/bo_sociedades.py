"""Boletín Oficial, SEGUNDA SECCIÓN (Sociedades y avisos judiciales): histórico
societario de un proveedor — autoridades, domicilio, objeto, capital, constitución.

Por qué una fuente separada de `boletin_oficial.py` (que lee PRIMERA/TERCERA,
las adjudicaciones): la segunda sección no se busca por texto libre, se busca
por un ÍNDICE ALFABÉTICO DE DENOMINACIONES (razón social) con un endpoint y un
formato de respuesta totalmente distintos (confirmado en vivo 2026-10-11).

Endpoint (no documentado; lo usa `/js/busqueda.js`, función
`cargarRespuestasAvanzadaSegunda`):
  POST /busquedaAvanzada/realizarBusqueda/segunda
       form: params=<json>, array_volver=[]  (misma cookie de sesión que PRIMERA:
       GET / primero). Requiere X-Requested-With: XMLHttpRequest.
  GET  /detalleAviso/segunda/<id>/<YYYYMMDD>   HTML (o PDF embebido, ver abajo).

Parámetros de `params` relevantes para segunda (los demás son los mismos que
`boletin_oficial.BOClient.search`, con seccion=[2] y seccionesOriginales=[2]):
  denominacion          texto a buscar en el índice de razón social.
  comienzaDenominacion  True = "empieza con"; False (default acá) = contiene.
  ordenamientoSegunda   True = orden Denominación-Rubro (el único que probamos).
  fechaDesde/fechaHasta dd/mm/yyyy — SÍ filtran (confirmado: variar el rango
                        cambia `cantidad_result_seccion`), a diferencia de lo
                        que podría suponerse de un índice puramente alfabético.

Respuesta: {"error":0, "content": {"html", "cantidad_result_seccion" (int,
no un dict como en primera), "sig_pag", "ultimos_items": {"itemExterno",
"itemInterno"}}}. El HTML son `<div class="linea-aviso">` anidados: un
`<p class="item">` con la denominación tal como está indexada, y dentro
`<a class="puntero" href="/detalleAviso/segunda/<ID>/<FECHA>?busqueda=1">`
por cada aviso de esa razón social. OJO: <ID> puede ser alfanumérico
("A1349431", "H2383968") — el regex de `boletin_oficial._AVISO_RE` (solo
\\d+) NO lo matchea, por eso este módulo tiene el suyo.

GOTCHAS verificados en vivo (2026-10-11), todos importantes para journalists:

1. **El índice de denominación ignora palabras genéricas.** Buscar
   "COMPAÑIA AMERICANA" (ambas palabras presentes en el nombre real) da 0
   resultados; "AMERICANA ALIMENTOS" sí encuentra "COMPAÑIA AMERICANA DE
   ALIMENTOS S.A.". "COMPAÑIA" sola también da 0. "LA SIBILA" y "SIBILA" dan
   el mismo resultado (12). Conclusión: el buscador parece descartar palabras
   muy comunes del nombre societario (COMPAÑIA, LA, EL, DE...) sin avisar —
   una búsqueda con 0 resultados NO prueba que la sociedad no esté. Hay que
   buscar con la o las palabras más distintivas del nombre, nunca con el
   nombre completo si incluye términos genéricos. `_tokens_distintivos()`
   aplica esta regla al armar queries y al acotar texto (ver más abajo).

2. **Cobertura geográfica: domicilio LEGAL en CABA, no domicilio fiscal ni
   planta.** La sección segunda nacional solo trae sociedades cuyo domicilio
   legal/sede inscripta cae bajo jurisdicción de IGJ (Ciudad de Buenos
   Aires) — las que publican en un Boletín provincial (Santa Fe, Salta...)
   no aparecen nunca aquí, sin importar dónde operen. Confirmado buscando
   los 4 ejemplos del encargo: "MILKAUT" (Franck, Santa Fe) y "HOLOWATY"
   como sociedad (Holowaty Hermanos SRL, Cerrillos, Salta) → 0 resultados
   para la empresa (HOLOWATY sí matchea personas físicas homónimas, otro
   motivo para cruzar siempre por CUIT y no solo por nombre). "SIBILA S.A."
   (Rosario Norte, Santa Fe) tampoco aparece — lo que sí aparece bajo
   "SIBILA" son sociedades homónimas DE CABA sin relación. En cambio
   "COMPAÑIA AMERICANA DE ALIMENTOS S.A." SÍ aparece (domicilio legal CABA,
   aunque el domicilio fiscal/planta en AFIP está en Tortuguitas, Pcia. de
   Bs. As. — domicilio legal y domicilio fiscal son cosas distintas y pueden
   no coincidir). Para el universo completo de proveedores (muchos del
   interior) esta fuente sola deja afuera a la mayoría; hace falta cruzar
   con el Registro Nacional de Sociedades (ver `docs` / reporte de research)
   que sí cubre todas las jurisdicciones pero sin autoridades societarias.

3. **Texto plano solo desde ~2012.** Avisos con id "A..." (ej. A620576,
   2016; A194854, 2012-12-06) tienen `#cuerpoDetalleAviso` HTML legible.
   Avisos más viejos con id "H..." (ej. H2383968, 2010) NO tienen ese nodo:
   la página trae un PDF de LA PÁGINA COMPLETA del Boletín de ese día,
   codificado base64 e inyectado por JS (`convertBase64InUrlBlob(...)`) en
   un `<iframe>`. Hay que decodificar ese base64 y extraerlo con pdfplumber
   (ver `_extraer_pdf_de_html`).

4. **La página escaneada es a 3 columnas y pdfplumber NO respeta columnas.**
   `page.extract_text()` lee por renglón de izquierda a derecha cruzando las
   3 columnas, así que el texto de 2-3 avisos distintos queda intercalado
   palabra por palabra en la misma "línea" de salida — y un corte de línea
   con guión ("Ro-\\nmero Bonomi") puede terminar partido entre columnas de
   avisos totalmente distintos, no solo entre renglones de uno mismo. Esto
   es un problema real de extracción, no un bug de este módulo: una
   extracción prolija requeriría agrupar palabras por rango de x (columna)
   con `page.extract_words()` antes de concatenar texto por columna. NO
   está implementado acá (alcance de prototipo). Mitigación aplicada:
   `extraer_hechos` acota el texto a una ventana alrededor de las menciones
   del nombre distintivo de la empresa (`_ambito_relevante`) para evitar
   atribuirle a la empresa buscada hechos de la sociedad vecina en la misma
   página (confirmado con level test: un "resolvió disolver la sociedad" de
   otra firma en la misma página NO debe aparecer como hecho de la buscada).
   Pero dentro de la ventana, nombres cortados por la intercalación de
   columnas pueden salir incompletos o ilegibles: tratar los hechos
   extraídos de avisos "H" (OCR de página completa) como BAJA CONFIANZA,
   good enough para activar una revisión humana, no para citar sin mirar
   `fuente_texto`.

Sin anti-bot verificado a la fecha (mismo dominio y sesión que PRIMERA/TERCERA
en `boletin_oficial.py`); se reusa el mismo DELAY_S y User-Agent por cortesía
con la infraestructura pública.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import re

import httpx
from bs4 import BeautifulSoup

from .. import config

logger = logging.getLogger(__name__)

BASE = "https://www.boletinoficial.gob.ar"
SEARCH_URL = BASE + "/busquedaAvanzada/realizarBusqueda/segunda"
DELAY_S = 0.6
USER_AGENT = "Mozilla/5.0 (compatible; transparencia-compras/0.1; investigacion de precios publicos)"

# Palabras que el índice de denominación del BO parece ignorar (verificado en
# vivo: "COMPAÑIA" y "LA" solas dan 0 resultados aunque estén en el nombre
# real). Buscar/acotar siempre por lo que queda después de sacar esto.
_STOPWORDS_RAZON_SOCIAL = {
    "SA", "S.A.", "SRL", "S.R.L.", "SRL.", "SAS", "S.A.S.", "SACI", "SAIC", "SAICF",
    "LA", "EL", "LOS", "LAS", "DE", "DEL", "Y", "E", "CIA", "COMPAÑIA", "COMPAÑÍA",
    "SOCIEDAD", "ANONIMA", "ANÓNIMA", "RESPONSABILIDAD", "LIMITADA",
}

_AVISO_SEGUNDA_RE = re.compile(r"/detalleAviso/segunda/([A-Za-z0-9]+)/(\d{8})")
_PDF_B64_RE = re.compile(r'convertBase64InUrlBlob\("([A-Za-z0-9+/=]+)"\)')
_OCR_MARKER_RE = re.compile(r"#I\d+I#")


def _tokens_distintivos(razon_social: str) -> list[str]:
    """Palabras de 3+ letras de `razon_social` que no son stopwords societarias.

    Se usan tanto para construir la query de búsqueda (si el caller no pasa
    una ya recortada) como para acotar el texto de un aviso viejo (gotcha 4).
    """
    crudos = re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]+", razon_social.upper())
    return [t for t in crudos if t not in _STOPWORDS_RAZON_SOCIAL and len(t) > 2]


class BOSegundaClient:
    """Cliente HTTP para la segunda sección. Mismo patrón que `boletin_oficial.BOClient`
    pero endpoint, parámetros y forma de la respuesta son distintos (ver
    docstring del módulo)."""

    def __init__(self, delay_s: float = DELAY_S):
        self.delay_s = delay_s
        self._client = httpx.Client(timeout=45, follow_redirects=True,
                                    headers={"User-Agent": USER_AGENT})
        self._warm = False

    def _ensure_session(self) -> None:
        if not self._warm:
            self._client.get(BASE + "/")
            self._warm = True

    def _sleep(self) -> None:
        import time
        time.sleep(self.delay_s)

    def buscar(self, denominacion: str, desde: dt.date | None = None, hasta: dt.date | None = None,
               comienza: bool = False, max_paginas: int = 10) -> list[dict]:
        """Avisos indexados bajo `denominacion` (índice de razón social, no texto
        libre: ver gotcha 1 del módulo). Devuelve dicts crudos de `_parse_html`,
        sin deduplicar entre páginas (lo hace `buscar_avisos_sociedad`)."""
        self._ensure_session()
        params = {
            "texto": "", "seccion": [2], "rubros": [], "nroNorma": "", "anioNorma": "",
            "ordenamiento": "fecha", "denominacion": denominacion, "tipoContratacion": "",
            "anioContratacion": "", "nroContratacion": "",
            "fechaDesde": desde.strftime("%d/%m/%Y") if desde else "",
            "fechaHasta": hasta.strftime("%d/%m/%Y") if hasta else "",
            "fecha": "", "tipoBusqueda": "Avanzada", "numeroPagina": 1, "ultimoRubro": "",
            "busquedaRubro": False, "hayMasResultadosBusqueda": True,
            "ejecutandoLlamadaAsincronicaBusqueda": False, "ultimaSeccion": "",
            "todasLasPalabras": True, "filtroPorRubrosSeccion": False,
            "filtroPorRubroBusqueda": False, "filtroPorSeccionBusqueda": False,
            "busquedaOriginal": True, "comienzaDenominacion": comienza, "ordenamientoSegunda": True,
            "seccionesOriginales": [2], "ultimoItemExterno": None, "ultimoItemInterno": None,
        }
        out: list[dict] = []
        seen_html = set()
        for _ in range(max_paginas):
            resp = self._client.post(SEARCH_URL, data={"params": json.dumps(params), "array_volver": "[]"},
                                      headers={"X-Requested-With": "XMLHttpRequest",
                                               "Referer": BASE + "/busquedaAvanzada/segunda"})
            self._sleep()
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("error"):
                raise RuntimeError(f"BO segunda búsqueda falló: {payload.get('mensajes')}")
            content = payload.get("content") or {}
            html = content.get("html") or ""
            if not html or html in seen_html:
                break
            seen_html.add(html)
            out.extend(_parse_html(html))
            ultimos = content.get("ultimos_items") or {}
            ext = (ultimos.get("itemExterno") or {}).get("nombre")
            inte = (ultimos.get("itemInterno") or {}).get("nombre")
            if not ext and not inte:
                break
            params.update(numeroPagina=content.get("sig_pag"), ultimoItemExterno=ext, ultimoItemInterno=inte,
                          busquedaOriginal=False)
        return out

    def fetch_html(self, aviso_id: str, fecha_yyyymmdd: str) -> str:
        """HTML crudo de `/detalleAviso/segunda/<id>/<fecha>`, con cache en disco
        (los avisos publicados no cambian: ver `boletin_oficial.fetch_html`)."""
        path = config.cache_dir("bo", "segunda") / f"{fecha_yyyymmdd}_{aviso_id}.html"
        if path.exists() and path.stat().st_size > 500:
            return path.read_text(encoding="utf-8", errors="replace")
        resp = self._client.get(f"{BASE}/detalleAviso/segunda/{aviso_id}/{fecha_yyyymmdd}")
        self._sleep()
        resp.raise_for_status()
        path.write_text(resp.text, encoding="utf-8")
        return resp.text


def _parse_html(html: str) -> list[dict]:
    """HTML de resultados de segunda -> lista de avisos con su denominación indexada.

    Estructura real (ver docstring del módulo): `<div class="linea-aviso">`
    con un `<p class="item">` (denominación) y uno o más `<a class="puntero"
    href="/detalleAviso/segunda/<id>/<fecha>">` adentro.
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for bloque in soup.find_all("div", class_="linea-aviso"):
        item_p = bloque.find("p", class_="item")
        denominacion_bo = item_p.get_text(" ", strip=True) if item_p else ""
        for link in bloque.find_all("a", href=True):
            match = _AVISO_SEGUNDA_RE.search(link["href"])
            if not match:
                continue
            aviso_id, fecha = match.group(1), match.group(2)
            out.append({
                "denominacion_bo": re.sub(r"\s+", " ", denominacion_bo).strip(),
                "aviso_id": aviso_id,
                "fecha_yyyymmdd": fecha,
                "fecha_iso": f"{fecha[:4]}-{fecha[4:6]}-{fecha[6:]}",
                "url": f"{BASE}/detalleAviso/segunda/{aviso_id}/{fecha}",
            })
    return out


def buscar_avisos_sociedad(razon_social: str, desde: dt.date, hasta: dt.date,
                           client: BOSegundaClient | None = None) -> list[dict]:
    """Avisos de segunda sección para `razon_social` entre `desde` y `hasta`.

    Busca con los tokens distintivos del nombre (gotcha 1: palabras genéricas
    como "COMPAÑIA" o "LA" dan 0 resultados aunque la sociedad exista) y
    devuelve solo los avisos cuya `denominacion_bo` comparte esos tokens — el
    índice es por substring de palabra, así que "AMERICANA ALIMENTOS" también
    trae sociedades homónimas no relacionadas; filtrar por tokens de nuevo del
    lado del cliente reduce ese ruido pero NO lo elimina (hace falta cruzar
    por CUIT contra otra fuente, ver reporte de research).

    Devuelve lista vacía si la sociedad no tiene domicilio legal en CABA (no
    está en este índice: gotcha 2) — eso no significa que la sociedad no
    exista, solo que no publica en el Boletín nacional.
    """
    tokens = _tokens_distintivos(razon_social)
    if not tokens:
        logger.warning("razón social sin tokens distintivos: %r", razon_social)
        return []
    query = " ".join(tokens[:3])   # 3 alcanza para acotar sin perder precisión (probado en vivo)
    client = client or BOSegundaClient()
    crudos = client.buscar(query, desde, hasta)
    tokens_set = set(tokens)
    out, vistos = [], set()
    for aviso in crudos:
        denom_tokens = set(_tokens_distintivos(aviso["denominacion_bo"]))
        if not (tokens_set & denom_tokens):
            continue
        key = (aviso["aviso_id"], aviso["fecha_yyyymmdd"])
        if key in vistos:
            continue
        vistos.add(key)
        out.append(aviso)
    return out


def _extraer_pdf_de_html(html: str) -> bytes | None:
    match = _PDF_B64_RE.search(html)
    if not match:
        return None
    try:
        return base64.b64decode(match.group(1))
    except (ValueError, base64.binascii.Error) as exc:   # pdf corrupto/truncado: no tumbar el caller
        logger.warning("no se pudo decodificar el PDF embebido: %s", exc)
        return None


def _texto_desde_html(html: str) -> str:
    """Texto plano de un aviso moderno (id "A...", ~2012+): nodo HTML directo."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find(id="cuerpoDetalleAviso") or soup.find(class_="cuerpoDetalleAviso")
    return body.get_text("\n", strip=True) if body else ""


def _texto_desde_pdf_embebido(html: str) -> str:
    """Texto OCR de un aviso viejo (id "H...", pre-~2012): la página completa del
    día viene como PDF en base64 (gotcha 3). El texto sale con columnas
    intercaladas (gotcha 4) — devolver tal cual, acotar en `extraer_hechos`."""
    pdf_bytes = _extraer_pdf_de_html(html)
    if not pdf_bytes:
        return ""
    try:
        import pdfplumber
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:   # pdfplumber puede fallar con PDFs raros: degradar, no tumbar
        logger.warning("no se pudo extraer texto del PDF embebido: %s", exc)
        return ""


def texto_aviso(ref: dict, client: BOSegundaClient | None = None) -> str:
    """Texto plano de un aviso de segunda sección, desde cache si ya se bajó.

    `ref` es un dict de `buscar_avisos_sociedad` (o uno armado a mano con al
    menos `aviso_id` y `fecha_yyyymmdd`). Intenta primero el HTML directo
    (avisos modernos); si no hay `#cuerpoDetalleAviso`, cae al PDF embebido
    de la página completa (avisos viejos, ver gotchas 3 y 4 del módulo — el
    texto que vuelve en ese caso puede traer columnas intercaladas).
    """
    client = client or BOSegundaClient()
    html = client.fetch_html(ref["aviso_id"], ref["fecha_yyyymmdd"])
    texto = _texto_desde_html(html)
    if texto:
        return texto
    texto = _texto_desde_pdf_embebido(html)
    if not texto:
        logger.warning("sin texto para aviso %s/%s (ni HTML ni PDF embebido)",
                       ref.get("aviso_id"), ref.get("fecha_yyyymmdd"))
    return texto


# ---------------------------------------------------------------------------
# extraer_hechos: reglas primero (cubren autoridad/constitución/objeto, que
# son los casos reales vistos en los fixtures), LLM opcional para lo que las
# reglas no resuelven (domicilio/razón social/capital/disolución/socio son
# best-effort: regex razonable pero sin un aviso real en los fixtures que
# las ejercite — documentado así a propósito, no inventar cobertura).
# ---------------------------------------------------------------------------

_CARGO_RE = re.compile(
    r"(?:SOCIOS?\s+GERENTES?|S[IÍ]NDICOS?\s+TITULARES?|S[IÍ]NDICOS?\s+SUPLENTES?|"
    r"DIRECTORES?\s+TITULARES?|DIRECTOR\s+TITULAR|DIRECTORES?\s+SUPLENTES?|DIRECTOR\s+SUPLENTE|"
    r"VICEPRESIDENTE|PRESIDENTE|GERENTES?|APODERADOS?|TITULARES?|SUPLENTES?)\s*:",
    re.IGNORECASE)
_STOP_NOMBRES_RE = re.compile(
    r"\b(TODOS|AMBOS|AUTORIZAD[OA]|INSTRUMENTAD[AO]|FIJAN\s+DOMICILIO|CONSTITUYEN\s+DOMICILIO)\b",
    re.IGNORECASE)
_PERSONA_DNI_RE = re.compile(r"([A-Za-zÁÉÍÓÚÑáéíóúñ'’.\- ]+?),?\s*DNI\.?\s*([\d.]{6,12})\.?-?")
_CONSTITUCION_RE = re.compile(
    r"inscript[ao]\s+originariamente\s+en\s+el\s+Registro\s+P[uú]blico\s+de\s+Comercio\s+el\s+"
    r"(\d{1,2}/\d{1,2}/\d{2,4})", re.IGNORECASE)
_OBJETO_RE = re.compile(r"tiene por objeto\s+(.*?)(?:\.-\s*A tal fin|$)", re.IGNORECASE | re.DOTALL)
_DOMICILIO_RE = re.compile(
    r"(?:nuevo domicilio legal|domicilio social|sede social)\s+(?:el\s+establecido\s+en|en)\s+"
    r"([^.,;\n]+)", re.IGNORECASE)
_RAZON_SOCIAL_RE = re.compile(
    r"(?:modific[oó]|cambi[oó])\s+su\s+(?:denominaci[oó]n|raz[oó]n social)[^\"]*?[\"“]?([A-ZÁÉÍÓÚÑ0-9 .,]{4,80})[\"”]?",
    re.IGNORECASE)
_CAPITAL_RE = re.compile(
    r"capital social[^.]{0,80}?\$\s*([\d.,]+)", re.IGNORECASE)
_DISOLUCION_RE = re.compile(r"resolvi[oó]\s+disolver\s+la\s+sociedad", re.IGNORECASE)
_FECHA_RE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b")
# La fecha de la ASAMBLEA/ACTA es la fecha del acto (cuándo empieza a valer el
# hecho); la primera fecha del texto a secas suele ser la de la ESCRITURA
# notarial que lo protocoliza semanas/meses después (confirmado en el fixture
# 2014: escritura 09/06/14, acta 20/01/14 — son 5 meses de diferencia).
_FECHA_ACTO_RE = re.compile(r"ASAMBLEA[^.\d]{0,40}(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})", re.IGNORECASE)

_CARGO_CANON = {
    "PRESIDENTE": "presidente", "VICEPRESIDENTE": "vicepresidente",
    "DIRECTOR TITULAR": "director_titular", "DIRECTORES TITULARES": "director_titular",
    "TITULAR": "director_titular", "TITULARES": "director_titular",
    "DIRECTOR SUPLENTE": "director_suplente", "DIRECTORES SUPLENTES": "director_suplente",
    "SUPLENTE": "director_suplente", "SUPLENTES": "director_suplente",
    "SINDICO TITULAR": "sindico_titular", "SINDICOS TITULARES": "sindico_titular",
    "SÍNDICO TITULAR": "sindico_titular", "SÍNDICOS TITULARES": "sindico_titular",
    "SINDICO SUPLENTE": "sindico_suplente", "SINDICOS SUPLENTES": "sindico_suplente",
    "SOCIO GERENTE": "socio_gerente", "SOCIOS GERENTES": "socio_gerente",
    "GERENTE": "gerente", "GERENTES": "gerente",
    "APODERADO": "apoderado", "APODERADOS": "apoderado",
}


def _canon_cargo(crudo: str) -> str:
    key = re.sub(r"\s+", " ", crudo.upper().strip())
    return _CARGO_CANON.get(key, key.lower().replace(" ", "_"))


def _normalizar_fecha(texto_fecha: str) -> str | None:
    match = _FECHA_RE.search(texto_fecha)
    if not match:
        return None
    d, m, y = match.groups()
    if len(y) == 2:
        y = ("20" if int(y) < 50 else "19") + y
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def _ambito_relevante(texto: str, razon_social: str) -> str:
    """Acota `texto` a los párrafos cerca de las menciones de `razon_social`.

    Para avisos modernos (HTML limpio, un solo aviso por texto) es un no-op:
    no hay marcadores `#I<id>I#` y se devuelve el texto entero. Para avisos
    viejos (PDF de página completa, gotcha 3) evita leerle a la empresa
    buscada los hechos de la sociedad de al lado en la misma página (ver
    gotcha 4) — PERO dentro de la ventana el texto puede seguir viniendo con
    columnas intercaladas, así que esto reduce falsos positivos de OTRAS
    empresas, no garantiza que lo que quede sea perfectamente legible.
    """
    if not _OCR_MARKER_RE.search(texto):
        return texto
    tokens = _tokens_distintivos(razon_social)
    if not tokens:
        return texto
    upper = texto.upper()
    spans: list[tuple[int, int]] = []
    ANTES, DESPUES = 300, 1000   # asimétrico: el cuerpo del aviso sigue al nombre, no lo precede
    for tok in tokens:
        for match in re.finditer(re.escape(tok), upper):
            spans.append((max(0, match.start() - ANTES), min(len(texto), match.end() + DESPUES)))
    if not spans:
        logger.warning("ningún token distintivo de %r encontrado en el texto OCR; devolviendo todo "
                       "(riesgo de mezclar hechos de otra sociedad)", razon_social)
        return texto
    spans.sort()
    merged = [spans[0]]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return "\n".join(texto[s:e] for s, e in merged)


def extraer_hechos(texto: str, razon_social: str, cuit: str | None = None) -> list[dict]:
    """Reglas (regex) sobre el texto de un aviso -> lista de hechos fechados.

    Cada hecho: {tipo, persona, documento, cargo, valor, vigente_desde,
    fuente_texto}. `tipo` ∈ autoridad|socio|domicilio|razon_social|capital|
    objeto|constitucion|disolucion. `vigente_desde` es la primera fecha
    dd/mm/yyyy encontrada en el ámbito relevante del aviso (heurística: casi
    todos los avisos tienen la fecha del acto cerca del principio) — si no
    hay fecha, el caller decide si usar `fecha_iso` del aviso (la de
    PUBLICACIÓN, que no es lo mismo: puede ser semanas después del acto).

    Cuando las reglas no encuentran nada pero el texto no está vacío, el
    caller puede recurrir a `sobreprecios.llm.complete_json` con el prompt
    de `prompts/extraer_hechos_sociedad.md` (no se llama automáticamente
    acá: este módulo no decide presupuesto de LLM, eso es de `llm.py` /
    quien orqueste el pipeline).
    """
    if not texto:
        return []
    ambito = _ambito_relevante(texto, razon_social)
    acto_match = _FECHA_ACTO_RE.search(ambito)
    fecha_acto = _normalizar_fecha(acto_match.group(1)) if acto_match else _normalizar_fecha(ambito)
    hechos: list[dict] = []

    def _snippet(span_texto: str, centro: int, radio: int = 160) -> str:
        s, e = max(0, centro - radio), min(len(span_texto), centro + radio)
        return re.sub(r"\s+", " ", span_texto[s:e]).strip()

    # --- autoridad: "CARGO: Nombre[, DNI N]..." hasta el próximo cargo o un stop ---
    cargo_matches = list(_CARGO_RE.finditer(ambito))
    for i, cmatch in enumerate(cargo_matches):
        start = cmatch.end()
        end = cargo_matches[i + 1].start() if i + 1 < len(cargo_matches) else len(ambito)
        span = ambito[start:end]
        stop = _STOP_NOMBRES_RE.search(span)
        if stop:
            span = span[:stop.start()]
        cargo = _canon_cargo(cmatch.group(0)[:-1])
        dni_matches = list(_PERSONA_DNI_RE.finditer(span))
        if dni_matches:
            personas = [(m.group(1), re.sub(r"\D", "", m.group(2))) for m in dni_matches]
        else:
            personas = [(n, None) for n in re.split(r",|;|\sy\s", span)]
        for nombre, documento in personas:
            nombre = re.sub(r"\s+", " ", nombre).strip(" ,.;-")
            if len(nombre) <= 2:
                continue
            hechos.append({
                "tipo": "autoridad", "persona": nombre, "documento": documento, "cargo": cargo,
                "valor": None, "vigente_desde": fecha_acto,
                "fuente_texto": _snippet(ambito, start),
            })

    # --- constitución: fecha de inscripción originaria ---
    cmatch = _CONSTITUCION_RE.search(ambito)
    if cmatch:
        fecha = _normalizar_fecha(cmatch.group(1))
        hechos.append({
            "tipo": "constitucion", "persona": None, "documento": cuit, "cargo": None,
            "valor": razon_social, "vigente_desde": fecha,
            "fuente_texto": _snippet(ambito, cmatch.start()),
        })

    # --- objeto social ---
    omatch = _OBJETO_RE.search(ambito)
    if omatch:
        hechos.append({
            "tipo": "objeto", "persona": None, "documento": cuit, "cargo": None,
            "valor": re.sub(r"\s+", " ", omatch.group(1)).strip()[:2000], "vigente_desde": fecha_acto,
            "fuente_texto": _snippet(ambito, omatch.start()),
        })

    # --- domicilio social (best-effort: sin fixture real que lo ejercite) ---
    dmatch = _DOMICILIO_RE.search(ambito)
    if dmatch:
        hechos.append({
            "tipo": "domicilio", "persona": None, "documento": cuit, "cargo": None,
            "valor": re.sub(r"\s+", " ", dmatch.group(1)).strip(), "vigente_desde": fecha_acto,
            "fuente_texto": _snippet(ambito, dmatch.start()),
        })

    # --- cambio de razón social (best-effort) ---
    rmatch = _RAZON_SOCIAL_RE.search(ambito)
    if rmatch:
        hechos.append({
            "tipo": "razon_social", "persona": None, "documento": cuit, "cargo": None,
            "valor": rmatch.group(1).strip(), "vigente_desde": fecha_acto,
            "fuente_texto": _snippet(ambito, rmatch.start()),
        })

    # --- capital social (best-effort) ---
    kmatch = _CAPITAL_RE.search(ambito)
    if kmatch:
        hechos.append({
            "tipo": "capital", "persona": None, "documento": cuit, "cargo": None,
            "valor": kmatch.group(1), "vigente_desde": fecha_acto,
            "fuente_texto": _snippet(ambito, kmatch.start()),
        })

    # --- disolución ---
    for dismatch in _DISOLUCION_RE.finditer(ambito):
        hechos.append({
            "tipo": "disolucion", "persona": None, "documento": cuit, "cargo": None,
            "valor": None, "vigente_desde": fecha_acto,
            "fuente_texto": _snippet(ambito, dismatch.start()),
        })

    return hechos
