import pytest

from sobreprecios.units import parse_ars, parse_number, parse_size, price_per_base


@pytest.mark.parametrize("text,expected", [
    ("41,49", 41.49),
    ("$ 1.234.567,89", 1234567.89),
    ("1.500", 1500.0),           # punto + 3 dígitos = miles (convención AR)
    ("75.07", 75.07),            # scrapes viejos con punto decimal
    ("38,3435", 38.3435),
    ("98.000.000", 98000000.0),
    ("0.500", 0.5),
    ("1/2", 0.5),
    ("47", 47.0),
    ("", None),
    ("sin dato", None),
])
def test_parse_number_handles_ar_and_anglo_formats(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize("value,expected", [
    ("$ 41,49", 41.49), ("ARS 98.000.000", 98000000.0), (47, 47.0), (None, None),
])
def test_parse_ars_strips_currency(value, expected):
    assert parse_ars(value) == expected


@pytest.mark.parametrize("text,amount,unit,pack", [
    ("Leche en polvo x 800 Gr.", 800, "g", 1),
    ("Yerba Mate x 1 Kg.", 1000, "g", 1),
    ("Puré de tomate x 530 Gr.", 530, "g", 1),
    ("Picadillo MAROLIO x90g. (24)", 90, "g", 1),          # "(24)" es el bulto, no el contenido
    ("Pure de Tomate DE LA HUERTA Tetra Recart x1030g.", 1030, "g", 1),
    ("Aceite Mezcla Girasol y Oliva Blend Natura 900 Ml", 900, "ml", 1),
    ("Aceite girasol 1,5 L", 1500, "ml", 1),
    ("Agua mineral 2.25 lts", 2250, "ml", 1),
    ("Lavandina 1.000 cc", 1000, "ml", 1),
    ("Yerba 1/2 kg", 500, "g", 1),
    ("Harina de Trigo 000 x 1 Kg.", 1000, "g", 1),          # "000" no es un tamaño
    ("Arroz Largo Fino 00000 Gallo 1 Kg", 1000, "g", 1),
    ("Caja x 12 unidades de 1 lt", 1000, "ml", 12),
    ("Pack 6 x 2,25 L", 2250, "ml", 6),
    ("Resma A4 75 g/m2 500 hojas", 500, "u", 1),             # gramaje no es contenido
    # Nombres del SEPA (Precios Claros)
    ("yerba mate PIPORÉ Sin palo PAQ-500-gr.", 500, "g", 1),
    ("Aceite Girasol Bot-1500-cc", 1500, "ml", 1),
    ("Fideos MATARAZZO Tirabuzón In N34 x500g PAQ-500-gr.", 500, "g", 1),   # N34 = forma del fideo
    ("Pipore Yerba Con Palo Suave Clasi10x1/2k Paq-500-gr.", 500, "g", 1),
    ("Yerba La Tranquera SeleccEspec-NM6x1K PVC-1000-gr.", 1000, "g", 1),
])
def test_parse_size_reads_package_content(text, amount, unit, pack):
    size = parse_size(text)
    assert size is not None, text
    assert (size.amount, size.unit, size.pack_count) == (amount, unit, pack)


@pytest.mark.parametrize("text", [
    "Harina de Maiz Blanco para Arepas Morixe 20 Un 1 Kg",   # NO es pack de 20 kg
])
def test_parse_size_does_not_invent_multipacks(text):
    size = parse_size(text)
    assert size is not None and size.pack_count == 1 and size.amount == 1000


@pytest.mark.parametrize("text", [
    "Yerba mate 1 kg o 500 g",       # dos tamaños sin "x" que desempate
    "Aceite 1.5",                     # sin unidad
    "Fideos guiseros",                # sin tamaño
    None,
])
def test_parse_size_returns_none_when_ambiguous(text):
    assert parse_size(text) is None


def test_price_per_base_converts_to_kg_and_l():
    assert price_per_base(309.0, parse_size("x 800 gr")) == (pytest.approx(386.25), "kg")
    assert price_per_base(90.0, parse_size("x 1,5 lt")) == (pytest.approx(60.0), "l")
    assert price_per_base(1200.0, parse_size("12 x 1 lt")) == (pytest.approx(100.0), "l")
    assert price_per_base(None, parse_size("x 1 kg")) == (None, None)
