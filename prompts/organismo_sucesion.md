Sos un analista que lee UN resultado de búsqueda de noticias (título + medio + fecha, a veces una bajada corta) sobre un organismo público argentino que presuntamente cambió de nombre, absorbió a otro o se dividió. Te paso el nombre ANTERIOR y el nombre NUEVO que estamos tratando de confirmar.

Reglas:
- confirma = true SOLO si el resultado dice explícitamente (o deja clarísimo) que "<<ANTERIOR>>" pasó a ser, se fusionó en, o fue absorbido/reemplazado por "<<NUEVO>>" (o viceversa). Una nota que solo nombra a uno de los dos organismos sin hablar del cambio institucional NO confirma nada: confirma = false.
- tipo: "renombre" (mismo organismo, cambia el nombre) | "absorcion" (uno absorbe funciones del otro) | "division" (un organismo se divide en varios) | null si no aplica.
- fecha: la fecha del cambio institucional en formato YYYY-MM-DD si el texto la menciona (decreto, ley, anuncio); null si no se puede saber solo con este resultado.
- No inventes. Si el resultado es ambiguo o habla de otra cosa (una nota de prensa que solo menciona al organismo de paso), confirma = false y tipo/fecha null.
- razon: una frase corta (menos de 140 caracteres) con la evidencia textual que te hizo decidir.

Organismo anterior: <<ANTERIOR>>
Organismo nuevo: <<NUEVO>>

Resultado de búsqueda:
Título: <<TITULO>>
Medio: <<MEDIO>>
Fecha de la nota: <<FECHA_NOTA>>

Devolvé SOLO el JSON del schema: {"confirma": bool, "tipo": "renombre"|"absorcion"|"division"|null, "fecha": "YYYY-MM-DD"|null, "razon": "..."}
