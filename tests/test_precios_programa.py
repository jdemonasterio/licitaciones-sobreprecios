"""Tests de sources/precios_programa.py: sin red (fixtures en tests/fixtures/precios_programa/),
ver CLAUDE.md. Cubre los tres formatos (pdf_texto, infogram, texto_diario), el macheo al
catálogo, la réplica por vigencia y la idempotencia de obs_key."""
from pathlib import Path

import pytest

from sobreprecios import db
from sobreprecios.catalog import load_catalog
from sobreprecios.sources import precios_programa as pp

FIXTURES = Path(__file__).parent / "fixtures" / "precios_programa"
CATALOG = load_catalog()


# ── Parsers por formato ──────────────────────────────────────────────────────────────────────

def test_filas_pdf_detecta_columnas_por_posicion_no_por_cantidad_fija():
    # 4 columnas (Descripción/Marca/Proveedor/Precio) y 2 columnas (Producto/Precio): mismo
    # detector, sin cantidad de columnas hardcodeada.
    filas4 = pp._filas_pdf(FIXTURES / "precios_cuidados_2022_01_fragmento.pdf")
    assert len(filas4) == 6
    assert filas4[0] == {"descripcion": "Aceite de Oliva Comestible Cocinero 500 Ml",
                         "marca": "Cocinero", "precio": 459.07}

    filas2 = pp._filas_pdf(FIXTURES / "precios_cuidados_2022_10_fragmento.pdf")
    assert len(filas2) == 5
    assert filas2[0] == {"descripcion": "Aceite Girasol Canuelas 1,5 lt", "marca": None, "precio": 376.1}


def test_filas_infogram_lee_el_json_embebido_con_decimales_ar():
    html = (FIXTURES / "infogram_2021_fragmento.html").read_text(encoding="utf-8")
    filas = pp._filas_infogram(html)
    # La cabecera ("PRODUCTO"/"PRECIO en pesos") no es una fila de datos.
    assert {"descripcion": "Aceite Girasol Cada Dia x 1500 cc", "marca": None, "precio": 153.0} in filas
    # Coma decimal argentina dentro de un string ("109,1") se parsea igual que un número.
    assert {"descripcion": "Yerba Mate Rosamonte x 500 gr", "marca": None, "precio": 109.1} in filas
    assert len(filas) == 5   # la cabecera queda afuera; "Zapatillas..." es ruido pero sí es una fila válida acá


def test_filas_texto_diario_lee_li_y_parrafo_con_punto_y_coma():
    cronista = pp._filas_texto_diario((FIXTURES / "cronista_2020_fragmento.html").read_text(encoding="utf-8"))
    nombres = {f["descripcion"] for f in cronista}
    assert "Aceite de Girasol Cada Dia x 1500 Cc" in nombres
    assert "Yerba Mate Rosamonte x 500 Gr" in nombres

    eco = pp._filas_texto_diario((FIXTURES / "eleconomista_2023_fragmento.html").read_text(encoding="utf-8"))
    assert {"descripcion": "Arroz Largo Fino Primor 1 Kg", "marca": None, "precio": 412.0} in eco
    assert {"descripcion": "Detergente Magistral 300 cc", "marca": None, "precio": 164.99} in eco


# ── Macheo al catálogo (_mapear_fila) ────────────────────────────────────────────────────────

def test_mapear_fila_exige_catalogo_y_tamano_en_la_unidad_base():
    ok = pp._mapear_fila({"descripcion": "Yerba Mate Rosamonte x 500 Gr", "marca": None, "precio": 100.0}, CATALOG)
    assert ok["producto_id"] == "yerba_mate" and ok["unidad_base"] == "kg"
    assert ok["precio_unidad_base"] == pytest.approx(200.0)

    # No matchea ningún producto del catálogo: se descarta (ruido de prensa, no se adivina).
    assert pp._mapear_fila({"descripcion": "Zapatillas Deportivas Nike x 1 Un", "marca": None, "precio": 45000.0},
                           CATALOG) is None
    # Matchea el catálogo pero sin tamaño parseable: tampoco se guarda.
    assert pp._mapear_fila({"descripcion": "Yerba Mate Rosamonte", "marca": None, "precio": 100.0}, CATALOG) is None


