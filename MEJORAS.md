# Mejoras pendientes

Esta es la lista de cosas que faltan o se pueden mejorar en la herramienta. Esta
pensada para dos lectores: vos (para elegir que pedir primero) y Claude Code
(para implementarla).

**Como usar este archivo**: abri Claude Code en la carpeta del proyecto (ver
[guia_inicio.html](docs/guia_inicio.html), etapa 11) y decile algo como "hace la
mejora #7" o "lee MEJORAS.md y hace la mejora #12". Claude va a leer el detalle
de ese numero, hacer el cambio, correr los tests y avisarte que modifico.

Las que dicen **"pendiente de check de jdemonasterio"** son decisiones que el LLM prime
ya analizo (ver `_scratch/licitaciones/DECISIONES_prime.md`, no se sube a git)
pero que jdemonasterio todavia no marco como aprobadas. Pedile a Claude que implemente
esas solo despues de marcarlas `[x] OK` en ese archivo.

---

## Datos y fuentes

**#1 — Bajar el acto de adjudicacion final, no solo el dictamen.**
Hoy "quien gano" sale del Dictamen de Evaluacion (que *recomienda*, no decide).
Falta bajar el acto administrativo final y sus anexos para confirmar ganador,
cantidad y precio por renglon. *Pendiente de check de jdemonasterio (P6).*
Como verificar: un caso de COMPR.AR muestra un renglon con fuente "acto de
adjudicacion" en vez de (o ademas de) "recomendado (dictamen)".

**#2 — Leer la condicion de pago real del pliego.**
El costo financiero hoy usa 1 mes fijo como aproximacion por IPC. Falta leer
plazo, hito desde el que corre, y anticipos del pliego/contrato de cada caso.
*Pendiente de check de jdemonasterio (P2).*
Como verificar: el detalle de una licitacion muestra el plazo de pago leido del
pliego (no solo "1 mes, aproximado").

**#3 — Completar COMPR.AR: filtro de fechas del servidor + PDFs de pliegos/anexos.**
El filtro de fechas de busqueda se aplica hoy del lado nuestro (lento con
rangos grandes); los PDFs de pliegos y anexos no se descargan directo.
Como verificar: `python -m sobreprecios.cli comprar --desde X --hasta Y` tarda
lo mismo sin importar el tamano del rango, y un proceso trae su pliego adjunto.

**#4 — Backfill de precios historicos con Wayback Machine para casos rojos.**
Para compras viejas (2020-2023) sin precio de mercado propio, probar snapshots
de mayoristas en archive.org como evidencia puntual (no automatica).
Como verificar: un caso de "Investigar" de 2021 muestra una referencia con
fuente "Wayback Machine" cuando no hay otra.

**#5 — Pedido formal a Secretaria de Comercio del historico SEPA 2023-2026.**
No existe un archivo publico de precios por producto de esos anos; pedirlo
formalmente vale mas que deflactar precios de hoy durante anos.
*Pendiente de decision (P8).* Accion del responsable del proyecto, no una tarea de codigo:
redactar el pedido de acceso a informacion publica.
Como verificar: existe un borrador de nota/pedido en el repo o en Drive.

**#6 — Sumar mayoristas regionales (empezar por Diarco).**
Hoy el unico mayorista en vivo es Maxiconsumo (sucursal Moreno): para compras
en otras provincias esa referencia puede no ser representativa.
*Pendiente de check de jdemonasterio (P9).*
Como verificar: "Datos y fuentes" lista una segunda fuente mayorista con fecha
de observacion y, si falla, dice "sin datos" (nunca reusa un precio viejo).

**#7 — Parser de PDF superior para actas y anexos escaneados.**
Evaluar pdfplumber + OCR local + Gemini como respaldo controlado (sin
PyMuPDF, que es AGPL) para mejorar la lectura de PDFs viejos o escaneados.
*Esperando el benchmark en curso (P10).*
Como verificar: existe `sobreprecios/pdf_text.py` (o similar) con la cadena
recomendada, y los tests de extraccion de PDF pasan.

