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
| COMPR.AR | `sources/comprar.py`, `pipeline/comprar_import.py` | Procesos, renglones, ofertas (cuadro comparativo), adjudicación (dictamen), actos administrativos, ofertas desestimadas con causa y plazo de pago del pliego (regex, casi siempre sin dato); público, httpx puro |
| Registro de Sociedades | `sources/registro_sociedades.py` | CUIT, constitución, domicilios, actividades (todas las provincias) |
| Precios | `sources/indec.py`, `sources/precios_hoy.py`, `sources/legacy_quijotes.py` | INDEC/IPC, foto diaria, dataset 2021 |
| Precios de programa | `sources/precios_programa.py`, `config/precios_programa.yml` | Precios Cuidados/Justos/Máximos recuperados de prensa (PDF, Infogram, texto diario): fallback de mercado |
| Validación de programa | `sobreprecios/validaciones_programa.py` | Revisión humana (webui `/precios-programa`) + import de un juicio LLM externo por TSV |
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
| Revisión humana | `sobreprecios/revision_humana.py` | Cola de casos grises/rojos/equivalencias/juez, correcciones manuales por campo, validación de referencias de mercado y validación rápida por renglón; proxy de documentos (`/doc`) |

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
    grupos comerciales distintos a ±5 % (`corroboracion_tolerancia`, P1); un scraper y el SEPA de la misma cadena son el mismo grupo). Solo la
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

