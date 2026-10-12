import pytest

from sobreprecios.catalog import load_catalog, match_rules
from sobreprecios.textnorm import norm


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


def test_norm_strips_accents_keeps_enie_and_splits_sizes():
    assert norm("Puré de Tomate x530Gr.") == "pure de tomate x 530 gr"
    assert norm("Fideos MOÑOS") == "fideos moños"


@pytest.mark.parametrize("descripcion,expected", [
    # Renglones reales del BO 2021 (dump Grupo Quijote)
    ("Leche en polvo x 800 Gr.", "leche_polvo_entera"),
    ("Puré de tomate x 530 Gr.", "pure_tomate"),
    ("Fideos de semola x 400 Gr. ***", "fideos_secos"),
    ("Arvejas partidas x 400 Gr.", "arvejas_secas"),
    ("Harina de Maiz x 1 Kg.", "harina_maiz"),
    ("Harina de Trigo 000 x 1 Kg.", "harina_trigo_000"),
    ("Harina de Trigo Fortificada x 1 Kg.", "harina_trigo_000"),
    ("Arroz Blanco x 1 Kg.", "arroz_blanco"),
    ("Azucar Blanco x 1 Kg.", "azucar"),
    ("Choclo en conserva x350 Gr.", "choclo_conserva"),
    ("Yerba Mate x 1 Kg.", "yerba_mate"),
    # Observaciones de mayoristas 2021
    ("YERBA TARAGUI DESPALADA 1 KG", "yerba_mate"),
    ("Arroz Largo Fino 00000 Gallo 1 Kg", "arroz_blanco"),
    ("FIDEOS LUCCHETTI MOÑOS 500 GR", "fideos_secos"),
    ("Picadillo MAROLIO x90g. (24)", "picadillo_carne"),
    ("Agua lavandina Ayudín 1 L", "lavandina"),
    ("Harina de trigo 0000 Blancaflor 1 kg", "harina_trigo_0000"),
])
def test_match_rules_finds_canonical_product(catalog, descripcion, expected):
    match = match_rules(descripcion, catalog)
    assert match.producto_id == expected, match.razon


@pytest.mark.parametrize("descripcion", [
    "Aceite Mezcla Girasol y Oliva Blend Natura 900 Ml",   # blend con oliva: otro producto
    "Harina de Maiz Blanco para Arepas Morixe 20 Un 1 Kg",  # harina de arepas, no polenta
    "FIDEOS MOLTO DOBLADITOS HUEVO 500 GR",                 # al huevo: más caros
    "Leche en polvo descremada 400 g",
    "Yerba mate compuesta con hierbas 500 g",
    "Servicio de mantenimiento de ascensores",
    "Alimento húmedo para gato Whiskas 340 g. atún",          # exclusión global (mascotas)
])
def test_match_rules_rejects_lookalikes(catalog, descripcion):
    assert match_rules(descripcion, catalog).producto_id is None


def test_catalog_reference_sizes_are_in_base_units(catalog):
    assert catalog["leche_polvo_entera"].tamano_referencia == pytest.approx(0.8)
    assert catalog["aceite_girasol"].tamano_referencia == pytest.approx(1.5)
    assert catalog["yerba_mate"].indec_contenido_base() == pytest.approx(0.5)
    assert catalog["resma_a4"].tamano_referencia == 500


from sobreprecios.catalog import compatible_sibys, variante


def test_compatible_sibys_bloquea_clases_ajenas():
    assert compatible_sibys("MEDIOS DE CULTIVO Y SUPLEMENTO; NOMBRE: AGAR HIERRO TRIPLE AZUCAR, ESTADO: SOLIDO", "azucar")[0] is False
    assert compatible_sibys("FLANES; PESO: ENVASE X 60 GR, SABOR: DULCE DE LECHE", "dulce_leche")[0] is False
    assert compatible_sibys("FIDEOS SECOS; VARIEDAD: GUISERO, RELLENO: SIN", "fideos_secos")[0] is True
    ok, why = compatible_sibys("REPUESTOS; TIPO: FILTRO, MODELO: X", "detergente")
    assert ok is False and "sin mapeo" in why
    assert compatible_sibys("Yerba Mate x 1 Kg.", "yerba_mate")[0] is True   # descripción sin forma SIByS


