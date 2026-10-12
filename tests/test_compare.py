import pytest

from sobreprecios.pipeline.compare import (Obs, compare, index_ratio, monthly_inflation, percentile,
                                           same_brand, select_observations)

CFG = {
    "ventana_directa_dias": 15, "gap_maximo_dias": 120, "banda_tamano": 2.5,
    "outlier_bajo": 0.4, "outlier_alto": 2.5, "plazo_pago_meses": 1.0,
    "referencia_principal": "p25_mayorista", "umbral_rojo_pct": 20, "umbral_amarillo_pct": 8,
    "plausibilidad_max_ratio": 4.0,
}
# IPC alimentos ficticio: 3% mensual.
IPC = {"2021-04-01": 100.0, "2021-05-01": 103.0, "2021-06-01": 106.09}


def may(precio, fecha="2021-06-05", fuente="maxiconsumo", contenido=1.0, desc=None):
    # Cada observación sintética es otro producto (serie): el comparador se queda con una por serie.
    return Obs(fuente, "mayorista", fecha, precio, contenido, desc or f"Yerba X{precio:g} 1 kg")


def test_percentile_interpolates():
    assert percentile([100, 200, 300, 400, 500], 0.25) == 200
    assert percentile([100, 200], 0.25) == 125


def test_index_ratio_and_monthly_inflation():
    assert index_ratio(IPC, "2021-04-15", "2021-06-20") == pytest.approx(1.0609)
    assert monthly_inflation(IPC, "2021-06-10") == pytest.approx(0.03)


def test_select_observations_prefers_same_window_then_adjusts_by_cpi():
    obs = [may(100, "2021-04-10")]
    selected, gap = select_observations(obs, "2021-06-08", 1.0, IPC, CFG)
    assert gap == 59
    # Factor del mes exacto: I(jun) / I(abr).
    assert selected[0].precio_ub == pytest.approx(100 * 1.0609)
    assert selected[0].precio_original == 100 and "por IPC" in selected[0].descripcion


def test_select_observations_drops_other_pack_sizes():
    obs = [may(100, contenido=25.0)]   # bolsa de 25 kg vs paquete de 1 kg
    assert select_observations(obs, "2021-06-08", 1.0, IPC, CFG) == ([], None)


def test_compare_flags_red_only_with_confidence_and_financial_adjustment():
    obs = [may(p, fuente=f) for p, f in [(240, "a"), (250, "b"), (260, "a"), (300, "b"), (320, "a")]]
    res = compare(330.0, 1000.0, "2021-06-08", 1.0, obs, IPC, CFG)
    assert res.ref_principal == "p25_mayorista" and res.ref_p25_mayorista == 250
    # Ningún par de familias distintas a ±2% por debajo de 250: no hay mínimo corroborado -> sin rojo.
    assert res.ref_corroborada is None and res.semaforo == "amarillo"
    assert res.ajuste_financiero_pct == pytest.approx(3.0)
    assert res.sobreprecio_ajustado_pct == pytest.approx(28.16, abs=0.05)   # 330 / 1.03 vs 250
    assert res.exceso_ars == pytest.approx((330 / 1.03 - 250) * 1000, rel=1e-4)
    # Con un par corroborado (a y b a ±2%), rojo si aguanta 2 meses de plazo (factor compuesto).
    ok = compare(330.0, 1000.0, "2021-06-08", 1.0, obs + [may(251, fuente="a")], IPC, CFG)
    assert ok.ref_corroborada == 251 and ok.confianza == "alta" and ok.semaforo == "rojo"
    assert ok.sobreprecio_corroborado_pct == pytest.approx((330 / 1.03 ** 2 / 251 - 1) * 100, abs=0.05)


def test_compare_usa_plazo_de_pago_del_pliego_para_el_ajuste_amarillo():
    """P2 (jdemonasterio 2026-10-12): 'perfecto, usá el plazo de pago de la licitación' en vez
    del supuesto genérico de 1 mes. Con 90 días (3 meses) el descuento financiero es mayor que
    el default -> compara contra un precio más bajo -> menos sobreprecio ajustado."""
    obs = [may(p, fuente=f) for p, f in [(240, "a"), (250, "b"), (260, "a"), (300, "b"), (320, "a")]]
    base = compare(330.0, 1000.0, "2021-06-08", 1.0, obs, IPC, CFG)
    con_plazo = compare(330.0, 1000.0, "2021-06-08", 1.0, obs, IPC, CFG, plazo_pago_dias=90)
    assert con_plazo.ajuste_financiero_pct == pytest.approx((1.03 ** 3 - 1) * 100, abs=0.01)
    assert con_plazo.ajuste_financiero_pct > base.ajuste_financiero_pct
    assert con_plazo.sobreprecio_ajustado_pct == pytest.approx(
        (330 / 1.03 ** 3 / 250 - 1) * 100, abs=0.05)
    assert "plazo de pago del pliego: 90 días" in con_plazo.motivo