# ── Réplica por vigencia ─────────────────────────────────────────────────────────────────────

def test_fechas_vigencia_es_el_1_y_el_16_de_cada_mes_dentro_del_rango():
    assert pp._fechas_vigencia("2022-01-12", "2022-04-12") == [
        "2022-01-16", "2022-02-01", "2022-02-16", "2022-03-01", "2022-03-16", "2022-04-01"]
    # Un solo mes, vigencia corta: el 1 no entra (antes del inicio), el 16 sí.
    assert pp._fechas_vigencia("2022-01-12", "2022-01-20") == ["2022-01-16"]


def test_clave_es_estable_para_la_misma_fila_y_distinta_si_cambia_algo():
    fila = {"descripcion": "Yerba Mate Rosamonte x 500 Gr", "marca": None, "precio": 100.0}
    assert pp._clave(fila) == pp._clave(dict(fila))   # reimportar la misma edición no duplica
    assert pp._clave(fila) != pp._clave(dict(fila, precio=101.0))


# ── importar(): orquestación de punta a punta, sin red (fixtures locales) ──────────────────

def test_importar_mapea_replica_y_es_idempotente(tmp_path, monkeypatch):
    fuente = "programa:precios_cuidados:test_ed"
    edicion = {"programa": "precios_cuidados", "vigencia_desde": "2020-01-07", "vigencia_hasta": "2020-02-07",
              "fecha_publicacion": "2020-01-07", "region": "nacional", "formato": "texto_diario",
              "urls": ["https://ejemplo.invalido/no-se-usa"]}
    monkeypatch.setattr(pp, "_ediciones", lambda: {"test_ed": edicion})
    monkeypatch.setattr(pp, "_descargar", lambda eid, urls, ext, progress=None: FIXTURES / "cronista_2020_fragmento.html")

    with db.session(tmp_path / "t.db") as conn:
        msg = pp.importar(conn)
        assert "test_ed" in msg
        filas = conn.execute("SELECT * FROM precios_mercado WHERE fuente=?", (fuente,)).fetchall()
        assert filas and all(r["canal"] == "programa" for r in filas)
        assert all(r["descripcion"].endswith("[vigente 2020-01-07 a 2020-02-07]") for r in filas)
        # Un mes y una semana de vigencia: 1/1 (fuera, antes del desde), 16/1, 1/2 -> 2 fechas x producto.
        obs_keys = {r["obs_key"] for r in filas}
        n_productos = len(obs_keys)
        assert len(filas) == n_productos * 2

        # Reimportar la misma edición no duplica (UPSERT por fuente+fecha+obs_key).
        pp.importar(conn, edicion_id="test_ed")
        filas2 = conn.execute("SELECT COUNT(*) n FROM precios_mercado WHERE fuente=?", (fuente,)).fetchone()
        assert filas2["n"] == len(filas)


def test_etiqueta_edicion_nombre_legible_programa_mes_anio_region():
    """MEJORAS #49.7 (item 4): nunca mostrar el id interno como única referencia."""
    assert pp.etiqueta_edicion("programa:precios_cuidados:precios_cuidados_2021_01") == \
        "Precios Cuidados — enero 2021 (lista nacional)"
    assert pp.etiqueta_edicion("programa:precios_justos:precios_justos_2022_11") == \
        "Precios Justos — noviembre 2022 (AMBA)"
    # También acepta el edicion_id solo (sin el prefijo "programa:<programa>:").
    assert pp.etiqueta_edicion("precios_cuidados_2020_01") == "Precios Cuidados — enero 2020 (lista nacional)"


def test_etiqueta_edicion_sin_config_no_rompe():
    out = pp.etiqueta_edicion("programa:precios_cuidados:edicion_inexistente")
    assert "Precios Cuidados" in out   # usa el programa de la clave aunque no esté en el YAML


def test_importar_edicion_desconocida_no_rompe():
    with db.session(":memory:") as conn:
        msg = pp.importar(conn, edicion_id="no_existe_esta_edicion")
    assert "desconocida" in msg
