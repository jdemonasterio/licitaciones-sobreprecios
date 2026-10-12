"""Tests del módulo de revisión humana (MEJORAS #34): correcciones manuales, cola de revisión,
validación de referencias de mercado (P16), validación rápida por renglón, importar-juez y el
proxy de documentos con allowlist de host."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from sobreprecios import db, revision_humana as rh
from sobreprecios.pipeline.compare import Obs

FIXTURES = Path(__file__).parent / "fixtures"


def _renglon(id=1, producto_id="yerba_mate", contenido_base=1.0, unidad_base="kg", precio_unitario=5000.0,
            cantidad=10.0, fecha="2021-06-07", marca=None, descripcion="Yerba mate 1 kg", alertas=None):
    return {"id": id, "producto_id": producto_id, "contenido_base": contenido_base, "unidad_base": unidad_base,
            "precio_unitario": precio_unitario, "cantidad": cantidad, "fecha": fecha, "marca": marca,
            "descripcion": descripcion, "alertas": alertas}


def _crear_renglon_fila(conn, renglon_id, proceso_id=1):
    """Las tablas de corrección/validación tienen FK a renglones(id): hace falta una fila real
    (aunque sea minima) antes de poder guardar una corrección o un juicio sobre ella."""
    db.upsert(conn, "procesos", {"fuente": "bo_primera", "external_id": f"p{proceso_id}",
                                 "created_at": db.now_iso(), "updated_at": db.now_iso()},
             ("fuente", "external_id"))
    conn.execute("INSERT OR IGNORE INTO renglones (id, proceso_id, descripcion, external_key) "
                "VALUES (?, ?, 'yerba', ?)", (renglon_id, proceso_id, f"k{renglon_id}"))
    conn.commit()


# ── aplicar_correcciones ─────────────────────────────────────────────────────

def test_aplicar_correcciones_sin_filas_devuelve_copia_sin_cambios(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        out, notas = rh.aplicar_correcciones(conn, _renglon())
    assert notas == []
    assert out["producto_id"] == "yerba_mate" and out["precio_unitario"] == 5000.0
    assert out["_variante_manual"] is None and out["_campos_corregidos"] == set()


def test_aplicar_correcciones_producto_y_precio_recalcula_precio_unidad_base(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_correccion(conn, 1, "producto_id", "arroz_blanco", "en realidad es arroz", "mateo")
        rh.guardar_correccion(conn, 1, "precio_unitario", "6000", "precio mal leido", "mateo")
        out, notas = rh.aplicar_correcciones(conn, _renglon())
    assert out["producto_id"] == "arroz_blanco"
    assert out["precio_unitario"] == 6000.0
    # contenido_base no cambio (1.0): precio_unidad_base se recalcula con el precio corregido.
    assert out["precio_unidad_base"] == pytest.approx(6000.0)
    assert "producto" in notas and "precio unitario" in notas
    assert out["_campos_corregidos"] == {"producto_id", "precio_unitario"}


def test_aplicar_correcciones_envase_recalcula_precio_unidad_base_sin_tocar_precio(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_correccion(conn, 1, "contenido_base", "0.5", "era medio kilo, no un kilo", "mateo")
        out, notas = rh.aplicar_correcciones(conn, _renglon(precio_unitario=2500.0))
    assert out["contenido_base"] == 0.5
    assert out["precio_unidad_base"] == pytest.approx(2500.0 / 0.5)
    assert "envase" in notas


def test_aplicar_correcciones_acepta_coma_decimal(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_correccion(conn, 1, "cantidad", "12,5", "", "mateo")
        out, _ = rh.aplicar_correcciones(conn, _renglon())
    assert out["cantidad"] == pytest.approx(12.5)


def test_aplicar_correcciones_variante_no_se_no_fuerza_nada_pero_deja_constancia(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_correccion(conn, 1, "variante", rh.VARIANTE_NO_SE, "no se puede saber", "mateo")
        out, notas = rh.aplicar_correcciones(conn, _renglon())
    assert out["_variante_manual"] is None   # no fuerza ninguna variedad real
    assert "variedad" in notas               # pero deja constancia de que un humano miro


def test_aplicar_correcciones_variante_real_fuerza_verificado_humano(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_correccion(conn, 1, "variante", "largos", "se confirmo con el pliego", "mateo")
        out, _ = rh.aplicar_correcciones(conn, _renglon())
    assert out["_variante_manual"] == "largos"


def test_aplicar_correcciones_fecha_y_marca(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_correccion(conn, 1, "fecha_comparacion", "2021-05-01", "", "mateo")
        rh.guardar_correccion(conn, 1, "marca", "Playadito", "", "mateo")
        out, notas = rh.aplicar_correcciones(conn, _renglon())
    assert out["fecha"] == "2021-05-01" and out["marca"] == "Playadito"
    assert "fecha de comparación" in notas and "marca" in notas


def test_deshacer_correccion_la_borra_y_deja_de_aplicarse(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        cid = rh.guardar_correccion(conn, 1, "marca", "Playadito", "", "mateo")
        assert rh.deshacer_correccion(conn, cid) is True
        out, notas = rh.aplicar_correcciones(conn, _renglon())
    assert out["marca"] is None and notas == []
    with db.session(tmp_path / "t.db") as conn:
        assert rh.deshacer_correccion(conn, 9999) is False   # ya no existe / nunca existio


def test_correccion_mas_reciente_gana(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_correccion(conn, 1, "marca", "Primera", "", "mateo")
        rh.guardar_correccion(conn, 1, "marca", "Segunda", "", "mateo")
        out, _ = rh.aplicar_correcciones(conn, _renglon())
    assert out["marca"] == "Segunda"


# ── Cola de revisión ─────────────────────────────────────────────────────────

def _seed_comparacion(conn, renglon_id, proceso_id, semaforo, motivo_codigo, producto_id="yerba_mate"):
    db.upsert(conn, "procesos", {"fuente": "bo_primera", "external_id": f"p{proceso_id}",
                                 "organismo": "ORGANISMO X", "fecha_publicacion": "2021-06-07",
                                 "created_at": db.now_iso(), "updated_at": db.now_iso()},
             ("fuente", "external_id"))
    conn.execute("INSERT INTO renglones (id, proceso_id, descripcion, producto_id, external_key) "
                "VALUES (?, ?, 'yerba', ?, ?)", (renglon_id, proceso_id, producto_id, f"k{renglon_id}"))
    conn.execute("INSERT INTO comparaciones (renglon_id, semaforo, motivo_codigo, evidencia_json) "
                "VALUES (?, ?, ?, '[]')", (renglon_id, semaforo, motivo_codigo))


def test_cola_detecta_grises_relevantes_y_rojos_pero_no_verdes(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _seed_comparacion(conn, 1, 1, "gris", "sin_datos")
        _seed_comparacion(conn, 2, 1, "gris", "sin_precio")   # no esta en la lista de motivos a revisar
        _seed_comparacion(conn, 3, 1, "rojo", "ok")
        _seed_comparacion(conn, 4, 1, "verde", "ok")
        datos = rh.cola(conn)
    ids = {it["renglon_id"] for it in datos["items"]}
    assert ids == {1, 3}
    origenes = {it["renglon_id"]: it["origen"] for it in datos["items"]}
    assert origenes[1] == "gris" and origenes[3] == "rojo"
    assert datos["counts"]["gris"] == 1 and datos["counts"]["rojo"] == 1


def test_cola_no_pisa_una_decision_humana_ya_tomada(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _seed_comparacion(conn, 1, 1, "gris", "sin_datos")
        rh.cola(conn)   # primera sincronizacion: crea la fila 'pendiente'
        conn.execute("UPDATE revisiones_caso SET estado='descartado' WHERE renglon_id=1")
        conn.commit()
        datos = rh.cola(conn)   # el caso sigue siendo gris: no debe volver a 'pendiente'
    item = next(it for it in datos["items"] if it["renglon_id"] == 1)
    assert item["estado"] == "descartado"


def test_cola_filtra_por_estado_y_producto(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _seed_comparacion(conn, 1, 1, "rojo", "ok", producto_id="yerba_mate")
        _seed_comparacion(conn, 2, 1, "rojo", "ok", producto_id="arroz_blanco")
        solo_yerba = rh.cola(conn, {"producto": "yerba_mate"})
        assert {it["renglon_id"] for it in solo_yerba["items"]} == {1}
        pendientes = rh.cola(conn, {"estado": "pendiente"})
        assert {it["renglon_id"] for it in pendientes["items"]} == {1, 2}


# ── Validación de referencias de mercado (P16) ──────────────────────────────

def test_exclusiones_mercado_solo_los_juicios_bloqueantes(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_validacion_mercado(conn, 1, "maxiconsumo", "2021-06-07", "Yerba X 1kg",
                                      "no_es_mismo_producto", "", "mateo")
        rh.guardar_validacion_mercado(conn, 1, "sepa", "2021-06-07", "Yerba Y 1kg",
                                      "precio_o_tamano_mal_leido", "", "mateo")
        rh.guardar_validacion_mercado(conn, 1, "sepa", "2021-06-08", "Yerba Z 1kg",
                                      "equivalente", "", "mateo")
        excl = rh.exclusiones_mercado(conn, 1)
    assert excl == {("maxiconsumo", "2021-06-07", "Yerba X 1kg"), ("sepa", "2021-06-07", "Yerba Y 1kg")}


def test_aplicar_exclusiones_mercado_ignora_el_sufijo_de_ajuste_ipc(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_validacion_mercado(conn, 1, "maxiconsumo", "2021-04-01", "Yerba X 1kg",
                                      "no_es_mismo_producto", "", "mateo")
        obs = [Obs("maxiconsumo", "mayorista", "2021-04-01", 500.0, 1.0,
                   "Yerba X 1kg [llevado al mes de la compra por IPC x1.030]"),
               Obs("sepa", "mayorista", "2021-04-01", 510.0, 1.0, "Yerba Z 1kg")]
        out = rh.aplicar_exclusiones_mercado(conn, 1, obs)
    assert [o.fuente for o in out] == ["sepa"]


def test_aplicar_exclusiones_mercado_sin_validaciones_devuelve_la_misma_lista(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        obs = [Obs("sepa", "mayorista", "2021-04-01", 510.0, 1.0, "Yerba Z 1kg")]
        out = rh.aplicar_exclusiones_mercado(conn, 1, obs)
    assert out is obs


def test_quitar_validacion_mercado_vuelve_a_incluir(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        rh.guardar_validacion_mercado(conn, 1, "maxiconsumo", "2021-04-01", "Yerba X 1kg",
                                      "no_es_mismo_producto", "", "mateo")
        rh.quitar_validacion_mercado(conn, 1, "maxiconsumo", "2021-04-01", "Yerba X 1kg")
        excl = rh.exclusiones_mercado(conn, 1)
    assert excl == set()


def test_guardar_validacion_mercado_rechaza_juicio_desconocido(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        with pytest.raises(ValueError):
            rh.guardar_validacion_mercado(conn, 1, "sepa", "2021-04-01", "Yerba Z 1kg", "mal_valor", "", "mateo")


# ── Validación rápida por renglón (in-line en /licitacion/<id>) ─────────────

def test_validaciones_renglones_resumen_validado_vs_objeciones_vs_parcial(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        _crear_renglon_fila(conn, 1)
        _crear_renglon_fila(conn, 2)
        _crear_renglon_fila(conn, 3)
        for parte in rh.PARTES_RENGLON:
            rh.guardar_validacion_renglon(conn, 1, parte, "ok", "", "Mateo")
        rh.guardar_validacion_renglon(conn, 2, "producto", "no", "mal asignado", "Mateo")
        rh.guardar_validacion_renglon(conn, 3, "extraccion", "ok", "", "Mateo")
        out = rh.validaciones_renglones(conn, [1, 2, 3])
    assert out[1]["resumen"] == "validado por Mateo"
    assert out[2]["resumen"] == "con objeciones"
    assert out[3]["resumen"] == "revisión parcial"


def test_validaciones_renglones_vacio_sin_ids(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        assert rh.validaciones_renglones(conn, []) == {}


# ── Importar corridas del juez externo ───────────────────────────────────────

def test_importar_corrida_juez_parsea_el_tsv_fixture(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        msg = rh.importar_corrida_juez(conn, FIXTURES / "juez_sample")
        corridas = rh.corridas_juez(conn)
    assert "3 veredictos importados" in msg
    assert len(corridas) == 1
    c = corridas[0]
    assert c["modelo"] == "gpt-6-astra" and c["modo"] == "muestra" and c["n_items"] == 3
    assert c["usd"] == pytest.approx(0.42)
    conteos = {(x["capa"], x["veredicto"]): x["n"] for x in c["conteos"]}
    assert conteos[("comparacion", "error")] == 1 and conteos[("comparacion", "ok")] == 1


def test_importar_corrida_juez_reimportar_reemplaza_sin_duplicar(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        rh.importar_corrida_juez(conn, FIXTURES / "juez_sample")
        rh.importar_corrida_juez(conn, FIXTURES / "juez_sample")
        corridas = rh.corridas_juez(conn)
        items = rh.items_corrida_juez(conn, corridas[0]["id"])
    assert len(corridas) == 1
    assert len(items) == 3


def test_importar_corrida_juez_carpeta_inexistente(tmp_path):
    with db.session(tmp_path / "t.db") as conn:
        with pytest.raises(FileNotFoundError):
            rh.importar_corrida_juez(conn, tmp_path / "no_existe_20261212-0000")


# ── Proxy de documentos (allowlist de host) ─────────────────────────────────

def test_obtener_documento_host_no_permitido_nunca_hace_red():
    class _NuncaLlamar:
        def get(self, *a, **k):
            raise AssertionError("no debia llamar a la red con un host prohibido")

    with pytest.raises(ValueError, match="no está autorizado"):
        rh.obtener_documento("https://evil.example.com/algo.pdf", client=_NuncaLlamar())


def test_obtener_documento_http_no_https_es_rechazado():
    with pytest.raises(ValueError):
        rh.obtener_documento("http://www.boletinoficial.gob.ar/algo.pdf")


def test_obtener_documento_redirect_a_host_no_permitido_se_rechaza(monkeypatch, tmp_path):
    monkeypatch.setenv("LIC_CACHE_DIR", str(tmp_path / "cache"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.boletinoficial.gob.ar":
            return httpx.Response(302, headers={"location": "https://evil.example.com/robado.pdf"})
        raise AssertionError("no debia seguir el redirect a un host prohibido")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(ValueError, match="no autorizado"):
        rh.obtener_documento("https://www.boletinoficial.gob.ar/detalleAviso/1", client=client)


def test_obtener_documento_descarga_y_cachea(monkeypatch, tmp_path):
    monkeypatch.setenv("LIC_CACHE_DIR", str(tmp_path / "cache"))
    llamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        llamadas["n"] += 1
        return httpx.Response(200, content=b"%PDF-contenido-de-prueba",
                              headers={"content-type": "application/pdf"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    contenido, ctype = rh.obtener_documento("https://comprar.gob.ar/pliego/1", client=client)
    assert contenido == b"%PDF-contenido-de-prueba" and ctype == "application/pdf"
    assert llamadas["n"] == 1
    # Segunda vez: sale del cache de disco, no vuelve a pegarle al cliente (ni se pasa cliente).
    contenido2, ctype2 = rh.obtener_documento("https://comprar.gob.ar/pliego/1")
    assert contenido2 == contenido and ctype2 == ctype


def test_obtener_documento_demasiado_pesado_se_rechaza(monkeypatch, tmp_path):
    monkeypatch.setenv("LIC_CACHE_DIR", str(tmp_path / "cache"))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (rh.DOC_MAX_BYTES + 1))

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(ValueError, match="30 MB"):
        rh.obtener_documento("https://comprar.gob.ar/pliego/pesado", client=client)
