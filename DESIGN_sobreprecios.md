# DESIGN — sobreprecios

Documento operativo: qué hace cada pieza, dónde vive, decisiones con fecha y por qué, cómo operarla y sus
límites. Los planes en borrador viven en `_scratch/` (no se commitean); lo que sobrevive se escribe acá.

## 1. Mapa

| Pieza | Archivo | Qué hace |
|---|---|---|
| Config | `config/settings.yml`, `config/productos.yml` | Parámetros de comparación, perfiles LLM, catálogo canónico |
| Base | `sobreprecios/db.py` | Esquema SQLite + migraciones idempotentes (`MIGRATIONS`) |
| Unidades | `sobreprecios/units.py` | Tamaños de envase, packs, montos en pesos (formato AR y SEPA) |
| Catálogo | `sobreprecios/catalog.py` | Productos canónicos y matcher por reglas (incluye/excluye) |
| LLM | `sobreprecios/llm.py` | JSON con schema, cache, perfiles gratis/pago, RPM, tope de gasto |
| BO 1ª secc. | `sources/boletin_oficial.py` | Búsqueda por texto + descarga de avisos (cache inmutable) |
| BO 2ª secc. | `sources/bo_sociedades.py` | Avisos societarios: autoridades, socios, domicilios |
| COMPR.AR | `sources/comprar.py`, `pipeline/comprar_import.py` | Procesos, renglones, ofertas (cuadro comparativo) y adjudicación (dictamen); público, httpx puro |
| Registro de Sociedades | `sources/registro_sociedades.py` | CUIT, constitución, domicilios, actividades (todas las provincias) |
| Precios | `sources/indec.py`, `sources/precios_hoy.py`, `sources/legacy_quijotes.py` | INDEC/IPC, foto diaria, dataset 2021 |
| Extracción | `pipeline/extract.py` | Renglones adjudicados: regex → LLM, validación de montos |
| Macheo | `pipeline/match.py` | Producto canónico + precio por unidad base (renglones y mercado) |
| Comparación | `pipeline/compare.py` | Referencia, sobreprecio, confianza, semáforo, evidencia |
| Investigar | `sobreprecios/investigar.py` | Caso puntual: normaliza links, extrae, releva precios de hoy, compara |
| Empresas | `sobreprecios/empresas.py` | Hechos societarios fechados |
| Reportes | `sobreprecios/reports.py` | HTML/XLSX/CSV con metodología y limitaciones |
| Goldens | `sobreprecios/goldens.py` | TSV de validación manual en `_scratch/licitaciones/` |
| Normalización | `sobreprecios/normalize/` | CUIT (dígito verificador), razones sociales, personas, organismos; `entidades_alias` |
| Archivo SEPA | `sources/sepa_archivo.py` | Volcados diarios SEPA 2024-08 → hoy (archivo comunitario preciazo), mediana por cadena |
| Compuerta | `sobreprecios/revision.py` | "publicable" atado a una huella del cálculo; se revoca solo |
| PDFs | `sobreprecios/pdf_text.py` | pdfplumber por columnas → OCR → LLM (benchmark 2026-10-11) |
| Juez externo | `auditoria/juez.py` | Auditoría LLM fuera del programa, solo lectura, TSV + reglas sugeridas |
| Simulador | `tests/e2e/`, `scripts/simular.py`, `simular.bat` | Recorridos de usuario reales en el navegador: compuerta antes de entregar |
| Webui | `sobreprecios/web/` | FastAPI + Jinja; `soft-nav.js` (refresco en el lugar) y `_background.py` (jobs) |

## 2. Decisiones

- **2026-10-11 · Fuente de precios pagados**: Boletín Oficial 1ª sección (resoluciones que adjudican). Trae
  proveedor, CUIT, cantidad, marca y precio unitario en 2020-2022; desde 2024 casi solo totales y el número de
  proceso → COMPR.AR es necesario para lo reciente. La 3ª sección y los CSV de la ONC no tienen precio por renglón.
