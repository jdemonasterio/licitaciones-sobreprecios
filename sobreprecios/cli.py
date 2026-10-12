"""CLI: ingesta, análisis y webui de sobreprecios en compras del Estado.

Qué hace: baja normas de adjudicación del Boletín Oficial, extrae los renglones
comprados (proveedor, cantidad, precio unitario), los machea contra un catálogo
de productos "peras con peras" y los compara con precios de mercado (mayoristas,
supermercados, INDEC) a la misma fecha, ajustando por inflación y costo
financiero. El resultado se navega en un webui local.

Flujo de datos:
  BO (primera sección) --bo-buscar--> documentos --extraer--> procesos + renglones
  COMPR.AR --comprar--> procesos + renglones + ofertas (y completa los procesos del BO)
  dump Quijote 2021 --legacy--> renglones + precios mayoristas 2021
  INDEC (apis.datos.gob.ar) --indec--> IPC + precios promedio
  renglones + precios --machear--> producto canónico + precio por kg/l/u
  todo --comparar--> comparaciones (sobreprecio, semáforo, evidencia) --servir--> webui

Uso:
  python -m sobreprecios.cli setup                 # base + catálogo + INDEC + demo 2021
  python -m sobreprecios.cli bo-buscar --desde 2021-01-01 --hasta 2021-12-31
  python -m sobreprecios.cli bo-releer-legacy      # baja los avisos originales del dataset 2021
  python -m sobreprecios.cli comprar --desde 2026-01-01 --hasta 2026-09-30   # COMPR.AR alimentos/limpieza
  python -m sobreprecios.cli comprar --numero 80-0014-LPU25
  python -m sobreprecios.cli extraer [--sin-llm]
  python -m sobreprecios.cli machear [--sin-llm] [--todo]
  python -m sobreprecios.cli comparar
  python -m sobreprecios.cli pipeline --desde 2026-01-01 --hasta 2026-09-30
  python -m sobreprecios.cli servir [--puerto 8000]
  python -m sobreprecios.cli precios-hoy           # foto diaria de precios (súper + mayoristas)
  python -m sobreprecios.cli investigar "https://www.boletinoficial.gob.ar/detalleAviso/primera/341035/20260422"
  python -m sobreprecios.cli goldens               # TSV de validación en _scratch/licitaciones/
  python -m sobreprecios.cli estado

Tips:
  - Todo se cachea en data/cache (avisos del BO, respuestas de LLM): re-correr es gratis.
  - LIC_LLM_OFFLINE=1 corre sin LLM (solo reglas): sirve para probar sin claves.
  - Los goldens de validación van a _scratch/licitaciones/ (no se commitean).
"""
from __future__ import annotations

import datetime as dt
import logging
import sys

import click

from . import catalog, config, db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


@click.group()
def cli():
    config.load_env()
    if hasattr(sys.stdout, "reconfigure"):   # consola de Windows: acentos sin romper
        sys.stdout.reconfigure(encoding="utf-8")


def _run(tipo: str, fn, **params) -> str:
    with db.session() as conn:
        run_id = db.start_run(conn, tipo, params)
        try:
            msg = fn(conn)
        except Exception as exc:
            db.finish_run(conn, run_id, "error", 0, f"{type(exc).__name__}: {exc}")
            raise
        db.finish_run(conn, run_id, "ok", 0, msg)
    click.echo(msg)
    return msg


@cli.command()
@click.option("--sin-demo", is_flag=True, help="No importar el dataset Quijote 2021.")
@click.option("--dump", type=click.Path(), default=None, help="Ruta al dump MySQL de Quijote.")
def setup(sin_demo, dump):
    """Crea la base, carga catálogo e INDEC y (opcional) el dataset 2021 de ejemplo."""
    from .sources import indec, legacy_quijotes
    from .pipeline import compare, match
    with db.session() as conn:
        click.echo(f"base: {config.db_path()}")
        click.echo(f"catálogo: {catalog.sync_to_db(conn)} productos")
    _run("indec", indec.run)
    _cotizaciones_sin_cortar()
    if not sin_demo:
        try:
            _run("legacy_quijotes", lambda c: legacy_quijotes.run(c, dump))
        except FileNotFoundError as exc:
            click.echo(f"(sin dataset 2021: {exc})")
    _run("machear_mercado", match.match_mercado)
    _run("machear_renglones", lambda c: match.match_renglones(c, use_llm=False))
    _run("comparar", compare.run)