- **2026-10-12 · Actos administrativos, ofertas desestimadas y plazo de pago (COMPR.AR)**
  (pedido de jdemonasterio — backlog #37). Verificado EN VIVO contra 6 procesos 2018-2026
  (14/2-0001-LPR19, 14/1-0424-CDI18, 132/61-0010-CDI19, 21-0001-LPU22, 14/2-0031-LPU22,
  80-0014-LPU25), viejos y nuevos por igual:
  - **Actos administrativos** (autorización de pliego, autorización de llamado, adjudicación y
    OC) SÍ aparecen en VistaPreviaPliegoCiudadano en los 6 procesos, con número y fecha, en una
    tabla con un id de control DECLARADO (`UC_ActosAdministrativos`, no autonumerado por
    DevExpress: estable entre versiones del sitio y entre procesos viejos/nuevos). Van a
    `comprar_actos`. Pero NO hay PDF descargable sin sesión: el botón "Ver" es un postback que
    abre un visor de GDE que no resolvió sin login al probarlo de nuevo (mismo límite que
    acta/dictamen en PDF, ya documentado). Decisión (pedido explícito de jdemonasterio: "en vez
    de bajar el acto final, poné el link para que lo abra una persona"): `comprar_actos.url`
    apunta al proceso en COMPR.AR con un fragmento (`#ctl00_CPH1_UCVistaPreviaPliego_UC_ActosAdministrativos_lblTitulo`,
    `comprar.ACTOS_ANCHOR_ID`) que salta directo a la sección — sin guardar ni servir PDFs
    (el webui los muestra como "Ver documento ↗"; otro agente del backlog construye el visor
    genérico `/doc?url=`).
  - **Ofertas desestimadas, con causa**: SÍ están en el dictamen (sección aparte de "Ofertas
    recomendadas", no paginada junto a esa grilla). `sources.comprar.parse_ofertas_desestimadas_html`
    las lee por renglón+proveedor; `ofertas.estado` (admitida/desestimada) y
    `ofertas.causa_desestimacion` quedan en la base, más el total en `procesos.ofertas_desestimadas_n`.
    `comprar_import.brecha_ofertas` ahora dice si la oferta más baja estaba desestimada y por qué
    (`oferta_min_desestimada`, `oferta_min_causa`), para que el webui no insinúe sobreprecio
    sobre una oferta que no cumplía el pliego.
  - **Plazo de pago** (cuánto tarda el Estado en pagarle al proveedor) NO aparece como campo
    estructurado en NINGUNO de los 6 procesos probados, viejos ni nuevos: el Pliego Único de
    Bases y Condiciones Generales (Decreto 1030/2016) lo fija a nivel nacional en un documento
    GEDO común a casi todo proceso (ej. `DI-2024-79130471-APN-ONC#JGM`), no descargable sin
    sesión. Los campos que SÍ están en la página y no hay que confundir: "Plazo mantenimiento de
    la oferta" (validez de la oferta), "Fecha estimada del inicio del contrato" (plazo de
    ENTREGA) y "Duración del contrato". `sources.comprar.parse_plazo_pago` solo rescata el caso
    en que el plazo de pago aparece como texto libre (regex con ventana de proximidad a
    "pago"/"factura"/"abonar", conversión hábiles→corridos ×1,4); en los 6 procesos probados da
    "sin dato" en los 6 — es el resultado correcto, no un bug. P2 (jdemonasterio, confirmado):
    "perfecto, usá el plazo de pago de la licitación" — por eso `pipeline.compare.compare()`
    acepta `plazo_pago_dias` opcional: cuando se conoce, reemplaza el supuesto genérico
    (`plazo_pago_meses`) en el escenario amarillo y actúa como PISO (nunca acorta) del escenario
    rojo. Con "sin dato" (el caso de hoy) el comportamiento es idéntico al de antes.
  - Regresión real encontrada y corregida: una ventana de proximidad simétrica a la palabra
    "pago" producía un falso positivo ("Plazo mantenimiento de la oferta: 60 Días corridos"
    seguido, en el texto plano de la página, por la label no relacionada "Requiere pago" —  si
    hay que pagar para RETIRAR el pliego, no cuánto tarda el Estado en pagar). Se corrigió con
    una ventana asimétrica (90 caracteres antes, 20 después) más la exclusión explícita de
    "requiere pago"; test de regresión en `tests/test_comprar.py`.

- **2026-10-12 · Higiene del mercado: envase implausible y cuarentena**: el grupo "arroz" del archivo SEPA traía
  snacks de 80 g, condimentos de 25 g, alfajores y sopas (más caros por kg: no bajaban el mínimo, pero arruinaban
  medianas y gráficos). (1) Un envase < `contenido_minimo_factor` (0,3) × tamaño de referencia del producto no cuenta
  como ese producto (`match_metodo='envase_no_plausible'`, ~23 mil observaciones). (2) Cuarentena
  (`pipeline/cuarentena.py`, columna `precios_mercado.cuarentena` con el motivo, no se borra): envase a menos de $5
  (Comodín reportó $1,00 en todo el 19/08/2024) o < 0,3× / > 5× la mediana del mismo producto, mes y TAMAÑO de envase
  (precios viejos sin actualizar: fideos 500 g a $47 en 2024). Se recalcula con cada macheo de mercado; los
  apartados no entran al comparador ni a los gráficos. Semáforos: sin cambios (el comparador ya filtraba por envase).

- **2026-10-12 · Módulo de revisión humana (MEJORAS #34, pedido de jdemonasterio)**: un periodista nunca ve un
  caso sin que un humano lo haya mirado — esto es la interfaz para mirarlo. Qué hace, dónde vive, cómo se
  desactiva:
  - **Cola (`/revision`)**: `revision_humana.cola()` sincroniza la tabla `revisiones_caso` con lo que el
    comparador y el juez externo encuentran HOY (gris con `motivo_codigo` en atributo_desconocido /
    sin_referencia_variante / sin_referencia_presentacion / sin_datos / revisar_unidad / equivalencia_por_verificar,
    todos los rojos, y veredicto del juez error/dudoso) pero **nunca pisa una decisión ya tomada**: un caso marcado
    "descartado" no vuelve a "pendiente" aunque el comparador lo siga viendo gris (mismo criterio que el gate de
    "publicable" en `sobreprecios/revision.py`, que se revoca solo pero nunca se reafirma solo).
  - **Correcciones manuales por campo (`/revision/<renglon_id>`)**: `correcciones_renglon` guarda producto,
    variedad, envase (contenido + unidad), precio unitario, cantidad, fecha de comparación y marca; la más
    reciente por campo es la vigente, "Deshacer" borra esa fila puntual (vuelve el dato automático). `pipeline/
    compare.run()` llama a `revision_humana.aplicar_correcciones()` ANTES de armar la comparación — `compare()`
    (el núcleo puro) sigue sin saber que hubo una corrección manual, solo recibe el dato ya resuelto — y
    recalcula en un job de background (`revision_<renglon_id>`) que refresca la página en el lugar (soft-nav).
    La variedad tiene un valor sentinela `__no_se__` ("No sé cuál es la variedad"): guarda que un humano miró sin
    forzar ningún valor al comparador (el caso sigue resolviéndose solo, puede seguir gris).
  - **Validación de referencias de mercado (P16, pedido de jdemonasterio)**: en la tabla de evidencia de cada
    caso, el humano marca cada observación como equivalente / no es el mismo producto / precio o tamaño mal
    leído / no sé, con una nota. Como una observación de `precios_mercado` no tiene un id estable visible en
    `evidencia_json` (es un snapshot JSON armado por el comparador, no una FK), la clave es (fuente, fecha,
    descripción original) — `revision_humana.clave_observacion()` saca el sufijo que el comparador le agrega al
    ajustar por IPC antes de comparar. Las marcadas "no es el mismo producto" / "precio o tamaño mal leído" se
    excluyen del cálculo de ESE renglón (`aplicar_exclusiones_mercado()`, tabla `validaciones_mercado`); otros
    renglones del mismo producto no se tocan.
  - **Validación rápida in-line (`/licitacion/<id>`)**: por renglón, 3 partes (extracción / producto y variedad /
    referencia de mercado) con ✓ OK · ✗ No · ? No sé y un comentario, sin abrir la revisión completa
    (`validaciones_renglon`, última por `(renglon_id, parte)`). El resumen ("validado por…" / "con objeciones" /
    "revisión parcial") se muestra junto al semáforo. Un botón "Validé esta licitación" en la misma tarjeta de
    revisión de siempre marca el proceso `revisado` (reusa `sobreprecios/revision.py`, no es una tabla nueva).
  - **Juez externo importado (`/revision/juez`)**: `auditoria/juez.py` SIGUE siendo de solo lectura sobre la
    base — el que escribe es este módulo, a pedido explícito (`python -m sobreprecios.cli importar-juez <carpeta>`
    o el botón del webui), nunca automático. Lee `items.tsv` + `resumen.md` de una corrida y los vuelca en
    `juez_corridas` / `juez_veredictos`; re-importar la misma carpeta reemplaza sus veredictos enteros.
  - **Proxy de documentos (`/doc`)**: allowlist estricta de host (boletinoficial.gob.ar, comprar.gob.ar y sus
    `www.`), solo https, re-chequea el host en CADA redirect (nunca confía en el host inicial), tope de 30 MB y
    30 s de timeout, cache de 1 día en `cache_dir("docs")` (no es un archivo permanente del proyecto). Para
    mostrar el PDF/HTML inline en un `<iframe>` sin bajarlo a mano ni pegarle directo a la fuente desde el
    navegador del revisor.
  - **Desactivar**: no hay flag — es el gate de revisión en sí. Para no verlo, no entrar a `/revision` ni a
    `/licitacion/<id>`; el pipeline sigue funcionando igual sin que nadie corrija ni valide nada (correcciones y
    validaciones son estrictamente aditivas, nunca requeridas).
- **2026-10-12 · Precios de programa (Precios Cuidados/Justos/Máximos) como FALLBACK de mercado**
  (jdemonasterio): en el mercado libre las empresas suelen querer cobrar MÁS que el precio pactado/techo, no menos
  — funcionan más como un PISO que como el mínimo real de góndola. No hay Boletín Oficial para esto (las
  resoluciones remiten a un Anexo que nunca se publica, ver `_scratch/licitaciones/historico_precios/RESUMEN.md`):
  la única fuente es prensa que reproduce el listado oficial de la Secretaría de Comercio. Ediciones registradas
  en `config/precios_programa.yml` (id, programa, vigencia, formato, urls, notas de qué se probó);
  `sources/precios_programa.py` las baja (con caché y reintento por URL), parsea tres formatos de fuente
  (`pdf_texto`: columnas detectadas por posición de palabra, no por cantidad fija — una edición trae
  Descripción/Marca/Proveedor/Precio y otra solo Producto/Precio; `infogram`: el widget embebido vía Wayback trae
  su JSON de datos tal cual dentro del HTML; `texto_diario`: una lista `<li>` o un párrafo separado por `;`, mismo
  parser para los dos porque el filtro real es el catálogo) y machea contra `config/productos.yml` igual que
  `precios_hoy.py`. Cada fila se guarda con `canal="programa"` y se REPLICA el 1 y el 16 de cada mes dentro de su
  vigencia (el comparador solo mira ±15 días de la fecha de la compra); `obs_key` es un hash del texto original
  (idempotente: reimportar la misma edición no duplica).
  - **Fallback en el comparador** (`pipeline/compare.py`, cambio mínimo): `select_observations` descarta las
    observaciones `programa` si hay cualquier mayorista/minorista en la fecha; si no hay ninguna, se tratan como
    minoristas (`ref_min_minorista` / `min_mercado`) y el motivo agrega la nota "referencia: precio de programa
    `<programa>` (`<edición>`)". `familia_fuente("programa:<programa>:<edición>")` devuelve `"programa:<programa>"`
    (una familia por PROGRAMA, no por edición): dos ediciones de Precios Cuidados nunca se corroboran entre sí: un
    programa solo nunca da rojo (la regla general de 2 familias independientes sigue igual; Precios Cuidados +
    Precios Justos SÍ son familias distintas).
  - **Cobertura lograda** (productos del catálogo con al menos una edición, de 34 totales): 2020 (ene, Cronista,
    18), 2021 (ene, Infogram vía Wayback, 24), 2022 (ene, PDF Drive 4 columnas CABA, 27; oct, PDF Drive 2 columnas
    nacional, 24), 2022-11→2023-03 (El Economista, Precios Justos AMBA, 11) — 28/34 productos cubiertos por al
    menos una edición; nunca aparecieron en ninguna: caballa, carne vacuna en conserva, merluza, picadillo,
    porotos, resma A4 (no estaban en esas canastas puntuales, no es límite del parser).
  - **Búsqueda de ediciones 2023** (pedido explícito: "para 2023 no hay otra fuente, el precio de programa ES la
    fuente"): abr-dic 2023 queda sin cerrar — ~12 búsquedas y ~16 notas de prensa revisadas, todas con links de
    Drive caídos o sin el listado embebido (detalle y un candidato de bajo esfuerzo para el backlog —
    LA NACIÓN 2023-10-31, 3 productos en prosa narrativa— en las notas de `config/precios_programa.yml`).
  - **Validación manual: webui `/precios-programa`** (nueva solapa): lista cada edición con cuántas filas tiene y
    cuántas ya se revisaron; al abrir una, cada fila muestra el texto original (con el link a la fuente), el
    producto/variante asignado, el precio por unidad base, el veredicto de una auditoría LLM externa si existe
    (automático, nunca excluye solo) y tres botones para la revisión humana (✓ Ok / ✗ No / ? No sé + comentario),
    guardados sin recargar la página (soft-nav.js). Tabla `validaciones_programa` (`fuente`, `obs_key`, `autor`,
    `veredicto`, `nota`; `UNIQUE(fuente, obs_key, autor)` — guardar de nuevo pisa la marca anterior del mismo
    autor, no deja historial). Solo un ✗ de autor="humano" excluye la fila del comparador
    (`pipeline/compare.load_observations`, `NOT EXISTS` contra `validaciones_programa`): "un resultado falso es
    peor que ninguno", el juez LLM solo no alcanza. Marcar ✗ recalcula `compare.run()` en el momento.
  - **Import de un juez externo por TSV** (`validaciones_programa.importar_juez_tsv`, CLI `precios-programa
    --juez <tsv>`): empareja por `fecha_publicacion` → edición (`config/precios_programa.yml`) y por texto
    normalizado (sin precio, sin el sufijo `[vigente...]`) contra `precios_mercado`; filas del TSV sin producto
    del catálogo (se descartaron en la importación) no tienen con qué emparejar y quedan afuera, no es un error.
    Confirmado con un lote de 55 filas (2020-2022, `_scratch/licitaciones/historico_precios/
    validacion_precios_cuidados_final_20261012-1213.tsv`): 44/55 emparejaron; detectó dos errores reales de la
    fuente que el import muestra tal cual (no se "arreglan" en el parser): "Leche Larga Vida Entera Sancor x 1000
    cc $68" (2021) es en realidad el precio de la descremada, y confirmó que "Yerba Mate con Palo Ecopack Cruz de
    Malta 500 Gr" (2022) es $224,67 (la muestra manual original lo había marcado "no legible", el parser por
    posición de palabra sí lo recuperó bien).
  - **Límite conocido del PDF 2022-01**: algunas filas consecutivas de "Aceite Girasol/Mezcla" en el PDF oficial
    directamente OMITEN la palabra "Aceite" en la columna Descripción (confirmado mirando las palabras y su
    posición: no es un error de lectura de columnas, el PDF fuente dice literalmente "GIRASOL 900 ml Cocinero").
    El catálogo correctamente no matchea "girasol" solo (exige la frase "aceite girasol"): ~3 % de filas de esa
    edición se pierden por diseño en vez de adivinar — ver `tests/test_precios_programa.py` y la nota en
    `config/precios_programa.yml`.

- **2026-10-12 · Política de precios por período y precio INFERIDO (jdemonasterio, MEJORAS #46)**: 2020-2023 →
  listas de Precios Cuidados/Justos (fallback de mercado); ene-jul 2024 (hueco: el archivo SEPA empieza en
  ago-2024) → si no hay NINGÚN dato anterior a la compra y sí uno posterior a <= `gap_inferencia_dias` (240), se usa
  el primero que aparece llevado hacia atrás con el IPC del mes exacto, rotulado "PRECIO INFERIDO" en el motivo,
  confianza baja, nunca rojo. Si existe un dato anterior (aunque viejo) no se infiere: rige `gap_maximo_dias`.

- **2026-10-12 · Decisiones del owner sobre el catálogo que PISAN al LLM prime** (jdemonasterio, revierte
  donde difiere lo que el prime había propuesto el 2026-10-11; detalle y motivo por producto en
  `config/productos_ajustes.yml`, campo `decision_jdemonasterio`):
  - **leche_polvo_entera**: default "comun" restituido ("es leche en polvo entera y punto"; el prime lo había
    dejado en `null`). La leche FORTIFICADA (Ley 25.459: hierro, zinc y vitamina C/ácido ascórbico) no se vende
    al público: se compara contra la leche en polvo COMÚN de góndola con una advertencia visible en el motivo.
    Mecanismo **genérico**, no hardcodeado a leche: `Producto.compatibles` (`catalog.py`) ya permitía mapear
    una variante de la compra a otra(s) variante(s) del mercado (lo usaba `fideos_secos` para "guisero acepta
    cualquier forma corta"); ahora también mapea `fortificada_* -> [comun]`. Se agregó `Producto.
    nota_equivalencia` (variante -> texto) que `pipeline.compare.compare()` suma al motivo cuando esa variante
    se usó (parámetro nuevo `nota_equivalencia`). El semáforo no se relaja ni se endurece: reglas normales.
  - **arroz_blanco** y **harina_maiz**: se BORRAN las variantes por tipo de grano / cocción que proponía el
    prime (`variantes: {}` en el ajuste). Antes `load_catalog()` solo pisaba las variantes del catálogo base si
    el ajuste tenía una lista NO vacía (`if aj.get("variantes")`); un diccionario vacío se ignoraba y dejaba las
    del base puestas — no había forma de "sacar" una variante sin reemplazarla por otra. Se cambió a
    `if "variantes" in aj` (la clave presente, aunque vacía, pisa). Las exclusiones de producto no-arroz/no-polenta
    (parboil, integral, preparados, snacks, saborizada) se mantienen.
  - **harina_trigo_000**: variantes "organica" y "enriquecida"; default "comun" si no dice ninguna.
  - **pure_tomate**: confirmado que salsa y concentrado ya estaban excluidos en `config/productos.yml` (base,
    no en el ajuste) y que eso es seguro para la marca "Salsati": el matcher machea por PALABRA completa
    (`catalog._contains`), nunca por substring, así que el token "salsati" nunca activa la exclusión de la
    palabra "salsa". Default "comun" para el puré sin passata ni tomate amarillo.
  - **lentejas / arvejas_secas / porotos**: lo común es la legumbre seca en bolsa tal como viene — se refuerzan
    las exclusiones de pastas/preparaciones (medallones y hamburguesas ya estaban en las exclusiones globales
    del catálogo).
  - **atun_conserva / caballa_conserva**: solo DOS variantes (al_aceite / al_agua), bajando de 4 (atún:
    corte x líquido) y 4 (caballa: al_natural/aceite_y_agua/en_aceite/salsa_tomate). El corte y la sal no
    definen una variante de precio. Una descripción que menciona los dos líquidos a la vez ("en aceite y
    agua") sigue quedando ambigua de forma NATURAL (la frase "aceite" matchea al_aceite y la palabra "agua"
    matchea al_agua, sin necesitar una tercera variante "mixta" dedicada) — nunca se asume aceite solo.
  - **dulce_leche**: default "pote" (antes `null`); "bolsa" nunca es la presentación licitada (exclusión
    nueva); se agregan las variantes "balde" y "frasco" (antes solo existía "monodosis").
  - **cacao_polvo**: default "azucarado" (antes `null`). **pan_dulce**: default "frutado" (antes `null`).
  - **sal_fina**: default "comun" (básica, antes `null`); se agrega la variante "yodada" junto a "marina".
  - **agua_bidon**: default "no_retornable" (bidón/botellón/botella que se vende y no vuelve).
  - **detergente**: sin cambios — se mantiene `atributo_obligatorio` (valida concentración/dosis).
  - **jabon_tocador**: default "estandar" (antes `null`); confirmado que barra/caja/multipack son
    PRESENTACIÓN (envase), no variante — sin cambio de código, ya se normalizaban por tamaño de envase.
  - **Regla de presentación** (sin cambios, se reconfirma): entre presentaciones comparables se toma el
    MÍNIMO, salvo que la licitación especifique la presentación exacta (entonces se compara solo contra esa,
    ±`tolerancia_presentacion`).
  - `yerba_mate` y `fideos_secos` quedan tal cual (sin default, parseo por campo PALO / formas exactas).

- **2026-10-12 · Comparador v3 — datos de la referencia, impuestos, marca exigida, estado documental, IPC**
  (jdemonasterio, pedido explícito tras la ronda del prime; todo revertible):
  - **IDs de la evidencia** (MEJORAS #27 parcial): `Obs` ganó un campo `id` (precios_mercado.id,
    `pipeline.compare.load_observations` ahora selecciona `id`); `Resultado.obs_ids` guarda
    `{"minimo": [...], "par_corroborado": [...]}` — los ids de `precios_mercado` que FIJAN el valor de
    `ref_principal_valor` (coincide el precio exacto) y los 2 que confirman `ref_corroborada`. Persiste en
    `comparaciones.obs_ids_json` (migración). Listas vacías cuando no hay referencia (gris) o cuando la
    referencia es una mediana que no coincide con ningún id puntual (misma_marca/mediana_*).
  - **Impuestos** (MEJORAS #42): `config/settings.yml -> impuestos.incluye_iva` mapea canal/fuente ->
    si ese precio INCLUYE IVA (mayorista/minorista/programa/oficial/estado: todos `true`, es consumidor
    final en los 5 — SEPA/Precios Claros, Precios Cuidados/Justos/Máximos, INDEC y lo que paga el Estado).
    Puramente documental, `config.incluye_iva(canal)`: el owner decidió que el IVA es el mismo para todos los
    productos/proveedores de este catálogo, así que NO cambia ningún cálculo. Promociones: precio vigente tal
    cual viene de la fuente. Stock y compra mínima: no se consideran (decisión explícita, no un pendiente).
  - **Monto inconsistente en la cola de revisión**: `revision_humana._detectar()` ahora también junta
    renglones con la alerta `monto_inconsistente` (cantidad x precio no cierra con el aviso) con origen
    `"monto"` — ANTES solo entraban si además el semáforo quedaba gris/rojo; como `compare.run()` ya bloquea
    el rojo para estos (`bloquear_rojo`), nadie se enteraba de que falta completar el dato si el semáforo caía
    en amarillo/verde. El color sigue mandando (un rojo con esta alerta, rarísimo, sigue siendo "rojo" en la
    cola). `/revision/<renglon_id>` muestra un cartel sugiriendo completar cantidad/precio unitario en
    "c. Qué entendiste vos"; `/revision` tiene el filtro y el contador nuevos.
  - **Empate de ofertas = se elige una** (`pipeline.comprar_import.ofertas_ganadoras_y_pendientes`): el prime
    había dejado ambas en `pendientes` (ganadora NULL) cuando dos alternativas del MISMO proveedor empataban
    en el precio del dictamen. El owner lo revierte: "si hay empate, elegís una; si está adjudicado, está
    adjudicado" — se marca ganadora la de menor índice (la primera alternativa del cuadro comparativo para
    ese proveedor/renglón), nunca se deja pendiente. `pendientes` queda siempre vacío (se conserva en la firma
    de la función por compatibilidad).
  - **Marca exigida vs. ofrecida vs. "o equivalente"** (`pipeline.extract.parse_marca_exigida`): nueva
    distinción entre `renglones.marca` (la OFRECIDA por el ganador, ya existía) y `renglones.marca_exigida` /
    `marca_equivalente_ok` (lo que el PLIEGO/descripción exige, migración nueva). Se parsea con dos patrones
    conservadores ("un resultado falso es peor que ninguno"): "marca X" (con o sin "o equivalente/similar"
    después) y "tipo X o equivalente/similar" — "tipo" SOLO cuenta como marca cuando viene con "o
    equivalente/similar" en la misma frase; sin eso, "tipo" casi siempre describe otro atributo (ficha SIByS
    "TIPO: GRANO LARGO-FINO", "TIPO: CON PALO") y NO se asume marca (bug real encontrado armando los tests:
    la primera versión leía "tipo" solo y convertía cualquier ficha de medicamentos "TIPO: MONODROGA" en una
    marca exigida falsa). `pipeline.compare.compare()` solo usa `misma_marca` como referencia PRINCIPAL
    cuando `marca_exigida` está seteada y `marca_equivalente_ok` es `False` (sin "o equivalente"); si el
    pliego aceptó un equivalente, o no se encontró ninguna exigencia, se usa el mínimo de mercado de siempre y
    la marca ofrecida queda solo de contexto (`ref_misma_marca`/`ref_n_misma_marca` siguen mostrándose).
    Explicado en el motivo. Se parsea en los dos orígenes de renglones (`pipeline.extract.py`, texto del BO;
    `pipeline.comprar_import.store_detalle`, texto del PLIEGO de COMPR.AR) — el dataset legacy 2021 y los
    renglones ya importados ANTES de este cambio quedan con `marca_exigida` NULL hasta que se vuelvan a
    importar/extraer (recalcular sobreprecios no re-extrae texto, solo recalcula con lo que ya está en la
    base): mientras tanto `misma_marca` simplemente no se usa como principal para esos renglones viejos, que
    es el lado seguro (cae al mínimo de mercado, nunca una referencia inventada).
  - **Estado documental** (MEJORAS #29, `pipeline.comprar_import.actualizar_estado_documental`, se llama al
    final de `compare.run()`): `renglones.estado_documental` y el agregado `procesos.estado_documental`
    (migración nueva) en `dictamen` (COMPR.AR, `extraccion_metodo='comprar'` — es una RECOMENDACIÓN, no una
    decisión) | `adjudicado` (resolución del Boletín Oficial que adjudica, `procesos.fuente='bo_primera'` con
    extracción regex/LLM, no el dataset legacy — es un ACTO ya decidido) | `contratado` (existe una orden de
    compra para ese proveedor en ese proceso, manda sobre dictamen/adjudicado) | `desconocido` (dataset legacy
    sin metadata de origen). Nunca se escribe "pagado": no hay fuente de pagos reales. Visible en
    `/licitacion/<id>` (encabezado "Renglones recomendados en el dictamen / contratados / adjudicados" +
    leyenda) y en los reportes (`reports.py`: columna "Estado documental", y la columna que antes decía
    "Pagó $/u. base" ahora dice "Precio $/u. base"; el prompt que alimenta el resumen LLM también dice
    "cotizó (recomendado/adjudicado/contratado)" en vez de "pagó").
  - **IPC con fecha de publicación y versión, modo "disponible a la fecha"** (MEJORAS #30/#45, P20): tabla
    `indices` gana `fecha_publicacion` y `version` (migración). `sources/indec.py` no tiene una fuente de la
    fecha REAL de publicación de apis.datos.gob.ar/series (la API no trae esa metadata): se ESTIMA como el 15
    del mes siguiente (`fecha_publicacion_estimada`, INDEC publica con ~2 semanas de atraso), y `version` es
    el timestamp de la bajada (se actualiza en cada corrida). `config/settings.yml -> comparacion.ipc_modo`:
    `retrospectivo` (default, comportamiento idéntico al de siempre: usa el IPC definitivo de HOY, aunque se
    haya publicado después de la compra) | `disponible_a_fecha` (el ajuste financiero —inflación del plazo de
    pago— y el ajuste temporal —llevar un precio de mercado de otro mes al mes de la compra— solo usan meses
    de IPC cuya `fecha_publicacion` sea <= la fecha de la compra; probado: una compra del 20 de julio NO usa
    julio, porque julio recién se publica el 15 de agosto). `pipeline.compare.run()` solo carga las fechas de
    publicación (`load_index_publicaciones`) cuando el modo las necesita, así el modo default no paga ningún
    costo ni cambia nada.
  - **IPC trasladado puede dar rojo, con advertencia explícita** (pedido del coordinador, revierte la cautela
    original "fuera de la ventana directa, nunca rojo"): `comparacion.rojo_con_ajuste_ipc_hasta_dias` (default
    120 = `gap_maximo_dias`; poner 15 = `ventana_directa_dias` para la regla estricta de antes) — un precio de
    mercado a más de `ventana_directa_dias` pero dentro de este tope SÍ puede dar rojo si corrobora con 2
    familias y aguanta el plazo de pago, con el motivo diciendo explícitamente "precio de mercado trasladado
    por IPC desde N días (método: I(mes compra)/I(mes observación))" — nunca silencioso. Una observación
    **posterior** a la compra dentro de `ventana_directa_dias` (15 días) ahora SÍ corrobora (antes cualquier
    posterior quedaba excluida sin importar la distancia); más allá de la ventana la cautela se mantiene (no
    corrobora). **Precio de HOY** (flujo Investigar, `investigar.py`, otro agente): el comparador lo reconoce
    automáticamente por la FECHA REAL de la observación (no por un canal/fuente especial —
    `sources/precios_hoy.py` guarda con el mismo canal mayorista/minorista de siempre), lo etiqueta distinto
    en el motivo ("precio de HOY trasladado por IPC a la fecha de la compra") y lo deja corroborar con otra
    observación de HOY aunque la compra sea vieja (no hay riesgo de "promo que todavía no existía": es,
    literalmente, la góndola de hoy) — pero habilitar ROJO a esa distancia sigue exigiendo el override
    explícito `rojo_con_ajuste_ipc_hasta_dias` (vía `comparacion` o el parámetro nuevo `pipeline.compare.
    run(..., rojo_con_ajuste_ipc_hasta_dias=...)`, mismo patrón que `gap_maximo`), nunca automático por
    default. El precio INFERIDO (MEJORAS #46, hueco ene-jul 2024) sigue sin excepción: nunca rojo.

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
| Precios de programa | botón en Datos y fuentes · `precios-programa [--edicion ID] [--juez tsv]` · revisar en "Precios de programa" |
| Sin IA | `LIC_LLM_OFFLINE=1` |
| Validar | `python -m sobreprecios.cli goldens` → TSV en `_scratch/licitaciones/` |
| Normalizar entidades | `python -m sobreprecios.cli normalizar` (también corre dentro del pipeline) |
| Auditar con juez | `python -m auditoria.juez --modelo <modelo> --n 60 --max-usd 5` → `_scratch/licitaciones/auditoria/` |
| Auditar casos (rojos + cambios) | `python -m auditoria.juez --modelo <modelo> --modo casos --n 45 --max-usd 2.5 --ids 1761,1787` |
| Ver actos admin./ofertas desestimadas/plazo de pago de un proceso COMPR.AR | automático: se muestran en `/licitacion/<id>` al importar (sin comando propio) |
| Revisar casos pendientes | `/revision` (cola) → abrir un caso → corregir dato y/o validar referencias de mercado → "Guardar y recalcular" |
| Validar un renglón sin abrir el caso | `/licitacion/<id>` → desplegar el renglón → "Validación rápida" (✓/✗/?) |
| Importar una corrida del juez a la cola | `python -m sobreprecios.cli importar-juez <carpeta>` o botón en `/revision/juez` |
| Mirar un documento (BO/COMPR.AR) sin salir | botón "Ver documento" en `/revision/<renglon_id>` (proxy `/doc`, solo esos hosts) |
| Bajar / validar precios de programa (Precios Cuidados/Justos) | `python -m sobreprecios.cli precios-programa [--edicion ID] [--juez TSV]` · solapa `/precios-programa` |
| Antes de entregar | `simular.bat` / `python scripts/simular.py`: 20 recorridos de usuario tienen que dar OK |

## 4. Tests

`python -m pytest -q`: parser de unidades y montos, catálogo (casos reales 2021 y SEPA), extracción con fixture real
(MDS Res. 735/2021), reparto de montos conjuntos, comparador (ventana, IPC, envase, marca, plausibilidad, plazo de
pago del pliego), gateway LLM (cache, cuota, perfiles), normalización de links, hechos fechados, sociedades
(fixtures BO 2ª sección), claves, COMPR.AR (actos administrativos, ofertas desestimadas con causa, plazo de pago
con regresión del falso positivo "Requiere pago"), revisión humana (`tests/test_revision_humana.py`: correcciones
por campo y su recálculo, la cola no pisa una decisión humana, exclusión de referencias de mercado, resumen de
validación por renglón, parser de `items.tsv`/`resumen.md` del juez, allowlist de host del proxy de documentos incl.
redirect a host no permitido). El simulador (`tests/e2e/`) cubre los journeys de `/revision` y de la validación
in-line en `/licitacion/<id>` con un navegador real, precios de programa (los tres formatos con fixtures, macheo, réplica por
vigencia, idempotencia, fallback y familia en el comparador) y validaciones de programa (guardar/leer, import de TSV
del juez); el simulador también cubre marcar una fila de "programa" ✗ y verla excluida del comparador.

Comparador v3 (2026-10-12): ids que forman la referencia (`obs_ids`), `nota_equivalencia` para variantes
mapeadas por `compatibles` (leche fortificada -> común), misma_marca como principal SOLO con exigencia estricta
(`tests/test_extract.py::test_parse_marca_exigida_*`, incluido el falso positivo real de "tipo" sin
"equivalente" en una ficha de medicamentos), empate de ofertas resuelto a una ganadora
(`test_alternativas_empatadas_elige_una_ganadora`), estado documental derivado (dictamen/adjudicado/contratado/
desconocido, `tests/test_comprar_import.py::test_actualizar_estado_documental_*`) y nunca "pagado" en reportes
(`tests/test_reports.py`), IPC con fecha de publicación estimada y modo "disponible a la fecha"
(`tests/test_indec.py`, `tests/test_compare.py::test_monthly_inflation_disponible_a_fecha_*` y
`test_a_la_fecha_disponible_a_fecha_*`), rojo con precio trasladado por IPC hasta `rojo_con_ajuste_ipc_hasta_dias`
y etiqueta "precio de HOY" para el flujo Investigar (`test_gap_hasta_120_dias_puede_dar_rojo_con_ajuste_por_ipc`,
`test_precio_de_hoy_se_etiqueta_distinto_y_solo_habilita_rojo_con_override`), posterior dentro de la ventana
corrobora (`test_precio_posterior_dentro_de_la_ventana_corrobora_y_puede_dar_rojo`) y más allá de la ventana
sigue sin corroborar (`test_precio_posterior_mas_alla_de_la_ventana_no_corrobora`), y los 17 casos de catálogo
nuevos en `tests/test_catalog.py` (uno por decisión del owner sobre productos).

## 5. Límites conocidos

- COMPR.AR: el filtro de fechas de la búsqueda se aplica del lado nuestro (el del sitio no está reverseado);
  PDFs de pliegos/anexos/actos administrativos sin descarga directa (el botón "Ver" abre un visor de GDE que
  requiere sesión); el webui linkea al proceso con un fragmento en vez de bajar el PDF. El plazo de pago al
  proveedor no es un campo estructurado en ningún proceso probado (2018-2026): lo fija un documento GEDO nacional
  común, no legible sin sesión; `parse_plazo_pago` solo rescata el caso raro de texto libre.
- Mayorista vivo: solo Maxiconsumo (sucursal Moreno, sin login). Supermercados: AMBA + espejo SEPA por provincia.
- BO 2ª sección solo cubre sociedades con sede en CABA; avisos anteriores a ~2012 son páginas escaneadas (baja confianza).
- Presentaciones INDEC de yerba (500 g) y leche en polvo (800 g) inferidas, no confirmadas.
- Archivo SEPA con huecos (sin volcados entre 2026-03-16 y 2026-07-01): compras de abril-junio 2026 solo tienen precios
  posteriores → no pueden ser rojas (regla as-of).
- Variantes por bolsa de palabras: no entiende negaciones ("sin palo con menta" queda ambiguo) ni atributos
  coexistentes (fortificación + formulación); el prime pidió parseo por campo — backlog.
- datos.gob.ar / datos.produccion.gob.ar no respondieron desde la red de desarrollo.
- Validación de referencias de mercado (P16): la clave es (fuente, fecha, descripción original), no un id de
  `precios_mercado` — si dos observaciones del mismo producto comparten exactamente esas 3 cosas (raro, pero
  posible con el espejo SEPA), un juicio humano afecta a ambas. La mejora #27 (clave de serie con EAN) resuelve
  esto de raíz cuando se implemente.
- El proxy de documentos (`/doc`) no persiste nada más que un cache de 1 día: no sirve como archivo para un
  reporte que se vaya a mandar después (para eso, descargar el PDF directo con el link de "Abrir el proceso").
- Precios de programa: abr-dic 2023 sin edición encontrada (ver decisión 2026-10-12); PDF de 2022-01 pierde ~3 %
  de filas de aceite por una omisión de palabra en la fuente misma (ver esa decisión); el PDF nacional de
  2022-01 con ~30.000 precios (12,6 MB) no se incorporó, solo el de CABA.
- `comparaciones.obs_ids_json` ("minimo") resuelve MEJORAS #27 solo para referencias por MÍNIMO: cuando
  `ref_principal` es `misma_marca` o una `mediana_*`, el valor no tiene por qué coincidir con el precio
  EXACTO de ninguna observación puntual y "minimo" queda vacío. La clave de serie sigue siendo
  (fuente, descripción normalizada), no un EAN — la mejora #27 completa (clave con EAN) sigue sin implementar.
- `marca_exigida`/`marca_equivalente_ok` (parseado del texto del renglón/pliego) solo se calcula al EXTRAER o
  IMPORTAR un renglón (`pipeline/extract.py`, `pipeline/comprar_import.py`). Los renglones ya cargados ANTES
  de este cambio quedan con `marca_exigida` NULL hasta que se vuelvan a extraer/importar — "Recalcular
  sobreprecios" (`compare.run()`) NO re-extrae texto, solo recalcula con lo que ya está en la base. Mientras
  tanto, para esos renglones viejos `misma_marca` simplemente no se usa como principal (cae al mínimo de
  mercado): es el lado seguro, nunca una referencia inventada, pero sí un cambio de comportamiento silencioso
  para datos históricos hasta que se vuelvan a importar.
- `indices.fecha_publicacion` es SIEMPRE una ESTIMA (15 del mes siguiente): apis.datos.gob.ar/series no trae
  la fecha real de publicación de cada punto. Si aparece una fuente con la fecha real, debería reemplazar
  `sources/indec.fecha_publicacion_estimada`; hoy el modo `disponible_a_fecha` es tan preciso como esa estima.