- **2026-10-11 · Referencia de mercado** (jdemonasterio): misma marca si la norma la nombra; si no, mínimo de mercado
  normalizado por unidad base sin importar marca; envase exacto (±15 %) si la norma lo indica, si no banda 2,5×.
  Orden: misma marca → mínimo mayorista → mínimo supermercado → P25/mediana → INDEC. Configurable en
  `comparacion.referencia_principal`.
- **2026-10-11 · Higiene antes del mínimo**: outliers < 0,4 × mediana fuera; exclusiones globales (mascotas);
  nombres SEPA (`PAQ-500-gr.`) y packs (`N34 x500g` no es un pack); el nombre manda sobre la "presentación" de la
  fuente (el espejo SEPA trae "0.5 gr." para 500 g). Motivo: con criterio "mínimo", un dato sucio se vuelve la vara.
- **2026-10-11 · Costo financiero**: se descuenta inflación mensual (IPC del último mes publicado) × `plazo_pago_meses`.
- **2026-10-11 · Semáforo conservador**: confianza baja nunca es rojo; ratio > 4× = "revisar unidad".
- **2026-10-11 · Monto conjunto**: si una norma da un monto por proveedor para varios renglones, se reparte con
  precio promedio ponderado solo si están en la misma unidad, y queda la alerta `precio_promedio_del_grupo`.
- **2026-10-11 · LLM**: perfil `gratis` por default (todo es información pública): Gemini `flash-lite-latest` con
  10 pedidos/min y reintentos de hasta 5 min ante 429; cuota diaria agotada = se corta y la próxima corrida sigue.
  Perfil `pago`: gpt-6-luna (~USD 0,001 por aviso).
- **2026-10-11 · Empresas como hechos fechados**: `empresa_hechos` nunca se pisa; una designación de directorio
  nueva cierra todo el directorio anterior; domicilio y razón social, uno vigente.
- **2026-10-11 · Precios históricos**: no existe archivo público 2023-2026 por producto; se acumula una foto diaria
  desde hoy (`precios-hoy`). INDEC mensual (2017-2026) como referencia oficial genérica.
- **2026-10-11 · Variables de entorno aisladas**: solo las del proyecto (`.env.example`).
- **2026-10-11 · COMPR.AR sin navegador**: todo el flujo (búsqueda → detalle → cuadro comparativo → dictamen) se
  reproduce reenviando los campos del formulario WebForms con httpx; el token `qs` es determinístico por proceso,
  así que el HTML de un proceso adjudicado se cachea para siempre. Un proceso del BO que cita un número de COMPR.AR se
  completa en el MISMO proceso (una compra = una fila).
- **2026-10-11 · Precios 2024-2026 = archivo SEPA comunitario**: preciazo (@esoesnulo) archiva cada volcado
  oficial en un bucket público (índice en github.com/catdevnull/sepa-precios-metadata). Se bajan solo los días
  cercanos a las compras + uno por mes. Mercado Libre, Rappi y PedidosYa no tienen historia ni API pública
  (verificado). 2022-2023 sigue sin fuente por producto.
- **2026-10-11 · Decisiones del LLM prime (pendientes de check de jdemonasterio, ver _scratch/licitaciones/DECISIONES_prime.md)**:
  rojo exige mínimo corroborado por ≥2 familias de fuentes independientes; el dictamen es recomendación (no
  adjudicación) y las OC son contrato (no pago); uniones de empresas solo por CUIT válido; organismos con el
  nombre de la fecha (el SAF no une renombres); nada sale sin "publicable".
- **2026-10-11 · Proceso 46-0004-LPU25 (regresión de paginación)**: la primera versión marcó por error a un
  adjudicatario con "OC sin recomendación" por leer solo la página 1 del dictamen. Regla: toda grilla de COMPR.AR se
  lee completa (paginación) y hay control de completitud por proceso (test de regresión con el caso).
- **2026-10-11 · Indicador de ofertas**: ganadora vs oferta más baja del mismo renglón. No depende de precios de
  mercado. La más baja pudo ser desestimada con razón: el webui remite al dictamen.
