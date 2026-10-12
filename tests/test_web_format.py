"""Tests de sobreprecios/web/format.py: por ahora solo la lógica no trivial, `explicar_semaforo`
(MEJORAS #49.3) — los formateadores de número (_ars/_num/_pct/_millones) son triviales y ya
los ejercitan los tests de app.py/e2e de forma indirecta."""
from __future__ import annotations

from sobreprecios.web import format as fmt


def test_explicar_semaforo_gris_usa_el_texto_del_motivo_codigo():
    row = {"semaforo": "gris", "motivo_codigo": "sin_datos", "motivo": "x"}
    out = fmt.explicar_semaforo(row)
    assert "no hay precios de mercado" in out


def test_explicar_semaforo_gris_sin_codigo_conocido_usa_el_motivo_crudo():
    row = {"semaforo": "gris", "motivo_codigo": "codigo_inventado", "motivo": "motivo a mano"}
    assert fmt.explicar_semaforo(row) == "motivo a mano"


def test_explicar_semaforo_rojo_con_minimo_corroborado():
    row = {"semaforo": "rojo", "sobreprecio_corroborado_pct": 25.3}
    out = fmt.explicar_semaforo(row)
    assert out.startswith("Rojo")
    assert "mínimo confirmado por dos fuentes" in out
    assert "+25,3%" in out


def test_explicar_semaforo_rojo_sin_corroborado_cae_al_generico():
    row = {"semaforo": "rojo", "sobreprecio_ajustado_pct": 40.0}
    out = fmt.explicar_semaforo(row)
    assert out.startswith("Rojo") and "+40,0%" in out


def test_explicar_semaforo_amarillo_explica_que_bloquea_el_rojo():
    row = {
        "semaforo": "amarillo", "sobreprecio_ajustado_pct": 52.0, "motivo_codigo": "ok",
        "motivo": ("+52.0% vs min mercado (plazo supuesto 1 mes: 3.0%, aprox. IPC; confianza media, "
                   "sin rojo: el mínimo no está confirmado por dos fuentes independientes)"),
    }
    out = fmt.explicar_semaforo(row)
    assert "+52,0%" in out
    assert "el mínimo no está confirmado por dos fuentes independientes" in out
    assert "pero amarillo" in out


def test_explicar_semaforo_amarillo_sin_brecha_alta_usa_el_generico():
    row = {"semaforo": "amarillo", "sobreprecio_ajustado_pct": 10.0, "motivo": "+10.0% vs min mercado"}
    out = fmt.explicar_semaforo(row)
    assert out == "Amarillo: paga +10,0% sobre la referencia de mercado."


def test_explicar_semaforo_equivalencia_por_verificar_extrae_el_detalle():
    row = {
        "semaforo": "amarillo", "motivo_codigo": "equivalencia_por_verificar", "sobreprecio_ajustado_pct": 30.0,
        "motivo": ("posible sobreprecio: +30.0% vs min mercado de productos parecidos, pero la equivalencia no "
                   "está verificada (atributo crítico desconocido). Revisar el pliego antes de concluir"),
    }
    out = fmt.explicar_semaforo(row)
    assert "atributo crítico desconocido" in out
    assert "+30,0%" in out


def test_explicar_semaforo_verde():
    row = {"semaforo": "verde", "sobreprecio_ajustado_pct": -5.0}
    out = fmt.explicar_semaforo(row)
    assert out.startswith("Verde") and "-5,0%" in out


def test_explicar_semaforo_sin_porcentaje_no_rompe():
    assert fmt.explicar_semaforo({"semaforo": "verde"}) == "Verde: dentro de lo esperado."
    assert fmt.explicar_semaforo({"semaforo": "rojo"}) == "Rojo."
    assert fmt.explicar_semaforo({"semaforo": "amarillo", "motivo": ""}) == "Amarillo."


def test_explicar_semaforo_equivalencia_con_parentesis_anidados_no_se_corta():
    """Regresión: el detalle trae la variante entre paréntesis y la frase se cortaba en el primer ")"."""
    row = {"semaforo": "amarillo", "motivo_codigo": "equivalencia_por_verificar", "sobreprecio_ajustado_pct": 106.9,
           "motivo": "posible sobreprecio: +106.9% vs min mercado de productos parecidos, pero la equivalencia no "
                     "está verificada (no hay precios de mercado de la variante exigida (mostachol) en la fecha; "
                     "las otras variantes no son referencia). Revisar el pliego antes de concluir"}
    texto = fmt.explicar_semaforo(row)
    assert "(mostachol) en la fecha; las otras variantes no son referencia" in texto
    assert texto.count("(") == texto.count(")")
