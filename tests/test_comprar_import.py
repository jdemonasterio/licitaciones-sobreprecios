from pathlib import Path

import pytest

from sobreprecios import db
from sobreprecios.pipeline.comprar_import import brecha_ofertas, ofertas_ganadoras, store_detalle
from sobreprecios.sources import comprar

FIX = Path(__file__).parent / "fixtures" / "comprar"


@pytest.fixture
def detalle():
    d = comprar.parse_detalle_html((FIX / "detalle_80-0014-LPU25.html").read_text(encoding="utf-8"))
    for k in ("_acta_url", "_dictamen_url", "_cuadro_comparativo_target"):
        d.pop(k, None)
    d["ofertas"] = comprar.parse_cuadro_comparativo_html((FIX / "cuadro_comparativo_80-0014-LPU25.html").read_text(encoding="utf-8"))
    d["adjudicaciones"] = comprar.parse_dictamen_html((FIX / "dictamen_80-0014-LPU25.html").read_text(encoding="utf-8"))
    d["ofertas_desestimadas"] = comprar.parse_ofertas_desestimadas_html(
        (FIX / "dictamen_80-0014-LPU25.html").read_text(encoding="utf-8"))
    d["organismo"] = "310 - Ministerio de Salud"
    d["url"] = "https://comprar.gob.ar/PLIEGO/VistaPreviaPliegoCiudadano.aspx?qs=x"
    for acto in d["actos_administrativos"]:
        acto["url"] = d["url"] + "#" + comprar.ACTOS_ANCHOR_ID
    return d


def test_store_detalle_creates_renglones_and_offers(tmp_path, detalle):
    with db.session(tmp_path / "t.db") as conn:
        pid = store_detalle(conn, detalle)
        p = conn.execute("SELECT * FROM procesos WHERE id=?", (pid,)).fetchone()
        assert p["numero"] == "80-0014-LPU25" and p["tipo_procedimiento"] == "licitacion_publica"
        assert p["fecha_adjudicacion"] == "2026-03-04"
        r1 = conn.execute("SELECT * FROM renglones WHERE proceso_id=? AND nro_renglon=1", (pid,)).fetchone()
        assert r1["proveedor"].startswith("MICROSULES") and r1["precio_unitario"] == 67400.0
        assert r1["cantidad"] == 480.0 and r1["monto_total"] == 32352000.0
        assert "ZOLEDRONICO" in r1["descripcion"]
        n_ofertas = conn.execute("SELECT COUNT(*) FROM ofertas WHERE proceso_id=?", (pid,)).fetchone()[0]
        assert n_ofertas == len(detalle["ofertas"])
        # La oferta más baja del renglón 1 fue $18.337 (misma marca, desestimada): el ganador cobró 3,7x.
        info = brecha_ofertas(conn, pid, 1, 67400.0)
        assert info["oferta_min"] == 18337.0
        assert info["brecha_oferta_min_pct"] == pytest.approx(267.56, abs=0.01)
        # Pedido de jdemonasterio 2026-10-12: si la más baja fue desestimada, se tiene que
        # poder decir por qué (no "sobreprecio 267%" a secas).
        assert info["oferta_min_desestimada"] is True
        assert "requisitos técnicos" in info["oferta_min_causa"].lower()
        # Marca exigida (jdemonasterio 2026-10-12): el pliego de este renglón dice "TIPO: MONODROGA"
        # (ficha SIByS de medicamentos) — eso NO es una marca exigida, es el falso positivo real que
        # motivó no tratar "tipo X" como marca salvo que diga "o equivalente/similar".
        assert r1["marca_exigida"] is None and r1["marca_equivalente_ok"] is None


def test_store_detalle_guarda_marca_exigida_del_pliego(tmp_path, detalle):
    """`marca_exigida`/`marca_equivalente_ok` salen del texto del PLIEGO (reng), no de la marca
    OFRECIDA por el ganador (que ya se guarda aparte en `marca`)."""
    detalle["renglones"][0]["descripcion"] = "ARROZ BLANCO; MARCA: GALLO O EQUIVALENTE, PRESENTACION: 1 KG"
    with db.session(tmp_path / "t.db") as conn:
        pid = store_detalle(conn, detalle)
        r1 = conn.execute("SELECT * FROM renglones WHERE proceso_id=? AND nro_renglon=1", (pid,)).fetchone()
        assert r1["marca_exigida"] == "GALLO"
        assert bool(r1["marca_equivalente_ok"]) is True


def test_store_detalle_is_idempotent(tmp_path, detalle):
    with db.session(tmp_path / "t.db") as conn:
        a = store_detalle(conn, detalle)
        b = store_detalle(conn, detalle)
        assert a == b
        assert conn.execute("SELECT COUNT(*) FROM renglones").fetchone()[0] == len(detalle["adjudicaciones"])


