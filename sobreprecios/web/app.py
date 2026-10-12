"""Webui local (FastAPI + Jinja2): licitaciones, análisis, reportes y fuentes.

Solapas:
  /              Licitaciones: lista filtrable, ordenada por exceso estimado
  /licitacion/N  Detalle: contexto del proceso, renglones, referencias, evidencia, revisión humana
  /analitica     Totales por organismo, producto, proveedor, procedimiento y mes
  /producto/ID   Precio pagado por el Estado vs mercado en el tiempo
  /empresas      Adjudicatarios; /empresa/CUIT con historia (autoridades, socios, domicilios)
  /reportes      Armar un reporte para periodistas (HTML imprimible + Excel + CSV)
  /fuentes       Estado de los datos y botones para actualizar (jobs en background)
  /investigar    Investigar UNA licitación a pedido: link del BO / COMPR.AR, número, texto o PDF

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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import config, db, reports
from . import _background, queries

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

def _ars(value, decimals=0):
    if value in (None, ""):
        return "—"
    try:
        text = f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"
    return "$ " + text.replace(",", "X").replace(".", ",").replace("X", ".")


def _num(value, decimals=0):
    if value in (None, ""):
        return "—"
    text = f"{float(value):,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(value):
    if value in (None, ""):
        return "—"
    return f"{float(value):+.1f}%".replace(".", ",")


def _millones(value):
    if not value:
        return "$ 0"
    value = float(value)
    if abs(value) >= 1e9:
        return f"$ {value / 1e9:,.2f} mil M".replace(".", ",")
    if abs(value) >= 1e6:
        return f"$ {value / 1e6:,.1f} M".replace(".", ",")
    return _ars(value)


SEMAFORO_TXT = {"rojo": "Sobreprecio alto", "amarillo": "Sobreprecio moderado", "verde": "En línea con mercado",
                "gris": "Sin comparación"}
REF_TXT = {"misma_marca": "misma marca", "p25_mayorista": "mayorista (P25)",
           "mediana_mayorista": "mayorista (mediana)", "min_mayorista": "mayorista (mínimo)",
           "mediana_minorista": "supermercado (mediana)", "indec": "INDEC (promedio)",
           "min_mercado": "mínimo de mercado", "min_minorista": "supermercado (mínimo)"}
ATRIBUTO_TXT = {"verificado": "variedad verificada", "no_exigido": "variedad no exigida en la ficha",
                "desconocido": "variedad desconocida", "ambiguo": "variedad ambigua", "sin_mercado": "variedad sin mercado"}

templates.env.filters.update(ars=_ars, num=_num, pct=_pct, millones=_millones)
templates.env.globals.update(SEMAFORO_TXT=SEMAFORO_TXT, REF_TXT=REF_TXT, ATRIBUTO_TXT=ATRIBUTO_TXT)


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


def _filtros(request: Request) -> dict:
    keys = ["q", "producto", "organismo", "semaforo", "desde", "hasta", "revision", "proveedor"]
    return {k: request.query_params.get(k, "").strip() for k in keys}


def _render(request: Request, name: str, **ctx) -> HTMLResponse:
    ctx.setdefault("message", request.query_params.get("message"))
    ctx["request"] = request
    ctx["jobs"] = _background.all_status()
    return templates.TemplateResponse(request, name, ctx)


# ── Licitaciones ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def licitaciones(request: Request):
    filtros = _filtros(request)
    orden = request.query_params.get("orden", "exceso")
    with db.session() as conn:
        return _render(request, "licitaciones.html", filtros=filtros, orden=orden,
                       kpis=queries.kpis(conn, filtros),
                       procesos=queries.list_procesos(conn, filtros, orden),
                       opciones=queries.opciones_filtros(conn))


@app.get("/licitacion/{proceso_id}", response_class=HTMLResponse)
def licitacion(request: Request, proceso_id: int):
    with db.session() as conn:
        proc = queries.proceso(conn, proceso_id)
        if not proc:
            return _redirect("/", "No existe esa licitación")
        rows = queries.renglones(conn, proceso_id)
    from ..pipeline.extract import resolutive_part
    texto = resolutive_part(proc.get("texto") or "", 12000) if proc.get("texto") else ""
    return _render(request, "licitacion.html", p=proc, renglones=rows, texto=texto,
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


# ── Analítica ──────────────────────────────────────────────────────────────

@app.get("/analitica", response_class=HTMLResponse)
def analitica(request: Request):
    filtros = _filtros(request)
    with db.session() as conn:
        data = {dim: queries.agrupado(conn, filtros, dim) for dim in
                ["organismo", "producto", "proveedor", "procedimiento", "mes"]}
        return _render(request, "analitica.html", filtros=filtros, data=data,
                       kpis=queries.kpis(conn, filtros), peores=queries.peores_renglones(conn, filtros),
                       opciones=queries.opciones_filtros(conn))


@app.get("/producto/{producto_id}", response_class=HTMLResponse)
def producto(request: Request, producto_id: str):
    with db.session() as conn:
        prod = conn.execute("SELECT * FROM productos WHERE id=?", (producto_id,)).fetchone()
        if not prod:
            return _redirect("/analitica", "Producto desconocido")
        return _render(request, "producto.html", prod=dict(prod), serie=queries.serie_producto(conn, producto_id))


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


# ── Fuentes y jobs ─────────────────────────────────────────────────────────

ACCIONES = {
    "pipeline": "Buscar en el BO y COMPR.AR + extraer + machear + comparar",
    "comprar": "Buscar en COMPR.AR (alimentos y limpieza adjudicados)",
    "bo_buscar": "Buscar adjudicaciones en el Boletín Oficial",
    "extraer": "Extraer renglones de los avisos bajados",
    "machear": "Asignar productos y precios por kg/l",
    "indec": "Actualizar IPC y precios INDEC",
    "precios": "Bajar precios de hoy (supermercados y mayoristas)",
    "comparar": "Recalcular sobreprecios",
    "empresas": "Completar empresas (Registro Nacional de Sociedades, ~230 MB la primera vez)",
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
                if accion == "empresas":
                    from ..sources import registro_sociedades
                    mensajes.append(registro_sociedades.run(conn, progress=progress))
                if accion == "precios":
                    from ..sources import precios_hoy
                    mensajes.append(precios_hoy.run(conn, progress=progress))
                if accion in {"pipeline", "machear", "comprar"}:
                    mensajes.append(match.match_mercado(conn))
                    mensajes.append(match.match_renglones(conn, progress=progress))
                if accion in {"pipeline", "machear", "comparar", "indec", "precios", "comprar", "extraer"}:
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


@app.get("/jobs/{name}")
def job_status(name: str):
    return JSONResponse(_background.status(name))
