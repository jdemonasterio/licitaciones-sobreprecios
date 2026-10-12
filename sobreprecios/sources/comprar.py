"""COMPR.AR (comprar.gob.ar): procesos de compra, renglones, ofertas y adjudicación.

Por qué existe este módulo: desde ~2024 el Boletín Oficial dejó de describir
lo que se compra. Solo cita el número de proceso COMPR.AR (ej. "LICITACIÓN
PÚBLICA Nº 80-0014-LPU25... renglón 1 por 480 unidades, monto total
$32.352.000"). La descripción del renglón, cantidad, oferentes, precios y el
ganador viven en COMPR.AR. Este módulo los trae.

Verificado en una sesión nueva, SIN LOGIN (2026-10-11, contra 80-0014-LPU25 —
Ministerio de Salud, medicamentos oncológicos, adjudicado — y 14/2-0031-LPU22
— racionamiento Gendarmería/CNEA — además de varios "próximos a abrir" sin
oferta todavía). No se probó ninguna pantalla que requiera cuenta (ofertar,
escritorio del proveedor): quedan fuera de alcance.

Arquitectura del sitio: ASP.NET WebForms + DevExpress. Cada página manda su
estado completo (__VIEWSTATE, 100-400KB en base64) y los botones son
`__doPostBack('target', 'arg')` sobre el mismo formulario, no links de
verdad. A simple vista parece necesitar un navegador (y así lo probamos
primero, con Playwright) — pero HAY UN ATAJO: alcanza con tomar los
<input>/<select> que trae cada respuesta, pisar el campo que cambiamos y
volver a mandar todo por POST con httpx. Ninguna de las páginas de este
flujo depende de cookies de sesión: se probó repetidas veces desde clientes
httpx nuevos (sin warm-up, sin login) y respondieron igual. Por eso este
módulo NO usa Playwright: solo httpx + BeautifulSoup, mismo patrón que
boletin_oficial.py.

Flujo completo (5 pasos, 4 páginas, TODAS públicas):
  1. GET  /BuscarAvanzado.aspx                     -> formulario de búsqueda
     POST (mismo form; __EVENTTARGET=botón)        -> grilla de resultados
     (otro POST con __EVENTTARGET=<numero>$lnkNumeroProceso del resultado
     elegido) -> redirige a (2).
  2. GET /PLIEGO/VistaPreviaPliegoCiudadano.aspx?qs=<token opaco>
     Metadata del proceso + renglones (código, descripción, cantidad) +
     cronograma + actos administrativos + "Documento contractual por
     proveedor" (= las órdenes de compra: total $ por proveedor, pero NO
     desglosado por renglón). Dos links traen su URL completa ya embebida
     en el HTML estático (un detalle de WebForms: `__doPostBack` normal NO
     trae URL, pero estos usan `WebForm_DoPostBackWithOptions(...)` con un
     5to parámetro que SÍ es la URL de destino):
       - "Ver acta de apertura"       -> URL directa a (3)
       - "Ver vista previa dictamen"  -> URL directa a (4)
     Un tercer link NO trae URL embebida (es un postback de verdad, hay que
     re-mandar el formulario de esta misma página con
     __EVENTTARGET=...lnkVerCuadroComparativo):
       - "Ver cuadro comparativo"     -> redirige a (5)
  3. GET /EVALUACIONOFERTA/GenerarActaAperturaBastrap.aspx?qs=<token>
     Ofertas confirmadas: oferente, CUIT, fecha, PRECIO TOTAL GENERAL (no
     por renglón), garantía. Útil para ver quién se presentó; no para precio
     unitario.
  4. GET /EVALUACIONOFERTA/PreAdjudicarVisualizarDictamen.aspx?qs=<token>
     "Ofertas recomendadas": el GANADOR por renglón (número renglón,
     oferente, cantidad, precio unitario, precio total renglón). Esta es la
     adjudicación real. Confirmado contra el ejemplo exacto citado por el
     BO: 80-0014-LPU25 renglón 1 = 480 unidades, $32.352.000 → coincide con
     lo que trae esta página (MICROSULES, $67.400 unitario). No trae CUIT:
     se cruza por nombre de proveedor contra el cuadro comparativo (5).
  5. GET /EVALUACIONOFERTA/VerCuadroComparativo.aspx?qs=<token>
     La matriz completa: por renglón, por CADA oferente que cotizó ese
     renglón: precio unitario, cantidad ofertada, total, moneda,
     especificaciones técnicas (de ahí sale la marca, cuando el oferente la
     puso en su "especificación"), nombre + CUIT. Es la fuente más rica para
     comparar precios entre competidores de un mismo renglón, no solo el
     que ganó.

El token "qs" es determinístico: el mismo número de proceso devuelve
siempre el mismo qs, pedido desde sesiones nuevas sin cookies. No es un
nonce de sesión. Por eso se puede cachear el HTML de (2)-(5) sin
vencimiento una vez que el proceso está adjudicado (no va a cambiar).

Gotchas / nombres ambiguos del propio sitio (para quien use estos dicts):
  - "organismo" vs "uoc" vs "saf": el sitio NO usa un término único. La
    grilla de búsqueda trae "Unidad Ejecutora" (quién gestiona, ej.
    "80/000 - Dirección General de Administración - MS") y "Servicio
    Administrativo Financiero" (el ministerio/jurisdicción, ej. "310 -
    Ministerio de Salud"). La página de detalle repite esto como "Unidad
    Operativa de Contrataciones". Este módulo mapea: `organismo` = SAF
    completo (el ministerio), `saf` = solo el código numérico del SAF,
    `uoc` = unidad operativa completa. `organismo`/`saf` solo se completan
    cuando `detalle_proceso` resuelve por NÚMERO (pasa por la grilla); si
    se le pasa directamente una URL con qs, quedan en "" (no hay forma de
    obtenerlos desde la página de detalle sola).
  - `estado` (Adjudicado/En Evaluación/etc.) tampoco aparece en la página
    de detalle: solo en la grilla de búsqueda. Mismo gotcha que el punto
    anterior.
  - El filtro de fecha de apertura (desde/hasta) de la búsqueda avanzada
    usa un ASPxDateEdit de DevExpress cuyo campo oculto exacto no se
    terminó de reversear (ver research_comprar.md, "abierto"). Por las
    dudas, además de mandarlo, filtramos en Python con la fecha que ya
    trae cada fila de la grilla — así el resultado es correcto aunque el
    filtro del lado del servidor no se aplique.
  - Los ids de control de DevExpress (ctl02, ctl03...) NO son estables
    entre búsquedas (dependen de cuántas filas hay antes). Nunca asumir un
    id fijo: siempre resolver por sufijo de id (`re.compile(r"_lblX$")`) o
    por atributo (title=, class=).
  - Actas, dictámenes y pliegos/anexos en PDF (ANEXO II, ESPECIFICACION
    TECNICA.pdf, etc.) NO tienen un link de descarga directo verificado:
    sus botones "Ver"/"Descargar" son postbacks que no se probaron a fondo
    (abren un visor de GDE que puede necesitar más contexto). `documentos`
    en este módulo solo incluye lo que SÍ se confirmó descargable sin
    login: la propia acta de apertura y el propio dictamen.

Ritmo: no se detectó WAF/challenge, pero es infraestructura pública de un
organismo de control — misma regla que boletin_oficial.py: ir despacio
(DELAY_S entre requests), sin paralelismo.

Actos administrativos, ofertas desestimadas y plazo de pago (verificado en vivo 2026-10-12
contra 6 procesos 2018-2026: 14/2-0001-LPR19, 14/1-0424-CDI18, 132/61-0010-CDI19, 21-0001-LPU22,
14/2-0031-LPU22, 80-0014-LPU25 — ver DESIGN_sobreprecios.md):
  - "Actos administrativos" (autorización de pliego, autorización de llamado, adjudicación y OC)
    SÍ aparece en VistaPreviaPliegoCiudadano (tabla `UC_ActosAdministrativos_gvActosAdministrativos`,
    `_parse_actos_administrativos`) en TODOS los procesos probados, viejos y nuevos. Pero su botón
    "Ver" es el mismo gotcha de arriba (postback a un visor de GDE que no resolvió sin sesión al
    probarlo de nuevo): no hay PDF descargable sin login. Por eso `documentos`/`comprar_actos` no
    guardan el acto, guardan el link al PROCESO con un fragmento (`ACTOS_ANCHOR_ID`) que salta a esa
    sección, para que una persona lo abra y lea número/fecha del acto con sus propios ojos.
  - "Ofertas desestimadas" con su causa SÍ está en el dictamen (PreAdjudicarVisualizarDictamen.aspx,
    sección aparte de "Ofertas recomendadas", repetidor `rptOfertasNoCumplieron`) — ver
    `parse_ofertas_desestimadas_html`. No está paginada junto al grid de recomendadas.
  - "Plazo de pago" (cuántos días tarda el Estado en pagarle al proveedor) NO aparece como campo
    estructurado en ninguno de los 6 procesos probados (ni viejos ni nuevos): el Pliego Único de
    Bases y Condiciones Generales (Decreto 1030/2016) lo fija a nivel nacional en un documento GEDO
    común a casi todo proceso, no descargable sin sesión. Lo que SÍ hay en la página (y no hay que
    confundir): "Plazo mantenimiento de la oferta" (validez de la oferta, no de pago), "Fecha
    estimada del inicio del contrato" (plazo de ENTREGA tras la orden de compra) y "Duración del
    contrato". `parse_plazo_pago` solo rescata el caso en que el plazo de pago SÍ aparece como texto
    libre (cláusula particular citada inline, o el texto de un aviso del BO); en los 6 procesos
    probados da (None, None) en los 6 — "sin dato" es el resultado correcto y esperado hoy.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html as _html
import logging
import re
import time

import httpx
from bs4 import BeautifulSoup

from .. import config
from ..textnorm import norm
from ..units import parse_ars, parse_number

logger = logging.getLogger(__name__)

BASE = "https://comprar.gob.ar"
BUSCAR_URL = f"{BASE}/BuscarAvanzado.aspx"
DELAY_S = 0.8
FUENTE = "comprar"
USER_AGENT = "Mozilla/5.0 (compatible; transparencia-compras/0.1; investigacion de precios publicos)"

# Códigos de ctl00$CPH1$ddlRubro vistos en BuscarAvanzado.aspx (verificado
# 2026-10-11). Pasalos tal cual a buscar_procesos(rubro=...).
RUBRO_ALIMENTOS = "80021"
RUBRO_LIMPIEZA = "79973"

# Códigos de ctl00$CPH1$ddlEstadoProceso más útiles para sobreprecio (solo
# interesa lo YA adjudicado, con precio real).
ESTADO_ADJUDICADO = "21"
ESTADO_ADJUDICADO_CON_OC = "47"

_FECHA_RE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")
_POSTBACK_TARGET_RE = re.compile(r"__doPostBack\('([^']+)'")
# WebForm_PostBackOptions(target, argument, validation, validationGroup, actionUrl, ...)
_POSTBACK_OPTIONS_RE = re.compile(
    r'WebForm_PostBackOptions\("[^"]*",\s*"[^"]*",\s*\w+,\s*"[^"]*",\s*"([^"]*)"'
)

# El id del <span> título de "Actos administrativos" es un control DECLARADO en el .aspx
# (UC_ActosAdministrativos), no uno autonumerado por DevExpress (ctl02, ctl03...): es estable
# entre procesos (verificado 2026-10-12 en 6 procesos 2018-2026). Se usa como ancla (#fragmento)
# del link al proceso para "Ver documento" de un acto: NO hay descarga directa confirmada (el
# botón "Ver" es un postback que abre un visor de GDE que requiere sesión — mismo gotcha que
# acta/dictamen en PDF, ver docstring del módulo).
ACTOS_ANCHOR_ID = "ctl00_CPH1_UCVistaPreviaPliego_UC_ActosAdministrativos_lblTitulo"


class ComprarError(RuntimeError):
    """El proceso no existe, no tiene las páginas esperadas, o COMPR.AR cambió de forma."""


class ComprarClient:
    """Sesión httpx que repite a mano los postbacks de WebForms (ver docstring del módulo).

    No guarda ninguna cookie de sesión a propósito distinto del default de
    httpx: no hace falta (ver "El token qs es determinístico" arriba). El
    cliente se reutiliza entre llamadas solo para no pagar handshake TLS de
    nuevo en cada request.
    """

    def __init__(self, delay_s: float = DELAY_S, use_cache: bool = True):
        self.delay_s = delay_s
        self.use_cache = use_cache
        self._client = httpx.Client(timeout=45, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT})

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ComprarClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _sleep(self) -> None:
        time.sleep(self.delay_s)

    def _get(self, url: str) -> httpx.Response:
        resp = self._client.get(url)
        self._sleep()
        resp.raise_for_status()
        return resp

    def _post(self, url: str, data: dict) -> httpx.Response:
        resp = self._client.post(url, data=data)
        self._sleep()
        resp.raise_for_status()
        return resp

    @staticmethod
    def _form_fields(html: str) -> tuple[dict, BeautifulSoup]:
        """Todos los <input>/<select> del <form> de WebForms, listos para re-postear.

        No hace falta entender qué hay adentro del __VIEWSTATE: alcanza con
        tomarlo tal cual de la respuesta anterior y pisar solo los campos
        que cambiamos (igual a lo que hace el navegador).
        """
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        fields: dict[str, str] = {}
        if form is None:
            return fields, soup
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype == "checkbox" and not inp.has_attr("checked"):
                continue
            fields[name] = inp.get("value", "") or ""
        for sel in form.find_all("select"):
            name = sel.get("name")
            if not name:
                continue
            opt = sel.find("option", selected=True) or sel.find("option")
            fields[name] = opt.get("value", "") if opt else ""
        return fields, soup

    def _cache_path(self, kind: str, key: str):
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return config.cache_dir("comprar") / f"{kind}_{digest}.html"

    # El detalle cambia (OC nuevas, prórrogas, rectificaciones): se re-consulta pasados N días.
    # Dictamen, acta y cuadro de un proceso ya evaluado no cambian: sin vencimiento.
    TTL_DIAS = {"detalle": 7}

    def _fetch_cached(self, url: str, kind: str) -> str:
        path = self._cache_path(kind, url)
        ttl = self.TTL_DIAS.get(kind)
        fresh = not ttl or (time.time() - path.stat().st_mtime) < ttl * 86400 if path.exists() else False
        if self.use_cache and path.exists() and path.stat().st_size > 500 and fresh:
            return path.read_text(encoding="utf-8", errors="replace")
        html = self._get(url).text
        path.write_text(html, encoding="utf-8")
        return html

    def _postback_and_fetch(self, origin_url: str, origin_html: str, event_target: str,
                             kind: str) -> str:
        """Re-manda el form de `origin_html` con otro __EVENTTARGET (ver 'Ver cuadro comparativo')."""
        path = self._cache_path(kind, origin_url + "::" + event_target)
        if self.use_cache and path.exists() and path.stat().st_size > 500:
            return path.read_text(encoding="utf-8", errors="replace")
        fields, _ = self._form_fields(origin_html)
        fields["__EVENTTARGET"] = event_target
        fields["__EVENTARGUMENT"] = ""
        resp = self._post(origin_url, fields)
        path.write_text(resp.text, encoding="utf-8")
        return resp.text

    def _all_pages(self, url: str, first_html: str, grid_target: str, kind: str,
                   max_pages: int = 40) -> list[str]:
        """Todas las páginas de una grilla paginada (DevExpress/GridView: 'Page$N' por postback).

        El dictamen muestra 30 renglones por página. Leer solo la primera hacía que
        proveedores recomendados en la página 2 parecieran "no recomendados" (falso
        positivo encontrado en 46-0004-LPU25, 2026-10-11).
        """
        pages = [first_html]
        html = first_html
        for n in range(2, max_pages + 1):
            # En el HTML crudo las comillas del href vienen escapadas (&#39;): comparar desescapado.
            if f"{grid_target}','Page${n}'" not in _html.unescape(html):
                break
            path = self._cache_path(kind, f"{url}::{grid_target}::Page${n}")
            if self.use_cache and path.exists() and path.stat().st_size > 500:
                html = path.read_text(encoding="utf-8", errors="replace")
            else:
                fields, _ = self._form_fields(html)
                fields["__EVENTTARGET"] = grid_target
                fields["__EVENTARGUMENT"] = f"Page${n}"
                html = self._post(url, fields).text
                path.write_text(html, encoding="utf-8")
            pages.append(html)
        return pages

    def _resolver_url_detalle(self, numero: str) -> tuple[str | None, dict | None]:
        """Número de proceso -> (URL de VistaPreviaPliegoCiudadano, fila de la grilla).

        Dos postbacks sobre BuscarAvanzado.aspx, igual que un usuario:
        1) búsqueda rápida por número  2) click en el resultado (redirige).
        """
        r1 = self._get(BUSCAR_URL)
        fields, _ = self._form_fields(r1.text)
        fields["__EVENTTARGET"] = "ctl00$CPH1$btnListarPliegoNumero"
        fields["__EVENTARGUMENT"] = ""
        fields["ctl00$CPH1$txtNumeroProceso"] = numero
        r2 = self._post(BUSCAR_URL, fields)

        filas = parse_busqueda_html(r2.text)
        fila = next((f for f in filas if f["numero"].strip().upper() == numero.strip().upper()),
                    filas[0] if filas else None)

        fields2, soup2 = self._form_fields(r2.text)
        link = soup2.find("a", id=lambda x: x and "lnkNumeroProceso" in x and "GridListaPliegos" in x)
        if link is None:
            return None, fila
        match = _POSTBACK_TARGET_RE.search(link.get("href", ""))
        if not match:
            return None, fila
        fields2["__EVENTTARGET"] = match.group(1)
        fields2["__EVENTARGUMENT"] = ""
        r3 = self._post(BUSCAR_URL, fields2)
        url = str(r3.url)
        return (url if "VistaPreviaPliegoCiudadano" in url else None), fila

    def buscar_procesos(self, texto: str | None = None, organismo: str | None = None,
                         rubro: str | None = None, estado: str | None = None,
                         desde: dt.date | None = None, hasta: dt.date | None = None,
                         max_paginas: int = 3, progress=None) -> list[dict]:
        """Búsqueda avanzada (filtros combinables). Ver RUBRO_* / ESTADO_* para códigos conocidos.

        `organismo` es el valor crudo de ddlJurisdicion (no lo reversamos
        completo: inspeccionar BuscarAvanzado.aspx para otros códigos).
        `desde`/`hasta` filtran por fecha de apertura; se aplican también en
        Python sobre lo que devuelve la grilla (ver gotcha del date-editor
        DevExpress en el docstring del módulo).
        """
        r1 = self._get(BUSCAR_URL)
        fields, _ = self._form_fields(r1.text)
        if texto:
            fields["ctl00$CPH1$txtNombrePliego"] = texto
        if organismo:
            fields["ctl00$CPH1$ddlJurisdicion"] = organismo
        if rubro:
            fields["ctl00$CPH1$ddlRubro"] = rubro
        if estado:
            fields["ctl00$CPH1$ddlEstadoProceso"] = estado
        fields["__EVENTTARGET"] = "ctl00$CPH1$btnListarPliegoAvanzado"
        fields["__EVENTARGUMENT"] = ""
        fields.setdefault("ctl00$ScriptManager1", "")

        procesos: list[dict] = []
        html = self._post(BUSCAR_URL, fields).text
        for pagina in range(1, max_paginas + 1):
            nuevas = parse_busqueda_html(html)
            procesos.extend(nuevas)
            if progress:
                progress(f"COMPR.AR página {pagina}: {len(procesos)} procesos")
            if pagina >= max_paginas:
                break
            fields, soup = self._form_fields(html)
            siguiente = f"Page${pagina + 1}"
            pager = soup.find("a", href=lambda h: h and f"'{siguiente}'" in h)
            if pager is None:
                break
            fields["__EVENTTARGET"] = "ctl00$CPH1$GridListaPliegos"
            fields["__EVENTARGUMENT"] = siguiente
            html = self._post(BUSCAR_URL, fields).text

        if desde or hasta:
            procesos = [p for p in procesos if _fecha_en_rango(p.get("fecha_apertura", ""), desde, hasta)]
        return procesos

    def detalle_proceso(self, numero_or_url: str) -> dict:
        """Número de proceso (ej. "80-0014-LPU25") o URL de VistaPreviaPliegoCiudadano -> dict.

        Ver el docstring del módulo para el schema completo y sus gotchas
        (organismo/saf/estado solo se completan resolviendo por número).
        """
        fila = None
        if "qs=" in numero_or_url:
            url = numero_or_url
        else:
            url, fila = self._resolver_url_detalle(numero_or_url)
            if url is None:
                raise ComprarError(f"no se encontró el proceso {numero_or_url!r} en COMPR.AR")

        html = self._fetch_cached(url, "detalle")
        data = parse_detalle_html(html)
        data["ordenes_compra"] = parse_ordenes_compra_html(html)
        acta_url = data.pop("_acta_url", None)
        dictamen_url = data.pop("_dictamen_url", None)
        cuadro_target = data.pop("_cuadro_comparativo_target", None)

        data["url"] = url
        # Sin link de descarga directo confirmado al acto (ver ACTOS_ANCHOR_ID): se linkea al
        # proceso con un fragmento que salta a la sección "Actos administrativos" (pedido de
        # jdemonasterio: mostrar el link para que lo abra una persona, no bajar el PDF).
        acto_url = f"{url}#{ACTOS_ANCHOR_ID}"
        for acto in data.get("actos_administrativos") or []:
            acto["url"] = acto_url
        alcance = parse_alcance_html(html)
        data["organismo"] = (fila or {}).get("saf", "")
        # La grilla a veces trae columnas corridas ("7" como SAF): si no tiene letras, vale el Alcance.
        if not re.search(r"[A-Za-zÁÉÍÓÚáéíóú]", data["organismo"] or ""):
            data["organismo"] = alcance["saf"]
        data["saf"] = (data["organismo"] or "").split(" - ")[0].strip()
        data["estado"] = (fila or {}).get("estado", "")

        ofertas: list[dict] = []
        if cuadro_target:
            try:
                cuadro_html = self._postback_and_fetch(url, html, cuadro_target, "cuadro")
                ofertas = parse_cuadro_comparativo_html(cuadro_html)
            except httpx.HTTPError as exc:
                logger.warning("no se pudo bajar cuadro comparativo de %s: %s", url, exc)
        data["ofertas"] = ofertas

        adjudicaciones: list[dict] = []
        desestimadas: list[dict] = []
        if dictamen_url:
            try:
                dictamen_html = self._fetch_cached(dictamen_url, "dictamen")
                # "Ofertas desestimadas" no está paginada junto a gvItems: se parsea una sola
                # vez de la primera página (si se leyera de cada página paginada, se duplicaría).
                desestimadas = parse_ofertas_desestimadas_html(dictamen_html)
                grid = re.search(r"__doPostBack\('([^']*gvItems)','Page\$2'\)", _html.unescape(dictamen_html))
                paginas = (self._all_pages(dictamen_url, dictamen_html, grid.group(1), "dictamen")
                           if grid else [dictamen_html])
                vistos = set()
                for pagina in paginas:
                    for adj in parse_dictamen_html(pagina):
                        key = (adj.get("renglon"), adj.get("proveedor"), adj.get("precio_unitario"))
                        if key not in vistos:
                            vistos.add(key)
                            adjudicaciones.append(adj)
                cuit_by_proveedor = {o["proveedor"].strip().upper(): o["cuit"]
                                      for o in ofertas if o.get("cuit")}
                for adj in adjudicaciones:
                    adj["cuit"] = cuit_by_proveedor.get(adj["proveedor"].strip().upper(), "")
            except httpx.HTTPError as exc:
                logger.warning("no se pudo bajar dictamen de %s: %s", url, exc)
        data["adjudicaciones"] = adjudicaciones
        data["ofertas_desestimadas"] = desestimadas

        documentos = []
        if acta_url:
            documentos.append({"nombre": "Acta de Apertura", "url": acta_url})
        if dictamen_url:
            documentos.append({"nombre": "Dictamen de Evaluación", "url": dictamen_url})
        data["documentos"] = documentos
        return data


# --- Funciones de módulo (envoltorio fino sobre ComprarClient, mismo patrón
# que sobreprecios/sources/boletin_oficial.py: un client opcional para poder
# reusar sesión/cache entre llamadas, o para inyectar un fake en tests). ---

def buscar_procesos(texto: str | None = None, organismo: str | None = None,
                     desde: dt.date | None = None, hasta: dt.date | None = None,
                     estado: str | None = None, rubro: str | None = None,
                     max_paginas: int = 3, progress=None,
                     client: ComprarClient | None = None) -> list[dict]:
    client = client or ComprarClient()
    return client.buscar_procesos(texto=texto, organismo=organismo, rubro=rubro, estado=estado,
                                   desde=desde, hasta=hasta, max_paginas=max_paginas,
                                   progress=progress)


def detalle_proceso(numero_or_url: str, client: ComprarClient | None = None) -> dict:
    client = client or ComprarClient()
    return client.detalle_proceso(numero_or_url)


# --- Parsing puro (sin red): cada función toma HTML ya bajado y devuelve
# dicts planos. Se testean offline contra fixtures en tests/test_comprar.py. ---

def _fecha_en_rango(fecha_texto: str, desde: dt.date | None, hasta: dt.date | None) -> bool:
    match = _FECHA_RE.search(fecha_texto or "")
    if not match:
        return True  # si no se puede leer la fecha, no descartamos la fila
    d, m, y = (int(g) for g in match.groups())
    try:
        fecha = dt.date(y, m, d)
    except ValueError:
        return True
    if desde and fecha < desde:
        return False
    if hasta and fecha > hasta:
        return False
    return True


def _clean_text(value: str) -> str:
    """Normaliza espacios. El sitio a veces manda "&nbsp" SIN ";" (entidad
    inválida) para rellenar números de expediente (ej. "EX-2022-87285532-
    &nbsp&nbsp&nbsp-APN-GCCAE#CNEA"); html.parser no la decodifica como
    espacio, así que la reemplazamos a mano junto con el \\xa0 real."""
    return " ".join((value or "").replace("&nbsp", " ").replace("\xa0", " ").split())


def _parse_cantidad_unidad(texto: str) -> tuple[float | None, str]:
    """"480,00 UNIDAD" -> (480.0, "UNIDAD"); "29.000,00 UNIDAD" -> (29000.0, "UNIDAD")."""
    texto = " ".join((texto or "").split())
    match = re.match(r"([\d.,/]+)\s*(.*)", texto)
    if not match:
        return None, ""
    return parse_number(match.group(1)), match.group(2).strip()


def _label_values(soup: BeautifulSoup) -> dict[str, str]:
    """<label>Texto</label><p>Valor</p> -> {"Texto": "Valor"} (patrón de toda la vista previa)."""
    out: dict[str, str] = {}
    for label in soup.find_all("label"):
        text = label.get_text(" ", strip=True)
        if not text:
            continue
        p = label.find_next_sibling("p")
        if p is None:
            continue
        out.setdefault(text, _clean_text(p.get_text(" ", strip=True)))
    return out


def _extract_actionurl_links(soup: BeautifulSoup) -> dict[str, str]:
    """title del <a> -> URL embebida en WebForm_PostBackOptions (si la trae; ver docstring)."""
    out: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "WebForm_PostBackOptions" not in href:
            continue
        match = _POSTBACK_OPTIONS_RE.search(href)
        if not match:
            continue
        action_url = match.group(1)
        if not action_url.startswith("/"):
            continue
        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        out[title] = BASE + action_url
    return out


def _parse_renglones(soup: BeautifulSoup) -> list[dict]:
    tabla = soup.find("table", id=re.compile(r"_gvLineaPliego$"))
    if tabla is None:
        return []
    renglones = []
    body = tabla.find("tbody") or tabla
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue
        nro = tds[0].get_text(strip=True)
        if not nro.isdigit():
            continue
        codigo = tds[2].get_text(strip=True)
        desc_span = tds[3].find("span")
        descripcion = _clean_text((desc_span or tds[3]).get_text(" ", strip=True))
        cantidad, unidad = _parse_cantidad_unidad(tds[4].get_text(" ", strip=True))
        renglones.append({
            "nro": int(nro),
            "descripcion": descripcion,
            "codigo_sibys": codigo,
            "cantidad": cantidad,
            "unidad": unidad,
        })
    return renglones


def _parse_actos_administrativos(soup: BeautifulSoup) -> list[dict]:
    # Gotcha: la sección "Cláusulas Particulares" reusa el MISMO user-control
    # (y por lo tanto el mismo sufijo de id "_gvActosAdministrativos") que la
    # sección real de "Actos administrativos". Hay que exigir el prefijo
    # "UC_ActosAdministrativos_" para no quedarse con la tabla equivocada.
    tabla = soup.find("table", id=re.compile(r"UC_ActosAdministrativos_gvActosAdministrativos$"))
    if tabla is None:
        return []
    actos = []
    body = tabla.find("tbody") or tabla
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        actos.append({
            "documento": _clean_text(tds[0].get_text(" ", strip=True)),
            "numero_gde": _clean_text(tds[1].get_text(" ", strip=True)),
            "numero_especial": _clean_text(tds[2].get_text(" ", strip=True)),
            "fecha": tds[3].get_text(" ", strip=True),
        })
    return actos


# --- Plazo de pago: regex sobre el texto plano de la página (sin tabla propia; ver
# docstring del módulo / DESIGN_sobreprecios.md — en los 6 procesos 2018-2026 chequeados en
# vivo NO aparece un campo estructurado "Plazo de pago": el Decreto 1030/2016 fija un plazo
# general en el Pliego Único de Bases y Condiciones Generales, que es un documento GEDO
# común a (casi) todo proceso nacional y no se puede leer sin sesión. Esta función solo rescata
# el caso en que la cláusula SÍ aparece como texto libre (cláusulas particulares citadas en la
# vista previa, o en el texto del aviso del BO para procesos viejos). Si no hay match: (None,
# None) — "sin dato" es preferible a inventar un plazo (regla del repo). ---

_NUM_PALABRAS = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiuno": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30,
    "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90,
    "cien": 100,
}
# norm() ya sacó tildes/puntuación (": (30)" -> " 30 "), así que un "treinta (30) dias" original
# queda "treinta 30 dias": el número en dígitos (más confiable que la palabra) alcanza para
# capturarlo sin depender de los paréntesis.
_PAGO_CERCA_RE = r"(?:pago|pagar\w*|abonar\w*|factura\w*)"
_DIAS_DIGITO_RE = re.compile(r"\b(\d{1,3})\s+dias?\s*(habiles|corridos)?\b")
_DIAS_PALABRA_RE = re.compile(r"\b([a-z]+)\s+dias?\s*(habiles|corridos)?\b")
_CONTRA_ENTREGA_RE = re.compile(r"\bcontra\s+entrega\b")
_PLAZO_PAGO_ANTES = 90   # caracteres ANTES del número: alcanza para "el pago se hara dentro de los"
_PLAZO_PAGO_DESPUES = 20  # caracteres DESPUES: deliberadamente corto (ver regresión abajo)


def _cerca_de_pago(texto: str, inicio: int, fin: int) -> bool:
    """¿Hay una mención a pago/factura pegada a este número de días?

    Ventana asimétrica (90 antes, 20 después): en español administrativo la mención a pago
    casi siempre antecede al plazo ("el pago se efectuara dentro de los... días"), nunca al
    revés por mucha distancia. Un radio "después" largo y simétrico generaba un falso positivo
    real (regresión 2026-10-12, ver tests): en VistaPreviaPliegoCiudadano el campo "Plazo
    mantenimiento de la oferta: 60 Días corridos" queda, en el texto plano de la página, a ~30
    caracteres de la label SIGUIENTE "Requiere pago" (que no tiene nada que ver: es si hay que
    pagar para RETIRAR el pliego, no cuánto tarda el Estado en pagarle al proveedor). Por eso
    además se ignora "requiere pago" como mención válida.
    """
    ventana = texto[max(0, inicio - _PLAZO_PAGO_ANTES):fin + _PLAZO_PAGO_DESPUES].replace("requiere pago", "")
    return re.search(_PAGO_CERCA_RE, ventana) is not None


def parse_plazo_pago(texto: str) -> tuple[int | None, str | None]:
    """Plazo de pago del Estado al proveedor, en días CORRIDOS -> (dias, texto_legible).

    Busca "contra entrega" o "N días (corridos|hábiles)" cerca de una mención a pago/factura
    (ventana de ~90 caracteres: lo bastante para la misma cláusula, no para cruzar a otra). Si
    el plazo está en días hábiles se informa el equivalente aproximado en corridos (factor 1,4x,
    pedido de jdemonasterio 2026-10-12) y se aclara en el texto; nunca se devuelve un número sin
    decir de dónde salió. Sin match: (None, None).
    """
    t = norm(texto)
    for m in _CONTRA_ENTREGA_RE.finditer(t):
        if _cerca_de_pago(t, m.start(), m.end()):
            return 0, "contra entrega"
    for patron in (_DIAS_DIGITO_RE, _DIAS_PALABRA_RE):
        for m in patron.finditer(t):
            if not _cerca_de_pago(t, m.start(), m.end()):
                continue
            crudo = m.group(1)
            dias = int(crudo) if crudo.isdigit() else _NUM_PALABRAS.get(crudo)
            if not dias:
                continue
            tipo = m.group(2) or "corridos"
            if tipo == "habiles":
                dias_corridos = round(dias * 1.4)
                return dias_corridos, (f"{dias} dias habiles (equivalente a ~{dias_corridos} dias "
                                        "corridos, factor 1,4x)")
            return dias, f"{dias} dias corridos"
    return None, None


def parse_busqueda_html(html: str) -> list[dict]:
    """Grilla de resultados de BuscarAvanzado.aspx (búsqueda rápida o avanzada) -> filas.

    Cada fila: numero, expediente, nombre, tipo_procedimiento, fecha_apertura
    (texto tal cual "DD/MM/YYYY HH:MM Hrs."), estado, unidad_ejecutora, saf.
    """
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find(id=re.compile(r"_GridListaPliegos$"))
    if grid is None:
        return []
    procesos = []
    for tr in grid.find_all("tr"):
        if tr.find("th"):
            continue
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        numero_link = tds[0].find("a")
        numero = (numero_link or tds[0]).get_text(strip=True)
        if not numero:
            continue
        procesos.append({
            "numero": numero,
            "expediente": _clean_text(tds[1].get_text(" ", strip=True)),
            "nombre": _clean_text(tds[2].get_text(" ", strip=True)),
            "tipo_procedimiento": tds[3].get_text(" ", strip=True),
            "fecha_apertura": tds[4].get_text(" ", strip=True),
            "estado": tds[5].get_text(" ", strip=True),
            "unidad_ejecutora": tds[6].get_text(" ", strip=True),
            "saf": tds[7].get_text(" ", strip=True),
        })
    return procesos


def parse_detalle_html(html: str) -> dict:
    """VistaPreviaPliegoCiudadano.aspx -> metadata + renglones (sin red; ver módulo client para ofertas/adjudicaciones).

    Incluye 3 claves internas (_acta_url, _dictamen_url,
    _cuadro_comparativo_target) que ComprarClient.detalle_proceso usa y
    descarta antes de devolver el dict final; se exponen acá porque son
    parte real de lo que se puede extraer de esta página sola, útil para
    testear este parser de forma aislada.
    """
    soup = BeautifulSoup(html, "html.parser")
    labels = _label_values(soup)
    renglones = _parse_renglones(soup)
    actos = _parse_actos_administrativos(soup)
    fecha_adjudicacion = next((a["fecha"] for a in actos if "adjudicaci" in a["documento"].lower()), "")
    plazo_pago_dias, plazo_pago_texto = parse_plazo_pago(soup.get_text(" "))

    action_links = _extract_actionurl_links(soup)
    acta_url = next((v for k, v in action_links.items() if "apertura" in k.lower()), None)
    dictamen_url = next((v for k, v in action_links.items() if "dictamen" in k.lower()), None)

    cuadro_target = None
    cuadro_link = soup.find("a", id=re.compile(r"lnkVerCuadroComparativo$"))
    if cuadro_link is not None:
        match = _POSTBACK_TARGET_RE.search(cuadro_link.get("href", ""))
        if match:
            cuadro_target = match.group(1)

    return {
        "numero": labels.get("Número de proceso", ""),
        "expediente": labels.get("Número de expediente", ""),
        "objeto": labels.get("Objeto de la contratación", ""),
        "uoc": labels.get("Unidad Operativa de Contrataciones", ""),
        "tipo_procedimiento": labels.get("Procedimiento de selección", ""),
        "modalidad": labels.get("Modalidad", ""),
        "fechas": {
            "publicacion": labels.get("Fecha y hora estimada de publicación en el portal", ""),
            "apertura": labels.get("Fecha y hora acto de apertura", ""),
            "adjudicacion": fecha_adjudicacion,
        },
        "renglones": renglones,
        "actos_administrativos": actos,
        "plazo_pago_dias": plazo_pago_dias,
        "plazo_pago_texto": plazo_pago_texto,
        "_acta_url": acta_url,
        "_dictamen_url": dictamen_url,
        "_cuadro_comparativo_target": cuadro_target,
    }


def parse_alcance_html(html: str) -> dict:
    """Tabla "Alcance" del detalle (gvAlcance): SAF (ministerio/organismo) y UOCs del proceso.

    Es la fuente confiable del organismo cuando se entra por URL (sin pasar por la
    grilla de búsqueda, que además a veces viene con columnas corridas: "7" como SAF).
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=re.compile(r"gvAlcance$"))
    if table is None:
        return {"saf": "", "uocs": []}
    safs, uocs = [], []
    for tr in table.find_all("tr")[1:]:
        cells = [_clean_text(td.get_text(" ", strip=True)) for td in tr.find_all("td")]
        if len(cells) >= 2:
            if re.match(r"^\d{2,3}\s*-\s*\S", cells[0]):
                safs.append(cells[0])
            if cells[1]:
                uocs.append(cells[1])
    return {"saf": safs[0] if safs else "", "uocs": list(dict.fromkeys(uocs))}


