#!/usr/bin/env python3
"""Caso sintético con respuesta conocida, para validar visualmente el motor de comparación
(MEJORAS #50, último punto): yerba mate 1 kg, comprada por el Estado a un precio conocido, con
dos cadenas de familias de fuente DISTINTAS a precios conocidos ese mismo mes (par que corrobora
el mínimo) e IPC conocido — todo elegido para que la cuenta se pueda verificar con una
calculadora, sin adivinar nada del motor real.

Los datos de entrada (todos sintéticos, ninguno pisa `data/sobreprecios.db`):
  - Compra: 1.000 kg de "yerba mate sin palo" a $1.300/kg (precio_unitario), el 2024-02-15.
  - Mercado, mismo día (gap=0): cadena_e2e_norte $1.000/kg, cadena_e2e_sur $1.020/kg — dos
    "familias" de fuente distintas (sobreprecios/pipeline/compare.py:familia_fuente trata
    cualquier fuente sin patrón conocido como su propia familia).
  - IPC alimentos (config/settings.yml → indices.ipc_alimentos): ene-2024=100, feb-2024=102
    (2 % mensual), seteado a mano en la tabla `indices` — nunca se toca la serie real del INDEC.
  - Plazo de pago: no viene en el pliego (plazo_pago_dias=None) → usa los defaults de
    config/settings.yml (comparacion.plazo_pago_meses=1, comparacion.plazo_escenarios_rojo_meses=2).

La cuenta a mano (misma fórmula que `pipeline/compare.py:compare()`):
  1. precio_estado_ub = precio_unitario / contenido_base = 1.300 / 1 = $1.300/kg.
  2. ref_principal_valor = mínimo de mercado = min(1.000, 1.020) = $1.000/kg (referencia_principal:
     min_mercado).
  3. ref_corroborada = el par de DOS familias distintas dentro de ±5 % (corroboracion_tolerancia)
     con el máximo más chico = $1.020/kg (el par 1.000/1.020 corrobora: 1.020 <= 1.000×1,05).
  4. Escenario default (1 mes de plazo): factor = 1,02^1 = 1,02.
     precio ajustado = 1.300 / 1,02 = $1.274,51/kg.
     sobreprecio_ajustado_pct = (1.274,51 / 1.000 − 1) × 100 = **+27,45 %**.
  5. Escenario "rojo" (el más largo plausible, 2 meses): factor = 1,02^2 = 1,0404.
     precio ajustado = 1.300 / 1,0404 = $1.249,52/kg.
     sobreprecio_corroborado_pct = (1.249,52 / 1.020 − 1) × 100 = **+22,50 %**.
  6. Semáforo: sobreprecio_ajustado_pct (27,45 %) >= umbral_amarillo (8 %) Y
     sobreprecio_corroborado_pct (22,50 %) >= umbral_rojo (20 %) Y el mínimo SÍ está corroborado
     por 2 fuentes → **rojo**.
  7. Exceso en pesos = (precio ajustado del escenario default − ref_principal_valor) × cantidad_base
     = (1.274,51 − 1.000) × 1.000 kg = **$274.509,80**.

Ver `ESPERADO` más abajo (son los valores EXACTOS, calculados con Python — misma aritmética,
sin redondeos manuales) y `tests/test_caso_sintetico.py`, que corre esto mismo y assertea esos
números contra lo que de verdad calcula `pipeline/compare.py`.

Nunca toca `data/sobreprecios.db`: todo corre contra una base temporal propia en
`_scratch/licitaciones/caso_sintetico/` (gitignored).

Uso:
    .venv/bin/python scripts/caso_sintetico.py build         # siembra + imprime los numeros
    .venv/bin/python scripts/caso_sintetico.py screenshots   # build + 3 capturas (Playwright, 1280x800)
                                                              # a _scratch/licitaciones/caso_sintetico/
    .venv/bin/python scripts/caso_sintetico.py validar-prime # manda comprobante+expediente al
                                                              # modelo prime (gpt-6-astra) para una
                                                              # segunda opinion (ver auditoria/juez.py
                                                              # y _scratch/licitaciones/auditoria/
                                                              # gasto_prime.tsv para el ledger de gasto)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from sobreprecios import catalog, config, db  # noqa: E402
from sobreprecios.pipeline import compare, match  # noqa: E402

CASO_DIR = config.SCRATCH_DIR / "licitaciones" / "caso_sintetico"
DB_PATH = CASO_DIR / "caso_sintetico.db"

ORGANISMO = "ORGANISMO CASO SINTETICO E2E"
NUMERO = "00-0001-SINT"
FECHA = "2024-02-15"
DESCRIPCION = "Yerba mate sin palo 1 kg"
CANTIDAD_KG = 1000.0
PRECIO_ESTADO = 1300.0
MONTO_TOTAL = CANTIDAD_KG * PRECIO_ESTADO
PRECIO_NORTE = 1000.0
PRECIO_SUR = 1020.0
IPC_ENERO = 100.0
IPC_FEBRERO = 102.0

# Valores EXACTOS esperados en la fila de `comparaciones` (misma aritmética que el docstring de
# arriba, sin redondear a mano — ver tests/test_caso_sintetico.py para la cuenta paso a paso).
_INFLACION = IPC_FEBRERO / IPC_ENERO - 1
_ADJ_1M = PRECIO_ESTADO / (1 + _INFLACION) ** 1
_ADJ_2M = PRECIO_ESTADO / (1 + _INFLACION) ** 2
ESPERADO = {
    "semaforo": "rojo",
    "ref_principal_valor": 1000.0,
    "ref_corroborada": 1020.0,
    "sobreprecio_ajustado_pct": round((_ADJ_1M / 1000.0 - 1) * 100, 2),
    "sobreprecio_corroborado_pct": round((_ADJ_2M / 1020.0 - 1) * 100, 2),
    "exceso_ars": round((_ADJ_1M - 1000.0) * CANTIDAD_KG, 2),
}


def sembrar(conn) -> dict:
    """Siembra el caso en `conn` (una base ya con el esquema inicializado, ver `db.session`) y
    corre match + compare de verdad. Devuelve {"proceso_id", "renglon_id"}."""
    catalog.sync_to_db(conn)

    proceso_id = db.upsert(conn, "procesos", {
        "fuente": "bo_primera", "external_id": "caso_sintetico/yerba1",
        "numero": NUMERO, "organismo": ORGANISMO, "tipo_procedimiento": "licitacion_publica",
        "fecha_adjudicacion": FECHA, "fecha_publicacion": FECHA,
        "monto_total": MONTO_TOTAL, "estado_revision": "pendiente",
        "created_at": db.now_iso(), "updated_at": db.now_iso(),
    }, ("fuente", "external_id"))
    renglon_id = db.upsert(conn, "renglones", {
        "proceso_id": proceso_id, "nro_renglon": 1,
        "descripcion": DESCRIPCION, "cantidad": CANTIDAD_KG, "unidad_compra": "kilogramos",
        "precio_unitario": PRECIO_ESTADO, "monto_total": MONTO_TOTAL,
        "proveedor": "PROVEEDOR CASO SINTETICO SRL", "proveedor_cuit": "30-00000000-1",
        "external_key": "caso-sintetico:yerba:1",
    }, ("proceso_id", "external_key"))

    # Dos cadenas de familias DISTINTAS (fuentes sin ningún patrón conocido en familia_fuente():
    # cada una es su propia familia), mismo día de la compra (gap=0) → corroboran el mínimo.
    for fuente, precio, obs_key in (
        ("cadena_e2e_norte", PRECIO_NORTE, "caso-sintetico-norte"),
        ("cadena_e2e_sur", PRECIO_SUR, "caso-sintetico-sur"),
    ):
        db.upsert(conn, "precios_mercado", {
            "fuente": fuente, "canal": "mayorista", "fecha": FECHA,
            "descripcion": f"{DESCRIPCION} - {fuente}",
            "precio": precio, "contenido_base": 1.0, "unidad_base": "kg", "precio_unidad_base": precio,
            "producto_id": "yerba_mate", "match_metodo": "sintetico", "obs_key": obs_key,
        }, ("fuente", "fecha", "obs_key"))

    # IPC alimentos a mano (nunca la serie real del INDEC): ene-2024=100, feb-2024=102 (+2%).
    serie = (config.settings().get("indices") or {})["ipc_alimentos"]
    for fecha_mes, valor in (("2024-01-01", IPC_ENERO), ("2024-02-01", IPC_FEBRERO)):
        conn.execute(
            "INSERT INTO indices (serie, fecha, valor) VALUES (?, ?, ?) "
            "ON CONFLICT (serie, fecha) DO UPDATE SET valor=excluded.valor",
            (serie, fecha_mes, valor))
    conn.commit()

    match.match_renglones(conn, use_llm=False)
    compare.run(conn, [proceso_id])
    return {"proceso_id": proceso_id, "renglon_id": renglon_id}


def _fila_comparacion(conn, renglon_id: int) -> dict:
    row = conn.execute("SELECT * FROM comparaciones WHERE renglon_id=?", (renglon_id,)).fetchone()
    return dict(row) if row else {}


def build(reset: bool = True) -> dict:
    """Crea (o resetea) la base temporal propia y siembra el caso. Nunca toca LIC_DB_PATH si ya
    apuntaba a otro lado: lo pisa a propósito, porque esta función ES el dueño de esa base."""
    CASO_DIR.mkdir(parents=True, exist_ok=True)
    if reset:
        for suf in ("", "-wal", "-shm"):
            p = DB_PATH.with_name(DB_PATH.name + suf)
            if p.exists():
                p.unlink()
    os.environ["LIC_DB_PATH"] = str(DB_PATH)
    with db.session(DB_PATH) as conn:
        ids = sembrar(conn)
    return ids


def cmd_build(_args) -> None:
    ids = build()
    with db.session(DB_PATH) as conn:
        fila = _fila_comparacion(conn, ids["renglon_id"])
    print(f"base: {DB_PATH}")
    print(f"proceso_id={ids['proceso_id']} renglon_id={ids['renglon_id']}")
    todo_ok = True
    for campo, esperado in ESPERADO.items():
        real = fila.get(campo)
        ok = isinstance(real, (int, float)) and isinstance(esperado, (int, float)) and abs(real - esperado) < 0.005 \
            or real == esperado
        todo_ok = todo_ok and ok
        print(f"  {campo}: esperado={esperado!r} real={real!r} [{'OK' if ok else 'DIFERENTE'}]")
    print("TODO OK" if todo_ok else "HAY DIFERENCIAS — revisar antes de confiar en el caso")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_up(base_url: str, timeout: float = 25.0) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base_url + "/", timeout=1.0).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"uvicorn no respondió en {timeout}s en {base_url}")


def cmd_screenshots(_args) -> None:
    ids = build()
    from sobreprecios import reports
    reports.crear_expediente(ids["proceso_id"], "Expediente caso sintetico", [ids["renglon_id"]])
    with db.session(DB_PATH) as conn:
        rep = conn.execute(
            "SELECT html_path FROM reportes WHERE tipo='expediente' AND proceso_id=? ORDER BY id DESC LIMIT 1",
            (ids["proceso_id"],)).fetchone()
    expediente_html = Path(rep["html_path"]).resolve()

    port = _free_port()
    env = dict(os.environ)
    env.update({
        "LIC_DB_PATH": str(DB_PATH), "LIC_CACHE_DIR": str(CASO_DIR / "cache"),
        "LIC_LLM_OFFLINE": "1", "LIC_LLM_PERFIL": "gratis",
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": "",
        "PYTHONUNBUFFERED": "1",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sobreprecios.web.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_up(base_url)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})

            page.goto(f"{base_url}/licitacion/{ids['proceso_id']}")
            page.wait_for_load_state("load")
            page.screenshot(path=str(CASO_DIR / "licitacion.png"), full_page=True)

            page.goto(f"{base_url}/renglon/{ids['renglon_id']}/comprobante")
            page.wait_for_load_state("load")
            page.screenshot(path=str(CASO_DIR / "comprobante.png"), full_page=True)

            page.goto(f"file://{expediente_html}")
            page.wait_for_load_state("load")
            page.screenshot(path=str(CASO_DIR / "expediente.png"), full_page=True)

            browser.close()
        print(f"capturas en {CASO_DIR}")
        for nombre in ("licitacion.png", "comprobante.png", "expediente.png"):
            print(f"  {CASO_DIR / nombre}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# ── Segunda opinión del modelo "prime" (gpt-6-astra) sobre el caso sintético ────────────────

_SCHEMA_VALIDACION_PRIME = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cuenta_correcta", "cuenta_comentario", "color_coherente", "color_comentario",
                 "entendible_periodista", "entendible_comentario", "hallazgos"],
    "properties": {
        "cuenta_correcta": {"type": "boolean"},
        "cuenta_comentario": {"type": "string"},
        "color_coherente": {"type": "boolean"},
        "color_comentario": {"type": "string"},
        "entendible_periodista": {"type": "boolean"},
        "entendible_comentario": {"type": "string"},
        "hallazgos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tipo", "descripcion"],
                "properties": {
                    "tipo": {"type": "string", "enum": ["bug_real", "mejora_texto", "opinion"]},
                    "descripcion": {"type": "string"},
                },
            },
        },
    },
}

_PROMPT_VALIDACION_PRIME = """Sos un auditor externo e independiente de un sistema que detecta posibles
sobreprecios en compras del Estado argentino, comparando contra el precio MÍNIMO de mercado del mismo
producto/variante/envase a la misma fecha.

