"""Lee anchos de COLUMNAS (sección ESTRATEGIA) y los imprime como JSON."""
import json
import sys
try:
    from configurador import conectar_excel
    ws = conectar_excel().worksheet("COLUMNAS")
    filas = ws.get_all_values()
    widths = {}
    for fila in filas:
        if (len(fila) >= 3
                and str(fila[0]).strip().upper() == 'ESTRATEGIA'
                and fila[1] and fila[2]):
            try:
                widths[fila[1].strip()] = int(float(str(fila[2]).replace(',', '.')))
            except ValueError:
                pass
    print(json.dumps(widths))
except Exception as e:
    print(json.dumps({"error": str(e)}))
    sys.exit(1)