@cli.command("legacy")
@click.option("--dump", type=click.Path(), default=None)
def legacy(dump):
    """Importa el dump MySQL del sistema Grupo Quijote (2021)."""
    from .sources import legacy_quijotes
    _run("legacy_quijotes", lambda c: legacy_quijotes.run(c, dump), dump=dump)


@cli.command()
def indec():
    """Actualiza IPC y precios promedio INDEC."""
    from .sources import indec as indec_src
    _run("indec", indec_src.run)
    _cotizaciones_sin_cortar()


def _cotizaciones_sin_cortar():
    """Las cotizaciones del dólar solo sirven para mostrar montos: si la API no responde, se sigue."""
    from .sources import cotizaciones as cot
    try:
        _run("cotizaciones", cot.run)
    except Exception as exc:   # noqa: BLE001 — red caída no debe frenar el resto
        click.echo(f"(cotizaciones del dólar no disponibles: {exc})")


@cli.command("cotizaciones")
def cotizaciones():
    """Actualiza las cotizaciones diarias del dólar (para ver montos en dólares o pesos reales)."""
    from .sources import cotizaciones as cot
    _run("cotizaciones", cot.run)


@cli.command("bo-buscar")
@click.option("--desde", required=True, type=_date)
@click.option("--hasta", required=True, type=_date)
@click.option("--query", "queries", multiple=True, help="Búsqueda extra (todas las palabras). Repetible.")
def bo_buscar(desde, hasta, queries):
    """Busca en el BO normas que adjudican bienes y guarda su texto."""
    from .sources import boletin_oficial
    _run("bo_buscar", lambda c: boletin_oficial.discover(c, desde, hasta, list(queries) or None),
         desde=str(desde), hasta=str(hasta), queries=list(queries))


@cli.command("bo-releer-legacy")
def bo_releer_legacy():
    """Baja del BO los avisos originales de los procesos importados del dataset 2021."""
    from .sources import boletin_oficial

    def fn(conn):
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT p.external_id FROM procesos p JOIN renglones r ON r.proceso_id=p.id "
            "WHERE r.extraccion_metodo='legacy_quijotes' AND p.external_id LIKE 'primera/%'")]
        n = boletin_oficial.fetch_known(conn, ids)
        return f"{n}/{len(ids)} avisos originales bajados"
    _run("bo_releer_legacy", fn)


@cli.command("comprar")
@click.option("--desde", type=_date, default=None)
@click.option("--hasta", type=_date, default=None)
@click.option("--paginas", default=2, type=int, help="Páginas de resultados por rubro y estado.")
@click.option("--numero", "numeros", multiple=True, help="Bajar procesos puntuales (ej. 80-0014-LPU25).")
def comprar_cmd(desde, hasta, paginas, numeros):
    """COMPR.AR: procesos adjudicados de alimentos/limpieza (o números puntuales) + completar los del BO."""
    from .pipeline import comprar_import
    from .sources import comprar

    def fn(conn):
        msgs = []
        if numeros:
            client = comprar.ComprarClient()
            for n in numeros:
                comprar_import.store_detalle(conn, client.detalle_proceso(n))
            msgs.append(f"{len(numeros)} procesos puntuales")
        else:
            msgs.append(comprar_import.descubrir(conn, desde, hasta, max_paginas=paginas))
        msgs.append(comprar_import.enriquecer_bo(conn))
        return " · ".join(msgs)
    _run("comprar", fn, desde=str(desde), hasta=str(hasta), numeros=list(numeros))


@cli.command()
@click.option("--sin-llm", is_flag=True)
@click.option("--todo", is_flag=True, help="Re-extraer también los ya extraídos.")
def extraer(sin_llm, todo):
    """Extrae renglones adjudicados de los avisos del BO guardados."""
    from .pipeline import extract
    _run("extraer", lambda c: extract.run(c, only_pending=not todo, use_llm=not sin_llm))


@cli.command()
@click.option("--sin-llm", is_flag=True)
@click.option("--todo", is_flag=True, help="Re-machear todo, no solo lo pendiente.")
def machear(sin_llm, todo):
    """Asigna producto canónico y precio por unidad base (renglones y mercado)."""
    from .pipeline import match
    _run("machear_mercado", lambda c: match.match_mercado(c, solo_pendientes=not todo))
    _run("machear_renglones", lambda c: match.match_renglones(c, use_llm=not sin_llm,
                                                               solo_pendientes=not todo))


