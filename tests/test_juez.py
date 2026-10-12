import sqlite3

from auditoria import juez
from sobreprecios import db


def test_muestra_es_estratificada_y_de_solo_lectura(tmp_path):
    path = tmp_path / "t.db"
    with db.session(path) as conn:
        pid = db.upsert(conn, "procesos", {"fuente": "bo_primera", "external_id": "x"}, ("fuente", "external_id"))
        conn.execute("INSERT INTO productos (id, nombre, unidad_base) VALUES ('yerba_mate', 'Yerba', 'kg')")
        for i, sem in enumerate(["rojo", "amarillo", "verde", "gris"]):
            conn.execute("INSERT INTO renglones (proceso_id, descripcion, producto_id, external_key) VALUES (?, 'yerba', "
                         "'yerba_mate', ?)", (pid, f"k{i}"))
            rid = conn.execute("SELECT MAX(id) FROM renglones").fetchone()[0]
            conn.execute("INSERT INTO comparaciones (renglon_id, semaforo, evidencia_json) VALUES (?, ?, '[]')", (rid, sem))
    ro = juez._ro(path)
    items = juez.muestra(ro, 20)
    capas = {i["capa"] for i in items}
    assert {"extraccion", "comparacion"} <= capas
    sems = {i["item"]["resultado"]["semaforo"] for i in items if i["capa"] == "comparacion"}
    assert sems == {"rojo", "amarillo", "verde", "gris"}
    try:
        ro.execute("DELETE FROM renglones")
        assert False, "debería ser solo lectura"
    except sqlite3.OperationalError:
        pass