**#8 — Arreglar rutas que ignoran `LIC_DB_PATH`/`LIC_CACHE_DIR`.**
`reports.reportes_dir()` e `investigar.guardar_upload()` usan `config.resolve()`,
que siempre apunta a `<repo>/data/...` sin pasar por esas variables de entorno
(encontrado armando el simulador, ver [simulador.md](docs/simulador.md)).
Como verificar: corriendo el simulador (`LIC_DB_PATH`/`LIC_CACHE_DIR` apuntando
a una carpeta temporal) los reportes y los uploads de "Investigar" NO aparecen
en `data/` del repo real.

**#9 — Declarar `playwright` como dependencia de desarrollo.**
Ya esta instalado en `.venv` (lo usa `scripts/simular.py`) pero no figura en
ningun `requirements*.txt` todavia, a proposito, hasta decidir si se adopta.
Como verificar: existe `requirements-dev.txt` (o una seccion aparte) con
`playwright>=1.47` y `simular.bat` sigue funcionando en una maquina nueva.

## Calidad y validacion

**#10 — Fijar el modelo del juez LLM externo.**
El modulo de auditoria (`auditoria/`, solo lectura, tope de gasto por corrida)
ya esta implementado, pero el modelo por defecto queda sin fijar hasta medir
su acuerdo contra los veredictos de jdemonasterio en una muestra real.
*Pendiente de check de jdemonasterio (P11).*
Como verificar: `_scratch/licitaciones/auditoria/<fecha>/` tiene un resumen con
tasa de acuerdo juez-humano y el modelo elegido.

**#11 — Golden de validacion de uniones de entidades.**
Falta generar `validation_entidades_*.tsv` (cada alias → clave canonica de
empresa/organismo/persona) para que jdemonasterio marque cuales uniones son correctas.
Como verificar: `python -m sobreprecios.cli goldens` genera ese TSV en
`_scratch/licitaciones/` con columnas `Tu veredicto` / `Tu comentario`.

**#12 — Distinguir fecha del acto, vigencia y publicacion en autoridades.**
Una designacion de directorio puede ser parcial: hoy el riesgo es cerrar todo
el directorio anterior aunque el acto nuevo no lo reemplace entero.
Como verificar: un hecho societario de `empresa_hechos` muestra las 3 fechas
por separado, y una designacion parcial no cierra cargos que no menciona.

**#13 — Revisar y marcar las 23 decisiones del LLM prime.**
`_scratch/licitaciones/DECISIONES_prime.md` (no se sube a git) tiene 23
decisiones ya tomadas por el LLM prime (P1-P15 y la ronda de método P16-P23), esperando que jdemonasterio marque cada una
`[x] OK` o `[ ] cambiar: ...`. Varias mejoras de esta lista dependen de eso.
Como verificar: ese archivo no tiene ninguna fila `Tu check` vacia.

**#26 — Atributos coexistentes por campo (no una sola "variante").**
Hoy cada producto tiene una sola dimensión de variante. Leche: fortificación y
formulación conviven; caballa: cobertura y "sin sal"; jabón: infantil +
glicerina. El prime (P17/P22) pidió atributos separados con estado propio y
parseo clave-valor de SIByS. Como verificar: "Caballa en aceite y agua sin sal"
da cobertura=aceite_y_agua y sal=sin, no "ambiguo".

**#27 — Clave de serie estable (EAN + sucursal) y IDs de la evidencia.**
La serie hoy es fuente + descripción normalizada (provisional); el par que
confirma el mínimo se guarda solo como las 2 primeras filas de la evidencia.
Como verificar: cada comparación guarda los ids de `precios_mercado` que la
forman y la corroboración usa EAN cuando existe.

**#28 — Cuarentena de precios sospechosos en vez de borrarlos por mediana.**
Prime (riesgos ronda 2): el filtro de outliers descarta datos sin probar el
error. Como verificar: una observación fuera de rango queda visible con
"cuarentena: <razón>" y se puede rehabilitar.

**#29 — Estado documental en todo el sistema (dictamen / contrato / pago).**
Los textos todavía dicen "el Estado pagó" para recomendaciones de dictamen.
Como verificar: cada renglón y reporte dice "recomendado", "contratado" o
"pagado" según la fuente.

**#30 — Índices con fecha de publicación y versión (decidido en #45).**
Prime P20: guardar cuándo se publicó cada IPC y permitir el modo "lo que se
sabía a la fecha". Como verificar: tabla `indices` con `fecha_publicacion` y
`version`; el comparador puede elegir modo.

