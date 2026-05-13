import ctypes
from ctypes import wintypes, POINTER
import sys
import subprocess
import time

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
GetWindowText = user32.GetWindowTextW
GetWindowTextLength = user32.GetWindowTextLengthW
IsWindowVisible = user32.IsWindowVisible
SetForegroundWindow = user32.SetForegroundWindow
ShowWindow = user32.ShowWindow
SetWindowPos = user32.SetWindowPos
GetWindowRect = user32.GetWindowRect
SW_RESTORE = 9
SWP_NOSIZE = 1
SWP_NOMOVE = 2
HWND_TOP = 0


def force_foreground(hwnd):
    """Activa una ventana aunque el proceso llamante sea background."""
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    current_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(target_tid, current_tid, True)
    user32.BringWindowToTop(hwnd)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(target_tid, current_tid, False)

windows_list = []
handles_list = []

def callback(hwnd, lParam):
    if IsWindowVisible(hwnd):
        length = GetWindowTextLength(hwnd)
        if length > 0:
            buffer = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if title:
                windows_list.append(title)
                handles_list.append(hwnd)
    return True

def main():
    EnumWindows(EnumWindowsProc(callback), 0)
    
    if len(sys.argv) > 1:
        key = sys.argv[1].upper()
        if key == "LISTA":
            print("Ventanas activas:\n")
            for i, w in enumerate(windows_list, 1):
                print(f"{i}. {w}")
        elif key == "GUARDAR":
            guardar_posicion()
        elif key == "INICIO":
            abrir_navegador()
        else:
            found = False
            for i, w in enumerate(windows_list):
                if key in w.upper():
                    hwnd = handles_list[i]
                    force_foreground(hwnd)
                    print(f"Ventana activada: {w}")
                    found = True
                    break
            if not found:
                print(f"No se encontro ventana con: {key}")
    else:
        print("Uso: py windows.py <palabra> | INICIO | GUARDAR | LISTA")

def abrir_navegador():
    try:
        x, y = 100, 100
        ancho, alto = 150, 700
        try:
            with open("ventana_pos.txt", "r") as f:
                datos = f.read().strip().split(",")
                if len(datos) == 4:
                    x, y = int(datos[0]), int(datos[1])
        except:
            pass
        
        windows_list.clear()
        handles_list.clear()
        EnumWindows(EnumWindowsProc(callback), 0)
        target_hwnd = None
        exclusion = "pk_arena - hojas de calculo"
        for i, w in enumerate(windows_list):
            if "peakarena" in w.lower() and exclusion not in w.lower():
                target_hwnd = handles_list[i]
                print(f"Ventana encontrada: {w}, activando...")
                force_foreground(target_hwnd)
                print("VENTANA")
                return

        for i, w in enumerate(windows_list):
            if "pa2016" in w.lower() and exclusion not in w.lower():
                target_hwnd = handles_list[i]
                print(f"Ventana encontrada: {w}, activando...")
                force_foreground(target_hwnd)
                print("VENTANA")
                return
        
        print("Ventana no encontrada, creando...")
        chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        args = [
            chrome_path,
            "--new-window",
            "https://www.pa2016.vip/#/?cur=home"
        ]
        proc = subprocess.Popen(args)
        time.sleep(2)
        
        windows_list.clear()
        handles_list.clear()
        EnumWindows(EnumWindowsProc(callback), 0)
        for i, w in enumerate(windows_list):
            if "pa2016" in w.lower() or "chrome" in w.lower():
                target_hwnd = handles_list[i]
                user32.SetWindowPos(target_hwnd, HWND_TOP, x, y, ancho, alto, 0)
                force_foreground(target_hwnd)
                print("VENTANA")
                break
    except Exception as e:
        print(f"Error: {e}")

def guardar_posicion():
    try:
        ventanas = []
        handles_temp = []
        
        def cb(hwnd, lParam):
            if IsWindowVisible(hwnd):
                length = GetWindowTextLength(hwnd)
                if length > 0:
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buffer, length + 1)
                    title = buffer.value.strip()
                    if title:
                        ventanas.append(title)
                        handles_temp.append(hwnd)
            return True
        
        EnumWindows(EnumWindowsProc(cb), 0)
        
        for i, w in enumerate(ventanas):
            if "pa2016" in w.lower() or "chrome" in w.lower():
                hwnd = handles_temp[i]
                rect = ctypes.create_string_buffer(16)
                GetWindowRect(hwnd, rect)
                x = ctypes.cast(rect, ctypes.POINTER(ctypes.c_int))[0]
                y = ctypes.cast(rect, ctypes.POINTER(ctypes.c_int))[1]
                w = ctypes.cast(rect, ctypes.POINTER(ctypes.c_int))[2]
                h = ctypes.cast(rect, ctypes.POINTER(ctypes.c_int))[3]
                ancho = w - x
                alto = h - y
                
                with open("ventana_pos.txt", "w") as f:
                    f.write(f"{x},{y},{ancho},{alto}")
                print(f"Posicion guardada: x={x}, y={y}, ancho={ancho}, alto={alto}")
                break
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()