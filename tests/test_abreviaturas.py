"""Tests de sobreprecios/abreviaturas.py (MEJORAS #1, P22): expansión aditiva para el matcher
y minería de candidatos a partir de descripciones sin producto. Sin red, LLM mockeado."""
from __future__ import annotations

import pytest

from sobreprecios import abreviaturas, catalog, db


@pytest.fixture
def catalogo():
    return catalog.load_catalog()


def _renglon_sin_producto(conn, descripcion: str, nro: int) -> None:
    pid = db.upsert(conn, "procesos", {"fuente": "test", "external_id": f"p{nro}"}, ("fuente", "external_id"))
    conn.execute("INSERT INTO renglones (proceso_id, descripcion, cantidad, precio_unitario, external_key) "
                "VALUES (?, ?, 1, 1, ?)", (pid, descripcion, f"k{nro}"))


def test_cargar_lee_los_seeds_aprobados_de_config():
    aprobadas = {a.abreviatura for a in abreviaturas.cargar(estado="aprobada")}
    assert {"fid", "lch", "desc", "descr", "p descr"} <= aprobadas


def test_expandir_agrega_tokens_sin_borrar_el_original():
    out = abreviaturas.expandir("FID GUISERO 500G")
    toks = set(out.lower().split())
    assert "fid" in toks        # el token original se preserva
    assert "fideos" in toks     # la expansión se agrega


def test_expandir_multi_palabra_p_descr():
    out = abreviaturas.expandir("LECHE UAT P DESCR FORT")
    toks = set(out.lower().split())
    assert {"parcialmente", "descremada"} <= toks


def test_expandir_nunca_matchea_substring_no_token_completo():
    # "fid" no tiene que "pegarle" a una palabra que lo contiene como substring.
    out = abreviaturas.expandir("CONFIDENCIAL ALGO")
    assert out == "CONFIDENCIAL ALGO"


def test_expandir_sin_texto_no_rompe():
    assert abreviaturas.expandir(None) == ""
    assert abreviaturas.expandir("") == ""


def test_expandir_es_idempotente_en_el_set_de_tokens_relevantes():
    una = set(abreviaturas.expandir("FID GUISERO").lower().split())
    dos = set(abreviaturas.expandir(abreviaturas.expandir("FID GUISERO")).lower().split())
    assert una == dos


def test_match_rules_fid_matchea_fideos_secos_via_abreviatura(catalogo):
    m = catalog.match_rules("FID TIPO MOSTACHOL X 500 GRS", catalogo)
    assert m.producto_id == "fideos_secos"


def test_match_rules_lch_matchea_leche_entera_via_abreviatura(catalogo):
    m = catalog.match_rules("LCH ENTERA SACHET 1 LT", catalogo)
    assert m.producto_id == "leche_entera_fluida"


def test_match_rules_fid_hvo_no_cae_como_fideos_secos_comunes(catalogo):
    """Regresion encontrada midiendo el impacto real de 'fid' en la base principal (2026-10-12):
    sin la abreviatura 'hvo'->'huevo', fideos al huevo abreviados como HVO pasaban el incluye
    de fideos_secos (gracias a 'fid'->'fideos') pero no activaban su excluye (huevo), porque
    'huevo' estaba abreviado. Con 'hvo' aprobado, vuelve a excluirse."""
    m = catalog.match_rules("FID DON FELIPE TALL HVO", catalogo)
    assert m.producto_id is None


def test_match_rules_fid_int_no_cae_como_fideos_secos_comunes(catalogo):
    m = catalog.match_rules("FID INT SAN AGUSTIN MOST", catalogo)
    assert m.producto_id is None


