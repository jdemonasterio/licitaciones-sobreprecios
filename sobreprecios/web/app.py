"""Webui local (FastAPI + Jinja2): licitaciones, análisis, reportes y fuentes.

Solapas:
  /              Licitaciones: lista filtrable, ordenada por exceso estimado
  /licitacion/N  Detalle: contexto del proceso, renglones, referencias, evidencia, marca de "publicable"
  /revision      Cola de revisión humana (casos grises/rojos/juez); /revision/N el caso con su
                 formulario de corrección manual (sobreprecios/revision_humana.py)
  /analitica     Totales por organismo, producto, proveedor, procedimiento y mes
  /producto/ID   Precio pagado por el Estado vs mercado en el tiempo
  /empresas      Adjudicatarios; /empresa/CUIT con historia (autoridades, socios, domicilios)
  /reportes      Armar un reporte para periodistas (HTML imprimible + Excel + CSV)
  /fuentes       Estado de los datos y botones para actualizar (jobs en background)
  /investigar    Investigar UNA licitación a pedido: link del BO / COMPR.AR, número, texto o PDF
  /doc           Proxy de solo lectura para mirar un PDF/HTML del BO o COMPR.AR en un <iframe>

Regla del webui: una acción nunca recarga la página
ni borra lo que el usuario está haciendo. Los POST pasan por soft-nav.js, el
trabajo largo corre en un job (web/_background.py) y la página consulta
/jobs/<nombre> hasta que termina; el resultado llega como cartel (toast).

Sin login: corre en 127.0.0.1. Igual se rechazan POST de otros orígenes (una
página maliciosa abierta en otra pestaña no puede disparar acciones).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import urllib.parse
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import backup, config, db, reports, revision_humana
from . import _background, format as fmt, queries

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
app = FastAPI(title="Sobreprecios en compras del Estado")
# Solo se atiende a nombres locales: frena el "DNS rebinding" (una web externa cuyo dominio apunta a 127.0.0.1
# y que con eso podría saltar el chequeo de origen). Para usarlo en la red local: LIC_HOSTS_PERMITIDOS=ip,nombre
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"] + [
    h.strip() for h in os.getenv("LIC_HOSTS_PERMITIDOS", "").split(",") if h.strip()])
MAX_UPLOAD_BYTES = 30 * 1024 * 1024   # un PDF de licitación grande ronda los 10 MB
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
logger = logging.getLogger(__name__)


# ── Formato ────────────────────────────────────────────────────────────────
# Los números y los textos de semáforo/motivo/referencia viven en web/format.py (no acá):
# reports.py los necesita igual para el "comprobante de cálculo" y el expediente para
# periodista, y así los dos lugares muestran EXACTAMENTE lo mismo para el mismo dato.

templates.env.filters.update(ars=fmt.ars, num=fmt.num, pct=fmt.pct, millones=fmt.millones)
templates.env.globals.update(SEMAFORO_TXT=fmt.SEMAFORO_TXT, REF_TXT=fmt.REF_TXT, ATRIBUTO_TXT=fmt.ATRIBUTO_TXT,
                             MOTIVO_CODIGO_TXT=fmt.MOTIVO_CODIGO_TXT, LEGENDA_INVESTIGACION=fmt.LEGENDA_INVESTIGACION,
                             PARTE_TXT=revision_humana.PARTE_TXT, PARTES_RENGLON=revision_humana.PARTES_RENGLON,
                             explicar_semaforo=fmt.explicar_semaforo,
                             EXPLICACIONES_ITEMS=revision_humana.EXPLICACIONES_ITEMS,
                             EXPLICACION_TXT=revision_humana.EXPLICACION_TXT,
                             EXPLICACION_ESTADOS=revision_humana.EXPLICACION_ESTADOS,
                             EXPLICACION_ESTADO_TXT=revision_humana.EXPLICACION_ESTADO_TXT)


# ── Seguridad mínima ───────────────────────────────────────────────────────

@app.middleware("http")
async def block_cross_origin_writes(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
        if origin == "null" or urllib.parse.urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower():
            return JSONResponse({"error": "pedido de otro origen bloqueado"}, status_code=403)
    return await call_next(request)


def _redirect(path: str, message: str | None = None) -> RedirectResponse:
    if message:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}message={urllib.parse.quote(message)}"
    return RedirectResponse(path, status_code=303)


def _modo_publico() -> bool:
    """Despliegue de solo lectura (deploy en la nube, LIC_MODO_PUBLICO=1): además de lo que
    bloquee la seguridad general del deploy público, una descarga de copia de seguridad expone
    TODA la base (notas de revisión humana incluidas) y nunca debe quedar disponible ahí."""
    return os.getenv("LIC_MODO_PUBLICO") == "1"


def _filtros(request: Request) -> dict:
    keys = ["q", "producto", "organismo", "semaforo", "desde", "hasta", "revision", "proveedor"]
    return {k: request.query_params.get(k, "").strip() for k in keys}


def _moneda(request: Request) -> str:
    """Moneda en que se muestran los montos: nominal | real | usd (normalize/moneda.py)."""
    from ..normalize.moneda import MODOS
    valor = request.query_params.get("moneda", "nominal")
    return valor if valor in MODOS else "nominal"


def _moneda_ctx(conn, moneda: str) -> dict:
    from ..normalize import moneda as moneda_mod
    m = moneda_mod.cargar(conn)
    return {"moneda": moneda, "moneda_txt": m.etiqueta(moneda),
            "monedas": [(k, m.etiqueta(k)) for k in moneda_mod.MODOS]}


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("message", request.query_params.get("message"))
    ctx.setdefault("moneda", "nominal")
    ctx.setdefault("modo_publico", _modo_publico())
    ctx["request"] = request
    ctx["jobs"] = _background.all_status()
    return templates.TemplateResponse(request, name, ctx)


# ── Licitaciones ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def licitaciones(request: Request):
    filtros = _filtros(request)
    orden = request.query_params.get("orden", "exceso")
    moneda = _moneda(request)
    with db.session() as conn:
        return _render(request, "licitaciones.html", filtros=filtros, orden=orden,
                       kpis=queries.kpis(conn, filtros, moneda),
                       procesos=queries.list_procesos(conn, filtros, orden, moneda=moneda),
                       opciones=queries.opciones_filtros(conn), **_moneda_ctx(conn, moneda))


@app.get("/licitacion/{proceso_id}", response_class=HTMLResponse)
def licitacion(request: Request, proceso_id: int):
    with db.session() as conn:
        proc = queries.proceso(conn, proceso_id)
        if not proc:
            return _redirect("/", "No existe esa licitación")
        rows = queries.renglones(conn, proceso_id)
        validaciones = revision_humana.validaciones_renglones(conn, [r["id"] for r in rows])
        resumen = queries.resumen_proceso(proc, rows, validaciones)
        expedientes = [dict(r) for r in conn.execute(
            "SELECT * FROM reportes WHERE tipo='expediente' AND proceso_id=? ORDER BY id DESC", (proceso_id,))]
        from ..normalize import moneda as moneda_mod
        mon = moneda_mod.cargar(conn)
        fecha = proc.get("fecha_adjudicacion") or proc.get("fecha_publicacion")
        equiv = {"usd": mon.a_usd(proc.get("monto_total"), fecha), "tasa": mon.tasa_usd(fecha),
                 "real": mon.a_real(proc.get("monto_total"), fecha), "real_txt": mon.etiqueta("real"),
                 "usd_txt": mon.etiqueta("usd")}
    from ..pipeline.extract import resolutive_part
    texto = resolutive_part(proc.get("texto") or "", 12000) if proc.get("texto") else ""
    return _render(request, "licitacion.html", p=proc, renglones=rows, texto=texto, equiv=equiv,
                   validaciones=validaciones, resumen=resumen, expedientes=expedientes,
                   cfg=config.settings().get("comparacion") or {})


@app.post("/licitacion/{proceso_id}/revision")
def licitacion_revision(proceso_id: int, estado: str = Form(...), nota: str = Form("")):
    if estado not in {"pendiente", "revisado", "descartado", "publicable"}:
        return _redirect(f"/licitacion/{proceso_id}", "Estado inválido")
    from .. import revision
    with db.session() as conn:
        revision.marcar(conn, proceso_id, estado, nota.strip() or None)
    extra = " (si cambia el cálculo, la marca se revoca sola)" if estado == "publicable" else ""
    return _redirect(f"/licitacion/{proceso_id}", f"Marcada como {estado}{extra}")


@app.post("/licitacion/{proceso_id}/validar")
def licitacion_validar(proceso_id: int, nota: str = Form("")):
    """"Validé esta licitación": atajo de un clic sobre el mismo comentario de arriba (reusa
    revision.marcar, el mismo gate que "Guardar"; marca estado='revisado')."""
    from .. import revision
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM procesos WHERE id=?", (proceso_id,)).fetchone():
            return _redirect("/", "No existe esa licitación")
        revision.marcar(conn, proceso_id, "revisado", nota.strip() or None)
    return _redirect(f"/licitacion/{proceso_id}", "Licitación validada")


@app.get("/renglon/{renglon_id}/comprobante", response_class=HTMLResponse)
def renglon_comprobante(request: Request, renglon_id: int):
    """"Comprobante de cálculo" por renglón (MEJORAS #49.2): una vista imprimible y autocontenida
    (sin formularios: @media print de styles.css ya oculta nav/botones) con el dato original, la
    normalización, la fecha de comparación, las referencias (con el par que confirma el mínimo),
    los factores IPC, los dos escenarios financieros y las correcciones/validaciones humanas."""
    with db.session() as conn:
        data = revision_humana.comprobante(conn, renglon_id)
        if not data:
            return _redirect("/", "No existe ese renglón")
    return _render(request, "comprobante.html", c=data)


@app.post("/licitacion/{proceso_id}/expediente")
def licitacion_expediente(proceso_id: int, titulo: str = Form(""), renglon_ids: list[int] = Form([])):
    """"Armar expediente para periodista" (MEJORAS #49.5): job en segundo plano que junta la
    síntesis del proceso y el comprobante de cálculo de los renglones elegidos en un HTML + ZIP
    autocontenidos (aparecen en /reportes, tipo "expediente")."""
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM procesos WHERE id=?", (proceso_id,)).fetchone():
            return _redirect("/", "No existe esa licitación")
    started, msg = _background.start(
        f"expediente_{proceso_id}", lambda: reports.crear_expediente(proceso_id, titulo, list(renglon_ids)))
    return _redirect(f"/licitacion/{proceso_id}", "Armando el expediente…" if started else msg)


@app.post("/revision/{renglon_id}/explicacion")
def revision_explicacion(renglon_id: int, item: str = Form(...), estado: str = Form(...),
                         nota: str = Form(""), revisor: str = Form("")):
    """Checklist de explicaciones alternativas de UN caso (MEJORAS #49.6): nunca recalcula nada,
    solo deja constancia de qué se descartó o qué quedó pendiente de chequear."""
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM renglones WHERE id=?", (renglon_id,)).fetchone():
            return _redirect("/revision", "No existe ese renglón")
        try:
            revision_humana.guardar_explicacion(conn, renglon_id, item, estado, nota.strip(),
                                                revisor.strip() or "revisor")
        except ValueError as exc:
            return _redirect(f"/revision/{renglon_id}", str(exc))
    return _redirect(f"/revision/{renglon_id}", "Explicación guardada")


@app.post("/renglon/{renglon_id}/validar")
def renglon_validar(renglon_id: int, parte: str = Form(...), juicio: str = Form(...),
                    nota: str = Form(""), revisor: str = Form("")):
    """Validación rápida in-line de UNA parte de UN renglón, sin abrir /revision/<renglon_id>."""
    with db.session() as conn:
        row = conn.execute("SELECT proceso_id FROM renglones WHERE id=?", (renglon_id,)).fetchone()
        if not row:
            return _redirect("/", "No existe ese renglón")
        try:
            revision_humana.guardar_validacion_renglon(conn, renglon_id, parte, juicio, nota.strip(),
                                                       revisor.strip() or "revisor")
        except ValueError as exc:
            return _redirect(f"/licitacion/{row['proceso_id']}", str(exc))
        proceso_id = row["proceso_id"]
    return _redirect(f"/licitacion/{proceso_id}", "Validación guardada")


# ── Analítica ──────────────────────────────────────────────────────────────

@app.get("/analitica", response_class=HTMLResponse)
def analitica(request: Request):
    filtros = _filtros(request)
    moneda = _moneda(request)
    with db.session() as conn:
        data = {dim: queries.agrupado(conn, filtros, dim, moneda=moneda) for dim in
                ["organismo", "producto", "proveedor", "procedimiento", "mes"]}
        return _render(request, "analitica.html", filtros=filtros, data=data,
                       kpis=queries.kpis(conn, filtros, moneda),
                       peores=queries.peores_renglones(conn, filtros, moneda=moneda),
                       opciones=queries.opciones_filtros(conn), **_moneda_ctx(conn, moneda))


@app.get("/producto/{producto_id}", response_class=HTMLResponse)
def producto(request: Request, producto_id: str):
    with db.session() as conn:
        prod = conn.execute("SELECT * FROM productos WHERE id=?", (producto_id,)).fetchone()
        if not prod:
            return _redirect("/analitica", "Producto desconocido")
        moneda = _moneda(request)
        return _render(request, "producto.html", prod=dict(prod),
                       serie=queries.serie_producto(conn, producto_id, moneda), **_moneda_ctx(conn, moneda))


# ── Empresas ───────────────────────────────────────────────────────────────

@app.get("/empresas", response_class=HTMLResponse)
def empresas(request: Request):
    q = request.query_params.get("q", "").strip()
    with db.session() as conn:
        db.sync_empresas(conn)
        return _render(request, "empresas.html", q=q, empresas=queries.empresas(conn, q))


@app.get("/empresa/{cuit}", response_class=HTMLResponse)
def empresa(request: Request, cuit: str):
    with db.session() as conn:
        data = queries.empresa(conn, cuit)
    if not data:
        return _redirect("/empresas", "No hay datos de ese CUIT")
    return _render(request, "empresa.html", e=data, job_name=f"contexto_{cuit}")


@app.post("/empresa/{cuit}/contexto")
def empresa_contexto(cuit: str):
    from .. import empresas as empresas_mod
    started, msg = _background.start(f"contexto_{cuit}", lambda: empresas_mod.actualizar_contexto(cuit))
    return _redirect(f"/empresa/{cuit}", "Buscando contexto en el Boletín Oficial…" if started else msg)


# ── Reportes ───────────────────────────────────────────────────────────────

@app.get("/reportes", response_class=HTMLResponse)
def reportes_page(request: Request):
    with db.session() as conn:
        lista = [dict(r) for r in conn.execute("SELECT * FROM reportes ORDER BY id DESC")]
        return _render(request, "reportes.html", reportes=lista, opciones=queries.opciones_filtros(conn),
                       filtros=_filtros(request))


@app.post("/reportes")
def reportes_crear(titulo: str = Form(...), desde: str = Form(""), hasta: str = Form(""),
                   organismo: str = Form(""), producto: str = Form(""), semaforo: str = Form(""),
                   revision: str = Form(""), con_resumen: str = Form("")):
    filtros = {"desde": desde, "hasta": hasta, "organismo": organismo, "producto": producto,
               "semaforo": semaforo, "revision": revision}
    started, msg = _background.start(
        "reporte", lambda: reports.crear(titulo.strip() or "Reporte", filtros, bool(con_resumen)))
    return _redirect("/reportes", "Armando el reporte…" if started else msg)


@app.get("/reportes/{reporte_id}/{formato}")
def reportes_descargar(reporte_id: int, formato: str):
    with db.session() as conn:
        row = conn.execute("SELECT * FROM reportes WHERE id=?", (reporte_id,)).fetchone()
    if not row:
        return _redirect("/reportes", "No existe ese reporte")
    path = reports.archivo(row, formato)
    if not path or not path.exists():
        return _redirect("/reportes", "Archivo no disponible")
    if formato == "html":
        return HTMLResponse(path.read_text(encoding="utf-8"))
    return FileResponse(path, filename=path.name)


# ── Investigar una licitación a pedido ─────────────────────────────────────

@app.get("/investigar", response_class=HTMLResponse)
def investigar_page(request: Request):
    from .. import investigar as inv
    with db.session() as conn:
        return _render(request, "investigar.html", investigaciones=inv.lista(conn))


@app.post("/investigar")
async def investigar_run(entrada: str = Form(""), archivo: UploadFile | None = File(None)):
    from .. import investigar as inv
    path = None
    if archivo is not None and archivo.filename:
        contenido = await archivo.read(MAX_UPLOAD_BYTES + 1)
        if len(contenido) > MAX_UPLOAD_BYTES:
            return _redirect("/investigar", "El PDF pesa más de 30 MB: subí solo el pliego o el acto que interesa")
        if not contenido[:5].startswith(b"%PDF"):
            return _redirect("/investigar", "El archivo tiene que ser un PDF")
        path = str(inv.guardar_upload(archivo.filename, contenido))
    if not entrada.strip() and not path:
        return _redirect("/investigar", "Pegá un link, un número de proceso o el texto, o subí un PDF")

    def run():
        return inv.investigar(entrada, path, progress=lambda t: _background.set_progress("investigar", t))
    started, msg = _background.start("investigar", run)
    return _redirect("/investigar", "Investigando… (podés seguir usando el resto de la página)" if started else msg)


# ── Revisión humana (MEJORAS #34) ───────────────────────────────────────────
# Rutas estáticas (/revision/juez...) ANTES de la dinámica (/revision/{renglon_id}): Starlette
# matchea en el orden de declaración, así "juez" nunca intenta convertirse a renglon_id:int.

@app.get("/revision/juez", response_class=HTMLResponse)
def revision_juez(request: Request):
    with db.session() as conn:
        corridas = revision_humana.corridas_juez(conn)
        for c in corridas:
            c["items"] = revision_humana.items_corrida_juez(conn, c["id"])
        return _render(request, "revision_juez.html", corridas=corridas)


@app.post("/revision/juez/importar")
def revision_juez_importar(carpeta: str = Form(...)):
    with db.session() as conn:
        try:
            msg = revision_humana.importar_corrida_juez(conn, carpeta.strip())
        except FileNotFoundError as exc:
            return _redirect("/revision/juez", str(exc))
    return _redirect("/revision/juez", msg)


@app.get("/revision", response_class=HTMLResponse)
def revision_page(request: Request):
    filtros = {k: request.query_params.get(k, "").strip() for k in ("origen", "estado", "producto")}
    with db.session() as conn:
        datos = revision_humana.cola(conn, filtros)
        return _render(request, "revision.html", filtros=filtros, items=datos["items"], counts=datos["counts"],
                       colores=datos["colores"],
                       opciones=queries.opciones_filtros(conn))


@app.get("/revision/{renglon_id}", response_class=HTMLResponse)
def revision_caso(request: Request, renglon_id: int):
    with db.session() as conn:
        data = revision_humana.caso(conn, renglon_id)
        if not data:
            return _redirect("/revision", "No existe ese renglón")
        return _render(request, "revision_caso.html", c=data, valores=revision_humana.formulario(data))


def _recalcular_proceso(proceso_id: int) -> str:
    """Corre en el worker thread del job 'revision_<renglon_id>': abre SU PROPIA conexión
    (ver docstring de web/_background.py), nunca la del request que disparó el guardado."""
    from ..pipeline import compare as compare_mod
    with db.session() as conn:
        return compare_mod.run(conn, [proceso_id])


@app.post("/revision/{renglon_id}/guardar")
def revision_guardar(renglon_id: int, producto_id: str = Form(""), variante: str = Form(""),
                     contenido_base: str = Form(""), unidad_base: str = Form(""),
                     precio_unitario: str = Form(""), cantidad: str = Form(""),
                     fecha_comparacion: str = Form(""), marca: str = Form(""),
                     veredicto: str = Form(""), nota: str = Form(""), revisor: str = Form("")):
    with db.session() as conn:
        data = revision_humana.caso(conn, renglon_id)
        if not data:
            return _redirect("/revision", "No existe ese renglón")
        actuales = revision_humana.formulario(data)
        entrada = {"producto_id": producto_id, "variante": variante, "contenido_base": contenido_base,
                   "unidad_base": unidad_base, "precio_unitario": precio_unitario, "cantidad": cantidad,
                   "fecha_comparacion": fecha_comparacion, "marca": marca}
        cambiados = revision_humana.procesar_formulario(conn, renglon_id, actuales, entrada,
                                                         nota.strip(), revisor.strip() or "revisor")
        if veredicto.strip() or nota.strip() or revisor.strip():
            revision_humana.guardar_revision(conn, renglon_id, veredicto.strip() or None, nota.strip(),
                                             revisor.strip(), estado="resuelto" if veredicto.strip() else "pendiente")
        proceso_id = data["proceso_id"]
    etiquetas = {"producto_id": "producto", "variante": "variedad", "contenido_base": "envase",
                "unidad_base": "envase", "precio_unitario": "precio", "cantidad": "cantidad",
                "fecha_comparacion": "fecha", "marca": "marca"}
    if not cambiados:
        return _redirect(f"/revision/{renglon_id}", "Revisión guardada")
    resumen = ", ".join(dict.fromkeys(etiquetas[c] for c in cambiados))
    started, jmsg = _background.start(f"revision_{renglon_id}", lambda: _recalcular_proceso(proceso_id))
    return _redirect(f"/revision/{renglon_id}", f"Guardado ({resumen}); recalculando…" if started else jmsg)


@app.post("/revision/{renglon_id}/deshacer")
def revision_deshacer(renglon_id: int, correccion_id: int = Form(...)):
    with db.session() as conn:
        row = conn.execute("SELECT proceso_id FROM renglones WHERE id=?", (renglon_id,)).fetchone()
        if not row:
            return _redirect("/revision", "No existe ese renglón")
        ok = revision_humana.deshacer_correccion(conn, correccion_id)
        proceso_id = row["proceso_id"]
    if not ok:
        return _redirect(f"/revision/{renglon_id}", "Esa corrección ya no existe")
    _background.start(f"revision_{renglon_id}", lambda: _recalcular_proceso(proceso_id))
    return _redirect(f"/revision/{renglon_id}", "Corrección deshecha; recalculando…")


@app.post("/revision/{renglon_id}/validar-mercado")
def revision_validar_mercado(renglon_id: int, obs_fuente: str = Form(...), obs_fecha: str = Form(...),
                             obs_descripcion: str = Form(...), juicio: str = Form(""), nota: str = Form(""),
                             revisor: str = Form("")):
    """Juicio humano sobre UNA observación de mercado, para ESTE renglón (P16): juicio vacío
    ("sin marcar") borra la fila y vuelve a incluir la observación."""
    with db.session() as conn:
        row = conn.execute("SELECT proceso_id FROM renglones WHERE id=?", (renglon_id,)).fetchone()
        if not row:
            return _redirect("/revision", "No existe ese renglón")
        if juicio.strip():
            try:
                revision_humana.guardar_validacion_mercado(conn, renglon_id, obs_fuente, obs_fecha,
                                                           obs_descripcion, juicio.strip(), nota.strip(),
                                                           revisor.strip() or "revisor")
            except ValueError as exc:
                return _redirect(f"/revision/{renglon_id}", str(exc))
        else:
            revision_humana.quitar_validacion_mercado(conn, renglon_id, obs_fuente, obs_fecha, obs_descripcion)
        proceso_id = row["proceso_id"]
    started, jmsg = _background.start(f"revision_{renglon_id}", lambda: _recalcular_proceso(proceso_id))
    return _redirect(f"/revision/{renglon_id}", "Guardado; recalculando…" if started else jmsg)


_DOC_HEADERS = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
# El HTML de terceros (BO, COMPR.AR) se muestra en un origen opaco, sin scripts ni formularios: si no, un script
# de esa página correría con el origen del webui y podría disparar acciones (revisión de seguridad 2026-10-12).
_CSP_SANDBOX = ("sandbox; default-src 'none'; img-src https: data:; style-src https: 'unsafe-inline'; "
                "font-src https: data:")


@app.get("/doc")
def doc_proxy(url: str):
    import html as _html
    try:
        contenido, ctype = revision_humana.obtener_documento(url)
    except ValueError as exc:
        return HTMLResponse(f"<p class='help'>No se pudo mostrar el documento: {_html.escape(str(exc))}</p>",
                            status_code=400, headers={**_DOC_HEADERS, "Content-Security-Policy": "sandbox"})
    except Exception as exc:   # noqa: BLE001 — una fuente externa caída no debe tumbar la página
        return HTMLResponse(f"<p class='help'>No se pudo bajar el documento ({type(exc).__name__}). "
                            "Probá abrirlo directo con el link de arriba.</p>", status_code=502,
                            headers={**_DOC_HEADERS, "Content-Security-Policy": "sandbox"})
    base = (ctype or "").split(";")[0].strip().lower()
    if base == "application/pdf" or contenido[:5].startswith(b"%PDF"):
        return Response(content=contenido, media_type="application/pdf",
                        headers={**_DOC_HEADERS, "Content-Disposition": "inline"})
    if base in {"text/html", "application/xhtml+xml"}:
        return Response(content=contenido, media_type=ctype,
                        headers={**_DOC_HEADERS, "Content-Security-Policy": _CSP_SANDBOX})
    # Cualquier otro tipo se muestra como texto, nunca como algo que el navegador ejecute.
    return Response(content=contenido, media_type="text/plain; charset=utf-8",
                    headers={**_DOC_HEADERS, "Content-Security-Policy": "sandbox"})


# ── Fuentes y jobs ─────────────────────────────────────────────────────────

ACCIONES = {
    "pipeline": "Buscar en el BO y COMPR.AR + extraer + machear + comparar",
    "comprar": "Buscar en COMPR.AR (alimentos y limpieza adjudicados)",
    "bo_buscar": "Buscar adjudicaciones en el Boletín Oficial",
    "extraer": "Extraer renglones de los avisos bajados",
    "machear": "Asignar productos y precios por kg/l",
    "indec": "Actualizar IPC, precios INDEC y cotizaciones del dólar",
    "precios": "Bajar precios de hoy (supermercados y mayoristas)",
    "comparar": "Recalcular sobreprecios",
    "empresas": "Completar empresas (Registro Nacional de Sociedades, ~230 MB la primera vez)",
    "precios_programa": "Bajar listas de Precios Cuidados/Justos 2020-2023",
}


@app.get("/fuentes", response_class=HTMLResponse)
def fuentes(request: Request):
    hoy = dt.date.today()
    with db.session() as conn:
        return _render(request, "fuentes.html", resumen=queries.fuentes_resumen(conn),
                       corridas=queries.corridas(conn), acciones=ACCIONES,
                       desde_default=(hoy - dt.timedelta(days=60)).isoformat(), hasta_default=hoy.isoformat(),
                       perfil_llm=__import__("sobreprecios.llm", fromlist=["perfil"]).perfil())


def _job_runner(accion: str, desde: str, hasta: str):
    from ..pipeline import compare, extract, match
    from ..sources import boletin_oficial, indec

    def progress(text):
        _background.set_progress("datos", text)

    def run():
        d1 = dt.date.fromisoformat(desde) if desde else dt.date.today() - dt.timedelta(days=60)
        d2 = dt.date.fromisoformat(hasta) if hasta else dt.date.today()
        mensajes = []
        with db.session() as conn:
            run_id = db.start_run(conn, accion, {"desde": str(d1), "hasta": str(d2)})
            try:
                if accion in {"pipeline", "bo_buscar"}:
                    mensajes.append(boletin_oficial.discover(conn, d1, d2, progress=progress))
                if accion in {"pipeline", "comprar"}:
                    from ..pipeline import comprar_import
                    mensajes.append(comprar_import.descubrir(conn, d1, d2, progress=progress))
                if accion in {"pipeline", "extraer"}:
                    mensajes.append(extract.run(conn, progress=progress))
                if accion in {"pipeline", "comprar", "extraer"}:
                    from ..pipeline import comprar_import
                    mensajes.append(comprar_import.enriquecer_bo(conn, progress=progress))
                if accion == "indec":
                    mensajes.append(indec.run(conn))
                    from ..sources import cotizaciones
                    try:
                        mensajes.append(cotizaciones.run(conn))
                    except Exception as exc:   # noqa: BLE001 — sin dólar se sigue: solo afecta cómo se muestran montos
                        mensajes.append(f"cotizaciones del dólar no disponibles ({type(exc).__name__})")
                if accion == "empresas":
                    from ..sources import registro_sociedades
                    mensajes.append(registro_sociedades.run(conn, progress=progress))
                if accion == "precios":
                    from ..sources import precios_hoy
                    mensajes.append(precios_hoy.run(conn, progress=progress))
                if accion == "precios_programa":
                    from ..sources import precios_programa
                    mensajes.append(precios_programa.importar(conn, progress=progress))
                if accion in {"pipeline", "machear", "comprar", "precios_programa"}:
                    mensajes.append(match.match_mercado(conn))
                    mensajes.append(match.match_renglones(conn, progress=progress))
                if accion in {"pipeline", "machear", "comparar", "indec", "precios", "comprar", "extraer",
                             "precios_programa"}:
                    mensajes.append(compare.run(conn))
                db.sync_empresas(conn)
                from ..normalize import aplicar
                mensajes.append(aplicar.run(conn))
                db.finish_run(conn, run_id, "ok", 0, " · ".join(mensajes))
            except Exception as exc:
                db.finish_run(conn, run_id, "error", 0, f"{type(exc).__name__}: {exc}")
                raise
        return " · ".join(mensajes)
    return run


@app.post("/fuentes/run")
def fuentes_run(accion: str = Form(...), desde: str = Form(""), hasta: str = Form("")):
    if accion not in ACCIONES:
        return _redirect("/fuentes", "Acción desconocida")
    started, msg = _background.start("datos", _job_runner(accion, desde, hasta))
    return _redirect("/fuentes", f"Arrancó: {ACCIONES[accion]}" if started else msg)


# ── Copia de seguridad / restauración sin consola (MEJORAS #49.8) ──────────

@app.get("/fuentes/backup")
def fuentes_backup():
    """Descarga una copia de la base ACTUAL (API de backup online de SQLite: no bloquea otros
    pedidos). El archivo temporal se borra solo una vez que terminó de mandarse.

    Deshabilitado en modo público (LIC_MODO_PUBLICO=1, deploy en la nube de solo lectura): es un
    GET que de otra forma expondría TODA la base, notas de revisión humana incluidas, a
    cualquiera con el link. El formulario de abajo (`_modo_publico` en el contexto de la
    plantilla) ya no se muestra ahí, pero el chequeo acá es la barrera real."""
    if _modo_publico():
        return _redirect("/fuentes", "Descargar la base está deshabilitado en este despliegue de solo lectura")
    tmp_path = backup.hacer_backup()
    nombre = backup.nombre_backup()
    return FileResponse(tmp_path, filename=nombre, media_type="application/octet-stream",
                        background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)))


@app.post("/fuentes/restaurar")
async def fuentes_restaurar(archivo: UploadFile = File(...), confirmo: str = Form("")):
    # Defensa en profundidad: un deploy público ya bloquea todo POST con su propio middleware,
    # pero esta ruta reemplaza la base entera, así que no depende solo de eso.
    if _modo_publico():
        return _redirect("/fuentes", "Restaurar una base está deshabilitado en este despliegue de solo lectura")
    if confirmo != "si":
        return _redirect("/fuentes", "Confirmá la casilla antes de restaurar: esto reemplaza los datos actuales")
    contenido = await archivo.read(backup.MAX_RESTORE_BYTES + 1)
    if len(contenido) > backup.MAX_RESTORE_BYTES:
        return _redirect("/fuentes", f"El archivo pesa más de {backup.MAX_RESTORE_BYTES // (1024*1024)} MB: no se puede restaurar")
    try:
        msg = backup.restaurar(contenido)
    except ValueError as exc:
        return _redirect("/fuentes", f"No se pudo restaurar: {exc}")
    return _redirect("/fuentes", msg)


# ── Precios de programa (Precios Cuidados/Justos/Máximos): validación manual ───────────────

@app.get("/precios-programa", response_class=HTMLResponse)
def precios_programa(request: Request):
    abierta = request.query_params.get("edicion") or None
    with db.session() as conn:
        ediciones = queries.precios_programa_ediciones(conn)
        filas = queries.precios_programa_filas(conn, abierta) if abierta else []
    return _render(request, "precios_programa.html", ediciones=ediciones, abierta=abierta, filas=filas)


@app.post("/precios-programa/veredicto")
def precios_programa_veredicto(fuente: str = Form(...), obs_key: str = Form(...),
                               veredicto: str = Form(...), nota: str = Form("")):
    from .. import validaciones_programa as vp
    destino = f"/precios-programa?edicion={urllib.parse.quote(fuente)}"
    if veredicto not in vp.VEREDICTOS:
        return _redirect(destino, "Veredicto inválido")
    with db.session() as conn:
        vp.guardar(conn, fuente, obs_key, veredicto, nota, autor="humano")
        from ..pipeline import compare
        if conn.execute("SELECT producto_id FROM precios_mercado WHERE fuente=? AND obs_key=? LIMIT 1",
                        (fuente, obs_key)).fetchone():
            compare.run(conn)
    extra = " (se recalcularon los sobreprecios)" if veredicto == "no" else ""
    return _redirect(destino, f"Marcada como {veredicto}{extra}")


@app.get("/jobs/{name}")
def job_status(name: str):
    return JSONResponse(_background.status(name))
