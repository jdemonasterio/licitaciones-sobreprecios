"""SQLite: esquema, conexión y helpers chicos.

Una sola base local (data/sobreprecios.db). Modelo:

  documentos   texto/HTML/PDF crudo bajado de cada fuente (BO, COMPR.AR, ...)
  procesos     una licitación / contratación (organismo, procedimiento, fechas)
  renglones    lo adjudicado: descripción, cantidad, precio unitario, proveedor
               + normalización (producto canónico, contenido, precio por kg/l/u)
  productos    catálogo canónico (config/productos.yml) = la unidad "peras con peras"
  precios_mercado  observaciones de precio (mayorista, minorista, oficial INDEC)
  indices      series mensuales (IPC) para ajustar por inflación
  comparaciones    resultado por renglón: referencias, sobreprecio, semáforo, evidencia
  llm_calls    costo y cache de cada llamada a LLM
  corridas     log de ingestas y procesos batch (lo que muestra la solapa Fuentes)
  reportes     reportes armados desde el webui
  empresas     un registro por CUIT adjudicatario (razón social y datos actuales)
  empresa_hechos  hechos FECHADOS de cada empresa: autoridades, socios, domicilios,
               razón social, capital, sanciones. Nunca se pisan: cada publicación
               (BO segunda sección, registros) agrega un hecho con su vigente_desde;
               el estado actual es el último hecho vigente de cada tipo/cargo. Así
               se puede contestar "quién presidía la empresa cuando ganó esta licitación".

Las conexiones son por hilo: un job en background abre la suya (ver
web/_background.py), nunca reusa la del request.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY,
    fuente TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    fecha_publicacion TEXT,
    titulo TEXT,
    texto TEXT,
    raw_path TEXT,
    fetched_at TEXT,
    UNIQUE (fuente, external_id)
);

CREATE TABLE IF NOT EXISTS procesos (
    id INTEGER PRIMARY KEY,
    fuente TEXT NOT NULL,
    external_id TEXT NOT NULL,
    numero TEXT,
    organismo TEXT,
    jurisdiccion TEXT,
    nivel TEXT DEFAULT 'nacional',
    provincia TEXT,
    tipo_procedimiento TEXT,
    objeto TEXT,
    categoria TEXT,
    es_comparable INTEGER,
    categoria_razon TEXT,
    fecha_apertura TEXT,
    fecha_adjudicacion TEXT,
    fecha_publicacion TEXT,
    monto_total REAL,
    url TEXT,
    documento_id INTEGER REFERENCES documentos(id),
    estado_revision TEXT DEFAULT 'pendiente',
    nota_revision TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (fuente, external_id)
);

CREATE TABLE IF NOT EXISTS renglones (
    id INTEGER PRIMARY KEY,
    proceso_id INTEGER NOT NULL REFERENCES procesos(id) ON DELETE CASCADE,
    nro_renglon INTEGER,
    descripcion TEXT NOT NULL,
    cantidad REAL,
    unidad_compra TEXT,
    precio_unitario REAL,
    monto_total REAL,
    proveedor TEXT,
    proveedor_cuit TEXT,
    marca TEXT,
    producto_id TEXT,
    contenido_base REAL,
    unidad_base TEXT,
    precio_unidad_base REAL,
    match_metodo TEXT,
    match_confianza REAL,
    match_razon TEXT,
    es_comparable INTEGER,
    alertas TEXT,
    extraccion_metodo TEXT,
    external_key TEXT,
    UNIQUE (proceso_id, external_key)
);
CREATE INDEX IF NOT EXISTS ix_renglones_producto ON renglones(producto_id);

CREATE TABLE IF NOT EXISTS productos (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    rubro TEXT,
    unidad_base TEXT NOT NULL,
    tamano_referencia REAL,
    ipc_serie TEXT,
    definicion_json TEXT
);

CREATE TABLE IF NOT EXISTS precios_mercado (
    id INTEGER PRIMARY KEY,
    fuente TEXT NOT NULL,
    canal TEXT NOT NULL,
    fecha TEXT NOT NULL,
    ean TEXT,
    descripcion TEXT,
    marca TEXT,
    precio REAL NOT NULL,
    contenido_base REAL,
    unidad_base TEXT,
    precio_unidad_base REAL,
    producto_id TEXT,
    match_metodo TEXT,
    match_confianza REAL,
    region TEXT,
    sucursal TEXT,
    url TEXT,
    obs_key TEXT NOT NULL,
    UNIQUE (fuente, fecha, obs_key)
);
CREATE INDEX IF NOT EXISTS ix_precios_prod_fecha ON precios_mercado(producto_id, fecha);

CREATE TABLE IF NOT EXISTS indices (
    serie TEXT NOT NULL,
    fecha TEXT NOT NULL,
    valor REAL NOT NULL,
    PRIMARY KEY (serie, fecha)
);

CREATE TABLE IF NOT EXISTS comparaciones (
    renglon_id INTEGER PRIMARY KEY REFERENCES renglones(id) ON DELETE CASCADE,
    fecha_compra TEXT,
    precio_estado_ub REAL,
    ref_min_mayorista REAL,
    ref_p25_mayorista REAL,
    ref_mediana_mayorista REAL,
    ref_n_mayorista INTEGER,
    ref_misma_marca REAL,
    ref_n_misma_marca INTEGER,
    ref_min_minorista REAL,
    ref_mediana_minorista REAL,
    ref_n_minorista INTEGER,
    presentacion_exacta INTEGER,
    ref_indec REAL,
    ref_principal TEXT,
    ref_principal_valor REAL,
    gap_dias INTEGER,
    ajuste_financiero_pct REAL,
    sobreprecio_pct REAL,
    sobreprecio_ajustado_pct REAL,
    sobreprecio_min_pct REAL,
    sobreprecio_minorista_pct REAL,
    sobreprecio_indec_pct REAL,
    exceso_ars REAL,
    confianza TEXT,
    semaforo TEXT,
    motivo TEXT,
    evidencia_json TEXT,
    computed_at TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id INTEGER PRIMARY KEY,
    ts TEXT,
    model TEXT,
    proposito TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    costo_usd REAL,
    cache_hit INTEGER,
    prompt_hash TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS corridas (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL,
    parametros TEXT,
    started_at TEXT,
    finished_at TEXT,
    estado TEXT,
    n_items INTEGER,
    mensaje TEXT
);

CREATE TABLE IF NOT EXISTS empresas (
    cuit TEXT PRIMARY KEY,
    razon_social TEXT,
    tipo_societario TEXT,
    fecha_constitucion TEXT,
    domicilio_actual TEXT,
    primera_adjudicacion TEXT,
    ultima_adjudicacion TEXT,
    contexto_actualizado_at TEXT,
    notas TEXT
);

CREATE TABLE IF NOT EXISTS empresa_hechos (
    id INTEGER PRIMARY KEY,
    cuit TEXT NOT NULL,
    tipo TEXT NOT NULL,
    persona TEXT,
    documento TEXT,
    cargo TEXT,
    valor TEXT,
    vigente_desde TEXT,
    vigente_hasta TEXT,
    fuente TEXT,
    fuente_url TEXT,
    documento_id INTEGER REFERENCES documentos(id),
    extraido_por TEXT,
    confianza REAL,
    created_at TEXT,
    hecho_key TEXT NOT NULL,
    UNIQUE (cuit, hecho_key)
);
CREATE INDEX IF NOT EXISTS ix_hechos_persona ON empresa_hechos(persona);

CREATE TABLE IF NOT EXISTS validaciones_programa (
    id INTEGER PRIMARY KEY,
    fuente TEXT NOT NULL,
    obs_key TEXT NOT NULL,
    autor TEXT NOT NULL,
    veredicto TEXT,
    nota TEXT,
    created_at TEXT,
    UNIQUE (fuente, obs_key, autor)
);

CREATE TABLE IF NOT EXISTS reportes (
    id INTEGER PRIMARY KEY,
    titulo TEXT NOT NULL,
    filtros_json TEXT,
    resumen TEXT,
    created_at TEXT,
    html_path TEXT,
    xlsx_path TEXT
);

-- Módulo de revisión humana (MEJORAS #34): cola de casos grises/rojos/equivalencias/juez con
-- veredicto humano, y correcciones manuales por campo que compare.run() aplica antes de calcular
-- (ver sobreprecios/revision_humana.py). Nunca se pisa un veredicto humano desde el pipeline.
CREATE TABLE IF NOT EXISTS revisiones_caso (
    id INTEGER PRIMARY KEY,
    renglon_id INTEGER NOT NULL REFERENCES renglones(id) ON DELETE CASCADE,
    origen TEXT NOT NULL,          -- gris | equivalencia | rojo | juez | manual
    estado TEXT NOT NULL DEFAULT 'pendiente',   -- pendiente | resuelto | descartado
    veredicto TEXT,                -- confirma | descarta | corrige | no_se_puede
    nota TEXT,
    revisor TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (renglon_id)
);

-- Correcciones manuales por campo: la más reciente por (renglon_id, campo) es la vigente; borrar
-- una fila es "deshacer" esa corrección (vuelve a mandar el dato automático).
CREATE TABLE IF NOT EXISTS correcciones_renglon (
    id INTEGER PRIMARY KEY,
    renglon_id INTEGER NOT NULL REFERENCES renglones(id) ON DELETE CASCADE,
    campo TEXT NOT NULL,           -- producto_id | variante | contenido_base | unidad_base |
                                    -- precio_unitario | cantidad | fecha_comparacion | marca
    valor TEXT,
    nota TEXT,
    autor TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_correcciones_renglon ON correcciones_renglon(renglon_id, campo, created_at);

-- Corridas del juez LLM externo (auditoria/juez.py, solo lectura) importadas a la base con
-- `python -m sobreprecios.cli importar-juez <carpeta>`: ese módulo sigue sin escribir nada acá.
CREATE TABLE IF NOT EXISTS juez_corridas (
    id INTEGER PRIMARY KEY,
    carpeta TEXT NOT NULL UNIQUE,
    modelo TEXT,
    modo TEXT,
    n_items INTEGER,
    usd REAL,
    importada_at TEXT
);

CREATE TABLE IF NOT EXISTS juez_veredictos (
    id INTEGER PRIMARY KEY,
    corrida_id INTEGER NOT NULL REFERENCES juez_corridas(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL,         -- "renglon:<id>" | "obs:<id>" | "clave:<entidad>"
    capa TEXT,
    veredicto TEXT,
    tipo_error TEXT,
    explicacion TEXT,
    valor_correcto TEXT,
    regla_sugerida TEXT
);
CREATE INDEX IF NOT EXISTS ix_juez_veredictos_item ON juez_veredictos(item_id);

-- Juicio humano sobre UNA observación de mercado puntual, en el contexto de UN renglón (pedido de
-- jdemonasterio, ronda P16): las observaciones de precios_mercado no tienen un id estable visible
-- en evidencia_json (es un snapshot JSON, no una FK), así que la clave natural es
-- (fuente, fecha, descripción). "no_es_mismo_producto" / "precio_o_tamano_mal_leido" hacen que
-- pipeline/compare.run() excluya esa observación SOLO para este renglón (ver
-- revision_humana.aplicar_exclusiones_mercado); "equivalente" y "no_se" no excluyen nada, solo
-- dejan constancia de que un humano la miró.
CREATE TABLE IF NOT EXISTS validaciones_mercado (
    id INTEGER PRIMARY KEY,
    renglon_id INTEGER NOT NULL REFERENCES renglones(id) ON DELETE CASCADE,
    obs_fuente TEXT NOT NULL,
    obs_fecha TEXT NOT NULL,
    obs_descripcion TEXT NOT NULL,
    juicio TEXT NOT NULL,          -- equivalente | no_es_mismo_producto | precio_o_tamano_mal_leido | no_se
    nota TEXT,
    autor TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (renglon_id, obs_fuente, obs_fecha, obs_descripcion)
);

-- Validación rápida IN-LINE en /licitacion/<id> (pedido de jdemonasterio, misma ronda que P16):
-- un vistazo por renglón sin tener que abrir /revision/<renglon_id>, partido en las 3 partes que
-- pueden estar mal por separado. Última por (renglon_id, parte) es la vigente (como correcciones_renglon).
CREATE TABLE IF NOT EXISTS validaciones_renglon (
    id INTEGER PRIMARY KEY,
    renglon_id INTEGER NOT NULL REFERENCES renglones(id) ON DELETE CASCADE,
    parte TEXT NOT NULL,           -- extraccion | producto | comparacion
    juicio TEXT NOT NULL,          -- ok | no | no_se
    nota TEXT,
    autor TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (renglon_id, parte)
);

-- Checklist de explicaciones alternativas por caso (MEJORAS #49.6, pedido de jdemonasterio):
-- una brecha de precio no prueba una irregularidad por si sola; estos son los motivos mas
-- comunes que pueden explicarla. Se muestran en /revision/<renglon_id>, el comprobante de
-- calculo y el expediente para periodista. Nunca alimentan al comparador (son anotaciones
-- humanas, no datos de entrada): a diferencia de correcciones_renglon, no se recalcula nada
-- al guardar una.
CREATE TABLE IF NOT EXISTS explicaciones_caso (
    id INTEGER PRIMARY KEY,
    renglon_id INTEGER NOT NULL REFERENCES renglones(id) ON DELETE CASCADE,
    item TEXT NOT NULL,            -- logistica | impuestos | especificaciones | plazo_pago | error_publicacion
    estado TEXT NOT NULL DEFAULT 'pendiente',   -- pendiente | verificado | no_aplica
    nota TEXT,
    autor TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (renglon_id, item)
);
"""


