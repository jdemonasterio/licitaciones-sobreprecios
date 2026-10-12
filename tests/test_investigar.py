import json

import pytest

from sobreprecios import db
from sobreprecios.investigar import (_clave_caso, _nota_extrapolado, _resumen_comparacion, ensure_schema,
                                     lista, normalizar_entrada)


@pytest.mark.parametrize("entrada,esperado", [
    ("https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607?busqueda=1",
     "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607"),
    ("  boletinoficial.gob.ar/detalleAviso/tercera/2418819/20261009  ",
     "https://www.boletinoficial.gob.ar/detalleAviso/tercera/2418819/20261009"),
    ("https://www.boletinoficial.gob.ar/#!DetalleNorma/245330/20210607",
     "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607"),
])
def test_normalizar_links_del_bo(entrada, esperado):
    out = normalizar_entrada(entrada)
    assert out["tipo"] == "bo" and out["url"] == esperado


def test_normalizar_link_comprar_saca_fragmento():
    out = normalizar_entrada("https://comprar.gob.ar/PLIEGO/VistaPreviaPliegoCiudadano.aspx?qs=AbC123#top")
    assert out == {"tipo": "comprar_url",
                   "url": "https://comprar.gob.ar/PLIEGO/VistaPreviaPliegoCiudadano.aspx?qs=AbC123"}


@pytest.mark.parametrize("entrada,numero", [("80-0014-lpu25", "80-0014-LPU25"), ("84/99-0651-LPU26", "84/99-0651-LPU26")])
def test_normalizar_numero_de_proceso(entrada, numero):
    assert normalizar_entrada(entrada) == {"tipo": "comprar_numero", "numero": numero}


def test_normalizar_texto_y_vacio():
    assert normalizar_entrada("ARTÍCULO 1°.- Adjudícase ...")["tipo"] == "texto"
    assert normalizar_entrada("   ") == {"tipo": "vacio"}


def test_normalizar_rechaza_hosts_ajenos():
    out = normalizar_entrada("https://evil.example/?x=comprar.gob.ar")
    assert out["tipo"] == "invalido"
    assert normalizar_entrada("http://comprar.gob.ar/PLIEGO/x.aspx?qs=1")["url"].startswith("https://comprar.gob.ar/")


# ── Agrupar reintentos del mismo caso (MEJORAS #49.1, item 1d) ──────────────

def test_clave_caso_agrupa_el_mismo_aviso_del_bo_con_y_sin_busqueda():
    con_busqueda = normalizar_entrada(
        "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607?busqueda=1")
    sin_busqueda = normalizar_entrada(
        "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607")
    fila = lambda n, row_id: {"id": row_id, "normalizada": json.dumps(n, ensure_ascii=False)}
    assert _clave_caso(fila(con_busqueda, 1)) == _clave_caso(fila(sin_busqueda, 2))


def test_clave_caso_no_agrupa_texto_pegado_distinto():
    fila = lambda texto, row_id: {"id": row_id, "normalizada": json.dumps({"tipo": "texto", "texto": texto})}
    assert _clave_caso(fila("a", 1)) != _clave_caso(fila("b", 2))


def _investigacion(conn, entrada_norm, estado="ok", proceso_id=None, created_at="2026-10-01T10:00:00+00:00"):
    ensure_schema(conn)
    cur = conn.execute(
        "INSERT INTO investigaciones (entrada, tipo, normalizada, estado, proceso_id, created_at) "
        "VALUES ('x', ?, ?, ?, ?, ?)",
        (entrada_norm.get("tipo"), json.dumps(entrada_norm, ensure_ascii=False), estado, proceso_id, created_at))
    conn.commit()
    return cur.lastrowid


