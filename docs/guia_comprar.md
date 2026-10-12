# Guia de COMPR.AR: como ver una licitacion y quien gano a que precio

COMPR.AR (https://comprar.gob.ar) es el sistema donde el Estado nacional
publica sus compras. Desde 2024 el Boletin Oficial dejo de describir lo que
se compra: solo dice el numero de proceso (por ejemplo "LICITACION PUBLICA
N 80-0014-LPU25"). Todo lo demas -que se compro, cuanto, quien oferto a que
precio y quien gano- esta en COMPR.AR. Esta guia es para buscarlo a mano,
con el navegador, sin necesidad de saber programar.

No hace falta crear cuenta ni iniciar sesion para nada de lo que explica
esta guia: todo esto es publico.

## Antes de empezar: el vocabulario del sitio

COMPR.AR usa varias palabras para cosas parecidas. Esta tabla las traduce:

| Palabra en el sitio | Que es en criollo |
|---|---|
| Proceso de compra / Pliego | La licitacion en si (tiene un numero unico) |
| Renglon | Cada item que se compra dentro del proceso (ej: "renglon 1: leche en polvo x 800g"). Un proceso puede tener 1 renglon o 100. |
| Oferente | Cada empresa que presento una oferta (un precio) para competir |
| Acta de Apertura | La lista de quien se presento y cuanto oferto EN TOTAL (no por renglon) |
| Cuadro comparativo | La tabla que compara, renglon por renglon, cuanto oferto CADA empresa (aca esta el precio unitario) |
| Dictamen de Evaluacion | El informe que dice quien RECOMIENDAN que gane cada renglon y a que precio |
| Adjudicacion | La decision final (normalmente sigue lo que dice el dictamen) |
| Orden de Compra (OC) | El "contrato" ya firmado con el proveedor ganador, con el monto total |

## Paso 1: buscar el proceso

### Si ya tenes el numero de proceso (ej. "80-0014-LPU25")

1. Entra a **https://comprar.gob.ar**
2. En la pagina principal, bajá hasta el cuadro de busqueda (o hace click en
   **"Buscar procesos"**).
3. Esto te lleva a la pagina **"Buscador de Procesos"**.
4. En la caja chica que dice **"Numero de proceso"** (arriba de todo, dice
   "Busqueda rapida"), escribi el numero EXACTO, por ejemplo `80-0014-LPU25`.
   - Ojo con el formato: tiene que ser `XXX-XXXX-LPUXX` o `XX/X-XXXX-LPUXX`
     (los ultimos 2 numeros son el anio, las 3 letras del medio son el tipo
     de proceso: LPU = Licitacion Publica, LPR = Licitacion Privada, CDI =
     Contratacion Directa, etc).
5. Apreta el boton de buscar de esa cajita (no el de abajo, ese es para la
   busqueda con filtros).
6. Te aparece 1 resultado abajo, con nombre, tipo, fecha de apertura y
   estado. Hace click en el NUMERO del proceso (esta en azul, es un link).
7. Se abre la pagina del proceso completo (ver "Paso 2" mas abajo).

### Si NO tenes el numero y queres buscar por tema/organismo/fecha

1. Desde la misma pagina **"Buscador de Procesos"**, bajá a la seccion
   **"Busqueda avanzada"** (debajo de la busqueda rapida).
2. Filtros utiles para buscar compras de alimentos/limpieza:
   - **Rubro**: elegi "ALIMENTOS" o "ELEMENTOS DE LIMPIEZA" en el
     desplegable (hay como 50 opciones, estan ordenadas alfabeticamente).
   - **Estado del proceso**: elegi "Adjudicado" si solo te interesan
     compras YA definidas (con precio real, no en tramite).
   - **Servicio Administrativo Financiero** (SAF): es el ministerio u
     organismo (ej. "Ministerio de Salud", "Ministerio de Defensa").
   - **Fecha de apertura desde/hasta**: para acotar a un periodo.
3. Apreta el boton **"Buscar"** (abajo a la derecha del formulario).
4. Te aparece una lista (puede ser de miles de resultados si no filtraste
   mucho). Hay numeros de pagina abajo de la tabla para navegarla.
5. Cada fila tiene: numero de proceso, expediente, nombre, tipo, fecha de
   apertura, estado, unidad ejecutora y SAF. Hace click en el numero (en
   azul) de la licitacion que te interese.
6. Tambien existe el boton **"Descargar Reporte Excel"** arriba de la
   tabla de resultados: baja un Excel con todas las filas que ves
   filtradas (util si son muchas).

### Alternativa rapida: la pagina de inicio

La pagina principal (https://comprar.gob.ar) tiene una tabla
**"Procesos de compra - proximos a abrir"** con licitaciones reales que
todavia no tuvieron su apertura (todavia no hay ofertas ni ganador, pero ya
podes ver que se va a comprar y cuando).

## Paso 2: la pagina del proceso (lo que vas a ver primero)

Al entrar a un proceso vas a ver, de arriba a abajo:

1. **Informacion basica**: numero de proceso, expediente, objeto (que se
   compra, en una frase), tipo de procedimiento, modalidad, moneda.
2. **Detalle de productos o servicios**: la tabla de RENGLONES. Cada fila
   tiene: numero de renglon, codigo del item, descripcion completa, y
   cantidad + unidad (ej "480,00 UNIDAD"). Hace click en la lupita de
   "Acciones" de un renglon para ver mas detalle de ESE item (marca
   pedida, especificaciones, etc).
3. **Cronograma**: fechas de publicacion, consultas, apertura.
4. **Garantias**, **Requisitos minimos**, **Clausulas particulares**:
   la letra chica del pliego (poco relevante para comparar precios).
5. Si el proceso YA TUVO su apertura, vas a ver ademas:
   - **"Ofertas al proceso de compra"**: cuantos proveedores participaron
     y cuantas ofertas confirmaron, con un link **"Ver cuadro
     comparativo"** (ver Paso 3).
   - **Actos administrativos**: la lista de resoluciones/disposiciones
     que autorizaron el pliego, el llamado y (si ya paso) la
     adjudicacion, con sus fechas.
   - **Actas de apertura**: un link **"Acta de Apertura"** (ver Paso 4).
   - **Dictamenes de evaluacion de ofertas**: un link **"Dictamen de
     Evaluacion"** (ver Paso 5 - ACA esta quien gano y a que precio).
   - **Documento contractual por proveedor**: la tabla de Ordenes de
     Compra YA firmadas, con proveedor, CUIT, fecha y MONTO TOTAL (pero
     sin desglosar por renglon).

Si el proceso todavia no tuvo apertura (fecha de apertura futura), nada de
lo del punto 5 va a estar: todavia no hay ofertas que mostrar.

## Paso 3: el Cuadro Comparativo (el precio de CADA oferente, por renglon)

Hace click en **"Ver cuadro comparativo"**. Se abre una pagina larga con un
"acordeon" (lista desplegable) por cada renglon. Para cada renglon vas a
ver:

- Arriba: numero de renglon, codigo, descripcion completa, cantidad
  solicitada y unidad de medida.
- Abajo, UN BLOQUE POR CADA EMPRESA QUE OFERTO ese renglon, con:
  **Precio Unitario**, **Cantidad ofertada**, **Total por renglon**,
  moneda, y **Especificaciones tecnicas** (ahi a veces dice la MARCA del
  producto ofertado, buscala en el texto, suele decir "MARCA: tal cosa").

Esta es la pagina mas util para COMPARAR precios entre competidores de un
mismo renglon: vas a ver, por ejemplo, que 3 empresas ofertaron la misma
ampolla de medicamento a $18.337, $50.000 y $67.400 respectivamente.

## Paso 4: el Acta de Apertura (quien se presento, en total)

El link **"Acta de Apertura"** abre una pagina con la lista de TODAS las
ofertas confirmadas: nombre de la empresa, CUIT, fecha de confirmacion,
**precio total general** (de TODO lo que esa empresa oferto en el proceso,
no por renglon) y los datos de su garantia. Sirve para saber cuantos
competidores hubo y confirmar el CUIT de cada uno; para precio POR renglon
usa el Cuadro Comparativo (Paso 3).

## Paso 5: el Dictamen de Evaluacion (quien GANO cada renglon)

Este es el documento clave para saber **quien se llevo cada renglon y a
que precio**. Hace click en **"Dictamen de Evaluacion"**. La tabla
**"Ofertas recomendadas"** tiene una fila por renglon adjudicado, con:
numero de renglon, nombre del oferente ganador, cantidad, precio unitario,
moneda y precio total del renglon.

Ejemplo real (proceso 80-0014-LPU25, Ministerio de Salud): el renglon 1
("acido zoledronico, ampolla 4mg", 480 unidades) lo gano MICROSULES
ARGENTINA a $67.400 la unidad, total $32.352.000. Esto es exactamente lo
que dice el Boletin Oficial cuando publica la resolucion de adjudicacion
("renglon 1 por 480 unidades, monto total $32.352.000") - el Dictamen es
donde sale ese numero.

El Dictamen NO muestra el CUIT del ganador directamente: para confirmarlo,
busca el mismo nombre de empresa en el Cuadro Comparativo (Paso 3) o en el
Acta de Apertura (Paso 4), ahi si aparece el CUIT.

## Paso 6 (opcional): la Orden de Compra firmada

Si quere confirmar que la adjudicacion se formalizo (no quedo "en el
papel"), volve a la pagina del proceso (Paso 2) y mira la tabla
**"Documento contractual por proveedor"**: ahi aparece el numero de OC, el
proveedor, CUIT, estado ("Perfeccionado" = ya firmada) y el MONTO TOTAL de
esa orden (que puede incluir varios renglones de ese mismo proveedor, asi
que no siempre coincide 1 a 1 con un renglon del Dictamen).

## Resumen: para calcular un posible sobreprecio necesitas...

1. La **descripcion y cantidad** del renglon (Paso 2, tabla de renglones).
2. El **precio unitario adjudicado** (Paso 5, Dictamen - o Paso 3, Cuadro
   Comparativo, buscando la fila del ganador).
3. Comparalo contra el precio de mercado de ese mismo producto en la misma
   fecha (eso ya lo hace el resto de esta herramienta, no esta en
   COMPR.AR).

## Problemas conocidos / cosas raras que vas a ver

- **Buscar por numero de proceso es estricto con el formato**: si escribis
  mal un guion o te falta un digito, el sitio te va a decir "verifique el
  formato" y no va a encontrar nada aunque el proceso exista.
- Algunos procesos tienen **varias Ordenes de Compra** para el mismo
  proveedor (una "Original" y despues "Ampliaciones"): no te asustes si
  ves el mismo CUIT 2 o 3 veces en esa tabla.
- Los numeros de expediente a veces se ven con un espacio raro en el medio
  (ej "EX-2025-48989745- -APN-DCYC#MS"): es un error de formato del
  propio sitio, no tuyo.
- Si un proceso NO tiene todavia Acta de Apertura/Dictamen/Cuadro
  Comparativo, es porque todavia no llego a esa etapa (mira la fecha de
  apertura en el cronograma: si es futura, por eso no hay nada que ver
  ahi todavia).