**#31 — Exportación externa que rechace lo no publicable.**
Hoy los reportes llevan banner BORRADOR INTERNO pero se pueden mandar igual.
Como verificar: el botón "Exportar para prensa" falla si hay un renglón sin
"publicable", y la huella cubre evidencia, catálogo y configuración.

**#32 — Requisitos de envase y certificación (libre de gluten, PET, papel).**
El juez marcó compras con "libre de gluten" o envase de papel comparadas con
productos que no lo acreditan. Decidir qué requisitos son críticos para el
precio. Como verificar: lista en `config/productos_ajustes.yml` de requisitos
críticos por producto y test que lo cubra.

**#33 — Volcados SEPA "as-of" para compras sin volcado previo.**
El archivo comunitario no tiene volcados entre 2026-03-16 y 2026-07-01: las
compras de abril-junio 2026 no pueden ser rojas. Pedir el histórico oficial
(P8) o sumar otra fuente diaria. Como verificar: `sepa_archivo` encuentra un
volcado <= fecha a <= 15 días para cada compra comparable.

## Decisiones de jdemonasterio sobre la revisión del prime (2026-10-12)

**#34 — Módulo de revisión humana (P3 + P11 + visor de P6).**
Cola de casos para revisar (grises, "equivalencia por verificar", rojos, errores del juez) y una pantalla por
caso muy visual: producto, renglón, evidencia de mercado, veredicto del juez y acceso con un click al PDF del
acto, al aviso del BO y a COMPR.AR (bajados a demanda, no guardados). El humano carga lo que falte o corrige
(fecha, unidad, envase, variedad/composición, precio, producto) y su veredicto; queda en la base local y el
comparador lo usa como dato cargado a mano. Corridas del juez visibles en el webui (estilo "workhorse radar").
Como verificar: un renglón gris se completa desde la pantalla, se recalcula solo y el simulador lo cubre.

**#35 — Ampliar las compras a 2021-2022 (P4) y presentarlas como investigaciones.**
Cubrir 2021-2022 en BO y COMPR.AR para cruzarlas con las listas de Precios Cuidados. Los textos presentan cada
caso como "investigación pendiente de revisión humana", nunca como sobreprecio establecido.
Como verificar: `estado` muestra procesos 2021-2022 comparados contra precios de programa.

**#36 — Precios Cuidados / Justos / Máximos 2020-2023 como dato de mercado.**
Están cerca del mínimo (el productor en el libre mercado suele cobrar más): se usan como un dato más cuando no
hay datos mejores para esa fecha. Importador de las listas (PDF oficial, widget de datos vía Wayback, texto de
diarios); muestra validada por el juez en `_scratch/licitaciones/historico_precios/`.
Como verificar: una compra de 2021 sin SEPA se compara contra la lista vigente con la fuente a la vista.

**#37 — COMPR.AR completo: acto de adjudicación, OC, ofertas desestimadas y plazo de pago (P2, P6).**
Navegar procesos viejos (probar con 4-5) para encontrar el acto de adjudicación y las órdenes de compra; parsear
las ofertas desestimadas con su causa; leer el plazo de pago del pliego y usarlo en el ajuste financiero en
lugar del supuesto de 1 mes. Como verificar: test con fixtures de procesos viejos y la causa visible en el detalle.

**#38 — Mayoristas: priorizar datos del pasado (P9).**
Averiguar si Diarco y otros mayoristas tienen historial (o solo precio actual); el valor está en el pasado
(Wayback). Como verificar: tabla de fuentes con desde/hasta por mayorista.

**#39 — Cambios de nombre de organismos con búsqueda web + LLM (P13).**
Script que busca en diarios los cambios de identidad institucional y los da por buenos con 4 confirmaciones
independientes; grafo acíclico de sucesión/absorción. Como verificar: Desarrollo Social → Capital Humano
aparece como sucesión con sus fuentes.

**#40 — Autoridades de empresas con ventana temporal (riesgo del prime).**
Buscar notas de diarios que fechen la designación y en qué Boletín salió; distinguir fecha del acto, vigencia y
publicación. Como verificar: una designación parcial no cierra cargos que no menciona.