def test_compare_plazo_de_pago_del_pliego_nunca_acorta_el_escenario_rojo():
    """El plazo real es un PISO para el escenario rojo, nunca lo acorta: si el pliego dice
    15 días (0,5 meses) pero el escenario rojo configurado ya usa 2 meses, el rojo sigue
    evaluándose con 2 meses (no con 0,5) — así no se "ablanda" el rojo con un plazo corto."""
    obs = [may(p, fuente=f) for p, f in [(240, "a"), (250, "b"), (260, "a"), (300, "b"), (320, "a"), (251, "a")]]
    sin_plazo = compare(330.0, 1000.0, "2021-06-08", 1.0, obs, IPC, CFG)
    con_plazo_corto = compare(330.0, 1000.0, "2021-06-08", 1.0, obs, IPC, CFG, plazo_pago_dias=15)
    assert sin_plazo.semaforo == con_plazo_corto.semaforo == "rojo"
    assert con_plazo_corto.sobreprecio_corroborado_pct == sin_plazo.sobreprecio_corroborado_pct
    # Pero el ajuste del escenario amarillo SÍ usa el plazo corto real (0,5 meses, no 1).
    assert con_plazo_corto.ajuste_financiero_pct == pytest.approx((1.03 ** 0.5 - 1) * 100, abs=0.01)


def test_compare_same_brand_beats_statistical_reference():
    obs = [may(240, desc="Yerba Playadito 1 kg"), may(250), may(400, desc="Yerba Playadito 1 kg", fuente="b"),
           may(260), may(270, fuente="b")]
    res = compare(330.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG, marca="PLAYADITO")
    assert res.ref_principal == "misma_marca"
    assert res.ref_misma_marca == 320 and res.ref_n_misma_marca == 2


def test_compare_implausible_ratio_is_grey_not_red():
    obs = [may(p, fuente=f) for p, f in [(240, "a"), (250, "b"), (260, "a")]]
    res = compare(5000.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG)   # bulto cargado como unidad
    assert res.semaforo == "gris" and res.motivo.startswith("revisar_unidad")


def test_compare_without_market_data_is_grey():
    res = compare(300.0, 10.0, "2021-06-08", 1.0, [], IPC, CFG)
    assert res.semaforo == "gris" and "sin observaciones" in res.motivo


def test_same_brand_ignores_generic_words():
    obs = [may(1, desc="Leche en polvo Purísima 800 g"), may(2, desc="Leche en polvo La Serenisima 800 g")]
    assert [o.precio_ub for o in same_brand(obs, "PURÍSIMA")] == [1]
    assert same_brand(obs, "S.A.") == []


from sobreprecios.pipeline.compare import familia_fuente

CFG_MIN = dict(CFG, referencia_principal="min_mayorista", rojo_min_fuentes=2, corroboracion_tolerancia=0.02)


def test_familia_fuente_groups_sepa_mirrors():
    assert familia_fuente("sepa_gondolar") == familia_fuente("precios_claros") == "sepa"
    assert familia_fuente("quijotes_2021:preciosclaros_mayoristas") == "sepa"
    assert familia_fuente("quijotes_2021:maxiconsumo") == familia_fuente("maxiconsumo") == "maxiconsumo"
    # Archivo SEPA: cada empresa es una familia; banderas de la misma empresa no se corroboran.
    assert familia_fuente("sepa_archivo:c9:Vea") == familia_fuente("sepa_archivo:c9:Jumbo") == "sepa:c9"
    assert familia_fuente("sepa_archivo:c12:COTO CICSA") != familia_fuente("sepa_archivo:c15:Supermercados DIA")


def test_min_reference_needs_two_independent_sources_for_red():
    # El mínimo (250) lo confirma una sola familia: aunque la brecha sea > 20%, queda amarillo.
    solo = [may(250, fuente="maxiconsumo"), may(251, fuente="maxiconsumo"), may(300, fuente="golomax"),
            may(320, fuente="golomax")]
    res = compare(330.0, 10.0, "2021-06-08", 1.0, solo, IPC, CFG_MIN)
    assert res.ref_principal == "min_mayorista" and res.fuentes_independientes_min == 1
    assert res.semaforo == "amarillo" and "dos fuentes independientes" in res.motivo
    # Con una segunda familia que confirma el mínimo (±2%), sí es rojo.
    dos = solo + [may(252, fuente="golomax")]
    res2 = compare(330.0, 10.0, "2021-06-08", 1.0, dos, IPC, CFG_MIN)
    assert res2.fuentes_independientes_min == 2 and res2.semaforo == "rojo"


