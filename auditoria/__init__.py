"""Auditoría externa: un juez LLM revisa muestras del sistema SIN tocar la base.

Corre aparte del programa (`python -m auditoria.juez`), abre la base en solo
lectura y deja sus veredictos en _scratch/licitaciones/auditoria/<fecha>/ para que
una persona los revise. Sirve para encontrar reglas y errores (parseos, precios mal
detectados, productos distintos, uniones de entidades), no para corregir solo.
"""