@cli.command()
def comparar():
    """Recalcula sobreprecios y semáforos."""
    from .pipeline import compare
    _run("comparar", compare.run)


@cli.command()
@click.option("--desde", type=_date, default=None)
@click.option("--hasta", type=_date, default=None)
@click.option("--sin-llm", is_flag=True)
def pipeline(desde, hasta, sin_llm):
    """Todo junto: [BO] -> extraer -> machear -> INDEC -> comparar."""
    from .pipeline import compare, extract, match
    from .sources import boletin_oficial, indec as indec_src
    from .pipeline import comprar_import
    if desde and hasta:
        _run("bo_buscar", lambda c: boletin_oficial.discover(c, desde, hasta),
             desde=str(desde), hasta=str(hasta))
        _run("comprar", lambda c: comprar_import.descubrir(c, desde, hasta))
    _run("extraer", lambda c: extract.run(c, use_llm=not sin_llm))
    _run("comprar_enriquecer", comprar_import.enriquecer_bo)
    _run("indec", indec_src.run)
    _cotizaciones_sin_cortar()
    _run("machear_mercado", match.match_mercado)
    _run("machear_renglones", lambda c: match.match_renglones(c, use_llm=not sin_llm))
    from .normalize import aplicar
    _run("normalizar", aplicar.run)
    _run("comparar", compare.run)


@cli.command()
@click.option("--puerto", default=8000, type=int)
@click.option("--host", default="127.0.0.1")
def servir(puerto, host):
    """Levanta el webui en http://127.0.0.1:<puerto>."""
    import uvicorn
    with db.session() as conn:
        catalog.sync_to_db(conn)
    uvicorn.run("sobreprecios.web.app:app", host=host, port=puerto, reload=False)


@cli.command()
@click.option("--zip", "zip_path", type=click.Path(exists=True), default=None, help="Snapshot ya bajado.")
def empresas(zip_path):
    """Completa datos de adjudicatarios con el Registro Nacional de Sociedades (todas las provincias)."""
    from pathlib import Path
    from .sources import registro_sociedades
    _run("registro_sociedades", lambda c: registro_sociedades.run(c, Path(zip_path) if zip_path else None))


@cli.command()
def normalizar():
    """Re-aplica la normalización de entidades (organismos, proveedores, personas) a toda la base."""
    from .normalize import aplicar
    _run("normalizar", aplicar.run)


@cli.command()
def goldens():
    """Escribe los TSV de validación manual en _scratch/licitaciones/."""
    from . import goldens as goldens_mod
    with db.session() as conn:
        for path in goldens_mod.run(conn):
            click.echo(str(path))


@cli.command("precios-hoy")
@click.option("--fuente", "fuentes", multiple=True, help="precios_claros, carrefour, maxiconsumo, sepa_gondolar")
def precios_hoy_cmd(fuentes):
    """Releva precios de HOY (supermercados y mayoristas). Correr una vez por día."""
    from .sources import precios_hoy
    _run("precios_hoy", lambda c: precios_hoy.run(c, list(fuentes) or None))


@cli.command()
@click.argument("entrada", required=False, default="")
@click.option("--pdf", type=click.Path(exists=True), default=None)
def investigar(entrada, pdf):
    """Investiga UNA licitación: link del BO / COMPR.AR, número de proceso, texto o --pdf."""
    from . import investigar as inv
    click.echo(inv.investigar(entrada, pdf, progress=click.echo))


@cli.command()
def estado():
    """Conteos rápidos de la base."""
    with db.session() as conn:
        for table in ["documentos", "procesos", "renglones", "precios_mercado", "indices", "comparaciones",
                      "llm_calls"]:
            click.echo(f"{table:16s} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>8}")
        for row in conn.execute("SELECT semaforo, COUNT(*) n, ROUND(SUM(exceso_ars)) exceso FROM comparaciones "
                                "GROUP BY semaforo ORDER BY n DESC"):
            click.echo(f"  semáforo {row['semaforo']:9s} {row['n']:>5}  exceso ${row['exceso'] or 0:,.0f}")
        spend = conn.execute("SELECT ROUND(SUM(costo_usd), 4) FROM llm_calls").fetchone()[0]
        click.echo(f"gasto LLM acumulado: USD {spend or 0}")


if __name__ == "__main__":
    cli()
