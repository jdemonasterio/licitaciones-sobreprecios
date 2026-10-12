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
| Abreviaturas | `sobreprecios/abreviaturas.py`, `config/abreviaturas.yml` | Diccionario de abreviaturas de producto; expansión aditiva pre-matcher + minería de candidatos (CLI `abreviaturas`) |
| Sucesión de organismos | `sobreprecios/organismos_historia.py` | Grafo de renombres/absorciones (tabla `organismos_sucesion`), confirmado con búsqueda web + LLM barato (CLI `organismos-historia`) |
| Archivo SEPA | `sources/sepa_archivo.py` | Volcados diarios SEPA 2024-08 → hoy (archivo comunitario preciazo), mediana por cadena |
| Compuerta | `sobreprecios/revision.py` | "publicable" atado a una huella del cálculo; se revoca solo |
| Impacto | `sobreprecios/impacto.py` | Foto antes/después de un recálculo disparado desde el webui; `/impacto` muestra qué cambió |
| PDFs | `sobreprecios/pdf_text.py` | pdfplumber por columnas → OCR → LLM (benchmark 2026-10-11) |
| Juez externo | `auditoria/juez.py` | Auditoría LLM fuera del programa, solo lectura, TSV + reglas sugeridas |
| Simulador | `tests/e2e/`, `scripts/simular.py`, `simular.bat` | Recorridos de usuario reales en el navegador: compuerta antes de entregar |
| Webui | `sobreprecios/web/` | FastAPI + Jinja; `soft-nav.js` (refresco en el lugar), `_background.py` (jobs) y `seguridad_publica` (auth básica + modo solo lectura para un despliegue público) |
| Despliegue | `Dockerfile`, `docker-entrypoint.sh`, `railway.json`, `render.yaml` | Imagen para correr en la nube (Railway recomendado); primer boot corre `setup` si la base no existe en el volumen |
| Revisión humana | `sobreprecios/revision_humana.py` | Cola de casos grises/rojos/equivalencias/juez, correcciones manuales por campo, validación de referencias de mercado y validación rápida por renglón; proxy de documentos (`/doc`) |

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

- **2026-10-12 · Despliegue en la nube (plataforma: Railway)**: para una URL pública (fuera de
  127.0.0.1) se eligió Railway Hobby (~USD 5-7/mes con disco incluido) sobre Render (disco solo
  en plan pago desde USD 7/mes + disco), Fly.io (requiere su propia CLI, menos apto para un
  usuario no técnico) y Vercel/Netlify (serverless: no sirven, no hay disco persistente para
  SQLite ni jobs de fondo). `Dockerfile` (`python:3.12-slim`, `docker-entrypoint.sh`) corre
  `setup --sin-demo` solo en el primer boot (si `LIC_DB_PATH` no existe en el volumen montado en
  `/data`; si falla a mitad de camino no tumba el contenedor, el webui igual levanta) y después
  `servir --host 0.0.0.0`. `railway.json` (builder Dockerfile, healthcheck `/healthz`) y
  `render.yaml` (alternativa, sin credenciales en el archivo: `sync: false`) ya están en el repo.
  Seguridad para hacerla pública: middleware `seguridad_publica` en `sobreprecios/web/app.py` —
  `LIC_USUARIO`+`LIC_CLAVE` (HTTP Basic con `secrets.compare_digest`, nunca logueada) para TODA la
  app, y/o `LIC_MODO_PUBLICO=1` (solo lectura: 403 a cualquier método que no sea GET/HEAD/OPTIONS,
  `/doc` e `/investigar` deshabilitados con redirect a `/`, variable de template `modo_publico` que
  oculta el CTA "＋ Investigar" y muestra el cartel). `/healthz` queda siempre sin auth (lo pega el
  healthcheck de la plataforma). Sin esas variables de entorno, el comportamiento local no cambia
  (`tests/test_seguridad_publica.py`, 7 casos). `TrustedHostMiddleware` (ya existía) necesita el
  dominio de la plataforma en `LIC_HOSTS_PERMITIDOS` — documentado en `docs/despliegue_nube.md`.
  **Límite conocido / pendiente:** el modo público oculta el CTA de navegación y el aviso de la
  página de licitaciones vacía, pero NO oculta los botones de escritura dentro de las subpáginas
  (`/fuentes`, `/revision/<id>`, `/empresa/<cuit>`, `/reportes`, `/precios-programa`,
  `/revision/juez`): esos siguen visibles y, si se tocan, el middleware igual los bloquea con 403
  (soft-nav.js lo muestra como un toast de error) — la seguridad real no depende de ocultarlos,
  pero falta ese pulido de UI (se deja fuera a propósito para no pisar al agente que trabaja en
  paralelo sobre esas plantillas; la variable de template `modo_publico` ya está disponible en
  todas para quien lo haga).

