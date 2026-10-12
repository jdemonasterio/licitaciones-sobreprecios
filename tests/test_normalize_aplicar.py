"""Tests de `normalize/aplicar.run()` para `organismo_canonico` (MEJORAS #53): el mismo organismo
aparece con nombres distintos segun la fuente (codigo SAF de COMPR.AR adelante, titulo completo
del Boletin Oficial, con una subunidad despues) y eso partia las cifras en Analitica, los filtros
y los rankings del webui. Casos reales de `data/sobreprecios.db` (verificado 2026-10-12)."""
from __future__ import annotations

from sobreprecios import config, db
from sobreprecios.normalize import aplicar


def _proceso(conn, external_id: str, organismo: str) -> int:
    db.upsert(conn, "procesos", {
        "fuente": "bo_primera", "external_id": external_id, "organismo": organismo,
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }, ("fuente", "external_id"))
    return conn.execute("SELECT id FROM procesos WHERE fuente='bo_primera' AND external_id=?",
                        (external_id,)).fetchone()[0]


def _organismo_canonico(conn, pid: int) -> str | None:
    return conn.execute("SELECT organismo_canonico FROM procesos WHERE id=?", (pid,)).fetchone()[0]


def test_run_unifica_las_3_variantes_reales_del_mismo_ministerio(tmp_path):
    """"311 - Ministerio de Desarrollo Social" (COMPR.AR, con SAF), "MINISTERIO DE DESARROLLO
    SOCIAL" (BO, sin SAF) y la misma con una subunidad despues: las 3 tienen que terminar con el
    MISMO `organismo_canonico` (antes partian la cifra en 3 baldes: 4 + 45 + 19 procesos)."""
    with db.session(tmp_path / "t.db") as conn:
        p1 = _proceso(conn, "1", "311 - Ministerio de Desarrollo Social")
        p2 = _proceso(conn, "2", "MINISTERIO DE DESARROLLO SOCIAL")
        p3 = _proceso(conn, "3", "MINISTERIO DE DESARROLLO SOCIAL SECRETARÍA DE GESTIÓN ADMINISTRATIVA")
        aplicar.run(conn)
        canon = {_organismo_canonico(conn, p) for p in (p1, p2, p3)}
    assert canon == {"Ministerio de Desarrollo Social"}


def test_run_elige_la_variante_mejor_acentuada_para_mostrar(tmp_path):
    """Si una fuente perdio la tilde (glitch de encoding/scraping) y otra la tiene bien, el
    canonico que se MUESTRA tiene que ser el acentuado correcto, no el que llego primero ni el
    que mas se repite."""
    with db.session(tmp_path / "t.db") as conn:
        sin_tilde = _proceso(conn, "1", "MINISTERIO DE ECONOMIA")          # glitch, sin tilde
        sin_tilde_2 = _proceso(conn, "2", "MINISTERIO DE ECONOMIA")        # se repite (2 procesos)
        con_tilde = _proceso(conn, "3", "MINISTERIO DE ECONOMÍA")          # bien escrito, 1 proceso
        aplicar.run(conn)
        canon = {_organismo_canonico(conn, p) for p in (sin_tilde, sin_tilde_2, con_tilde)}
    assert canon == {"Ministerio de Economía"}


def test_run_no_une_organismos_con_nombre_parecido_pero_distinto(tmp_path):
    """"Ministerio de Salud" y "Ministerio de Salud y Desarrollo Social" son organismos
    DISTINTOS (ejemplo del owner): no tienen que compartir canonico aunque uno sea casi
    sub-cadena del otro."""
    with db.session(tmp_path / "t.db") as conn:
        salud = _proceso(conn, "1", "MINISTERIO DE SALUD")
        salud_y_ds = _proceso(conn, "2", "MINISTERIO DE SALUD Y DESARROLLO SOCIAL")
        aplicar.run(conn)
        assert _organismo_canonico(conn, salud) == "Ministerio de Salud"
        assert _organismo_canonico(conn, salud_y_ds) == "Ministerio de Salud y Desarrollo Social"


def test_run_agrupa_subunidades_en_la_jurisdiccion_madre_por_default(tmp_path):
    """Default (`organismos.agrupar_subunidades: true`): una subunidad sube a su jurisdiccion."""
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso(conn, "1", "MINISTERIO DE SALUD SECRETARÍA DE EQUIDAD EN SALUD")
        aplicar.run(conn)
        assert _organismo_canonico(conn, pid) == "Ministerio de Salud"


def test_run_no_agrupa_subunidades_si_el_owner_lo_apaga(tmp_path, monkeypatch):
    """`organismos.agrupar_subunidades: false`: la subunidad queda en su propio balde (no sube a
    la jurisdiccion madre), pero sigue normalizada (mayusculas/acentos no la duplican)."""
    original = config.load_yaml
    monkeypatch.setattr(config, "load_yaml", lambda name: (
        {"organismos": {"agrupar_subunidades": False}} if name == "settings.yml" else original(name)))
    with db.session(tmp_path / "t.db") as conn:
        ministerio = _proceso(conn, "1", "MINISTERIO DE SALUD")
        subunidad_a = _proceso(conn, "2", "MINISTERIO DE SALUD SECRETARÍA DE EQUIDAD EN SALUD")
        subunidad_a_otra_fuente = _proceso(conn, "3", "ministerio de salud secretaria de equidad en salud")
        aplicar.run(conn)
        canon_ministerio = _organismo_canonico(conn, ministerio)
        canon_subunidad = _organismo_canonico(conn, subunidad_a)
        canon_subunidad_otra_fuente = _organismo_canonico(conn, subunidad_a_otra_fuente)
    assert canon_ministerio == "Ministerio de Salud"
    assert canon_subunidad != canon_ministerio
    assert canon_subunidad == canon_subunidad_otra_fuente   # sigue deduplicando


def test_run_es_idempotente(tmp_path):
    """Correr `run()` dos veces da el mismo resultado (pipeline lo llama despues de cada ingesta)."""
    with db.session(tmp_path / "t.db") as conn:
        pid = _proceso(conn, "1", "311 - Ministerio de Desarrollo Social")
        _proceso(conn, "2", "MINISTERIO DE DESARROLLO SOCIAL")
        aplicar.run(conn)
        primero = _organismo_canonico(conn, pid)
        aplicar.run(conn)
        segundo = _organismo_canonico(conn, pid)
    assert primero == segundo == "Ministerio de Desarrollo Social"
