#!/usr/bin/env python3
"""Ventana de animación de victoria con confeti"""

import tkinter as tk
import random
import math
import json
from pathlib import Path

C = {
    'bg': '#0A0A1A',
    'gold': '#FFD700',
    'red': '#FF4444',
    'blue': '#4488FF',
    'green': '#44FF88',
    'yellow': '#FFFF44',
    'purple': '#FF44FF',
    'orange': '#FF8844',
}

ARCHIVO_POS = Path(__file__).parent / "victoria_pos.json"
COLORS = [C['gold'], C['red'], C['blue'], C['green'], C['yellow'], C['purple'], C['orange']]

class VentanaVictoria:
    def __init__(self, auto_cierre=True):
        self.root = tk.Tk()
        self.root.title("🎉 VICTORIA 🎉")
        self.root.configure(bg=C['bg'])
        self._cargar_posicion()
        self.root.resizable(False, False)

        self.canvas = tk.Canvas(self.root, width=800, height=600, bg=C['bg'], highlightthickness=0)
        self.canvas.pack()

        self.particulas = []
        self.estrellas = []
        self.corriendo = True

        self._crear_texto_victoria()
        self._crear_estrellas()
        self._crear_confeti()

        self._animar()

        self.root.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.root.bind('<Control-c>', lambda e: self._cerrar())
        self.root.bind('<Control-C>', lambda e: self._cerrar())

        if auto_cierre:
            self.root.after(15000, self._cerrar)

    def _cargar_posicion(self):
        try:
            if ARCHIVO_POS.exists():
                with open(ARCHIVO_POS, 'r') as f:
                    pos = json.load(f)
                self.root.geometry(f"{pos['w']}x{pos['h']}+{pos['x']}+{pos['y']}")
                return
        except:
            pass
        self.root.geometry("800x600")

    def _guardar_posicion(self):
        try:
            pos = {
                'x': self.root.winfo_x(),
                'y': self.root.winfo_y(),
                'w': self.root.winfo_width(),
                'h': self.root.winfo_height(),
            }
            with open(ARCHIVO_POS, 'w') as f:
                json.dump(pos, f)
        except:
            pass

    def _crear_texto_victoria(self):
        self.canvas.create_text(400, 150, text="🎊 VICTORIA 🎊",
                                 font=('Consolas', 48, 'bold'), fill=C['gold'])
        self.canvas.create_text(400, 220, text="¡GANASTE!",
                                 font=('Consolas', 24, 'bold'), fill=C['green'])

    def _crear_estrellas(self):
        for _ in range(50):
            x = random.randint(0, 800)
            y = random.randint(0, 600)
            tam = random.randint(2, 5)
            color = random.choice(COLORS)
            brillo = random.randint(50, 100)
            estrella = {
                'x': x, 'y': y, 'tam': tam, 'color': color,
                'brillo': brillo, 'angulo': random.random() * math.pi * 2,
                'tipo': random.choice(['estrella', 'circulo', 'rombo']),
                'dx': random.uniform(-0.5, 0.5),
                'dy': random.uniform(-0.3, 0.3),
            }
            self.estrellas.append(estrella)

    def _crear_confeti(self):
        for _ in range(200):
            x = random.randint(-100, 900)
            y = random.randint(-600, 0)
            tam = random.randint(5, 15)
            color = random.choice(COLORS)
            velocidad = random.uniform(2, 6)
            rotacion = random.uniform(-3, 3)
            angulo = random.random() * 360
            tipo = random.choice(['rect', 'oval', 'poly'])
            confeti = {
                'x': x, 'y': y, 'tam': tam, 'color': color,
                'velocidad': velocidad, 'rotacion': rotacion,
                'angulo': angulo, 'tipo': tipo,
                'oscilacion': random.uniform(0, 2),
                'fase': random.random() * math.pi * 2,
            }
            self.particulas.append(confeti)

    def _animar(self):
        if not self.corriendo:
            return

        self.canvas.delete('particula')
        self.canvas.delete('estrella')

        for e in self.estrellas:
            e['x'] += e['dx']
            e['y'] += e['dy']
            e['angulo'] += 0.05

            if e['x'] < 0: e['x'] = 800
            if e['x'] > 800: e['x'] = 0
            if e['y'] < 0: e['y'] = 600
            if e['y'] > 600: e['y'] = 0

            x1 = e['x'] - e['tam']
            y1 = e['y'] - e['tam']
            x2 = e['x'] + e['tam']
            y2 = e['y'] + e['tam']

            if e['tipo'] == 'estrella':
                self._dibujar_estrella(e['x'], e['y'], e['tam'], e['color'])
            elif e['tipo'] == 'circulo':
                self.canvas.create_oval(x1, y1, x2, y2, fill=e['color'], tags='estrella')
            else:
                self.canvas.create_polygon(
                    e['x'], y1, x2, e['y'], e['x'], y2, x1, e['y'],
                    fill=e['color'], tags='estrella'
                )

        for p in self.particulas:
            p['y'] += p['velocidad']
            p['x'] += math.sin(p['fase']) * p['oscilacion']
            p['fase'] += 0.05
            p['angulo'] += p['rotacion']

            if p['y'] > 650:
                p['y'] = random.randint(-100, -10)
                p['x'] = random.randint(0, 800)

            x1 = p['x'] - p['tam']
            y1 = p['y'] - p['tam']
            x2 = p['x'] + p['tam']
            y2 = p['y'] + p['tam']

            if p['tipo'] == 'rect':
                self.canvas.create_rectangle(x1, y1, x2, y2, fill=p['color'], tags='particula')
            elif p['tipo'] == 'oval':
                self.canvas.create_oval(x1, y1, x2, y2, fill=p['color'], tags='particula')
            else:
                points = self._rotar_puntos(p['x'], p['y'], p['tam'], p['angulo'])
                self.canvas.create_polygon(points, fill=p['color'], tags='particula')

        self.root.after(30, self._animar)

    def _dibujar_estrella(self, cx, cy, radio, color):
        puntos = []
        for i in range(10):
            angulo = i * math.pi / 5 - math.pi / 2
            r = radio if i % 2 == 0 else radio * 0.4
            x = cx + r * math.cos(angulo)
            y = cy + r * math.sin(angulo)
            puntos.extend([x, y])
        self.canvas.create_polygon(puntos, fill=color, tags='estrella')

    def _rotar_puntos(self, cx, cy, radio, angulo):
        puntos = []
        for i in range(4):
            a = math.radians(angulo) + i * math.pi / 2
            x = cx + radio * math.cos(a)
            y = cy + radio * math.sin(a)
            puntos.extend([x, y])
        return puntos

    def _cerrar(self):
        self._guardar_posicion()
        self.corriendo = False
        self.root.destroy()

def mostrar_victoria(auto_cierre=True):
    app = VentanaVictoria(auto_cierre=auto_cierre)
    app.root.mainloop()

if __name__ == '__main__':
    mostrar_victoria(True)