- **2026-10-12 · Diccionario de abreviaturas de producto (MEJORAS #1, P22)**: `config/abreviaturas.yml` (lista de
  `{abreviatura, expande, producto_id, estado, evidencia, fecha, fuente}`, `estado` ∈ aprobada|propuesta|rechazada)
  + `sobreprecios/abreviaturas.expandir()`, enganchado en `catalog.match_rules` (una línea: `tokens(abreviaturas.
  expandir(descripcion))`). Es ADITIVO (nunca borra texto) e idempotente: agrega, al final de la descripción, los
  tokens de la expansión de cada abreviatura APROBADA que aparece como tokens COMPLETOS y contiguos (nunca
  substring: "fid" no le pega a "confidencial"). Seeds aprobados (`fuente: humano`, de los mismos ejemplos que ya
  probó `config/productos_ajustes.yml` como exclusión): `fid`→fideos, `lch`→leche, `desc`/`descr`→descremada,
  `"p descr"`→"parcialmente descremada". `hvo`→huevo e `int`→integral se agregaron DESPUÉS, al medir el impacto
  real (ver abajo): sin ellos, "fid" hacía que fideos al huevo/integrales abreviados pasaran el `incluye` de
  `fideos_secos` sin activar su propio `excluye` (que busca "huevo"/"integral" completos, no abreviados) — mismo
  mecanismo, two-sided. Minería (`cli.py abreviaturas --buscar [--limite N] [--con-llm] | --aprobar X | --rechazar
  X`, módulo `minar_candidatos`): sobre descripciones SIN producto (`renglones` + `precios_mercado`), tokens de
  2-6 letras con frecuencia >= 2 que son prefijo o "esqueleto sin vocales" de una palabra `incluye` de algún
  producto; se agregan siempre con `estado: propuesta` (nunca auto-aprobado, con o sin `--con-llm`: el veredicto
  del LLM barato, con el prompt `prompts/abreviatura_candidata.md`, queda en `nota`/`llm_veredicto` para que un
  humano decida). **Medición real** (copia de `data/sobreprecios.db`, 2026-10-12): de 1062 descripciones de
  `renglones` sin producto, 0 contienen "fid"/"lch" (las compras oficiales no abrevian así); de 4939 descripciones
  de `precios_mercado` sin producto, 63 pasan a tener producto (`fideos_secos` vía "fid") sin crear ningún falso
  positivo nuevo (las 3 que lo eran — fideos al huevo/integral abreviados — se corrigieron agregando `hvo`/`int`,
  ver arriba); "lch" aparece una sola vez en la base principal (impacto real nulo hoy, seed de bajo riesgo para
  cuando aparezcan más SKUs así).

