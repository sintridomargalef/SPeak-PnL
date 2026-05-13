import pyautogui
import time
import sys
import subprocess
import io
import winsound
from configurador import conectar_hojas

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

pyautogui.PAUSE = 0
pyautogui.FAILSAFE = True

# Optimización: usar mouse directo (más rápido que pyautogui)
try:
    from pynput.mouse import Button, Controller as MouseController
    MOUSE_FAST = MouseController()
    USE_PYNPUT = True
except ImportError:
    USE_PYNPUT = False

VOICE_EXE = r"c:\Python\voice.exe"

def click_rapido(x, y):
    """Click optimizado: usa pynput si está disponible (más rápido), si no usa pyautogui."""
    if USE_PYNPUT:
        MOUSE_FAST.position = (x, y)
        MOUSE_FAST.click(Button.left, 1)
    else:
        pyautogui.click(x=x, y=y)

def ejecutar_apuesta(color_objetivo, multiplicador=0):
    color_objetivo = color_objetivo.upper()
    multiplicador = max(1, min(10, multiplicador))


    try:
        # Cargar TODAS las coordenadas en una sola llamada batch
        sheet_lector, _ = conectar_hojas()
        h_coords = sheet_lector.spreadsheet.worksheet("Coordenadas")
        todas_filas = h_coords.get_all_values()  # 1 sola llamada API

        # Construir diccionario nombre → (x, y)
        coord_map = {}
        for fila in todas_filas:
            if len(fila) >= 4 and fila[0].strip() and fila[2] and fila[3]:
                try:
                    coord_map[fila[0].strip().upper()] = (int(fila[2]), int(fila[3]))
                except ValueError:
                    pass

        # Pre-calcular todas las posiciones antes de hacer clicks
        pasos = [color_objetivo] + ["A0.1"] * multiplicador + ["CONFIRMAR"]
        #pasos = [color_objetivo] + ["A0.1"] * multiplicador
        clicks = []
        for target in pasos:
            pos = coord_map.get(target.upper())
            if pos:
                clicks.append(pos)
            else:
                print(f"[!] No se encontraron coordenadas para: {target}")

        # Voz en paralelo (no bloquea clicks)
        try:
            subprocess.Popen([VOICE_EXE, f"Apostando a {color_objetivo} nivel {multiplicador}"])
        except:
            pass

        # Ejecutar clicks RÁPIDO
        for i, (x, y) in enumerate(clicks):
            click_rapido(x, y)
            if i == 0:  # Pausa después del click de color
                time.sleep(0.5)
            elif i == len(clicks) - 2:  # Pausa antes de CONFIRMAR
                time.sleep(0.2)

        # 🔔 Alarma sonora al finalizar la apuesta
        time.sleep(0.5)  # Pequeña pausa para que se vea el resultado
        winsound.Beep(1000, 200)  # Frecuencia 1000 Hz, duración 200ms
        time.sleep(0.1)
        winsound.Beep(1000, 300)  # Segundo beep
        print("✓ Apuesta completada")

    except Exception as e:
        print(f"[X] Error en apuesta.py: {e}")


if __name__ == "__main__":
    color = sys.argv[1] if len(sys.argv) > 1 else "AZUL"
    try:
        mult = max(1, round(float(sys.argv[2]) / 0.1)) if len(sys.argv) > 2 else 1
    except:
        mult = 1

    ejecutar_apuesta(color, mult)
