import tkinter as tk
from tkinter import messagebox, ttk
import subprocess
import sys
import os
import threading
import time
import gspread
from google.oauth2.service_account import Credentials
from configurador import conectar_excel

# Forzar directorio de trabajo al del script
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Configuración y Colores unificados con SuperVentana
VOICE_EXE = r"C:\Python\voice.exe"
DISENO_TAB = "Diseño"
PANEL_TAB = "Panel"
COORDS_TAB = "Coordenadas"

# Paleta ciberpunk extraída de COLORS_DEFAULT
c = {
    "Fondo Principal": "#020A0F",
    "Fondo Elevado": "#071520",
    "Borde / Separador": "#0A1E2A",
    "Texto Título": "#00F5FF",
    "Texto Detalle": "#888888",
    "Acento Sistema": "#00F5FF",
    "Acento Éxito": "#00FF00",
    "Acento Alerta": "#FF003C",
    "Acento Purpura": "#BC13FE",
    "Acento Resumen": "#FFFF00",
    "Botones": "#071520",
    "Color Texto Botón": "#FFFFFF"
}

def cargar_diseno(spread):
    try:
        ws_d = spread.worksheet(DISENO_TAB)
        for row in ws_d.get_all_values()[1:]:
            if len(row) >= 3:
                prop, val = row[1].strip(), row[2].strip()
                if val.startswith("#"): c[prop] = val
    except: pass

def cargar_colores_sensor(spread):
    global color_map_sensor, color_texto_sensor
    try:
        ws = spread.worksheet("Sensor_Color")
        for row in ws.get_all_values()[3:]:  # Desde fila 4
            if len(row) >= 2:
                nombre = row[0].strip().upper()
                codigo = row[1].strip()
                if nombre and codigo.startswith("#"):
                    color_map_sensor[nombre] = codigo
                    # Columna C: color de texto
                    if len(row) >= 3 and row[2].strip().startswith("#"):
                        color_texto_sensor[nombre] = row[2].strip()
                    else:
                        color_texto_sensor[nombre] = "#FFFFFF"  # Blanco por defecto
    except: pass

def hablar(texto):
    if os.path.exists(VOICE_EXE):
        subprocess.Popen([VOICE_EXE, str(texto)], creationflags=subprocess.CREATE_NO_WINDOW)

procesos_activos = []
datos_cache = []
hoja_panel = None
hoja_coords_global = None
color_map_sensor = {}
color_texto_sensor = {}
spread_global = None

def lanzar_script(nombre_archivo, nombre_boton):
    if not nombre_archivo.endswith(".py"): nombre_archivo += ".py"
    hablar(f"Iniciando {nombre_boton}")
    # Flag 0x08000000 para ejecutar sin ventana de consola
    proc = subprocess.Popen([sys.executable, nombre_archivo], creationflags=0x08000000)
    procesos_activos.append(proc)

def detener_todo():
    count = 0
    for proc in procesos_activos:
        if proc.poll() is None:
            proc.terminate()
            count += 1
    procesos_activos.clear()
    hablar(f"Detenidos {count} programas" if count > 0 else "No hay programas activos")

def guardar_config_ventana():
    try:
        if hoja_coords_global:
            root.update_idletasks()
            geo = root.winfo_geometry()
            size, pos = geo.split('+', 1)
            w, h = size.split('x')
            x, y = pos.split('+')
            filas = hoja_coords_global.col_values(1)
            for i, nombre in enumerate(filas):
                if nombre.strip().upper() == "PANEL":
                    hoja_coords_global.update_cell(i+1, 3, x)
                    hoja_coords_global.update_cell(i+1, 4, y)
                    hoja_coords_global.update_cell(i+1, 5, w)
                    hoja_coords_global.update_cell(i+1, 6, h)
                    return
    except Exception as e:
        print(f"Error guardar coords: {e}")

def guardar_y_salir():
    guardar_config_ventana()
    hablar("Cerrando sistema")
    root.destroy()