def test_sensitivity_and_reason_codes():
    obs = [may(p, fuente=f) for p, f in [(240, "a"), (250, "b"), (260, "a")]]
    res = compare(300.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG)
    assert set(res.sensibilidad) == {"0m", "1m", "2m", "3m"}
    assert res.sensibilidad["0m"] > res.sensibilidad["3m"]
    assert res.motivo_codigo == "ok"
    assert compare(300.0, 10.0, "2021-06-08", 1.0, [], IPC, CFG).motivo_codigo == "sin_datos"
    assert compare(5000.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG).motivo_codigo == "revisar_unidad"


def test_indec_inferred_cannot_be_principal():
    oficial = [Obs("indec_gba", "oficial", "2021-06-01", 200.0, 0.5, "INDEC")]
    res = compare(300.0, 10.0, "2021-06-08", 1.0, oficial, IPC, CFG, indec_principal=False)
    assert res.ref_indec == 200.0 and res.ref_principal is None and res.semaforo == "gris"


def test_variante_sin_verificar_no_da_rojo():
    vfn = lambda d: "largos" if "spaghetti" in d.lower() else ("cortos" if "tirabuzon" in d.lower() else None)
    obs = [may(240, fuente="a", desc="Fideos tirabuzon 500 g"), may(242, fuente="b", desc="Fideos tirabuzon 500 g"),
           may(300, fuente="a", desc="Fideos tirabuzon")]
    # La licitación pide largos y el mercado solo tiene cortos: gris sin porcentaje (prime P18)...
    gris = dict(CFG_MIN, equivalencia_no_verificada="gris")
    res = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, gris, variante="largos", variante_obs=vfn)
    assert res.semaforo == "gris" and res.motivo_codigo == "sin_referencia_variante"
    assert res.sobreprecio_ajustado_pct is None
    # ...salvo que la brecha contra lo parecido llegue a rojo: amarillo "equivalencia por verificar" (P7).
    eq = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN, variante="largos", variante_obs=vfn)
    assert eq.semaforo == "amarillo" and eq.motivo_codigo == "equivalencia_por_verificar"
    assert "equivalencia no está verificada" in eq.motivo and eq.confianza == "baja"
    barato = compare(250.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN, variante="largos", variante_obs=vfn)
    assert barato.semaforo == "gris" and barato.motivo_codigo == "sin_referencia_variante"
    # Atributo desconocido en la compra: gris (P17); "no exigido" (ficha SIByS sin la variante): todo el mercado.
    assert compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, gris, variante_obs=vfn).motivo_codigo == "atributo_desconocido"
    libre = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN, variante_obs=vfn, estado_atributo="no_exigido")
    assert libre.semaforo == "rojo" and "variedad no exigida" in libre.motivo
    # Si pide cortos y hay cortos: compara contra esos y puede ser rojo.
    res2 = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN, variante="cortos", variante_obs=vfn)
    assert res2.semaforo == "rojo" and "misma variedad (cortos)" in res2.motivo


def test_una_observacion_por_serie_la_mas_cercana():
    # Misma góndola (fuente + descripción): vale el precio del día más cercano, no el mínimo de la ventana.
    obs = [may(859, fecha="2021-06-01", fuente="a", desc="Arroz Bulnez 1 kg"),
           may(999, fecha="2021-06-08", fuente="a", desc="Arroz Bulnez 1 kg"),
           may(990, fecha="2021-06-08", fuente="b", desc="Arroz Gallo 1 kg")]
    res = compare(1100.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN)
    assert res.ref_min_mayorista == 990 and res.ref_n_mayorista == 2


def test_precio_posterior_se_lleva_a_la_fecha_por_ipc_y_no_da_rojo():
    # Compra 2021-05-20, góndola del 2021-06-01: se deflacta por el IPC de mayo/junio (3%).
    obs = [may(103.0, fecha="2021-06-01", fuente="a"), may(103.0, fecha="2021-06-01", fuente="b", desc="Otra 1 kg")]
    res = compare(140.0, 10.0, "2021-05-20", 1.0, obs, IPC, CFG_MIN)
    assert res.ref_min_mayorista == pytest.approx(100.0)
    assert "por IPC" in res.evidencia[0]["descripcion"] and res.evidencia[0]["posterior"]
    # Observaciones posteriores no corroboran (P21): sin rojo aunque la brecha sea +36%.
    assert res.ref_corroborada is None and res.semaforo == "amarillo"


