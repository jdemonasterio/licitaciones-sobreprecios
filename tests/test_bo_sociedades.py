"""Tests de la extracción por reglas (sin red, sin LLM) de `bo_sociedades.py`.

Los fixtures son avisos REALES de "COMPAÑIA AMERICANA DE ALIMENTOS S.A."
(CUIT 30-70779374-7, uno de los ejemplos del encargo) bajados en vivo del
Boletín Oficial el 2026-10-11 — ver cabecera de cada .txt para el id/fecha
original si hace falta re-verificar contra la fuente.
"""
from pathlib import Path

import pytest

from sobreprecios.sources.bo_sociedades import (
    _tokens_distintivos,
    extraer_hechos,
)

FIXTURES = Path(__file__).parent / "fixtures" / "bo_segunda"
RAZON_SOCIAL = "COMPAÑIA AMERICANA DE ALIMENTOS S.A."


def _leer(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def test_tokens_distintivos_descarta_palabras_genericas_societarias():
    # "COMPAÑIA", "DE" y "S.A." no son distintivos (el buscador del BO los
    # ignora igual: ver gotcha 1 en bo_sociedades.py). Esto es lo que hace que
    # buscar "COMPAÑIA AMERICANA" sin más dé 0 resultados en el sitio real.
    assert _tokens_distintivos(RAZON_SOCIAL) == ["AMERICANA", "ALIMENTOS"]
    assert _tokens_distintivos("S.A. LA SIBILA") == ["SIBILA"]
    assert _tokens_distintivos("MILKAUT S.A.") == ["MILKAUT"]


def test_extraer_hechos_designacion_autoridades_2016():
    texto = _leer("americana_alimentos_2016_designacion.txt")
    hechos = extraer_hechos(texto, RAZON_SOCIAL)
    autoridades = [h for h in hechos if h["tipo"] == "autoridad"]
    por_cargo = {(h["cargo"], h["persona"]) for h in autoridades}
    assert ("presidente", "Jaime Szmuc") in por_cargo
    assert ("director_titular", "Hugo Sebastian Romero Bonomi") in por_cargo
    assert ("director_titular", "Julio Cesar Romero Bonomi") in por_cargo
    assert ("director_titular", "Sonia de Las Heras Liceras") in por_cargo
    assert ("director_suplente", "Facundo Miguel Iturbe") in por_cargo
    assert len(autoridades) == 5
    # Este aviso no trae DNI: el campo tiene que quedar null, no inventado.
    assert all(h["documento"] is None for h in autoridades)


def test_extraer_hechos_designacion_autoridades_2014_con_dni():
    texto = _leer("americana_alimentos_2014_designacion_dni.txt")
    hechos = extraer_hechos(texto, RAZON_SOCIAL)
    autoridades = {(h["cargo"], h["persona"]): h["documento"] for h in hechos if h["tipo"] == "autoridad"}
    assert autoridades[("presidente", "Jaime Szmuc")] == "4126876"
    assert autoridades[("director_titular", "Hugo Sebastian Romero Bonomi")] == "27745127"
    assert autoridades[("director_titular", "Sonia de Las Heras Liceras")] == "93942581"
    assert autoridades[("director_suplente", "Facundo Miguel Iturbe")] == "23469377"
    # vigente_desde viene de la fecha del acta (20/01/2014), no de la fecha de
    # publicación del aviso (04/08/2014) ni de la escritura (09/06/2014).
    assert all(h["vigente_desde"] == "2014-01-20" for h in hechos if h["tipo"] == "autoridad")


def test_extraer_hechos_constitucion_y_objeto_social_2012():
    texto = _leer("americana_alimentos_2012_objeto_constitucion.txt")
    hechos = extraer_hechos(texto, RAZON_SOCIAL, cuit="30707793747")

    constitucion = next(h for h in hechos if h["tipo"] == "constitucion")
    assert constitucion["vigente_desde"] == "2001-10-19"
    assert constitucion["documento"] == "30707793747"

    objeto = next(h for h in hechos if h["tipo"] == "objeto")
    assert "establecimientos alimenticios" in objeto["valor"]
    assert "agricolo-ganaderas" in objeto["valor"].lower() or "agrícolo-ganaderas" in objeto["valor"].lower()


def test_extraer_hechos_pagina_ocr_vieja_no_mezcla_sociedad_vecina():
    """El fixture es una página escaneada de 2010 con VARIAS sociedades por
    columnas intercaladas (ver gotcha 4 de bo_sociedades.py): en la misma
    página hay un "resolvió disolver la sociedad" que es de CANTERA LA
    DONOSTIA S.A., no de la empresa buscada. Si `extraer_hechos` no acotara
    el texto por los tokens distintivos de la razón social, le atribuiría
    esa disolución (y el domicilio legal de esa otra sociedad) a la
    buscada — este test es la regresión para ese bug."""
    texto = _leer("pagina_ocr_2010_columnas_mezcladas.txt")
    hechos = extraer_hechos(texto, RAZON_SOCIAL)

    assert not any(h["tipo"] == "disolucion" for h in hechos)
    assert not any(h["tipo"] == "domicilio" for h in hechos)
    # Lo que SÍ es de la empresa buscada (designación de Jaime Szmuc como
    # Presidente) tiene que seguir apareciendo pese a acotar el texto. Es un
    # match por substring, no exacto: dentro de la ventana el texto sigue
    # viniendo con columnas intercaladas (gotcha 4), así que sin un DNI que
    # ancle el nombre, el fallback por coma arrastra basura OCR alrededor
    # ("otorgada ante el Jaime Szmuc") — esperado y documentado, no un bug.
    presidentes = [h["persona"] for h in hechos if h["tipo"] == "autoridad" and h["cargo"] == "presidente"]
    assert any("Jaime Szmuc" in p for p in presidentes)


def test_extraer_hechos_fecha_publicacion_distinta_de_fecha_del_acto():
    """MEJORAS #12/#40: `fecha_publicacion` (pie "e. DD/MM/AAAA Nº ...") es la fecha en que el
    Boletín publicó el aviso, no la del acto (`vigente_desde`) — en este fixture son 6 meses y
    medio distintas (acta 20/01/2014, publicación 04/08/2014)."""
    texto = _leer("americana_alimentos_2014_designacion_dni.txt")
    hechos = extraer_hechos(texto, RAZON_SOCIAL)
    autoridades = [h for h in hechos if h["tipo"] == "autoridad"]
    assert autoridades
    assert all(h["vigente_desde"] == "2014-01-20" for h in autoridades)
    assert all(h["fecha_publicacion"] == "2014-08-04" for h in autoridades)


def test_extraer_hechos_renovacion_total_por_vencimiento_de_cargos():
    """El fixture 2014 dice "por vencimiento de los cargos de los directores se decide
    designar": es una renovación TOTAL (todos los cargos vencieron), aunque el texto no diga
    literalmente "nuevo directorio"."""
    texto = _leer("americana_alimentos_2014_designacion_dni.txt")
    hechos = extraer_hechos(texto, RAZON_SOCIAL)
    autoridades = [h for h in hechos if h["tipo"] == "autoridad"]
    assert autoridades and all(h["renovacion_total"] is True for h in autoridades)


def test_extraer_hechos_renovacion_total_nuevo_directorio_2016():
    texto = _leer("americana_alimentos_2016_designacion.txt")
    hechos = extraer_hechos(texto, RAZON_SOCIAL)
    autoridades = [h for h in hechos if h["tipo"] == "autoridad"]
    assert autoridades and all(h["renovacion_total"] is True for h in autoridades)
    assert all(h["fecha_publicacion"] == "2016-12-21" for h in autoridades)


def test_extraer_hechos_sin_pie_de_publicacion_fecha_publicacion_none():
    """Un hecho sin el pie "e. .../ v. ..." en su ámbito relevante no debe inventar una fecha
    de publicación (best-effort: ninguno de los fixtures de constitución/objeto la ejercita con
    certeza, así que esto cubre el caso general sin ese pie)."""
    from sobreprecios.sources.bo_sociedades import extraer_hechos as _eh
    hechos = _eh("CARGO: Presidente: Alguien Random.", "ALGUIEN RANDOM SA")
    assert hechos and hechos[0]["fecha_publicacion"] is None


def test_extraer_hechos_texto_vacio_devuelve_lista_vacia():
    assert extraer_hechos("", RAZON_SOCIAL) == []


@pytest.mark.parametrize("razon_social", ["", "S.A.", "DE LA"])
def test_extraer_hechos_razon_social_sin_tokens_no_rompe(razon_social):
    texto = _leer("americana_alimentos_2016_designacion.txt")
    # Sin tokens distintivos no se puede acotar nada (no aplica en este
    # fixture, que no tiene marcadores OCR), pero tampoco debe explotar.
    assert isinstance(extraer_hechos(texto, razon_social), list)