def sync_empresas(conn: sqlite3.Connection) -> int:
    """Crea/actualiza `empresas` a partir de los CUIT adjudicatarios de los renglones."""
    rows = conn.execute(
        "SELECT r.proveedor_cuit AS cuit, MAX(r.proveedor) AS razon, "
        "MIN(COALESCE(p.fecha_adjudicacion, p.fecha_publicacion)) AS primera, "
        "MAX(COALESCE(p.fecha_adjudicacion, p.fecha_publicacion)) AS ultima "
        "FROM renglones r JOIN procesos p ON p.id = r.proceso_id "
        "WHERE r.proveedor_cuit IS NOT NULL AND r.proveedor_cuit <> '' GROUP BY r.proveedor_cuit").fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO empresas (cuit, razon_social, primera_adjudicacion, ultima_adjudicacion) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(cuit) DO UPDATE SET "
            "razon_social=COALESCE(empresas.razon_social, excluded.razon_social), "
            "primera_adjudicacion=excluded.primera_adjudicacion, ultima_adjudicacion=excluded.ultima_adjudicacion",
            (r["cuit"], r["razon"], r["primera"], r["ultima"]))
    conn.commit()
    return len(rows)


def close_superseded_facts(conn: sqlite3.Connection, cuit: str) -> None:
    """Cierra (vigente_hasta) los hechos reemplazados por un acto posterior del mismo tipo.

    Domicilio, razón social y autoridades: cada acto nuevo reemplaza al anterior
    completo (una designación de directorio nombra a TODO el directorio). Los
    hechos con la misma fecha son del mismo acto y no se cierran entre sí. Socios
    y sanciones no se cierran solos: un socio nuevo no implica que el anterior se fue.
    """
    for tipo in ("domicilio", "domicilio_fiscal", "razon_social", "autoridad"):
        rows = conn.execute(
            "SELECT id, vigente_desde FROM empresa_hechos WHERE cuit=? AND tipo=? AND vigente_desde IS NOT NULL",
            (cuit, tipo)).fetchall()
        fechas = sorted({r["vigente_desde"] for r in rows})
        for row in rows:
            later = [f for f in fechas if f > row["vigente_desde"]]
            if later:
                conn.execute("UPDATE empresa_hechos SET vigente_hasta=? WHERE id=? AND vigente_hasta IS NULL",
                             (later[0], row["id"]))
    conn.commit()


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(path) if path else config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# Columnas agregadas después de la primera versión: (tabla, columna, tipo).
# init() las crea si faltan, así una base vieja no hay que borrarla.
MIGRATIONS = [
    ("comparaciones", "ref_p25_mayorista", "REAL"),
    ("comparaciones", "ref_misma_marca", "REAL"),
    ("comparaciones", "ref_n_misma_marca", "INTEGER"),
    ("comparaciones", "ref_min_minorista", "REAL"),
    ("comparaciones", "presentacion_exacta", "INTEGER"),
    ("procesos", "origen", "TEXT"),
    ("comparaciones", "n_ofertas", "INTEGER"),
    ("comparaciones", "oferta_min", "REAL"),
    ("comparaciones", "oferta_min_proveedor", "TEXT"),
    ("comparaciones", "brecha_oferta_min_pct", "REAL"),
    ("comparaciones", "ofertas_implausibles", "INTEGER"),
    ("comparaciones", "motivo_codigo", "TEXT"),
    ("comparaciones", "fuentes_independientes_min", "INTEGER"),
    ("comparaciones", "sensibilidad_json", "TEXT"),
    ("procesos", "revision_huella", "TEXT"),
    ("procesos", "organismo_canonico", "TEXT"),
    ("procesos", "saf", "TEXT"),
    ("renglones", "proveedor_clave", "TEXT"),
    ("empresa_hechos", "persona_clave", "TEXT"),
    # Fragmento fuente por renglón (fila del dictamen + renglón del pliego): para auditar sin re-scrapear.
    ("renglones", "fuente_fragmento", "TEXT"),
    # Decisiones del LLM prime P16-P17 (2026-10-11): mínimo corroborado y estado del atributo crítico.
    ("comparaciones", "ref_corroborada", "REAL"),
    ("comparaciones", "sobreprecio_corroborado_pct", "REAL"),
    ("comparaciones", "estado_atributo", "TEXT"),
    # Precio de mercado sospechoso (placeholder $1, precio viejo sin actualizar): no se borra, se aparta con motivo.
    ("precios_mercado", "cuarentena", "TEXT"),
    # Plazo de pago del pliego (sources/comprar.parse_plazo_pago) y ofertas desestimadas con causa
    # (pedido de jdemonasterio 2026-10-12, ver DESIGN_sobreprecios.md).
    ("procesos", "plazo_pago_dias", "INTEGER"),
    ("procesos", "plazo_pago_texto", "TEXT"),
    ("procesos", "ofertas_desestimadas_n", "INTEGER"),
    ("ofertas", "estado", "TEXT"),
    ("ofertas", "causa_desestimacion", "TEXT"),
    ("comparaciones", "oferta_min_desestimada", "INTEGER"),
    ("comparaciones", "oferta_min_causa", "TEXT"),
    # Expediente para periodista (MEJORAS #49.5): reusa la tabla `reportes` (misma pantalla,
    # mismos archivos html/xlsx/csv) en vez de una tabla aparte; `tipo` distingue uno de otro y
    # `proceso_id` ata el expediente a UNA licitacion puntual (un reporte normal no tiene una).
    ("reportes", "tipo", "TEXT"),
    ("reportes", "proceso_id", "INTEGER"),
]


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    from .pipeline.comprar_import import OFERTAS_SCHEMA
    conn.executescript(OFERTAS_SCHEMA)
    for table, column, kind in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.commit()


