"""Caso sintético con respuesta conocida (MEJORAS #50, último punto): valida que
`pipeline/compare.py` (el motor real) produce EXACTAMENTE el número que da la cuenta a mano
documentada en `scripts/caso_sintetico.py`. Corre contra una base temporal (`tmp_path`), nunca
contra `data/sobreprecios.db` ni contra la base propia del script (`_scratch/.../caso_sintetico.db`
es para inspección manual/capturas, no para este test)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import scripts.caso_sintetico as caso  # noqa: E402  (REPO ya está en sys.path)
from sobreprecios import db  # noqa: E402


def test_caso_sintetico_da_el_numero_exacto_calculado_a_mano(tmp_path):
    db_path = tmp_path / "caso_sintetico.db"
    with db.session(db_path) as conn:
        ids = caso.sembrar(conn)
        fila = caso._fila_comparacion(conn, ids["renglon_id"])

    # La cuenta a mano completa (precio por unidad base, brecha, IPC y plazo) vive en el
    # docstring de scripts/caso_sintetico.py; acá solo se corrobora que el motor real, con estos
    # datos de entrada conocidos, da EXACTAMENTE esos números — no una aproximación.
    assert fila["semaforo"] == "rojo"
    assert fila["ref_principal_valor"] == 1000.0
    assert fila["ref_corroborada"] == 1020.0
    assert fila["sobreprecio_ajustado_pct"] == 27.45
    assert fila["sobreprecio_corroborado_pct"] == 22.5
    assert fila["exceso_ars"] == 274509.8

    # Mismos valores que ESPERADO (fuente única de verdad del script, para que build()/los
    # pytest no puedan divergir entre sí sin que este test lo note).
    for campo, esperado in caso.ESPERADO.items():
        assert fila[campo] == esperado, f"{campo}: esperado {esperado!r}, dio {fila[campo]!r}"


def test_caso_sintetico_precio_por_unidad_base_y_cantidad_base(tmp_path):
    """La parte de la cuenta anterior a la referencia de mercado: cómo se normaliza la compra."""
    db_path = tmp_path / "caso_sintetico.db"
    with db.session(db_path) as conn:
        ids = caso.sembrar(conn)
        renglon = dict(conn.execute(
            "SELECT * FROM renglones WHERE id=?", (ids["renglon_id"],)).fetchone())

    assert renglon["contenido_base"] == 1.0 and renglon["unidad_base"] == "kg"
    assert renglon["precio_unidad_base"] == caso.PRECIO_ESTADO   # $1.300 / 1 kg = $1.300/kg
    assert renglon["cantidad"] * renglon["contenido_base"] == caso.CANTIDAD_KG   # cantidad_base
