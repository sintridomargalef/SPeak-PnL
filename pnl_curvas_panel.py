"""
PNL DASHBOARD — Ventana CURVAS: tabla canvas con estado de cada filtro.
Mismo patrón que DecisionHistoryWindow pero para mostrar stats de filtros.
"""
import tkinter as tk
import json
import threading
import re
from pathlib import Path

from pnl_config import C, FONT_TITLE, FONT_SM

# ── Constantes de layout ───────────────────────────────────────────────────────
CURVAS_COL_DEFS = [
    ('_dot',   '',         20,  'c'),   # bolita verde/rojo por PNL
    ('idx',    '#',        30,  'c'),   # índice filtro
    ('nombre', 'Filtro',  220,  'l'),   # nombre
    ('ops',    'Ops',      55,  'c'),   # n_total
    ('ac',     'Ac',       45,  'c'),   # n_aciertos
    ('fa',     'Fa',       45,  'c'),   # fallos
    ('wr',     'WR%',      65,  'c'),   # win rate
    ('pnl',    'PNL',      80,  'c'),   # PNL acumulado
    ('ratio',  'PNL/op',   80,  'c'),   # eficiencia
]

ROW_H    = 28
HDR_H    = 32
DOT_R    = 7
SEP_CLR  = '#1A3050'
HDR_BG   = '#060F1E'
HDR_FG   = '#00D4FF'
FONT_HDR = ('Consolas', 10, 'bold')
FONT_ROW = ('Consolas', 10)

CURVAS_GEOM_FILE = Path(__file__).parent / 'pnl_curvas_geom.json'


