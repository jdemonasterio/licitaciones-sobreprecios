"""Tests de `web/queries.py`: orden en pesos constantes (pedido del owner, 2026-10-12) y
"qué se importó" (MEJORAS #49.5, item 1a)."""
from __future__ import annotations

from sobreprecios import db
from sobreprecios.web import queries


def _ipc(conn, fecha, valor):
    # indices no tiene columna "id" (PK compuesta serie+fecha): db.upsert asume "id" por defecto.
    conn.execute("INSERT INTO indices (serie, fecha, valor) VALUES ('ipc_general', ?, ?) "
                "ON CONFLICT (serie, fecha) DO UPDATE SET valor=excluded.valor", (fecha, valor))
    conn.commit()


def _proceso(conn, external_id, fecha, exceso_nominal, monto_nominal, semaforo="rojo", organismo=None):
    db.upsert(conn, "procesos", {
        "fuente": "bo_primera", "external_id": external_id, "organismo": organismo or f"ORGANISMO {external_id}",
        "fecha_adjudicacion": fecha, "fecha_publicacion": fecha, "monto_total": monto_nominal,
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }, ("fuente", "external_id"))
    pid = conn.execute("SELECT id FROM procesos WHERE fuente='bo_primera' AND external_id=?",
                       (external_id,)).fetchone()[0]
    conn.execute("INSERT INTO renglones (proceso_id, descripcion, monto_total, external_key) "
                "VALUES (?, 'bien', ?, ?)", (pid, monto_nominal, f"k:{external_id}"))
    (rid,) = conn.execute("SELECT id FROM renglones WHERE proceso_id=?", (pid,)).fetchone()
    db.upsert(conn, "comparaciones", {"renglon_id": rid, "semaforo": semaforo, "exceso_ars": exceso_nominal,
                                      "sobreprecio_ajustado_pct": 15.0, "motivo_codigo": "ok"}, ("renglon_id",))
    conn.commit()
    return pid


def test_list_procesos_orden_exceso_usa_pesos_constantes_no_nominales(tmp_path):
    """Un exceso nominal CHICO de un año viejo (con mucha inflación hasta el mes base) puede
    valer más en pesos reales que un exceso nominal GRANDE de un año reciente: el orden
    "exceso estimado" tiene que reflejar eso, no el número nominal (pedido del owner), aunque
    la pantalla siga mostrando montos nominales por defecto."""
    with db.session(tmp_path / "t.db") as conn:
        for fecha, valor in [("2019-01-01", 10.0), ("2025-01-01", 1000.0)]:   # 100x de inflación
            _ipc(conn, fecha, valor)
        pid_viejo = _proceso(conn, "viejo", "2019-01-15", exceso_nominal=100.0, monto_nominal=1000.0)
        pid_nuevo = _proceso(conn, "nuevo", "2025-01-15", exceso_nominal=150.0, monto_nominal=1500.0)

        filas = queries.list_procesos(conn, {}, orden="exceso", moneda="nominal")
        rows = {p["id"]: p for p in filas}

    # La columna MOSTRADA respeta la moneda elegida (nominal: 100 y 150 tal cual).
    assert rows[pid_viejo]["exceso"] == 100.0 and rows[pid_nuevo]["exceso"] == 150.0
    # $100 de 2019 equivalen a $10.000 de 2025 (100x de IPC): en pesos reales "viejo" es mucho
    # peor que "nuevo" aunque su número nominal sea menor.
    assert rows[pid_viejo]["exceso_real"] > rows[pid_nuevo]["exceso_real"]
    # Por eso el orden por defecto (pesos reales) pone "viejo" primero, al revés que el nominal.
    assert [p["id"] for p in filas] == [pid_viejo, pid_nuevo]


