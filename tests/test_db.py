from sobreprecios import db


def _fact(conn, tipo, persona, cargo, desde):
    conn.execute("INSERT INTO empresa_hechos (cuit, tipo, persona, cargo, vigente_desde, hecho_key) "
                 "VALUES ('30-1', ?, ?, ?, ?, ?)", (tipo, persona, cargo, desde, f"{tipo}{persona}{desde}"))


def test_close_superseded_facts_replaces_whole_board(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _fact(conn, "autoridad", "Ana", "presidente", "2019-05-01")
        _fact(conn, "autoridad", "Beto", "director suplente", "2019-05-01")
        _fact(conn, "autoridad", "Carla", "presidente", "2022-03-10")
        _fact(conn, "socio", "Ana", None, "2015-01-01")
        _fact(conn, "socio", "Dario", None, "2020-01-01")
        db.close_superseded_facts(conn, "30-1")
        got = {r["persona"] + ":" + r["tipo"]: r["vigente_hasta"] for r in
               conn.execute("SELECT persona, tipo, vigente_hasta FROM empresa_hechos")}
    assert got == {"Ana:autoridad": "2022-03-10", "Beto:autoridad": "2022-03-10",
                   "Carla:autoridad": None, "Ana:socio": None, "Dario:socio": None}
