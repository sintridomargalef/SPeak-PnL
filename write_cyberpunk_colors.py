import sys
sys.path.append(r"C:\Python\SPeak")
from configurador import conectar_excel
import gspread

def write_cyberpunk_colors():
    spread = conectar_excel()
    ws = spread.worksheet("Sensor_Color")
    
    colores_cyberpunk = [
        ("FONDO PRINCIPAL", "#020A0F"),
        ("FONDO ELEVADO", "#071520"),
        ("BORDE SEPARADOR", "#0A1E2A"),
        ("TEXTO TITULO", "#00F5FF"),
        ("TEXTO DETALLE", "#888888"),
        ("ACENTO SISTEMA", "#00F5FF"),
        ("ACENTO EXITO", "#00FF00"),
        ("ACENTO ALERTA", "#FF003C"),
        ("ACENTO PURPURA", "#BC13FE"),
        ("ACENTO RESUMEN", "#FFFF00"),
        ("BOTONES", "#071520"),
        ("COLOR TEXTO BOTON", "#FFFFFF")
    ]
    
    for idx, (nombre, codigo) in enumerate(colores_cyberpunk, start=14):
        ws.update_cell(idx, 1, nombre)
        ws.update_cell(idx, 2, codigo)
        print(f"Escrito {nombre} en fila {idx}")

if __name__ == "__main__":
    write_cyberpunk_colors()
    print("Colores cyberpunk guardados en Sensor_Color")
