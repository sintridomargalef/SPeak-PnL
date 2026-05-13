import sys
import json
import os
import time
from ctypes import windll, c_int, c_bool, c_wchar, byref, WINFUNCTYPE
from ctypes.wintypes import HWND, LPARAM, RECT

# ========== CONFIGURACIÓN ==========
CONFIG_FILE = "window_pos.json"
WINDOW_TITLE_PART = "PeakArena"   # Subcadena del título de tu ventana
                                   # Ej: "PeakArena" o "PeakArena - Google Chrome"
GUARDAR_TAMBIEN_TAMANO = True      # True = guarda y restaura también ancho/alto
# ===================================

# APIs de Windows
user32 = windll.user32
kernel32 = windll.kernel32

# Constantes
SW_RESTORE = 9
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002

# Prototipo para EnumWindows
EnumWindowsProc = WINFUNCTYPE(c_bool, HWND, LPARAM)

# Variable global para acumular ventanas (usada en callbacks)
windows = []

def get_window_text(hwnd):
    """Devuelve el título de la ventana usando el buffer correcto."""
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buf = (c_wchar * (length + 1))()
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value

def enum_callback(hwnd, lparam):
    """Callback para EnumWindows: recoge ventanas visibles con título."""
    if user32.IsWindowVisible(hwnd):
        title = get_window_text(hwnd)
        if title:
            windows.append((hwnd, title))
    return True  # seguir enumerando

def find_window_by_title_part(partial):
    """Devuelve (hwnd, título) de la primera ventana cuyo título contenga 'partial'."""
    global windows
    windows = []
    callback = EnumWindowsProc(enum_callback)
    user32.EnumWindows(callback, 0)
    for hwnd, title in windows:
        if partial.lower() in title.lower():
            return hwnd, title
    return None, None

def list_all_windows():
    """Muestra todas las ventanas visibles con título (útil para depurar)."""
    global windows
    windows = []
    callback = EnumWindowsProc(enum_callback)
    user32.EnumWindows(callback, 0)
    print("Ventanas visibles actualmente:")
    for i, (hwnd, title) in enumerate(windows, 1):
        print(f"{i:3}. {title} (HWND: {hwnd})")
    return windows

def get_window_rect(hwnd):
    """Devuelve (x, y, ancho, alto) de la ventana."""
    rect = RECT()
    if user32.GetWindowRect(hwnd, byref(rect)):
        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    return None

def activate_and_move(hwnd, x, y, width=None, height=None):
    """
    Restaura la ventana, la trae al frente y la mueve/redimensiona.
    Si width/height son None, solo mueve (sin cambiar tamaño).
    """
    if not hwnd:
        return False
    # Restaurar si minimizada
    user32.ShowWindow(hwnd, SW_RESTORE)
    # Traer al frente
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.1)  # pausa breve para que la ventana se active
    # Mover (y redimensionar si se especifica)
    flags = 0
    if width is None or height is None:
        flags |= SWP_NOSIZE
    result = user32.SetWindowPos(hwnd, 0, x, y,
                                 width if width else 0,
                                 height if height else 0,
                                 flags)
    return result != 0

def save_position(hwnd):
    """Guarda posición (y opcionalmente tamaño) en archivo JSON."""
    rect = get_window_rect(hwnd)
    if rect:
        x, y, w, h = rect
        data = {'x': x, 'y': y}
        if GUARDAR_TAMBIEN_TAMANO:
            data['width'] = w
            data['height'] = h
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f)
        print(f"Posición guardada: x={x}, y={y}" + (f", ancho={w}, alto={h}" if GUARDAR_TAMBIEN_TAMANO else ""))
    else:
        print("No se pudo obtener la posición de la ventana.")

def load_position_and_size():
    """Carga posición (y tamaño si existe) desde JSON. Devuelve (x, y, width, height)."""
    if not os.path.exists(CONFIG_FILE):
        return None, None, None, None
    with open(CONFIG_FILE, 'r') as f:
        data = json.load(f)
        x = data.get('x')
        y = data.get('y')
        width = data.get('width') if GUARDAR_TAMBIEN_TAMANO else None
        height = data.get('height') if GUARDAR_TAMBIEN_TAMANO else None
        return x, y, width, height
    return None, None, None, None

def main():
    if len(sys.argv) < 2:
        print("Uso:")
        print("  python winpos.py --list        -> Listar ventanas (para identificar títulos)")
        print("  python winpos.py --save        -> Guardar posición (y tamaño) de la ventana")
        print("  python winpos.py --move        -> Mover/redimensionar a valores guardados")
        return

    modo = sys.argv[1].lower()

    if modo == "--list":
        list_all_windows()
        return

    # Buscar la ventana objetivo
    hwnd, title = find_window_by_title_part(WINDOW_TITLE_PART)
    if hwnd is None:
        print(f"No se encontró ninguna ventana con título que contenga '{WINDOW_TITLE_PART}'")
        print("Ejecuta '--list' para ver todas las ventanas disponibles y ajusta WINDOW_TITLE_PART.")
        return

    print(f"Ventana encontrada: {title} (HWND: {hwnd})")

    if modo == "--save":
        save_position(hwnd)
        # Activar la ventana para feedback visual
        activate_and_move(hwnd, 0, 0)  # solo activar, no mover
        print("Ventana activada. Configuración guardada.")
    elif modo == "--move":
        x, y, w, h = load_position_and_size()
        if x is None or y is None:
            print("No hay posición guardada. Ejecuta '--save' primero.")
            return
        if GUARDAR_TAMBIEN_TAMANO and w is not None and h is not None:
            if activate_and_move(hwnd, x, y, w, h):
                print(f"Ventana activada y movida a ({x}, {y}) con tamaño {w}x{h}")
            else:
                print("Error al mover/redimensionar la ventana.")
        else:
            if activate_and_move(hwnd, x, y):
                print(f"Ventana activada y movida a ({x}, {y}) (tamaño sin cambios)")
            else:
                print("Error al mover la ventana.")
    else:
        print("Modo no reconocido. Usa --list, --save o --move.")

if __name__ == "__main__":
    main()