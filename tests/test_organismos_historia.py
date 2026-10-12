"""Tests de sobreprecios/organismos_historia.py (MEJORAS #39, P13). Sin red: `buscar_noticias`
y `_llm_confirma` van mockeados. El grafo acíclico y el umbral de 4 confirmaciones son la
lógica que realmente queremos cubrir."""
from __future__ import annotations

import pytest

from sobreprecios import db, organismos_historia as oh


def _item(medio: str, titulo: str = "t", url: str | None = None) -> dict:
    return {"titulo": titulo, "medio": medio, "fecha_nota": "2026-01-01", "url": url or f"http://{medio}/x"}


# ── seeds() desde config/organismos.yml ─────────────────────────────────────────────────────

def test_seeds_lee_los_renombres_reales_de_organismos_yml():
    todos = oh.seeds()
    por_saf = {s.saf: s for s in todos}
    assert "311" in por_saf
    assert por_saf["311"].anterior == "Ministerio de Desarrollo Social"
    assert por_saf["311"].nuevo == "Ministerio de Capital Humano"
    assert "343" in por_saf
    assert por_saf["343"].anterior == "Ministerio de Seguridad"
    assert por_saf["343"].nuevo == "Ministerio de Seguridad Nacional"


# ── investigar(): umbral de confirmaciones independientes ──────────────────────────────────

def test_investigar_confirmado_con_4_medios_distintos(monkeypatch):
    items = [_item(f"Medio{i}") for i in range(4)]
    monkeypatch.setattr(oh, "buscar_noticias", lambda q, max_resultados=6: items)
    monkeypatch.setattr(oh, "_llm_confirma", lambda a, n, it: {
        "confirma": True, "tipo": "renombre", "fecha": "2023-12-10", "razon": "mock"})
    resultado = oh.investigar("A", "B", con_llm=True)
    assert resultado["estado"] == "confirmado"
    assert len(resultado["medios_independientes"]) == 4


def test_investigar_propuesto_con_menos_de_4_medios(monkeypatch):
    items = [_item("Medio1"), _item("Medio1", url="http://medio1/otra-nota"), _item("Medio2")]
    monkeypatch.setattr(oh, "buscar_noticias", lambda q, max_resultados=6: items)
    monkeypatch.setattr(oh, "_llm_confirma", lambda a, n, it: {
        "confirma": True, "tipo": "renombre", "fecha": "2023-12-10", "razon": "mock"})
    resultado = oh.investigar("A", "B", con_llm=True)
    # 2 notas de "Medio1" (mismo medio, no suman independencia) + 1 de "Medio2" = 2 medios distintos.
    assert resultado["estado"] == "propuesto"
    assert resultado["medios_independientes"] == ["Medio1", "Medio2"]


def test_investigar_sin_llm_nunca_confirma_nada(monkeypatch):
    """Sin LLM no hay forma de leer si la nota realmente confirma el cambio: nunca se asume
    que sí solo porque salió en la búsqueda (CLAUDE.md: "un resultado falso es peor que ninguno")."""
    items = [_item(f"Medio{i}") for i in range(6)]
    monkeypatch.setattr(oh, "buscar_noticias", lambda q, max_resultados=6: items)
    resultado = oh.investigar("A", "B", con_llm=False)
    assert resultado["estado"] == "propuesto"
    assert resultado["confirmaciones"] == []


def test_investigar_llm_no_confirma_no_cuenta(monkeypatch):
    items = [_item(f"Medio{i}") for i in range(4)]
    monkeypatch.setattr(oh, "buscar_noticias", lambda q, max_resultados=6: items)
    monkeypatch.setattr(oh, "_llm_confirma", lambda a, n, it: {
        "confirma": False, "tipo": None, "fecha": None, "razon": "no habla del cambio"})
    resultado = oh.investigar("A", "B", con_llm=True)
    assert resultado["estado"] == "propuesto"
    assert resultado["confirmaciones"] == []


def test_investigar_llm_no_disponible_no_rompe(monkeypatch):
    items = [_item("Medio1")]
    monkeypatch.setattr(oh, "buscar_noticias", lambda q, max_resultados=6: items)
    monkeypatch.setattr(oh, "_llm_confirma", lambda a, n, it: None)   # LLMUnavailable, ya capturado adentro
    resultado = oh.investigar("A", "B", con_llm=True)
    assert resultado["estado"] == "propuesto"
    assert resultado["confirmaciones"] == []


