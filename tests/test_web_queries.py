"""Tests de sobreprecios/web/queries.resumen_proceso (MEJORAS #49.1): puramente en Python sobre
datos ya cargados por la ruta — sin tocar la base."""
from __future__ import annotations

from sobreprecios.web import queries


def _renglon(semaforo=None, motivo_codigo=None, monto_total=0, exceso_ars=None, motivo=""):
    return {"semaforo": semaforo, "motivo_codigo": motivo_codigo, "monto_total": monto_total,
            "exceso_ars": exceso_ars, "motivo": motivo}


def test_resumen_proceso_cuenta_semaforos_monto_y_cobertura():
    proc = {"monto_total": 1000.0, "estado_revision": "pendiente"}
    renglones = [
        _renglon("rojo", "ok", 400, 100),
        _renglon("amarillo", "ok", 300, 20),
        _renglon("verde", "ok", 200, None),
        _renglon(None, "sin_datos", 100, None),
    ]
    out = queries.resumen_proceso(proc, renglones, {})
    assert out["total"] == 4
    assert out["comparados"] == 3
    assert out["grises"] == 1
    assert (out["rojos"], out["amarillos"], out["verdes"]) == (1, 1, 1)
    assert out["monto_renglones"] == 1000
    assert out["exceso"] == 120
    assert out["cobertura_pct"] == 75.0
    assert out["monto_inconsistente"] is False
    assert any("sin comparación" in b for b in out["bloqueantes"])
    assert any("publicable" in b for b in out["bloqueantes"])


def test_resumen_proceso_publicable_no_agrega_ese_bloqueante():
    proc = {"monto_total": 0, "estado_revision": "publicable"}
    out = queries.resumen_proceso(proc, [], {})
    assert not any("publicable" in b for b in out["bloqueantes"])


def test_resumen_proceso_detecta_monto_inconsistente():
    proc = {"monto_total": 10000.0, "estado_revision": "publicable"}
    renglones = [_renglon("verde", "ok", 100, None)]
    out = queries.resumen_proceso(proc, renglones, {})
    assert out["monto_inconsistente"] is True
    assert any("no coincide" in b for b in out["bloqueantes"])


def test_resumen_proceso_monto_consistente_dentro_de_tolerancia_no_marca():
    proc = {"monto_total": 1000.0, "estado_revision": "publicable"}
    renglones = [_renglon("verde", "ok", 995.0, None)]   # diferencia < 1% y < $1
    out = queries.resumen_proceso(proc, renglones, {})
    assert out["monto_inconsistente"] is False


def test_resumen_proceso_detecta_precio_inferido_y_de_programa():
    proc = {"monto_total": 100.0, "estado_revision": "publicable"}
    renglones = [
        _renglon("amarillo", "ok", 100, 10, motivo="PRECIO INFERIDO: se usa el del 2024-09-01"),
    ]
    out = queries.resumen_proceso(proc, renglones, {})
    assert any("INFERIDO" in b for b in out["bloqueantes"])

    renglones_prog = [_renglon("amarillo", "ok", 100, 10, motivo="referencia: precio de programa precios_cuidados (ed1)")]
    out2 = queries.resumen_proceso(proc, renglones_prog, {})
    assert any("precio de programa" in b for b in out2["bloqueantes"])


def test_resumen_proceso_cuenta_validados_y_con_objeciones():
    proc = {"monto_total": 0, "estado_revision": "publicable"}
    renglones = [_renglon("verde", "ok", 0, None), _renglon("verde", "ok", 0, None)]
    validaciones = {1: {"resumen": "validado por mateo"}, 2: {"resumen": "con objeciones"}}
    out = queries.resumen_proceso(proc, renglones, validaciones)
    assert out["validados"] == 1
    assert out["con_objeciones"] == 1
    assert out["sin_validar"] == 0


def test_resumen_proceso_sin_renglones_cobertura_none():
    out = queries.resumen_proceso({"monto_total": None, "estado_revision": "pendiente"}, [], {})
    assert out["total"] == 0
    assert out["cobertura_pct"] is None