class CurvasTable(tk.Frame):
    """Widget de tabla canvas: muestra el estado de cada filtro (curvas)."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C['bg'], **kw)

        import tkinter.font as tkfont
        fnt = tkfont.Font(family='Consolas', size=10, weight='bold')
        HDR_PAD = 20
        self._col_defs = [
            (key, label, max(col_w, fnt.measure(label) + HDR_PAD if label else col_w), align)
            for key, label, col_w, align in CURVAS_COL_DEFS
        ]
        self.TOTAL_W = sum(c[2] for c in self._col_defs)

        self._cv = tk.Canvas(self, bg='#07101E', highlightthickness=0,
                             width=self.TOTAL_W)
        vsb = tk.Scrollbar(self, orient='vertical',   command=self._cv.yview)
        hsb = tk.Scrollbar(self, orient='horizontal', command=self._cv.xview)
        self._cv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        hsb.grid(row=1, column=0, sticky='ew')
        vsb.grid(row=0, column=1, sticky='ns')
        self._cv.grid(row=0, column=0, sticky='nsew')
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._cv.bind('<MouseWheel>', self._on_scroll)
        self._cv.bind('<Button-4>',   self._on_scroll)
        self._cv.bind('<Button-5>',   self._on_scroll)

        self._dibujar_header()

    def _on_scroll(self, ev):
        delta = -1 if (getattr(ev, 'num', 0) == 5 or getattr(ev, 'delta', 0) < 0) else 1
        self._cv.yview_scroll(delta, 'units')

    def _dibujar_header(self):
        self._cv.delete('header')
        x = 0
        self._cv.create_rectangle(0, 0, self.TOTAL_W, HDR_H,
                                   fill=HDR_BG, outline='', tags='header')
        for key, label, col_w, align in self._col_defs:
            if label:
                tx = x + col_w // 2
                self._cv.create_text(tx, HDR_H // 2, text=label,
                                     fill=HDR_FG, font=FONT_HDR,
                                     anchor='center', tags='header')
            self._cv.create_line(x + col_w, 0, x + col_w, HDR_H,
                                  fill=SEP_CLR, width=1, tags='header')
            x += col_w
        self._cv.create_line(0, HDR_H, self.TOTAL_W, HDR_H,
                              fill='#00D4FF', width=1, tags='header')

    def actualizar_anchos(self, widths_dict):
        """Recibe {key: ancho} y reconstruye col_defs + redibuja."""
        import tkinter.font as tkfont
        fnt = tkfont.Font(family='Consolas', size=10, weight='bold')
        HDR_PAD = 20
        self._col_defs = [
            (key, label,
             max(widths_dict.get(key, col_w),
                 fnt.measure(label) + HDR_PAD if label else 0),
             align)
            for key, label, col_w, align in CURVAS_COL_DEFS
        ]
        self.TOTAL_W = sum(c[2] for c in self._col_defs)
        self._cv.configure(width=self.TOTAL_W)
        self._dibujar_header()

    def set_data(self, filas):
        self._cv.delete('row')
        n = len(filas)
        total_h = HDR_H + n * ROW_H + 4

        # ── Top 3 por PNL/op entre filtros con ops > 0 ──────────────────
        _PODIO = {1: ('#FFD700', '#1F1C00'),   # oro
                  2: ('#C0C0C0', '#161620'),   # plata
                  3: ('#CD7F32', '#1A1000')}   # bronce
        candidatos = [(d.get('ratio', 0.0) or 0.0, i)
                      for i, d in enumerate(filas) if (d.get('ops') or 0) > 0]
        candidatos.sort(reverse=True)
        top3 = {idx: rank + 1 for rank, (_, idx) in enumerate(candidatos[:3])}

        for row_idx, d in enumerate(filas):
            y       = HDR_H + row_idx * ROW_H
            pnl_val = d.get('pnl', 0.0) or 0.0
            rank    = top3.get(row_idx)
            dot_col, bg_top = _PODIO.get(rank, (None, None))
            if dot_col is None:
                dot_col = C['accent2'] if pnl_val >= 0 else C['accent3']
            pnl_col = C['accent2'] if pnl_val >= 0 else C['accent3']
            # Fondo: podio o alternado normal
            if bg_top:
                bg = bg_top
            else:
                bg = '#0A1628' if row_idx % 2 == 0 else '#07101E'

            self._cv.create_rectangle(0, y, self.TOTAL_W, y + ROW_H,
                                       fill=bg, outline='', tags='row')
            self._cv.create_line(0, y + ROW_H, self.TOTAL_W, y + ROW_H,
                                  fill=SEP_CLR, width=1, tags='row')

            x = 0
            for key, label, col_w, align in self._col_defs:
                cy = y + ROW_H // 2

                if key == '_dot':
                    cx = x + col_w // 2
                    self._cv.create_oval(cx - DOT_R, cy - DOT_R,
                                         cx + DOT_R, cy + DOT_R,
                                         fill=dot_col, outline=dot_col, tags='row')
                else:
                    raw = d.get(key, '')
                    if key == 'wr':
                        val = f"{raw:.1f}%" if isinstance(raw, (int, float)) else str(raw)
                    elif key == 'pnl':
                        val = f"{raw:+.2f}" if isinstance(raw, (int, float)) else str(raw)
                    elif key == 'ratio':
                        val = f"{raw:+.3f}" if isinstance(raw, (int, float)) else str(raw)
                    else:
                        val = str(raw) if raw is not None else ''

                    # Texto más brillante para el podio
                    if rank:
                        fg = (dot_col if key in ('pnl', 'ratio', 'wr')
                              else C['white'])
                    else:
                        fg = pnl_col if key in ('pnl', 'ratio') else C['text']

                    if align == 'c':
                        tx  = x + col_w // 2
                        anc = 'center'
                    else:
                        tx  = x + 10
                        anc = 'w'

                    max_c = max(1, (col_w - 10) // 7)
                    if len(val) > max_c:
                        val = val[:max_c - 1] + '…'

                    fnt = (FONT_ROW[0], FONT_ROW[1], 'bold') if rank else FONT_ROW
                    self._cv.create_text(tx, cy, text=val,
                                         fill=fg, font=fnt,
                                         anchor=anc, tags='row')

                self._cv.create_line(x + col_w, y, x + col_w, y + ROW_H,
                                      fill=SEP_CLR, width=1, tags='row')
                x += col_w

        self._cv.configure(scrollregion=(0, 0, self.TOTAL_W, total_h))
        self._cv.yview_moveto(0)


# ── Ventana singleton ─────────────────────────────────────────────────────────

class CurvasWindow(tk.Toplevel):
    """Ventana independiente con tabla de estado de filtros. Singleton."""

    _instancia = None

    def __init__(self, master, get_filtro_hist, get_filtros_curva):
        super().__init__(master)
        self._get_filtro_hist   = get_filtro_hist
        self._get_filtros_curva = get_filtros_curva
        self._geom_after_id     = None
        CurvasWindow._instancia = self

        self.title("PNL — CURVAS DE FILTROS")
        self.configure(bg=C['bg'])
        self._restaurar_geometria()
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.bind('<Configure>', self._on_configure)
        self._construir_ui()
        self.refrescar()

        # Cargar anchos desde Sheets COLUMNAS / CURVAS en background
        def _cargar_anchos(win=self):
            try:
                from configurador import conectar_excel
                ws = conectar_excel().worksheet("COLUMNAS")
                filas = ws.get_all_values()
                widths = {}
                for fila in filas:
                    if (len(fila) >= 3
                            and str(fila[0]).strip().upper() == 'CURVAS'
                            and fila[1] and fila[2]):
                        try:
                            widths[fila[1].strip()] = int(float(str(fila[2]).replace(',', '.')))
                        except ValueError:
                            pass
                if widths:
                    win.after(0, lambda w=widths: win.aplicar_anchos(w))
            except Exception:
                pass
        threading.Thread(target=_cargar_anchos, daemon=True).start()

    @classmethod
    def abrir_o_focus(cls, master, get_filtro_hist, get_filtros_curva):
        if cls._instancia and cls._instancia.winfo_exists():
            cls._instancia.lift()
            cls._instancia.focus_force()
            cls._instancia.refrescar()
            return cls._instancia
        return cls(master, get_filtro_hist, get_filtros_curva)

    # ── Geometría ──

    def _restaurar_geometria(self):
        try:
            cfg = json.loads(CURVAS_GEOM_FILE.read_text(encoding='utf-8'))
            self.geometry(f"{cfg['w']}x{cfg['h']}+{cfg['x']}+{cfg['y']}")
        except Exception:
            self.geometry("600x520")

    def _on_configure(self, event=None):
        if self._geom_after_id:
            try:
                self.after_cancel(self._geom_after_id)
            except Exception:
                pass
        self._geom_after_id = self.after(600, self._guardar_geometria)

    def _guardar_geometria(self):
        self._geom_after_id = None
        try:
            geo = self.geometry()
            m = re.match(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)', geo)
            if m:
                w, h, x, y = m.groups()
                CURVAS_GEOM_FILE.write_text(
                    json.dumps({'w': int(w), 'h': int(h),
                                'x': int(x), 'y': int(y)}),
                    encoding='utf-8')
        except Exception:
            pass

    def _cerrar(self):
        self._guardar_geometria()
        CurvasWindow._instancia = None
        self.destroy()

    # ── UI ──

    def _construir_ui(self):
        # Header
        hf = tk.Frame(self, bg='#020810', height=46)
        hf.pack(fill='x')
        hf.pack_propagate(False)
        tk.Frame(hf, bg=C['accent'], height=2).pack(fill='x', side='top')
        inner = tk.Frame(hf, bg='#020810')
        inner.pack(fill='both', expand=True, padx=12)

        tk.Label(inner, text="CURVAS DE FILTROS",
                 font=FONT_TITLE, bg='#020810', fg=C['accent']).pack(side='left', pady=10)

        self._lbl_count = tk.Label(inner, text="", font=FONT_SM,
                                   bg='#020810', fg=C['muted'])
        self._lbl_count.pack(side='right', padx=12)

        tk.Button(inner, text="REFRESCAR", font=('Consolas', 10, 'bold'),
                  bg=C['border'], fg=C['accent2'], relief='flat', cursor='hand2',
                  padx=10, command=self.refrescar).pack(side='right', padx=3)

        # Barra VENTANA
        ctrl = tk.Frame(self, bg=C['bg'])
        ctrl.pack(fill='x', padx=6, pady=(2, 0))
        tk.Label(ctrl, text="VENTANA:", font=('Consolas', 10, 'bold'),
                 bg=C['bg'], fg=C['muted']).pack(side='left', padx=(4, 6))
        self._ventana = tk.StringVar(value='TODOS')
        opciones = ('25', '50', '100', '200', '300', '500', 'TODOS')
        om = tk.OptionMenu(ctrl, self._ventana, *opciones,
                           command=lambda _: self.refrescar())
        om.config(font=('Consolas', 10, 'bold'), bg='#0D2137', fg=C['accent2'],
                  activebackground='#1A3050', activeforeground=C['accent2'],
                  highlightthickness=0, relief='flat', cursor='hand2', width=6)
        om['menu'].config(font=('Consolas', 10), bg='#0D2137', fg=C['accent2'],
                          activebackground='#1A3050', activeforeground=C['accent2'])
        om.pack(side='left')

        # Tabla canvas
        self._tabla = CurvasTable(self)
        self._tabla.pack(fill='both', expand=True, padx=0, pady=0)

    # ── Datos ──

    def refrescar(self):
        filtro_hist   = self._get_filtro_hist()
        filtros_curva = self._get_filtros_curva()
        vent = self._ventana.get() if self._ventana else 'TODOS'
        filas = []
        for i, entry in enumerate(filtros_curva):
            nombre = entry[0]
            hist   = filtro_hist.get(i)
            if not hist or not hist[0]:
                pnl   = 0.0
                n_ac  = 0
                n_tot = 0
            else:
                curva_full, n_ac_full, n_tot_full, _ = hist
                if vent != 'TODOS' and len(curva_full) > 0:
                    _n = int(vent)
                    curva_w = curva_full[-_n:] if len(curva_full) > _n else curva_full
                    if len(curva_w) > 1:
                        deltas = [round(curva_w[j] - curva_w[j-1], 2)
                                  for j in range(1, len(curva_w))]
                        n_tot = sum(1 for d in deltas if d != 0)
                        n_ac  = sum(1 for d in deltas if d > 0)
                        pnl   = round(curva_w[-1] - curva_w[0], 2)
                    else:
                        pnl, n_ac, n_tot = (curva_w[-1] if curva_w else 0.0), 0, 0
                else:
                    curva_w = curva_full
                    pnl     = curva_full[-1] if curva_full else 0.0
                    n_ac    = n_ac_full
                    n_tot   = n_tot_full
            wr    = (n_ac / n_tot * 100) if n_tot else 0.0
            ratio = pnl / n_tot if n_tot else 0.0
            filas.append({
                'idx':    i,
                'nombre': nombre,
                'ops':    n_tot,
                'ac':     n_ac,
                'fa':     n_tot - n_ac,
                'wr':     wr,
                'pnl':    pnl,
                'ratio':  ratio,
            })
        self._tabla.set_data(filas)
        n_con_datos = sum(1 for f in filas if f['ops'] > 0)
        vent_txt = f"  [{vent}]" if vent != 'TODOS' else ''
        self._lbl_count.config(
            text=f"{len(filas)} filtros  ({n_con_datos} con datos){vent_txt}")

    def aplicar_anchos(self, widths_dict):
        """Actualiza anchos de columna y redibuja la tabla."""
        self._tabla.actualizar_anchos(widths_dict)
        self.refrescar()