Te paso dos documentos de UN CASO SINTÉTICO (datos inventados a propósito, con la cuenta ya calculada a
mano para poder chequearte): el "comprobante de cálculo" (vista interna, paso a paso) y el "expediente
para periodista" (lo que se le mandaría a un medio, sin jerga técnica). Es yerba mate 1 kg comprada por
el Estado a $1.300/kg, comparada contra dos cadenas mayoristas de familias de fuente DISTINTAS
($1.000/kg y $1.020/kg, mismo mes), con una inflación mensual conocida del 2% y plazo de pago de 1 mes
(escenario default) / 2 meses (escenario que habilita rojo).

Revisá TRES cosas, por separado:
1. LA CUENTA: precio por unidad base, referencia de mercado (mínimo vs. mínimo CORROBORADO por dos
   fuentes independientes), el ajuste por IPC/inflación, el plazo de pago, y el "exceso" en pesos.
   Verificala vos con una calculadora: no asumas que está bien solo porque el documento lo dice.
2. EL COLOR: ¿el semáforo (rojo/amarillo/verde) que muestra el documento es coherente con las reglas
   que el propio documento describe (umbral de rojo, umbral de amarillo, exigencia de corroboración
   por 2 fuentes)?
3. CLARIDAD PARA UN PERIODISTA NO TÉCNICO: ¿alguien sin formación en economía o estadística entendería
   QUÉ se afirma y QUÉ NO se afirma (que un color es una alerta para revisar, nunca una conclusión de
   irregularidad)?

