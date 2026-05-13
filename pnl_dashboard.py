"""PNL DASHBOARD"""
import ctypes
import winsound
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import MultipleLocator

import json
import queue
import threading
import time

from tkinter import messagebox
from pnl_config import (C, FONT_MONO, FONT_MONO_B, FONT_BIG, FONT_SM, FONT_TITLE,
                         INPUT_TXT, INPUT_WS, CONFIG_FILE, LIVE_HIST_FILE,
                         FILTROS_CURVA, ORDEN_RANGOS, FILTRO_HIST_FILE,
                         DECISION_HIST_FILE, FILTROS_LONG_FILE, COOLDOWN_FILE)
from pnl_data import parsear, parsear_websocket, curva_pnl, curva_pnl_ep, curva_pnl_umbral
from pnl_live import LiveMonitor
from pnl_panels import PanelFiltros, PanelLive, HistoricoApuestasPanel, hablar
from pnl_decision_panel import DecisionHistoryWindow
from pnl_curvas_panel import CurvasWindow


class PnlDashboard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PNL DASHBOARD")
        self.root.configure(bg=C['bg'])
        self.root.resizable(True, True)
        self._all_ops = []
        self._fuente = 'live'
        self._filtro_hist      = self._cargar_filtro_hist_archivo()
        self._filtro_hist_base = {k: v for k, v in self._filtro_hist.items()}  # base histórica inmutable
        self._ops_historia     = self._autocargar_live_history()
        self._objetivo_sheets  = None   # PNL objetivo leído de Sheets Variables
        self._mult_maximo      = 5      # MULT_MAXIMO leído de Sheets (default 5)
        self._fases_ep_win     = None   # referencia a la ventana FASES EP abierta
        self._fases_ep_geo     = None   # última posición/tamaño de la ventana FASES EP
        self._solo_base_mode   = False
        self._ventana_filtros  = None  # se inicializa como StringVar en _construir_ui

        # Estado para título de ventana
        self._titulo_filtro = ""
        self._titulo_color  = "---"
        self._titulo_pnl    = 0.0

        # Explicaciones cargadas desde Sheets (idx → texto)
        self._explicaciones_sheets: dict = {}

        # Worksheet de Variables cacheado para leer cada ronda
        self._ws_variables = None

        # Sonido suave al final de cada ronda (controlado desde Sheets)
        self._sonido_suave = False

        # Balance inicial para histórico de apuestas (modificable desde BALANCES)
        self._balance_historico_inicio = 0.0
        self._round_counter = 0

        # Rachas de pérdidas para VUELTA_BASE
        self._racha_perdidas = 0
        self._filtro_racha = None
        self._vuelta_base_bloqueo = False  # True → no salir de Base aunque auto-selector quiera

        # Cooldown de filtros: {idx: timestamp_fin} — filtro en cooldown no se auto-selecciona
        self._cooldown_filtros: dict = self._cargar_cooldown()

        # Live monitor
        self._live_q       = queue.Queue()
        self._live_monitor = LiveMonitor(self._live_q)
        self._poll_job     = None

        self._restaurar_geometria()
        self._construir_ui()
        if self._ops_historia:
            self._lbl_hist.config(text=f"auto({len(self._ops_historia)})", fg='#FF8C00')
        if self._solo_base_mode:
            self._activar_solo_base()

        self.root.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.root.update()
        self._cargar_datos()
        # Cargar reconstructor_data_AI.txt al inicio (independiente de la fuente activa)
        # para tener los datos T35 disponibles sin tener que cambiar a modo data_ai
        try:
            if INPUT_TXT.exists():
                self._all_ops = parsear(str(INPUT_TXT))
                print(f"[INIT] reconstructor_data_AI.txt → {len(self._all_ops)} rondas cargadas")
        except Exception as e:
            print(f"[INIT] Error cargando reconstructor_data_AI.txt: {e}")
        self._inicializar_filtro_hist()   # bootstrap filtros 1-n desde decisiones históricas
        import pnl_filtros_cache as _fc; _fc._cargar_desde_archivo()  # cache disponible desde arranque
        threading.Thread(target=self._cargar_explicaciones_sheets_bg, daemon=True).start()
        threading.Thread(target=self._cargar_variables_sheets_bg, daemon=True).start()
        self._poll_live()
        self.root.after(200, self.panel_live.actualizar_ui)
        # Refrescar histórico con datos persistentes al arranque
        try:
            _saldo = self._historico_apuestas.refrescar(
                [], self._balance_historico_inicio)
            self.panel_live._saldo_global_historico = _saldo
        except Exception:
            pass

    def _cargar_cooldown(self) -> dict:
        try:
            if COOLDOWN_FILE.exists():
                raw = json.loads(COOLDOWN_FILE.read_text(encoding='utf-8'))
                now = time.time()
                return {int(k): v for k, v in raw.items() if v > now}
        except Exception as e:
            print(f"[COOLDOWN] Error cargando: {e}")
        return {}

    def _guardar_cooldown(self):
        try:
            COOLDOWN_FILE.write_text(
                json.dumps(self._cooldown_filtros, ensure_ascii=False, indent=2),
                encoding='utf-8')
        except Exception as e:
            print(f"[COOLDOWN] Error guardando: {e}")

    def _limpiar_cooldown(self):
        now = time.time()
        antes = len(self._cooldown_filtros)
        self._cooldown_filtros = {k: v for k, v in self._cooldown_filtros.items() if v > now}
        if len(self._cooldown_filtros) < antes:
            self._guardar_cooldown()

    def _restaurar_geometria(self):
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            w = max(cfg['w'], 3010)
            self.root.geometry(f"{w}x{cfg['h']}+{cfg['x']}+{cfg['y']}")
            self._solo_base_mode = bool(cfg.get('solo_base', False))
            self._balance_historico_inicio = float(cfg.get('balance_historico', 0.0))
        except Exception:
            self.root.geometry("3100x1000")

    def _cerrar(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
            self._poll_job = None
        self.root.update_idletasks()
        # Guardar geometría de la ventana principal
        geo = self.root.geometry()
        try:
            size, pos = geo.split('+', 1)
            w, h = size.split('x')
            x, y = pos.split('+')
            CONFIG_FILE.write_text(json.dumps({'w': int(w), 'h': int(h), 'x': int(x), 'y': int(y),
                                               'solo_base': self._solo_base_mode,
                                               'balance_historico': self._balance_historico_inicio}))
        except Exception:
            pass
        # Guardar geometría de la ventana histórico si está abierta
        try:
            from pnl_decision_panel import DecisionHistoryWindow
            inst = DecisionHistoryWindow._instancia
            if inst and inst.winfo_exists():
                inst._guardar_geometria()
        except Exception:
            pass
        # Parar LiveMonitor (cierra websocket + thread daemon)
        try:
            mon = getattr(self.panel_live, '_monitor', None)
            if mon is not None:
                mon.detener()
        except Exception:
            pass
        # Terminar subproceso esperar_ronda.py si sigue vivo
        try:
            proc = getattr(self.panel_live, '_proc_espera', None)
            if proc is not None and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
        self.root.destroy()
        import sys
        sys.exit(0)

    def _construir_ui(self):
        # Header — botones compactados para que entren todos en una línea.
        hf = tk.Frame(self.root, bg='#020810', height=46)
        hf.pack(fill='x')
        hf.pack_propagate(False)
        tk.Frame(hf, bg=C['accent'], height=3).pack(fill='x', side='top')
        inner = tk.Frame(hf, bg='#020810')
        inner.pack(fill='both', expand=True, padx=10)
        _BFONT = ('Consolas', 9, 'bold')   # fuente única para todos los botones
        _LFONT = ('Consolas', 9, 'bold')   # labels secundarios
        tk.Label(inner, text="PNL DASHBOARD", font=('Consolas', 13, 'bold'),
                 bg='#020810', fg=C['accent']).pack(side='left', pady=8)
        self._lbl_archivo = tk.Label(inner, text="", font=FONT_SM,
                                     bg='#020810', fg=C['muted'])
        self._lbl_archivo.pack(side='right', padx=6)

        self._lbl_mejor_filtro = tk.Label(inner, text="MEJOR: —", font=_LFONT,
                                           bg='#020810', fg='#00FF88')
        self._lbl_mejor_filtro.pack(side='right', padx=6)

        # Toggle fuente de datos
        src_frame = tk.Frame(inner, bg='#020810')
        src_frame.pack(side='right', padx=4)
        tk.Label(src_frame, text="FUENTE:", font=_LFONT,
                 bg='#020810', fg=C['muted']).pack(side='left', padx=(0, 3))
        self._btn_data_ai = tk.Button(src_frame, text="DATA_AI", font=_BFONT,
                                       bg='#1A3050', fg=C['accent2'], relief='flat', cursor='hand2',
                                       padx=4, command=lambda: self._cambiar_fuente('data_ai'))
        self._btn_data_ai.pack(side='left', padx=1)
        self._btn_websocket = tk.Button(src_frame, text="WEBSOCKET", font=_BFONT,
                                         bg=C['border'], fg=C['muted'], relief='flat', cursor='hand2',
                                         padx=4, command=lambda: self._cambiar_fuente('websocket'))
        self._btn_websocket.pack(side='left', padx=1)
        self._btn_live_src = tk.Button(src_frame, text="LIVE", font=_BFONT,
                                        bg=C['border'], fg=C['muted'], relief='flat', cursor='hand2',
                                        padx=4, command=lambda: self._cambiar_fuente('live'))
        self._btn_live_src.pack(side='left', padx=1)

        # Marcar fuente inicial
        self._btn_data_ai.config(bg=C['border'], fg=C['muted'])
        self._btn_live_src.config(bg='#1A2A10', fg='#FFD700')

        # Botón para abrir ventana de histórico de decisiones
        tk.Button(inner, text="HISTÓRICO", font=_BFONT,
                  bg='#2A1A4A', fg=C['accent'], relief='flat', cursor='hand2',
                  padx=5, command=self._abrir_historico_decisiones).pack(
                  side='right', padx=3)

        # Botón para recargar pnl_decision_history.json desde disco
        tk.Button(inner, text="↻", font=_BFONT,
                  bg='#1A3A2A', fg='#7DFFA0', relief='flat', cursor='hand2',
                  padx=4, command=self._recargar_decisiones).pack(
                  side='right', padx=1)

        # Botón para abrir ventana de curvas de filtros
        tk.Button(inner, text="CURVAS", font=_BFONT,
                  bg='#1A2A3A', fg='#00D4FF', relief='flat', cursor='hand2',
                  padx=5, command=self._abrir_curvas).pack(side='right', padx=3)

        # Botón FASES EP
        tk.Button(inner, text="FASES EP", font=_BFONT,
                  bg='#1A2A3A', fg='#00D4FF', relief='flat', cursor='hand2',
                  padx=5, command=self._abrir_fases_ep).pack(side='right', padx=1)

        # Botón para poner a cero todos los datos
        tk.Button(inner, text="A CERO", font=_BFONT,
                  bg='#3A0A0A', fg='#FF4444', relief='flat', cursor='hand2',
                  padx=5, command=self._poner_a_cero).pack(side='right', padx=3)

        # Botón análisis de confianza
        tk.Button(inner, text="CONFIANZA", font=_BFONT,
                  bg='#1A3A2A', fg=C['accent2'], relief='flat', cursor='hand2',
                  padx=5, command=self._abrir_ventana_confianza).pack(
                  side='right', padx=0)

        # Botón ajuste de balances
        tk.Button(inner, text="BALANCES", font=_BFONT,
                  bg='#2A1A1A', fg='#FF9900', relief='flat', cursor='hand2',
                  padx=5, command=self._abrir_ajuste_balances).pack(
                  side='right', padx=3)

        # Botón cargar historia previa para EP UMBRAL
        self._lbl_hist = tk.Label(inner, text="sin hist.", font=('Consolas', 8),
                                  bg=C['bg'], fg='#4A6080', width=10, anchor='w')
        self._lbl_hist.pack(side='right', padx=(0, 1))
        tk.Button(inner, text="HIST PREV", font=_BFONT,
                  bg='#1A2A1A', fg='#FF8C00', relief='flat', cursor='hand2',
                  padx=5, command=self._cargar_historia_previa).pack(side='right', padx=3)

        # Botón solo-BASE
        self._btn_solo_base = tk.Button(inner, text="SOLO BASE", font=_BFONT,
                  bg='#1A1A3A', fg='#FFD700', relief='flat', cursor='hand2',
                  padx=5, command=self._activar_solo_base)
        self._btn_solo_base.pack(side='right', padx=3)

        # Variable EP GATE compartida entre PanelFiltros (checkbox) y PanelLive (lógica)
        self._ep_gate_var = tk.BooleanVar(value=False)
        self._ep_gate_ventana = None   # int = últimas N ops por (rango,modo); None = TODOS. Override desde Sheets (EP_GATE_VENTANA).

        body = tk.Frame(self.root, bg=C['bg'])
        body.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        # Columna extremo derecho: HISTÓRICO APOSTADAS (pack primero → queda a la derecha)
        historico_col = tk.Frame(body, bg=C['bg'], width=530)
        historico_col.pack(side='right', fill='both', padx=(4, 0))
        historico_col.pack_propagate(False)
        self._historico_apuestas = HistoricoApuestasPanel(historico_col)
        self._historico_apuestas.pack(fill='both', expand=True)

        # Columna LIVE
        live_col = tk.Frame(body, bg=C['bg'], width=640)
        live_col.pack(side='right', fill='both', padx=(4, 0))
        live_col.pack_propagate(False)
        self.panel_live = PanelLive(live_col, self._live_monitor,
                                    self._get_filtro_state, self._on_resultado,
                                    on_senal=self._on_senal_cambio,
                                    ep_gate_activo=lambda: self._ep_gate_var.get(),
                                    on_auto_reeval=self._seleccionar_mejor_filtro,
                                    solo_base_activo=lambda: self._solo_base_mode,
                                    get_ep_wr=self._get_wr_rango)
        self.panel_live.pack(fill='both', expand=True)

        # Columna derecha: filtros
        right = tk.Frame(body, bg=C['bg'], width=460)
        right.pack(side='right', fill='both', padx=(4, 0))
        right.pack_propagate(False)

        # Columna centro: resumen + rangos + filtros
        center = tk.Frame(body, bg=C['bg'], width=500)
        center.pack(side='right', fill='both', padx=(4, 0))
        center.pack_propagate(False)

        # Resumen
        rf = tk.Frame(center, bg=C['panel'], bd=1, relief='solid')
        rf.pack(fill='x', pady=(0, 4))
        tk.Label(rf, text="RESUMEN (filtro activo)", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(pady=8)
        self._lbl_balance = tk.Label(rf, text="+0.00", font=FONT_BIG, bg=C['panel'], fg=C['accent2'])
        self._lbl_balance.pack()
        self._lbl_live_badge = tk.Label(rf, text="", font=FONT_SM, bg=C['panel'], fg=C['warn'])
        self._lbl_live_badge.pack()
        self._stats = {}
        for key in ['ops', 'aciertos', 'fallos', 'winrate', 'max', 'min', 'drawdown', 'ratio']:
            lbl = tk.Label(rf, text="", font=FONT_MONO, bg=C['panel'], fg=C['text'])
            lbl.pack(anchor='w', padx=16, pady=1)
            self._stats[key] = lbl
        tk.Frame(rf, height=6, bg=C['panel']).pack()

        # Contenedor inferior: Rangos + Filtros (se reparten el espacio)
        lower = tk.Frame(center, bg=C['bg'])
        lower.pack(fill='both', expand=True)

        # Fuente compacta para ambas tablas
        _FT = ('Consolas', 8)
        _FTB = ('Consolas', 8, 'bold')

        # ── Helper: barra de título con toggle ▼/▶ ─────────────────
        def _titulo_toggle(parent, texto, on_toggle):
            bar = tk.Frame(parent, bg=C['panel'], cursor='hand2')
            bar.pack(fill='x')
            btn = tk.Label(bar, text="▼", font=('Consolas', 10, 'bold'),
                           bg=C['panel'], fg=C['accent'], cursor='hand2')
            btn.pack(side='right', padx=6, pady=4)
            tk.Label(bar, text=texto, font=FONT_TITLE,
                     bg=C['panel'], fg=C['accent']).pack(side='left', pady=4, padx=8)
            bar.bind('<Button-1>', lambda e: on_toggle())
            btn.bind('<Button-1>', lambda e: on_toggle())
            return btn

        # Rangos — columnas: Rango(6) Ops(4) WR%(5) PNL(7)
        _RCOLS = [("Rango", 6), ("Ops", 4), ("WR%", 5), ("PNL", 7)]
        rgf = tk.Frame(lower, bg=C['panel'], bd=1, relief='solid')
        rgf.pack(fill='x', pady=(0, 4))
        self._rango_body = tk.Frame(rgf, bg=C['panel'])

        def _toggle_rango(self=self):
            if self._rango_body.winfo_ismapped():
                self._rango_body.pack_forget()
                self._btn_toggle_rango.config(text="▶")
            else:
                self._rango_body.pack(fill='x')
                self._btn_toggle_rango.config(text="▼")

        self._btn_toggle_rango = _titulo_toggle(rgf, "PNL POR RANGO", _toggle_rango)
        header = tk.Frame(self._rango_body, bg=C['panel'])
        header.pack(fill='x', padx=12)
        for txt, w in _RCOLS:
            tk.Label(header, text=txt, font=_FTB, bg=C['panel'],
                     fg=C['muted'], width=w, anchor='w').pack(side='left')
        tk.Frame(self._rango_body, bg=C['border'], height=1).pack(fill='x', padx=10, pady=1)
        scroll_f = tk.Frame(self._rango_body, bg=C['panel'])
        scroll_f.pack(fill='x', padx=12, pady=(0, 4))
        self._rango_body.pack(fill='x')   # visible por defecto
        self._rango_labels = {}
        for rango in ORDEN_RANGOS:
            row = tk.Frame(scroll_f, bg=C['panel'])
            row.pack(fill='x')
            labels = []
            for _, w in _RCOLS:
                lbl = tk.Label(row, text="", font=_FT, bg=C['panel'],
                               fg=C['text'], width=w, anchor='w')
                lbl.pack(side='left')
                labels.append(lbl)
            self._rango_labels[rango] = labels

        # PNL por filtro — anchos en chars (Consolas): margen amplio entre cols
        _FCOLS = [("Filtro", 18), ("Ops", 5), ("WR%", 6), ("Bal.Filt", 9), ("/op", 8)]
        fgf = tk.Frame(lower, bg=C['panel'], bd=1, relief='solid')
        fgf.pack(fill='x', pady=(0, 4))
        self._filtro_body = tk.Frame(fgf, bg=C['panel'])

        def _toggle_filtro(self=self):
            if self._filtro_body.winfo_ismapped():
                self._filtro_body.pack_forget()
                self._btn_toggle_filtro.config(text="▶")
            else:
                self._filtro_body.pack(fill='x')
                self._btn_toggle_filtro.config(text="▼")

        self._btn_toggle_filtro = _titulo_toggle(fgf, "PNL POR FILTRO", _toggle_filtro)
        fhdr = tk.Frame(self._filtro_body, bg=C['panel'])
        fhdr.pack(fill='x', padx=4)
        for txt, w in _FCOLS:
            tk.Label(fhdr, text=txt, font=_FTB, bg=C['panel'],
                     fg=C['muted'], width=w, anchor='w').pack(side='left')
        tk.Frame(self._filtro_body, bg=C['border'], height=1).pack(fill='x', padx=10, pady=1)
        frows_f = tk.Frame(self._filtro_body, bg=C['panel'])
        frows_f.pack(fill='x', padx=4, pady=(0, 4))
        self._filtro_body.pack(fill='x')   # visible por defecto
        self._filtro_perf_rows = []
        for entry in FILTROS_CURVA:
            nombre_f = entry[0]
            row = tk.Frame(frows_f, bg=C['panel'])
            row.pack(fill='x')
            lbl_n = tk.Label(row, text=nombre_f[:18], font=_FT,
                             bg=C['panel'], fg=C['muted'], width=18, anchor='w')
            lbl_n.pack(side='left')
            lbls = []
            for _, w in _FCOLS[1:]:
                lbl = tk.Label(row, text="-", font=_FT, bg=C['panel'],
                               fg=C['muted'], width=w, anchor='w')
                lbl.pack(side='left')
                lbls.append(lbl)
            self._filtro_perf_rows.append((row, lbl_n, *lbls))

        # PNL por confianza — columnas: CONF(4) Ops(3) WR%(4) PNL(5) /op(5)
        _CCOLS = [("CONF", 4), ("Ops", 3), ("WR%", 4), ("PNL", 5), ("/op", 5)]
        cgf = tk.Frame(lower, bg=C['panel'], bd=1, relief='solid')
        cgf.pack(fill='both', expand=True)
        self._conf_body = tk.Frame(cgf, bg=C['panel'])

        def _toggle_conf(self=self):
            if self._conf_body.winfo_ismapped():
                self._conf_body.pack_forget()
                self._btn_toggle_conf.config(text="▶")
            else:
                self._conf_body.pack(fill='both', expand=True)
                self._btn_toggle_conf.config(text="▼")

        self._btn_toggle_conf = _titulo_toggle(cgf, "PNL POR CONFIANZA", _toggle_conf)
        chdr = tk.Frame(self._conf_body, bg=C['panel'])
        chdr.pack(fill='x', padx=4)
        for txt, w in _CCOLS:
            tk.Label(chdr, text=txt, font=_FTB, bg=C['panel'],
                     fg=C['muted'], width=w, anchor='w').pack(side='left')
        tk.Frame(self._conf_body, bg=C['border'], height=1).pack(fill='x', padx=10, pady=1)
        crows_f = tk.Frame(self._conf_body, bg=C['panel'])
        crows_f.pack(fill='x', padx=4, pady=(0, 4))
        self._conf_body.pack(fill='both', expand=True)   # visible por defecto
        self._conf_perf_rows = []
        for v in range(1, 9):
            row = tk.Frame(crows_f, bg=C['panel'])
            row.pack(fill='x')
            lbls_c = []
            for j, (_, w) in enumerate(_CCOLS):
                txt0 = str(v) if j == 0 else "-"
                lbl = tk.Label(row, text=txt0, font=_FTB if j == 0 else _FT,
                               bg=C['panel'], fg=C['muted'], width=w, anchor='w')
                lbl.pack(side='left')
                lbls_c.append(lbl)
            self._conf_perf_rows.append((row, *lbls_c))

        # ── Columna estado: filtros en uso + notas ──────────────────
        estado_col = tk.Frame(body, bg=C['bg'], width=210)
        estado_col.pack(side='left', fill='both', padx=(0, 4))
        estado_col.pack_propagate(False)
        self._construir_panel_estado(estado_col)

        # Columna izquierda: grafica (pack ultimo para que ocupe el resto)
        left = tk.Frame(body, bg=C['bg'])
        left.pack(side='left', fill='both', expand=True)

        # Barra de control de ventana de visualización
        ctrl_bar = tk.Frame(left, bg=C['bg'])
        ctrl_bar.pack(fill='x', pady=(0, 2))
        tk.Label(ctrl_bar, text="VENTANA:", font=('Consolas', 10),
                 bg=C['bg'], fg=C['muted']).pack(side='left', padx=(4, 2))
        self._ventana_filtros = tk.StringVar(value='TODOS')
        for op in ('50', '100', '200', 'TODOS'):
            tk.Radiobutton(ctrl_bar, text=op, variable=self._ventana_filtros, value=op,
                           font=('Consolas', 10, 'bold'), bg=C['bg'], fg=C['accent2'],
                           selectcolor='#1A2A1A', activebackground=C['bg'],
                           command=self._dibujar).pack(side='left', padx=4)

        # Normalización ÷mult: cuando está activo cada delta se divide por su mult
        # (todas las rondas pesan igual, eventos de mult alto no distorsionan)
        self._normalizar_mult = tk.BooleanVar(value=False)
        tk.Checkbutton(ctrl_bar, text="Igualar apuestas", variable=self._normalizar_mult,
                       font=('Consolas', 10, 'bold'),
                       bg=C['bg'], fg='#FFD700', selectcolor='#1A1A3A',
                       activebackground=C['bg'], activeforeground='#FFD700',
                       command=self._dibujar).pack(side='left', padx=(10, 4))

        # Filtro de mult: excluye rondas cuyo mult no coincide
        tk.Label(ctrl_bar, text="MULT:", font=('Consolas', 10),
                 bg=C['bg'], fg=C['muted']).pack(side='left', padx=(10, 2))
        self._filtro_mult = tk.StringVar(value='TODOS')
        _mult_om = tk.OptionMenu(ctrl_bar, self._filtro_mult,
                                  'TODOS', '0.1', '0.2', '0.3', '0.5', '1', '2', '3', '5',
                                  command=lambda _: self._dibujar())
        _mult_om.config(bg='#0D2137', fg=C['accent'], font=('Consolas', 10, 'bold'),
                        relief='flat', highlightthickness=0,
                        activebackground='#1A3050', activeforeground=C['accent2'])
        _mult_om.pack(side='left', padx=2)

        gf = tk.Frame(left, bg=C['panel'], bd=1, relief='solid')
        gf.pack(fill='both', expand=True)
        self._fig, (self._ax, self._ax2) = plt.subplots(
            2, 1, sharex=False,
            gridspec_kw={'height_ratios': [1, 2]}
        )
        self._fig.patch.set_facecolor(C['bg'])
        self._ax.set_facecolor(C['panel'])
        self._ax2.set_facecolor(C['panel'])
        self._canvas = FigureCanvasTkAgg(self._fig, master=gf)
        self._canvas.get_tk_widget().pack(fill='both', expand=True, padx=6, pady=6)

        ff = tk.Frame(right, bg=C['panel'], bd=1, relief='solid')
        ff.pack(fill='both', expand=True)
        self.panel_filtros = PanelFiltros(ff, on_change=self._dibujar,
                                          on_auto=self._seleccionar_mejor_filtro,
                                          ep_gate_var=self._ep_gate_var,
                                          on_filtro_select=self._actualizar_notas,
                                          get_ops=lambda: (
                                              self.panel_live.live_all_ops if self._fuente == 'live'
                                              else self._all_ops + self.panel_live.live_all_ops))
        self.panel_filtros.pack(fill='both', expand=True)

        # Conectar proyección: labels en PanelFiltros, actualizados desde PanelLive.actualizar_ui
        self.panel_live._proj_lbls        = self.panel_filtros._proj_lbls
        self.panel_live._proj_lbls_base   = self.panel_filtros._proj_lbls_base
        self.panel_live._proj_lbls_filtro = self.panel_filtros._proj_lbls_filtro

        # Conectar INV: PanelLive lee el estado del botón INV de PanelFiltros
        self.panel_live._base_inv_activo = lambda: self.panel_filtros._base_inv_active

    def _construir_panel_estado(self, parent):
        """Panel izquierdo: EP gate + filtros activos + notas."""
        pf = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        pf.pack(fill='both', expand=True)

        # ── Título ──────────────────────────────────────────────
        tk.Label(pf, text="AUTO-APUESTA", font=FONT_TITLE,
                 bg=C['panel'], fg=C['accent']).pack(pady=(8, 2))
        tk.Frame(pf, bg=C['border'], height=1).pack(fill='x', padx=8)

        # ── EP Gate ─────────────────────────────────────────────
        ep_f = tk.Frame(pf, bg=C['panel'])
        ep_f.pack(fill='x', padx=10, pady=(6, 2))
        tk.Label(ep_f, text="EP GATE", font=FONT_MONO_B,
                 bg=C['panel'], fg=C['muted']).pack(anchor='w')
        self._lbl_ep_gate = tk.Label(ep_f, text="OBS  0/20",
                                      font=('Consolas', 13, 'bold'),
                                      bg=C['panel'], fg=C['warn'])
        self._lbl_ep_gate.pack(anchor='w')
        tk.Frame(pf, bg=C['border'], height=1).pack(fill='x', padx=8, pady=(6, 2))

        self._filtros_estado_lbls = []   # eliminado de UI, mantenido para compatibilidad

        # ── Notas ────────────────────────────────────────────────
        notas_hdr = tk.Frame(pf, bg=C['panel'])
        notas_hdr.pack(fill='x', padx=10, pady=(4, 2))
        tk.Label(notas_hdr, text="NOTAS", font=FONT_MONO_B,
                 bg=C['panel'], fg=C['muted']).pack(side='left')
        tk.Button(notas_hdr, text="🔊", font=('Consolas', 11),
                  bg=C['panel'], fg=C['accent'], relief='flat', cursor='hand2',
                  command=self._leer_notas).pack(side='left', padx=(6, 0))
        tk.Button(notas_hdr, text="VALIDAR", font=('Consolas', 10, 'bold'),
                  bg='#0A2A0A', fg='#00FF88', relief='flat', cursor='hand2',
                  command=self._guardar_notas).pack(side='left', padx=(6, 0))
        self._notas_text = tk.Text(pf, bg='#020810', fg=C['text'],
                                    font=('Consolas', 10), relief='flat',
                                    wrap='word', insertbackground=C['accent'])
        self._notas_text.pack(fill='both', expand=True, padx=8, pady=(0, 8))

    def _calcular_curva(self, ops, entry):
        """Dispatch curve calculation for a FILTROS_CURVA entry.
        Returns (curva, n_ac, n_total, cambios)."""
        nombre, color, filtro, contrario, raw = entry
        if filtro is None:
            return curva_pnl_ep(ops, contrarian=contrario)
        elif filtro == 'EP_WR70':
            return curva_pnl_ep(ops, min_wr_dir=70, contrarian=contrario)
        elif filtro == 'EP_UMBRAL':
            return curva_pnl_umbral(ops, umbral=62.0, min_ops=5,
                                    ops_hist=self._ops_historia or None,
                                    mult_maximo=self._mult_maximo,
                                    adaptativo=True,
                                    ventana_regimen=30, warmup=10,
                                    umbral_alto=0.55, umbral_bajo=0.50)
        elif isinstance(filtro, str):
            return ([], 0, 0, [])   # BAL_FILTRO y otros marcadores: calculados aparte
        else:
            curva, n_ac, n_total = curva_pnl(ops, filtro, contrarian=contrario, raw=raw)
            return curva, n_ac, n_total, []

    def _actualizar_panel_estado(self):
        """Actualiza EP gate status y lista de filtros activos."""
        # EP gate
        n_sess = self.panel_live.ep_session_ops
        EP_V = 20
        if n_sess < EP_V:
            self._lbl_ep_gate.config(text=f"OBS  {n_sess}/{EP_V}", fg=C['warn'])
        else:
            ep_dir, ep_pasa, ep_mot = self.panel_live._ep_rolling_dir(min_wr_dir=70)
            if ep_pasa:
                self._lbl_ep_gate.config(text=f"ACTIVO  {ep_dir}", fg=C['accent2'])
            else:
                self._lbl_ep_gate.config(
                    text=f"SKIP  {(ep_mot or ep_dir)[:12]}", fg=C['accent3'])

        # Filtros activos
        ops = (self.panel_live.live_all_ops if self._fuente == 'live'
               else self._all_ops + self.panel_live.live_all_ops)
        for i, (row, dot, lbl_n, lbl_p) in enumerate(self._filtros_estado_lbls):
            entry  = FILTROS_CURVA[i]
            activo = self.panel_filtros.filtro_vars[i].get()
            sel    = (i == self.panel_filtros.selected_filter)
            if activo and ops:
                try:
                    curva, _, _, _ = self._calcular_curva(ops, entry)
                    pnl = curva[-1] if curva else 0.0
                except Exception:
                    pnl = 0.0
                lbl_p.config(text=f"{pnl:+.1f}", fg=C['accent2'] if pnl >= 0 else C['accent3'])
                lbl_n.config(fg=C['white'] if sel else C['text'])
                dot.config(fg=entry[1])
            else:
                lbl_p.config(text="")
                lbl_n.config(fg=C['muted'])
                dot.config(fg='#1A2A3A')
            row.pack(fill='x', pady=1)

    def _cargar_filtros_info(self):
        """Lee explicaciones desde la cache compartida (pnl_filtros_cache).
        Si la cache está vacía, intenta migrar desde filtros_explicacion.json como fallback."""
        import pnl_filtros_cache
        stats = pnl_filtros_cache.get_stats()
        if stats:
            return {f['idx']: f.get('explicacion', '') for f in stats}
        # Fallback: JSON legacy (migración automática en primera ejecución)
        try:
            ruta = Path(__file__).parent / 'filtros_explicacion.json'
            datos = json.loads(ruta.read_text(encoding='utf-8'))
            return {e['id']: e.get('explicacion', '') for e in datos}
        except Exception:
            return {}

    def _leer_notas(self):
        """Lee en voz alta el contenido actual del cuadro de notas."""
        texto = self._notas_text.get('1.0', 'end').strip()
        if texto:
            hablar(texto)

    def _guardar_notas(self):
        """Guarda el texto del cuadro NOTAS en la cache + Sheets (columna Explicacion)."""
        import pnl_filtros_cache
        idx         = self.panel_filtros.selected_filter
        nuevo_texto = self._notas_text.get('1.0', 'end').strip()

        # ── 0. Actualizar dict Sheets en memoria (fuente primaria de notas) ──────
        self._explicaciones_sheets[idx] = nuevo_texto

        # ── 1. Actualizar cache local ──────────────────────────────────────────
        stats = pnl_filtros_cache.get_stats()
        encontrado = False
        for f in stats:
            if f.get('idx') == idx:
                f['explicacion'] = nuevo_texto
                encontrado = True
                break
        if not encontrado:
            stats.append({'idx': idx,
                          'nombre': FILTROS_CURVA[idx][0] if idx < len(FILTROS_CURVA) else str(idx),
                          'color':  FILTROS_CURVA[idx][1] if idx < len(FILTROS_CURVA) else '#FFFFFF',
                          'explicacion': nuevo_texto})
            stats.sort(key=lambda f: f.get('idx', 0))
        pnl_filtros_cache.actualizar(stats)

        # ── 2. Actualizar celda Explicacion en Sheets (background) ────────────
        fila_sheets = idx + 2   # fila 1 = cabecera; filtro idx=0 → fila 2
        def _tarea(fila=fila_sheets, texto=nuevo_texto):
            try:
                import gspread
                from oauth2client.service_account import ServiceAccountCredentials
                scope = ["https://spreadsheets.google.com/feeds",
                         "https://www.googleapis.com/auth/drive"]
                cred_path = str(Path(__file__).parent / 'credenciales.json')
                creds   = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
                cliente = gspread.authorize(creds)
                ws = cliente.open("Pk_Arena").worksheet("Filtros")
                ws.update_cell(fila, 9, texto)   # columna 9 = Explicacion
            except Exception as e:
                print(f"[VALIDAR] Error Sheets: {e}")
        threading.Thread(target=_tarea, daemon=True).start()

    def _cargar_explicaciones_sheets_bg(self):
        """Carga la columna Explicacion de la pestaña Filtros en Sheets (hilo background).
        Rellena self._explicaciones_sheets y refresca el cuadro NOTAS si ya hay filtro seleccionado."""
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            scope = ["https://spreadsheets.google.com/feeds",
                     "https://www.googleapis.com/auth/drive"]
            cred_path = str(Path(__file__).parent / 'credenciales.json')
            creds   = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
            cliente = gspread.authorize(creds)
            ws      = cliente.open("Pk_Arena").worksheet("Filtros")
            filas   = ws.get_all_values()   # [cabecera, fila0, fila1, ...]
            nuevo   = {}
            for fila in filas[1:]:          # saltar cabecera
                try:
                    idx_f       = int(fila[0])
                    explicacion = fila[8] if len(fila) > 8 else ''
                    nuevo[idx_f] = explicacion
                except Exception:
                    pass
            self._explicaciones_sheets = nuevo
            print(f"[NOTAS] {len(nuevo)} explicaciones cargadas desde Sheets")
            # Refrescar el cuadro si ya hay un filtro seleccionado
            self.root.after(0, lambda: self._actualizar_notas(
                self.panel_filtros.selected_filter))
        except Exception as e:
            print(f"[NOTAS] Error cargando desde Sheets: {e}")

    def _cargar_variables_sheets_bg(self):
        """Autentica en Sheets y cachea el worksheet 'Variables' para leer cada ronda.
        También hace una lectura inicial para que los valores estén disponibles
        desde el arranque sin esperar a la primera ronda."""
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            scope = ["https://spreadsheets.google.com/feeds",
                     "https://www.googleapis.com/auth/drive"]
            cred_path = str(Path(__file__).parent / 'credenciales.json')
            creds   = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
            cliente = gspread.authorize(creds)
            self._ws_variables = cliente.open("Pk_Arena").worksheet("Variables")
            print("[VARS] Worksheet 'Variables' cacheado")
            # Lectura inicial
            self._leer_variables_sheets()
        except Exception as e:
            print(f"[VARS] Error autenticando: {e}")

    def _leer_variables_sheets(self):
        """Lee la pestaña Variables de Sheets (usa el worksheet cacheado) y aplica
        FILTER_PARAMS, BOTS_USO y TELEGRAM. Se llama al arranque y en cada ronda."""
        from pnl_config import FILTER_PARAMS
        ws = self._ws_variables
        if ws is None:
            return
        try:
            filas = ws.get_all_values()
        except Exception as e:
            print(f"[VARS] Error leyendo datos: {e}")
            return
        for fila in filas:
            if not fila:
                continue
            clave = str(fila[0]).strip().upper()
            val   = str(fila[1]).strip() if len(fila) > 1 else ''
            if clave in ('BOTS_USO', 'BOT_USO', 'BOTS') and val:
                _v = val.replace(' ', '')
                if _v in ('1', '2', '1-2') and hasattr(self, 'panel_live'):
                    self.panel_live._bots_uso = _v
                    print(f"[VARS] BOTS_USO={_v}")
            elif clave in ('TELEGRAM', 'TELEGRAM_ON', 'TG') and val:
                _on = val.upper() in ('ON', '1', 'TRUE', 'SI', 'SÍ', 'YES')
                if hasattr(self, 'panel_live'):
                    self.panel_live._tg_activo = _on
                    def _upd_btn(on=_on, pl=self.panel_live):
                        try:
                            btn = getattr(pl, '_btn_tg', None)
                            if btn is None:
                                return
                            if on:
                                btn.config(text="TG ON", bg='#0A3320', fg='#00FF88')
                            else:
                                btn.config(text="TG OFF", bg='#2A0A0A', fg='#FF4444')
                        except Exception:
                            pass
                    self.root.after(0, _upd_btn)
                    print(f"[VARS] TELEGRAM={'ON' if _on else 'OFF'}")
            elif clave in ('DIR_WR_70', 'DIR_WR70') and val:
                try:
                    FILTER_PARAMS['wr_70'] = int(float(val.replace(',', '.')))
                    print(f"[VARS] DIR_WR_70 → {FILTER_PARAMS['wr_70']}")
                except Exception:
                    pass
            elif clave in ('DIR_WR_80', 'DIR_WR80') and val:
                try:
                    FILTER_PARAMS['wr_80'] = int(float(val.replace(',', '.')))
                    print(f"[VARS] DIR_WR_80 → {FILTER_PARAMS['wr_80']}")
                except Exception:
                    pass
            elif clave in ('WR_PERDEDORA', 'WR_MAYORIA') and val:
                try:
                    FILTER_PARAMS['wr_40'] = int(float(val.replace(',', '.')))
                    print(f"[VARS] WR_PERDEDORA → {FILTER_PARAMS['wr_40']}")
                except Exception:
                    pass
            elif clave in ('ACEL_UMBRAL', 'VOLATIL_UMBRAL') and val:
                try:
                    FILTER_PARAMS['acel_umbral'] = float(val.replace(',', '.'))
                    print(f"[VARS] ACEL_UMBRAL → {FILTER_PARAMS['acel_umbral']}")
                except Exception:
                    pass
            elif clave in ('SONIDO_SUAVE', 'SONIDO', 'SOUND') and val:
                self._sonido_suave = val.upper() in ('SI', 'SÍ', '1', 'TRUE', 'ON', 'YES')
                print(f"[VARS] SONIDO_SUAVE → {'SI' if self._sonido_suave else 'NO'}")
            elif clave in ('VUELTA_BASE', 'VUELTA') and val:
                try:
                    FILTER_PARAMS['vuelta_base'] = int(float(val.replace(',', '.')))
                    print(f"[VARS] VUELTA_BASE → {FILTER_PARAMS['vuelta_base']}")
                except Exception:
                    pass

    def _actualizar_notas(self, idx):
        """Rellena el cuadro NOTAS con la explicación del filtro seleccionado."""
        # Prioridad: Sheets > cache local > filtros_explicacion.json
        explicacion = self._explicaciones_sheets.get(idx, '')
        if not explicacion:
            explicacion = self._cargar_filtros_info().get(idx, "")
        # Fallback directo a filtros_explicacion.json si la cache no tiene contenido
        if not explicacion:
            try:
                ruta = Path(__file__).parent / 'filtros_explicacion.json'
                datos = json.loads(ruta.read_text(encoding='utf-8'))
                for e in datos:
                    if e.get('id') == idx:
                        explicacion = e.get('explicacion', '')
                        break
            except Exception:
                pass
        self._notas_text.config(state='normal')
        self._notas_text.delete('1.0', 'end')
        if explicacion:
            self._notas_text.insert('1.0', explicacion)
            self._notas_text.see('1.0')

    def _abrir_ajuste_balances(self):
        """Ventana para ajustar manualmente balance_real y balance_filtro de inicio de sesión."""
        win = tk.Toplevel(self.root)
        win.title("AJUSTAR BALANCES")
        win.configure(bg=C['bg'])
        win.resizable(False, False)
        win.grab_set()

        pl = self.panel_live
        br_actual = round(pl._balance_real_inicio + sum(
            d.get('pnl_base') or 0 for d in pl.get_decisiones()[pl._session_decision_start:]
            if d.get('pnl_base') is not None), 2)
        bf_actual = round(pl._balance_filtro_inicio + sum(
            d.get('pnl') or 0 for d in pl.get_decisiones()[pl._session_decision_start:]
            if d.get('decision') == 'APOSTADA' and d.get('pnl') is not None), 2)

        tk.Label(win, text="AJUSTAR BALANCES", font=FONT_TITLE,
                 bg=C['bg'], fg=C['accent']).pack(pady=(16, 4), padx=24)
        tk.Label(win, text="Los nuevos valores se aplican como inicio de sesión.",
                 font=FONT_SM, bg=C['bg'], fg=C['muted']).pack(pady=(0, 12))

        grid = tk.Frame(win, bg=C['bg'])
        grid.pack(padx=24, pady=(0, 16))

        def _fila(row, etiqueta, valor_actual):
            tk.Label(grid, text=etiqueta, font=FONT_MONO_B,
                     bg=C['bg'], fg=C['text'], anchor='w', width=18).grid(
                     row=row, column=0, sticky='w', pady=6, padx=(0, 12))
            tk.Label(grid, text=f"Actual: {valor_actual:+.2f}",
                     font=FONT_MONO, bg=C['bg'], fg=C['muted'], width=14).grid(
                     row=row, column=1, sticky='w', padx=(0, 12))
            var = tk.StringVar(value=f"{valor_actual:.2f}")
            ent = tk.Entry(grid, textvariable=var, font=FONT_MONO,
                           bg='#0D1F33', fg=C['accent2'], insertbackground=C['accent'],
                           relief='flat', bd=2, width=12)
            ent.grid(row=row, column=2, sticky='w')
            return var

        var_real   = _fila(0, "Balance REAL",   br_actual)
        var_filtro = _fila(1, "Balance FILTRO",  bf_actual)
        var_hist   = _fila(2, "Balance HISTÓRICO", self._balance_historico_inicio)

        def _aplicar():
            try:
                nuevo_real   = float(var_real.get().replace(',', '.'))
                nuevo_filtro = float(var_filtro.get().replace(',', '.'))
                nuevo_hist   = float(var_hist.get().replace(',', '.'))
            except ValueError:
                tk.Label(win, text="Valores inválidos", font=FONT_SM,
                         bg=C['bg'], fg=C['accent3']).pack()
                return
            # Ajustar inicio de sesión para que el acumulado cuadre con el nuevo valor
            delta_real   = sum(d.get('pnl_base') or 0
                               for d in pl.get_decisiones()[pl._session_decision_start:]
                               if d.get('pnl_base') is not None)
            delta_filtro = sum(d.get('pnl') or 0
                               for d in pl.get_decisiones()[pl._session_decision_start:]
                               if d.get('decision') == 'APOSTADA' and d.get('pnl') is not None)
            pl._balance_real_inicio   = round(nuevo_real   - delta_real,   2)
            pl._balance_filtro_inicio = round(nuevo_filtro - delta_filtro, 2)
            self._balance_historico_inicio = round(nuevo_hist, 2)
            pl.actualizar_ui()
            self._dibujar()
            try:
                _saldo = self._historico_apuestas.refrescar(
                    pl.get_decisiones(), self._balance_historico_inicio)
                self.panel_live._saldo_global_historico = _saldo
            except Exception:
                pass
            win.destroy()

        bf = tk.Frame(win, bg=C['bg'])
        bf.pack(pady=(0, 16))
        tk.Button(bf, text="APLICAR", font=FONT_MONO_B,
                  bg='#1A3A2A', fg=C['accent2'], relief='flat', cursor='hand2',
                  padx=20, pady=6, command=_aplicar).pack(side='left', padx=8)
        tk.Button(bf, text="CANCELAR", font=FONT_MONO_B,
                  bg=C['border'], fg=C['muted'], relief='flat', cursor='hand2',
                  padx=20, pady=6, command=win.destroy).pack(side='left', padx=8)

        win.update_idletasks()
        pw = self.root.winfo_x() + self.root.winfo_width() // 2
        ph = self.root.winfo_y() + self.root.winfo_height() // 2
        ww, wh = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{pw - ww//2}+{ph - wh//2}")

    def _recargar_decisiones(self):
        """Recarga pnl_decision_history.json desde disco, reemplazando la lista
        en memoria. Útil tras editar el JSON externamente. Refresca la ventana
        del histórico si está abierta, **invalida la caché de filtros**
        (pnl_filtro_history.json) y dispara un redibujo del dashboard.

        Sin esta invalidación, las curvas EP (idx 13-16) seguirían mostrando
        el valor stale del cache de disco, con dirección contraria a las
        nuevas decisiones."""
        from pnl_decision_panel import cargar_decisiones
        try:
            nuevas = cargar_decisiones()
            self.panel_live._decisiones = nuevas
            self.panel_live._session_decision_start = min(
                self.panel_live._session_decision_start, len(nuevas))
            self.panel_live._refrescar_decision_window()

            # ── Invalidar caché de filtros y reconstruir desde decisiones ──
            self._filtro_hist      = {}
            self._filtro_hist_base = {}
            try:
                self._inicializar_filtro_hist()        # rebuild idx 0 y 1..12 simples
            except Exception:
                pass
            try:
                dec_ops = self._ops_desde_decisiones()
                self._reconstruir_filtro_hist(dec_ops) # rebuild idx EP (13-16)
            except Exception:
                pass
            # Redibujar gráficas del dashboard
            try:
                self._dibujar()
            except Exception:
                pass
            print(f"[RECARGAR] {len(nuevas)} decisiones cargadas — caché filtros invalidada")
        except Exception as exc:
            print(f"[RECARGAR] ERROR: {exc}")

    def _abrir_historico_decisiones(self):
        """Abre (o trae al frente) la ventana de histórico de decisiones."""
        win = DecisionHistoryWindow.abrir_o_focus(
            self.root,
            self.panel_live.get_decisiones,
            on_clear=self.panel_live._panel_live_clear_decisiones,
            get_filtro_nombre=lambda: FILTROS_CURVA[self._get_filtro_state()[0]][0],
            get_pnl_ep_umbral=lambda: self.panel_live.simular_pnl_ep_umbral_sesion(
                self._mult_maximo, solo_filtro_activo=True))
        self.panel_live.set_decision_window(win)

    def _abrir_curvas(self):
        """Abre (o trae al frente) la ventana de curvas de filtros."""
        win = CurvasWindow.abrir_o_focus(
            self.root,
            get_filtro_hist=lambda: self._filtro_hist,
            get_filtros_curva=lambda: FILTROS_CURVA)
        if win:
            win.refrescar()

    def _poner_a_cero(self):
        """Borra todos los datos históricos y reinicia el estado en memoria."""
        self.root.lift()
        self.root.focus_force()
        respuesta = messagebox.askokcancel(
                "⚠ PONER A CERO",
                "¿Borrar TODOS los datos?\n\n"
                "  • Historial de decisiones\n"
                "  • Historial de curvas de filtros\n"
                "  • Historial de rondas live\n\n"
                "Esta acción NO se puede deshacer.",
                icon='warning', parent=self.root)
        if not respuesta:
            return

        # ── 1. Vaciar archivos JSON ──────────────────────────────────────────
        try:
            DECISION_HIST_FILE.write_text('[]', encoding='utf-8')
        except Exception:
            pass
        try:
            FILTRO_HIST_FILE.write_text('{}', encoding='utf-8')
        except Exception:
            pass
        try:
            LIVE_HIST_FILE.write_text(
                json.dumps({'ops': [], 'raw': []}), encoding='utf-8')
        except Exception:
            pass

        # ── 2. Resetear estado en memoria del dashboard ──────────────────────
        self._filtro_hist      = {}
        self._filtro_hist_base = {}

        # ── 3. Resetear estado en memoria de PanelLive ──────────────────────
        pl = self.panel_live
        pl._decisiones               = []
        pl._session_decision_start   = 0
        pl._balance_real_inicio      = 0.0
        pl._balance_filtro_inicio    = 0.0
        pl._live_all_ops             = []
        pl._live_ops                 = []

        # ── 4. Refrescar ventanas abiertas ───────────────────────────────────
        try:
            if CurvasWindow._instancia and CurvasWindow._instancia.winfo_exists():
                CurvasWindow._instancia.refrescar()
        except Exception:
            pass
        try:
            from pnl_decision_panel import DecisionHistoryWindow
            if (DecisionHistoryWindow._instancia and
                    DecisionHistoryWindow._instancia.winfo_exists()):
                DecisionHistoryWindow._instancia.refrescar()
        except Exception:
            pass

        # ── 5. Redibujar gráfica ─────────────────────────────────────────────
        self._dibujar()

    def _autocargar_live_history(self):
        try:
            import json as _j
            from collections import deque as _dq
            data = _j.loads(LIVE_HIST_FILE.read_text(encoding='utf-8'))
            historial = _dq(maxlen=20)
            ops = []
            for ev in data.get('raw', []):
                rango = ev.get('rango', '')
                acierto = ev.get('acierto')
                if not rango or acierto is None:
                    continue
                historial.append(1 if acierto else 0)
                wr = sum(historial) / len(historial) * 100 if historial else 50.0
                modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
                ops.append({
                    'rango': rango, 'modo': modo, 'skip': modo == 'SKIP',
                    'acierto': bool(acierto), 'wr': round(wr, 2),
                    'est': ev.get('est', 'ESTABLE'), 'acel': float(ev.get('acel', 0)),
                })
            return ops if ops else []
        except Exception:
            pass
        return []

    def _cargar_historia_previa(self):
        """Carga ops históricas para inicializar WR del filtro EP UMBRAL."""
        ruta = filedialog.askopenfilename(
            title="Seleccionar archivo de historia previa",
            filetypes=[("Todos los soportados", "*.txt *.log *.json"),
                       ("TXT / LOG", "*.txt *.log"),
                       ("JSON", "*.json"),
                       ("Todos", "*.*")])
        if not ruta:
            return
        ruta = Path(ruta)
        if ruta.suffix.lower() == '.json':
            try:
                from backtest_ep import parsear_json_base
                self._ops_historia = parsear_json_base(ruta)
            except Exception as e:
                self._lbl_hist.config(text=f"error: {e}", fg='#FF4444')
                return
        else:
            self._ops_historia = parsear(str(ruta))
        n = len(self._ops_historia)
        nombre_corto = ruta.stem[:8] if len(ruta.stem) > 8 else ruta.stem
        self._lbl_hist.config(text=f"{nombre_corto}({n})", fg='#FF8C00')
        self._dibujar()

    def _abrir_fases_ep(self):
        """Abre ventana con mini-cards por (rango, modo) mostrando fases EP UMBRAL."""
        if self._fases_ep_win and self._fases_ep_win.winfo_exists():
            self._fases_ep_win.destroy()
        self._fases_ep_win = None
        ops_main = (self.panel_live.live_all_ops if self._fuente == 'live'
                    else self._all_ops + self.panel_live.live_all_ops)
        ops_hist = self._ops_historia or []

        # Calcular WR y sparkline por (rango, modo) usando misma lógica que EP UMBRAL
        from collections import defaultdict
        from pnl_config import EP_UMBRAL_MIN
        EP_UMBRAL = getattr(self.panel_live, '_ep_umbral_min', EP_UMBRAL_MIN)
        MIN_OPS   = 5   # alineado con _get_wr_rango (live)

        # Primera pasada: stats globales (hist + main)
        stats = defaultdict(lambda: defaultdict(lambda: {'ops': 0, 'ganadas': 0}))
        for op in ops_hist + ops_main:
            r, m = op.get('rango', ''), op.get('modo', '')
            if m not in ('DIRECTO', 'INVERSO'):
                continue
            stats[r][m]['ops'] += 1
            if op.get('acierto', False):
                stats[r][m]['ganadas'] += 1

        # Determinar mejor_modo por rango
        mejor_modo = {}
        for rango, modos in stats.items():
            d = modos.get('DIRECTO', {'ops': 0, 'ganadas': 0})
            i = modos.get('INVERSO', {'ops': 0, 'ganadas': 0})
            d_wr = d['ganadas'] / d['ops'] * 100 if d['ops'] >= MIN_OPS else 0.0
            i_wr = i['ganadas'] / i['ops'] * 100 if i['ops'] >= MIN_OPS else 0.0
            if d_wr >= EP_UMBRAL or i_wr >= EP_UMBRAL:
                mejor_modo[rango] = ('DIRECTO', d_wr) if d_wr >= i_wr else ('INVERSO', i_wr)
            else:
                mejor_modo[rango] = (None, 0.0)

        # Segunda pasada: construir cards por (rango, modo) con sparkline solo de ops_main
        cards = []
        claves_vistas = set()
        from pnl_config import ORDEN_RANGOS
        for rango in ORDEN_RANGOS:
            for modo in ('DIRECTO', 'INVERSO'):
                clave = (rango, modo)
                if clave in claves_vistas:
                    continue
                claves_vistas.add(clave)

                # Stats históricas (hist + main)
                st = stats[rango][modo]
                n_hist_total = st['ops']
                n_hist_prev  = sum(1 for o in ops_hist
                                   if o.get('rango') == rango and o.get('modo') == modo)
                n_hist_live  = n_hist_total - n_hist_prev
                wr_hist = st['ganadas'] / n_hist_total * 100 if n_hist_total >= MIN_OPS else 0.0
                activo  = wr_hist >= EP_UMBRAL

                # Sparkline: PNL acumulado solo en ops_main para este (rango, modo)
                _mm_val = mejor_modo.get(rango, (None, 0.0))
                mm, wr_m = _mm_val if _mm_val else (None, 0.0)
                bal_curve = []
                acum = 0.0
                for op in ops_main:
                    if op.get('rango') != rango or op.get('modo') != modo:
                        continue
                    if mm and wr_m >= EP_UMBRAL and activo:
                        gano = op.get('acierto', False) if mm == op.get('modo') else not op.get('acierto', False)
                        acum += 0.9 if gano else -1.0
                    bal_curve.append(acum)

                if n_hist_total == 0:
                    continue

                cards.append({
                    'rango': rango, 'modo': modo,
                    'n_hist_prev': n_hist_prev, 'n_hist_live': n_hist_live,
                    'n_total': n_hist_total, 'wr': wr_hist,
                    'activo': activo, 'bal': bal_curve,
                })

        # Ordenar: activos primero, luego por WR desc
        cards.sort(key=lambda c: (not c['activo'], -c['wr']))

        # PNL EP UMBRAL actual (total acumulado sesión live, lógica ADAPTATIVA)
        from pnl_data import curva_pnl_umbral as _curva_umbral
        # Tomar params adaptativos de panel_live (configurables vía Sheets)
        _v_reg = getattr(self.panel_live, '_ep_umbral_outcomes', None)
        _v_max = _v_reg.maxlen if _v_reg is not None else 50
        _w_arm = getattr(self.panel_live, '_ep_umbral_warmup', 5)
        _u_hi  = getattr(self.panel_live, '_ep_umbral_hi', 0.55)
        _u_lo  = getattr(self.panel_live, '_ep_umbral_lo', 0.50)
        _curva_ep, _n_ac_ep, _n_bets_ep, _ = _curva_umbral(
            ops_main, umbral=62.0, min_ops=5,
            ops_hist=ops_hist or None, mult_maximo=self._mult_maximo,
            adaptativo=True, ventana_regimen=_v_max, warmup=_w_arm,
            umbral_alto=_u_hi, umbral_bajo=_u_lo)
        pnl_ep_actual = _curva_ep[-1] if _curva_ep else 0.0

        # Acumulación: rangos activos con ≥ MIN_OPS ops live
        activos_total  = sum(1 for c in cards if c['activo'])
        activos_acum   = sum(1 for c in cards if c['activo'] and c['n_hist_live'] >= MIN_OPS)
        faltan_acum    = activos_total - activos_acum

        # Intervalo promedio entre rondas (segundos) a partir de timestamps
        import datetime as _dt
        def _parse_ts(ts):
            try:
                return _dt.datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')
            except Exception:
                return None
        _ts_list = [_parse_ts(o.get('timestamp', '')) for o in ops_main]
        _ts_list = [t for t in _ts_list if t is not None]
        _seg_por_ronda = None
        if len(_ts_list) >= 2:
            total_seg = (_ts_list[-1] - _ts_list[0]).total_seconds()
            _seg_por_ronda = total_seg / (len(_ts_list) - 1)

        def _fmt_tiempo(seg):
            seg = int(seg)
            if seg >= 3600:
                return f"{seg//3600}h {(seg%3600)//60}m"
            elif seg >= 60:
                return f"{seg//60}m {seg%60}s"
            return f"{seg}s"

        # Construir ventana Toplevel
        top = tk.Toplevel(self.root)
        top.title("FASES EP — Por rango/modo")
        top.configure(bg=C['bg'])
        _geo = getattr(self, '_fases_ep_geo', None)
        top.geometry(_geo if _geo else "620x720")
        self._fases_ep_win = top

        def _on_close_fases():
            self._fases_ep_geo = top.geometry()
            self._fases_ep_win = None
            top.destroy()
        top.protocol("WM_DELETE_WINDOW", _on_close_fases)

        # Cabecera
        hdr = tk.Frame(top, bg='#020810')
        hdr.pack(fill='x')
        tk.Frame(hdr, bg=C['accent'], height=2).pack(fill='x')
        tk.Label(hdr, text="FASES EP  —  umbral 53.2%", font=('Consolas', 12, 'bold'),
                 bg='#020810', fg=C['accent']).pack(side='left', padx=12, pady=6)
        n_hist_lbl = f"hist:{len(ops_hist)}  live:{len(ops_main)}"
        tk.Label(hdr, text=n_hist_lbl, font=('Consolas', 10),
                 bg='#020810', fg=C['muted']).pack(side='right', padx=12)
        tk.Button(hdr, text='↺', font=('Consolas', 11, 'bold'),
                  bg='#020810', fg=C['accent'], relief='flat', cursor='hand2',
                  command=self._abrir_fases_ep).pack(side='right', padx=4)

        # ── Panel de progreso ──────────────────────────────────────
        prog_frame = tk.Frame(top, bg='#0A1628', pady=6, padx=10)
        prog_frame.pack(fill='x')
        tk.Frame(prog_frame, bg=C['border'], height=1).pack(fill='x', pady=(0, 6))

        # Fila 1: acumulación
        r1 = tk.Frame(prog_frame, bg='#0A1628')
        r1.pack(fill='x', pady=1)
        tk.Label(r1, text="ACUM", font=('Consolas', 9, 'bold'),
                 bg='#0A1628', fg=C['muted'], width=6, anchor='w').pack(side='left')
        if faltan_acum == 0:
            acum_txt = f"✓ {activos_total}/{activos_total} rangos con datos"
            acum_col = C['accent2']
        else:
            acum_txt = f"{activos_acum}/{activos_total} rangos  —  faltan {faltan_acum} por acumular ≥{MIN_OPS} live"
            acum_col = C['warn']
        tk.Label(r1, text=acum_txt, font=('Consolas', 9),
                 bg='#0A1628', fg=acum_col).pack(side='left', padx=4)

        # PNL teórico EP UMBRAL — solo rondas donde el filtro activo era EP UMBRAL
        pnl_live_ep = 0.0
        if hasattr(self, 'panel_live'):
            pnl_live_ep = self.panel_live.simular_pnl_ep_umbral_sesion(
                self._mult_maximo, solo_filtro_activo=True)

        obj = self._objetivo_sheets

        def _progreso_pct(val, obj_val):
            if obj_val is None or obj_val == 0:
                return ""
            pct = val / obj_val * 100
            return f"  {pct:.1f}%"

        # Fila 2: SIM — backtest EP UMBRAL sobre todos los datos (lookahead)
        r2 = tk.Frame(prog_frame, bg='#0A1628')
        r2.pack(fill='x', pady=1)
        tk.Label(r2, text="SIM", font=('Consolas', 9, 'bold'),
                 bg='#0A1628', fg=C['muted'], width=6, anchor='w').pack(side='left')
        sim_col = C['accent2'] if pnl_ep_actual >= 0 else C['accent3']
        sim_txt = f"{pnl_ep_actual:+.1f}  (backtest EP UMBRAL hist+live)"
        tk.Label(r2, text=sim_txt, font=('Consolas', 9),
                 bg='#0A1628', fg=sim_col).pack(side='left', padx=4)

        # Fila 3: LIVE — PNL real de EP UMBRAL en esta sesión
        r3_meta = tk.Frame(prog_frame, bg='#0A1628')
        r3_meta.pack(fill='x', pady=1)
        tk.Label(r3_meta, text="LIVE", font=('Consolas', 9, 'bold'),
                 bg='#0A1628', fg=C['muted'], width=6, anchor='w').pack(side='left')
        live_col = C['accent2'] if pnl_live_ep >= 0 else C['accent3']
        if obj is not None:
            falta    = obj - pnl_live_ep
            meta_txt = (f"{pnl_live_ep:+.1f}  obj {obj:+.1f}"
                        f"  falta {falta:+.1f}{_progreso_pct(pnl_live_ep, obj)}")
            meta_col = C['accent2'] if falta <= 0 else C['warn']
        else:
            meta_txt = f"{pnl_live_ep:+.1f}  —  cargando objetivo…"
            meta_col = live_col
        self._lbl_meta_fases = tk.Label(r3_meta, text=meta_txt, font=('Consolas', 9),
                                         bg='#0A1628', fg=meta_col)
        self._lbl_meta_fases.pack(side='left', padx=4)

        # Fila 3: previsión temporal
        r3 = tk.Frame(prog_frame, bg='#0A1628')
        r3.pack(fill='x', pady=1)
        tk.Label(r3, text="PREV", font=('Consolas', 9, 'bold'),
                 bg='#0A1628', fg=C['muted'], width=6, anchor='w').pack(side='left')

        # Varianza del PNL por ronda (para rango de desviación)
        import math as _math
        _diffs = [_curva_ep[i] - _curva_ep[i-1] for i in range(1, len(_curva_ep))]
        _diffs_activos = [d for d in _diffs if d != 0.0]
        _std_ronda = 0.0
        if len(_diffs_activos) >= 2:
            _media = sum(_diffs_activos) / len(_diffs_activos)
            _var   = sum((d - _media) ** 2 for d in _diffs_activos) / len(_diffs_activos)
            _std_ronda = _math.sqrt(_var)

        def _calcular_prev(obj_val):
            if obj_val is None or _seg_por_ronda is None:
                return "—  sin datos de tiempo", C['muted']
            falta_pnl = obj_val - pnl_live_ep
            if falta_pnl <= 0:
                return "✓ objetivo alcanzado", C['accent2']
            n_rondas = len(ops_main)
            if n_rondas == 0 or pnl_live_ep <= 0:
                return "—  ritmo insuficiente", C['muted']
            pnl_por_ronda = pnl_live_ep / n_rondas
            if pnl_por_ronda <= 0:
                return "—  ritmo negativo", C['accent3']
            rondas_restantes = falta_pnl / pnl_por_ronda
            seg_restantes    = rondas_restantes * _seg_por_ronda
            eta = _dt.datetime.now() + _dt.timedelta(seconds=seg_restantes)
            eta_txt = eta.strftime('%H:%M')
            # Desviación: ±1σ sobre el PNL proyectado respecto al objetivo
            if obj_val != 0 and _std_ronda > 0 and rondas_restantes > 0:
                desv_pnl = _std_ronda * _math.sqrt(rondas_restantes)
                desv_pct = desv_pnl / abs(obj_val) * 100
                desv_txt = f"  ±{desv_pct:.1f}%"
            else:
                desv_txt = ""
            return (f"~{_fmt_tiempo(seg_restantes)}  ({int(rondas_restantes)} r)  "
                    f"ETA {eta_txt}{desv_txt}"), C['accent']

        prev_txt, prev_col = _calcular_prev(obj)
        self._lbl_prev_fases = tk.Label(r3, text=prev_txt, font=('Consolas', 9),
                                         bg='#0A1628', fg=prev_col)
        self._lbl_prev_fases.pack(side='left', padx=4)

        tk.Frame(prog_frame, bg=C['border'], height=1).pack(fill='x', pady=(6, 0))

        # Lanzar lectura de OBJETIVO en background si no está cargado
        def _leer_objetivo_bg():
            try:
                import gspread
                from oauth2client.service_account import ServiceAccountCredentials
                scope = ["https://spreadsheets.google.com/feeds",
                         "https://www.googleapis.com/auth/drive"]
                cred_path = str(Path(__file__).parent / 'credenciales.json')
                creds   = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
                cliente = gspread.authorize(creds)
                ws = cliente.open("Pk_Arena").worksheet("Variables")
                filas = ws.get_all_values()
                for fila in filas:
                    if not fila:
                        continue
                    clave = str(fila[0]).strip().upper()
                    val = str(fila[1]).strip().replace(',', '.') if len(fila) > 1 else ''
                    if clave == 'OBJETIVO':
                        self._objetivo_sheets = float(val) if val else None
                    elif clave == 'MULT_MAXIMO':
                        try:
                            self._mult_maximo = int(float(val)) if val else 5
                        except Exception:
                            self._mult_maximo = 5
                    elif clave == 'WARMUP' and val:
                        try:
                            _w = int(float(val))
                            if hasattr(self, 'panel_live'):
                                self.panel_live._ep_umbral_warmup = max(0, _w)
                        except Exception:
                            pass
                    elif clave in ('EP_UMBRAL_HI', 'UMBRAL_HI') and val:
                        try:
                            _hi = float(val)
                            if _hi > 1:
                                _hi /= 100   # admite formato 55 → 0.55
                            if hasattr(self, 'panel_live'):
                                self.panel_live._ep_umbral_hi = _hi
                        except Exception:
                            pass
                    elif clave in ('EP_UMBRAL_LO', 'UMBRAL_LO') and val:
                        try:
                            _lo = float(val)
                            if _lo > 1:
                                _lo /= 100
                            if hasattr(self, 'panel_live'):
                                self.panel_live._ep_umbral_lo = _lo
                        except Exception:
                            pass
                    elif clave in ('EP_RANGOS_BLOQUEADOS', 'RANGOS_BLOQUEADOS') and hasattr(self, 'panel_live'):
                        _items = [r.strip() for r in (val or '').split(',') if r.strip()]
                        self.panel_live._ep_rangos_bloqueados = set(_items)
                    elif clave in ('EP_UMBRAL_POR_RANGO', 'UMBRAL_POR_RANGO') and hasattr(self, 'panel_live'):
                        # Formato: "45-50:55, 25-30:58"
                        _por_rango = {}
                        for item in (val or '').split(','):
                            if ':' not in item:
                                continue
                            _r, _u = item.split(':', 1)
                            _r = _r.strip()
                            try:
                                _u_v = float(_u.strip().replace(',', '.'))
                                if 0 < _u_v <= 1:
                                    _u_v *= 100
                                _por_rango[_r] = _u_v
                            except Exception:
                                pass
                        self.panel_live._ep_umbral_por_rango = _por_rango
                    elif clave in ('EP_GATE_VENTANA', 'GATE_VENTANA') and val:
                        _v = val.strip().upper()
                        if _v in ('TODOS', 'ALL', '0', '-1', ''):
                            self._ep_gate_ventana = None
                        else:
                            try:
                                _n = int(float(_v))
                                self._ep_gate_ventana = _n if _n > 0 else None
                            except Exception:
                                pass
                    elif clave in ('BOTS_USO', 'BOT_USO', 'BOTS') and val:
                        _v = val.strip().replace(' ', '')
                        if _v in ('1', '2', '1-2'):
                            if hasattr(self, 'panel_live'):
                                self.panel_live._bots_uso = _v
                    elif clave in ('TELEGRAM', 'TELEGRAM_ON', 'TG') and val:
                        _on = val.strip().upper() in ('ON', '1', 'TRUE', 'SI', 'SÍ', 'YES')
                        if hasattr(self, 'panel_live'):
                            self.panel_live._tg_activo = _on
                            try:
                                btn = getattr(self.panel_live, '_btn_tg', None)
                                if btn is not None:
                                    if _on:
                                        btn.config(text="TG ON", bg='#0A3320', fg='#00FF88')
                                    else:
                                        btn.config(text="TG OFF", bg='#2A0A0A', fg='#FF4444')
                            except Exception:
                                pass
                    elif clave == 'EP_UMBRAL_MIN' and val:
                        try:
                            _um = float(val)
                            if 0 < _um <= 1:
                                _um *= 100   # admite formato 0.62 → 62
                            if hasattr(self, 'panel_live'):
                                self.panel_live._ep_umbral_min = _um
                        except Exception:
                            pass
                    elif clave in ('EP_UMBRAL_VENTANA', 'VENTANA_REGIMEN') and val:
                        try:
                            from collections import deque as _dq
                            _v = int(float(val))
                            if hasattr(self, 'panel_live'):
                                # preservar contenido existente al cambiar maxlen
                                _old = list(self.panel_live._ep_umbral_outcomes)
                                self.panel_live._ep_umbral_outcomes = _dq(_old[-_v:], maxlen=_v)
                        except Exception:
                            pass
            except Exception:
                pass
            # Actualizar labels si la ventana sigue abierta
            def _update():
                try:
                    obj2 = self._objetivo_sheets
                    if obj2 is not None:
                        falta2 = obj2 - pnl_ep_actual
                        meta_c2 = C['accent2'] if falta2 <= 0 else C['warn']
                        txt2 = (f"actual {pnl_ep_actual:+.1f}  obj {obj2:+.1f}"
                                f"  falta {falta2:+.1f}{_progreso_pct(obj2)}")
                        self._lbl_meta_fases.config(text=txt2, fg=meta_c2)
                        prev_txt2, prev_col2 = _calcular_prev(obj2)
                        self._lbl_prev_fases.config(text=prev_txt2, fg=prev_col2)
                except Exception:
                    pass
            try:
                top.after(0, _update)
            except Exception:
                pass
        if self._objetivo_sheets is None:
            import threading as _th
            _th.Thread(target=_leer_objetivo_bg, daemon=True).start()

        # Canvas scrollable
        outer = tk.Frame(top, bg=C['bg'])
        outer.pack(fill='both', expand=True, padx=4, pady=4)
        sc = tk.Canvas(outer, bg=C['bg'], highlightthickness=0)
        sb = tk.Scrollbar(outer, orient='vertical', command=sc.yview)
        inner_f = tk.Frame(sc, bg=C['bg'])
        sc.create_window((0, 0), window=inner_f, anchor='nw')
        sc.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        sc.pack(side='left', fill='both', expand=True)

        CARD_BG  = '#0D2137'
        CY       = C['accent']    # #00D4FF
        GN       = C['accent2']   # #00FF88
        RD       = C['accent3']   # #FF3366
        MU       = C['muted']
        TX       = C['text']

        for cd in cards:
            card = tk.Frame(inner_f, bg=CARD_BG, pady=3, padx=6)
            card.pack(fill='x', pady=2, padx=4)

            # Header: rango | modo | WR | estado
            hf2 = tk.Frame(card, bg=CARD_BG)
            hf2.pack(fill='x')
            col_rng = CY if cd['activo'] else MU
            tk.Label(hf2, text=f"{cd['rango']:>4}", font=('Consolas', 10, 'bold'),
                     bg=CARD_BG, fg=col_rng).pack(side='left')
            col_mod = GN if cd['modo'] == 'DIRECTO' else RD
            tk.Label(hf2, text=f"  {cd['modo'][:7]}", font=('Consolas', 10),
                     bg=CARD_BG, fg=col_mod).pack(side='left')
            estado_txt = "ACTIVO" if cd['activo'] else "SKIP"
            estado_col = GN if cd['activo'] else MU
            tk.Label(hf2, text=estado_txt, font=('Consolas', 9, 'bold'),
                     bg=CARD_BG, fg=estado_col).pack(side='right')
            wr_col = GN if cd['wr'] >= 53.2 else (C['warn'] if cd['wr'] >= 45 else RD)
            tk.Label(hf2, text=f"WR {cd['wr']:.1f}%", font=('Consolas', 10, 'bold'),
                     bg=CARD_BG, fg=wr_col).pack(side='right', padx=8)

            # Barra WR con marcador umbral
            BAR_W = 490
            bar_f = tk.Frame(card, bg='#0A1020', height=10, width=BAR_W)
            bar_f.pack(fill='x', pady=(2, 1))
            bar_f.pack_propagate(False)
            fill_w = max(1, int(BAR_W * min(cd['wr'], 100) / 100))
            bar_col = GN if cd['activo'] else ('#2A4A7A' if cd['wr'] > 0 else '#1A2A3A')
            tk.Frame(bar_f, bg=bar_col, width=fill_w, height=10).place(x=0, y=0)
            # Marcador umbral en 53.2%
            x_umbral = int(BAR_W * 53.2 / 100)
            tk.Frame(bar_f, bg=C['warn'], width=2, height=10).place(x=x_umbral, y=0)

            # Leyenda: H:prev  L:live  T:total
            lbl_txt = f"H:{cd['n_hist_prev']}  L:{cd['n_hist_live']}  T:{cd['n_total']}"
            tk.Label(card, text=lbl_txt, font=('Consolas', 8),
                     bg=CARD_BG, fg=MU).pack(anchor='w')

            # Sparkline (solo si hay datos de sesión live)
            bal = cd['bal']
            if bal and len(bal) > 1:
                SPK_W, SPK_H = 490, 32
                spk = tk.Canvas(card, bg='#07111D', width=SPK_W, height=SPK_H,
                                highlightthickness=0)
                spk.pack(pady=(2, 1))
                mn, mx = min(bal), max(bal)
                rng_bal = (mx - mn) if mx != mn else 1
                y0_pct = (0 - mn) / rng_bal
                y0 = SPK_H - max(2, int(y0_pct * (SPK_H - 4))) - 2
                y0 = max(2, min(SPK_H - 2, y0))
                spk.create_line(0, y0, SPK_W, y0, fill='#1A3A5A', dash=(3, 3))
                pts = []
                for k, v in enumerate(bal):
                    x = int(k / (len(bal) - 1) * (SPK_W - 4)) + 2
                    y = SPK_H - int((v - mn) / rng_bal * (SPK_H - 4)) - 2
                    y = max(1, min(SPK_H - 1, y))
                    pts.extend([x, y])
                if len(pts) >= 4:
                    line_col = GN if bal[-1] >= 0 else RD
                    spk.create_line(*pts, fill=line_col, width=2, smooth=True)
                pnl_lbl = f"{bal[-1]:+.1f}"
                spk.create_text(SPK_W - 4, 4, text=pnl_lbl, anchor='ne',
                                fill=GN if bal[-1] >= 0 else RD, font=('Consolas', 8))

        inner_f.update_idletasks()
        sc.configure(scrollregion=sc.bbox('all'))
        sc.bind('<MouseWheel>', lambda e: sc.yview_scroll(-1 * (e.delta // 120), 'units'))
        inner_f.bind('<MouseWheel>', lambda e: sc.yview_scroll(-1 * (e.delta // 120), 'units'))

        def _auto_refresh():
            if self._fases_ep_win and self._fases_ep_win.winfo_exists():
                self._fases_ep_geo = self._fases_ep_win.geometry()
                self._abrir_fases_ep()
        top.after(30_000, _auto_refresh)

    def _activar_solo_base(self):
        """Activa solo el filtro BASE en la gráfica y habilita modo apuesta-siempre-mayor."""
        self._solo_base_mode = True
        self.panel_filtros.seleccion_rapida([0], seleccionado=0)
        self._btn_solo_base.config(bg='#FFD700', fg='#000000', relief='raised', bd=2)

    def _seleccionar_mejor_filtro(self, silencio_si_igual=True):
        import datetime as _dt
        from pathlib import Path as _P
        _lf = _P(__file__).parent / "auto_mejor_log.txt"
        def _w(m):
            try:
                with open(_lf, 'a', encoding='utf-8') as _f:
                    _f.write(f"{_dt.datetime.now().strftime('%H:%M:%S')} {m}\n")
            except Exception:
                pass
        _w(f"_seleccionar_mejor_filtro llamado  solo_base={self._solo_base_mode}  fuente={self._fuente}  pinned={self.panel_filtros._pinned_filter}")
        if getattr(self.panel_filtros, '_pinned_filter', False):
            _w("SALIDA: filtro anclado (pinned) — no se cambia")
            return
        if self._solo_base_mode:
            if silencio_si_igual:
                _w("SALIDA: solo_base_mode activo (auto-reeval)")
                return
            # Click manual → desactivar SOLO BASE y continuar
            _w("Desactivando solo_base_mode por click manual")
            self._solo_base_mode = False
            self._btn_solo_base.config(bg='#1A1A3A', fg='#FFD700', relief='flat', bd=0)
        # ── VUELTA_BASE: si Base está ganando, bloquear cambio ─────────────────
        if getattr(self, '_vuelta_base_bloqueo', False) and self.panel_filtros.selected_filter == 0:
            _w("VUELTA_BASE: Base ganando → se queda")
            return
        # ── Limpiar cooldowns expirados ────────────────────────────────────────
        self._limpiar_cooldown()
        # En LIVE: escoger desde _filtro_hist (misma fuente que la tabla
        # PNL POR FILTRO y el panel FILTRO SELECCIONADO).
        if self._fuente == 'live' and self._filtro_hist:
            # ── Fallback anti-bucle: si el filtro activo no ha apostado en las
            #    últimas N rondas, volver a Base para no quedarse atascado en
            #    SKIPs permanentes. ─────────────────────────────────────────────
            N_INACTIVO = 5
            try:
                cur_idx = self.panel_filtros.selected_filter
                if cur_idx != 0:
                    decs = self.panel_live.get_decisiones()
                    ultimas = [d for d in decs[-N_INACTIVO:] if d.get('winner') is not None]
                    if len(ultimas) >= N_INACTIVO:
                        sin_apostar = all(d.get('decision') != 'APOSTADA' for d in ultimas)
                        if sin_apostar:
                            _w(f"FALLBACK: filtro #{cur_idx} inactivo {N_INACTIVO} rondas → Base")
                            if cur_idx not in self._cooldown_filtros:
                                fin_cd = time.time() + 1800
                                self._cooldown_filtros[cur_idx] = fin_cd
                                _w(f"COOLDOWN: #{cur_idx} por inactividad, hasta "
                                   f"{_dt.datetime.fromtimestamp(fin_cd).strftime('%H:%M')}")
                                self._guardar_cooldown()
                            cambio = (0 != cur_idx)
                            self.panel_filtros.seleccion_rapida([0], seleccionado=0)
                            if cambio:
                                def _sonido_fallback():
                                    try:
                                        import winsound
                                        for f, d in [(800, 80), (600, 80), (400, 150)]:
                                            winsound.Beep(f, d)
                                    except Exception:
                                        pass
                                import threading as _thr
                                _thr.Thread(target=_sonido_fallback, daemon=True).start()
                                hablar("Volviendo a Base")
                            return
            except Exception as _ex:
                _w(f"fallback check error: {_ex}")

            MIN_BETS = 3
            mejor_idx, mejor_pnl = None, float('-inf')
            for i, entry in enumerate(FILTROS_CURVA):
                if entry[2] == 'BAL_FILTRO':
                    continue   # no es comparable, es el balance real
                if i in self._cooldown_filtros:
                    _w(f"  [{i:2}] {entry[0][:20]:<20} → SKIP (cooldown hasta "
                       f"{_dt.datetime.fromtimestamp(self._cooldown_filtros[i]).strftime('%H:%M')})")
                    continue
                hist = self._filtro_hist.get(i)
                if not hist or not hist[0]:
                    continue
                curva_h, _n_ac_h, n_total_h, _ = hist
                if n_total_h < MIN_BETS:
                    continue
                pnl_i = curva_h[-1]
                if pnl_i > mejor_pnl:
                    mejor_pnl, mejor_idx = pnl_i, i
            if mejor_idx is None:
                _w("SALIDA: ningún filtro pasó el mínimo desde _filtro_hist")
                return
            entry_m  = FILTROS_CURVA[mejor_idx]
            nombre_m = entry_m[0]
            fn_m     = entry_m[2]
            es_ep    = (fn_m is None) or isinstance(fn_m, str)
            cambio   = (mejor_idx != self.panel_filtros.selected_filter)
            _w(f"MEJOR (desde _filtro_hist): #{mejor_idx} {nombre_m}  pnl={mejor_pnl:+.2f}  cambio={cambio}  es_ep={es_ep}")
            # ── Cooldown: si cambia a Base (0), el filtro anterior entra en 30 min ──
            if cambio and mejor_idx == 0:
                old_idx = self.panel_filtros.selected_filter
                if old_idx != 0 and old_idx not in self._cooldown_filtros:
                    fin_cooldown = time.time() + 1800
                    self._cooldown_filtros[old_idx] = fin_cooldown
                    _w(f"COOLDOWN: #{old_idx} → Base, bloqueado hasta "
                       f"{_dt.datetime.fromtimestamp(fin_cooldown).strftime('%H:%M')}")
                    self._guardar_cooldown()
            # Auto-activar EP GATE si el mejor es un filtro EP* y aún no está activo
            if es_ep and hasattr(self, '_ep_gate_var') and not self._ep_gate_var.get():
                try:
                    self._ep_gate_var.set(True)
                    _w(f"AUTO-ACTIVADO EP GATE para filtro EP #{mejor_idx}")
                    hablar("E P gate activado")
                except Exception as _ex:
                    _w(f"error activando EP GATE: {_ex}")
            if silencio_si_igual and not cambio:
                return
            self.panel_filtros.seleccion_rapida([0, mejor_idx], seleccionado=mejor_idx)
            # Sonido impactante: arpeggio ascendente + descenso final cuando cambia el filtro
            if cambio:
                def _sonido_cambio_filtro():
                    try:
                        import winsound
                        for f, d in [(523, 70), (659, 70), (784, 70),
                                     (1046, 90), (1568, 130), (1046, 200)]:
                            winsound.Beep(f, d)
                    except Exception:
                        pass
                import threading as _thr
                _thr.Thread(target=_sonido_cambio_filtro, daemon=True).start()
            hablar(f"{mejor_idx} {nombre_m}")
            return

        # Fuera de LIVE: comportamiento clásico sobre ops del websocket/data_ai
        ops = self._all_ops + self.panel_live.live_all_ops
        _w(f"ops disponibles: {len(ops)}")
        self.panel_filtros.seleccionar_mejor(ops, ventana=20,
                                              silencio_si_igual=silencio_si_igual)

    # ── Historial de filtros desde decisiones reales ────────────

    def _ops_desde_decisiones(self):
        """Convierte decisiones de sesión en ops para curva_pnl.
        OBS y SKIP quedan como skip=True → línea plana para todos los filtros.
        Solo las rondas APOSTADA con resultado completo mueven el balance."""
        decs  = self.panel_live.get_decisiones()
        # Incluir TODO el histórico (no solo la sesión actual) para que la
        # gráfica de filtros refleje saldos acumulados completos.
        ops   = []
        for d in decs:
            if d.get('winner') is None:
                continue           # ronda sin resultado aún
            decision = d.get('decision', 'SKIP')
            modo     = d.get('modo', 'SKIP')
            # Si modo BASE (SOLO BASE), derivar modo teórico del WR para filtros
            wr_d = float(d.get('wr') or 50)
            if modo == 'BASE':
                modo = 'DIRECTO' if wr_d >= 60 else ('INVERSO' if wr_d <= 40 else 'SKIP')
            skip = (modo == 'SKIP') or (decision != 'APOSTADA')
            winner_d = (d.get('winner') or '').lower()
            mayor_d_raw = (d.get('mayor') or '').lower()
            winner_norm = 'AZUL' if 'blue' in winner_d else ('ROJO' if 'red' in winner_d else '')
            mayor_norm  = 'AZUL' if ('blue' in mayor_d_raw or 'azul' in mayor_d_raw) else (
                          'ROJO' if ('red'  in mayor_d_raw or 'rojo' in mayor_d_raw) else '')
            # Sin fallback a `acierto`: si no se puede determinar mayor/winner, la
            # op queda con gano_mayoria=None y curva_pnl_ep la descartará.
            if winner_norm and mayor_norm:
                gano_mayoria = (winner_norm == mayor_norm)
            else:
                gano_mayoria = None
            ops.append({
                'skip':         skip,
                'acierto':      bool(d.get('acierto', False)),
                'gano_mayoria': gano_mayoria,
                'modo':         modo,
                'rango':        d.get('rango', '?'),
                'est':          d.get('est', 'ESTABLE'),
                'acel':         float(d.get('acel') or 0),
                'wr':           float(d.get('wr') or 50),
                'mult':         float(d.get('mult') or 1),
            })
        return ops

    def _delta_teorico(self, d, i) -> float:
        """Delta del filtro i para la ronda d.
        REGLA ABSOLUTA: si decision != APOSTADA → 0 para TODOS los filtros.
        El resto se evalúa con lambda + direccion modo-aware + contrarian."""
        if d.get('decision') != 'APOSTADA':
            return 0.0
        winner = (d.get('winner') or '').lower()
        mayor_raw = (d.get('mayor') or '').lower()
        winner_n = 'azul' if ('blue' in winner or 'azul' in winner) else (
                   'rojo' if ('red' in winner or 'rojo' in winner) else '')
        mayor_n  = 'azul' if ('blue' in mayor_raw or 'azul' in mayor_raw) else (
                   'rojo' if ('red' in mayor_raw or 'rojo' in mayor_raw) else '')
        if not winner_n or not mayor_n:
            return 0.0
        try:
            nombre, _, filtro_fn, contrarian, raw = FILTROS_CURVA[i]
        except Exception:
            return 0.0
        if filtro_fn is None or isinstance(filtro_fn, str):
            return 0.0
        # Construir 'op' compatible con las lambdas de FILTROS_CURVA
        modo = d.get('modo', 'SKIP')
        wr_d = float(d.get('wr') or 50)
        if modo == 'BASE':
            modo = 'DIRECTO' if wr_d >= 60 else ('INVERSO' if wr_d <= 40 else 'SKIP')
        gano_mayoria = (winner_n == mayor_n)
        op = {
            'skip':         modo == 'SKIP',
            'acierto':      bool(d.get('acierto', False)),
            'gano_mayoria': gano_mayoria,
            'modo':         modo,
            'rango':        d.get('rango', '?'),
            'est':          d.get('est', 'ESTABLE'),
            'acel':         float(d.get('acel') or 0),
            'wr':           wr_d,
            'mult':         float(d.get('mult') or 1),
        }
        # Si el filtro no es raw y su lambda no se cumple → no apuesta
        if not raw:
            try:
                if not filtro_fn(op):
                    return 0.0
            except Exception:
                return 0.0
            # SKIP por modo (filtro depende de modo y este es SKIP) → no apuesta
            if op['skip']:
                return 0.0
        apuesta = float(d.get('apuesta') or 1)
        mult    = float(d.get('mult') or 1)
        factor  = apuesta if (i == 0 or raw) else apuesta * mult
        # Dirección preferida del filtro — alineada con live (`_calcular_senal`):
        #   raw            → mayor (Base apuesta siempre a mayoría)
        #   nombre INVERSO → minor (filtros forzados a INVERSO)
        #   resto          → sigue op['modo'] (DIRECTO=mayor, INVERSO=minor)
        # Después contrarian invierte la apuesta final (CONTRA TOTAL/ESTABLE…).
        if raw:
            gano = op['gano_mayoria']
        elif 'INVERSO' in nombre.upper():
            gano = not op['gano_mayoria']
        elif op['modo'] == 'INVERSO':
            gano = not op['gano_mayoria']
        else:
            gano = op['gano_mayoria']
        if contrarian:
            gano = not gano
        return round(0.9 * factor if gano else -1.0 * factor, 2)

    def _inicializar_filtro_hist(self):
        """Bootstrap al arrancar: filtros 1-n simples se leen de long.jsonl
        (verdad histórica grabada ronda a ronda); Base (idx 0) se reconstruye
        desde `pnl_base` de las decisiones."""
        decs = self.panel_live.get_decisiones()
        if not decs:
            return
        n_filtros = len(FILTROS_CURVA)
        curva_base = []
        acum_b = 0.0; nac_b = 0; ntot_b = 0
        for d in decs:
            if d.get('winner') is None:
                continue
            pnl_b = d.get('pnl_base')
            if pnl_b is not None:
                acum_b = round(acum_b + pnl_b, 2)
                ntot_b += 1
                if pnl_b > 0:
                    nac_b += 1
            curva_base.append(acum_b)

        if curva_base:
            self._filtro_hist[0]      = (curva_base, nac_b, ntot_b, [])
            self._filtro_hist_base[0] = (curva_base, nac_b, ntot_b, [])

        for i in range(1, n_filtros):
            filtro_fn = FILTROS_CURVA[i][2]
            if filtro_fn is None or isinstance(filtro_fn, str):
                continue   # EP / BAL_FILTRO: calculados aparte
            curva_l, nac_l, ntot_l, _ = self._curva_desde_long(i)
            if curva_l:
                self._filtro_hist[i]      = (curva_l, nac_l, ntot_l, [])
                self._filtro_hist_base[i] = (curva_l, nac_l, ntot_l, [])

        self._guardar_filtro_hist()

    def _curva_desde_long(self, filtro_idx):
        """Construye (curva, n_ac, n_total) iterando pnl_filtros_long.jsonl
        usando el delta almacenado (calculado por _calcular_pnl_filtros en su
        momento). Así el saldo es coherente con el long file."""
        if filtro_idx <= 0 or filtro_idx >= len(FILTROS_CURVA):
            return [], 0, 0, []
        nombre, _color, filtro_fn, contrarian, raw = FILTROS_CURVA[filtro_idx]
        if filtro_fn is None or isinstance(filtro_fn, str):
            return [], 0, 0, []
        curva = []
        acum = 0.0
        n_ac = n_total = 0
        if not FILTROS_LONG_FILE.exists():
            return [], 0, 0, []

        try:
            with FILTROS_LONG_FILE.open('r', encoding='utf-8') as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    try:
                        r = json.loads(linea)
                    except Exception:
                        continue
                    if r.get('filtro_idx') != filtro_idx:
                        continue
                    if not r.get('winner'):
                        curva.append(round(acum, 2))
                        continue

                    # Usar el delta almacenado en el long file (calculado por
                    # _calcular_pnl_filtros o backfill), no recalcular.
                    delta = float(r.get('delta') or 0.0)

                    acum = round(acum + delta, 2)
                    curva.append(acum)
                    if delta != 0.0:
                        n_total += 1
                        if delta > 0:
                            n_ac += 1
        except Exception:
            pass
        return curva, n_ac, n_total, []

    def _reconstruir_filtro_hist(self, dec_ops):
        """Recalcula curva real de cada filtro sobre TODO el histórico de decisiones."""
        decs  = self.panel_live.get_decisiones()
        base0 = self.panel_live._balance_real_inicio

        n_filtros = len(FILTROS_CURVA)
        # Construir la curva desde cero sobre todas las decisiones grabadas
        acum   = [0.0] * n_filtros
        curves = [[]   for _ in range(n_filtros)]
        n_ac   = [0]   * n_filtros
        n_tot  = [0]   * n_filtros

        for d in decs:
            if d.get('winner') is None:
                continue

            # ── BASE (idx 0): punto en CADA ronda (plano si no hubo apuesta) ──
            pnl_b = d.get('pnl_base')
            if pnl_b is not None:
                acum[0] = round(acum[0] + pnl_b, 2)
                n_tot[0] += 1
                if pnl_b > 0:
                    n_ac[0] += 1
            curves[0].append(acum[0])

        # ── Base idx 0: guardar ──
        self._filtro_hist[0] = (curves[0], n_ac[0], n_tot[0], [])

        # ── Filtros 1-n simples: leer saldo histórico de long.jsonl ──
        # Es la verdad grabada ronda a ronda (lo que el usuario ve en la
        # columna "Saldo" de la ventana de historial). Evitamos la
        # divergencia que daba la regeneración teórica vía _delta_teorico.
        for i in range(1, n_filtros):
            filtro_fn = FILTROS_CURVA[i][2]
            if filtro_fn is None or isinstance(filtro_fn, str):
                continue   # EP / BAL_FILTRO: calculados aparte
            curva_l, nac_l, ntot_l, _ = self._curva_desde_long(i)
            if curva_l:
                self._filtro_hist[i] = (curva_l, nac_l, ntot_l, [])

        # ── Filtros EP: mantener la curva cacheada (base) y añadir deltas live encima ──
        # Para que la gráfica EP UMBRAL muestre el +83.60 cacheado y siga sumando con
        # las nuevas rondas live (en lugar de reiniciar desde 0 cada vez).
        # Pre-construir un ops_hist extendido con las decisiones previas a la sesión
        # actual, así las stats por (rango, modo) son continuas.
        decs_full   = self.panel_live.get_decisiones()
        sess_start  = self.panel_live._session_decision_start
        decs_pre    = decs_full[:sess_start]
        # Convertir las decisiones pre-sesión a ops compatibles con curva_pnl_umbral
        ops_pre = []
        for d in decs_pre:
            if d.get('winner') is None:
                continue
            modo = d.get('modo', 'SKIP')
            wr   = float(d.get('wr') or 50)
            if modo == 'BASE':
                modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
            ops_pre.append({
                'rango':   d.get('rango', '?'),
                'modo':    modo,
                'acierto': bool(d.get('acierto', False)),
            })
        ops_hist_extendido = (self._ops_historia or []) + ops_pre

        for i, entry in enumerate(FILTROS_CURVA):
            if (entry[2] is None or isinstance(entry[2], str)) and entry[2] != 'BAL_FILTRO':
                base_tuple = self._filtro_hist_base.get(i)
                base_curva = list(base_tuple[0]) if (base_tuple and base_tuple[0]) else []
                base_offset = base_curva[-1] if base_curva else 0.0
                base_nac    = base_tuple[1] if base_tuple else 0
                base_nbets  = base_tuple[2] if base_tuple else 0

                # Recalcular curva sólo de las nuevas ops live, con stats arrancando
                # desde el ops_hist extendido (live + pre-sesión).
                if entry[2] == 'EP_UMBRAL':
                    from pnl_data import curva_pnl_umbral as _cpu
                    new_curva, new_nac, new_nbets, _ = _cpu(
                        dec_ops, umbral=62.0, min_ops=5,
                        ops_hist=ops_hist_extendido or None,
                        mult_maximo=self._mult_maximo,
                        adaptativo=True,
                        ventana_regimen=30, warmup=10,
                        umbral_alto=0.55, umbral_bajo=0.50)
                else:
                    new_curva, new_nac, new_nbets, _ = self._calcular_curva(dec_ops, entry)

                if new_curva:
                    curva_offset = [base_offset + v for v in new_curva]
                    self._filtro_hist[i] = (
                        base_curva + curva_offset,
                        base_nac + new_nac,
                        base_nbets + new_nbets,
                        [])
                else:
                    # Sin ops live nuevas → conservar la base cacheada
                    self._filtro_hist[i] = (base_curva,
                                            base_nac, base_nbets, [])

        self._guardar_filtro_hist()

    def _cargar_filtro_hist_archivo(self):
        try:
            if FILTRO_HIST_FILE.exists():
                data = json.loads(FILTRO_HIST_FILE.read_text(encoding='utf-8'))
                result = {}
                for k, v in data.items():
                    result[int(k)] = (v.get('curva', []), v.get('n_ac', 0), v.get('n_total', 0), [])
                return result
        except Exception:
            pass
        return {}

    def _guardar_filtro_hist(self):
        try:
            data = {}
            for i, v in self._filtro_hist.items():
                data[str(i)] = {'curva': v[0], 'n_ac': v[1], 'n_total': v[2]}
            FILTRO_HIST_FILE.write_text(
                json.dumps(data, ensure_ascii=False), encoding='utf-8')
        except Exception:
            pass

    # ────────────────────────────────────────────────────────────

    def _exportar_filtros_sheets(self):
        """Actualiza cache compartida + escribe pestaña 'Filtros' en Google Sheets."""
        import pnl_filtros_cache
        # Capturar datos en hilo principal antes de pasar al thread
        filas_data = []
        info_json = self._cargar_filtros_info()
        for i, entry in enumerate(FILTROS_CURVA):
            nombre, color = entry[0], entry[1]
            hist = self._filtro_hist.get(i)
            if hist and hist[0]:
                curva, n_ac, n_tot, _ = hist
                pnl   = round(curva[-1], 2) if curva else 0.0
                wr    = round(n_ac / n_tot * 100, 1) if n_tot else 0.0
                ratio = round(pnl / n_tot, 3) if n_tot else 0.0
            else:
                pnl, wr, ratio, n_tot, n_ac = 0.0, 0.0, 0.0, 0, 0
            explicacion = info_json.get(i, '')
            filas_data.append({
                'idx': i, 'nombre': nombre, 'color': color,
                'ops': n_tot, 'ac': n_ac,
                'wr': f"{wr:.1f}%", 'pnl': f"{pnl:+.2f}",
                'ratio': ratio, 'explicacion': explicacion,
            })

        # ── Actualizar cache compartida en hilo principal (sin latencia de red) ──
        pnl_filtros_cache.actualizar(filas_data)

        # ── Convertir a filas para Sheets ──────────────────────────────────────
        filas_sheets = [
            [f['idx'], f['nombre'], f['color'], f['ops'], f['ac'],
             f['wr'], f['pnl'], f"{f['ratio']:+.3f}", f['explicacion']]
            for f in filas_data
        ]

        def _tarea(filas=filas_sheets):
            try:
                import gspread
                from oauth2client.service_account import ServiceAccountCredentials
                scope = ["https://spreadsheets.google.com/feeds",
                         "https://www.googleapis.com/auth/drive"]
                cred_path = str(Path(__file__).parent / 'credenciales.json')
                creds  = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
                cliente = gspread.authorize(creds)
                ss = cliente.open("Pk_Arena")
                try:
                    ws = ss.worksheet("Filtros")
                except Exception:
                    ws = ss.add_worksheet(title="Filtros", rows=60, cols=10)
                cabecera = [['#', 'Nombre', 'Color', 'Ops', 'Ac', 'WR%', 'PNL', 'PNL/op', 'Explicacion']]
                ws.clear()
                ws.update('A1', cabecera + filas)
                try:
                    ws.format('A1:I1', {
                        'textFormat': {
                            'bold': True,
                            'foregroundColor': {'red': 1.0, 'green': 1.0, 'blue': 1.0},
                        },
                        'backgroundColor': {'red': 0.02, 'green': 0.06, 'blue': 0.12},
                    })
                except Exception:
                    pass
            except Exception as e:
                print(f"[Sheets Filtros] Error: {e}")

        threading.Thread(target=_tarea, daemon=True).start()

    def _on_resultado(self, live_all_ops):
        """Callback de PanelLive: actualiza label archivo + filtro_info + dibujar."""
        # Leer Variables de Sheets cada ronda (hilo para no bloquear UI)
        threading.Thread(target=self._leer_variables_sheets, daemon=True).start()
        if self._fuente == 'live':
            n = len(live_all_ops)
            self._lbl_archivo.config(text=f"LIVE  ({n} rondas)", fg='#FFD700')
            self.panel_filtros.actualizar_infos(live_all_ops)
            dec_ops = self._ops_desde_decisiones()
            self._reconstruir_filtro_hist(dec_ops)
            # ── VUELTA_BASE: verificar racha de pérdidas ──────────────────────
            try:
                self._verificar_vuelta_base()
            except Exception as _e:
                print(f"[VUELTA_BASE] error: {_e}")
            # ── Auto-seleccionar el mejor filtro tras cada ronda ─────────────
            # Esto actualiza self.panel_filtros.selected_filter, que dirige
            # tanto el resaltado en la gráfica (_dibujar) como el filtro que
            # PanelLive incluye en el mensaje de Telegram en la siguiente
            # predicción. silencio_si_igual=True evita anunciar por voz si
            # el mejor filtro no cambia.
            try:
                self._seleccionar_mejor_filtro(silencio_si_igual=True)
            except Exception as _e:
                print(f"[AUTO-MEJOR] error: {_e}")
            # ── Refrescar histórico de apuestas ────────────────────────────
            try:
                decisiones = self.panel_live.get_decisiones()
                session_start = getattr(self.panel_live, '_session_decision_start', 0)
                saldo = self._historico_apuestas.refrescar(
                    decisiones[session_start:],
                    self._balance_historico_inicio)
                self.panel_live._saldo_global_historico = saldo
                self._round_counter += 1
                if self._round_counter % 5 == 0:
                    hablar(f"Saldo final {saldo:+.2f}")
            except Exception as _e:
                print(f"[HIST-APUESTAS] error: {_e}")
            # ── Actualizar ventana CURVAS si está abierta ──────────────────────
            try:
                if CurvasWindow._instancia and CurvasWindow._instancia.winfo_exists():
                    CurvasWindow._instancia.refrescar()
            except Exception:
                pass
            # ── Exportar stats de filtros a Sheets ────────────────────────────
            self._exportar_filtros_sheets()
        self._dibujar()
        self._emitir_sonido_suave()

    def _emitir_sonido_suave(self):
        if self._sonido_suave:
            try:
                winsound.Beep(600, 100)
            except Exception:
                pass

    def _verificar_vuelta_base(self):
        """VUELTA_BASE: si Base pierde N veces seguidas, permite cambiar;
        si Base gana, se queda. Si un filtro no-Base pierde N veces, vuelve a Base."""
        from pnl_config import FILTER_PARAMS, FILTROS_CURVA
        if getattr(self.panel_filtros, '_pinned_filter', False):
            return
        max_racha = FILTER_PARAMS.get('vuelta_base', 3)
        if max_racha <= 0:
            return
        cur_idx = self.panel_filtros.selected_filter
        decs = self.panel_live.get_decisiones()
        if not decs:
            return
        ultima = decs[-1]
        if ultima.get('decision') != 'APOSTADA':
            return
        if ultima.get('winner') is None:
            return
        if self._filtro_racha is not None and self._filtro_racha != cur_idx:
            self._racha_perdidas = 0
        self._filtro_racha = cur_idx
        gano = bool(ultima.get('acierto'))
        if cur_idx == 0:
            if gano:
                self._racha_perdidas = 0
                self._vuelta_base_bloqueo = True
            else:
                self._racha_perdidas += 1
                if self._racha_perdidas >= max_racha:
                    hablar(f"Base perdió {max_racha} veces seguidas, evaluando cambio")
                    self._racha_perdidas = 0
                    self._vuelta_base_bloqueo = False
        else:
            if gano:
                self._racha_perdidas = 0
            else:
                self._racha_perdidas += 1
                if self._racha_perdidas >= max_racha:
                    nombre_f = FILTROS_CURVA[cur_idx][0]
                    hablar(f"{nombre_f} perdió {max_racha} veces, volviendo a Base")
                    self.panel_filtros.seleccion_rapida([0], seleccionado=0)
                    if cur_idx not in self._cooldown_filtros:
                        self._cooldown_filtros[cur_idx] = time.time() + 1800
                        self._guardar_cooldown()
                    self._racha_perdidas = 0
                    self._filtro_racha = None
                    self._vuelta_base_bloqueo = False

    def _get_filtro_state(self):
        return (self.panel_filtros.selected_filter, self.panel_filtros.filtro_pnl_positivo)

    def _get_wr_rango(self, rango, modo):
        """Devuelve WR% para (rango, modo) usando ventana rolling configurable.
        self._ep_gate_ventana: int (n últimas ops del par rango/modo) o None ('TODOS').
        Combina ops_historia + ops_live. None si <5 ops disponibles."""
        ops_hist = self._ops_historia or []
        ops_live = self.panel_live.live_all_ops if hasattr(self, 'panel_live') else []
        matches = [op for op in (ops_hist + ops_live)
                   if op.get('rango') == rango and op.get('modo') == modo]
        ventana = getattr(self, '_ep_gate_ventana', None)
        if ventana is not None and ventana > 0:
            matches = matches[-ventana:]
        if len(matches) < 5:
            return None
        ganadas = sum(1 for op in matches if op.get('acierto', False))
        return ganadas / len(matches) * 100

    def _on_senal_cambio(self, nombre, color):
        """Callback llamado desde PanelLive en tick 36 cuando cambia la señal."""
        self._titulo_filtro = nombre
        self._titulo_color  = color or "---"
        self._actualizar_titulo()

    def _actualizar_titulo(self):
        color = self._titulo_color if self._titulo_color else "---"
        nombre = self._titulo_filtro if self._titulo_filtro else "---"
        pnl = self._titulo_pnl
        self.root.title(f"PNL DASHBOARD  │  {nombre}  │  {color}  │  {pnl:+.2f}€")

    def _cambiar_fuente(self, fuente):
        if fuente == self._fuente:
            return
        self._fuente = fuente
        self._btn_data_ai.config(bg=C['border'],  fg=C['muted'])
        self._btn_websocket.config(bg=C['border'], fg=C['muted'])
        self._btn_live_src.config(bg=C['border'],  fg=C['muted'])
        if fuente == 'data_ai':
            self._btn_data_ai.config(bg='#1A3050', fg=C['accent2'])
        elif fuente == 'websocket':
            self._btn_websocket.config(bg='#1A3050', fg=C['accent2'])
        else:
            self._btn_live_src.config(bg='#1A2A10', fg='#FFD700')
        self._cargar_datos()

    def _cargar_datos(self):
        if self._fuente == 'data_ai':
            ruta = INPUT_TXT
            if not ruta.exists():
                self._lbl_archivo.config(text="No se encontro reconstructor_data_AI.txt", fg=C['accent3'])
                self._all_ops = []
                self._dibujar()
                return
            self._all_ops = parsear(str(ruta))
        elif self._fuente == 'websocket':
            ruta = INPUT_WS
            if not ruta.exists():
                self._lbl_archivo.config(text="No se encontro websocket_log.txt", fg=C['accent3'])
                self._all_ops = []
                self._dibujar()
                return
            self._all_ops = parsear_websocket(str(ruta))
        else:  # live
            self._all_ops = []
            n = len(self.panel_live.live_all_ops)
            self._lbl_archivo.config(text=f"LIVE  ({n} rondas)", fg='#FFD700')
            self.panel_filtros.actualizar_infos(self.panel_live.live_all_ops)
            self._dibujar()
            return

        self._lbl_archivo.config(text=f"{ruta.name}  ({len(self._all_ops)} rondas)", fg=C['accent2'])

        # Pre-calcular info de cada filtro
        self.panel_filtros.actualizar_infos(self._all_ops)

        self._dibujar()

    def _poll_live(self):
        pl = self.panel_live

        # ── Comprobar si el proceso de espera de ronda inicial ha terminado ──
        if pl._bloqueando_inicio and pl._proc_espera:
            if pl._proc_espera.poll() is not None:
                pl._bloqueando_inicio = False
                pl._proc_espera = None
                try:
                    pl._lbl_live_status.config(text='CONECTADO', fg=C['accent2'])
                except Exception:
                    pass

        # ── Procesar eventos del websocket ────────────────────────────────────
        try:
            while True:
                ev = self._live_q.get_nowait()
                if pl._bloqueando_inicio:
                    # Descartamos todos los eventos hasta tener ronda limpia;
                    # solo dejamos pasar el estado de la conexión en el log
                    if ev.get('ev') == 'status' and ev.get('msg') != 'CONECTADO':
                        try:
                            pl._lbl_live_status.config(
                                text=f"⏳ {ev['msg']}", fg=C['warn'])
                        except Exception:
                            pass
                    continue
                pl.handle_ev(ev)
        except queue.Empty:
            pass
        self._poll_job = self.root.after(100, self._poll_live)

    def _dibujar(self):
        # SOLO BASE: si está activo, verificar si el usuario cambió filtros manualmente
        if self._solo_base_mode:
            vars_on = [i for i, v in enumerate(self.panel_filtros.filtro_vars) if v.get()]
            if vars_on != [0]:
                self._solo_base_mode = False
        self._btn_solo_base.config(
            bg='#FFD700' if self._solo_base_mode else '#1A1A3A',
            fg='#000000' if self._solo_base_mode else '#FFD700',
            relief='raised' if self._solo_base_mode else 'flat',
            bd=2 if self._solo_base_mode else 0)

        # Notas: actualizar siempre, incluso sin datos (el filtro puede cambiar antes de tener rondas)
        self._actualizar_notas(self.panel_filtros.selected_filter)

        if not self._all_ops and not self.panel_live.live_all_ops:
            return

        # Ajustar figura al tamaño real del canvas
        cw = self._canvas.get_tk_widget()
        pw = cw.winfo_width()
        ph = cw.winfo_height()
        if pw > 10 and ph > 10:
            self._fig.set_size_inches(pw / self._fig.dpi, ph / self._fig.dpi, forward=False)

        # Actualizar info labels y datos de grafica (misma fuente)
        ops_main = self.panel_live.live_all_ops if self._fuente == 'live' else self._all_ops

        # ── Filtro de mult + normalización ÷mult ───────────────────────
        _mult_sel  = self._filtro_mult.get() if hasattr(self, '_filtro_mult') else 'TODOS'
        _normaliza = self._normalizar_mult.get() if hasattr(self, '_normalizar_mult') else False
        _ops_dirty = (_mult_sel != 'TODOS') or _normaliza
        if _ops_dirty:
            _ops_v = []
            try:
                _mult_target = float(_mult_sel) if _mult_sel != 'TODOS' else None
            except Exception:
                _mult_target = None
            for _op in ops_main:
                _m = float(_op.get('mult') or 1)
                if _mult_target is not None and abs(_m - _mult_target) > 1e-9:
                    continue
                if _normaliza:
                    _op = dict(_op)
                    _op['mult'] = 1.0
                _ops_v.append(_op)
            ops_main = _ops_v

        self.panel_filtros.actualizar_infos(ops_main)

        # ── Gráfica: dos subplots (Base arriba / Filtros abajo) ─────
        self._ax.clear()
        self._ax2.clear()
        self._ax.set_facecolor(C['panel'])
        self._ax2.set_facecolor(C['panel'])

        # ── Pre-calcular BAL.FILTRO desde decisiones de sesión ───────────────
        _BAL_IDX = next((i for i, e in enumerate(FILTROS_CURVA) if e[2] == 'BAL_FILTRO'), None)
        if _BAL_IDX is not None:
            _decs  = self.panel_live.get_decisiones()
            _start = self.panel_live._session_decision_start
            _bf_acc  = 0.0
            _bf_nac  = 0
            _bf_ntot = 0
            _bf_curva = []
            for _d in _decs[_start:]:
                # SKIP = filtro no actuó → el balance no se mueve
                if _d.get('decision') != 'SKIP':
                    _delta = _d.get('pnl') or 0.0
                    _bf_acc = round(_bf_acc + _delta, 2)
                    if _delta != 0.0:
                        _bf_ntot += 1
                        if _delta > 0:
                            _bf_nac += 1
                _bf_curva.append(_bf_acc)
            self._filtro_hist[_BAL_IDX] = (_bf_curva, _bf_nac, _bf_ntot, [])

        _curva_base = None   # se captura al pintar i==0, para la tendencia polinómica

        for i, entry in enumerate(FILTROS_CURVA):
            nombre, color = entry[0], entry[1]
            contrario = entry[3]
            if not self.panel_filtros.filtro_vars[i].get() and i != self.panel_filtros.selected_filter:
                continue
            # BAL_FILTRO: siempre desde _filtro_hist (ya pre-calculado arriba)
            if entry[2] == 'BAL_FILTRO':
                curva, n_ac, n_total, cambios = self._filtro_hist.get(i, ([], 0, 0, []))
            # En modo live usar historial real (desde decisiones); fuera de live: teórico
            elif self._fuente == 'live' and i in self._filtro_hist and self._filtro_hist[i][0] and not _ops_dirty:
                curva, n_ac, n_total, cambios = self._filtro_hist[i]
            else:
                curva, n_ac, n_total, cambios = self._calcular_curva(ops_main, entry)
            if not curva:
                continue

            # ── Aplicar ventana de visualización ──────────────────────────
            _vent = self._ventana_filtros.get() if self._ventana_filtros else 'TODOS'
            if _vent != 'TODOS':
                _n = int(_vent)
                if len(curva) > _n:
                    _off   = len(curva) - _n
                    curva  = curva[_off:]
                    cambios = [(x - _off, d) for x, d in cambios if x >= _off]
                # Normalizar curva a 0 y recalcular stats sobre la ventana
                if len(curva) > 1:
                    _base   = curva[0]
                    curva   = [round(v - _base, 2) for v in curva]
                    _deltas = [round(curva[j] - curva[j-1], 2) for j in range(1, len(curva))]
                    n_total = sum(1 for d in _deltas if d != 0)
                    n_ac    = sum(1 for d in _deltas if d > 0)
            pnl = curva[-1]
            wr = (n_ac / n_total * 100) if n_total else 0
            if i == 0:
                _curva_base = list(curva)   # guardar para tendencia polinómica
            lw = 2.5 if i == self.panel_filtros.selected_filter else 1.5
            # Línea sólida para los primeros filtros y para los EP (incluido EP UMBRAL),
            # para que los segmentos planos (rondas SKIP) sean visibles dentro del trazado.
            _filtro_fn = entry[2]
            _es_ep = (_filtro_fn is None) or isinstance(_filtro_fn, str)
            ls = '-' if (i <= 5 or _es_ep) else '--'
            # Base (i==0) → eje superior; resto → eje inferior
            ax_t = self._ax if i == 0 else self._ax2

            # ── Dibujar con colores dinámicos para EP (DIRECTO vs INVERSO) ──
            if cambios:
                color_directo = '#00BFFF' if contrario else color
                color_inverso = '#FF6644' if contrario else color
                puntos = [0] + [x for x, _ in cambios] + [len(curva)]
                for seg_idx in range(len(puntos) - 1):
                    x_start = puntos[seg_idx]
                    x_end   = puntos[seg_idx + 1]
                    seg_dir = cambios[0][1] if seg_idx == 0 else cambios[seg_idx - 1][1]
                    seg_color = color_directo if seg_dir == 'DIRECTO' else color_inverso
                    if x_start < x_end and x_end <= len(curva):
                        ax_t.plot(range(x_start, x_end), curva[x_start:x_end],
                                  color=seg_color, linewidth=lw, linestyle=ls,
                                  alpha=1.0 if i == self.panel_filtros.selected_filter else 0.7)
                for x_cambio, dir_cambio in cambios:
                    if x_cambio < len(curva):
                        col_m = '#00BFFF' if dir_cambio == 'DIRECTO' else '#FF6644'
                        mk = '^' if dir_cambio == 'DIRECTO' else 'v'
                        ax_t.plot(x_cambio, curva[x_cambio], marker=mk, color=col_m,
                                  markersize=10, zorder=6, alpha=0.95)
                        ax_t.axvline(x_cambio, color=col_m, linewidth=0.7, linestyle=':', alpha=0.4)
                ax_t.plot([], [], color=color, linewidth=lw, linestyle=ls,
                          label=f'{nombre}: {pnl:+.1f} ({n_total}ops)')
            else:
                ax_t.plot(curva, color=color, linewidth=lw, linestyle=ls,
                          label=f'{nombre}: {pnl:+.1f} ({n_total}ops)',
                          alpha=1.0 if i == self.panel_filtros.selected_filter else 0.7)

        # ── Tendencia polinómica (grado 3) sobre la curva BASE ───────────────
        if _curva_base and len(_curva_base) >= 6:
            try:
                _x = np.arange(len(_curva_base))
                _coef = np.polyfit(_x, _curva_base, 3)
                _tend = np.polyval(_coef, _x)
                self._ax.plot(_x, _tend, color='#FF3333', linewidth=1.8,
                              linestyle='--', alpha=0.85, zorder=4,
                              label=f'Tendencia ({_tend[-1]:+.1f})')
            except Exception:
                pass

        # Curva LIVE superpuesta en gráfica Base (solo si fuente no es live)
        if self.panel_live.live_all_ops and self._fuente != 'live':
            curva_live, n_ac_l, n_l = curva_pnl(self.panel_live.live_all_ops, lambda op: not op['skip'])
            if curva_live:
                pnl_l = curva_live[-1]
                self._ax.plot(curva_live, color='#FFD700', linewidth=2.5, linestyle='-',
                              label=f'⬤ LIVE: {pnl_l:+.1f} ({n_l}ops)', alpha=1.0, zorder=5)

        # ── Formateo eje Base (arriba) ───────────────────────────
        self._ax.axhline(0, color='white', linewidth=0.5, alpha=0.3)
        if self.panel_filtros.filtro_vars[0].get() or (self.panel_live.live_all_ops and self._fuente != 'live'):
            self._ax.legend(fontsize=9, loc='upper left', facecolor=C['panel'],
                            edgecolor=C['border'], labelcolor=C['text'])
        self._ax.set_title('BASE', color=C['muted'], fontsize=11)
        self._ax.set_ylabel('PNL (EUR)', color=C['text'], fontsize=10)
        self._ax.tick_params(which='major', colors=C['muted'], labelsize=8)
        self._ax.tick_params(which='minor', colors=C['muted'], length=0)
        self._ax.xaxis.set_minor_locator(MultipleLocator(5))
        self._ax.minorticks_on()
        self._ax.grid(which='major', color=C['border'], linewidth=0.6)
        self._ax.grid(which='minor', axis='x', color='#1E3A5A', linewidth=0.6, linestyle='--', alpha=0.8)
        for spine in self._ax.spines.values():
            spine.set_color(C['border'])

        # ── Formateo eje Filtros (abajo) ─────────────────────────
        self._ax2.axhline(0, color='white', linewidth=0.5, alpha=0.3)
        activas2 = sum(1 for j, v in enumerate(self.panel_filtros.filtro_vars) if j > 0 and v.get())
        if activas2 > 0:
            self._ax2.legend(fontsize=9, loc='upper left', facecolor=C['panel'],
                             edgecolor=C['border'], labelcolor=C['text'])
        try:
            _sel_idx = self.panel_filtros.selected_filter
            _sel_nombre = FILTROS_CURVA[_sel_idx][0] if 0 <= _sel_idx < len(FILTROS_CURVA) else ''
            _titulo_f2 = f"FILTROS  │  #{_sel_idx} {_sel_nombre}" if _sel_nombre else 'FILTROS'
        except Exception:
            _titulo_f2 = 'FILTROS'
        self._ax2.set_title(_titulo_f2, color=C['accent'], fontsize=11)
        self._ax2.set_xlabel('Apuesta', color=C['text'], fontsize=10)
        self._ax2.set_ylabel('PNL (EUR)', color=C['text'], fontsize=10)
        self._ax2.tick_params(which='major', colors=C['muted'], labelsize=8)
        self._ax2.tick_params(which='minor', colors=C['muted'], length=0)
        self._ax2.xaxis.set_minor_locator(MultipleLocator(5))
        self._ax2.minorticks_on()
        self._ax2.grid(which='major', color=C['border'], linewidth=0.6)
        self._ax2.grid(which='minor', axis='x', color='#1E3A5A', linewidth=0.6, linestyle='--', alpha=0.8)
        for spine in self._ax2.spines.values():
            spine.set_color(C['border'])

        self._fig.subplots_adjust(left=0.10, right=0.99, top=0.97, bottom=0.06, hspace=0.30)
        self._canvas.draw()

        # ── Resumen + Rangos del filtro seleccionado ────────────
        idx = self.panel_filtros.selected_filter
        entry_sel = FILTROS_CURVA[idx]
        nombre, color, filtro, contrario, raw_sel = entry_sel
        ops_combined = (self.panel_live.live_all_ops if self._fuente == 'live'
                        else self._all_ops + self.panel_live.live_all_ops)
        if (filtro == 'BAL_FILTRO' or (self._fuente == 'live')) and idx in self._filtro_hist and self._filtro_hist[idx][0]:
            curva, n_ac, n_total, _ = self._filtro_hist[idx]
        else:
            curva, n_ac, n_total, _ = self._calcular_curva(ops_combined, entry_sel)
        pnl = curva[-1] if curva else 0
        # Actualizar título de ventana con nuevo filtro y PNL
        self._titulo_filtro = nombre
        self._titulo_pnl    = pnl
        self._actualizar_titulo()
        self._actualizar_panel_estado()
        wr = (n_ac / n_total * 100) if n_total else 0
        n_fa = n_total - n_ac
        maximo = max(curva) if curva else 0
        minimo = min(curva) if curva else 0
        drawdown = maximo - pnl
        ratio = pnl / n_total if n_total else 0

        # Indicador de datos live incluidos
        n_live = len(self.panel_live.live_all_ops)
        live_txt = f"+{n_live} rondas LIVE incluidas" if n_live else ""
        # ── Mejor filtro = filtro activo seleccionado (consistente con gráfica y TG) ──
        try:
            mejor_idx = self.panel_filtros.selected_filter
            entry_m = FILTROS_CURVA[mejor_idx] if 0 <= mejor_idx < len(FILTROS_CURVA) else None
        except Exception:
            mejor_idx, entry_m = None, None
        if entry_m is not None:
            hist = self._filtro_hist.get(mejor_idx)
            mejor_val = hist[0][-1] if (hist and hist[0]) else None
            nombre_m = entry_m[0]
            if mejor_val is None:
                self._lbl_mejor_filtro.config(
                    text=f"MEJOR: #{mejor_idx} {nombre_m}",
                    fg='#00FF88')
            else:
                self._lbl_mejor_filtro.config(
                    text=f"MEJOR: #{mejor_idx} {nombre_m}  {mejor_val:+.1f}",
                    fg='#00FF88' if mejor_val >= 0 else '#FF4466')
        else:
            self._lbl_mejor_filtro.config(text="MEJOR: —", fg='#00FF88')

        col_bal = C['accent2'] if pnl >= 0 else C['accent3']
        self._lbl_balance.config(text=f"{pnl:+.2f} EUR", fg=col_bal)
        self._lbl_live_badge.config(text=live_txt)
        self._stats['ops'].config(text=f"Operaciones:  {n_total}")
        self._stats['aciertos'].config(text=f"Aciertos:     {n_ac}")
        self._stats['fallos'].config(text=f"Fallos:       {n_fa}")
        self._stats['winrate'].config(text=f"Win Rate:     {wr:.1f}%",
                                      fg=C['accent2'] if wr > 52.6 else C['accent3'])
        self._stats['max'].config(text=f"Maximo:       {maximo:+.1f}")
        self._stats['min'].config(text=f"Minimo:       {minimo:+.1f}")
        self._stats['drawdown'].config(text=f"Drawdown:     {drawdown:.1f}")
        self._stats['ratio'].config(text=f"PNL/op:       {ratio:+.3f}")

        # Rangos
        filtro_rango = (lambda op: True) if not callable(filtro) else filtro
        rango_stats = defaultdict(lambda: {'ops': 0, 'gan': 0})
        for op in ops_combined:
            if op.get('skip'):
                continue   # apuesta no realizada → no cuenta para PNL por rango
            if not filtro_rango(op):
                continue
            rango_stats[op['rango']]['ops'] += 1
            if raw_sel:
                gano_r = op['acierto']   # apuesta mayoría siempre, sin ajuste
            else:
                gano_r = op['acierto'] if op.get('modo') != 'INVERSO' else not op['acierto']
            if contrario:
                gano_r = not gano_r
            if gano_r:
                rango_stats[op['rango']]['gan'] += 1

        for rango, labels in self._rango_labels.items():
            s = rango_stats.get(rango, {'ops': 0, 'gan': 0})
            n = s['ops']
            g = s['gan']
            wr_r = (g / n * 100) if n else 0
            pnl_r = g * 0.9 - (n - g) * 1.0
            labels[0].config(text=rango)
            labels[1].config(text=str(n) if n else "-")
            labels[2].config(text=f"{wr_r:.0f}%" if n else "-")
            labels[3].config(text=f"{pnl_r:+.1f}" if n else "-",
                             fg=C['accent2'] if pnl_r >= 0 else C['accent3'])

        # Info filtro seleccionado — siempre desde _filtro_hist[idx] (correcto para el filtro activo)
        self.panel_filtros.actualizar_sel(nombre, color, pnl, n_total, wr, ratio)

        # Tabla de PNL por filtro
        self._actualizar_filtros_tabla(ops_combined)

        # Tabla de PNL por confianza
        self._actualizar_conf_tabla()

        # Actualizar nombre de filtro en ventana histórico si está abierta
        self.panel_live._refrescar_decision_window()

    def _actualizar_filtros_tabla(self, ops):
        """Recalcula la tabla PNL POR FILTRO para todos los filtros sobre ops actuales."""
        sel_idx = self.panel_filtros.selected_filter
        for i, (entry, row_widgets) in enumerate(zip(FILTROS_CURVA, self._filtro_perf_rows)):
            row, lbl_n, lbl_ops, lbl_wr, lbl_pnl, lbl_ratio = row_widgets
            es_sel = (i == sel_idx)
            bg = '#0D2137' if es_sel else C['panel']
            row.config(bg=bg)
            lbl_n.config(bg=bg)
            lbl_ops.config(bg=bg)
            lbl_wr.config(bg=bg)
            lbl_pnl.config(bg=bg)
            lbl_ratio.config(bg=bg)
            try:
                # En modo LIVE: usar la curva reconstruida desde decisiones
                # (_filtro_hist) — misma fuente que el panel FILTRO SELECCIONADO,
                # para que ambos cuadren. BAL_FILTRO siempre desde _filtro_hist.
                if entry[2] == 'BAL_FILTRO':
                    curva, n_ac, n_total, _ = self._filtro_hist.get(i, ([], 0, 0, []))
                elif self._fuente == 'live' and i in self._filtro_hist and self._filtro_hist[i][0]:
                    curva, n_ac, n_total, _ = self._filtro_hist[i]
                else:
                    curva, n_ac, n_total, _ = self._calcular_curva(ops, entry)
                pnl_f = curva[-1] if curva else 0.0
                wr_f  = (n_ac / n_total * 100) if n_total else 0.0
                ratio_f = pnl_f / n_total if n_total else 0.0
                col_pnl = C['accent2'] if pnl_f >= 0 else C['accent3']
                col_n   = C['white'] if es_sel else C['text']
                lbl_n.config(fg=col_n)
                lbl_ops.config(text=str(n_total), fg=col_n)
                lbl_wr.config(text=f"{wr_f:.0f}%", fg=col_n)
                lbl_pnl.config(text=f"{pnl_f:+.2f}", fg=col_pnl)
                lbl_ratio.config(text=f"{ratio_f:+.3f}", fg=col_pnl)
            except Exception:
                for lbl in (lbl_ops, lbl_wr, lbl_pnl, lbl_ratio):
                    lbl.config(text="-", fg=C['muted'])
                lbl_n.config(fg=C['muted'])

    def _actualizar_conf_tabla(self):
        """Recalcula PNL POR CONFIANZA usando el histórico de decisiones live."""
        from collections import defaultdict
        decisiones = self.panel_live.get_decisiones()

        # Acumular aciertos anteriores para el fallback de cálculo de conf
        ops_acum = []
        by_conf  = defaultdict(list)
        for d in decisiones:
            if d.get('decision') == 'APOSTADA' and d.get('acierto') is not None:
                conf = d.get('conf')
                if conf is None:            # decisión antigua sin campo conf → simular
                    conf = self._sim_conf(d, ops_acum)
                by_conf[int(conf)].append(d.get('pnl') or 0)
                ops_acum.append(d)

        for v, row_widgets in enumerate(self._conf_perf_rows, start=1):
            row, lbl_conf, lbl_ops, lbl_wr, lbl_pnl, lbl_ratio = row_widgets
            rows = by_conf.get(v, [])
            n    = len(rows)
            bg   = '#0D2137' if v >= 7 else C['panel']
            row.config(bg=bg)
            for lbl in row_widgets[1:]:
                lbl.config(bg=bg)
            lbl_conf.config(fg=C['white'])
            if n == 0:
                for lbl in (lbl_ops, lbl_wr, lbl_pnl, lbl_ratio):
                    lbl.config(text="-", fg=C['muted'])
                continue
            # Contar aciertos: pnl > 0 → ganó
            ac   = sum(1 for p in rows if p > 0)
            wr   = ac / n * 100
            pnl  = sum(rows)
            rat  = pnl / n
            col_pnl = C['accent2'] if pnl >= 0 else C['accent3']
            col_wr  = C['accent2'] if wr > 52.6 else C['accent3']
            lbl_ops.config(text=str(n),          fg=C['text'])
            lbl_wr.config(text=f"{wr:.0f}%",     fg=col_wr)
            lbl_pnl.config(text=f"{pnl:+.1f}",   fg=col_pnl)
            lbl_ratio.config(text=f"{rat:+.3f}",  fg=col_pnl)

    @staticmethod
    def _sim_conf(d, ops_prev):
        """Calcula confianza 1-8 para decisiones antiguas sin campo conf."""
        wr   = d.get('wr', 50.0)
        est  = d.get('est', 'VOLATIL')
        acel = abs(d.get('acel', 0.0))
        pts  = 0
        dist = abs(wr - 50)
        if dist >= 20:   pts += 2
        elif dist >= 10: pts += 1
        if est == 'ESTABLE':   pts += 2
        if acel < 10:    pts += 2
        elif acel < 20:  pts += 1
        n_ep = min(len(ops_prev), 20)
        if n_ep >= 10:
            wr_ep   = sum(1 for o in ops_prev[-n_ep:] if o.get('acierto')) / n_ep * 100
            dist_ep = abs(wr_ep - 50)
            if dist_ep >= 20:   pts += 2
            elif dist_ep >= 10: pts += 1
        return max(1, min(8, pts))

    def _abrir_ventana_confianza(self):
        """Abre ventana con simulación de confianza 1-8 sobre el histórico de decisiones."""
        from pnl_decision_panel import cargar_decisiones
        from collections import defaultdict

        win = tk.Toplevel(self.root)
        win.title("ANÁLISIS DE CONFIANZA")
        win.configure(bg=C['bg'])
        win.resizable(False, False)

        # ── Título ──────────────────────────────────────────────────
        tk.Label(win, text="ANÁLISIS DE CONFIANZA PRE-APUESTA",
                 font=('Consolas', 14, 'bold'), bg=C['bg'], fg=C['accent']).pack(
                 pady=(16, 4), padx=20)
        tk.Label(win, text="Simulación sobre histórico · criterios: WR%  Estabilidad  Aceleración  EP rolling",
                 font=('Consolas', 9), bg=C['bg'], fg=C['muted']).pack(pady=(0, 10))

        # ── Calcular ────────────────────────────────────────────────
        def _conf_score(d, ops_prev):
            wr   = d.get('wr', 50.0)
            est  = d.get('est', 'VOLATIL')
            acel = abs(d.get('acel', 0.0))
            pts  = 0
            dist = abs(wr - 50)
            if dist >= 20:   pts += 2
            elif dist >= 10: pts += 1
            if est == 'ESTABLE':
                pts += 2
            if acel < 10:    pts += 2
            elif acel < 20:  pts += 1
            n_ep = min(len(ops_prev), 20)
            if n_ep >= 10:
                wr_ep    = sum(1 for o in ops_prev[-n_ep:] if o.get('acierto')) / n_ep * 100
                dist_ep  = abs(wr_ep - 50)
                if dist_ep >= 20:   pts += 2
                elif dist_ep >= 10: pts += 1
            return max(1, min(8, pts))

        hist      = cargar_decisiones()
        ops_acum  = []
        resultados = []
        for d in hist:
            if d.get('decision') == 'APOSTADA' and d.get('acierto') is not None:
                c = d.get('conf') or _conf_score(d, ops_acum)
                resultados.append({'conf': c, 'acierto': d['acierto'],
                                   'pnl': d.get('pnl') or 0})
                ops_acum.append(d)

        by_conf = defaultdict(list)
        for r in resultados:
            by_conf[r['conf']].append(r)

        # ── Tabla ───────────────────────────────────────────────────
        tf = tk.Frame(win, bg=C['panel'], bd=1, relief='solid')
        tf.pack(fill='x', padx=20, pady=(0, 8))

        FT  = ('Consolas', 10)
        FTB = ('Consolas', 10, 'bold')
        cols = [("CONF", 6), ("Ops", 5), ("Acier", 6), ("WR%", 7), ("PNL", 8), ("PNL/op", 8)]

        # Cabecera
        hdr = tk.Frame(tf, bg='#060F1E')
        hdr.pack(fill='x')
        for txt, w in cols:
            tk.Label(hdr, text=txt, font=FTB, bg='#060F1E',
                     fg=C['accent'], width=w, anchor='center').pack(side='left', padx=2, pady=4)

        tk.Frame(tf, bg=C['accent'], height=1).pack(fill='x')

        # Filas 1-8
        totals = {'n': 0, 'ac': 0, 'pnl': 0.0}
        for c in range(1, 9):
            rows = by_conf[c]
            n    = len(rows)
            row_f = tk.Frame(tf, bg=C['panel'])
            row_f.pack(fill='x')
            if n == 0:
                tk.Label(row_f, text=str(c), font=FTB, bg=C['panel'],
                         fg=C['muted'], width=6, anchor='center').pack(side='left', padx=2, pady=2)
                tk.Label(row_f, text="—", font=FT, bg=C['panel'],
                         fg=C['muted'], width=38, anchor='center').pack(side='left')
                continue
            ac   = sum(1 for r in rows if r['acierto'])
            wr   = ac / n * 100
            pnl  = sum(r['pnl'] for r in rows)
            rat  = pnl / n
            totals['n'] += n; totals['ac'] += ac; totals['pnl'] += pnl
            col_pnl = C['accent2'] if pnl >= 0 else C['accent3']
            col_rat = C['accent2'] if rat >= 0 else C['accent3']
            col_wr  = C['accent2'] if wr > 52.6 else C['accent3']
            # Color de fila por confianza alta
            bg_row = '#0D2137' if c >= 7 else C['panel']
            row_f.config(bg=bg_row)
            vals = [
                (str(c),          FTB, C['white'],  6),
                (str(n),          FT,  C['text'],   5),
                (f"{ac}/{n}",     FT,  C['text'],   6),
                (f"{wr:.1f}%",    FT,  col_wr,      7),
                (f"{pnl:+.1f}",   FT,  col_pnl,     8),
                (f"{rat:+.3f}",   FT,  col_rat,     8),
            ]
            for txt, font, fg, w in vals:
                tk.Label(row_f, text=txt, font=font, bg=bg_row,
                         fg=fg, width=w, anchor='center').pack(side='left', padx=2, pady=2)

        # Separador + total
        tk.Frame(tf, bg=C['border'], height=1).pack(fill='x')
        tot_f = tk.Frame(tf, bg='#080E1A')
        tot_f.pack(fill='x')
        tn = totals['n']; tac = totals['ac']; tpnl = totals['pnl']
        twr  = tac / tn * 100 if tn else 0
        trat = tpnl / tn if tn else 0
        col_tp  = C['accent2'] if tpnl >= 0 else C['accent3']
        col_twr = C['accent2'] if twr > 52.6 else C['accent3']
        for txt, font, fg, w in [
            ("TOT", FTB, C['accent'], 6), (str(tn), FT, C['text'], 5),
            (f"{tac}/{tn}", FT, C['text'], 6), (f"{twr:.1f}%", FT, col_twr, 7),
            (f"{tpnl:+.1f}", FT, col_tp, 8), (f"{trat:+.3f}", FT, col_tp, 8),
        ]:
            tk.Label(tot_f, text=txt, font=font, bg='#080E1A',
                     fg=fg, width=w, anchor='center').pack(side='left', padx=2, pady=4)

        # ── Resumen texto ────────────────────────────────────────────
        sf = tk.Frame(win, bg=C['bg'])
        sf.pack(fill='x', padx=20, pady=(0, 8))
        bajos = [r for r in resultados if r['conf'] <= 4]
        altos = [r for r in resultados if r['conf'] >= 5]
        lineas = []
        for label, grupo in [("CONF 1-4 (baja)", bajos), ("CONF 5-8 (alta)", altos)]:
            if not grupo:
                continue
            n = len(grupo); ac = sum(1 for r in grupo if r['acierto'])
            pnl = sum(r['pnl'] for r in grupo)
            lineas.append(f"{label}:  {n} ops  WR={ac/n*100:.1f}%  PNL={pnl:+.1f}  ratio={pnl/n:+.3f}")
        for l in lineas:
            tk.Label(sf, text=l, font=('Consolas', 10), bg=C['bg'],
                     fg=C['text'], anchor='w').pack(anchor='w')

        # ── Mejor filtro por eficiencia y por volumen ────────────────
        tk.Frame(win, bg=C['border'], height=1).pack(fill='x', padx=20, pady=(4, 6))
        ff2 = tk.Frame(win, bg=C['bg'])
        ff2.pack(fill='x', padx=20, pady=(0, 8))
        tk.Label(ff2, text="ANÁLISIS DE FILTROS", font=('Consolas', 11, 'bold'),
                 bg=C['bg'], fg=C['accent']).pack(anchor='w', pady=(0, 4))

        # Recopilar stats de cada filtro desde _filtro_hist
        filtro_stats = []
        for i, entry in enumerate(FILTROS_CURVA):
            hist = self._filtro_hist.get(i)
            if not hist or not hist[0] or hist[2] == 0:
                continue
            curva_f, n_ac_f, n_tot_f, _ = hist
            pnl_f   = curva_f[-1]
            ratio_f = pnl_f / n_tot_f if n_tot_f else 0
            filtro_stats.append({
                'idx':    i,
                'nombre': entry[0],
                'pnl':    pnl_f,
                'n_tot':  n_tot_f,
                'ratio':  ratio_f,
            })

        if filtro_stats:
            # Mejor ratio PNL/op (eficiente, pocas apuestas)
            mejor_ratio = max(filtro_stats, key=lambda x: x['ratio'])
            # Mejor PNL absoluto con muchas apuestas (n_tot >= mediana)
            n_tots = sorted(s['n_tot'] for s in filtro_stats)
            mediana = n_tots[len(n_tots) // 2]
            activos = [s for s in filtro_stats if s['n_tot'] >= mediana]
            mejor_volumen = max(activos, key=lambda x: x['pnl']) if activos else mejor_ratio

            for titulo, s, color in [
                ("↑ MAYOR RENDIMIENTO/OP (selectivo):", mejor_ratio,  '#00FF88'),
                ("↑ MAYOR BENEFICIO TOTAL (activo):",  mejor_volumen, '#FFD700'),
            ]:
                tf2 = tk.Frame(ff2, bg='#0D1F10' if color == '#00FF88' else '#1A1A00',
                               bd=1, relief='solid')
                tf2.pack(fill='x', pady=2)
                tk.Label(tf2, text=titulo, font=('Consolas', 9),
                         bg=tf2['bg'], fg=C['muted']).pack(anchor='w', padx=8, pady=(4, 0))
                txt = (f"  #{s['idx']}  {s['nombre']}   "
                       f"PNL {s['pnl']:+.1f}   {s['n_tot']} ops   "
                       f"ratio {s['ratio']:+.3f}/op")
                tk.Label(tf2, text=txt, font=('Consolas', 11, 'bold'),
                         bg=tf2['bg'], fg=color).pack(anchor='w', padx=8, pady=(0, 4))
        else:
            tk.Label(ff2, text="Sin datos de filtros disponibles", font=('Consolas', 10),
                     bg=C['bg'], fg=C['muted']).pack(anchor='w')

        # ── Botón OK ─────────────────────────────────────────────────
        tk.Button(win, text="OK", font=('Consolas', 12, 'bold'),
                  bg='#1A3A2A', fg=C['accent2'], relief='flat', cursor='hand2',
                  padx=30, pady=6, command=win.destroy).pack(pady=(4, 16))

        win.update_idletasks()
        # Centrar sobre la ventana principal
        pw = self.root.winfo_x() + self.root.winfo_width() // 2
        ph = self.root.winfo_y() + self.root.winfo_height() // 2
        ww = win.winfo_width(); wh = win.winfo_height()
        win.geometry(f"+{pw - ww//2}+{ph - wh//2}")

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    PnlDashboard().run()
