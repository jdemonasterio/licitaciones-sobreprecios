from sobreprecios import db


def _fact(conn, tipo, persona, cargo, desde, renovacion_total=None):
    conn.execute(
        "INSERT INTO empresa_hechos (cuit, tipo, persona, cargo, vigente_desde, renovacion_total, hecho_key) "
        "VALUES ('30-1', ?, ?, ?, ?, ?, ?)",
        (tipo, persona, cargo, desde, renovacion_total, f"{tipo}{persona}{cargo}{desde}"))


def test_close_superseded_facts_partial_designation_only_closes_matching_cargo(tmp_path):
    """MEJORAS #12/#40: un acto que solo reemplaza al presidente no puede cerrarle la fecha de
    salida a un director suplente que el acto nunca menciona."""
    with db.session(tmp_path / "t.db") as conn:
        _fact(conn, "autoridad", "Ana", "presidente", "2019-05-01")
        _fact(conn, "autoridad", "Beto", "director suplente", "2019-05-01")
        _fact(conn, "autoridad", "Carla", "presidente", "2022-03-10")  # reemplaza solo a Ana (mismo cargo)
        _fact(conn, "socio", "Ana", None, "2015-01-01")
        _fact(conn, "socio", "Dario", None, "2020-01-01")
        db.close_superseded_facts(conn, "30-1")
        got = {r["persona"] + ":" + r["tipo"]: r["vigente_hasta"] for r in
               conn.execute("SELECT persona, tipo, vigente_hasta FROM empresa_hechos")}
    assert got == {"Ana:autoridad": "2022-03-10", "Beto:autoridad": None,
                   "Carla:autoridad": None, "Ana:socio": None, "Dario:socio": None}


def test_close_superseded_facts_total_renewal_closes_whole_board(tmp_path):
    """Con `renovacion_total=1` (el acto dice "nuevo directorio" / "por vencimiento de los
    cargos") sí se cierra el directorio entero, aunque el acto no repita cada cargo."""
    with db.session(tmp_path / "t.db") as conn:
        _fact(conn, "autoridad", "Ana", "presidente", "2019-05-01")
        _fact(conn, "autoridad", "Beto", "director suplente", "2019-05-01")
        _fact(conn, "autoridad", "Carla", "presidente", "2022-03-10", renovacion_total=1)
        db.close_superseded_facts(conn, "30-1")
        got = {r["persona"]: r["vigente_hasta"] for r in
               conn.execute("SELECT persona, vigente_hasta FROM empresa_hechos WHERE tipo='autoridad'")}
    assert got == {"Ana": "2022-03-10", "Beto": "2022-03-10", "Carla": None}


def test_close_superseded_facts_same_date_same_act_does_not_close_itself(tmp_path):
    """Hechos con la misma fecha son del mismo acto: no se cierran entre sí."""
    with db.session(tmp_path / "t.db") as conn:
        _fact(conn, "autoridad", "Ana", "presidente", "2019-05-01")
        _fact(conn, "autoridad", "Beto", "director suplente", "2019-05-01")
        db.close_superseded_facts(conn, "30-1")
        got = {r["persona"]: r["vigente_hasta"] for r in
               conn.execute("SELECT persona, vigente_hasta FROM empresa_hechos WHERE tipo='autoridad'")}
    assert got == {"Ana": None, "Beto": None}
