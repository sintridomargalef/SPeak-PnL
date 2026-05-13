"""
estudio_martingala_dashboard.py — Dashboard editable cyberpunk
con monitoreo continuo de la simulacion Martingale sobre filtros.

Uso:
    python estudio_martingala_dashboard.py
"""
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import tkinter as tk
from tkinter import ttk
import json
import threading
from pathlib import Path

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from pnl_config import C, FONT_MONO, FONT_MONO_B, FONT_BIG, FONT_SM, FONT_TITLE

FILTROS = [
    "Base (todo)", "Solo DIRECTO", "Solo INVERSO",
    "DIR WR>=70%", "DIR WR>=80%", "DIR sin +50",
    "DIR ESTABLE", "DIR VOLATIL", "DIR |acel|<10",
    "DIR WR>=70 sin+50",
    "CONTRA TOTAL", "MAYORIA PERDEDORA", "CONTRA ESTABLE",
    "EP ADAPTATIVO", "EP + WR>=70", "EP + WR>=70 INV",
    "EP UMBRAL", "BAL.FILTRO",
]

FILTRO_COLORS = [
    '#4A6080', '#00FF88', '#FF3366', '#00D4FF', '#FFB800',
    '#2B7FFF', '#8B5CF6', '#F97316', '#EC4899', '#06B6D4',
    '#FF6B35', '#C084FC', '#F59E0B', '#FFD700', '#00FFFF',
    '#FF00FF', '#FF8C00', '#E8F4FF',
]

LONGFILE = Path(__file__).parent / 'pnl_filtros_long.jsonl'
CFG_FILE = Path(__file__).parent / 'estudio_martingala_dashboard_cfg.json'
REFRESH_MS = 2000

DEFAULT_BASE_BET = 0.1
DEFAULT_CAP = 0
DEFAULT_PNL_ACIERTO = 0.9
DEFAULT_PNL_FALLO = -1.0

PNL_ACIERTO_RATIO = DEFAULT_PNL_ACIERTO
PNL_FALLO_RATIO = DEFAULT_PNL_FALLO

MULT_TABLE = [1, 3, 9, 27, 81, 243, 729, 2187]


def mult_martingala(dobles):
    if dobles < len(MULT_TABLE):
        return MULT_TABLE[dobles]
    return MULT_TABLE[-1] * (3 ** (dobles - len(MULT_TABLE) + 1))


def _extraer_delta(op):
    return float(op.get('delta', op.get('pnl', 0)))


def simular_martingala(ops, base_bet=0.1, max_dobles=None):
    curve = [0.0]
    current_bet = base_bet
    max_bet = base_bet
    drawdown = 0.0
    peak = 0.0
    longest_loss_streak = 0
    current_loss_streak = 0
    n_bets = 0
    n_wins = 0
    n_losses = 0
    n_skips = 0

    for op in ops:
        delta = _extraer_delta(op)
        if delta == 0:
            curve.append(curve[-1])
            n_skips += 1
            continue

        n_bets += 1
        if delta > 0:
            pnl_martingala = round(current_bet * PNL_ACIERTO_RATIO, 2)
            current_bet = base_bet
            n_wins += 1
            current_loss_streak = 0
        else:
            pnl_martingala = round(current_bet * PNL_FALLO_RATIO, 2)
            n_losses += 1
            current_loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
            if max_dobles is not None and current_loss_streak > max_dobles:
                current_bet = base_bet
                current_loss_streak = 0
            else:
                current_bet = round(base_bet * mult_martingala(current_loss_streak), 2)
                max_bet = max(max_bet, current_bet)

        new_balance = round(curve[-1] + pnl_martingala, 2)
        curve.append(new_balance)
        peak = max(peak, new_balance)
        drawdown = min(drawdown, new_balance - peak)

    final_balance = curve[-1] if curve else 0.0
    win_rate = (n_wins / n_bets * 100) if n_bets else 0.0

    return {
        'curve': curve,
        'n_bets': n_bets,
        'n_skips': n_skips,
        'n_total': n_bets + n_skips,
        'n_wins': n_wins,
        'n_losses': n_losses,
        'win_rate': win_rate,
        'final_balance': final_balance,
        'max_bet': max_bet,
        'max_drawdown': round(drawdown, 2),
        'longest_loss_streak': longest_loss_streak,
    }


def simular_real(ops):
    curve = [0.0]
    n_bets = 0
    n_wins = 0
    n_losses = 0
    drawdown = 0.0
    peak = 0.0

    for op in ops:
        delta = _extraer_delta(op)
        if delta == 0:
            curve.append(curve[-1])
            continue
        n_bets += 1
        if delta > 0:
            n_wins += 1
        else:
            n_losses += 1
        new_bal = round(curve[-1] + delta, 2)
        curve.append(new_bal)
        peak = max(peak, new_bal)
        drawdown = min(drawdown, new_bal - peak)

    win_rate = (n_wins / n_bets * 100) if n_bets else 0.0
    return {
        'curve': curve,
        'n_bets': n_bets,
        'n_wins': n_wins,
        'n_losses': n_losses,
        'win_rate': win_rate,
        'final_balance': curve[-1],
        'max_drawdown': round(drawdown, 2),
    }


