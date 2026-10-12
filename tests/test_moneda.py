import sqlite3

import pytest

from sobreprecios.normalize.moneda import Moneda, registrar
from sobreprecios.sources.cotizaciones import parse_argentinadatos, parse_bluelytics

IPC = {"2021-01-01": 100.0, "2022-01-01": 150.0, "2023-01-01": 300.0}
USD = {"2021-01-08": 150.0, "2021-01-11": 152.0, "2023-01-02": 340.0}


def mon(**kw):
    return Moneda(ipc=IPC, usd=USD, **kw)


def test_pesos_reales_usa_el_ipc_del_mes_y_base_ultimo_mes():
    m = mon()
    assert m.base_mes == "2023-01-01"
    assert m.a_real(1000, "2021-01-20") == pytest.approx(3000)       # x 300/100
    assert m.a_real(1000, "2023-01-05") == pytest.approx(1000)
    # Mes sin IPC publicado todavía: se usa el último, marcado como aproximado.
    assert m.factor_real("2023-03-10") == (1.0, True)
    # Antes del comienzo de la serie: no se inventa.
    assert m.a_real(1000, "2019-05-01") is None


def test_dolares_usa_la_cotizacion_del_dia_o_la_anterior_hasta_7_dias():
    m = mon()
    assert m.a_usd(1500, "2021-01-08") == pytest.approx(10)
    assert m.tasa_usd("2021-01-10") == 150.0          # domingo: la del viernes
    assert m.tasa_usd("2021-01-30") is None           # más de 7 días sin cotización
    assert m.tasa_usd("2021-01") == pytest.approx(151.0)   # 'YYYY-MM' = promedio del mes
    assert m.a_usd(1000, "2020-12-31") is None


def test_convertir_y_funcion_sql():
    m = mon()
    assert m.convertir(1000, "2021-01-08", "nominal") == 1000
    conn = sqlite3.connect(":memory:")
    registrar(conn, "usd", m)
    assert conn.execute("SELECT conv(3000, '2021-01-08')").fetchone()[0] == pytest.approx(20)
    assert conn.execute("SELECT conv(NULL, '2021-01-08')").fetchone()[0] is None
    assert "dólares" in m.etiqueta("usd") and "enero 2023" in m.etiqueta("real")


def test_parsers_de_cotizaciones():
    payload = [{"casa": "contadoconliqui", "compra": 145.1, "venta": 145.13, "fecha": "2021-01-12"},
               {"casa": "blue", "compra": 153, "venta": 159, "fecha": "2021-01-12"},
               {"casa": "cripto", "venta": 160, "fecha": "2021-01-12"},
               {"casa": "oficial", "venta": 0, "fecha": "2021-01-12"},
               {"casa": "oficial", "venta": 91, "fecha": "2015-01-12"}]
    out = parse_argentinadatos(payload, ["contadoconliqui", "blue", "oficial"], "2016-01-01")
    assert out == {"contadoconliqui": {"2021-01-12": 145.13}, "blue": {"2021-01-12": 159.0}, "oficial": {}}
    bl = parse_bluelytics([{"date": "2021-01-12", "source": "Blue", "value_sell": 159.0},
                           {"date": "2021-01-12", "source": "Oficial", "value_sell": 91.0}], "2016-01-01")
    assert bl == {"oficial": {"2021-01-12": 91.0}, "blue": {"2021-01-12": 159.0}}


def test_cuarentena_aparta_placeholders_y_precios_viejos(tmp_path, monkeypatch):
    from sobreprecios import db
    from sobreprecios.pipeline import cuarentena
    with db.session(tmp_path / "t.db") as conn:
        # Mismo envase (1 kg): la mediana se calcula por producto, mes y tamaño de envase.
        filas = [(f"s{i}", 1000.0 + i, 1.0) for i in range(6)] + [("relleno", 1.0, 0.5), ("viejo", 240.0, 1.0)]
        for i, (fuente, precio, cont) in enumerate(filas):
            db.upsert(conn, "precios_mercado", {"fuente": fuente, "canal": "minorista", "fecha": "2025-07-01",
                      "descripcion": f"arroz {i}", "precio": precio, "contenido_base": cont, "unidad_base": "kg",
                      "precio_unidad_base": precio / cont, "producto_id": "arroz_blanco", "obs_key": str(i)},
                      ("fuente", "fecha", "obs_key"))
        assert cuarentena.run(conn).startswith("2 ")
        marcadas = dict(conn.execute("SELECT fuente, cuarentena FROM precios_mercado WHERE cuarentena IS NOT NULL"))
        assert set(marcadas) == {"relleno", "viejo"} and "relleno" in marcadas["relleno"]



def test_envase_implausible_no_cuenta_como_el_producto(tmp_path):
    from sobreprecios import db
    from sobreprecios.pipeline import match
    with db.session(tmp_path / "t.db") as conn:
        for i, (desc, precio) in enumerate([("Arroz largo fino Gallo 1 kg", 1800.0), ("Arroz Dos Hermanos snack 80 g", 900.0)]):
            db.upsert(conn, "precios_mercado", {"fuente": "s", "canal": "minorista", "fecha": "2025-07-01",
                      "descripcion": desc, "precio": precio, "obs_key": str(i)}, ("fuente", "fecha", "obs_key"))
        match.match_mercado(conn, solo_pendientes=False)
        estado = dict(conn.execute("SELECT descripcion, COALESCE(producto_id, match_metodo) FROM precios_mercado"))
        assert estado["Arroz largo fino Gallo 1 kg"] == "arroz_blanco"
        assert estado["Arroz Dos Hermanos snack 80 g"] in {"envase_no_plausible", "reglas_sin_producto"}