**#44 — Validación humana de las referencias de mercado (P16, P17, P18; dentro de #34).**
En la pantalla del caso, el humano marca cada precio de mercado (sobre todo los dos que confirman el mínimo) como
equivalente / no es el mismo producto / precio o tamaño mal leído / no sé, viendo fuente, cadena o mayorista,
fecha, descripción original, envase y factor IPC; el comparador excluye lo descartado. La variante del renglón se
puede elegir o responder "no sé". Como verificar: E2E que descarta una referencia y ve el recálculo en el lugar.

**#45 — IPC con fecha de publicación y versión, modo "disponible a la fecha" (P20, decidido).**
Ver #30: se implementa. Como verificar: test que muestra que una compra del 5 de un mes no usa el IPC de ese mes.

**#46 — Política de precios por período (jdemonasterio 2026-10-12).**
2020-2023: listas de Precios Cuidados/Justos/Máximos (en 2023 son la única fuente: sumar también recortes de
diarios con listas parciales). Ene-jul 2024: agujero sin datos -> precio INFERIDO hacia atrás desde los
volcados SEPA de agosto/septiembre 2024 corregidos por IPC (rotulado "inferido", confianza baja, nunca rojo).
Ago-2024 en adelante: archivo SEPA. Si aparece un dataset mejor, reemplaza a la inferencia.
Como verificar: una compra de marzo 2024 muestra referencia "inferida desde ago-2024 por IPC".

**#47 — Validación en el frontend: licitación y Precios Cuidados.**
En la página de cada licitación, botones ✓/✗/? y comentario para validar extracción, producto/variante y
comparación de cada renglón, y "validé esta licitación" (dentro de #34). Página de Precios Cuidados con cada
precio importado, su contexto (texto original, fuente, producto asignado, precio por kg, veredicto del juez) y
✓/✗/? + comentario; lo descartado no entra al comparador. Como verificar: E2E de ambos.

**#48 — Revisión de user journeys por el LLM prime (al terminar #34, #36, #37).**
Mandarle 3-4 recorridos paso a paso (capturas + texto) de usuarios investigando licitaciones y preguntar qué
ve bien, qué falta y qué agregaría; hasta 4 llamadas, presupuesto aparte de USD 10.
Como verificar: decisiones del prime registradas y su backlog derivado.

**#49 — Revisión de user journeys por el LLM prime: lo hecho y lo pendiente (2026-10-12).**
Hecho: fechas visibles (apertura = fecha de comparación vs adjudicación), cuenta del precio normalizado en el caso,
evidencia a todo el ancho, números de casos grises rotulados "diagnóstico bloqueado", "adjudicado" en vez de
"pagado", texto de Precios Cuidados sin afirmar que son un piso, cola de revisión que respeta el color (9 rojos),
DESIGN con la tolerancia real (±5 %). Pendiente, en orden:
1. **[hecho 2026-10-12]** Síntesis arriba del detalle de cada licitación: renglones señalados, monto total vs
   monto analizado, cobertura, exceso y qué bloquea publicar (antes la lista decía "monto adjudicado" para el
   subtotal de renglones Y para el total del proceso; ahora son dos etiquetas distintas). Ver
   `web/queries.resumen_proceso()` y DESIGN_sobreprecios.md §2.
2. **[hecho 2026-10-12]** Comprobante de cálculo por renglón (datos originales, normalización, las 3 fechas,
   par que confirma el mínimo con sus grupos, mes de IPC usado, escenarios de plazo) exportable con el caso:
   `/renglon/<id>/comprobante`, imprimible.
3. **[hecho 2026-10-12]** Explicar junto al color por qué un +52 % es amarillo (qué condición impide el rojo):
   `web/format.explicar_semaforo()`, en `/licitacion/<id>`, `/revision/<id>` y la cola.
4. **[hecho 2026-10-12]** Compuerta "publicable" visible: qué hacen Guardar / Validé / Publicable y qué falta
   para publicar; los reportes y el expediente nunca bloquean la exportación, imprimen avisos en su lugar
   (decisión explícita de jdemonasterio).
5. Investigar: separar comparación histórica de la extrapolación con precios de hoy (contexto, nunca rojo) y
   decir qué tipo de documento se importó (pliego, dictamen, adjudicación, OC); estado "procesado, sin
   comparación" con motivos y próximos pasos. *(fuera del alcance de la ronda 2026-10-12: queda para el agente de
   Investigar/Analítica/i18n.)*