- **2026-10-11 (noche) · Comparador v2 — decisiones del LLM prime P16-P23** (pendientes de check, detalle en
  `_scratch/licitaciones/DECISIONES_prime.md`; todas revertibles con flags en `config/settings.yml → comparacion`):
  - Referencia principal = **mínimo de mercado de ambos canales** (`min_mercado`): el pool mayorista es delgado y 1-2
    observaciones raras escondían sobreprecio. Dos brechas siempre visibles: vs mínimo observado (decide
    amarillo/verde) y vs **mínimo corroborado** (`ref_corroborada`: el menor máximo de un par de observaciones de
    grupos comerciales distintos a ±2 %; un scraper y el SEPA de la misma cadena son el mismo grupo). Solo la
    corroborada da rojo, y tiene que superar +20 % con 2 meses de plazo (`plazo_escenarios_rojo_meses`, factor
    compuesto).
  - **Fecha as-of**: por serie (fuente + descripción) la última observación ≤ fecha en ±15 días; una posterior solo si
    no hay anterior, rotulada y sin corroborar. Cada precio se lleva al mes de la compra con el IPC del mes exacto; si
    falta el índice no se sustituye (queda rotulado). El rojo se bloquea si cambia con/sin ese ajuste. Fuera de la
    ventana: contexto, nunca rojo. Primero fecha, después envase.
  - **Atributos críticos** (variantes): sin defaults; estados verificado / ambiguo / desconocido / no_exigido
    (ficha SIByS que no menciona la variante: interpretación de Claude, a chequear). Desconocido o ambiguo = gris
    `atributo_desconocido`; variante exigida sin mercado en la fecha = gris `sin_referencia_variante`.
  - **Envase exigido estricto**: si la compra dice el envase y el mercado no lo tiene en la fecha = gris
    `sin_referencia_presentacion` (no se relaja a la banda). Contenido desconocido no es compatible.
  - **INDEC solo nunca es rojo** y fuera de presentación comparable no es referencia. Multipack: se compara la unidad
    de adentro. `monto_inconsistente` bloquea el rojo. Comparaciones se reemplazan íntegras al recalcular.
  - Ajustes de catálogo del prime en `config/productos_ajustes.yml` (capa aparte sobre `productos.yml`, borrar una
    entrada la desactiva); re-macheo total del mercado desmachea lo que ya no corresponde (encontró "LAVANDINA X5"
    —marca— cargada como 5 l: el +167 % del renglón 2061 era falso).
  - COMPR.AR: `renglones.fuente_fragmento` guarda la fila del dictamen + el renglón del pliego; ganadora = la
    alternativa con el precio del dictamen (empate = pendiente); re-importar conserva los ids de renglones.
  - Formas exactas de fideos con `compatibles` (una compra "guisero" acepta cualquier forma corta); variantes por frase
    contigua y por campo SIByS; envase exacto ±3 % (`tolerancia_presentacion`); `atributo_obligatorio` (lavandina,
    detergente).
  - Efecto (180 renglones comparables): antes 5 rojo / 54 amarillo / 81 verde / 48 gris; ahora 8 / 19 / 28 / 125. Los
    grises suben a propósito: el sistema dice "no sé" en vez de comparar peras con manzanas. Juez (muestra
    estratificada): comparación de 2 ok / 7 error / 9 dudoso a 14 / 6 / 0.

- **2026-10-12 · Montos comparables entre años (pedido de jdemonasterio)**: con inflación y devaluación, $1.000 de
  2021 y de 2024 no se pueden sumar ni comparar. El webui (licitaciones, analítica, producto) tiene "Montos en":
  pesos de cada fecha / pesos reales (monto × IPC del mes base ÷ IPC del mes del monto; IPC nivel general INDEC, base
  = último mes publicado) / dólares (monto ÷ cotización de venta de ESE día, contado con liqui por defecto). Cada
  renglón se convierte con su fecha mediante una función SQL `conv()` registrada por conexión
  (`normalize/moneda.py`). Cotizaciones diarias desde 2016 de ArgentinaDatos (oficial, mayorista, blue, MEP, CCL;
  respaldo Bluelytics) en `indices` como `usd_<casa>`; se actualizan con `cotizaciones`, `indec`, `setup` y
  `pipeline`. El semáforo NO cambia (ya compara a la misma fecha). Validación: `validation_moneda_*.tsv`.

