from sobreprecios.sources.registro_sociedades import actividad_relacionada, collect, hechos_de

ROW = {"cuit": "30707793747", "razon_social": "COMPAÑIA AMERICANA DE ALIMENTOS S.A.",
       "fecha_hora_contrato_social": "2001-10-10-12:00", "tipo_societario": "SOCIEDAD ANONIMA",
       "fecha_hora_actualizacion": "2026-04-16-08:05", "dom_legal_calle": "CALLE 14", "dom_legal_numero": "0",
       "dom_legal_localidad": "CARLOS KEEN", "dom_legal_provincia": "BUENOS AIRES", "dom_fiscal_calle": "DESCARTES",
       "dom_fiscal_numero": "3535", "dom_fiscal_localidad": "TORTUGUITAS", "dom_fiscal_provincia": "BUENOS AIRES",
       "actividad_descripcion": "Elaboración de galletitas y bizcochos", "actividad_orden": "1", "actividad_estado": "AC"}


def test_collect_keeps_only_requested_cuits_and_active_activities():
    rows = [("f.csv", ROW), ("f.csv", dict(ROW, actividad_descripcion="", actividad_estado="BD")),
            ("f.csv", dict(ROW, cuit="30500000127"))]
    found = collect(iter(rows), {"30707793747"})
    assert list(found) == ["30707793747"]
    assert found["30707793747"]["actividades"] == [("1", "Elaboración de galletitas y bizcochos")]


def test_hechos_de_builds_dated_facts():
    item = {"row": ROW, "csv": "f.csv", "actividades": [("1", "Elaboración de galletitas y bizcochos")]}
    hechos = {h["tipo"]: h for h in hechos_de(item)}
    assert hechos["constitucion"]["vigente_desde"] == "2001-10-10"
    assert hechos["domicilio"]["valor"] == "CALLE 14, CARLOS KEEN, BUENOS AIRES (legal)"   # número 0 no se muestra
    assert hechos["domicilio_fiscal"]["valor"] == "DESCARTES 3535, TORTUGUITAS, BUENOS AIRES"
    assert hechos["actividad"]["vigente_desde"] == "2026-04-16"


def test_actividad_relacionada():
    assert actividad_relacionada(["Elaboración de galletitas y bizcochos"], {"alimentos"}) is True
    assert actividad_relacionada(["Transporte de energía eléctrica"], {"alimentos"}) is False
    assert actividad_relacionada([], {"alimentos"}) is None


import pytest

from sobreprecios.empresas import parece_persona


@pytest.mark.parametrize("nombre,esperado", [
    ("Jaime Szmuc", True), ("Sonia de Las Heras Liceras", True), ("SOFIA CAROLINA RIPANI", True),
    ("Ciudad de Buenos Aires", False), ("CPA 1428", False), ("MONROE 2270 piso - 803", False),
    ("con domicilio especial en Argentina", False), ("CONSULGROUP S.A. CAMIM S.A. ro 2835", False),
    ("Car- del Directorio de la misma fecha se designaron tiene en la nombrada sociedad", False), (None, False),
    ("otorgada ante el Jaime Szmuc", False), ("La socie- mero Bonomi", False), ("dentro del quíntuplo", False),
])
def test_parece_persona(nombre, esperado):
    assert parece_persona(nombre) is esperado