def test_agrupado_orden_tambien_usa_pesos_constantes(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        for fecha, valor in [("2019-01-01", 10.0), ("2025-01-01", 1000.0)]:
            _ipc(conn, fecha, valor)
        _proceso(conn, "viejo", "2019-01-15", exceso_nominal=100.0, monto_nominal=1000.0)
        _proceso(conn, "nuevo", "2025-01-15", exceso_nominal=150.0, monto_nominal=1500.0)
        filas = queries.agrupado(conn, {}, "organismo")
    claves = [f["clave"] for f in filas]
    assert claves[0] == "ORGANISMO viejo"   # 2019 pesa mucho mas en pesos reales


def test_agrupado_trae_cobertura_publicables_y_convertidos(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso(conn, "a", "2025-01-15", exceso_nominal=100.0, monto_nominal=1000.0, semaforo="verde")
        conn.execute("UPDATE procesos SET estado_revision='publicable' WHERE id=?", (pid,))
        conn.commit()
        filas = queries.agrupado(conn, {}, "organismo")
    assert filas[0]["renglones"] == 1 and filas[0]["comparados"] == 1 and filas[0]["publicables"] == 1


# ── organismo_canonico expuesto a las tablas interactivas (MEJORAS #53) ─────

def test_list_procesos_expone_organismo_canonico_para_el_filtro_de_columna(tmp_path):
    """La columna "Organismo" de la tabla de licitaciones filtra con `tablas.js` por el atributo
    `data-valor` de la celda; si esa columna no trae `organismo_canonico`, el filtro agrupa por el
    texto CRUDO y "Ministerio de Desarrollo Social" queda partido en variantes (con código SAF,
    sin código, con una subunidad después). `list_procesos()` tiene que traer las dos: la cruda
    (se MUESTRA, con el detalle de la subunidad) y la canónica (para agrupar)."""
    from sobreprecios.normalize import aplicar
    with db.session(tmp_path / "t.db") as conn:
        pid_saf = _proceso(conn, "a", "2025-01-15", 100.0, 1000.0, organismo="311 - Ministerio de Desarrollo Social")
        pid_sub = _proceso(conn, "b", "2025-01-16", 100.0, 1000.0,
                           organismo="MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA")
        aplicar.run(conn)
        filas = {p["id"]: p for p in queries.list_procesos(conn, {})}
    assert filas[pid_saf]["organismo"] == "311 - Ministerio de Desarrollo Social"   # detalle crudo intacto
    assert filas[pid_sub]["organismo"].endswith("GESTIÓN ADMINISTRATIVA")           # idem
    assert filas[pid_saf]["organismo_canonico"] == filas[pid_sub]["organismo_canonico"] == "Ministerio de Desarrollo Social"


def test_peores_renglones_expone_organismo_canonico(tmp_path):
    from sobreprecios.normalize import aplicar
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso(conn, "a", "2025-01-15", 100.0, 1000.0, organismo="MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA")
        aplicar.run(conn)
        filas = queries.peores_renglones(conn, {})
    assert filas and filas[0]["organismo_canonico"] == "Ministerio de Desarrollo Social"
    assert filas[0]["organismo"].endswith("GESTIÓN ADMINISTRATIVA")


# ── tipo_importado (MEJORAS #49.5, item 1a) ─────────────────────────────────

def test_tipo_importado_comprar_dice_recomendado_no_adjudicado():
    out = queries.tipo_importado({"fuente": "bo_primera"}, [{"extraccion_metodo": "comprar"}])
    assert "Dictamen" in out["etiqueta"] and "RECOMENDADO" in out["explicacion"]


def test_tipo_importado_bo_primera_dice_adjudicacion_no_pago():
    out = queries.tipo_importado({"fuente": "bo_primera"}, [{"extraccion_metodo": "regex"}])
    assert "adjudicación" in out["etiqueta"].lower()
    # Nunca afirma el pago en positivo (regla del repo): solo aparece dentro de "no...pagado".
    assert "no prueba que el estado ya haya pagado" in out["explicacion"].lower()


def test_tipo_importado_agrega_nota_de_ordenes_de_compra():
    out = queries.tipo_importado({"fuente": "bo_primera", "ordenes": [{}, {}]}, [{"extraccion_metodo": "regex"}])
    assert any("2 orden" in e for e in out["extra"])