6. Analítica auditable: cobertura por organismo (comparables / total / publicables), n por mes, ponderación; aviso
   cuando un ranking mezcla años en pesos de cada fecha. *(idem #5: fuera de alcance de esta ronda.)*
7. Nombres legibles (ediciones de programa, tipos de importación), fechas dd/mm/aaaa y controles en español.
   *(idem #5: fuera de alcance de esta ronda.)*
8. Lo que agregaría:
   - vista de impacto antes/después de un cambio de reglas o catálogo — **todavía pendiente**;
   - **[hecho 2026-10-12]** paquete para el periodista: `/licitacion/<id>` → "Armar expediente para periodista" →
     HTML + ZIP autocontenido en `/reportes` (reformulado a pedido directo de jdemonasterio: primero la
     licitación y los resultados, después cómo se construyó la comparación — ver DESIGN_sobreprecios.md §2);
   - **[hecho 2026-10-12]** checklist de explicaciones alternativas por caso: tarjeta en `/revision/<id>`
     (`revision_humana.EXPLICACIONES_ITEMS`);
   - **[hecho 2026-10-12]** backup/restauración de la base sin consola: botones en `/fuentes`
     (`sobreprecios/backup.py`), deshabilitado en el deploy público (`LIC_MODO_PUBLICO=1`).
Como verificar: cada punto con su criterio en `_scratch/licitaciones/REVISION_PRIME_JOURNEYS.md` (local). Lo
hecho el 2026-10-12 también quedó cubierto en el simulador (`tests/e2e/test_user_journeys.py`, escenarios 13-18)
y en tests unitarios nuevos (`test_web_format.py`, `test_web_queries.py`, `test_backup.py`, `test_reports.py`,
`test_web_app.py`).

Además, en la misma ronda, feedback directo de jdemonasterio (no estaba en la lista original de #49) también
quedó resuelto: fechas explícitas (apertura/adjudicación/publicación, y mes-a-mes de cada precio de mercado) en
toda la pantalla de la licitación, el comprobante y el expediente; "Ver documento" abre en el lugar (panel
plegable con `/doc`, nunca solo una pestaña nueva) en `/licitacion/<id>` y el comprobante; y la cola de revisión
(`/revision`) separa color / motivo / origen de la alerta en columnas, muestra un resumen corto del bien con la
descripción completa colapsada, explica Resolver vs Validar vs Publicable, y sus conteos de color coinciden en
vivo con la home.

**#41 — Domicilios: solo se ocultan los de personas humanas.** Los de empresas y organismos se muestran. Verificado 2026-10-12: la página de cada empresa muestra su domicilio legal y ningún filtro oculta domicilios de personas jurídicas; los reportes no exportan datos societarios.

**#42 — Impuestos en los precios.** Registrar si cada fuente informa precio con o sin IVA; las promociones se
toman como el precio vigente; stock y compra mínima no se consideran.

**#43 — Plan de uso por Quijote.** Lo operan ellos con claude.ai de punta a punta (iterar el software y las
validaciones humanas); costo extra estimado bajo. Enviar documentos públicos a proveedores de IA está bien: es
información pública de compras del Estado.

## Pantallas (webui)

**#14 — Tabla de correspondencias de organismos renombrados.**
Ya esta implementado que el sistema NO une renombres automaticamente (ej.
Desarrollo Social → Capital Humano), pero falta la vista de agrupacion
historica con las correspondencias documentadas, para quien la quiera ver.
*Pendiente de check de jdemonasterio (P13).*
Como verificar: "Analitica" permite ver un ranking "agrupado historicamente"
que explica que organismos agrupa y con que fuente.

**#15 — Mostrar cobertura de fuentes en los rankings.**
El minimo de mercado tiende a bajar cuanto mas fuentes se consultan: dos
organismos con distinta cobertura pueden quedar mal comparados entre si por
el metodo de muestreo, no por su comportamiento real.
Como verificar: "Analitica" muestra, al lado de cada ranking, cuantas fuentes
de precio tenia disponibles cada caso.

## Empresas

**#16 — Cruce de mismo domicilio / mismas personas entre oferentes.**
Util para detectar senales de posible cartelizacion dentro de un mismo
renglon (varios oferentes con el mismo domicilio o las mismas personas).
Como verificar: la pagina de un proceso con mas de un oferente muestra una
alerta si dos o mas comparten domicilio o una persona en comun (con fuente).

**#17 — Sumar sanciones (ONC / SIPRO / BCRA deudores) como contexto.**
Contexto adicional sobre una empresa adjudicataria, nunca como prueba de
irregularidad en la compra en si.
Como verificar: la pagina de una empresa muestra una seccion "Antecedentes"
con sanciones encontradas y su fuente, o "sin antecedentes encontrados".

**#18 — Cruce con declaraciones juradas de funcionarios (OA), solo por CUIT.**
Nunca por apellido (ya hubo homonimos reales encontrados en esta base).
Como verificar: un vinculo funcionario-empresa en el webui siempre muestra el
CUIT/DNI que lo confirma; sin ese dato, no se muestra el vinculo.

## Catalogo

**#19 — Ampliar el catalogo de limpieza e higiene.**
Orden sugerido: limpiadores/detergentes (formulacion y dilucion) → bolsas
(medida, espesor, material) → papel higienico/toallas (largo, ancho, capas) →
aderezos/especias (composicion y presentacion equivalente). Lavandina necesita
concentracion de cloro verificada; guantes quedan para una fase aparte.
*Pendiente de check de jdemonasterio (P7, incluye decidir que especias puntuales).*
Como verificar: `config/productos.yml` tiene los productos nuevos con sus
atributos criticos obligatorios, y sus tests pasan.

**#20 — Confirmar las presentaciones INDEC de yerba y leche en polvo.**
Hoy estan marcadas `verificado: inferido` (500 g y 800 g respectivamente): no
pueden ser referencia principal hasta confirmarse con la fuente oficial.
Como verificar: `config/productos.yml` muestra `verificado: confirmado` para
esos dos productos, con la fuente que lo confirma.

## Operacion

**#21 — Programar `actualizar_datos.bat` en el Programador de tareas de Windows.**
Cada dia que no se corre es un dia de precios de mercado que se pierde para
siempre (no existe un archivo historico publico). Ver
[guia_inicio.html](docs/guia_inicio.html), etapa 7, para el paso a paso.
Como verificar: el Programador de tareas de Windows tiene una tarea diaria que
corre `actualizar_datos.bat` sin intervencion manual.

**#24 — Ampliar a mas jurisdicciones (provincias/municipios).**
PBAC, BAC CABA (con datos OCDS), boletines provinciales — hoy solo cubre
compras nacionales.
Como verificar: `python -m sobreprecios.cli estado` muestra procesos de al
menos una jurisdiccion provincial o municipal ademas de la nacional.

---

## Limitaciones conocidas (no son bugs, son el alcance de hoy)

- **COMPR.AR**: solo se puede leer lo que el sitio muestra sin login; el
  filtro de fechas de la busqueda se aplica de nuestro lado, no del sitio.
- **Mayorista en vivo**: solo Maxiconsumo (sucursal Moreno, sin login).
  Supermercados: AMBA + el espejo SEPA por provincia.
- **Boletin Oficial 2da seccion** (datos de empresas) solo cubre sociedades
  con sede en CABA; los avisos anteriores a ~2012 son escaneos de baja
  confianza.
- **No existe un archivo publico de precios 2023-2026 por producto**: se
  acumula una foto diaria desde que se empezo a correr `precios-hoy` (por eso
  la mejora #21 importa tanto).
- `datos.gob.ar` y `datos.produccion.gob.ar` no respondieron desde la red de
  desarrollo (probable geobloqueo); el espejo SEPA en GitHub cubre el minorista
  mientras tanto.
- **Ningun caso sale a periodistas sin revision humana** y estado
  "publicable": esto es una regla de diseno a proposito (ver
  [CLAUDE.md](CLAUDE.md)), no algo pendiente de arreglar.
- **Una brecha de precio no prueba una irregularidad.** Logistica, plazos de
  pago, especificaciones tecnicas o errores de publicacion pueden explicarla;
  por eso cada caso se revisa a mano antes de compartirlo.
