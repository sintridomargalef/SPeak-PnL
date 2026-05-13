import pyautogui
import time
import random
import subprocess
from configurador import conectar_hojas
from logs import log_message

try:
    from pynput.mouse import Button, Controller as MouseController
    MOUSE = MouseController()
    USE_PYNPUT = True
except ImportError:
    USE_PYNPUT = False

NOMBRE_SPREADSHEET = "Pk_Arena"
NOMBRE_SENSOR      = "ACTIVACION"

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True


def click_humano(x, y):
    """Click humanizado: movimiento lineal con pynput + jitter + timing aleatorio."""
    jx = x + random.randint(-3, 3)
    jy = y + random.randint(-3, 3)
    if USE_PYNPUT:
        x_ini, y_ini = MOUSE.position
        pasos = 15
        duracion = random.uniform(0.12, 0.25)
        for i in range(pasos + 1):
            t = i / pasos
            MOUSE.position = (int(x_ini + (jx - x_ini) * t),
                              int(y_ini + (jy - y_ini) * t))
            time.sleep(duracion / pasos * random.uniform(0.8, 1.2))
        time.sleep(random.uniform(0.04, 0.10))
        MOUSE.press(Button.left)
        time.sleep(random.uniform(0.06, 0.14))
        MOUSE.release(Button.left)
    else:
        time.sleep(random.uniform(0.05, 0.14))
        pyautogui.click(x=jx, y=jy)
    time.sleep(random.uniform(0.06, 0.14))


def ejecutar_preparacion():
    try:
        sheet_lector, _ = conectar_hojas()
        sh = sheet_lector.spreadsheet

        # ── Verificar variable PREPARAR ANTES de activar la ventana ─────────
        try:
            h_vars_pre = sh.worksheet("Variables")
            vars_pre = {str(f[0]).strip().upper(): str(f[1]).strip()
                        for f in h_vars_pre.get_all_values() if len(f) >= 2}
            if vars_pre.get('PREPARAR', 'NO').upper() != 'SI':
                log_message("WARNING", "[PREPARAR]",
                            "ABORTADO: Variable PREPARAR != SI", output="console")
                return
        except Exception as e:
            log_message("WARNING", "[PREPARAR]",
                        f"No se pudo leer PREPARAR: {e}", output="console")
            return
        # ─────────────────────────────────────────────────────────────────────

        log_message("INFO", "[PREPARAR]", "Ejecutando windows.py INICIO", output="console")
        subprocess.run(["py", "windows.py", "INICIO"], capture_output=True)
        time.sleep(random.uniform(0.8, 1.3))

        h_coords = sh.worksheet("Coordenadas")
        h_sensor = sh.worksheet("Sensor_Color")

        log_message("INFO", "[PREPARAR]", "Iniciando preparacion", output="console")

        todas_filas = h_coords.get_all_values()
        coord_map = {}
        for fila in todas_filas:
            if len(fila) >= 4 and fila[0].strip() and fila[2] and fila[3]:
                if str(fila[1]).strip().upper() != 'CASA':
                    continue
                try:
                    coord_map[fila[0].strip().upper()] = (int(fila[2]), int(fila[3]))
                except ValueError:
                    pass

        def obtener_xy(nombre):
            pos = coord_map.get(nombre.strip().upper())
            return pos if pos else (None, None)

        s_x, s_y = obtener_xy(NOMBRE_SENSOR)

        filas_sensor = h_sensor.get_all_values()
        color_ref = None
        for fila in filas_sensor:
            if str(fila[0]).strip().upper() == NOMBRE_SENSOR:
                val_r = str(fila[1]).strip()
                if val_r.startswith("#") and len(val_r) == 7:
                    h = val_r[1:]
                    color_ref = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                elif "," in val_r:
                    color_ref = tuple(map(int, val_r.split(',')))
                else:
                    color_ref = (int(fila[1]), int(fila[2]), int(fila[3]))
                break

        if s_x is not None and color_ref is not None:
            pixel_actual = pyautogui.pixel(s_x, s_y)
            log_message("INFO", "[PREPARAR]",
                        f"Sensor en ({s_x},{s_y}) | Real: {pixel_actual} | Ref: {color_ref}",
                        output="console")
            if pyautogui.pixelMatchesColor(s_x, s_y, color_ref, tolerance=25):
                log_message("WARNING", "[PREPARAR]",
                            f"ABORTADO: Sensor {NOMBRE_SENSOR} activo", output="console")
                return
            else:
                log_message("INFO", "[PREPARAR]", "SENSOR OK: Listo para continuar",
                            output="console")
        else:
            log_message("WARNING", "[PREPARAR]", "Sensor no encontrado", output="console")
            return

        log_message("INFO", "[PREPARAR]", "Ejecutando secuencia", output="console")

        tarea_activa = True
        try:
            h_vars = sh.worksheet("Variables")
            vars_dict = {str(f[0]).strip().upper(): str(f[1]).strip()
                         for f in h_vars.get_all_values() if len(f) >= 2}
            tarea_activa = vars_dict.get('TAREA', 'SI').upper() == 'SI'
            log_message("INFO", "[PREPARAR]", f"TAREA={'SI' if tarea_activa else 'NO'}",
                        output="console")
        except Exception as e:
            log_message("WARNING", "[PREPARAR]", f"No se pudo leer TAREA: {e}", output="console")

        pasos_restantes = ["PEAK", "ADIVINAR", "CRICKET"]
        if tarea_activa:
            pasos_restantes.append("TAREA")
        pasos_restantes.append("REGALO")

        for i, target in enumerate(pasos_restantes):
            posX, posY = obtener_xy(target)
            if posX is not None and posY is not None:
                log_message("INFO", "[PREPARAR]",
                            f"Clic en {target} [{posX}, {posY}]", output="console")
                subprocess.run(["py", "windows.py", "INICIO"], capture_output=True)
                time.sleep(random.uniform(0.15, 0.30))
                click_humano(posX, posY)
                if i < len(pasos_restantes) - 1:
                    time.sleep(random.uniform(1.7, 2.4))
            else:
                log_message("WARNING", "[PREPARAR]",
                            f"Coordenada {target} no encontrada", output="console")

    except Exception as e:
        log_message("ERROR", "[PREPARAR]", f"Error: {e}", output="console")


if __name__ == "__main__":
    pyautogui.FAILSAFE = True
    ejecutar_preparacion()