def test_store_detalle_guarda_actos_administrativos(tmp_path, detalle):
    """Pedido de jdemonasterio 2026-10-12: autorización de pliego/llamado y adjudicación
    quedan en `comprar_actos` con número, fecha y el link (al proceso, con fragmento — no
    hay PDF descargable sin sesión, ver sources/comprar.py)."""
    with db.session(tmp_path / "t.db") as conn:
        pid = store_detalle(conn, detalle)
        actos = conn.execute(
            "SELECT * FROM comprar_actos WHERE proceso_id=? ORDER BY fecha", (pid,)).fetchall()
        assert len(actos) == 3
        adjudicacion = next(a for a in actos if a["tipo"] == "Adjudicación y OC")
        assert adjudicacion["numero"] == "RESOL-2026-346-MS"
        assert adjudicacion["fecha"] == "2026-03-04"
        assert adjudicacion["url"] == detalle["url"] + "#" + comprar.ACTOS_ANCHOR_ID
        assert "GDE" in adjudicacion["descripcion"]
        # Reimportar no duplica (UNIQUE proceso_id+tipo+numero) y refleja cambios de fecha/url.
        store_detalle(conn, detalle)
        assert conn.execute("SELECT COUNT(*) FROM comprar_actos WHERE proceso_id=?", (pid,)).fetchone()[0] == 3


def test_store_detalle_marca_ofertas_desestimadas_con_causa_y_cuenta_proceso(tmp_path, detalle):
    """Las ofertas que el dictamen desestimó quedan `estado='desestimada'` con su causa; las
    demás, `admitida`. El total también se guarda a nivel proceso (pedido explícito de
    jdemonasterio: 'store also a process-level count')."""
    with db.session(tmp_path / "t.db") as conn:
        pid = store_detalle(conn, detalle)
        p = conn.execute("SELECT ofertas_desestimadas_n FROM procesos WHERE id=?", (pid,)).fetchone()
        assert p["ofertas_desestimadas_n"] == len(detalle["ofertas_desestimadas"]) == 21
        alemana = conn.execute(
            "SELECT estado, causa_desestimacion FROM ofertas WHERE proceso_id=? AND nro_renglon=1 "
            "AND proveedor LIKE 'DROGUERIA Y ORTOPEDIA%'", (pid,)).fetchone()
        assert alemana["estado"] == "desestimada" and alemana["causa_desestimacion"]
        ganador = conn.execute(
            "SELECT estado, causa_desestimacion FROM ofertas WHERE proceso_id=? AND nro_renglon=1 "
            "AND proveedor LIKE 'MICROSULES%'", (pid,)).fetchone()
        assert ganador["estado"] == "admitida" and ganador["causa_desestimacion"] is None


def test_store_detalle_guarda_plazo_de_pago_del_pliego(tmp_path, detalle):
    """Cuando el plazo de pago SÍ se pudo leer (caso raro, ver DESIGN_sobreprecios.md), queda
    en `procesos.plazo_pago_dias`/`plazo_pago_texto` para que compare.py lo use."""
    detalle_con_plazo = dict(detalle, plazo_pago_dias=45, plazo_pago_texto="45 dias corridos")
    with db.session(tmp_path / "t.db") as conn:
        pid = store_detalle(conn, detalle_con_plazo)
        p = conn.execute("SELECT plazo_pago_dias, plazo_pago_texto FROM procesos WHERE id=?", (pid,)).fetchone()
        assert p["plazo_pago_dias"] == 45 and p["plazo_pago_texto"] == "45 dias corridos"


def test_store_detalle_sin_plazo_de_pago_queda_sin_dato(tmp_path, detalle):
    """El caso real y mayoritario: COMPR.AR no trae el dato -> columna en NULL, nunca un
    número inventado."""
    with db.session(tmp_path / "t.db") as conn:
        pid = store_detalle(conn, detalle)
        p = conn.execute("SELECT plazo_pago_dias FROM procesos WHERE id=?", (pid,)).fetchone()
        assert p["plazo_pago_dias"] is None


