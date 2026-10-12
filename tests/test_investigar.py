import pytest

from sobreprecios.investigar import normalizar_entrada


@pytest.mark.parametrize("entrada,esperado", [
    ("https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607?busqueda=1",
     "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607"),
    ("  boletinoficial.gob.ar/detalleAviso/tercera/2418819/20261009  ",
     "https://www.boletinoficial.gob.ar/detalleAviso/tercera/2418819/20261009"),
    ("https://www.boletinoficial.gob.ar/#!DetalleNorma/245330/20210607",
     "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607"),
])
def test_normalizar_links_del_bo(entrada, esperado):
    out = normalizar_entrada(entrada)
    assert out["tipo"] == "bo" and out["url"] == esperado


def test_normalizar_link_comprar_saca_fragmento():
    out = normalizar_entrada("https://comprar.gob.ar/PLIEGO/VistaPreviaPliegoCiudadano.aspx?qs=AbC123#top")
    assert out == {"tipo": "comprar_url",
                   "url": "https://comprar.gob.ar/PLIEGO/VistaPreviaPliegoCiudadano.aspx?qs=AbC123"}


@pytest.mark.parametrize("entrada,numero", [("80-0014-lpu25", "80-0014-LPU25"), ("84/99-0651-LPU26", "84/99-0651-LPU26")])
def test_normalizar_numero_de_proceso(entrada, numero):
    assert normalizar_entrada(entrada) == {"tipo": "comprar_numero", "numero": numero}


def test_normalizar_texto_y_vacio():
    assert normalizar_entrada("ARTÍCULO 1°.- Adjudícase ...")["tipo"] == "texto"
    assert normalizar_entrada("   ") == {"tipo": "vacio"}


def test_normalizar_rechaza_hosts_ajenos():
    out = normalizar_entrada("https://evil.example/?x=comprar.gob.ar")
    assert out["tipo"] == "invalido"
    assert normalizar_entrada("http://comprar.gob.ar/PLIEGO/x.aspx?qs=1")["url"].startswith("https://comprar.gob.ar/")
