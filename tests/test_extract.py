from pathlib import Path

import pytest

from sobreprecios.pipeline.extract import (extract_document, flatten_llm, numero_proceso,
                                           parse_marca_exigida, regex_extract, resolutive_part,
                                           validate_renglon)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def mds_735():
    return (FIXTURES / "bo_mds_res735_2021.txt").read_text(encoding="utf-8")


def test_regex_extract_reads_all_suppliers_of_mds_res_735(mds_735):
    rows = regex_extract(mds_735)
    assert [(r["proveedor"], r["precio_unitario"], r["monto_total"], r["marca"]) for r in rows] == [
        ("COMPAÑÍA AMERICANA DE ALIMENTOS S.A.", 490.0, 98000000.0, "SPRAYMILK"),
        ("MAFRALAC ALIMENTICIA S.R.L.", 519.74, 103948000.0, "FRANZ"),      # "$519,74.-"
        ("S.A. LA SIBILA", 532.0, 106400000.0, "PURÍSIMA"),
        ("ALIMENTOS VIDA S.A.", 547.0, 273500000.0, "VIDALAC"),
        ("MILKAUT S.A.", 553.0, 110600000.0, "FRANSAFÉ"),                   # "según mejora," en el medio
    ]
    assert all(r["unidad"] == "kilogramos" and r["contenido_envase"] == "1 kilogramo" for r in rows)
    assert rows[0]["cuit"] == "30-70779374-7"


def test_extract_document_without_llm_validates_amounts(mds_735):
    result = extract_document(mds_735, use_llm=False)
    assert result["metodo"] == "regex"
    assert result["tipo_procedimiento"] == "emergencia"
    assert result["numero_proceso"] == "Contratación por Emergencia COVID-19 Nº 24/21"
    assert all(r["alertas"] == [] for r in result["renglones"])


# ── Marca exigida por el pliego (jdemonasterio 2026-10-12, distinta de `marca`/la ofrecida) ─────

@pytest.mark.parametrize("descripcion,marca,equivalente_ok", [
    ("ARROZ BLANCO; MARCA: GALLO O EQUIVALENTE", "GALLO", True),
    ("Leche en polvo marca La Serenisima o similar x 800 g", "La Serenisima", True),
    ("Detergente marca Magistral, envase x 750 ml", "Magistral", False),
    ("ARROZ BLANCO; TIPO GALLO O EQUIVALENTE, PRESENTACION: 1 KG", "GALLO", True),
    ("Yerba mate tipo Taragui o similar x 1 kg", "Taragui", True),
    ("ARROZ; MARCA: GALLO, PRESENTACION 1 KG", "GALLO", False),
    ("Yerba mate marca Playadito x 1 kg", "Playadito", False),
    (None, None, None),
    ("Arroz blanco comun x 1 kg", None, None),   # sin "marca"/"tipo ... equivalente": no se asume nada
])
def test_parse_marca_exigida_casos(descripcion, marca, equivalente_ok):
    assert parse_marca_exigida(descripcion) == (marca, equivalente_ok)


def test_parse_marca_exigida_tipo_sin_equivalente_no_es_marca():
    """'tipo' sin "o equivalente/similar" describe casi siempre OTRO atributo (ficha SIByS), no una
    marca — el falso positivo real que motivó separar el patrón de "marca"."""
    assert parse_marca_exigida("ARROZ; TIPO GRANO: LARGO-FINO, PRESENTACION: 1 KG") == (None, None)
    assert parse_marca_exigida("YERBA MATE; TIPO: CON PALO, CERTIFICADO FORESTAL: SIN") == (None, None)


def test_resolutive_part_starts_at_decide():
    text = "VISTO ... CONSIDERANDO ... Por ello, EL JEFE DE GABINETE DE MINISTROS DECIDE: ARTÍCULO 1°.- Apruébase"
    assert resolutive_part(text).startswith("DECIDE:")


@pytest.mark.parametrize("text,expected", [
    ("Apruébase la LICITACIÓN PÚBLICA Nº 80-0014-LPU25, cuyo objeto", "80-0014-LPU25"),
    ("la Licitación Pública N° 12/2021 del Ministerio", "Licitación Pública N° 12/2021"),
    ("sin número", None),
])
def test_numero_proceso(text, expected):
    assert numero_proceso(text) == expected


def test_validate_renglon_flags_inconsistent_totals_and_fills_missing_price():
    bad = {"cantidad": 200000, "precio_unitario": 392.0, "monto_total": 98000000, "descripcion": "leche"}
    assert any(a.startswith("monto_inconsistente") for a in validate_renglon(bad))
    missing = {"cantidad": 600000, "precio_unitario": None, "monto_total": 237000000, "descripcion": "leche"}
    alerts = validate_renglon(missing)
    assert missing["precio_unitario"] == pytest.approx(395.0)
    assert alerts == ["precio_unitario_calculado: monto_total / cantidad"]


def test_flatten_llm_spreads_group_amount_as_weighted_average():
    # DA 2021 MSAL: "Renglón 1 por 321.144 kg, renglón 3 por 115.072 kg y renglón 5 por 365.551 kg: $309.255.655"
    adj = [{"proveedor": "S.A. LA SIBILA", "cuit": "30-58885563-1", "monto_total_adjudicado": 309255655,
            "renglones": [
                {"renglon": n, "descripcion": "leche entera en polvo", "cantidad": q, "unidad": "kilogramos",
                 "contenido_envase": None, "marca": None, "precio_unitario": None, "monto_total": None}
                for n, q in [(1, 321144), (3, 115072), (5, 365551)]]}]
    rows = flatten_llm(adj)
    assert {round(r["precio_unitario"], 2) for r in rows} == {385.72}
    assert sum(r["monto_total"] for r in rows) == pytest.approx(309255655, rel=1e-6)
    assert all(r["_alertas_previas"][0].startswith("precio_promedio_del_grupo") for r in rows)


def test_flatten_llm_does_not_average_mixed_units():
    adj = [{"proveedor": "X", "cuit": None, "monto_total_adjudicado": 1000,
            "renglones": [
                {"renglon": 1, "descripcion": "a", "cantidad": 10, "unidad": "kilogramos", "contenido_envase": None,
                 "marca": None, "precio_unitario": None, "monto_total": None},
                {"renglon": 2, "descripcion": "b", "cantidad": 5, "unidad": "litros", "contenido_envase": None,
                 "marca": None, "precio_unitario": None, "monto_total": None}]}]
    assert all(r["precio_unitario"] is None for r in flatten_llm(adj))


def test_flatten_llm_does_not_average_different_goods_same_unit():
    adj = [{"proveedor": "X", "cuit": None, "monto_total_adjudicado": 1000,
            "renglones": [
                {"renglon": 1, "descripcion": "arroz", "cantidad": 10, "unidad": "kilogramos", "contenido_envase": None,
                 "marca": None, "precio_unitario": None, "monto_total": None},
                {"renglon": 2, "descripcion": "azúcar", "cantidad": 5, "unidad": "kilogramos", "contenido_envase": None,
                 "marca": None, "precio_unitario": None, "monto_total": None}]}]
    rows = flatten_llm(adj)
    assert all(r["precio_unitario"] is None for r in rows)
    assert all(a.startswith("monto_conjunto") for r in rows for a in r["_alertas_previas"])