def test_as_of_prefiere_la_anterior_a_la_posterior():
    obs = [may(90, fecha="2021-06-10", fuente="a", desc="Arroz 1 kg"), may(100, fecha="2021-06-05", fuente="a", desc="Arroz 1 kg")]
    selected, _ = select_observations(obs, "2021-06-08", 1.0, IPC, CFG)
    assert [o.precio_ub for o in selected] == [100] and not selected[0].posterior


def test_mes_sin_indice_no_se_sustituye():
    obs = [may(100, fecha="2021-07-02", fuente="a")]
    selected, _ = select_observations(obs, "2021-06-28", 1.0, IPC, CFG)
    assert selected[0].precio_ub == 100 and "índice no disponible" in selected[0].descripcion


def test_indec_solo_nunca_da_rojo_y_de_otra_presentacion_no_es_referencia():
    oficial = [Obs("indec_gba", "oficial", "2021-06-01", 100.0, 0.75, "INDEC detergente 750 ml")]
    # Bidón de 5 l contra el promedio INDEC de 750 ml: no es referencia (P19).
    res = compare(300.0, 10.0, "2021-06-08", 5.0, oficial, IPC, CFG)
    assert res.ref_principal is None and res.semaforo == "gris" and "el promedio INDEC es de otra presentación" in res.motivo
    # Misma presentación: brecha contextual, como máximo amarillo.
    ok = compare(300.0, 10.0, "2021-06-08", 1.0, oficial, IPC, CFG)
    assert ok.ref_principal == "indec" and ok.semaforo == "amarillo" and "promedio INDEC" in ok.motivo


def test_variante_de_otra_fecha_no_es_referencia():
    vfn = lambda d: "fortificada" if "fortificada" in d.lower() else "comun"
    obs = [may(240, fuente="a", desc="Leche polvo comun"), may(250, fuente="b", desc="Leche polvo otra"),
           may(500, fecha="2024-06-05", fuente="a", desc="Leche polvo fortificada")]
    # La fortificada solo existe en 2024: en 2021 no hay referencia de esa variante -> gris (P18).
    res = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, dict(CFG_MIN, equivalencia_no_verificada="gris"),
                  variante="fortificada", variante_obs=vfn)
    assert res.semaforo == "gris" and res.motivo_codigo == "sin_referencia_variante"
    # Con la flag en sin_rojo vuelve el comportamiento anterior (todo el mercado, sin rojo).
    res2 = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, dict(CFG_MIN, variante_sin_mercado="sin_rojo"),
                   variante="fortificada", variante_obs=vfn)
    assert res2.semaforo == "amarillo" and "variedad no verificada" in res2.motivo


def test_presentacion_exacta_se_busca_en_la_fecha_no_en_todo_el_historial():
    # La licitación dice "x 1 kg"; en 2021 solo hay 800 g y en 2024 hay 1 kg.
    obs = [may(240, fuente="a", contenido=0.8), may(250, fuente="b", contenido=0.8),
           may(900, fecha="2024-06-05", fuente="a", contenido=1.0, desc="Leche 1 kg")]
    # Estricto (default): el envase exigido no está en la fecha -> gris; el de 2024 no lo reemplaza.
    res = compare(300.0, 10.0, "2021-06-08", 1.0, obs, IPC, dict(CFG_MIN, equivalencia_no_verificada="gris"),
                  presentacion_exacta=True)
    assert res.semaforo == "gris" and res.motivo_codigo == "sin_referencia_presentacion"
    # Con la brecha contra envases parecidos >= rojo: amarillo "equivalencia por verificar".
    eq = compare(300.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN, presentacion_exacta=True)
    assert eq.semaforo == "amarillo" and eq.motivo_codigo == "equivalencia_por_verificar"
    # No estricto: banda dentro de la fecha.
    res2 = compare(300.0, 10.0, "2021-06-08", 1.0, obs, IPC, dict(CFG_MIN, presentacion_exigida_estricta=False),
                   presentacion_exacta=True)
    assert res2.ref_min_mayorista == 240 and not res2.presentacion_exacta


def test_compra_generica_acepta_variantes_compatibles():
    vfn = lambda d: "tirabuzon" if "tirabuzon" in d else ("mostachol" if "mostachol" in d else None)
    obs = [may(240, fuente="a", desc="fideos tirabuzon"), may(242, fuente="b", desc="fideos mostachol")]
    res = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN, variante="guisero_sin_detalle", variante_obs=vfn,
                  variantes_ok={"guisero_sin_detalle", "tirabuzon", "mostachol"})
    assert res.semaforo == "rojo" and res.ref_corroborada == 242
    # Una compra de tirabuzón no se confirma con mostachol.
    solo = compare(400.0, 10.0, "2021-06-08", 1.0, obs, IPC, CFG_MIN, variante="tirabuzon", variante_obs=vfn)
    assert solo.ref_corroborada is None and solo.semaforo == "amarillo"