def test_store_detalle_enriches_existing_bo_process(tmp_path, detalle):
    with db.session(tmp_path / "t.db") as conn:
        pid = db.upsert(conn, "procesos", {"fuente": "bo_primera", "external_id": "primera/339058/20260304",
                                           "numero": "80-0014-LPU25", "organismo": "MINISTERIO DE SALUD"},
                        ("fuente", "external_id"))
        conn.execute("INSERT INTO renglones (proceso_id, nro_renglon, descripcion, external_key) VALUES (?, 1, "
                     "'renglón 1 (sin descripción en el BO)', 'bo:x')", (pid,))
        assert store_detalle(conn, detalle, proceso_id=pid) == pid
        assert conn.execute("SELECT organismo FROM procesos WHERE id=?", (pid,)).fetchone()[0] == "MINISTERIO DE SALUD"
        descs = [r[0] for r in conn.execute("SELECT descripcion FROM renglones WHERE proceso_id=?", (pid,))]
        assert not any("sin descripción" in d for d in descs)


def test_brecha_ofertas_ignores_offers_in_another_unit(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        pid = db.upsert(conn, "procesos", {"fuente": "comprar", "external_id": "x"}, ("fuente", "external_id"))
        for i, (prov, precio) in enumerate([("A", 98.0), ("B", 7000.0), ("C", 7800.0)]):
            conn.execute("INSERT INTO ofertas (proceso_id, nro_renglon, proveedor, precio_unitario, oferta_key) "
                         "VALUES (?, 19, ?, ?, ?)", (pid, prov, precio, str(i)))
        info = brecha_ofertas(conn, pid, 19, 7800.0, max_ratio=4.0)
    assert info["oferta_min"] == 7000.0 and info["ofertas_implausibles"] == 1
    assert info["brecha_oferta_min_pct"] == pytest.approx(11.43, abs=0.01)


import html as _html
import re


def test_dictamen_paginado_se_detecta_y_la_pagina_2_trae_a_he_vras():
    # Regresión 2026-10-11: leer solo la página 1 del dictamen hizo creer que un adjudicatario
    # tenía orden de compra "sin recomendación" (falso positivo con nombre propio).
    p1 = (FIX / "dictamen_46-0004-LPU25_p1.html").read_text(encoding="utf-8")
    p2 = (FIX / "dictamen_46-0004-LPU25_p2.html").read_text(encoding="utf-8")
    assert re.search(r"gvItems','Page\$2'", _html.unescape(p1))
    rows2 = comprar.parse_dictamen_html(p2)
    he_vras = [r for r in rows2 if r["proveedor"].startswith("HE VRAS")]
    assert {r["renglon"] for r in he_vras} == {58, 59}
    assert all(r["precio_unitario"] == 380000.0 for r in he_vras)


def test_ordenes_de_compra_se_parsean_con_tipo_y_monto():
    ocs = comprar.parse_ordenes_compra_html((FIX / "oca_46-0004-LPU25.html").read_text(encoding="utf-8"))
    he = [o for o in ocs if o["proveedor"].startswith("HE VRAS")]
    assert {(o["tipo"], o["monto"]) for o in he} == {("Original", 1292000000.0), ("Prórroga", 1292000000.0)}
    assert all(o["cuit"] == "30-71831219-8" and o["fecha"][:4] == "2025" for o in he)


def test_alcance_da_el_saf_del_proceso():
    html = (FIX / "detalle_80-0014-LPU25.html").read_text(encoding="utf-8")
    alc = comprar.parse_alcance_html(html)
    assert alc["saf"].startswith("310") or alc["saf"] == ""   # el fixture recortado puede no traer Alcance


def test_reimportar_conserva_ids_y_guarda_fragmento_fuente(tmp_path, detalle):
    with db.session(tmp_path / "t.db") as conn:
        pid = store_detalle(conn, detalle)
        antes = {r["external_key"]: r["id"] for r in conn.execute("SELECT id, external_key FROM renglones")}
        conn.execute("UPDATE renglones SET producto_id='x', match_metodo='reglas'")
        store_detalle(conn, detalle)
        despues = {r["external_key"]: r["id"] for r in conn.execute("SELECT id, external_key FROM renglones")}
        assert antes == despues
        r1 = conn.execute("SELECT * FROM renglones WHERE proceso_id=? AND nro_renglon=1", (pid,)).fetchone()
        assert r1["match_metodo"] == "reglas"            # el macheo no se pierde si el texto no cambió
        assert '"dictamen"' in r1["fuente_fragmento"] and "67400" in r1["fuente_fragmento"]


def test_ganadora_es_la_alternativa_con_el_precio_del_dictamen():
    adj = [{"renglon": 1, "proveedor": "ACME SA", "precio_unitario": 100.0}]
    ofs = [{"renglon": 1, "proveedor": "ACME S.A.", "precio_unitario": 120.0},
           {"renglon": 1, "proveedor": "ACME SA", "precio_unitario": 100.0},
           {"renglon": 1, "proveedor": "OTRA SRL", "precio_unitario": 90.0}]
    assert ofertas_ganadoras(adj, ofs) == {1}
    # Sin coincidencia de precio: todas las del proveedor (no se inventa cuál ganó).
    assert ofertas_ganadoras([dict(adj[0], precio_unitario=105.0)], ofs) == {0, 1}


def test_alternativas_empatadas_elige_una_ganadora():
    """jdemonasterio 2026-10-12: "si hay empate, elegís una; si está adjudicado, está adjudicado" —
    revierte la cautela del LLM prime de dejar ambas en `pendientes` (NULL)."""
    from sobreprecios.pipeline.comprar_import import ofertas_ganadoras_y_pendientes
    adj = [{"renglon": 1, "proveedor": "ACME SA", "precio_unitario": 100.0}]
    ofs = [{"renglon": 1, "proveedor": "ACME SA", "precio_unitario": 100.0},
           {"renglon": 1, "proveedor": "ACME SA", "precio_unitario": 100.2}]
    assert ofertas_ganadoras_y_pendientes(adj, ofs) == ({0}, set())


# ── Estado documental (MEJORAS #29, jdemonasterio 2026-10-12) ──────────────────────────────────

def _proceso(conn, pid, fuente, **extra):
    db.upsert(conn, "procesos", {"fuente": fuente, "external_id": f"p{pid}",
                                 "created_at": db.now_iso(), "updated_at": db.now_iso(), **extra},
             ("fuente", "external_id"))
    return conn.execute("SELECT id FROM procesos WHERE fuente=? AND external_id=?",
                        (fuente, f"p{pid}")).fetchone()[0]


def test_actualizar_estado_documental_dictamen_adjudicado_contratado_desconocido(tmp_path):
    from sobreprecios.pipeline.comprar_import import actualizar_estado_documental
    with db.session(tmp_path / "t.db") as conn:
        p_comprar = _proceso(conn, 1, "comprar")
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key, extraccion_metodo) "
                    "VALUES (?, 'x', 'k1', 'comprar')", (p_comprar,))
        p_bo = _proceso(conn, 2, "bo_primera")
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key, extraccion_metodo) "
                    "VALUES (?, 'x', 'k2', 'regex')", (p_bo,))
        p_legacy = _proceso(conn, 3, "bo_primera")
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key, extraccion_metodo) "
                    "VALUES (?, 'x', 'k3', 'legacy_quijotes')", (p_legacy,))
        p_oc = _proceso(conn, 4, "comprar")
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key, extraccion_metodo, "
                    "proveedor, proveedor_cuit) VALUES (?, 'x', 'k4', 'comprar', 'ACME SA', '30-1-1')", (p_oc,))
        conn.execute("INSERT INTO ordenes_compra (proceso_id, numero_oc, proveedor, cuit) "
                    "VALUES (?, 'OC1', 'ACME SA', '30-1-1')", (p_oc,))
        conn.commit()
        actualizar_estado_documental(conn)
        rows = {r["proceso_id"]: r["estado_documental"] for r in
               conn.execute("SELECT proceso_id, estado_documental FROM renglones")}
        procesos = {r["id"]: r["estado_documental"] for r in conn.execute("SELECT id, estado_documental FROM procesos")}
    assert rows[p_comprar] == "dictamen"
    assert rows[p_bo] == "adjudicado"
    assert rows[p_legacy] == "desconocido"   # dataset legacy sin metadata de origen
    assert rows[p_oc] == "contratado"        # hay OC: manda sobre "dictamen"
    assert procesos[p_comprar] == "dictamen" and procesos[p_bo] == "adjudicado"
    assert procesos[p_legacy] == "desconocido" and procesos[p_oc] == "contratado"


def test_actualizar_estado_documental_respeta_el_filtro_de_proceso_ids(tmp_path):
    from sobreprecios.pipeline.comprar_import import actualizar_estado_documental
    with db.session(tmp_path / "t.db") as conn:
        p1 = _proceso(conn, 1, "comprar")
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key, extraccion_metodo) "
                    "VALUES (?, 'x', 'k1', 'comprar')", (p1,))
        p2 = _proceso(conn, 2, "comprar")
        conn.execute("INSERT INTO renglones (proceso_id, descripcion, external_key, extraccion_metodo) "
                    "VALUES (?, 'x', 'k2', 'comprar')", (p2,))
        conn.commit()
        actualizar_estado_documental(conn, [p1])
        r1 = conn.execute("SELECT estado_documental FROM renglones WHERE proceso_id=?", (p1,)).fetchone()[0]
        r2 = conn.execute("SELECT estado_documental FROM renglones WHERE proceso_id=?", (p2,)).fetchone()[0]
    assert r1 == "dictamen" and r2 is None   # p2 no estaba en el alcance: no se tocó
