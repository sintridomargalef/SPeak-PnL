import asyncio
import websockets
import json
import random
from datetime import datetime
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
import tkinter as tk
from tkinter import ttk
from collections import deque
import os
from pathlib import Path

WS_URL = 'wss://www.ff2016.vip/game'
ORIGIN = 'https://www.ff2016.vip'
OUTPUT_TXT = 'reconstructor_data_AI.txt'

C = {
    'bg':       '#050A14',
    'panel':    '#0A1628',
    'border':   '#0D2137',
    'accent':   '#00D4FF',
    'accent2':  '#00FF88',
    'accent3':  '#FF3366',
    'warn':     '#FFB800',
    'text':     '#C8D8E8',
    'muted':    '#4A6080',
    'blue':     '#2B7FFF',
    'red':      '#FF3366',
    'white':    '#E8F4FF',
    'green':    '#00FF88',
}

FONT_MONO  = ('Consolas', 13)
FONT_MONO_B= ('Consolas', 13, 'bold')
FONT_BIG   = ('Consolas', 30, 'bold')
FONT_MED   = ('Consolas', 18, 'bold')
FONT_SM    = ('Consolas', 11)
FONT_TITLE = ('Consolas', 15, 'bold')


class ReconstructorDashboard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("RECONSTRUCTOR AI - DASHBOARD")
        self.root.configure(bg=C['bg'])
        self.root.geometry("1900x1100")
        self.root.resizable(True, True)

        self.ws_conectado = False
        self.ronda_actual = '---'
        self.tick_actual = 0
        self.blue_actual = 0
        self.red_actual = 0
        self.dif_actual = 0
        self.blue_pre = 0
        self.red_pre = 0
        self.blue_t35 = 0   # snapshot del tick 35 (alineado con live)
        self.red_t35  = 0
        self.dif_t25 = 0
        self.dif_t33 = 0
        self.aceleracion = 0.0
        self.estabilidad = "ESTABLE"
        self.racha_sesion = []
        self.total_rondas = 0
        self.total_ganados_mayor = 0
        self.historial = deque(maxlen=20)
        self.ultimo_resultado = None
        self.tick_data = deque(maxlen=30)

        self._cargar_config_ventana()
        self._construir_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
        self._actualizar_loop()

    def _cargar_config_ventana(self):
        config_file = "reconstructor_dashboard_geometry.txt"
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    geom = f.read().strip()
                    self.root.geometry(geom)
            except:
                pass

    def _guardar_config_ventana(self):
        config_file = "reconstructor_dashboard_geometry.txt"
        with open(config_file, 'w') as f:
            f.write(self.root.geometry())

    def _on_closing(self):
        self._guardar_config_ventana()
        self.root.destroy()

    def _construir_ui(self):
        self._header()
        body = tk.Frame(self.root, bg=C['bg'])
        body.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        left = tk.Frame(body, bg=C['bg'])
        left.pack(side='left', fill='both', expand=True, padx=(0, 6))
        self._panel_conexion(left)
        self._panel_ronda(left)
        self._panel_barras_tiempo_real(left)
        self._panel_grafico(left)

        right = tk.Frame(body, bg=C['bg'])
        right.pack(side='right', fill='both', padx=(6, 0), pady=0)
        right.configure(width=580)
        right.pack_propagate(False)
        self._panel_stats(right)
        self._panel_historial(right)
        self._panel_log(right)

    def _header(self):
        hf = tk.Frame(self.root, bg='#020810', height=70)
        hf.pack(fill='x')
        hf.pack_propagate(False)
        tk.Frame(hf, bg=C['accent'], height=2).pack(fill='x', side='top')
        inner = tk.Frame(hf, bg='#020810')
        inner.pack(fill='both', expand=True, padx=16)
        tk.Label(inner, text="◈ RECONSTRUCTOR AI ENGINE", font=('Consolas', 18, 'bold'),
                 bg='#020810', fg=C['accent']).pack(side='left', pady=14)
        self._lbl_ws = tk.Label(inner, text="● WS", font=FONT_MONO_B, bg='#020810', fg=C['muted'])
        self._lbl_ws.pack(side='right', padx=8, pady=12)
        self._lbl_clock = tk.Label(inner, text="00:00:00", font=('Consolas', 15, 'bold'),
                                   bg='#020810', fg=C['muted'])
        self._lbl_clock.pack(side='right', padx=8, pady=12)

    def _panel_conexion(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=(0, 6))
        tk.Label(f, text="CONEXIÓN WEBSOCKET", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(pady=8)
        self._btn_conectar = tk.Button(f, text="CONECTAR", font=FONT_MONO_B,
                                       bg=C['accent'], fg=C['bg'], relief='flat',
                                       command=self._toggle_conexion)
        self._btn_conectar.pack(pady=(0, 8))

    def _panel_ronda(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=(0, 6))
        tk.Label(f, text="RONDA ACTUAL", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(pady=8)
        info = tk.Frame(f, bg=C['panel'])
        info.pack(pady=(0, 8))
        self._lbl_ronda = tk.Label(info, text="---", font=FONT_BIG, bg=C['panel'], fg=C['white'])
        self._lbl_ronda.pack()
        self._lbl_tick = tk.Label(info, text="Tick: 0/40", font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_tick.pack()

    def _panel_barras_tiempo_real(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=(0, 6))
        tk.Label(f, text="APUESTAS EN VIVO", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(pady=8)

        self._barra_blue = tk.Frame(f, bg=C['blue'], height=40)
        self._barra_blue.pack(fill='x', padx=10, pady=2)
        self._lbl_blue = tk.Label(self._barra_blue, text="BLUE: 0", font=FONT_MONO_B,
                                  bg=C['blue'], fg=C['white'])
        self._lbl_blue.pack(side='left', padx=5)

        self._barra_red = tk.Frame(f, bg=C['red'], height=40)
        self._barra_red.pack(fill='x', padx=10, pady=2)
        self._lbl_red = tk.Label(self._barra_red, text="RED: 0", font=FONT_MONO_B,
                                 bg=C['red'], fg=C['white'])
        self._lbl_red.pack(side='left', padx=5)

        self._lbl_dif = tk.Label(f, text="Diferencia: 0.00%", font=FONT_MONO, bg=C['panel'], fg=C['text'])
        self._lbl_dif.pack(pady=4)
        self._lbl_pct = tk.Label(f, text="B: 50.0% | R: 50.0%", font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_pct.pack(pady=(0, 8))

    def _panel_grafico(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='both', expand=True, pady=(0, 6))
        tk.Label(f, text="TICK DATA", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(pady=8)
        self._canvas_grafico = tk.Canvas(f, bg=C['bg'], height=180, highlightthickness=0)
        self._canvas_grafico.pack(fill='x', padx=10, pady=(0, 8))
        self._dibujar_grafico_vacio()

    def _dibujar_grafico_vacio(self):
        c = self._canvas_grafico
        c.delete('all')
        w = c.winfo_width() or 600
        c.create_line(10, 60, w-10, 60, fill=C['border'], width=1)
        c.create_text(20, 10, text="Esperando datos...", fill=C['muted'], font=FONT_SM)

    def _actualizar_estado_ws(self, conectado):
        self.ws_conectado = conectado
        self._lbl_ws.config(fg=C['accent2'] if conectado else C['accent3'],
                            text="● WS CONECTADO" if conectado else "● WS DESCONECTADO")
        self._btn_conectar.config(text="DESCONECTAR" if conectado else "CONECTAR", state='normal')

    def _actualizar_ronda(self):
        self._lbl_ronda.config(text=self.ronda_actual if self.ronda_actual else '---')
        self._lbl_tick.config(text=f"Tick: {self.tick_actual}/40")

    def _actualizar_barras(self, blue, red, dif):
        self._lbl_ronda.config(text=self.ronda_actual if self.ronda_actual else '---')
        self._lbl_tick.config(text=f"Tick: {self.tick_actual}/40")
        self._lbl_blue.config(text=f"BLUE: {int(blue):,}")
        self._lbl_red.config(text=f"RED: {int(red):,}")
        self._lbl_dif.config(text=f"Diferencia: {dif:.2f}%")

        total = blue + red if (blue + red) > 0 else 1
        blue_pct = (blue / total) * 100
        red_pct = (red / total) * 100
        self._lbl_pct.config(text=f"B: {blue_pct:.1f}% | R: {red_pct:.1f}%")

        barra_w = self._barra_blue.winfo_width()
        if barra_w < 10:
            self.root.after(100, lambda: self._actualizar_barras(blue, red, dif))
            return

        self._barra_blue.config(width=max(10, int(barra_w * blue_pct / 100)))
        self._barra_red.config(width=max(10, int(barra_w * red_pct / 100)))
        self._dibujar_grafico()

    def _dibujar_grafico(self):
        c = self._canvas_grafico
        c.delete('all')
        w = c.winfo_width() or 600
        h = c.winfo_height() or 120

        c.create_line(10, h-20, w-10, h-20, fill=C['border'], width=1)

        if not self.tick_data:
            c.create_text(w//2, h//2, text="Esperando datos...", fill=C['muted'], font=FONT_SM)
            return

        points_blue = []
        points_red = []
        step = (w - 20) / max(len(self.tick_data) - 1, 1)

        for i, td in enumerate(self.tick_data):
            x = 10 + i * step
            blue_pct = td['blue'] / (td['blue'] + td['red']) * 100 if (td['blue'] + td['red']) > 0 else 50
            y_blue = (h - 40) - (blue_pct / 100) * (h - 60)
            points_blue.append((x, y_blue))
            y_red = (h - 40) - ((100 - blue_pct) / 100) * (h - 60)
            points_red.append((x, y_red))

        for i in range(len(points_blue) - 1):
            c.create_line(points_blue[i][0], points_blue[i][1], points_blue[i+1][0], points_blue[i+1][1],
                          fill=C['blue'], width=2)
            c.create_line(points_red[i][0], points_red[i][1], points_red[i+1][0], points_red[i+1][1],
                          fill=C['red'], width=2)

        c.create_line(points_blue[-1][0], points_blue[-1][1], points_blue[-1][0], h-20,
                      fill=C['blue'], width=1, dash=(2, 2))
        c.create_line(points_red[-1][0], points_red[-1][1], points_red[-1][0], h-20,
                      fill=C['red'], width=1, dash=(2, 2))

        c.create_text(w-30, 15, text=f"T{self.tick_actual}", fill=C['muted'], font=FONT_SM)

    def _panel_stats(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=(0, 6))
        tk.Label(f, text="ESTADÍSTICAS SESIÓN", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(pady=8)
        self._stats_labels = {}
        for key, label in [
            ('rondas', 'Rondas: 0'),
            ('ganados', 'Mayor Gana: 0'),
            ('ratio', 'Ratio: 0%'),
            ('racha_actual', 'Racha: ---'),
            ('dif_t25', 'Dif T25: ---'),
            ('dif_t33', 'Dif T33: ---'),
            ('modo', 'Modo: ---'),
            ('rango', 'Rango: ---'),
        ]:
            lbl = tk.Label(f, text=label, font=FONT_MONO, bg=C['panel'], fg=C['text'])
            lbl.pack(anchor='w', padx=15, pady=2)
            self._stats_labels[key] = lbl

    def _panel_historial(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='both', expand=True, pady=(0, 6))
        tk.Label(f, text="HISTORIAL ÚLTIMOS RESULTADOS", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(pady=8)
        scroll = tk.Scrollbar(f)
        scroll.pack(side='right', fill='y')
        self._historial_listbox = tk.Listbox(f, font=FONT_SM, bg=C['bg'], fg=C['text'],
                                              bd=0, highlightthickness=0, yscrollcommand=scroll.set)
        self._historial_listbox.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=(0, 8))
        scroll.config(command=self._historial_listbox.yview)

    def _panel_log(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='both', expand=True)
        tk.Label(f, text="LOG", font=FONT_TITLE, bg=C['panel'], fg=C['accent']).pack(pady=8)
        scroll = tk.Scrollbar(f)
        scroll.pack(side='right', fill='y')
        self._log_text = tk.Text(f, font=FONT_SM, bg=C['bg'], fg=C['muted'],
                                  bd=0, highlightthickness=0, yscrollcommand=scroll.set)
        self._log_text.pack(side='left', fill='both', expand=True, padx=(10, 0), pady=(0, 10))
        scroll.config(command=self._log_text.yview)

    def _toggle_conexion(self):
        if self.ws_conectado:
            self._desconectar()
        else:
            self._conectar()

    def _conectar(self):
        self._btn_conectar.config(text="CONECTANDO...", state='disabled')
        self._log("Iniciando conexión WebSocket...")
        from threading import Thread
        t = Thread(target=self._ws_loop, daemon=True)
        t.start()

    def _desconectar(self):
        self.ws_conectado = False
        self._actualizar_estado_ws(False)
        self._btn_conectar.config(text="CONECTAR", state='normal')

    def _ws_loop(self):
        asyncio.run(self._ws_loop_async())

    async def _ws_loop_async(self):
        while True:
            try:
                async with websockets.connect(WS_URL, origin=ORIGIN) as ws:
                    self.ws_conectado = True
                    self.root.after(0, lambda: self._actualizar_estado_ws(True))
                    self._log("Conectado al servidor")
                    await ws.send(json.dumps({'type': 'bind', 'uid': str(random.randint(1000, 9999))}))
                    self.blue_pre, self.red_pre = 0, 0
                    self.blue_t35, self.red_t35 = 0, 0

                    while True:
                        msg = await ws.recv()
                        data = json.loads(msg)
                        t = data.get('type')
                        timestamp = datetime.now().strftime("%H:%M:%S")

                        if t == 'ping':
                            await ws.send(json.dumps({'type': 'pong'}))
                            continue

                        if t in ('open_draw', 'start', 'game_start', 'game_init'):
                            self.ronda_actual = data.get('id') or data.get('issue') or data.get('round')
                            self.tick_actual = 0
                            self.dif_t25 = 0
                            self.dif_t33 = 0
                            self.blue_t35 = 0
                            self.red_t35  = 0
                            self.aceleracion = 0.0
                            self.estabilidad = "ESTABLE"
                            self.root.after(0, lambda: self._actualizar_ronda())
                            self._log(f"[NEW] Ronda {self.ronda_actual}")

                        if t == 'total_bet':
                            self.tick_actual += 1
                            blue = float(data.get('blue', 0))
                            red = float(data.get('red', 0))
                            total = blue + red
                            blue_p = (blue / total) * 100 if total > 0 else 50
                            red_p = (red / total) * 100 if total > 0 else 50
                            self.blue_pre, self.red_pre = blue, red
                            dif = round(abs(blue_p - red_p), 2)
                            self.dif_actual = dif

                            if self.tick_actual == 25:
                                self.dif_t25 = dif
                            if self.tick_actual == 33:
                                self.dif_t33 = dif
                                self.aceleracion = round(dif - self.dif_t25, 2)
                                self.estabilidad = "ESTABLE" if abs(self.aceleracion) < 3 else "VOLATIL"
                            if self.tick_actual == 35:
                                self.blue_t35 = blue
                                self.red_t35  = red

                            self.tick_data.append({'tick': self.tick_actual, 'blue': blue, 'red': red, 'dif': dif})
                            self.root.after(0, lambda b=blue, r=red, d=dif: self._actualizar_barras(b, r, d))

                        if t == 'drawed':
                            res = 'blue' if 'blue' in data.get('result', '').lower() else 'red'
                            self.root.after(0, lambda r=res: self._procesar_resultado(r))

            except Exception as e:
                self.ws_conectado = False
                self.root.after(0, lambda: self._actualizar_estado_ws(False))
                self._log(f"Error: {e}")
                await asyncio.sleep(5)

    def _procesar_resultado(self, winner):
        self.total_rondas += 1
        # Usar snapshot de T35 (alineado con live); fallback al último tick si no se llegó a T35
        _b = self.blue_t35 if self.blue_t35 > 0 else self.blue_pre
        _r = self.red_t35  if self.red_t35  > 0 else self.red_pre
        color_mayor = "BLUE" if _b > _r else "RED"
        gano_mayor = 1 if winner.upper() == color_mayor else 0
        self.racha_sesion.append(gano_mayor)
        if len(self.racha_sesion) > 10:
            self.racha_sesion.pop(0)
        if gano_mayor:
            self.total_ganados_mayor += 1

        winrate = round((sum(self.racha_sesion) / len(self.racha_sesion)) * 100, 1)
        dif_t33 = self.dif_t33 if self.dif_t33 > 0 else self.dif_actual
        rango = self._calcular_rango(dif_t33)
        modo = self._calcular_modo(winrate)

        self._stats_labels['rondas'].config(text=f"Rondas: {self.total_rondas}")
        self._stats_labels['ganados'].config(text=f"Mayor Gana: {self.total_ganados_mayor}")
        ratio = round((self.total_ganados_mayor / self.total_rondas) * 100, 1) if self.total_rondas > 0 else 0
        self._stats_labels['ratio'].config(text=f"Ratio Mayor: {ratio}%")
        racha_str = self._get_racha_str()
        self._stats_labels['racha_actual'].config(text=f"Racha: {racha_str}")
        self._stats_labels['dif_t25'].config(text=f"Dif T25: {self.dif_t25:.2f}%")
        self._stats_labels['dif_t33'].config(text=f"Dif T33: {dif_t33:.2f}%")
        self._stats_labels['modo'].config(text=f"Modo: {modo}")
        self._stats_labels['rango'].config(text=f"Rango: {rango}")

        self._historial_listbox.insert(0, f"{winner.upper()} | {color_mayor} | {modo} | {rango}")
        if self._historial_listbox.size() > 20:
            self._historial_listbox.delete(20)

        self._log(f"[*] RESULTADO: {winner.upper()} | MayorGana: {bool(gano_mayor)} | "
                  f"Racha: {winrate}% | Rango: {rango} | Modo: {modo} | "
                  f"Est: {self.estabilidad} | Acel: {self.aceleracion}")

        self._guardar_txt(winner, gano_mayor, winrate, rango, modo)
        self.tick_data.clear()

    def _guardar_txt(self, winner, gano_mayor, winrate, rango, modo):
        # Usar snapshot de T35 (alineado con live); fallback al último tick si no se llegó a T35
        _b = self.blue_t35 if self.blue_t35 > 0 else self.blue_pre
        _r = self.red_t35  if self.red_t35  > 0 else self.red_pre
        vol_blue = round(_b, 2)
        vol_red  = round(_r, 2)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        color_mayor = "BLUE" if _b > _r else "RED"
        acierto = (winner.upper() == color_mayor)
        dif_t33 = self.dif_t33 if self.dif_t33 > 0 else self.dif_actual
        linea = (f"[{timestamp}] | RONDA: {self.ronda_actual} | GANADOR: {winner.upper()} | "
                 f"MAYOR: {color_mayor} | ACIERTO: {acierto} | WINRATE: {winrate}% | "
                 f"RANGO: {rango} | MODO: {modo} | EST: {self.estabilidad} | "
                 f"ACEL: {self.aceleracion} | DIF_T25: {self.dif_t25:.2f}% | "
                 f"DIF_T33: {dif_t33:.2f}% | BLUE: {vol_blue} | RED: {vol_red}")
        try:
            with open(OUTPUT_TXT, 'a', encoding='utf-8') as f:
                f.write(linea + '\n')
        except Exception as e:
            self._log(f"Error guardando TXT: {e}")

    def _calcular_rango(self, dif):
        if dif < 5: return "0-5"
        elif dif < 10: return "5-10"
        elif dif < 15: return "10-15"
        elif dif < 20: return "15-20"
        elif dif < 25: return "20-25"
        elif dif < 30: return "25-30"
        elif dif < 35: return "30-35"
        elif dif < 40: return "35-40"
        elif dif < 45: return "40-45"
        elif dif < 50: return "45-50"
        else: return "+50"

    def _calcular_modo(self, winrate):
        if winrate >= 60: return "DIRECTO"
        elif winrate <= 40: return "INVERSO"
        else: return "SKIP"

    def _get_racha_str(self):
        if not self.racha_sesion:
            return "---"
        ultimos = self.racha_sesion[-5:]
        return ''.join(['✓' if x else '✗' for x in ultimos])

    def _log(self, msg):
        self._log_text.insert('end', f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        self._log_text.see('end')

    def _actualizar_loop(self):
        self._lbl_clock.config(text=datetime.now().strftime("%H:%M:%S"))
        self.root.after(1000, self._actualizar_loop)


if __name__ == "__main__":
    app = ReconstructorDashboard()
    app.root.mainloop()