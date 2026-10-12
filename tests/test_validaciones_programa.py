"""Tests de validaciones_programa.py: guardar/leer veredictos humanos y del juez LLM, y el
import del TSV de auditoría externa (sin red, fixture propio del test)."""
import pytest

from sobreprecios import db, validaciones_programa as vp

FUENTE = "programa:precios_cuidados:ed1"


def test_guardar_valida_el_veredicto():
    with db.session(":memory:") as conn:
        with pytest.raises(ValueError):
            vp.guardar(conn, FUENTE, "k1", "quizas", None)


def test_guardar_es_upsert_por_fuente_obs_key_autor(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        vp.guardar(conn, FUENTE, "k1", "ok", "primera nota", autor="humano")
        vp.guardar(conn, FUENTE, "k1", "no", "cambié de opinión", autor="humano")
        rows = conn.execute("SELECT * FROM validaciones_programa WHERE fuente=? AND obs_key=?",
                            (FUENTE, "k1")).fetchall()
        assert len(rows) == 1   # no deja un historial de marcas viejas
        assert rows[0]["veredicto"] == "no" and rows[0]["nota"] == "cambié de opinión"

        # Autor distinto (el juez): fila aparte, no pisa la del humano.
        vp.guardar(conn, FUENTE, "k1", "ok", "el juez dice que está bien", autor="juez_llm")
        por_autor = vp.por_fuente(conn, FUENTE)["k1"]
        assert por_autor["humano"]["veredicto"] == "no"
        assert por_autor["juez_llm"]["veredicto"] == "ok"


def test_por_fuente_no_mezcla_otras_ediciones(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        vp.guardar(conn, FUENTE, "k1", "ok", None)
        vp.guardar(conn, "programa:precios_justos:ed2", "k1", "no", None)
        assert vp.por_fuente(conn, FUENTE) == {"k1": {"humano": {"veredicto": "ok", "nota": None}}}


# ── importar_juez_tsv: emparejamiento por edición (fecha_publicacion) + texto normalizado ──

TSV_HEADER = ("fecha_publicacion\tfuente\turl\tproducto_texto_original\tmarca\tpresentacion\tprecio_ars\t"
             "producto_canonico_sistema\tvariante_sistema\tcontenido_envase\tprecio_por_unidad_base\t"
             "Veredicto juez\tTipo de error\tExplicación juez\tValor correcto\tRegla sugerida\t"
             "Verificación contra la fuente\tTu veredicto\tTu comentario\n")


def _tsv_row(fecha, texto, veredicto, tipo_error="", explicacion=""):
    return (f"{fecha}\tfuente\turl\t{texto}\tMarca\t1 kg\t100\tcanonico\t\t1.0\t100.0\t"
           f"{veredicto}\t{tipo_error}\t{explicacion}\t\t\t\t\t\n")


def test_importar_juez_tsv_empareja_por_edicion_y_texto_sin_precio(tmp_path, monkeypatch):
    from sobreprecios import config

    ediciones = {"ed1": {"programa": "precios_cuidados", "fecha_publicacion": "2020-01-07"}}
    monkeypatch.setattr(config, "load_yaml", lambda name: {"ediciones": ediciones} if "precios_programa" in name
                        else pytest.fail(f"no debería leer {name}"))

    tsv = tmp_path / "juez.tsv"
    tsv.write_text(
        TSV_HEADER
        + _tsv_row("2020-01-07", "Yerba Mate Rosamonte x 500 Gr, $ 100", "ok")
        + _tsv_row("2020-01-07", "Producto Que No Esta En La Base x 1 Kg, $ 50", "error", "normalizacion_entidad",
                   "explicación")
        + _tsv_row("2099-12-31", "Fecha de una edición que no existe x 1 Kg, $ 1", "ok"),
        encoding="utf-8")

    with db.session(tmp_path / "t.db") as conn:
        conn.execute(
            "INSERT INTO precios_mercado (fuente, canal, fecha, descripcion, precio, producto_id, obs_key) "
            "VALUES (?, 'programa', '2020-01-16', ?, 100, 'yerba_mate', 'ok1')",
            ("programa:precios_cuidados:ed1", "Yerba Mate Rosamonte x 500 Gr [vigente 2020-01-07 a 2020-04-06]"))
        conn.commit()

        msg = vp.importar_juez_tsv(conn, tsv)
        assert "1/3" in msg

        v = vp.por_fuente(conn, "programa:precios_cuidados:ed1")
        assert v["ok1"]["juez_llm"]["veredicto"] == "ok"


def test_importar_juez_tsv_traduce_veredictos_dudosos_y_error(tmp_path, monkeypatch):
    from sobreprecios import config

    ediciones = {"ed1": {"programa": "precios_cuidados", "fecha_publicacion": "2020-01-07"}}
    monkeypatch.setattr(config, "load_yaml", lambda name: {"ediciones": ediciones})

    tsv = tmp_path / "juez.tsv"
    tsv.write_text(
        TSV_HEADER
        + _tsv_row("2020-01-07", "Atun desmenuzado Supermercado x 170 Gr, $ 69", "error", "variante_perdida", "msg")
        + _tsv_row("2020-01-07", "Dulce de leche Sancor x 400 Gr, $ 100", "dudoso")
        + _tsv_row("2020-01-07", "Cacao en polvo Arcoa x 180 Gr, $ 39", "no aplica"),
        encoding="utf-8")

    with db.session(tmp_path / "t.db") as conn:
        for key, desc, prod in [
            ("atun", "Atun desmenuzado Supermercado x 170 Gr", "atun_conserva"),
            ("dulce", "Dulce de leche Sancor x 400 Gr", "dulce_leche"),
            ("cacao", "Cacao en polvo Arcoa x 180 Gr", "cacao_polvo"),
        ]:
            conn.execute(
                "INSERT INTO precios_mercado (fuente, canal, fecha, descripcion, precio, producto_id, obs_key) "
                "VALUES ('programa:precios_cuidados:ed1', 'programa', '2020-01-16', ?, 1, ?, ?)",
                (desc, prod, key))
        conn.commit()

        vp.importar_juez_tsv(conn, tsv)
        v = vp.por_fuente(conn, "programa:precios_cuidados:ed1")
        assert v["atun"]["juez_llm"]["veredicto"] == "no"       # error -> no
        assert v["dulce"]["juez_llm"]["veredicto"] == "no_se"   # dudoso -> no_se (no se inventa una exclusión)
        assert v["cacao"]["juez_llm"]["veredicto"] == "no_se"   # "no aplica" -> no_se
