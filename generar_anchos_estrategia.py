"""Genera filas en COLUMNAS de Pk_Arena para la sección ESTRATEGIA (tabla de filtros)."""
from configurador import conectar_excel

ws = conectar_excel().worksheet("COLUMNAS")

# (sección, columna, ancho)
filas = [
    ("ESTRATEGIA", "IND",    26),
    ("ESTRATEGIA", "NOMBRE", 130),
    ("ESTRATEGIA", "PNL",    90),
    ("ESTRATEGIA", "SALDO",  65),
    ("ESTRATEGIA", "OPS",    28),
    ("ESTRATEGIA", "WR",     34),
]

# Buscar última fila
existentes = ws.get_all_values()
ultima = len(existentes) + 1

for i, (seccion, columna, ancho) in enumerate(filas):
    ws.update_cell(ultima + i, 1, seccion)
    ws.update_cell(ultima + i, 2, columna)
    ws.update_cell(ultima + i, 3, ancho)

print(f"✅ {len(filas)} filas añadidas en COLUMNAS (desde fila {ultima})")
