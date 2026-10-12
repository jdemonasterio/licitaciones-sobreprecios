# Guia de claves de API (LLM gratis)

La aplicacion usa un modelo de inteligencia artificial (LLM) para leer textos
raros del Boletin Oficial y PDFs de actas. Para eso necesita una "clave de
API" — como una contraseña que identifica tus consultas ante Google (o otro
proveedor). **Es gratis** y la consigue cada persona con su propia cuenta de
Google.

## Por que esto es gratis y seguro de usar

- Todo lo que procesa esta aplicacion es **informacion publica**: el Boletin
  Oficial y precios de supermercados/mayoristas. No hay datos personales ni
  de clientes.
- Los planes gratis de estos proveedores (Google, OpenRouter) pueden usar lo
  que les mandes para mejorar sus modelos (no es que "lean tus archivos" en
  el sentido de espionaje — es parte de los terminos del servicio gratis).
  Como la informacion es publica, no hay ningun problema en aceptar eso.
- Si en algun momento la organizacion quiere evitar esto, existe un plan de
  pago (unos pocos dolares) que no usa tus datos para entrenar modelos — ver
  la seccion "Si mas adelante quieren pasar a un plan de pago" al final.

## Paso 1 — Conseguir tu clave de Gemini (gratis, la principal)

1. Entra a **https://aistudio.google.com/apikey** con tu cuenta de Google
   (Gmail). Si no tenes cuenta de Google, cualquier compañero/a te puede
   ayudar a crear una gratis en minutos.
2. Aceptá los terminos de servicio si te los pide (solo la primera vez).
3. Hace click en **"Get API key"** / **"Crear clave de API"** (el boton puede
   estar en ingles aunque el resto este en español, es normal).
4. Si te pide elegir un proyecto de Google Cloud, dejá el que te sugiere por
   defecto.
5. Te va a mostrar una clave larga que arranca distinto segun el caso (una
   tira de letras y numeros). **Copiala** con el boton de copiar al lado.

   Que tenes que ver en pantalla: una tabla con una fila nueva y, a la
   derecha, un icono para copiar la clave.

6. Guardala en algun lado temporal (un Bloc de notas) hasta pegarla en el
   Paso 3. No se la mandes a nadie por mail ni chat una vez configurada.

### Video (verificado, 2026)

**"Google AI Studio API Keys: Guía Completa Paso a Paso (2026)"** — canal
*Apasoft Training*
https://www.youtube.com/watch?v=mSDU6Ix3b6E

Alternativa, mas corta: **"Gemini API GRATIS: crea y configura tu API Key
paso a paso"** — canal *AI MAKER COLOMBIA*
https://www.youtube.com/watch?v=3kwKOVYnNrc

## Paso 2 (opcional) — Clave de OpenRouter, como respaldo

Esta clave NO es obligatoria. Sirve como "plan B": si el plan gratis de
Gemini llega a su limite del dia, la aplicacion puede usar OpenRouter para no
frenarse del todo. Si recien estan arrancando, podes saltear este paso.

1. Entra a **https://openrouter.ai/keys**.
2. Creá una cuenta gratis (con Google, GitHub o tu mail).
3. Hace click en **"Create Key"**, poné un nombre (por ejemplo
   "licitaciones-sobreprecios") y confirma.
4. Copiá la clave apenas aparece — OpenRouter **no te la vuelve a mostrar
   despues**, si la perdes hay que crear una nueva.

### Video (verificado)

**"Como crear una API KEY en OpenRouter fácil y rápido?"**
https://www.youtube.com/watch?v=mGsCF1PfPaU

## Paso 3 — Guardar las claves en la aplicacion

1. Doble click en **`configurar_claves.bat`** (en la carpeta del proyecto).
2. Te va a preguntar, una por una, cada clave. Pegá la de Gemini cuando te la
   pida (click derecho → Pegar, o Ctrl+V) y apretá Enter.
3. Para las que no tengas (OpenRouter, OpenAI), apretá Enter sin escribir
   nada: las deja como estaban.
4. Al final te va a confirmar que guardo los cambios. **Nunca** te va a
   mostrar la clave completa de nuevo, por seguridad — solo los ultimos 4
   caracteres, para que puedas reconocerla.

Las claves quedan guardadas en un archivo llamado `.env`, adentro de la
carpeta del proyecto, **solo en tu computadora**. Ese archivo nunca se sube a
internet ni se comparte con nadie.

## Que pasa si el plan gratis "va lento"

Es normal y esperado. Los planes gratis de Gemini y OpenRouter dejan hacer
una cantidad limitada de consultas por minuto y por dia (varia segun el
proveedor y cambia con el tiempo, asi que no hay un numero fijo). Si la
aplicacion tarda mas de lo esperado en analizar un lote grande de compras, no
es un error: esta esperando que se "libere" la cuota gratis. Podes dejarla
corriendo en segundo plano y volver mas tarde.

Si aparece un mensaje que menciona "cuota", "quota" o el numero **429**, es
exactamente esto — ver
[preguntas_frecuentes.md](preguntas_frecuentes.md#limite-de-cuota-mensaje-429).

## Nunca compartas ni subas tu .env

- No mandes el archivo `.env` por mail, WhatsApp ni lo subas a ningun lado
  (Drive, GitHub, etc.).
- Si por error alguien lo comparte, lo mas rapido es entrar de nuevo a
  https://aistudio.google.com/apikey, borrar esa clave y crear una nueva, y
  despues volver a correr `configurar_claves.bat` con la clave nueva.

## Si mas adelante quieren pasar a un plan de pago

Si el uso crece y el plan gratis no alcanza, se puede activar un plan de pago
(cuesta centavos de dolar por consulta) que ademas garantiza que los datos
**no** se usan para entrenar modelos de nadie — aunque en este proyecto, al
ser todo informacion publica, eso no es un requisito, es solo una opcion.
Para eso hay que: conseguir una clave de pago (Gemini o OpenAI) y correr
`configurar_claves.bat` de nuevo eligiendo el perfil `pago` cuando lo
pregunte.