- **2026-10-12 · Higiene del mercado: envase implausible y cuarentena**: el grupo "arroz" del archivo SEPA traía
  snacks de 80 g, condimentos de 25 g, alfajores y sopas (más caros por kg: no bajaban el mínimo, pero arruinaban
  medianas y gráficos). (1) Un envase < `contenido_minimo_factor` (0,3) × tamaño de referencia del producto no cuenta
  como ese producto (`match_metodo='envase_no_plausible'`, ~23 mil observaciones). (2) Cuarentena
  (`pipeline/cuarentena.py`, columna `precios_mercado.cuarentena` con el motivo, no se borra): envase a menos de $5
  (Comodín reportó $1,00 en todo el 19/08/2024) o < 0,3× / > 5× la mediana del mismo producto, mes y TAMAÑO de envase
  (precios viejos sin actualizar: fideos 500 g a $47 en 2024). Se recalcula con cada macheo de mercado; los
  apartados no entran al comparador ni a los gráficos. Semáforos: sin cambios (el comparador ya filtraba por envase).

## 3. Cómo operar

| Tarea | Comando / lugar |
|---|---|
| Primera vez | `instalar.bat` o `python -m sobreprecios.cli setup` |
| Dólar e IPC | `python -m sobreprecios.cli cotizaciones` / `indec` · botón "Actualizar IPC, precios INDEC y cotizaciones" |
| Precios del día | botón en Datos y fuentes · `precios-hoy` · `actualizar_datos.bat` en el Programador de tareas |
| Buscar adjudicaciones | Datos y fuentes → "Buscar en el BO + …" · `pipeline --desde --hasta` |
| Caso puntual | ＋ Investigar · `investigar "<link>"` |
| Cambiar criterio | `config/settings.yml` → `comparacion` y después "Recalcular sobreprecios" |
| Agregar un producto | `config/productos.yml` (incluye/excluye/busqueda/indec) → "Asignar productos" → "Recalcular" |
| Sin IA | `LIC_LLM_OFFLINE=1` |
| Validar | `python -m sobreprecios.cli goldens` → TSV en `_scratch/licitaciones/` |
| Normalizar entidades | `python -m sobreprecios.cli normalizar` (también corre dentro del pipeline) |
| Auditar con juez | `python -m auditoria.juez --modelo <modelo> --n 60 --max-usd 5` → `_scratch/licitaciones/auditoria/` |
| Auditar casos (rojos + cambios) | `python -m auditoria.juez --modelo <modelo> --modo casos --n 45 --max-usd 2.5 --ids 1761,1787` |
| Antes de entregar | `simular.bat` / `python scripts/simular.py`: 14 recorridos de usuario tienen que dar OK |

## 4. Tests

`python -m pytest -q`: parser de unidades y montos, catálogo (casos reales 2021 y SEPA), extracción con fixture real
(MDS Res. 735/2021), reparto de montos conjuntos, comparador (ventana, IPC, envase, marca, plausibilidad), gateway LLM
(cache, cuota, perfiles), normalización de links, hechos fechados, sociedades (fixtures BO 2ª sección), claves.

## 5. Límites conocidos

- COMPR.AR: el filtro de fechas de la búsqueda se aplica del lado nuestro (el del sitio no está reverseado);
  PDFs de pliegos/anexos sin descarga directa.
- Mayorista vivo: solo Maxiconsumo (sucursal Moreno, sin login). Supermercados: AMBA + espejo SEPA por provincia.
- BO 2ª sección solo cubre sociedades con sede en CABA; avisos anteriores a ~2012 son páginas escaneadas (baja confianza).
- Presentaciones INDEC de yerba (500 g) y leche en polvo (800 g) inferidas, no confirmadas.
- Archivo SEPA con huecos (sin volcados entre 2026-03-16 y 2026-07-01): compras de abril-junio 2026 solo tienen precios
  posteriores → no pueden ser rojas (regla as-of).
- Variantes por bolsa de palabras: no entiende negaciones ("sin palo con menta" queda ambiguo) ni atributos
  coexistentes (fortificación + formulación); el prime pidió parseo por campo — backlog.
- datos.gob.ar / datos.produccion.gob.ar no respondieron desde la red de desarrollo.
