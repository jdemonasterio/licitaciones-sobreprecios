from sobreprecios import config


def test_incluye_iva_lee_el_mapa_de_settings():
    # MEJORAS #42 (jdemonasterio 2026-10-12): documental, no cambia ningún cálculo.
    assert config.incluye_iva("mayorista") is True
    assert config.incluye_iva("minorista") is True
    assert config.incluye_iva("oficial") is True


def test_incluye_iva_canal_desconocido_es_none():
    assert config.incluye_iva("canal_que_no_existe") is None


def test_agrupar_subunidades_organismo_default_true():
    # MEJORAS #53: config/settings.yml -> organismos.agrupar_subunidades (default true).
    assert config.agrupar_subunidades_organismo() is True


def test_agrupar_subunidades_organismo_lee_false_del_yaml(monkeypatch):
    monkeypatch.setattr(config, "load_yaml", lambda name: {"organismos": {"agrupar_subunidades": False}})
    assert config.agrupar_subunidades_organismo() is False


def test_agrupar_subunidades_organismo_sin_seccion_es_default_true(monkeypatch):
    monkeypatch.setattr(config, "load_yaml", lambda name: {})
    assert config.agrupar_subunidades_organismo() is True
