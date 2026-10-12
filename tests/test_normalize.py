import pytest

from sobreprecios.normalize.entidades import (cuit_valido, normalizar_empresa, normalizar_organismo,
                                              normalizar_persona)


@pytest.mark.parametrize("cuit,ok", [
    ("30-70779374-7", True), ("30707793747", True), ("30-58885563-1", True), ("30-71831219-8", True),
    ("30-70779374-8", False), ("3070779374", False), (None, False),
])
def test_cuit_valido(cuit, ok):
    assert cuit_valido(cuit) is ok


@pytest.mark.parametrize("a,b", [
    # Casos reales de hoy: misma empresa, distinto texto.
    ("S.A. LA SIBILA", "SOCIEDAD ANONIMA LA SIBILA"),
    ("MICROSULES ARGENTINA SA de SCIIA", "MICROSULES ARGENTINA S.A. DE S.C.I.I.A."),
    ("COMPAÑÍA AMERICANA DE ALIMENTOS S.A.", "COMPANIA AMERICANA DE ALIMENTOS  SA"),
    ("HOLOWATY HNOS. S.R.L.", "Holowaty Hermanos Sociedad de Responsabilidad Limitada"),
    ("STATE GRID ARGENTINA S.A", "State Grid Argentina SA"),
    ("FLEITAS HERNAN Y CIA CONSTRUCTORA S.R.L.", "FLEITAS HERNAN & CIA CONSTRUCTORA SRL"),
])
def test_normalizar_empresa_unifica_variantes(a, b):
    na, nb = normalizar_empresa(a), normalizar_empresa(b)
    assert (na["nombre_base"], na["forma"]) == (nb["nombre_base"], nb["forma"])


def test_normalizar_empresa_clave_solo_por_cuit_valido():
    con = normalizar_empresa("S.A. LA SIBILA", "30-58885563-1")
    assert con["clave"] == "CUIT:30588855631" and con["nombre_display"] == "LA SIBILA S.A."
    sin = normalizar_empresa("S.A. LA SIBILA", None)
    assert sin["clave"].startswith("NOMBRE:")           # candidato, no identidad
    mal = normalizar_empresa("S.A. LA SIBILA", "30-58885563-9")
    assert mal["clave"].startswith("NOMBRE:") and mal["cuit_valido"] is False


def test_normalizar_empresa_persona_humana_y_cooperativa():
    assert normalizar_empresa("Juana Ejemplo Gómez", "27-10000002-9")["forma"] == "PERSONA_HUMANA"
    coop = normalizar_empresa("MANFREY COOPERATIVA DE TAMBEROS DE C. e I. LTDA")
    assert coop["forma"] == "COOPERATIVA" and coop["nombre_base"] == "MANFREY COOPERATIVA DE TAMBEROS DE CEI"


def test_normalizar_persona():
    a = normalizar_persona("PEREZ, JUAN", "10.000.001")
    b = normalizar_persona("Juan Perez", "DNI 10000001")
    assert a["clave"] == b["clave"] == "DNI:10000001"
    assert a["nombre_display"] == "Juan Perez"
    sin = normalizar_persona("Juan Perez")
    assert sin["clave"] == "POSIBLE:JUAN PEREZ"        # pista, no identidad (homónimos)
    assert normalizar_persona("x", "20-10000001-7")["documento"] == "10000001"   # CUIL -> DNI


@pytest.mark.parametrize("texto,jur,unidad,saf", [
    ("MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA", "Ministerio De Desarrollo Social",
     "Secretaría De Gestión Administrativa", None),
    ("310 - Ministerio de Salud", "Ministerio De Salud", None, "310"),
    ("80/000 - Dirección General de Administración - MS", "Dirección General De Administración - Ms", None, None),
    ("MINISTERIO DE SALUD", "Ministerio De Salud", None, None),
])
def test_normalizar_organismo(texto, jur, unidad, saf):
    o = normalizar_organismo(texto)
    assert o["saf"] == saf and o["unidad"] == unidad
    assert o["jurisdiccion"] == jur


@pytest.mark.parametrize("texto,jur_con_tilde", [
    # MEJORAS #53: antes se mostraba sin tildes ("Ministerio De Economia") porque el corte entre
    # jurisdiccion y unidad se hacia sobre el texto YA pelado de acentos y ESE pedazo era lo que
    # se devolvia. Casos reales de la base (2026-10-12).
    ("MINISTERIO DE ECONOMÍA SECRETARÍA LEGAL Y ADMINISTRATIVA", "Ministerio De Economía"),
    ("105 - Comisión Nacional de Energía Atómica", "Comisión Nacional De Energía Atómica"),
    ("201 - Dirección Nacional de Migraciones", "Dirección Nacional De Migraciones"),
])
def test_normalizar_organismo_conserva_tildes_en_la_jurisdiccion(texto, jur_con_tilde):
    assert normalizar_organismo(texto)["jurisdiccion"] == jur_con_tilde


def test_normalizar_organismo_jurisdiccion_clave_ignora_acentos_mayusculas_y_saf():
    """La CLAVE de comparacion (no la que se muestra) tiene que ser la misma para las 3 variantes
    reales del mismo organismo (MEJORAS #53): con codigo SAF adelante, en mayusculas sin SAF (BO),
    y con una subunidad despues (el corte deja la jurisdiccion aparte, ver arriba)."""
    claves = {
        normalizar_organismo("311 - Ministerio de Desarrollo Social")["jurisdiccion_clave"],
        normalizar_organismo("MINISTERIO DE DESARROLLO SOCIAL")["jurisdiccion_clave"],
        normalizar_organismo("MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA")["jurisdiccion_clave"],
    }
    assert len(claves) == 1


def test_normalizar_organismo_jurisdiccion_clave_no_une_organismos_distintos():
    """Nombres parecidos pero DISTINTOS (uno no es subcadena limpia del otro) no deben compartir
    clave: "Ministerio de Salud" != "Ministerio de Salud y Desarrollo Social" (ejemplo del owner)."""
    a = normalizar_organismo("MINISTERIO DE SALUD")["jurisdiccion_clave"]
    b = normalizar_organismo("MINISTERIO DE SALUD Y DESARROLLO SOCIAL")["jurisdiccion_clave"]
    assert a != b


def test_organismo_saf_no_une_renombres():
    o = normalizar_organismo("311 - Ministerio de Capital Humano")
    assert o["saf"] == "311" and o["nombre"] == "Ministerio de Capital Humano"
    assert o["saf_nombre"] == "Ministerio de Desarrollo Social"   # se registra, pero el nombre de la fecha manda