def test_variantes(catalog):
    f = catalog["fideos_secos"]
    assert variante(f, "FIDEOS SECOS; VARIEDAD: SPAGHETTI, RELLENO: SIN") == "spaghetti"
    assert variante(f, "Fideos Tirabuzón Matarazzo 500 g") == "tirabuzon"
    # La forma específica le gana a la familia genérica; una compra genérica acepta toda la familia.
    assert variante(f, "FIDEOS LA PROVIDENCIA LARGOS SPAGHETTI 500") == "spaghetti"
    from sobreprecios.catalog import variantes_aceptadas
    assert {"tirabuzon", "mostachol"} <= variantes_aceptadas(f, "guisero_sin_detalle")
    assert variantes_aceptadas(f, "tallarin") == {"tallarin"}
    assert variante(f, "Fideos Lucchetti 500 g") is None
    leche = catalog["leche_polvo_entera"]
    # Lo específico le gana a "fortificada" sola; una mención parcial queda sin detalle (no cae en común).
    assert variante(leche, "leche entera en polvo fortificada con hierro, zinc y vitamina C") == "fortificada_hierro_zinc_c"
    assert variante(leche, "leche entera en polvo fortificada con hierro y zinc") == "fortificada_sin_detalle"
    assert variante(leche, "Leche en polvo entera La Serenísima 800 g") is None   # sin default (P17)


def test_variante_estado_de_la_compra(catalog):
    from sobreprecios.catalog import variante_estado
    caballa = catalog["caballa_conserva"]
    # "en aceite y agua" activa dos variantes: ambiguo, no se elige por largo de frase (prime P22).
    assert variante_estado(caballa, "Caballa en aceite y agua Coqueiro 300 g") == (None, "ambiguo")
    assert variante_estado(caballa, "Caballa al natural Gomes da Costa") == ("al_natural", "verificado")
    arroz = catalog["arroz_blanco"]
    assert variante_estado(arroz, "Arroz doble carolina Gallo 1 kg") == ("doble_carolina", "verificado")
    assert variante_estado(arroz, "ARROZ; TIPO GRANO: LARGO-FINO, TIPO: BLANCO")[0] == "largo_fino"
    # Ficha SIByS sin la variante: no exigida. Texto libre sin la variante: desconocida (sin default, P17).
    assert variante_estado(catalog["sal_fina"], "SAL DE MESA; CONSISTENCIA: FINA, TIPO: SIN VALOR") == (None, "no_exigido")
    assert variante_estado(catalog["sal_fina"], "Sal fina x 500 g") == (None, "desconocido")
    assert variante_estado(catalog["yerba_mate"], "Yerba mate sin palo 500 g") == ("sin_palo", "verificado")
    # Frases contiguas y por campo SIByS: "sin palo con menta" no es "con palo"; el "SIN" de otro campo no choca.
    assert variante_estado(catalog["yerba_mate"], "yerba sin palo con menta") == ("sin_palo", "verificado")
    assert variante_estado(catalog["yerba_mate"], "YERBA MATE; TIPO: CON PALO, CERTIFICADO FORESTAL (GFS): SIN") == \
        ("con_palo", "verificado")
    assert variante_estado(catalog["yerba_mate"], "Yerba Mate Suave con Palo 1kg") == (None, "ambiguo")
    # Atributo obligatorio: la ficha SIByS sin concentración no alcanza.
    assert variante_estado(catalog["lavandina"], "LIMPIADORES; TIPO: LAVANDINA, PRESENTACION: ENVASE X 5 L") == \
        (None, "desconocido")


def test_catalogo_sin_ajustes_es_el_base():
    from sobreprecios import config
    base = load_catalog(config.load_yaml("productos.yml"))
    assert list(base["leche_polvo_entera"].variantes) == ["fortificada"]
    assert list(load_catalog()["leche_polvo_entera"].variantes) != ["fortificada"]