@contextmanager
def session(path: Path | str | None = None):
    """Conexión con esquema garantizado; commit al salir, rollback si explota."""
    conn = connect(path)
    try:
        init(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert(conn: sqlite3.Connection, table: str, row: dict, keys: tuple[str, ...]) -> int:
    """INSERT o UPDATE por clave natural; devuelve el id de la fila.

    `keys` son las columnas del UNIQUE. Solo se actualizan las columnas
    presentes en `row`, así una re-ingesta no borra lo que agregó otro paso
    (por ejemplo, el producto_id que puso el matcher).
    """
    cols = list(row)
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
    conflict = ", ".join(keys)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT ({conflict}) DO " + (f"UPDATE SET {updates}" if updates else "NOTHING"))
    conn.execute(sql, [row[c] for c in cols])
    where = " AND ".join(f"{k}=?" for k in keys)
    pk = "renglon_id" if table == "comparaciones" else "id"
    found = conn.execute(f"SELECT {pk} FROM {table} WHERE {where}", [row[k] for k in keys]).fetchone()
    return found[0] if found else 0


def start_run(conn: sqlite3.Connection, tipo: str, parametros: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO corridas (tipo, parametros, started_at, estado) VALUES (?, ?, ?, 'corriendo')",
        (tipo, json.dumps(parametros or {}, ensure_ascii=False), now_iso()))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, estado: str, n_items: int = 0,
               mensaje: str = "") -> None:
    conn.execute(
        "UPDATE corridas SET finished_at=?, estado=?, n_items=?, mensaje=? WHERE id=?",
        (now_iso(), estado, n_items, mensaje[:2000], run_id))
    conn.commit()
