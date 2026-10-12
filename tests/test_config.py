from sobreprecios import config


def test_incluye_iva_lee_el_mapa_de_settings():
    # MEJORAS #42 (jdemonasterio 2026-10-12): documental, no cambia ningún cálculo.
    assert config.incluye_iva("mayorista") is True
    assert config.incluye_iva("minorista") is True
    assert config.incluye_iva("oficial") is True


def test_incluye_iva_canal_desconocido_es_none():
    assert config.incluye_iva("canal_que_no_existe") is None