def test_minar_candidatos_encuentra_fid_si_no_estuviera_aprobado(tmp_path, monkeypatch):
    """Con un YAML vacío (sin seeds), minar_candidatos tiene que proponer 'fid' -> 'fideos' a
    partir de renglones sin producto que lo mencionan (prefijo de una palabra `incluye`)."""
    empty_yaml = tmp_path / "abreviaturas.yml"
    empty_yaml.write_text("abreviaturas: []\n", encoding="utf-8")
    monkeypatch.setattr(abreviaturas, "_yaml_path", lambda: tmp_path / "abreviaturas.yml")

    with db.session(tmp_path / "t.db") as conn:
        _renglon_sin_producto(conn, "FID TIPO GUISERO 500 G", 1)
        _renglon_sin_producto(conn, "FID CODITO 400 G", 2)
        candidatos = abreviaturas.minar_candidatos(conn, limite=10)
    por_abrev = {c["abreviatura"]: c for c in candidatos}
    assert "fid" in por_abrev
    assert por_abrev["fid"]["expande"] == "fideos"
    assert por_abrev["fid"]["producto_id"] == "fideos_secos"
    assert por_abrev["fid"]["frecuencia"] == 2


def test_minar_candidatos_no_repropone_lo_ya_visto(tmp_path, monkeypatch):
    monkeypatch.setattr(abreviaturas, "_yaml_path", lambda: tmp_path / "abreviaturas.yml")
    (tmp_path / "abreviaturas.yml").write_text("abreviaturas: []\n", encoding="utf-8")
    with db.session(tmp_path / "t.db") as conn:
        _renglon_sin_producto(conn, "FID TIPO GUISERO 500 G", 1)
        _renglon_sin_producto(conn, "FID CODITO 400 G", 2)
        candidatos_1 = abreviaturas.minar_candidatos(conn, limite=10)
        assert any(c["abreviatura"] == "fid" for c in candidatos_1)
        nuevas = abreviaturas.proponer(conn, limite=10)
        assert any(n["abreviatura"] == "fid" for n in nuevas)
        candidatos_2 = abreviaturas.minar_candidatos(conn, limite=10)
    assert not any(c["abreviatura"] == "fid" for c in candidatos_2)


def test_proponer_nunca_auto_aprueba_incluso_con_llm_mockeado(tmp_path, monkeypatch):
    monkeypatch.setattr(abreviaturas, "_yaml_path", lambda: tmp_path / "abreviaturas.yml")
    (tmp_path / "abreviaturas.yml").write_text("abreviaturas: []\n", encoding="utf-8")
    monkeypatch.setattr(abreviaturas, "_llm_verificar",
                        lambda c: {"es_abreviatura": True, "confianza": "alta", "razon": "mock"})
    with db.session(tmp_path / "t.db") as conn:
        _renglon_sin_producto(conn, "FID TIPO GUISERO 500 G", 1)
        _renglon_sin_producto(conn, "FID CODITO 400 G", 2)
        nuevas = abreviaturas.proponer(conn, limite=10, con_llm=True)
    assert nuevas
    assert all(n["estado"] == "propuesta" for n in nuevas)
    assert any(n.get("llm_veredicto", {}).get("es_abreviatura") is True for n in nuevas)


def test_aprobar_cambia_estado_de_una_propuesta(tmp_path, monkeypatch):
    monkeypatch.setattr(abreviaturas, "_yaml_path", lambda: tmp_path / "abreviaturas.yml")
    (tmp_path / "abreviaturas.yml").write_text(
        "abreviaturas:\n- abreviatura: xyz\n  expande: ejemplo\n  producto_id: null\n"
        "  estado: propuesta\n  evidencia: []\n  fecha: '2026-10-12'\n  fuente: humano\n",
        encoding="utf-8")
    assert abreviaturas.aprobar("xyz", "aprobada") is True
    assert abreviaturas.cargar(estado="aprobada")[0].abreviatura == "xyz"
    assert abreviaturas.aprobar("no existe", "aprobada") is False


def test_aprobar_preserva_el_comentario_de_cabecera(tmp_path, monkeypatch):
    monkeypatch.setattr(abreviaturas, "_yaml_path", lambda: tmp_path / "abreviaturas.yml")
    (tmp_path / "abreviaturas.yml").write_text(
        "# cabecera de prueba\n# segunda linea\n\nabreviaturas:\n- abreviatura: xyz\n"
        "  expande: ejemplo\n  producto_id: null\n  estado: propuesta\n  evidencia: []\n"
        "  fecha: '2026-10-12'\n  fuente: humano\n",
        encoding="utf-8")
    abreviaturas.aprobar("xyz", "aprobada")
    texto = (tmp_path / "abreviaturas.yml").read_text(encoding="utf-8")
    assert texto.startswith("# cabecera de prueba")
