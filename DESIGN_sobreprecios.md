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
| Revisión humana | `sobreprecios/revision_humana.py` | Cola de casos grises/rojos/equivalencias/juez, correcciones manuales por campo, validación de referencias de mercado, validación rápida por renglón, checklist de explicaciones alternativas y el comprobante de cálculo imprimible; proxy de documentos (`/doc`) |
| Formato webui | `sobreprecios/web/format.py` | Números (`$`, `%`), textos de semáforo/motivo/referencia y `explicar_semaforo()` — compartido por `app.py` (Jinja) y `reports.py` (HTML armado a mano) para que muestren exactamente lo mismo |
| Backup | `sobreprecios/backup.py` | Copia de seguridad (API online de SQLite) y restauración (valida, deja un backup fechado, reemplazo atómico) sin consola |

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

- **2026-10-12 · MEJORAS #49 pendiente (revisión de journeys del LLM prime) + feedback directo de
  jdemonasterio**: ocho piezas sobre la página de detalle de la licitación, el caso de revisión, los reportes y
  el backup, con una ronda de feedback en el medio que cambió el alcance de dos de ellas:
  - **Síntesis arriba de `/licitacion/<id>`** (`web/queries.resumen_proceso()`, función pura sobre los datos ya
    cargados por la ruta, sin SQL nueva): renglones total/comparados/rojo/amarillo/verde/gris, equivalencia por
    verificar / validados, **monto TOTAL del proceso** (declarado en la norma) vs **monto de los renglones
    analizados** (suma) — antes los dos se llamaban "monto adjudicado" y se podían confundir — exceso estimado,
    cobertura %, y una lista en castellano de "qué falta para publicar" (grises, equivalencias, objeciones,
    renglones sin validar, monto inconsistente, precio inferido, referencia de programa, o simplemente que la
    licitación no está marcada "publicable" todavía).
  - **Comprobante de cálculo** (`revision_humana.comprobante()`, `GET /renglon/<id>/comprobante`): una página
    imprimible (extiende `base.html`; `@media print` de `styles.css` ya oculta nav/formularios) con dato
    original, normalización, las 3 fechas (apertura/adjudicación/publicación) explícitas, el par que confirma el
    mínimo corroborado con su grupo comercial (`pipeline.compare.familia_fuente`), el mes de IPC usado por cada
    observación ("abr-2024 → jun-2024 (x1.030)"), los dos escenarios financieros (1 mes = amarillo, 2 meses =
    rojo, de `comparaciones.sensibilidad_json`), correcciones/validaciones humanas y el checklist de
    explicaciones alternativas. Link desde el renglón en `/licitacion/<id>` y desde `/revision/<id>`.
  - **Por qué este semáforo, en una línea** (`web/format.explicar_semaforo()`): se parsea `motivo`/`motivo_codigo`
    (nunca repite el cálculo) para mostrar, junto al color, la razón corta y — si la brecha llega al umbral de
    rojo pero el semáforo no es rojo — qué lo bloquea ("+52% pero amarillo: el mínimo no está confirmado por dos
    cadenas"). Vive en `web/format.py` (no en `app.py`) para que `reports.py` lo use igual en el expediente.
  - **Compuerta "publicable" visible**: el texto de la tarjeta "Revisión humana y publicación" en
    `/licitacion/<id>` explica qué hacen Guardar / Validé esta licitación / Publicable; los reportes y el
    expediente NUNCA bloquean la exportación (decisión explícita de jdemonasterio) pero imprimen una lista de
    avisos (`reports.advertencias()`) en vez de un solo contador.
  - **Expediente para periodista — redefinido a mitad de camino** (pedido directo de jdemonasterio: "el que
    revisa y el que analiza es la misma persona; lo que llega al periodista es UN html, primero la licitación y
    los resultados, después cómo se construyeron"): `reports.crear_expediente()` arma un HTML autocontenido (+
    ZIP) en dos partes — ①"Qué se compró y qué se encontró" (fechas, montos, tabla de resultados por renglón con
    semáforo y brecha) y ②"Cómo se construyó esta comparación" por renglón (fuentes con sus URLs, decisiones de
    normalización — variedad, equivalencias, correcciones humanas —, manejo de inflación/IPC mes a mes o "PRECIO
    INFERIDO", el supuesto de precio de programa cuando aplica, y las preguntas abiertas). Nunca repite el
    comprobante completo de cada renglón (eso vive aparte). Reusa la tabla `reportes` (`tipo='expediente'`,
    `proceso_id`) — aparece en `/reportes` junto a los reportes normales, con descarga ZIP en vez de Excel/CSV.
  - **Checklist de explicaciones alternativas** (`revision_humana.EXPLICACIONES_ITEMS`, tabla
    `explicaciones_caso`): logística/lugar de entrega, impuestos, especificaciones, plazo de pago, error de
    publicación — estado pendiente/verificado/no_aplica + nota por ítem, en `/revision/<id>`. Nunca recalcula
    nada (a diferencia de `correcciones_renglon`): es una anotación para quien lee, no un dato de entrada.
  - **Backup / restauración sin consola** (`sobreprecios/backup.py`): "Descargar copia de seguridad" en
    `/fuentes` usa la API de backup online de SQLite (`sqlite3.Connection.backup`, no bloquea otros pedidos);
    "Restaurar copia" valida el archivo subido (header de SQLite + que tenga las tablas de esta app, tope de
    300 MB), deja la base actual como un backup fechado al lado (`<nombre>.antes-de-restaurar-<fecha>.db`, nunca
    se borra) y reemplaza con `os.replace` (atómico). Antes de mover la base vieja se le hace
    `PRAGMA wal_checkpoint(TRUNCATE)` y se mueven sus `-wal`/`-shm` junto con el backup: sin esto, un WAL viejo
    quedado en el mismo nombre de archivo podría "seguir" la base nueva. **Deshabilitado en modo público**
    (`LIC_MODO_PUBLICO=1`, el deploy de solo lectura en la nube de otro agente): el GET de descarga expondría
    notas de revisión humana a cualquiera con el link, así que `app._modo_publico()` lo corta antes de tocar el
    archivo y la plantilla oculta el panel entero (`ctx["modo_publico"]`, seteado en `_render()` para toda
    página); el POST de restaurar lo corta igual aunque el middleware general del deploy público ya bloquee todo
    POST (defensa en profundidad: esta ruta reemplaza la base entera).
  - **"Ver documento" en el lugar** (pedido directo de jdemonasterio, no solo etiquetas más claras): un visor
    plegable reusable (`web/templates/_macros.html::doc_viewer`) que carga `/doc?url=` en un `<iframe>` (la
    allowlist de host y el sandbox siguen siendo de `revision_humana.obtener_documento`/`doc_proxy`) con el link
    directo como respaldo si el documento no carga ahí (p. ej. el visor de GDE que pide sesión). Aplicado al
    aviso del proceso y a los actos administrativos en `/licitacion/<id>` y al comprobante de cálculo; `/revision/
    <id>` ya tenía su propio visor desde #34 (sin tocar). Las etiquetas también se volvieron explícitas: "Ver
    documento ↗" en la tabla de actos pasó a "Abrir en COMPR.AR ↗" / "Ver acá (sección actos)" porque esa URL es
    el proceso con un ancla, nunca un PDF directo.
  - **Fechas explícitas en todos lados** (pedido directo de jdemonasterio): apertura/adjudicación/publicación
    como 3 líneas separadas (nunca un solo `COALESCE` escondiendo cuál se usó) en `/licitacion/<id>`,
    `/revision/<id>` y el comprobante; cada observación de mercado en las tablas de evidencia muestra "mes de la
    observación → mes de la compra (factor)" en vez de solo el factor IPC pelado.
  - **Cola de revisión rediseñada** (pedido directo de jdemonasterio): el color (semáforo), el motivo
    (`explicar_semaforo`) y el origen de la alerta (gris/equivalencia/rojo/juez/agregado a mano, más una
    etiqueta aparte "monto inconsistente" leída de `renglones.alertas`) son columnas separadas, nunca un solo
    badge. "Bien" muestra un resumen corto armado en Python (`revision_humana.cola()`: producto canónico +
    envase + motivo corto, p. ej. "aceite girasol 1,5 l — revisar unidad") con la descripción SIByS completa en
    un `<details>` colapsado. Los conteos de color son **en vivo** desde `comparaciones` (`cola()` devuelve
    `colores`, idéntico cálculo que `queries.kpis()` sin filtros) para que coincidan siempre con la home — el
    tally viejo por `origen` de `revisiones_caso` (`counts`) queda aparte, bajo "por qué entró a la cola", porque
    es un acumulado histórico que puede desactualizarse si un caso ya resuelto cambió de color después. Una
    leyenda explica "Resolver" (veredicto sobre un caso puntual) vs "Validé esta licitación" (atajo que marca
    TODO el proceso) vs "Publicable" (la compuerta real de exportación).
  - Verificado con el simulador (`tests/e2e/test_user_journeys.py`, escenarios 13-18) y ~70 tests nuevos
    (`test_web_format.py`, `test_web_queries.py`, `test_backup.py`, `test_reports.py`, `test_web_app.py`, más
    casos agregados a `test_revision_humana.py`).

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
| Mirar un documento (BO/COMPR.AR) sin salir | panel plegable "Ver documento"/"Ver acá" en `/licitacion/<id>`, `/revision/<renglon_id>` y el comprobante (proxy `/doc`, solo esos hosts; link directo de respaldo) |
| Bajar / validar precios de programa (Precios Cuidados/Justos) | `python -m sobreprecios.cli precios-programa [--edicion ID] [--juez TSV]` · solapa `/precios-programa` |
| Ver el comprobante de cálculo imprimible de un renglón | link "comprobante de cálculo" en `/licitacion/<id>` o `/revision/<id>` → `/renglon/<id>/comprobante` |
| Armar expediente para periodista | en `/licitacion/<id>`, elegir renglones y título → "Armar expediente para periodista" → aparece en `/reportes` (tipo "Expediente", descarga HTML o ZIP) |
| Completar el checklist de explicaciones alternativas de un caso | `/revision/<id>` → tarjeta "Explicaciones alternativas" (logística, impuestos, especificaciones, plazo de pago, error de publicación) |
| Descargar / restaurar una copia de la base sin consola | botones en `/fuentes` → "Descargar copia de seguridad" / "Restaurar copia" (deshabilitado si `LIC_MODO_PUBLICO=1`) |
| Antes de entregar | `simular.bat` / `python scripts/simular.py`: 26 recorridos de usuario tienen que dar OK |

## 4. Tests

`python -m pytest -q`: parser de unidades y montos, catálogo (casos reales 2021 y SEPA), extracción con fixture real
(MDS Res. 735/2021), reparto de montos conjuntos, comparador (ventana, IPC, envase, marca, plausibilidad, plazo de
pago del pliego), gateway LLM (cache, cuota, perfiles), normalización de links, hechos fechados, sociedades
(fixtures BO 2ª sección), claves, COMPR.AR (actos administrativos, ofertas desestimadas con causa, plazo de pago
con regresión del falso positivo "Requiere pago"), revisión humana (`tests/test_revision_humana.py`: correcciones
por campo y su recálculo, la cola no pisa una decisión humana, exclusión de referencias de mercado, resumen de
validación por renglón, parser de `items.tsv`/`resumen.md` del juez, allowlist de host del proxy de documentos incl.
redirect a host no permitido, checklist de explicaciones alternativas, comprobante de cálculo con su par
corroborado y la familia de cada observación, colores en vivo y resumen corto de la cola). El simulador
(`tests/e2e/`) cubre los journeys de `/revision` y de la validación in-line en `/licitacion/<id>` con un navegador
real, precios de programa (los tres formatos con fixtures, macheo, réplica por vigencia, idempotencia, fallback y
familia en el comparador), validaciones de programa (guardar/leer, import de TSV del juez), la síntesis y la
compuerta visible en `/licitacion/<id>`, el comprobante de cálculo, armar un expediente para periodista en
background y descargarlo (HTML + ZIP), el checklist de explicaciones alternativas sin recargar, que los colores de
la cola coincidan con la home, y la descarga de la copia de seguridad; el simulador también cubre marcar una fila
de "programa" ✗ y verla excluida del comparador. `test_web_format.py`/`test_web_queries.py` prueban
`explicar_semaforo()` y `resumen_proceso()` como funciones puras; `test_backup.py` prueba el backup/restauración
sobre bases en `tmp_path` (nunca la real); `test_reports.py` prueba `advertencias()` y `crear_expediente()`;
`test_web_app.py` prueba las rutas nuevas y que `LIC_MODO_PUBLICO=1` bloquee backup/restore (`TestClient`).

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
- El expediente para periodista y el comprobante de cálculo son HTML autocontenido (texto + links a las fuentes
  públicas): no empaquetan los PDFs externos (BO/COMPR.AR) dentro del ZIP, así que esos links dejan de servir si
  la fuente los mueve. Para un archivo que sobreviva eso, descargar los PDFs aparte con los links de "Abrir...".
- Backup/restauración no coordina con jobs en background que puedan estar escribiendo a mitad de camino (no hay
  lock global): en la práctica es una herramienta de un solo usuario local, así que el riesgo es bajo, pero
  restaurar mientras corre "Buscar en el BO + COMPR.AR" o "Recalcular sobreprecios" no está probado.
- Precios de programa: abr-dic 2023 sin edición encontrada (ver decisión 2026-10-12); PDF de 2022-01 pierde ~3 %
  de filas de aceite por una omisión de palabra en la fuente misma (ver esa decisión); el PDF nacional de
  2022-01 con ~30.000 precios (12,6 MB) no se incorporó, solo el de CABA.