def test_investigar_dedup_por_url_entre_queries(monkeypatch):
    llamadas = []

    def fake_buscar(q, max_resultados=6):
        llamadas.append(q)
        return [_item("Medio1", url="http://medio1/misma-nota")]

    monkeypatch.setattr(oh, "buscar_noticias", fake_buscar)
    monkeypatch.setattr(oh, "_llm_confirma", lambda a, n, it: {
        "confirma": True, "tipo": "renombre", "fecha": None, "razon": "mock"})
    resultado = oh.investigar("A", "B", con_llm=True)
    assert len(llamadas) >= 2            # investigar hace más de una query
    assert resultado["n_resultados"] == 1   # pero la misma URL en las dos no duplica el resultado


# ── es_aciclico() / guardar(): nunca cerrar un ciclo ────────────────────────────────────────

def _resultado(anterior, nuevo, n_medios=4, tipo="renombre", fecha="2023-01-01"):
    return {
        "anterior": anterior, "nuevo": nuevo, "n_resultados": n_medios,
        "confirmaciones": [{**_item(f"M{i}"), "tipo": tipo, "fecha": fecha} for i in range(n_medios)],
        "medios_independientes": [f"M{i}" for i in range(n_medios)],
        "estado": "confirmado" if n_medios >= 4 else "propuesto",
    }


def test_guardar_arco_confirmado_queda_en_la_tabla(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        msg = oh.guardar(conn, "311", _resultado("Desarrollo Social", "Capital Humano"))
        assert "confirmado" in msg
        rows = oh.leer(conn)
    assert len(rows) == 1
    assert rows[0]["desde"] == "Desarrollo Social" and rows[0]["hacia"] == "Capital Humano"
    assert rows[0]["estado"] == "confirmado"


def test_guardar_arco_propuesto_con_pocas_fuentes(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        msg = oh.guardar(conn, "343", _resultado("Seguridad", "Seguridad Nacional", n_medios=1))
        assert "propuesto" in msg
        rows = oh.leer(conn, estado="propuesto")
    assert len(rows) == 1


def test_es_aciclico_rechaza_un_arco_que_cierra_un_ciclo(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        oh.guardar(conn, None, _resultado("A", "B"))
        oh.guardar(conn, None, _resultado("B", "C"))
        assert oh.es_aciclico(conn, "C", "A") is False   # C -> A cerraria A -> B -> C -> A
        msg = oh.guardar(conn, None, _resultado("C", "A"))
        assert "RECHAZADO" in msg
        rows = oh.leer(conn)
    # el ciclo nunca se escribió: siguen solo los 2 arcos originales.
    assert {(r["desde"], r["hacia"]) for r in rows} == {("A", "B"), ("B", "C")}


def test_es_aciclico_permite_una_cadena_sin_ciclo(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        oh.guardar(conn, None, _resultado("A", "B"))
        assert oh.es_aciclico(conn, "B", "C") is True
        oh.guardar(conn, None, _resultado("B", "C"))
        rows = oh.leer(conn)
    assert len(rows) == 2


def test_es_aciclico_rechaza_un_arco_a_si_mismo(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        assert oh.es_aciclico(conn, "A", "A") is False


def test_guardar_actualiza_el_mismo_arco_sin_duplicar(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        oh.guardar(conn, "311", _resultado("Desarrollo Social", "Capital Humano", n_medios=1))
        oh.guardar(conn, "311", _resultado("Desarrollo Social", "Capital Humano", n_medios=4))
        rows = oh.leer(conn)
    assert len(rows) == 1
    assert rows[0]["estado"] == "confirmado"


# ── run(): orquesta seeds + investigar + guardar ────────────────────────────────────────────

def test_run_con_seeds_de_prueba(tmp_path, monkeypatch):
    monkeypatch.setattr(oh, "buscar_noticias", lambda q, max_resultados=6: [_item(f"M{i}") for i in range(4)])
    monkeypatch.setattr(oh, "_llm_confirma", lambda a, n, it: {
        "confirma": True, "tipo": "renombre", "fecha": "2023-12-10", "razon": "mock"})
    seed = oh.Seed(saf="999", anterior="Viejo Nombre", nuevo="Nombre Nuevo", desde="2023-01-01")
    with db.session(tmp_path / "t.db") as conn:
        msg = oh.run(conn, con_llm=True, solo=[seed])
        rows = oh.leer(conn)
    assert "confirmado" in msg
    assert len(rows) == 1 and rows[0]["desde"] == "Viejo Nombre"