def parse_ordenes_compra_html(html: str) -> list[dict]:
    """Tabla "Documento contractual por proveedor" de la página de detalle (gvOCA).

    Son las órdenes de compra realmente emitidas: proveedor, CUIT, fecha de
    perfeccionamiento, monto y tipo (Original / Prórroga / Ampliación). Es la
    contratación FINAL: el dictamen es solo una recomendación y puede no
    coincidir con las OC (por eso se cruzan; el dictamen está paginado y hay
    que leer todas las páginas antes de concluir que un proveedor no figura).
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id=re.compile(r"gvOCA$"))
    if table is None:
        return []
    rows = table.find_all("tr")
    if not rows:
        return []
    header = [_clean_text(c.get_text(" ", strip=True)).lower() for c in rows[0].find_all(["th", "td"])]

    def col(cells, *names):
        for name in names:
            for i, h in enumerate(header):
                if name in h and i < len(cells):
                    return _clean_text(cells[i].get_text(" ", strip=True))
        return ""

    out = []
    for tr in rows[1:]:
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue
        numero = col(cells, "número", "numero")
        if not numero:
            continue
        fecha = col(cells, "fecha")
        m = _FECHA_RE.search(fecha)
        out.append({
            "numero_oc": numero,
            "estado": col(cells, "estado"),
            "proveedor": col(cells, "proveedor"),
            "cuit": col(cells, "cuit"),
            "fecha": f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else fecha,
            "monto": parse_ars(col(cells, "monto")),
            "moneda": col(cells, "moneda"),
            "tipo": col(cells, "tipo"),
        })
    return out


def parse_cuadro_comparativo_html(html: str) -> list[dict]:
    """VerCuadroComparativo.aspx -> una fila por (renglón, oferente) con precio unitario.

    Cada renglón es un acordeón (rptLineas$ctlNN) con un <span id="...
    _lblRenglon"> para el número y un div "..._collapse" con un bloque por
    oferente (rptLineasOfertas$ctlNN, <span id="..._lblNombre"> = "NOMBRE  -
    CUIT"). La marca, cuando el oferente la escribió en su especificación
    técnica, sale del atributo title de "...lblVerMasDescripcion" (el texto
    completo que "Ver más" despliega) o, si no está, del texto corto de la
    especificación.
    """
    soup = BeautifulSoup(html, "html.parser")
    ofertas = []
    for renglon_span in soup.find_all("span", id=re.compile(r"_lblRenglon$")):
        renglon_txt = renglon_span.get_text(strip=True)
        if not renglon_txt.isdigit():
            continue
        prefix = renglon_span["id"][: -len("_lblRenglon")]
        collapse = soup.find(id=f"{prefix}_collapse")
        if collapse is None:
            continue
        for nombre_span in collapse.find_all("span", id=re.compile(r"_lblNombre$")):
            nombre_cuit = _clean_text(nombre_span.get_text(" ", strip=True))
            proveedor, _, cuit = nombre_cuit.rpartition(" - ")
            proveedor = proveedor.strip() or nombre_cuit
            cuit = cuit.strip()

            oferta_prefix = nombre_span["id"][: -len("_lblNombre")]
            precio_span = collapse.find("span", id=f"{oferta_prefix}_txtPrecioUnitario")
            total_span = collapse.find("span", id=f"{oferta_prefix}_txtTotalPorRenglon")
            moneda_span = collapse.find("span", id=f"{oferta_prefix}_lblMoneda")
            desc_span = collapse.find("span", id=f"{oferta_prefix}_lblDescripcion")
            vermas_span = collapse.find("span", id=f"{oferta_prefix}_lblVerMasDescripcion")

            marca = ""
            for texto in (vermas_span.get("title") if vermas_span else None,
                          desc_span.get_text(" ", strip=True) if desc_span else ""):
                if not texto:
                    continue
                match = re.search(r"MARCA:\s*([^.\"'\n]+)", texto, re.IGNORECASE)
                if match:
                    marca = match.group(1).strip().rstrip("'\"")
                    break

            ofertas.append({
                "renglon": int(renglon_txt),
                "proveedor": proveedor,
                "cuit": cuit,
                "precio_unitario": parse_ars(precio_span.get_text(strip=True)) if precio_span else None,
                "total": parse_ars(total_span.get_text(strip=True)) if total_span else None,
                "moneda": moneda_span.get_text(strip=True) if moneda_span else "",
                "marca": marca,
            })
    return ofertas


def parse_dictamen_html(html: str) -> list[dict]:
    """PreAdjudicarVisualizarDictamen.aspx -> "Ofertas recomendadas" (el ganador por renglón).

    No trae CUIT (ComprarClient la completa cruzando por nombre contra el
    cuadro comparativo). Columnas de la tabla real: Número renglón,
    Alternativa, Oferentes, Cantidad, Precio unitario, Moneda, Preferencia
    Compre Argentino (checkbox), Porcentaje otorgado, Precio total renglón.
    """
    soup = BeautifulSoup(html, "html.parser")
    tabla = soup.find("table", id=re.compile(r"_gvItems$"))
    if tabla is None:
        return []
    adjudicaciones = []
    body = tabla.find("tbody") or tabla
    for tr in body.find_all("tr"):
        if tr.find("th"):
            continue
        tds = tr.find_all("td")
        if len(tds) < 9:
            continue
        renglon_txt = tds[0].get_text(strip=True)
        if not renglon_txt.isdigit():
            continue
        oferente = tds[2].get_text(" ", strip=True)
        cantidad_num, _ = _parse_cantidad_unidad(tds[3].get_text(" ", strip=True))
        adjudicaciones.append({
            "renglon": int(renglon_txt),
            "proveedor": oferente,
            "cuit": "",
            "cantidad": cantidad_num,
            "precio_unitario": parse_ars(tds[4].get_text(strip=True)),
            "total": parse_ars(tds[8].get_text(strip=True)),
        })
    return adjudicaciones


_CUIT_FIN_RE = re.compile(r"(\d{10,11})\s*$")


def parse_ofertas_desestimadas_html(html: str) -> list[dict]:
    """PreAdjudicarVisualizarDictamen.aspx -> sección "Ofertas desestimadas" (pedido de
    jdemonasterio 2026-10-12: parsear la causa de cada oferta rechazada, no solo el ganador).

    Por cada oferente desestimado hay dos tablas repetidas (`rptOfertasNoCumplieron`, un
    user-control con el mismo id `UCProveedorRenglonDescartadoBastrap` por oferente):
      - `gvEvaluacionDescartada`: evaluación a nivel de TODA la oferta (un requisito
        administrativo/técnico que la descarta completa, sin renglón puntual).
      - `gvRenglonesDescartados`: un renglón por fila, con su causa (columnas "Evaluación
        Técnica"/"Evaluación Económica"). Si esas dos columnas vienen vacías para un renglón
        (pasa cuando la causa es administrativa y aplica a toda la oferta), se usa como causa
        la evaluación de `gvEvaluacionDescartada` del mismo oferente.
    No está paginada junto al dictamen (`gvItems`): el mismo bloque completo aparece en TODAS
    las páginas de un dictamen paginado, así que ComprarClient la parsea una sola vez (de la
    primera página), no dentro de `_all_pages`.
    """
    soup = BeautifulSoup(html, "html.parser")
    admin_text: dict[str, str] = {}
    for tabla in soup.find_all("table", id=re.compile(r"_gvEvaluacionDescartada$")):
        prefix = tabla["id"][: -len("_gvEvaluacionDescartada")]
        body = tabla.find("tbody") or tabla
        textos = []
        for tr in body.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4 or tds[0].find_parent("thead"):
                continue
            for td in tds[1:]:
                val = _clean_text(td.get_text(" ", strip=True))
                if val:
                    textos.append(val)
        if textos:
            admin_text[prefix] = " | ".join(dict.fromkeys(textos))

    out = []
    for tabla in soup.find_all("table", id=re.compile(r"_gvRenglonesDescartados$")):
        prefix = tabla["id"][: -len("_gvRenglonesDescartados")]
        proveedor_span = soup.find(id=f"{prefix}_lblProveedor")
        nombre_cuit = _clean_text(proveedor_span.get_text(" ", strip=True)) if proveedor_span else ""
        cuit_match = _CUIT_FIN_RE.search(nombre_cuit)
        cuit = cuit_match.group(1) if cuit_match else ""
        proveedor = nombre_cuit[:cuit_match.start()].strip() if cuit_match else nombre_cuit
        body = tabla.find("tbody") or tabla
        for tr in body.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
            renglon_txt = tds[0].get_text(strip=True)
            if not renglon_txt.isdigit():
                continue
            causa = " | ".join(dict.fromkeys(
                _clean_text(td.get_text(" ", strip=True)) for td in (tds[4], tds[5])
                if _clean_text(td.get_text(" ", strip=True))))
            if not causa:
                causa = admin_text.get(prefix, "")
            out.append({
                "renglon": int(renglon_txt),
                "alternativa": tds[1].get_text(strip=True),
                "proveedor": proveedor,
                "cuit": cuit,
                "causa": causa or "sin motivo detallado en el dictamen",
            })
    return out
