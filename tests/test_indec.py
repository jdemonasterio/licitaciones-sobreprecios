"""IPC con fecha de publicación y version (MEJORAS #30/#45, jdemonasterio 2026-10-12): indec.run()
guarda cuando se estima que se publico cada mes (no hay metadata de fecha real en la API) y la
version (timestamp de la bajada), para que el comparador pueda elegir el modo "disponible a la
fecha" (config/settings.yml -> comparacion.ipc_modo)."""
from sobreprecios import db
from sobreprecios.sources import indec


def test_fecha_publicacion_estimada_es_el_15_del_mes_siguiente():
    assert indec.fecha_publicacion_estimada("2021-04-01") == "2021-05-15"
    assert indec.fecha_publicacion_estimada("2021-12-01") == "2022-01-15"


def test_run_guarda_fecha_publicacion_y_version_en_indices_y_su_alias(tmp_path, monkeypatch):
    serie_ipc = "148.3_INIVELNAL_DICI_M_26"
    monkeypatch.setattr(indec, "fetch_series",
                        lambda ids, start="2016-01-01": {serie_ipc: {"2021-04-01": 100.0, "2021-05-01": 103.0}})
    monkeypatch.setattr(indec.config, "settings",
                        lambda: {"indices": {"ipc_general": serie_ipc}, "db_path": "x", "cache_dir": "x"})
    monkeypatch.setattr(indec, "load_catalog", lambda: {})
    with db.session(tmp_path / "t.db") as conn:
        indec.run(conn)
        serie_rows = {r["fecha"]: dict(r) for r in
                     conn.execute("SELECT * FROM indices WHERE serie=?", (serie_ipc,))}
        alias_rows = {r["fecha"]: dict(r) for r in
                     conn.execute("SELECT * FROM indices WHERE serie='ipc_general'")}
    assert serie_rows["2021-04-01"]["fecha_publicacion"] == "2021-05-15"
    assert serie_rows["2021-04-01"]["version"]   # timestamp no vacio
    # El alias (el que usa el comparador) conserva fecha_publicacion y version, no solo el valor.
    assert alias_rows["2021-04-01"]["fecha_publicacion"] == "2021-05-15"
    assert alias_rows["2021-04-01"]["valor"] == 100.0
    assert alias_rows["2021-05-01"]["fecha_publicacion"] == "2021-06-15"
