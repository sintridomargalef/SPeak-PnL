#!/usr/bin/env python3
"""Ventana de victoria del filtro — GIF aleatorio de Giphy con tkinter + Pillow."""

import io
import json
import random
import sys
import urllib.request
import urllib.parse
from pathlib import Path

import tkinter as tk
from PIL import Image, ImageTk

ARCHIVO_POS    = Path(__file__).parent / "victoria_f_pos.json"
AUTO_CIERRE_MS = 8000
TAMANO         = 200   # px de la ventana (cuadrada)

GIPHY_API_KEY  = "dc6zaTOxFJmzC"
GIPHY_BUSQUEDA = "victory celebration"

GIPHY_FALLBACK_IDS = [
    "1BfPP1taCof3s61x71",
    "artj92V8o75HxYib4s",
    "g9582DNuQppxC",
    "26u4lOMA8JKSnL9Uk",
    "5VMNcCxVBibZK",
]


# ── Giphy ──────────────────────────────────────────────────────────────────────

def _elegir_url_giphy() -> str:
    try:
        params = urllib.parse.urlencode({
            'api_key': GIPHY_API_KEY,
            'tag':     GIPHY_BUSQUEDA,
            'rating':  'g',
        })
        with urllib.request.urlopen(
                f"https://api.giphy.com/v1/gifs/random?{params}", timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
        gif_id = data.get('data', {}).get('id')
        if gif_id:
            print(f"[victoria_f] ID: {gif_id}")
            return f"https://media.giphy.com/media/{gif_id}/giphy.gif"
    except Exception as e:
        print(f"[victoria_f] API falló ({e}), fallback")
    gif_id = random.choice(GIPHY_FALLBACK_IDS)
    return f"https://media.giphy.com/media/{gif_id}/giphy.gif"


def _descargar_frames(url: str) -> list:
    """Descarga el GIF y devuelve lista de PIL Images redimensionadas."""
    with urllib.request.urlopen(url, timeout=10) as r:
        data = r.read()
    img = Image.open(io.BytesIO(data))
    frames = []
    try:
        while True:
            frames.append(img.copy().convert('RGBA').resize(
                (TAMANO, TAMANO), Image.LANCZOS))
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    return frames


# ── Ventana ────────────────────────────────────────────────────────────────────

def _cargar_pos(root):
    try:
        if ARCHIVO_POS.exists():
            p = json.loads(ARCHIVO_POS.read_text(encoding='utf-8'))
            root.geometry(f"{TAMANO}x{TAMANO}+{p['x']}+{p['y']}")
            return
    except Exception:
        pass
    root.geometry(f"{TAMANO}x{TAMANO}")


def _guardar_pos(root):
    try:
        ARCHIVO_POS.write_text(
            json.dumps({'x': root.winfo_x(), 'y': root.winfo_y()}),
            encoding='utf-8')
    except Exception:
        pass


def mostrar(pil_frames: list):
    root = tk.Tk()
    root.title("🏆 FILTRO 🏆")
    root.resizable(False, False)
    root.overrideredirect(True)
    root.attributes('-topmost', True)
    _cargar_pos(root)

    # Convertir a PhotoImage DESPUÉS de crear el root
    frames = [ImageTk.PhotoImage(f) for f in pil_frames]

    lbl = tk.Label(root, bg='black', cursor='fleur')
    lbl.pack()

    idx = [0]
    drag = {'x': 0, 'y': 0}

    def animar():
        lbl.config(image=frames[idx[0]])
        idx[0] = (idx[0] + 1) % len(frames)
        root.after(50, animar)

    def cerrar():
        _guardar_pos(root)
        root.destroy()

    # Arrastrar
    def _drag_start(e):
        drag['x'] = e.x_root - root.winfo_x()
        drag['y'] = e.y_root - root.winfo_y()

    def _drag_move(e):
        x = e.x_root - drag['x']
        y = e.y_root - drag['y']
        root.geometry(f"+{x}+{y}")

    # Doble clic para cerrar
    lbl.bind('<ButtonPress-1>',   _drag_start)
    lbl.bind('<B1-Motion>',       _drag_move)
    lbl.bind('<Double-Button-1>', lambda e: cerrar())

    root.after(AUTO_CIERRE_MS, cerrar)
    animar()
    root.mainloop()


if __name__ == '__main__':
    url = _elegir_url_giphy()
    print(f"[victoria_f] Cargando {url}")
    try:
        frames = _descargar_frames(url)
    except Exception as e:
        print(f"[victoria_f] Error: {e}")
        sys.exit(0)
    if not frames:
        print("[victoria_f] Sin frames.")
        sys.exit(0)
    mostrar(frames)