def test_lista_agrupa_reintentos_y_conserva_el_proceso_id_de_un_intento_anterior(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        norm = normalizar_entrada("https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607")
        _investigacion(conn, norm, estado="ok", proceso_id=7, created_at="2026-10-01T10:00:00+00:00")
        # Reintento del MISMO aviso (con ?busqueda=1), más tarde, que dio error (no trae proceso_id).
        norm2 = normalizar_entrada(
            "https://www.boletinoficial.gob.ar/detalleAviso/primera/245330/20210607?busqueda=1")
        _investigacion(conn, norm2, estado="error", proceso_id=None, created_at="2026-10-02T10:00:00+00:00")
        # Un caso totalmente distinto, sin relación.
        _investigacion(conn, {"tipo": "texto", "texto": "otra cosa"}, estado="ok", proceso_id=9)

        out = lista(conn, limit=10)
    assert len(out) == 2   # los dos avisos del BO quedan en UN solo grupo
    bo = next(g for g in out if g["tipo"] == "bo")
    assert bo["n_intentos"] == 2 and bo["estado"] == "error"   # el más reciente manda el estado
    assert bo["proceso_id"] == 7   # pero el proceso_id de un intento anterior no se pierde


# ── "Procesado; sin comparación" con motivos agrupados (MEJORAS #49.5, item 1c) ─────────────

def _proceso_con_renglones(conn, external_id="seed/1"):
    db.upsert(conn, "procesos", {"fuente": "bo_primera", "external_id": external_id,
                                 "created_at": db.now_iso(), "updated_at": db.now_iso()},
             ("fuente", "external_id"))
    return conn.execute("SELECT id FROM procesos WHERE fuente='bo_primera' AND external_id=?",
                        (external_id,)).fetchone()[0]


def test_resumen_comparacion_cero_comparables_agrupa_motivos_y_sugiere_proximo_paso(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglones(conn)
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key) VALUES (?, 'a', 'k1')", (pid,))
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key) VALUES (?, 'b', 'k2')", (pid,))
        r1, r2 = [r[0] for r in conn.execute("SELECT id FROM renglones WHERE proceso_id=? ORDER BY id", (pid,))]
        db.upsert(conn, "comparaciones", {"renglon_id": r1, "semaforo": "gris", "motivo_codigo": "sin_datos"},
                 ("renglon_id",))
        db.upsert(conn, "comparaciones", {"renglon_id": r2, "semaforo": "gris", "motivo_codigo": "fuera_alcance"},
                 ("renglon_id",))
        conn.commit()

        estado, msg = _resumen_comparacion(conn, pid, n_renglones=2, n_productos=0)
    assert estado == "sin_comparacion"
    assert msg.startswith("Procesado; sin comparación")
    assert "sin precio de mercado en esa fecha" in msg and "sin producto del catálogo" in msg
    assert "/revision/" in msg and "config/productos.yml" in msg


def test_resumen_comparacion_con_al_menos_un_comparable_es_listo(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglones(conn)
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key) VALUES (?, 'a', 'k1')", (pid,))
        (r1,) = conn.execute("SELECT id FROM renglones WHERE proceso_id=?", (pid,)).fetchone()
        db.upsert(conn, "comparaciones", {"renglon_id": r1, "semaforo": "verde", "motivo_codigo": "ok"},
                 ("renglon_id",))
        conn.commit()

        estado, msg = _resumen_comparacion(conn, pid, n_renglones=1, n_productos=1)
    assert estado == "ok" and msg == "Listo: 1 renglón(es), 1 producto(s) comparable(s)"


# ── Advertencia de referencia extrapolada por IPC (feedback del owner 2026-10-12) ───────────

def test_nota_extrapolado_none_si_la_observacion_esta_dentro_de_la_ventana(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglones(conn)
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key) VALUES (?, 'a', 'k1')", (pid,))
        (r1,) = conn.execute("SELECT id FROM renglones WHERE proceso_id=?", (pid,)).fetchone()
        db.upsert(conn, "comparaciones", {"renglon_id": r1, "gap_dias": 3, "fecha_compra": "2026-10-01",
                                          "evidencia_json": "[]"}, ("renglon_id",))
        conn.commit()
        assert _nota_extrapolado(conn, pid) is None


def test_nota_extrapolado_marca_la_referencia_de_hoy_llevada_por_ipc(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso_con_renglones(conn)
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key) VALUES (?, 'a', 'k1')", (pid,))
        (r1,) = conn.execute("SELECT id FROM renglones WHERE proceso_id=?", (pid,)).fetchone()
        evidencia = [{"fuente": "maxiconsumo", "fecha": "2026-10-10", "factor_ipc": 1.234, "precio_ub": 100.0}]
        db.upsert(conn, "comparaciones", {"renglon_id": r1, "gap_dias": 200, "fecha_compra": "2021-06-07",
                                          "evidencia_json": json.dumps(evidencia)}, ("renglon_id",))
        conn.commit()
        nota = _nota_extrapolado(conn, pid)
    assert nota is not None
    assert "2026-10-10" in nota and "2021-06-07" in nota and "1.234" in nota and "ADVERTENCIA" in nota