class MartingalaDashboard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ESTUDIO MARTINGALA DASHBOARD")
        self.root.configure(bg=C['bg'])
        self.root.resizable(True, True)

        self._encendido = False
        self._poll_job = None
        self._datos = {}
        self._datos_ops = {}
        self._curvas_reales = {}
        self._curvas_mart = {}
        self._resultados = {}
        self._mejor_filtro_idx = None
        self._ventanas_stats = {}
        self._ultimo_mtime = 0

        self.base_bet = tk.DoubleVar(value=DEFAULT_BASE_BET)
        self.cap = tk.IntVar(value=DEFAULT_CAP)
        self.pnl_acierto = tk.DoubleVar(value=DEFAULT_PNL_ACIERTO)
        self.pnl_fallo = tk.DoubleVar(value=DEFAULT_PNL_FALLO)
        self.capital = tk.DoubleVar(value=100.0)
        self.filtro_grafico = tk.IntVar(value=0)
        self.window_size = tk.IntVar(value=500)
        self.num_ventanas = tk.IntVar(value=500)

        self._restaurar_geometria()
        self._construir_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.root.after(100, self._recalcular)
        self.root.update()

    # ── Geometry ────────────────────────────────────────────────────

    def _restaurar_geometria(self):
        try:
            cfg = json.loads(CFG_FILE.read_text(encoding='utf-8'))
            self.root.geometry(f"{cfg['w']}x{cfg['h']}+{cfg['x']}+{cfg['y']}")
            if 'base_bet' in cfg:
                self.base_bet.set(cfg['base_bet'])
            if 'cap' in cfg:
                self.cap.set(cfg['cap'])
            if 'pnl_acierto' in cfg:
                self.pnl_acierto.set(cfg['pnl_acierto'])
            if 'pnl_fallo' in cfg:
                self.pnl_fallo.set(cfg['pnl_fallo'])
            if 'capital' in cfg:
                self.capital.set(cfg['capital'])
            if 'window_size' in cfg:
                self.window_size.set(cfg['window_size'])
            if 'num_ventanas' in cfg:
                self.num_ventanas.set(cfg['num_ventanas'])
        except Exception:
            self.root.geometry("1400x900")

    def _guardar_geometria(self):
        geo = self.root.geometry()
        size_part, pos_part = geo.split('+', 1)
        w_str, h_str = size_part.split('x')
        x_str, y_str = pos_part.split('+', 1)
        cfg = {
            'w': int(w_str), 'h': int(h_str), 'x': int(x_str), 'y': int(y_str),
            'base_bet': self.base_bet.get(),
            'cap': self.cap.get(),
            'pnl_acierto': self.pnl_acierto.get(),
            'pnl_fallo': self.pnl_fallo.get(),
            'capital': self.capital.get(),
            'window_size': self.window_size.get(),
            'num_ventanas': self.num_ventanas.get(),
        }
        CFG_FILE.write_text(json.dumps(cfg, indent=2), encoding='utf-8')

    def _cerrar(self):
        self._detener()
        self._guardar_geometria()
        try:
            self.root.destroy()
        except Exception:
            pass

    # ── UI Construction ─────────────────────────────────────────────

    def _construir_ui(self):
        hf = tk.Frame(self.root, bg='#020810', height=48)
        hf.pack(fill='x')
        hf.pack_propagate(False)

        tk.Frame(hf, bg=C['accent'], height=3).pack(fill='x', side='top')

        inner = tk.Frame(hf, bg='#020810')
        inner.pack(fill='both', expand=True, padx=12)

        tk.Label(inner, text="\u25c8  ESTUDIO MARTINGALA DASHBOARD",
                 font=FONT_TITLE, bg='#020810', fg=C['accent']).pack(side='left', pady=8)

        self._lbl_status = tk.Label(inner, text="\u25cf  DETENIDO",
                                    font=FONT_MONO_B, bg='#020810', fg=C['muted'])
        self._lbl_status.pack(side='right', pady=8, padx=(0, 8))

        self._btn_encender = tk.Button(inner, text="\u23cf ENCENDER",
                                       font=FONT_MONO_B, bg='#0A1628', fg=C['accent2'],
                                       relief='flat', cursor='hand2', padx=12,
                                       activebackground='#0D2137', activeforeground=C['accent2'],
                                       command=self._toggle)
        self._btn_encender.pack(side='right', pady=6, padx=4)

        btn_parar = tk.Button(inner, text="\u23f9 PARAR",
                              font=FONT_MONO_B, bg='#0A1628', fg=C['accent3'],
                              relief='flat', cursor='hand2', padx=12,
                              activebackground='#0D2137', activeforeground=C['accent3'],
                              command=self._detener)
        btn_parar.pack(side='right', pady=6, padx=4)

        btn_recalcular = tk.Button(inner, text="\u21bb RECALCULAR",
                                   font=FONT_MONO_B, bg='#0A1628', fg=C['warn'],
                                   relief='flat', cursor='hand2', padx=12,
                                   activebackground='#0D2137', activeforeground=C['warn'],
                                   command=self._recalcular)
        btn_recalcular.pack(side='right', pady=6, padx=4)

        # ── Body ────────────────────────────────────────────────────
        body = tk.Frame(self.root, bg=C['bg'])
        body.pack(fill='both', expand=True, padx=8, pady=(0, 8))

        body.columnconfigure(0, weight=0, minsize=280)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=0, minsize=320)
        body.rowconfigure(0, weight=0)
        body.rowconfigure(1, weight=1)

        # ── Left: Parameters + Summary ──────────────────────────────
        izq = tk.Frame(body, bg=C['bg'])
        izq.grid(row=0, column=0, rowspan=2, sticky='nsew', padx=(0, 4))

        self._panel_parametros(izq)
        self._panel_resumen(izq)
        self._panel_ventanas(izq)

        # ── Center: Table + Rondas ──────────────────────────────────
        centro = tk.Frame(body, bg=C['bg'])
        centro.grid(row=0, column=1, rowspan=2, sticky='nsew', padx=4)
        centro.grid_rowconfigure(0, weight=1)
        centro.grid_rowconfigure(1, weight=0)
        centro.grid_rowconfigure(2, weight=2)
        centro.grid_columnconfigure(0, weight=1)

        self._panel_tabla(centro)
        self._panel_rondas(centro)

        # ── Right: Graph + Best filter ──────────────────────────────
        der = tk.Frame(body, bg=C['bg'])
        der.grid(row=0, column=2, rowspan=2, sticky='nsew', padx=(4, 0))

        self._panel_grafico(der)

    # ── Panel: Parameters ──────────────────────────────────────────

    def _panel_parametros(self, parent):
        pf = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        pf.pack(fill='x', pady=(0, 4))

        tk.Frame(pf, bg=C['accent'], height=2).pack(fill='x', side='top')
        tk.Label(pf, text="PARAMETROS", font=FONT_MONO_B,
                 bg=C['panel'], fg=C['accent']).pack(anchor='w', padx=10, pady=(6, 2))

        def _fila(parent, label, var, fmt):
            f = tk.Frame(parent, bg=C['panel'])
            f.pack(fill='x', padx=10, pady=2)
            tk.Label(f, text=label, font=FONT_SM, bg=C['panel'], fg=C['text'], width=14, anchor='w').pack(side='left')
            e = tk.Entry(f, font=FONT_MONO, bg='#050A14', fg=C['accent'], bd=0,
                         highlightthickness=1, highlightbackground=C['border'],
                         highlightcolor=C['accent'], insertbackground=C['accent'],
                         textvariable=var, width=10, justify='right')
            e.pack(side='right')

        _fila(pf, "Base Bet:", self.base_bet, '.2f')
        _fila(pf, "Cap (dobles):", self.cap, 'd')
        _fila(pf, "Payout:", self.pnl_acierto, '.2f')
        _fila(pf, "Fallo:", self.pnl_fallo, '.2f')
        _fila(pf, "Capital:", self.capital, '.2f')
        _fila(pf, "Window Size:", self.window_size, 'd')
        _fila(pf, "Max Ventanas:", self.num_ventanas, 'd')

        info = tk.Label(pf, text="Cap=0 = sin limite  |  step=1",
                        font=('Consolas', 8), bg=C['panel'], fg=C['muted'])
        info.pack(anchor='w', padx=10, pady=(0, 6))

    # ── Panel: Summary ──────────────────────────────────────────────

    def _panel_resumen(self, parent):
        rf = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        rf.pack(fill='x', pady=(4, 0))

        tk.Frame(rf, bg=C['accent'], height=2).pack(fill='x', side='top')
        tk.Label(rf, text="RESUMEN FILTRO", font=FONT_MONO_B,
                 bg=C['panel'], fg=C['accent']).pack(anchor='w', padx=10, pady=(6, 2))

        self._lbl_filtro_nombre = tk.Label(rf, text="",
                                           font=FONT_MONO_B, bg=C['panel'], fg=C['accent'])
        self._lbl_filtro_nombre.pack(anchor='w', padx=10, pady=(0, 2))

        self._lbl_real = tk.Label(rf, text="Real:  --",
                                  font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_real.pack(anchor='w', padx=10, pady=1)

        self._lbl_mart = tk.Label(rf, text="Mart:  --",
                                  font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_mart.pack(anchor='w', padx=10, pady=1)

        self._lbl_diff = tk.Label(rf, text="Diff:  --",
                                  font=FONT_MONO, bg=C['panel'], fg=C['warn'])
        self._lbl_diff.pack(anchor='w', padx=10, pady=1)

        self._lbl_wr = tk.Label(rf, text="WR:  --",
                                font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_wr.pack(anchor='w', padx=10, pady=1)

        self._lbl_racha = tk.Label(rf, text="Peor racha:  --",
                                   font=FONT_MONO, bg=C['panel'], fg=C['accent3'])
        self._lbl_racha.pack(anchor='w', padx=10, pady=1)

        self._lbl_bets = tk.Label(rf, text="Apuestas:  --",
                                  font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_bets.pack(anchor='w', padx=10, pady=1)
        self._lbl_capital = tk.Label(rf, text="",
                                     font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_capital.pack(anchor='w', padx=10, pady=(1, 6))

        # ── Window analysis ──────────────────────────────────────────
        tk.Frame(rf, bg=C['border'], height=1).pack(fill='x', padx=10, pady=2)
        self._lbl_win_header = tk.Label(rf, text="VENTANAS (resultado)",
                                        font=FONT_MONO_B, bg=C['panel'], fg=C['warn'])
        self._lbl_win_header.pack(anchor='w', padx=10, pady=(2, 1))

        self._lbl_win_mejor = tk.Label(rf, text="",
                                       font=FONT_MONO, bg=C['panel'], fg=C['accent2'])
        self._lbl_win_mejor.pack(anchor='w', padx=10, pady=1)

        self._lbl_win_peor = tk.Label(rf, text="",
                                      font=FONT_MONO, bg=C['panel'], fg=C['accent3'])
        self._lbl_win_peor.pack(anchor='w', padx=10, pady=1)

        self._lbl_win_promedio = tk.Label(rf, text="",
                                          font=FONT_MONO, bg=C['panel'], fg=C['text'])
        self._lbl_win_promedio.pack(anchor='w', padx=10, pady=1)

        self._lbl_win_pos = tk.Label(rf, text="",
                                     font=FONT_MONO, bg=C['panel'], fg=C['accent2'])
        self._lbl_win_pos.pack(anchor='w', padx=10, pady=1)

        self._lbl_win_neg = tk.Label(rf, text="",
                                     font=FONT_MONO, bg=C['panel'], fg=C['accent3'])
        self._lbl_win_neg.pack(anchor='w', padx=10, pady=1)

        self._lbl_win_std = tk.Label(rf, text="",
                                     font=FONT_MONO, bg=C['panel'], fg=C['muted'])
        self._lbl_win_std.pack(anchor='w', padx=10, pady=(1, 4))

        self._lbl_ultima_act = tk.Label(rf, text="",
                                        font=('Consolas', 8), bg=C['panel'], fg=C['muted'])
        self._lbl_ultima_act.pack(anchor='w', padx=10, pady=(0, 4))

    # ── Panel: Ventanas (sliding windows) ──────────────────────────

    def _panel_ventanas(self, parent):
        vf = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        vf.pack(fill='both', expand=True, pady=(4, 0))

        tk.Frame(vf, bg=C['accent'], height=2).pack(fill='x', side='top')
        hdr = tk.Frame(vf, bg=C['panel'])
        hdr.pack(fill='x', padx=6, pady=(4, 0))

        tk.Label(hdr, text="VENTANAS (step=1)", font=FONT_MONO_B,
                 bg=C['panel'], fg=C['accent']).pack(side='left')
        self._btn_calcular_ventanas = tk.Button(hdr, text="\u25b6 Calcular",
                                      font=FONT_SM, bg='#0A1628', fg=C['accent2'],
                                      relief='flat', cursor='hand2', padx=6,
                                      activebackground='#0D2137', activeforeground=C['accent2'],
                                      command=self._on_calcular_ventanas)
        self._btn_calcular_ventanas.pack(side='right', padx=(4, 0))
        self._lbl_ventanas_count = tk.Label(hdr, text="",
                                             font=FONT_SM, bg=C['panel'], fg=C['muted'])
        self._lbl_ventanas_count.pack(side='right')

        txt_frame = tk.Frame(vf, bg=C['panel'])
        txt_frame.pack(fill='both', expand=True, padx=4, pady=(2, 4))

        self._txt_ventanas = tk.Text(txt_frame, height=10, width=40,
                                      bg='#050A14', fg=C['text'],
                                      font=('Consolas', 9),
                                      bd=0, highlightthickness=1,
                                      highlightbackground=C['border'],
                                      highlightcolor=C['border'],
                                      insertbackground=C['accent'],
                                      wrap='none',
                                      state='normal')
        vsb_v = tk.Scrollbar(txt_frame, orient='vertical',
                             command=self._txt_ventanas.yview)
        self._txt_ventanas.configure(yscrollcommand=vsb_v.set)

        self._txt_ventanas.tag_configure('header', foreground=C['muted'])
        self._txt_ventanas.tag_configure('pos', foreground=C['accent2'])
        self._txt_ventanas.tag_configure('neg', foreground=C['accent3'])
        self._txt_ventanas.tag_configure('dim', foreground=C['muted'])

        self._txt_ventanas.pack(side='left', fill='both', expand=True)
        vsb_v.pack(side='right', fill='y')

    # ── Panel: Table ────────────────────────────────────────────────

    def _panel_tabla(self, parent):
        tf = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        tf.grid(row=0, column=0, sticky='nsew')

        tk.Frame(tf, bg=C['accent'], height=2).pack(fill='x', side='top')

        header_row = tk.Frame(tf, bg=C['panel'])
        header_row.pack(fill='x', padx=4, pady=(4, 0))

        tk.Label(header_row, text="FILTROS", font=FONT_MONO_B,
                 bg=C['panel'], fg=C['accent']).pack(side='left')

        self._lbl_count = tk.Label(header_row, text="",
                                   font=FONT_SM, bg=C['panel'], fg=C['muted'])
        self._lbl_count.pack(side='right')

        cols = ["#", "Filtro", "Apuestas", "WR", "Real", "Martingala", "Diff", "MaxBet", "DD", "Racha"]
        widths = [28, 200, 80, 60, 80, 100, 80, 70, 70, 60]
        col_anchors = ['w', 'w', 'e', 'e', 'e', 'e', 'e', 'e', 'e', 'e']

        cv_frame = tk.Frame(tf, bg=C['panel'])
        cv_frame.pack(fill='both', expand=True, padx=4, pady=(0, 4))

        self._cv = tk.Canvas(cv_frame, bg=C['panel'], highlightthickness=0)
        vsb = tk.Scrollbar(cv_frame, orient='vertical', command=self._cv.yview)
        self._cv.configure(yscrollcommand=vsb.set)

        self._header_frame = tk.Frame(self._cv, bg=C['panel'])
        self._cv.create_window((0, 0), window=self._header_frame, anchor='nw', tags='inner')

        self._header_frame.bind('<Configure>', lambda e: self._cv.configure(scrollregion=self._cv.bbox('all')))

        self._cv.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        self._cv.bind('<MouseWheel>', lambda e: self._cv.yview_scroll(int(-1 * (e.delta / 120)), 'units'))

        # Table header labels
        hf = tk.Frame(self._header_frame, bg=C['bg'])
        hf.pack(fill='x')
        for i, (col_name, w) in enumerate(zip(cols, widths)):
            tk.Label(hf, text=col_name, font=('Consolas', 9, 'bold'),
                     bg=C['bg'], fg=C['muted'], width=max(1, w // 7),
                     anchor=col_anchors[i]).pack(side='left')

        self._row_container = tk.Frame(self._header_frame, bg=C['panel'])
        self._row_container.pack(fill='both', expand=True)

        self._row_widgets = []

    def _actualizar_tabla(self):
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets.clear()

        if not self._resultados:
            return

        sorted_fis = sorted(
            self._resultados.keys(),
            key=lambda fi: self._resultados[fi]['mart_final'],
            reverse=True
        )

        cols = ["#", "Filtro", "Apuestas", "WR", "Real", "Martingala", "Diff", "MaxBet", "DD", "Racha"]
        widths = [28, 200, 80, 60, 80, 100, 80, 70, 70, 60]
        col_anchors = ['w', 'w', 'e', 'e', 'e', 'e', 'e', 'e', 'e', 'e']

        for fi in sorted_fis:
            r = self._resultados[fi]
            name = FILTROS[fi][:28] if fi < len(FILTROS) else f'Filtro {fi}'
            diff = r['mart_final'] - r['real_final']
            bg_row = C['panel'] if fi % 2 == 0 else '#0C1E34'

            rf = tk.Frame(self._row_container, bg=bg_row)
            rf.pack(fill='x')

            vals = [
                str(fi), name, str(r['n_bets']), f"{r['win_rate']:.1f}%",
                f"{r['real_final']:+.2f}", f"{r['mart_final']:+.2f}",
                f"{diff:+.2f}", f"{r['max_bet']:.2f}",
                f"{r['max_drawdown']:.2f}", str(r['longest_loss']),
            ]
            colors_fg = [
                C['accent'], C['text'], C['text'], C['text'],
                C['accent2'] if r['real_final'] >= 0 else C['accent3'],
                C['accent2'] if r['mart_final'] >= 0 else C['accent3'],
                C['accent2'] if diff >= 0 else C['accent3'],
                C['warn'], C['accent3'] if r['max_drawdown'] < 0 else C['muted'],
                C['accent3'] if r['longest_loss'] > 5 else C['text'],
            ]

            for i, (val, w, anch, fgc) in enumerate(zip(vals, widths, col_anchors, colors_fg)):
                if fi == self._mejor_filtro_idx and i in (0, 1, 5):
                    fgc = C['accent2']
                    val = f"\u2605 {val}" if i == 1 else val

                lbl = tk.Label(rf, text=val, font=('Consolas', 9),
                               bg=bg_row, fg=fgc, width=max(1, w // 7),
                               anchor=anch, padx=2)
                lbl.pack(side='left')

            self._row_widgets.append(rf)

        self._lbl_count.config(text=f"{len(sorted_fis)} filtros  |  \u2605 = mejor Martingala")

    # ── Panel: Rondas (below filters table) ─────────────────────────

    def _panel_rondas(self, parent):
        parent.grid_rowconfigure(1, weight=0)
        parent.grid_columnconfigure(0, weight=1)

        rh_frame = tk.Frame(parent, bg=C['bg'])
        rh_frame.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        tk.Label(rh_frame, text="RONDAS", font=('Consolas', 9, 'bold'),
                 bg=C['bg'], fg=C['accent']).pack(side='left')
        self._lbl_rondas_count = tk.Label(rh_frame, text="",
                                          font=('Consolas', 8), bg=C['bg'], fg=C['muted'])
        self._lbl_rondas_count.pack(side='right')

        rf = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        rf.grid(row=2, column=0, sticky='nsew', pady=(2, 0))
        rf.grid_rowconfigure(0, weight=1)
        rf.grid_columnconfigure(0, weight=1)

        self._txt_rondas = tk.Text(rf, height=7, width=75,
                                    bg='#050A14', fg=C['text'],
                                    font=('Consolas', 10),
                                    bd=0, highlightthickness=1,
                                    highlightbackground=C['border'],
                                    highlightcolor=C['border'],
                                    insertbackground=C['accent'],
                                    wrap='none',
                                    state='normal')
        vsb_r = tk.Scrollbar(rf, orient='vertical',
                             command=self._txt_rondas.yview)
        self._txt_rondas.configure(yscrollcommand=vsb_r.set)

        self._txt_rondas.tag_configure('header', foreground=C['muted'])
        self._txt_rondas.tag_configure('w', foreground=C['accent2'])
        self._txt_rondas.tag_configure('l', foreground=C['accent3'])
        self._txt_rondas.tag_configure('risk_low', foreground=C['accent2'])
        self._txt_rondas.tag_configure('risk_mid', foreground=C['warn'])
        self._txt_rondas.tag_configure('risk_high', foreground='#FF8C00')
        self._txt_rondas.tag_configure('risk_crit', foreground=C['accent3'])
        self._txt_rondas.tag_configure('dim', foreground=C['muted'])

        self._txt_rondas.grid(row=0, column=0, sticky='nsew')
        vsb_r.grid(row=0, column=1, sticky='ns')

    # ── Panel: Graph ────────────────────────────────────────────────

    def _panel_grafico(self, parent):
        parent.grid_rowconfigure(0, weight=0)
        parent.grid_rowconfigure(1, weight=0)
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        # Row 0: header
        gf_header = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        gf_header.grid(row=0, column=0, sticky='nsew')
        tk.Frame(gf_header, bg=C['accent'], height=2).pack(fill='x', side='top')

        ctrl = tk.Frame(gf_header, bg=C['panel'])
        ctrl.pack(fill='x', padx=6, pady=(4, 4))

        tk.Label(ctrl, text="GRAFICO", font=FONT_MONO_B,
                 bg=C['panel'], fg=C['accent']).pack(side='left')

        self._lbl_mejor_nombre = tk.Label(ctrl, text="",
                                          font=FONT_SM, bg=C['panel'], fg=C['accent2'])
        self._lbl_mejor_nombre.pack(side='left', padx=(8, 0))

        tk.Label(ctrl, text="Filtro:", font=FONT_SM,
                 bg=C['panel'], fg=C['muted']).pack(side='right', padx=(0, 4))

        self._filtro_menu = ttk.Combobox(ctrl, values=FILTROS, state='readonly',
                                          width=20, font=FONT_SM)
        self._filtro_menu.pack(side='right')
        self._filtro_menu.bind('<<ComboboxSelected>>', self._on_filtro_change)

        # Row 1: info labels
        info_frame = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        info_frame.grid(row=1, column=0, sticky='nsew', pady=(2, 0))

        self._lbl_mart_val = tk.Label(info_frame, text="", font=FONT_MONO_B,
                                      bg=C['panel'], fg=C['accent'])
        self._lbl_mart_val.pack(side='left', padx=(10, 12), pady=4)

        self._lbl_real_val = tk.Label(info_frame, text="", font=FONT_MONO,
                                      bg=C['panel'], fg=C['muted'])
        self._lbl_real_val.pack(side='left', padx=(0, 12), pady=4)

        self._lbl_wr_val = tk.Label(info_frame, text="", font=FONT_MONO,
                                    bg=C['panel'], fg=C['warn'])
        self._lbl_wr_val.pack(side='left', pady=4)

        # Row 2: Matplotlib figure (takes remaining space)
        fig_frame = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        fig_frame.grid(row=2, column=0, sticky='nsew', pady=(2, 0))
        fig_frame.grid_rowconfigure(0, weight=1)
        fig_frame.grid_columnconfigure(0, weight=1)

        self._fig = plt.Figure(figsize=(5, 4), dpi=100, facecolor=C['bg'])
        self._ax = self._fig.add_subplot(111, facecolor=C['panel'])
        self._ax.tick_params(colors=C['muted'], labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color(C['border'])
        self._ax.set_xlabel("Operaciones", color=C['muted'], fontsize=9)
        self._ax.set_ylabel("PnL", color=C['muted'], fontsize=9)
        self._ax.axhline(y=0, color=C['border'], linewidth=0.6, linestyle='--')

        self._canvas = FigureCanvasTkAgg(self._fig, master=fig_frame)
        self._canvas.get_tk_widget().pack(fill='both', expand=True, padx=4, pady=4)



    def _actualizar_grafico(self):
        if not self._resultados:
            return

        idx = self.filtro_grafico.get()
        if idx not in self._curvas_mart:
            idx = self._mejor_filtro_idx
            if idx is None and self._resultados:
                idx = max(self._resultados.keys(),
                          key=lambda fi: self._resultados[fi]['mart_final'])
            if idx is None:
                return
            self.filtro_grafico.set(idx)

        self._ax.clear()
        self._ax.set_facecolor(C['panel'])

        real_c = self._curvas_reales.get(idx, [0])
        mart_c = self._curvas_mart.get(idx, [0])
        r = self._resultados.get(idx, {})

        n = max(len(real_c), len(mart_c))
        if n > 1:
            xs = list(range(n))
            if len(real_c) < n:
                real_c = real_c + [real_c[-1]] * (n - len(real_c))
            if len(mart_c) < n:
                mart_c = mart_c + [mart_c[-1]] * (n - len(mart_c))

            self._ax.plot(xs, real_c, color='#4A6080', linewidth=1.0, alpha=0.6, label='Real')
            self._ax.plot(xs, mart_c, color='#00D4FF', linewidth=1.8, label='Martingala')

        name = FILTROS[idx] if idx < len(FILTROS) else f'Filtro {idx}'
        title_color = C['accent2'] if r.get('mart_final', 0) > r.get('real_final', 0) else C['accent3']
        self._ax.set_title(f"[{idx}] {name}", color=title_color, fontsize=10, fontweight='bold')

        self._ax.tick_params(colors=C['muted'], labelsize=8)
        for spine in self._ax.spines.values():
            spine.set_color(C['border'])
        self._ax.axhline(y=0, color=C['border'], linewidth=0.6, linestyle='--')
        self._ax.set_xlabel("Operaciones", color=C['muted'], fontsize=9)
        self._ax.set_ylabel("PnL", color=C['muted'], fontsize=9)

        legend = self._ax.legend(loc='best', fontsize=8, facecolor=C['bg'],
                                  edgecolor=C['border'], labelcolor=[C['muted'], C['accent']])
        for text in legend.get_texts():
            text.set_color(C['text'])

        self._fig.tight_layout()
        self._canvas.draw()

        # Update info labels
        mart_final = r.get('mart_final', 0)
        real_final = r.get('real_final', 0)
        wr = r.get('win_rate', 0)
        n_bets = r.get('n_bets', 0)
        max_bet = r.get('max_bet', 0)
        dd = r.get('max_drawdown', 0)

        mart_color = C['accent2'] if mart_final >= 0 else C['accent3']
        real_color = C['accent2'] if real_final >= 0 else C['accent3']

        self._lbl_mart_val.config(text=f"Mart: {mart_final:+.2f}", fg=mart_color)
        self._lbl_real_val.config(text=f"Real: {real_final:+.2f}", fg=real_color)
        self._lbl_wr_val.config(text=f"WR: {wr:.1f}%  Bets: {n_bets}  MaxBet: {max_bet:.2f}  DD: {dd:.2f}")

        self._actualizar_rondas(idx)

    def _actualizar_rondas(self, idx):
        self._txt_rondas.delete('1.0', 'end')

        ops = self._datos_ops.get(idx, [])
        if not ops:
            self._lbl_rondas_count.config(text="sin datos")
            self._txt_rondas.insert('end', '  No hay datos para este filtro\n', 'dim')
            return

        apuesta_base = self.base_bet.get()
        capital_ini = self.capital.get()
        cap_val = self.cap.get()
        max_dobles = cap_val if cap_val > 0 else None
        current_bet = apuesta_base
        dobles = 0
        acum = 0.0
        rows = []
        total_ops = 0
        total_skip = 0
        bancarrota = False
        ban_idx = None
        n_mostrar = 200

        for op in ops:
            total_ops += 1
            delta = _extraer_delta(op)
            if delta == 0:
                total_skip += 1
                continue

            bet_used = current_bet
            issue = str(op.get('issue', ''))[-10:]
            resultado = 'W' if delta > 0 else 'L'
            apuesta_real = op.get('apuesta', 0)

            if delta > 0:
                pnl_mart = round(current_bet * PNL_ACIERTO_RATIO, 2)
                current_bet = apuesta_base
                dobles = 0
            else:
                pnl_mart = round(current_bet * PNL_FALLO_RATIO, 2)
                dobles += 1
                if max_dobles is not None and dobles > max_dobles:
                    current_bet = apuesta_base
                    dobles = 0
                else:
                    current_bet = round(apuesta_base * mult_martingala(dobles), 2)

            bank_before = round(capital_ini + acum, 2)
            risk_pct = round((bet_used / bank_before) * 100, 1) if bank_before > 0 else 999.0
            pnl_mart_round = round(pnl_mart, 2)
            acum = round(acum + pnl_mart_round, 2)
            bank_after = round(capital_ini + acum, 2)
            mult_usada = int(bet_used / apuesta_base) if bet_used >= apuesta_base else round(bet_used / apuesta_base, 1)
            rows.append((mult_usada, issue, resultado, delta, bet_used, bank_before, bank_after, apuesta_real, risk_pct))
            if not bancarrota and bank_after <= 0:
                bancarrota = True
                ban_idx = len(rows) - 1

        # Display only last n_mostrar rows (but ALL were calculated)
        if len(rows) > n_mostrar:
            rows = rows[-n_mostrar:]
            hubo_truncado = True
        else:
            hubo_truncado = False

        hdr = f"{'#':>3} {'Issue':>10} {'Rdo':>4} {'x':>4} {'Delta':>7} {'Bet':>7} {'Bank':>8} {'Risk%':>7} {'Apuesta':>7}\n"
        self._txt_rondas.insert('end', hdr, 'header')

        for i, (mult, issue, resultado, delta, bet_used, bank_before, bank_after, apuesta_real, risk_pct) in enumerate(rows):
            tag_rdo = 'w' if resultado == 'W' else 'l'
            if risk_pct == 999.0:
                risk_txt = "  BUST"
                risk_tag = 'risk_crit'
            elif risk_pct <= 5:
                risk_tag = 'risk_low'
                risk_txt = f"{risk_pct:>6.1f}%"
            elif risk_pct <= 25:
                risk_tag = 'risk_mid'
                risk_txt = f"{risk_pct:>6.1f}%"
            elif risk_pct <= 50:
                risk_tag = 'risk_high'
                risk_txt = f"{risk_pct:>6.1f}%"
            else:
                risk_tag = 'risk_crit'
                risk_txt = f"{risk_pct:>6.1f}%"
            pre = f"{i:>3} {issue:>10} {resultado:>4} {mult:>4} {delta:+7.2f} {bet_used:>7.2f} {bank_after:>8.2f} "
            post = f" {apuesta_real:>7.2f}\n"
            self._txt_rondas.insert('end', pre, tag_rdo)
            self._txt_rondas.insert('end', risk_txt, risk_tag)
            self._txt_rondas.insert('end', post, tag_rdo)

        # Footer: final balance (todas las ops calculadas, no truncadas)
        r = self._resultados.get(idx, {})
        mart_final = r.get('mart_final', acum)
        final_bank = round(capital_ini + mart_final, 2)
        final_color = C['accent2'] if mart_final >= 0 else C['accent3']
        sep = f"\n{'─'*65}\n"
        self._txt_rondas.insert('end', sep, 'dim')
        n_real = len(ops) - total_skip
        line = f"  FINAL: Bank={final_bank:+>10.2f}  Martingale PnL={mart_final:+>9.2f}  ({n_real} bets / {total_ops} total ops)"
        if hubo_truncado:
            line += f"  (mostradas ultimas {n_mostrar})"
        self._txt_rondas.insert('end', line + '\n', final_color)

        extra = ""
        if bancarrota:
            extra += f"  |  \u2620 BANCARROTA en ronda #{ban_idx}"
        self._lbl_rondas_count.config(
            text=f"{n_real} bets / {total_ops} ops{extra}" if hubo_truncado
                 else f"{n_real} bets / {total_ops} ops{extra}")
        self._txt_rondas.see('end')

    # ── Ventanas (sliding windows) ─────────────────────────────────

    def _calcular_ventanas(self, idx):
        ops = self._datos_ops.get(idx, [])
        if not ops:
            return []

        base_bet = self.base_bet.get()
        cap = self.cap.get()
        max_dobles = cap if cap > 0 else None
        window_size = self.window_size.get()
        if window_size < 10:
            window_size = 10
        if window_size > len(ops):
            return []

        results = []
        n_real = sum(1 for op in ops if _extraer_delta(op) != 0)
        if n_real < window_size:
            return []

        for i in range(0, len(ops) - window_size + 1, 1):
            window_ops = ops[i : i + window_size]
            res = simular_martingala(window_ops, base_bet=base_bet, max_dobles=max_dobles)
            results.append({
                'win_idx': i,
                'start': i,
                'end': i + window_size - 1,
                'final_balance': res['final_balance'],
                'n_bets': res['n_bets'],
                'win_rate': res['win_rate'],
            })

        return results

    def _actualizar_ventanas(self, idx):
        self._txt_ventanas.delete('1.0', 'end')

        if idx not in self._datos_ops or not self._datos_ops[idx]:
            self._lbl_ventanas_count.config(text="sin datos")
            return

        results = self._calcular_ventanas(idx)
        if not results:
            self._lbl_ventanas_count.config(text="sin ventanas")
            self._txt_ventanas.insert('end', '  Ventanas: window_size mayor que datos disponibles\n', 'dim')
            return

        n_mostrar = self.num_ventanas.get()
        if n_mostrar < 1:
            n_mostrar = 500
        total = len(results)
        if total > n_mostrar:
            display = results[-n_mostrar:]
        else:
            display = results

        hdr = f"{'#':>5} {'Win':>4} {'Start':>6} {'End':>6} {'PnL':>8} {'Bets':>5} {'WR':>5}\n"
        self._txt_ventanas.insert('end', hdr, 'header')

        for row in display:
            tag = 'pos' if row['final_balance'] >= 0 else 'neg'
            line = f"{row['win_idx']:>5} {row['start']:>4} {row['end']:>6} {row['final_balance']:+>8.2f} {row['n_bets']:>5} {row['win_rate']:>5.1f}%\n"
            self._txt_ventanas.insert('end', line, tag)

        sep = f"\n{'─'*50}\n"
        self._txt_ventanas.insert('end', sep, 'dim')
        if total > n_mostrar:
            self._txt_ventanas.insert('end', f"  Total: {total} ventanas (mostrando ultimas {n_mostrar})\n", 'dim')
        else:
            self._txt_ventanas.insert('end', f"  Total: {total} ventanas\n", 'dim')

        self._lbl_ventanas_count.config(text=f"{total} ventanas{'  |  ultimas '+str(n_mostrar) if total > n_mostrar else ''}")
        self._txt_ventanas.see('end')

        # Store stats for RESUMEN panel
        pnls = [r['final_balance'] for r in results]
        if pnls:
            import statistics
            best = max(pnls)
            worst = min(pnls)
            avg = sum(pnls) / len(pnls)
            n_pos = sum(1 for p in pnls if p > 0)
            n_cero = sum(1 for p in pnls if p == 0)
            n_neg = sum(1 for p in pnls if p < 0)
            pos_pct = n_pos / len(pnls) * 100
            std = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
            self._ventanas_stats[idx] = {
                'total': total,
                'best': best,
                'worst': worst,
                'avg': avg,
                'n_pos': n_pos,
                'n_cero': n_cero,
                'n_neg': n_neg,
                'pos_pct': pos_pct,
                'std': std,
            }
        else:
            self._ventanas_stats[idx] = None

        # Update RESUMEN if this idx is the current filter
        if idx == self.filtro_grafico.get():
            self._actualizar_ventanas_resumen(idx)

    def _actualizar_ventanas_resumen(self, idx):
        stats = self._ventanas_stats.get(idx)
        if not stats:
            self._lbl_win_header.config(text="VENTANAS (—)")
            for lbl in (self._lbl_win_mejor, self._lbl_win_peor,
                        self._lbl_win_promedio, self._lbl_win_pos,
                        self._lbl_win_neg, self._lbl_win_std):
                lbl.config(text="")
            return

        def _pnl(val):
            return f"{val:+>8.2f}"

        self._lbl_win_header.config(text=f"VENTANAS (step=1, win={self.window_size.get()})")
        self._lbl_win_mejor.config(text=f"Mejor:  {_pnl(stats['best'])}")
        self._lbl_win_peor.config(text=f"Peor:    {_pnl(stats['worst'])}")
        self._lbl_win_promedio.config(text=f"Promedio: {_pnl(stats['avg'])}")
        self._lbl_win_pos.config(text=f"Positivas: {stats['n_pos']} / {stats['total']}  ({stats['pos_pct']:.1f}%)",
                                  fg=C['accent2'] if stats['pos_pct'] >= 50 else C['accent3'])
        self._lbl_win_neg.config(text=f"Negativas: {stats['n_neg']} / {stats['total']}",
                                  fg=C['accent3'])
        self._lbl_win_std.config(text=f"StdDev: {stats['std']:.2f}")

    def _on_calcular_ventanas(self):
        self._btn_calcular_ventanas.config(text="\u23f3 ...", state='disabled')
        self.root.update_idletasks()
        idx = self.filtro_grafico.get()
        if idx in self._datos_ops:
            self._actualizar_ventanas(idx)
        self._btn_calcular_ventanas.config(text="\u25b6 Calcular", state='normal')

    def _on_filtro_change(self, event=None):
        sel = self._filtro_menu.current()
        if sel >= 0:
            self.filtro_grafico.set(sel)
            self._actualizar_ui()

    # ── Core Logic ──────────────────────────────────────────────────

    def _cargar_y_simular(self):
        global PNL_ACIERTO_RATIO, PNL_FALLO_RATIO
        PNL_ACIERTO_RATIO = self.pnl_acierto.get()
        PNL_FALLO_RATIO = self.pnl_fallo.get()
        base_bet = self.base_bet.get()
        cap = self.cap.get()
        max_dobles = cap if cap > 0 else None

        if not LONGFILE.exists():
            return

        try:
            with open(LONGFILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception:
            return

        by_idx = {}
        for line in lines:
            try:
                d = json.loads(line)
            except Exception:
                continue
            fi = d.get('filtro_idx')
            if fi is None:
                continue
            if fi not in by_idx:
                by_idx[fi] = []
            by_idx[fi].append(d)

        if not by_idx:
            return

        resultados = {}
        curvas_reales = {}
        curvas_mart = {}

        for fi in sorted(by_idx.keys()):
            ops = by_idx[fi]
            real = simular_real(ops)
            mart = simular_martingala(ops, base_bet=base_bet, max_dobles=max_dobles)

            resultados[fi] = {
                'filtro': FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}',
                'n_bets': mart['n_bets'],
                'n_skips': mart['n_skips'],
                'n_total': mart['n_total'],
                'n_wins': mart['n_wins'],
                'n_losses': mart['n_losses'],
                'win_rate': round(mart['win_rate'], 1),
                'real_final': real['final_balance'],
                'mart_final': mart['final_balance'],
                'max_bet': mart['max_bet'],
                'max_drawdown': mart['max_drawdown'],
                'longest_loss': mart['longest_loss_streak'],
}
            curvas_reales[fi] = real['curve']
            curvas_mart[fi] = mart['curve']

        self._resultados = resultados
        self._curvas_reales = curvas_reales
        self._curvas_mart = curvas_mart
        self._datos_ops = by_idx

        if resultados:
            mejor_fi = max(resultados.keys(), key=lambda fi: resultados[fi]['mart_final'])
            self._mejor_filtro_idx = mejor_fi
            if self.filtro_grafico.get() not in resultados:
                self.filtro_grafico.set(mejor_fi)
        else:
            self._mejor_filtro_idx = None

    def _actualizar_ui(self):
        if not self._resultados:
            return

        r = self._resultados

        def _pnl_txt(val):
            if val >= 0:
                return f"+{val:.2f}"
            return f"{val:.2f}"

        idx = self.filtro_grafico.get()
        if idx not in r:
            idx = self._mejor_filtro_idx
            if idx is None or idx not in r:
                idx = min(r.keys())
            self.filtro_grafico.set(idx)

        rd = r[idx]
        nombre = FILTROS[idx] if idx < len(FILTROS) else f'Filtro {idx}'
        real_val = rd['real_final']
        mart_val = rd['mart_final']
        diff_val = mart_val - real_val

        self._lbl_filtro_nombre.config(text=f"[{idx}] {nombre}")
        self._lbl_real.config(text=f"Real:  {_pnl_txt(real_val)}",
                              fg=C['accent2'] if real_val >= 0 else C['accent3'])
        self._lbl_mart.config(text=f"Mart:  {_pnl_txt(mart_val)}",
                              fg=C['accent2'] if mart_val >= 0 else C['accent3'])
        self._lbl_diff.config(text=f"Diff:  {_pnl_txt(diff_val)}",
                              fg=C['accent2'] if diff_val >= 0 else C['accent3'])
        self._lbl_wr.config(text=f"WR:  {rd['win_rate']:.1f}%  ({rd['n_wins']}W / {rd['n_losses']}L)")
        self._lbl_racha.config(text=f"Peor racha:  {rd['longest_loss']}")
        cap_ini = self.capital.get()
        cap_final = round(cap_ini + rd['mart_final'], 2)
        ban_txt = f"\u2620 BANCARROTA" if cap_final <= 0 else f"Capital final: {cap_final:.2f}"
        ban_color = C['accent3'] if cap_final <= 0 else C['accent2'] if cap_final > cap_ini else C['warn']
        self._lbl_capital.config(text=f"{cap_ini:.2f} inicial  \u2192  {ban_txt}", fg=ban_color)

        if self._mejor_filtro_idx is not None:
            mejor_nombre = FILTROS[self._mejor_filtro_idx] if self._mejor_filtro_idx < len(FILTROS) else f'Filtro {self._mejor_filtro_idx}'
            self._lbl_mejor_nombre.config(text=f"\u2605 [{self._mejor_filtro_idx}] {mejor_nombre}")

        self._actualizar_tabla()
        self._actualizar_grafico()
        self._actualizar_ventanas_resumen(idx)

    # ── Polling (always active loop) ────────────────────────────────

    def _recalcular(self):
        self._cargar_y_simular()
        self._actualizar_ui()

    def _toggle(self):
        if self._encendido:
            self._detener()
        else:
            self._encender()

    def _encender(self):
        if self._encendido:
            return
        self._encendido = True
        self._btn_encender.config(text="\u23f8 APAGAR", fg=C['accent3'])
        self._lbl_status.config(text="\u25cf  ENCENDIDO", fg=C['accent2'])
        self._ultimo_mtime = 0
        self._poll()

    def _detener(self):
        self._encendido = False
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        self._btn_encender.config(text="\u23cf ENCENDER", fg=C['accent2'])
        self._lbl_status.config(text="\u25cf  DETENIDO", fg=C['muted'])

    def _poll(self):
        if not self._encendido:
            return

        changed = False
        if LONGFILE.exists():
            try:
                mtime = LONGFILE.stat().st_mtime
                if mtime != self._ultimo_mtime:
                    self._ultimo_mtime = mtime
                    changed = True
            except Exception:
                pass

        if changed:
            self._cargar_y_simular()
            self._actualizar_ui()
            self._lbl_ultima_act.config(
                text=f"Ultima act: {mtime}" if 'mtime' in dir() else "")

        self._poll_job = self.root.after(REFRESH_MS, self._poll)


def main():
    app = MartingalaDashboard()
    app.root.mainloop()


if __name__ == '__main__':
    main()
