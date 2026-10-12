# Guia de Claude Code (para seguir mejorando el proyecto)

Claude Code es un programa que escribe y modifica codigo por vos, charlando
en español normal (como un chat). Sirve para pedirle cambios chiquitos al
proyecto sin tener que saber programar: agregar un producto al catalogo,
agregar un proveedor nuevo, armar un reporte, etc.

Esta guia asume que ya instalaste la aplicacion siguiendo
[guia_instalacion_windows.md](guia_instalacion_windows.md) y que usaste la
**Opcion B (Git)** para bajar el proyecto (Claude Code lo necesita para ver
el historial de cambios).

## Paso 1 — Instalar Claude Code

1. Abri la consola de Windows (tecla Windows, escribi `cmd`, Enter) o
   PowerShell (tecla Windows, escribi `powershell`, Enter).
2. Pegá este comando y apretá Enter:
   ```
   irm https://claude.ai/install.ps1 | iex
   ```
   (esto hay que correrlo en PowerShell, no en `cmd`; si abriste `cmd`,
   cerralo y abri PowerShell).
3. Esperá a que termine de descargar e instalar.
4. Cerrá la ventana y abri una nueva (PowerShell o la Terminal de Windows).
5. Escribi `claude --version` y apretá Enter.

   Que tenes que ver en pantalla: un numero de version (por ejemplo
   `2.x.x`). Si ves eso, quedo instalado.

### Documentacion oficial (en español)

https://code.claude.com/docs/es/setup

### Video (verificado, para principiantes)

**"Instalar CLAUDE CODE para Principiantes: Paso a paso en Windows y
MacOS"** — canal *Facundo Corengia*
https://www.youtube.com/watch?v=icx7GCL4hdE

Alternativa: **"Cómo instalar CLAUDE CODE Desde Cero: Paso a paso
(Windows)"** — canal *Aquapy*
https://www.youtube.com/watch?v=Al5xmPAGce4

Nota: Claude Code necesita una cuenta de Anthropic (Claude Pro o similar) o
una clave de API de Anthropic — es un servicio distinto de las claves de
Gemini/OpenRouter que usa la aplicacion para analizar licitaciones. Quien te
dio acceso al proyecto te va a decir que cuenta usar.

## Paso 2 — Abrir el proyecto y empezar a charlar

1. Abri PowerShell.
2. Entra a la carpeta del proyecto, por ejemplo:
   ```
   cd Desktop\licitaciones-sobreprecios
   ```
3. Escribi:
   ```
   claude
   ```
4. Se abre un chat dentro de la misma ventana. Ahi le podes escribir en
   español lo que necesitas, como si le escribieras a una persona.

(Opcional) Si instalaste VS Code, podes abrir la carpeta del proyecto ahi
(`Archivo > Abrir carpeta...`) y correr `claude` desde la terminal integrada
de VS Code (`Ver > Terminal`) — es lo mismo, pero vas viendo los archivos al
costado.

### Video instalacion VS Code (opcional, verificado)

**"Cómo Instalar Visual Studio Code (VS Code) en Windows | Guía Completa
2026"** — canal *TICnoticos*
https://www.youtube.com/watch?v=SwKQ96WBIeM

## Paso 3 — Ejemplos de pedidos para ESTE proyecto

Pegá cualquiera de estos (cambiando los datos) y Claude Code va a hacer el
cambio y mostrarte que modifico antes de guardarlo:

1. "Agregá el producto 'lentejas 500 g' al catalogo, en el rubro alimentos."
2. "Agregá un mayorista nuevo a la lista de fuentes de precios, se llama
   '<nombre>' y su pagina es '<url>'."
3. "Armame un reporte en texto de todas las compras de yerba mate del 2026,
   con el proveedor y el sobreprecio de cada una."
4. "Che, el filtro de fecha en el webui no me deja poner un rango de mas de
   un año, ¿lo podes arreglar?"
5. "Explicame en criollo que hace el archivo sobreprecios/pipeline/compare.py"
6. "Agregá una columna al reporte que muestre el nombre del organismo que
   compro."
7. "¿Por que esta compra de leche en polvo aparece en rojo? Explicame el
   calculo paso a paso."
8. "Quiero correr `actualizar_datos.bat` pero para el ultimo año completo,
   no los ultimos 60 dias. ¿Como hago?"
9. "Revisa si hay algun producto del catalogo que nunca matchea con ninguna
   compra, y decime cuales son."
10. "Bajame la ultima version del proyecto desde GitHub" (esto corre
    `git pull`, trae los cambios que hizo el equipo tecnico sin tocar tu
    `.env` ni tu base de datos).

## Consejos de seguridad

- **Revisa siempre los cambios antes de aceptarlos.** Claude Code te muestra
  que archivo va a modificar y que lineas cambian antes de guardarlo —
  leelo, aunque sea por arriba. Si algo no te cierra, preguntale "¿por que
  hiciste este cambio?" antes de aceptar.
- **Hace backup de la base antes de pedir cambios grandes.** La base de
  datos esta en `data\sobreprecios.db`. Copiala a otra carpeta (por ejemplo
  `data\sobreprecios_backup_2026-10-11.db`) antes de pedirle a Claude Code
  que toque algo relacionado a datos. Mas detalle en
  [preguntas_frecuentes.md](preguntas_frecuentes.md#como-hago-backup-de-la-base).
- **Nunca le pegues tu clave de API en el chat.** Claude Code no la
  necesita para editar codigo — las claves solo van en el archivo `.env`,
  nunca en un mensaje de chat.
- **Si no entendes un cambio, no lo aceptes.** Pedile que te lo explique de
  nuevo mas simple, o que lo deshaga.
- Los cambios que hace Claude Code quedan en los archivos del proyecto, no
  afectan a la informacion publica que ya bajaste (el Boletin Oficial, los
  precios). Esos datos viven en `data\sobreprecios.db` y no se tocan salvo
  que corras `actualizar_datos.bat` o se lo pidas explicitamente.
