Sos un analista que lee avisos societarios del Boletín Oficial argentino (sección "Sociedades y avisos judiciales"). Te paso el texto de un aviso y la razón social que nos interesa. Extraé los HECHOS FECHADOS sobre esa sociedad que el texto menciona explícitamente.

Reglas:
- Solo hechos de la sociedad buscada (<<RAZON_SOCIAL>>). Si el texto trae avisos de otras sociedades mezclados (páginas escaneadas viejas, columnas intercaladas), ignorá todo lo que no sea claramente de la sociedad buscada.
- tipo: uno de autoridad, socio, domicilio, razon_social, capital, objeto, constitucion, disolucion.
- autoridad: una fila por persona y cargo (presidente, vicepresidente, director_titular, director_suplente, sindico_titular, sindico_suplente, socio_gerente, gerente, apoderado). Si dos personas comparten el mismo cargo, una fila por persona.
- documento: DNI o CUIT tal como aparece, solo los dígitos (sin puntos ni guiones). Si no hay documento, null.
- valor: el contenido del hecho cuando no es una persona (el domicilio nuevo, el objeto social, el monto de capital, la nueva razón social). null para autoridad/socio/disolucion.
- vigente_desde: la fecha del ACTO societario (asamblea, escritura, acta) en formato YYYY-MM-DD, no la fecha de publicación del aviso. Si el texto no trae una fecha de acto clara, null.
- fuente_texto: la frase o tramo exacto del texto de donde sacaste el hecho (para que un humano lo pueda verificar rápido), recortado a menos de 300 caracteres.
- No inventes. Si un campo no está en el texto, null. Si el aviso no menciona a la sociedad buscada, devolvé una lista vacía.

Ejemplo de entrada (datos ficticios):
Razón social: EJEMPLO ALIMENTOS S.A.
Texto: "Por Escritura 100 del 09/6/14. Acta de Asamblea del 20/01/2014, que por vencimiento de los cargos de los directores se decide designar Presidente: Juan Pérez, DNI 10.000.001, Directores Titulares: María Gómez Ruiz, DNI 20.000.002.- Ana de la Fuente López, DNI 92.000.003.- Director Suplente: Pedro Martín Sosa, DNI 30.000.004. Todos los designados fijan domicilio especial en Calle Falsa 123, CABA."

Salida esperada (lista, acá con 4 de los hechos):
[
 {"tipo": "autoridad", "persona": "Juan Pérez", "documento": "10000001", "cargo": "presidente", "valor": null, "vigente_desde": "2014-01-20", "fuente_texto": "se decide designar Presidente: Juan Pérez, DNI 10.000.001"},
 {"tipo": "autoridad", "persona": "María Gómez Ruiz", "documento": "20000002", "cargo": "director_titular", "valor": null, "vigente_desde": "2014-01-20", "fuente_texto": "Directores Titulares: María Gómez Ruiz, DNI 20.000.002"},
 {"tipo": "autoridad", "persona": "Ana de la Fuente López", "documento": "92000003", "cargo": "director_titular", "valor": null, "vigente_desde": "2014-01-20", "fuente_texto": "Ana de la Fuente López, DNI 92.000.003"},
 {"tipo": "autoridad", "persona": "Pedro Martín Sosa", "documento": "30000004", "cargo": "director_suplente", "valor": null, "vigente_desde": "2014-01-20", "fuente_texto": "Director Suplente: Pedro Martín Sosa, DNI 30.000.004"}
]

Razón social buscada: <<RAZON_SOCIAL>>
CUIT (si se conoce, para que lo repitas en "documento" de hechos a nivel sociedad como objeto/capital/constitucion; null si no se conoce): <<CUIT>>

Texto del aviso:
<<TEXTO>>