Clasificá cada hallazgo como EXACTAMENTE uno de:
- "bug_real": la cuenta está mal, el color no corresponde a las reglas descriptas, o el documento
  afirma algo que los datos no sostienen.
- "mejora_texto": la cuenta está bien pero el texto podría ser más claro, más corto o menos técnico.
- "opinion": preferencia de estilo o enfoque, sin un error de fondo.

Sé específico (qué número, qué frase) y BREVE: máximo 5 hallazgos, 1-2 oraciones cada uno.

=== COMPROBANTE DE CÁLCULO (HTML) ===
{comprobante}

=== EXPEDIENTE PARA PERIODISTA (HTML) ===
{expediente}
"""


def _comprobante_html(ids: dict) -> str:
    """Levanta el webui apuntado a la base del caso, baja el HTML de /renglon/<id>/comprobante
    y lo apaga. No usa Playwright (no hace falta un navegador para esto, solo el texto)."""
    import httpx
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "LIC_DB_PATH": str(DB_PATH), "LIC_CACHE_DIR": str(CASO_DIR / "cache"),
        "LIC_LLM_OFFLINE": "1", "LIC_LLM_PERFIL": "gratis",
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": "",
        "PYTHONUNBUFFERED": "1",
    })
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "sobreprecios.web.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_up(base_url)
        resp = httpx.get(f"{base_url}/renglon/{ids['renglon_id']}/comprobante", timeout=15.0)
        resp.raise_for_status()
        return resp.text
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _registrar_gasto_prime(uso: str, modelo: str, items: int, usd: float) -> None:
    """Agrega una fila a _scratch/licitaciones/auditoria/gasto_prime.tsv (mismo ledger de
    auditoria/juez.py). Lee el archivo justo antes de escribir (no cachea un snapshot viejo): el
    ledger es compartido entre sesiones."""
    import datetime as dt
    ledger = config.SCRATCH_DIR / "licitaciones" / "auditoria" / "gasto_prime.tsv"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not ledger.exists()
    with open(ledger, "a", encoding="utf-8") as fh:
        if nuevo:
            fh.write("fecha\tuso\tmodelo\titems\tusd\n")
        fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M}\t{uso}\t{modelo}\t{items}\t{usd:.3f}\n")


def cmd_validar_prime(_args) -> None:
    from sobreprecios import config as config_mod
    from sobreprecios import llm

    ids = build()
    from sobreprecios import reports
    reports.crear_expediente(ids["proceso_id"], "Expediente caso sintetico", [ids["renglon_id"]])
    with db.session(DB_PATH) as conn:
        rep = conn.execute(
            "SELECT html_path FROM reportes WHERE tipo='expediente' AND proceso_id=? ORDER BY id DESC LIMIT 1",
            (ids["proceso_id"],)).fetchone()
    expediente_html = Path(rep["html_path"]).read_text(encoding="utf-8")
    comprobante_html = _comprobante_html(ids)

    config_mod.load_env()   # toma OPENAI_API_KEY del .env local (nunca se imprime)
    os.environ["LIC_LLM_PERFIL"] = "pago"          # perfil pago: gpt-6-astra cuesta de verdad
    os.environ["LIC_LLM_ROLE_JUEZ"] = "gpt-6-astra"
    MAX_USD = 2.0
    llm.reset_budget()

    prompt = _PROMPT_VALIDACION_PRIME.format(comprobante=comprobante_html, expediente=expediente_html)
    print(f"Llamando a gpt-6-astra (tope USD {MAX_USD:.2f})...")
    try:
        resultado = llm.complete_json(prompt, _SCHEMA_VALIDACION_PRIME, role="juez",
                                      proposito="validacion_prime_caso_sintetico",
                                      schema_name="validacion_prime", use_cache=False, max_usd=MAX_USD)
    except llm.LLMUnavailable as exc:
        gasto = llm.run_spend()
        _registrar_gasto_prime(f"[presupuesto 2] caso sintetico (FALLÓ: {type(exc).__name__})",
                               "gpt-6-astra", 1, gasto)
        print(f"ERROR: {exc}")
        raise SystemExit(1)

    gasto = llm.run_spend()
    _registrar_gasto_prime("[presupuesto 2] validacion caso sintetico (comprobante+expediente)",
                           "gpt-6-astra", 1, gasto)
    print(f"gasto real: USD {gasto:.3f}")
    print(json.dumps(resultado, ensure_ascii=False, indent=2))

    out_md = CASO_DIR / "VALIDACION_PRIME.md"
    reales = [h for h in resultado.get("hallazgos", []) if h.get("tipo") == "bug_real"]
    mejoras = [h for h in resultado.get("hallazgos", []) if h.get("tipo") == "mejora_texto"]
    opiniones = [h for h in resultado.get("hallazgos", []) if h.get("tipo") == "opinion"]
    lineas = [
        "# Validación del prime (gpt-6-astra) — caso sintético (MEJORAS #50)",
        "",
        f"Gasto real de esta llamada: USD {gasto:.3f} (tope USD {MAX_USD:.2f}; ledger en "
        "`_scratch/licitaciones/auditoria/gasto_prime.tsv`, prefijo `[presupuesto 2]`).",
        "",
        f"- Cuenta correcta (precio por unidad base, brecha, IPC, plazo): **{resultado['cuenta_correcta']}** — "
        f"{resultado['cuenta_comentario']}",
        f"- Color coherente con las reglas: **{resultado['color_coherente']}** — {resultado['color_comentario']}",
        f"- Entendible para un periodista no técnico: **{resultado['entendible_periodista']}** — "
        f"{resultado['entendible_comentario']}",
        "",
        "## Bugs reales" if reales else "## Bugs reales\n\n(ninguno)",
    ]
    for h in reales:
        lineas.append(f"- {h['descripcion']}")
    lineas.append("\n## Mejoras de texto" if mejoras else "\n## Mejoras de texto\n\n(ninguna)")
    for h in mejoras:
        lineas.append(f"- {h['descripcion']}")
    lineas.append("\n## Opinión" if opiniones else "\n## Opinión\n\n(ninguna)")
    for h in opiniones:
        lineas.append(f"- {h['descripcion']}")
    lineas.append("\n## Respuesta cruda (JSON)\n")
    lineas.append("```json")
    lineas.append(json.dumps(resultado, ensure_ascii=False, indent=2))
    lineas.append("```")
    out_md.write_text("\n".join(lineas), encoding="utf-8")
    print(f"resumen en {out_md}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="siembra el caso en la base temporal e imprime los números")
    sub.add_parser("screenshots", help="build + 3 capturas (licitación, comprobante, expediente)")
    sub.add_parser("validar-prime", help="build + manda comprobante/expediente al modelo prime")
    args = ap.parse_args()
    {"build": cmd_build, "screenshots": cmd_screenshots,
     "validar-prime": cmd_validar_prime}[args.cmd](args)


if __name__ == "__main__":
    main()