def _actualizar_ui(datos):
    global datos_cache
    if datos == datos_cache: return
    datos_cache = datos
    
    # Limpiar solo los botones dinámicos en el área de scroll
    for widget in scrollable_frame.winfo_children():
        widget.destroy()

    # Mapeo de colores basado en tu pestaña Panel
    colores_map = {
        "VERDE": c["Acento Éxito"],
        "ROJO": c["Acento Alerta"],
        "AZUL": c["Acento Sistema"],
        "AMARILLO": c["Acento Resumen"],
        "LILA": c["Acento Purpura"],
        "GRIS": "#555555"
    }

    for fila in datos[1:]:
        # Detecta 'boton' en Col A, nombre en Col B, Color en Col C y Script en Col E
        if len(fila) >= 5 and fila[0].strip().lower() == "boton":
            color_txt = fila[2].strip().upper()
            if color_txt == "INACTIVO": continue

            nombre_vis = fila[1]
            script_py = fila[4]
            
            # Prioridad: Hexadecimal > Sensor_Color > Mapa de Colores > Gris por defecto
            if color_txt.startswith("#"):
                bg_btn = color_txt
                fg_btn = color_texto_sensor.get(color_txt.upper(), "#FFFFFF")
            elif color_txt.upper() in color_map_sensor:
                bg_btn = color_map_sensor[color_txt.upper()]
                fg_btn = color_texto_sensor.get(color_txt.upper(), "#FFFFFF")
            else:
                bg_btn = colores_map.get(color_txt, c["Botones"])
                fg_btn = "#FFFFFF"
            
            btn = tk.Button(scrollable_frame, text=nombre_vis.upper(),
                            command=lambda s=script_py, n=nombre_vis: lanzar_script(s, n),
                            bg=bg_btn, fg=fg_btn, width=22, height=1,
                            font=("Consolas", 11, "bold"), relief="flat", bd=0, cursor="hand2")
            btn.pack(pady=3, padx=4, fill="x")

def monitor_excel():
    contador = 0
    while True:
        try:
            if hoja_panel:
                datos = hoja_panel.get_all_values()
                root.after(0, lambda d=datos: _actualizar_ui(d))
            
            # Recargar colores y diseño cada 1 minuto (4 iter x 15 seg)
            contador += 1
            if contador >= 4:
                contador = 0
                if spread_global:
                    cargar_colores_sensor(spread_global)
                    cargar_diseno(spread_global)
        except: pass
        time.sleep(15)

# --- Interfaz Principal ---
root = tk.Tk()
root.title("PEAK - Oráculo")
root.geometry("280x900+100+100")

try:
    spread_global = conectar_excel()
    cargar_diseno(spread_global)
    cargar_colores_sensor(spread_global)
    try:
        hoja_panel = spread_global.worksheet(PANEL_TAB)
    except:
        hoja_panel = None
    try:
        hoja_coords_global = spread_global.worksheet(COORDS_TAB)
    except:
        hoja_coords_global = None

    if hoja_coords_global:
        try:
            filas = hoja_coords_global.col_values(1)
            for i, nombre in enumerate(filas):
                if nombre.strip().upper() == "PANEL":
                    x = hoja_coords_global.cell(i+1, 3).value
                    y = hoja_coords_global.cell(i+1, 4).value
                    w = hoja_coords_global.cell(i+1, 5).value
                    h = hoja_coords_global.cell(i+1, 6).value
                    if all([w, h, x, y]):
                        root.geometry(f"{w}x{h}+{x}+{y}")
                    break
        except: pass
except:
    pass

root.configure(bg=c["Fondo Principal"])
root.resizable(True, True)
root.pack_propagate(False)

# Encabezado con estética SuperVentana
tk.Label(root, text="PEAK", font=("Consolas", 16, "bold"),
          fg=c["Acento Sistema"], bg=c["Fondo Principal"]).pack(pady=12)

# --- Sistema de Scroll para botones ---
container = tk.Frame(root, bg=c["Fondo Principal"])
container.pack(fill="both", expand=True, padx=5)

canvas = tk.Canvas(container, bg=c["Fondo Principal"], highlightthickness=0)
scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
scrollable_frame = tk.Frame(canvas, bg=c["Fondo Principal"])

scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=260)
canvas.configure(yscrollcommand=scrollbar.set)

canvas.pack(side="left", fill="both", expand=True)
scrollbar.pack(side="right", fill="y")

# --- Footer con botones fijos ---
frame_fijo = tk.Frame(root, bg=c["Fondo Principal"])
frame_fijo.pack(side="bottom", fill="x", pady=10)

tk.Frame(frame_fijo, height=1, bg=c["Borde / Separador"]).pack(fill="x", pady=5)

btn_frame = tk.Frame(frame_fijo, bg=c["Fondo Principal"])
btn_frame.pack(pady=2)

tk.Button(btn_frame, text="DETENER", command=detener_todo,
          bg=c["Acento Alerta"], fg="white", font=("Consolas", 11, "bold"),
          width=18, height=1, relief="flat", cursor="hand2").pack(side="left", padx=4)

tk.Button(btn_frame, text="SALIR", command=guardar_y_salir,
          bg=c["Acento Resumen"], fg=c["Fondo Principal"], font=("Consolas", 11, "bold"),
          width=18, height=1, relief="flat", cursor="hand2").pack(side="left", padx=4)

# Hilos y cierre
threading.Thread(target=monitor_excel, daemon=True).start()
root.protocol("WM_DELETE_WINDOW", guardar_y_salir)
root.mainloop()