- **2026-10-12 · Sucesión de organismos con búsqueda web + LLM (MEJORAS #39, P13)**: tabla `organismos_sucesion`
  (`desde`, `hacia`, `tipo` renombre|absorcion|division, `fecha`, `fuentes_json`, `estado` confirmado|propuesto,
  `UNIQUE(desde, hacia)`), SOLO LECTURA para el resto del sistema (nada agrupa por este grafo todavía; la vista de
  MEJORAS #14 lo haría con `organismos_historia.leer()`). Semillas: `renombres` de `config/organismos.yml`
  (`anterior = nombres[0]`, el "nombre a mostrar" documentado en ese YAML). Búsqueda: Google News RSS
  (`news.google.com/rss/search`, sin API key, 2 queries por semilla — frase exacta y "X pasa a llamarse Y" —
  deduplicadas por URL), parseado con `defusedxml.ElementTree` (no `xml.etree` a secas: el RSS es contenido
  EXTERNO no confiable, ver CLAUDE.md). Cada resultado (título + medio + fecha) se le pasa al LLM barato
  (`prompts/organismo_sucesion.md`, schema `{confirma, tipo, fecha, razon}`) — nunca se asume una confirmación sin
  LLM (`--sin-llm` solo cuenta resultados, nunca confirma: "un resultado falso es peor que ninguno"). Exige >= 4
  **medios DISTINTOS** confirmando (`<source>` del RSS como proxy de independencia — Google News no expone el
  dominio real sin resolver el redirect uno por uno, documentado como decisión, no como garantía matemática) para
  `estado=confirmado`; con menos, `estado=propuesto` con lo que se encontró (nunca se descarta). `es_aciclico()`
  (BFS sobre los arcos ya guardados) rechaza cualquier arco que cierre un ciclo, incluso a sí mismo. CLI
  `organismos-historia [--sin-llm] [--max-resultados N]`. **Corrida real** (2026-10-12, sin `GEMINI_API_KEY` en
  este entorno): ambas semillas (SAF 311 Desarrollo Social→Capital Humano, SAF 343 Seguridad→Seguridad Nacional)
  encontraron 12 resultados de noticias cada una (Google News respondió 200 OK) pero quedaron en `estado=propuesto`
  con 0 confirmaciones — correcto y esperado: sin LLM no hay forma de leer si una nota confirma el cambio
  institucional, así que nunca se asume que sí. Pendiente: correr con una clave real para ver si llega a las 4
  confirmaciones independientes (la lógica de umbral/aciclicidad está cubierta por 14 tests con búsqueda/LLM
  mockeados, `tests/test_organismos_historia.py`).

- **2026-10-12 · Autoridades con ventana temporal + designación parcial (MEJORAS #12, #40)**: `empresa_hechos`
  separa 3 fechas, nunca confundidas: `vigente_desde` (fecha del ACTO: asamblea/acta, ya existía),
  `fecha_publicacion` (NUEVA columna: cuándo salió impreso el aviso en el BO — pie "e. DD/MM/AAAA Nº ... v. ...",
  casi siempre semanas/meses después del acto, `bo_sociedades._PUBLICACION_RE`) y "vigencia" = el rango
  `(vigente_desde, vigente_hasta)` de siempre. `renovacion_total` (NUEVA columna, bool): el acto reemplaza TODO el
  directorio (texto "nuevo directorio" / "por vencimiento de los cargos de los directores" /
  `bo_sociedades._RENOVACION_TOTAL_RE`) vs. uno PARCIAL. `db._close_superseded_autoridades` (reemplaza el bloque
  que cerraba "autoridad" junto con domicilio/razón social) solo cierra un cargo anterior si el acto posterior
  tiene el MISMO cargo o está marcado `renovacion_total=1` — antes cerraba TODO el directorio con la fecha de
  cualquier acto posterior, aunque ese acto solo reemplazara al presidente (inventaba una fecha de salida para
  directores que el acto ni mencionaba). Domicilio/domicilio_fiscal/razón social siguen cerrándose enteros (son un
  solo valor, no un directorio). Tests con los fixtures reales de BO ya existentes (`tests/test_bo_sociedades.py`:
  fecha de publicación 2014/2016 distinta de la del acto, `renovacion_total` por las dos frases) + 3 tests nuevos
  de designación parcial/total en `tests/test_db.py` (el viejo `test_close_superseded_facts_replaces_whole_board`
  describía exactamente el bug que esto corrige, se reemplazó por los 3 casos).

- **2026-10-12 · Diseño de la interfaz (jdemonasterio): sistema de diseño en `styles.css`/`base.html`.**
  Investigación de referencia (portales de monitoreo de compras públicas, sitios de datos/elecciones y
  design systems de gobierno — opentender, DoZorro/ProZorro, OCDS/Jarbas, GOV.UK, USWDS, Poncho, Chequeado,
  ProPublica) en `_scratch/licitaciones/ux/INVESTIGACION_UX.md`. Decisiones:
  - **Tipografía**: Inter (Google Fonts + fallback `system-ui`), una sola familia para todo (no editorial +
    UI como ProPublica/Poncho: acá no hay prosa larga). Base 16px (antes 15px) — GOV.UK/USWDS/ProPublica/Poncho
    coinciden en 16px como mínimo legible. Cifras tabulares nativas (`font-feature-settings: "tnum"`) en
    columnas `.num` y KPIs. El encabezado más chico (`h3`) ahora pesa igual que el cuerpo y marca jerarquía
    solo con negrita (principio explícito de GOV.UK: "el peso señala estructura, no el tamaño").
  - **Tokens**: escala tipográfica `--fs-2xs`…`--fs-2xl`, espaciado en grilla de 4px (`--space-1`…`--space-6`,
    misma convención que GOV.UK/USWDS), `--radius`/`--radius-sm`/`--radius-pill`. Paleta neutra sin cambios
    (ya era un "papel" cálido con texto casi negro, acorde a estos design systems); un solo acento azul
    (`--accent` `#1f67bf` claro / `#3987e5` oscuro, igual criterio que GOV.UK/Poncho/opentender) compartido
    ahora con `--series-1` del gráfico (antes tenían valores ligeramente distintos sin motivo). Par de
    gráfico azul/naranja (`--series-1`/`--series-2`) sin tocar: ya era el par ColorBrewer clásico que
    ProPublica recomienda por ser distinguible con daltonismo.
  - **Semáforo (bug de contraste encontrado y corregido)**: color + ícono + texto, nunca el color solo (ya
    lo hacía el código). Lo que faltaba: los tonos de texto no tenían variante por tema — en modo oscuro
    quedaban con el MISMO hex que en modo claro puesto sobre fondo oscuro. Medido con la fórmula de contraste
    WCAG (luminancia relativa): rojo (`#d03b3b` sobre fondo rojo oscuro) daba **3.19:1**, verde (`#0a7a0a`
    sobre fondo verde oscuro) daba **2.59:1** — ambos por debajo del mínimo AA de texto (4.5:1). Se separaron
    en tokens `--critical-ink`/`--warning-ink`/`--good-ink` con un valor propio por tema:

    | token (claro) | valor | sobre badge | sobre fondo | token (oscuro) | valor | sobre badge |
    |---|---|---|---|---|---|---|
    | `--critical-ink` | `#a82f2f` | 5.68:1 | 6.23:1 | `--critical-ink` | `#ef6a67` | 5.05:1 |
    | `--warning-ink` | `#8a5a00` | 5.37:1 | — | `--warning-ink` | `#fab219` | 7.25:1 |
    | `--good-ink` | `#0a7a0a` | 4.88:1 | 5.10:1 | `--good-ink` | `#3fc23f` | 6.14:1 |
    | `--text-3` (muted) | `#666560` | — | 4.94:1 | `--text-3` | `#8f8e86` (sin cambio) | 5.70:1 |

    Todos ≥ AA (4.5:1). De paso se encontró un segundo bug: en modo oscuro el hover de `.btn-primary` ponía
    texto blanco sobre `#86b6ef` (un azul clarito pensado para texto de link, no para fondo de botón) →
    2.11:1. Se agregó `--accent-hover` como token separado para fondos de hover (blanco sobre él: 5.01-8.14:1
    según tema).
  - **Foco visible**: no existía ningún estilo de foco propio (dependía 100% del default del navegador).
    Se agregó un `outline` + halo de 3px en el acento sobre todo lo interactivo (`a`, `button`, `.btn`,
    inputs, `summary`, `[tabindex]`) — mismo principio que el contorno de foco de GOV.UK, en nuestro color
    en vez de amarillo.
  - **Tablas**: cebra sutil en filas pares (USWDS/opentender/ProPublica, para lectura horizontal de filas
    con muchas columnas numéricas) sumada al hover existente, sin tocar `overflow-x:auto` (tablas anchas
    siguen scrolleando dentro de `.table-wrap`, nunca la página). **Se probó y se descartó** un `thead`
    sticky (como opentender/DoZorro): con tablas cortas el header flotante tapaba celdas clickeables y
    rompió 3 journeys del simulador Playwright (`<th>` interceptaba los clicks) — revertido, documentado en
    el comentario de `styles.css` para no reintentarlo sin resolver antes el offset contra una navbar que
    puede ocupar 1-3 líneas según el ancho.
  - **Clases nuevas** (sin tocar templates salvo `base.html`, por los otros dos agentes trabajando en
    paralelo sobre templates): `.callout` (nota destacada dentro de una página, estaba en la lista de clases
    a mantener pero no existía ningún estilo) y `.soft-toast-error` (bug de CSS faltante: `soft-nav.js` ya le
    pone esa clase al toast de error — `soft-nav.js:69` — pero nunca tuvo estilo propio; un error se veía
    igual que un éxito).
  - **Verificación**: `_scratch/licitaciones/ux/antes_*.png` / `despues_*.png` (Playwright, 1366px y 375px,
    `/`, `/licitacion/<id>`, `/analitica`, `/revision`, contra una copia de `data/sobreprecios.db`, nunca la
    original) + una pasada en `color_scheme=dark`. `python -m pytest -q` y el simulador E2E (20/20) quedaron
    verdes; la única regresión real detectada en el camino fue el `thead` sticky (ver arriba), revertida.

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
- **2026-10-12 · Revisión de journeys del prime, lo pendiente de MEJORAS #49 (jdemonasterio)**: alcance de esta
  ronda — Investigar, Analítica, nombres legibles de precios de programa, fechas/i18n y una vista de impacto
  antes/después. Cambios, archivo por archivo:
  - **Investigar (#49.5)**: `queries.tipo_importado()` banner arriba de `/licitacion/<id>` ("Qué se importó":
    dictamen de COMPR.AR = recomendado, acto del BO = adjudicado, nunca "pagado"; suma una nota si además hay
    órdenes de compra o actos administrativos). `investigar._resumen_comparacion()` detecta cuando NINGÚN
    renglón quedó con semáforo y cambia el estado de la investigación a `sin_comparacion` (nunca el "listo"
    verde): agrupa los motivos grises (`_MOTIVO_CORTO`) y agrega un próximo paso (`/revision/<renglon_id>`,
    sumar el producto a `config/productos.yml`). `investigar.lista()` agrupa reintentos del MISMO caso
    (`_clave_caso`: mismo aviso del BO con o sin `?busqueda=1` normaliza a la misma clave en
    `normalizar_entrada`) mostrando "N intentos" y conservando el `proceso_id` de un intento anterior si el más
    reciente no tiene. **Decisión del owner, pisa un borrador anterior de esta misma ronda**: el precio de HOY
    llevado hacia atrás por IPC para una compra vieja (gap ensanchado en `investigar.py`, `gap_maximo=3*365`) SÍ
    puede habilitar un semáforo rojo como cualquier otra referencia — la adaptación del comparador para eso
    queda para otro agente; esta ronda solo rotula el resultado (`investigar._nota_extrapolado()`, lee
    `evidencia_json`/`gap_dias` de la comparación y agrega "ADVERTENCIA — referencia: precio de hoy (<fecha
    obs>) llevado a <fecha compra> con IPC (×factor)" al mensaje final) y marca un tag "⚠ referencia
    extrapolada por IPC" en `/licitacion/<id>` cuando `gap_dias` supera la ventana directa y hay `factor_ipc` en
    la evidencia. Se descartó (y se revirtió del todo, sin dejar código muerto) un diseño previo que forzaba
    gris con `motivo_codigo="extrapolado_contexto"`: quedó solo como nota en el docstring de
    `pipeline/compare.compare()` para quien adapte el núcleo.
  - **Analítica auditable (#49.6)**: `queries.agrupado()` suma `publicables` (comparados que además están en
    un proceso "publicable") y `convertidos`/`sin_convertir` (cuántos renglones no se pudieron pasar a la
    moneda elegida); las tablas de `/analitica` muestran esas columnas y aclaran que "brecha promedio" es un
    promedio SIMPLE por renglón, no ponderado por monto. El tooltip del gráfico mensual (`ch-mes`) ahora
    muestra cuántos renglones comparados tiene cada punto. Si el rango de fechas abarca más de un año Y la
    moneda es "nominal", un aviso (`aviso_multi_anio`, calculado en `web/app.py:analitica()`) sugiere pesos
    reales con un link que preserva el resto de los filtros — nunca cambia el default solo. Los títulos de
    ranking ("Por organismo", "Renglones con mayor brecha") suman la aclaración "(entre los renglones
    comparados con estos filtros...)" para no leerse como un ranking representativo de todo el universo.
  - **Pedido del owner, por fuera del alcance original — orden en pesos constantes (#49, feedback 2026-10-12)**:
    `normalize/moneda.registrar_real()` registra `conv_real(monto, fecha)` SIEMPRE (con fallback al nominal si
    todavía no hay IPC cargado), independiente de qué moneda esté eligiendo la pantalla. `queries.list_procesos()`
    y `queries.agrupado()` ahora ORDENAN "exceso"/"monto" por esa columna `_real()` por default — la columna que
    se MUESTRA sigue respetando el selector "Montos en" (nominal por default); un tooltip en cada celda muestra
    el equivalente en pesos reales. Motivo: con inflación alta, un exceso nominal grande de 2025 no es
    necesariamente "peor" que uno nominal chico de 2019 una vez ajustado — el ranking tiene que compararlos
    "peras con peras". Dicho explícitamente en la página (`licitaciones.html`, `analitica.html`).
  - **Precios de programa, nombres legibles y supuestos explícitos (#49.7 + feedback del owner)**:
    `sources/precios_programa.etiqueta_edicion()` traduce `"programa:precios_cuidados:precios_cuidados_2021_01"`
    a "Precios Cuidados — enero 2021 (lista nacional)" (el id interno queda en un `title=` tooltip); se usa en
    `queries.precios_programa_ediciones()` y en la página. El texto de `/precios-programa` ahora deja explícitos
    los dos supuestos de trabajo (pedido del owner, no estaban escritos antes): (a) se asume que el producto se
    podía comprar a ese precio porque era el pactado por el gobierno; (b) se asume que el precio regulado es el
    PISO del mercado (el productor siempre quiere vender más caro). El botón "✓ Ok" dice explícitamente que
    valida la TRANSCRIPCIÓN y el producto/envase asignado, no que el precio sea el real de góndola.
  - **Nombres legibles varios (#49.7)**: `web/app.py` suma `TIPO_INVESTIGACION_TXT` (nunca mostrar
    "comprar_url"/"comprar_numero" crudo en `/investigar`) y `_extraccion_txt()` (el método de extracción de un
    renglón, "dictamen de evaluación (COMPR.AR)" en vez de `"comprar"`, "lectura con IA del aviso (<modelo>)" en
    vez de `"llm:<modelo>"`).
  - **Fechas dd/mm/aaaa (#49.7, "dates/i18n")**: filtro Jinja `fecha` (`web/app.py:_fecha`, con `con_hora=True`
    para fechas con hora) aplicado a toda fecha que se MUESTRA en texto en licitaciones/analítica/producto/
    empresas/revisión/reportes/fuentes/precios de programa/investigar — nunca a un `<input type="date" value=...>`
    (esos tienen que seguir en ISO, el navegador los traduce al formato local). `base.html` pasa `lang="es-AR"`.
  - **Producto (parte de #49, item 3)**: la serie mensual de `/producto/<id>` ahora se rotula explícitamente
    como CONTEXTO (resumen mensual sin distinguir envase/variedad/marca), nunca la referencia exacta de cada
    semáforo; la tabla de adjudicaciones agrega el id de renglón con link a `/licitacion/<id>#reng-<id>` (ancla
    nueva en la fila de `licitacion.html`) para distinguir dos compras del mismo organismo el mismo día.
  - **Vista de impacto antes/después (#49.8, item 5)**: `sobreprecios/impacto.py` (tabla nueva
    `comparaciones_snapshot`, corrida de tipo `'recalculo'` en `corridas`). `impacto.recalcular_con_impacto()`
    envuelve los disparadores de recálculo del webui — `web/app.py:_job_runner` (botones de "Datos y fuentes"),
    `_recalcular_proceso` (guardar/deshacer una corrección o una validación de mercado en `/revision`) y el
    veredicto "No" de `/precios-programa` — sacando una foto de `semaforo`/`sobreprecio_ajustado_pct`/
    `exceso_ars`/`motivo_codigo`/`estado_revision` de cada renglón ANTES de llamar a `compare.run()`. Una
    ingesta nueva (pipeline sobre renglones sin comparación previa) o un caso de `investigar.py` recién creado
    no se envuelven: no hay "antes" útil. `/impacto` lista esas corridas; `/impacto/<id>` compara la foto contra
    el estado actual y muestra qué renglón cambió de color, cuánto cambió el exceso y qué "publicable" se
    revocó (el mismo gate de `sobreprecios/revision.py`, nunca se reafirma solo).
  - **Hallazgo del simulador (regresión real, corregida en esta misma ronda)**: el aviso `aviso_multi_anio` de
    Analítica, armado con la clase `.banner` (pensada para alertas cortas de una línea, `display:flex` SIN
    `flex-wrap`), recién se ve en una corrida real del simulador completo (la investigación de texto pegado
    agrega un segundo proceso y hace que el rango de fechas abarque más de un año) — ahí rompió el layout a
    390px: con `flex-wrap: nowrap` (el default) un párrafo largo no pasa a la línea siguiente y el contenedor
    desborda horizontal. Arreglo con dos capas: el aviso de Analítica pasa a `<p class="help">` (bloque, no
    flex) en vez de `.banner`, y se agrega `flex-wrap: wrap` a `.banner` en `styles.css` como defensa general
    (no cambia los banners cortos existentes, pero evita que un futuro banner con texto largo repita esto).
    `tests/e2e/test_user_journeys.py::test_layout_mobile_390px_sin_scroll_horizontal` lo cubre corriendo
    después de un journey que crea ese escenario multi-año; simulador en 21/21.

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

- **2026-10-12 · Tablas interactivas (orden + filtro) sin librerías externas** (jdemonasterio): `sobreprecios/web/static/tablas.js` (vanilla JS) + estilos en `static/styles.css` dan a cualquier tabla orden por columna (click/Enter en el encabezado, flecha + `aria-sort`, vacío "—"/"-" SIEMPRE al final en los dos sentidos) y, en columnas categóricas, un botón-embudo con un menú de checkboxes (buscador, "Todos"/"Ninguno", cuenta por valor) que combina con Y entre columnas. Opt-in por markup, nada se activa solo: `<table class="tabla-interactiva" data-tabla-id="...">` + `<th data-tipo="num|fecha|texto|cat|ninguno">` (`data-separador` para celdas con varios valores, p.ej. lista de proveedores) + `data-valor` en el `<td>` cuando el texto mostrado está formateado — ahí va el dato CRUDO de Jinja (el número/fecha ISO antes de `|ars`/`|millones`/`|pct`/`|fecha`), nunca el texto argentino ("$ 1.234,56"): `tablas.js` parsea esos dos formatos con funciones DISTINTAS (`parseNumCrudo` para `data-valor`, con punto decimal estándar; `parseNum` con comas/puntos argentinos solo para el texto mostrado cuando no hay `data-valor`) — mezclarlos fue el primer bug real (un `data-valor="25.0"` se leía como 250 si pasaba por el parser argentino, que trata el punto como separador de miles). Filas con detalle (`<tr data-fila-detalle>` inmediatamente después de la fila principal, como en `/licitacion/<id>`) se mueven/ocultan siempre junto a su fila. Estado (orden + filtros activos) en `sessionStorage` por `pathname + tabla`, reaplicado solo tras un refresco en el lugar de `soft-nav.js` (evento `softnav:swapped`). El panel del filtro es un singleton `position:fixed` sobre `document.body` (nunca lo recorta el `overflow-x:auto` de `.table-wrap`, cabe a 375px); se reposiciona (no se cierra) con scroll/resize de la página — cerrarlo ahí se probó primero y resultó frágil: el propio `focus()` del buscador puede disparar un scroll mínimo del documento y cerraría el panel al toque de abrirlo. Valores de filtro (pueden venir de scraping externo: proveedor, organismo, producto) siempre van por `textContent`/propiedades del DOM, nunca `innerHTML`, para no abrir una vía de inyección. Aplicado a las ~10 tablas principales: `licitaciones.html` (lista), `licitacion.html` (renglones + detalle, ofertas por renglón), `analitica.html` (rankings por organismo/producto/proveedor/procedimiento + peores renglones), `revision.html`, `empresas.html`, `empresa.html` (adjudicaciones), `precios_programa.html` (ediciones), `reportes.html`, `impacto_corrida.html`, `revision_juez.html` (ítems por corrida). Tests: `tests/e2e/test_user_journeys.py` (orden asc/desc con vacíos al final, filtro categórico + "Mostrando X de Y", persistencia tras soft-nav, sin scroll horizontal a 390px con el panel abierto) — diseñados para no asumir un total fijo de filas, porque la base de la sesión del simulador es compartida y otros journeys recalculan/agregan procesos antes.

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
| Minar/aprobar abreviaturas de producto | `python -m sobreprecios.cli abreviaturas [--buscar [--limite N] [--con-llm]] [--aprobar X] [--rechazar X]` → `config/abreviaturas.yml` |
| Sucesión de organismos (renombres/absorciones) | `python -m sobreprecios.cli organismos-historia [--sin-llm] [--max-resultados N]` |
| Auditar con juez | `python -m auditoria.juez --modelo <modelo> --n 60 --max-usd 5` → `_scratch/licitaciones/auditoria/` |
| Auditar casos (rojos + cambios) | `python -m auditoria.juez --modelo <modelo> --modo casos --n 45 --max-usd 2.5 --ids 1761,1787` |
| Ver actos admin./ofertas desestimadas/plazo de pago de un proceso COMPR.AR | automático: se muestran en `/licitacion/<id>` al importar (sin comando propio) |
| Revisar casos pendientes | `/revision` (cola) → abrir un caso → corregir dato y/o validar referencias de mercado → "Guardar y recalcular" |
| Validar un renglón sin abrir el caso | `/licitacion/<id>` → desplegar el renglón → "Validación rápida" (✓/✗/?) |
| Importar una corrida del juez a la cola | `python -m sobreprecios.cli importar-juez <carpeta>` o botón en `/revision/juez` |
| Mirar un documento (BO/COMPR.AR) sin salir | panel plegable "Ver documento"/"Ver acá" en `/licitacion/<id>`, `/revision/<renglon_id>` y el comprobante (proxy `/doc`, solo esos hosts; link directo de respaldo) |
| Bajar / validar precios de programa (Precios Cuidados/Justos) | `python -m sobreprecios.cli precios-programa [--edicion ID] [--juez TSV]` · solapa `/precios-programa` |
| Antes de entregar | `simular.bat` / `python scripts/simular.py`: 20 recorridos de usuario tienen que dar OK |
| Desplegar en la nube (URL pública) | `docker build . && docker run ...` local para probar · `docs/despliegue_nube.md` (Railway paso a paso) |

| Ver el comprobante de cálculo imprimible de un renglón | link "comprobante de cálculo" en `/licitacion/<id>` o `/revision/<id>` → `/renglon/<id>/comprobante` |
| Armar expediente para periodista | en `/licitacion/<id>`, elegir renglones y título → "Armar expediente para periodista" → aparece en `/reportes` (tipo "Expediente", descarga HTML o ZIP) |
| Completar el checklist de explicaciones alternativas de un caso | `/revision/<id>` → tarjeta "Explicaciones alternativas" (logística, impuestos, especificaciones, plazo de pago, error de publicación) |
| Descargar / restaurar una copia de la base sin consola | botones en `/fuentes` → "Descargar copia de seguridad" / "Restaurar copia" (deshabilitado si `LIC_MODO_PUBLICO=1`) |
| Antes de entregar | `simular.bat` / `python scripts/simular.py`: 26 recorridos de usuario tienen que dar OK |
| Ver qué cambió un recálculo (webui/revisión) | `/impacto` → elegir corrida → "Ver qué cambió" |
| Antes de entregar | `simular.bat` / `python scripts/simular.py`: 21 recorridos de usuario tienen que dar OK |

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
Seguridad del despliegue público (`tests/test_seguridad_publica.py`, 7 casos): sin variables el
comportamiento local no cambia, modo público bloquea toda escritura (403) y deshabilita `/doc` e
`/investigar`, cartel y CTA se ocultan en el template, auth básica exige usuario+clave correctos
(401 sin ellas o con credenciales malas, 200 con las correctas), `/healthz` nunca pide credenciales,
y la clave jamás aparece en los logs (`caplog`).

Diccionario de abreviaturas (`tests/test_abreviaturas.py`): expansión aditiva/idempotente, nunca por substring,
minería determinística (sin depender del orden de un `set`), nunca auto-aprueba con `--con-llm` mockeado,
regresión del caso real "fid" + "hvo"/"int" (huevo/integral abreviados no deben entrar como fideos comunes).
Sucesión de organismos (`tests/test_organismos_historia.py`, 14 tests, búsqueda/LLM mockeados): semillas reales de
`config/organismos.yml`, umbral de 4 medios independientes, deduplicación por URL entre queries, grafo acíclico
(rechaza un ciclo y un arco a sí mismo), nunca confirma sin LLM.

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

Ronda de Investigar/Analítica/precios de programa/fechas/impacto (2026-10-12, MEJORAS #49): `tests/test_impacto.py`
(foto antes/después, re-levanta la excepción del recálculo, diff de color/exceso/publicable revocado, ignora
renglones borrados), `tests/test_queries.py` (orden por pesos constantes en `list_procesos`/`agrupado` aunque la
pantalla muestre nominal, cobertura/publicables, `tipo_importado`), `tests/test_investigar.py` (agrupar reintentos
del mismo caso, "Procesado; sin comparación" con motivos agrupados, advertencia de referencia extrapolada por IPC),
`tests/test_moneda.py` (`registrar_real` fijo sin importar la moneda de pantalla), `tests/test_precios_programa.py`
(`etiqueta_edicion`), y `tests/test_webui_smoke.py` (TestClient sin Playwright sobre las páginas tocadas: atrapa
errores de Jinja — filtro mal usado, variable faltante — en cada `pytest -q` normal, no solo con `LIC_E2E=1`). El
simulador (`tests/e2e/`) suma `/impacto` (disparar un recálculo desde "Datos y fuentes" y ver la corrida) y
assertions sobre los nombres legibles en el journey de Investigar.

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
- Sucesión de organismos: "medios independientes" usa el `<source>` del RSS de Google News como proxy de dominio
  (no se resuelve el link de redirección uno por uno) — documentado como decisión, no como garantía matemática de
  independencia. Sin `GEMINI_API_KEY` configurada, `organismos-historia` encuentra resultados pero nunca los
  confirma (no hay LLM que juzgue la nota): quedó así en la corrida real de este entorno (2026-10-12, ambas
  semillas en `estado=propuesto`, 12 resultados cada una) — no es un bug, es el fallback seguro a propósito.
- Diccionario de abreviaturas: la minería (`abreviaturas --buscar`) solo propone tokens de UNA palabra (prefijo o
  "esqueleto sin vocales" contra las palabras `incluye` del catálogo); abreviaturas de dos o más palabras ("p
  descr") no se minan solas, se agregan a mano. Agregar una abreviatura de producto puede exponer gaps de exclusión
  ya existentes en el catálogo (ver decisión 2026-10-12, "fid" + huevo/integral abreviados): cada seed nuevo que se
  apruebe conviene medirlo contra la base principal antes, no asumir que es inocuo solo porque el token en sí es
  claro.

- El expediente para periodista y el comprobante de cálculo son HTML autocontenido (texto + links a las fuentes
  públicas): no empaquetan los PDFs externos (BO/COMPR.AR) dentro del ZIP, así que esos links dejan de servir si
  la fuente los mueve. Para un archivo que sobreviva eso, descargar los PDFs aparte con los links de "Abrir...".
- Backup/restauración no coordina con jobs en background que puedan estar escribiendo a mitad de camino (no hay
  lock global): en la práctica es una herramienta de un solo usuario local, así que el riesgo es bajo, pero
  restaurar mientras corre "Buscar en el BO + COMPR.AR" o "Recalcular sobreprecios" no está probado.
- Precios de programa: abr-dic 2023 sin edición encontrada (ver decisión 2026-10-12); PDF de 2022-01 pierde ~3 %
  de filas de aceite por una omisión de palabra en la fuente misma (ver esa decisión); el PDF nacional de
  2022-01 con ~30.000 precios (12,6 MB) no se incorporó, solo el de CABA.
- Vista de impacto (`/impacto`): solo cubre recálculos disparados DESDE el webui (botones de "Datos y fuentes",
  revisión humana, veredictos de precios de programa); una corrida del CLI (`python -m sobreprecios.cli
  pipeline`/`comparar`) o un caso nuevo de `investigar.py` no quedan registrados ahí (no tienen un "antes" útil
  o no pasan por esos puntos del webui). `comparaciones_snapshot` no se limpia solo (no hay purga automática
  de corridas viejas todavía).
- Orden por pesos constantes (`conv_real`, `web/queries.py`): si `indices.ipc_general` todavía no tiene datos
  (instalación nueva, antes de correr "Actualizar IPC"), el orden de "exceso"/"monto" cae al nominal (fallback
  explícito, no un error) hasta que se cargue el IPC.

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
