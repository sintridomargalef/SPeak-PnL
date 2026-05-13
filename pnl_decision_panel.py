"""
PNL DASHBOARD — Ventana de histórico de decisiones pre-apuesta.
Tabla personalizada con Canvas: bolitas de color, separadores y diseño profesional.
"""
import tkinter as tk
from tkinter import messagebox
import json

from pnl_config import (C, FONT_TITLE, FONT_SM,
                         DECISION_HIST_FILE, DECISION_GEOM_FILE, FILTROS_CURVA,
                         FILTROS_LONG_FILE)

# ── Definición de columnas: (key, encabezado, ancho, alineación) ──────────────
COL_DEFS = [
    ('_dot',     '',         30,  'c'),   # círculo de estado
    ('hora',     'Hora',     90,  'c'),
    ('issue',    'Ronda',   115,  'c'),
    ('mayor',    'Mayor',    72,  'br'),  # círculo azul/rojo
    ('p_b',      'B%',       64,  'c'),
    ('p_r',      'R%',       64,  'c'),
    ('dif',      'Δ',        58,  'c'),
    ('wr',       'WR%',     168,  'c'),
    ('rango',    'Rango',    75,  'c'),
    ('est',      'Est',      78,  'c'),
    ('acel',     'Acel',     66,  'c'),
    ('modo',     'Modo',     85,  'c'),
    ('filtro_idx',    '#',     38,  'c'),
    ('filtro_nombre', 'Filtro', 200, 'l'),
    ('filtro',   'F.activo', 180,  'l'),
    ('ep_gate',  'EP Gate', 260,  'l'),
    ('wr_ep',    'WR EP%',   80,  'c'),
    ('decision', 'Decisión',145,  'c'),
    ('color',    'Color',    68,  'br'),  # círculo azul/rojo
    ('winner',   'Ganador',  78,  'br'),  # círculo azul/rojo
    ('acierto',  '✓/✗',      58,  'c'),
    ('pnl',           'PNL',            78,  'c'),
    ('mult',          'MULT',           55,  'c'),
    ('conf',          'CONF',           45,  'c'),
    ('balance_real',  'Balance real',  130,  'c'),
    ('balance_filtro','Balance filtro', 130,  'c'),
    ('analisis',  'Análisis',   360,  'l'),
]

ROW_H    = 28
HDR_H    = 32
DOT_R    = 7        # radio del círculo
SEP_CLR  = '#1A3050'
HDR_BG   = '#060F1E'
HDR_FG   = '#00D4FF'
FONT_HDR  = ('Consolas', 10, 'bold')
FONT_ROW  = ('Consolas', 10)
FONT_ROWB = ('Consolas', 10, 'bold')   # negrita para SKIP en columna Modo
FONT_DOT  = ('Consolas', 11, 'bold')

# Colores de bolita por estado
def _dot_color(d):
    decision = d.get('decision', '')
    modo     = d.get('modo', '')
    if decision == 'OBS':
        return '#FFB800'
    if decision == 'SKIP':
        return '#3A5070'
    # APOSTADA
    if d.get('winner') is None:
        return '#FFD700'
    gano = bool(d.get('acierto'))
    if modo == 'INVERSO':
        return '#FF00FF' if gano else '#FF44AA'
    return '#00FF88' if gano else '#FF3366'

# Colores de texto por estado
def _row_fg(d):
    decision = d.get('decision', '')
    modo     = d.get('modo', '')
    if decision == 'OBS':
        return '#FFB800'
    if decision == 'SKIP':
        return '#00FF88'
    if d.get('winner') is None:
        return '#FFD700'
    gano = bool(d.get('acierto'))
    if modo == 'INVERSO':
        return '#FF00FF' if gano else '#FF5599'
    return '#00FF88' if gano else '#FF5577'

# Colores de fondo alternado
def _row_bg(d, idx):
    decision = d.get('decision', '')
    base_even = '#0A1628'
    base_odd  = '#07101E'
    base = base_even if idx % 2 == 0 else base_odd
    if decision == 'OBS':
        return '#1A1300' if idx % 2 == 0 else '#131000'
    if decision != 'APOSTADA':
        return base
    gano = bool(d.get('acierto')) if d.get('winner') is not None else None
    if gano is None:
        return '#1A1500' if idx % 2 == 0 else '#131000'
    modo = d.get('modo', '')
    if modo == 'INVERSO':
        return '#1A0A2A' if idx % 2 == 0 else '#130720'
    if gano:
        return '#081F10' if idx % 2 == 0 else '#061508'
    return '#200810' if idx % 2 == 0 else '#180608'


def _fmt(v, dec=1):
    if v is None:
        return ''
    try:
        return f"{float(v):.{dec}f}"
    except Exception:
        return str(v)


def _truncar(texto, col_w, padding=10, char_w=7):
    """Trunca el texto para que quepa en col_w píxeles."""
    max_c = max(1, (col_w - padding * 2) // char_w)
    if len(texto) > max_c:
        return texto[:max_c - 1] + '…'
    return texto


def _br_color(valor):
    """Devuelve color hex para bolita azul/rojo según el valor."""
    v = (valor or '').upper()
    if 'AZU' in v or 'BLU' in v:
        return '#2B7FFF'
    if 'ROJ' in v or 'RED' in v:
        return '#FF3366'
    return None   # sin bolita si está vacío


def _row_values(d, analizar_fn=None):
    return {
        '_dot':     '',
        'hora':     d.get('hora', ''),
        'issue':    d.get('issue', ''),
        'mayor':    d.get('mayor') or '',   # raw para _br_color
        'p_b':      _fmt(d.get('p_b')),
        'p_r':      _fmt(d.get('p_r')),
        'dif':      _fmt(d.get('dif')),
        'wr':       _fmt(d.get('wr')),
        'rango':    d.get('rango', ''),
        'est':      d.get('est', ''),
        'acel':     _fmt(d.get('acel')),
        'modo':     d.get('modo', ''),
        'filtro_idx':    str(d['filtro_idx']) if d.get('filtro_idx') is not None else '',
        'filtro_nombre': d.get('filtro_nombre', ''),
        'filtro':   d.get('filtro', ''),
        'ep_gate':  d.get('ep_gate', ''),
        'wr_ep':    _fmt(d.get('wr_ep')) if d.get('wr_ep') is not None else '',
        'decision': ('NO APUESTA' if d.get('decision') == 'SKIP' else d.get('decision', '')),
        'color':    d.get('color_apostado') or '',   # raw para _br_color
        'winner':   d.get('winner') or '',           # raw para _br_color
        'acierto':  d.get('acierto_marca', ''),
        'pnl':          _fmt(d.get('pnl')) if d.get('pnl') is not None else '',
        'mult':         str(d['mult']) if d.get('mult') is not None else '',
        'conf':         str(d['conf']) if d.get('conf') is not None else '',
        'balance_real': (f"{d['balance_real']:+.2f} €"
                         if d.get('balance_real') is not None else ''),
        'balance_filtro': (f"{d['saldo']:+.2f} €" if d.get('saldo') is not None
                           else (f"{d['balance_filtro']:+.2f} €"
                                 if d.get('balance_filtro') is not None else '')),
        'analisis':     analizar_fn(d) if analizar_fn else '',
    }


# ── Tabla personalizada con Canvas ────────────────────────────────────────────

class DecisionTable(tk.Frame):
    """Widget de tabla con Canvas: bolitas, separadores, scroll."""

    def __init__(self, parent, analizar_fn=None, **kw):
        super().__init__(parent, bg=C['bg'], **kw)
        self._analizar_fn = analizar_fn

        # Ajustar anchos mínimos según el texto real del encabezado
        import tkinter.font as tkfont
        fnt = tkfont.Font(family='Consolas', size=10, weight='bold')
        HDR_PAD = 20
        self._fnt_hdr = fnt
        self._hdr_pad = HDR_PAD
        self._extra_cols = []   # columnas dinámicas (ΔFiltro, Saldo) cuando filtro activo
        self._col_defs = [
            (key, label, max(col_w, fnt.measure(label) + HDR_PAD if label else col_w), align)
            for key, label, col_w, align in COL_DEFS
        ]
        self.TOTAL_W = sum(c[2] for c in self._col_defs)

        # Canvas principal
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

        # Scroll con rueda
        self._cv.bind('<MouseWheel>', self._on_scroll)
        self._cv.bind('<Button-4>',   self._on_scroll)
        self._cv.bind('<Button-5>',   self._on_scroll)

        self._dibujar_header()

    def _on_scroll(self, ev):
        delta = -1 if (getattr(ev, 'num', 0) == 5 or getattr(ev, 'delta', 0) < 0) else 1
        self._cv.yview_scroll(delta, 'units')

    # ── Header ──

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
            # Línea separadora vertical
            self._cv.create_line(x + col_w, 0, x + col_w, HDR_H,
                                  fill=SEP_CLR, width=1, tags='header')
            x += col_w
        # Borde inferior del header
        self._cv.create_line(0, HDR_H, self.TOTAL_W, HDR_H,
                              fill='#00D4FF', width=1, tags='header')

    # ── Datos ──

    def actualizar_anchos(self, widths_dict):
        """Recibe {key: ancho} y reconstruye col_defs + redibuja."""
        import tkinter.font as tkfont
        fnt = tkfont.Font(family='Consolas', size=10, weight='bold')
        HDR_PAD = 20
        base = [c for c in COL_DEFS if c[0] != 'analisis'] + getattr(self, '_extra_cols', []) + [COL_DEFS[-1]]
        self._col_defs = [
            (key, label,
             max(widths_dict.get(key, col_w),
                 fnt.measure(label) + HDR_PAD if label else 0),
             align)
            for key, label, col_w, align in base
        ]
        self.TOTAL_W = sum(c[2] for c in self._col_defs)
        self._cv.configure(width=self.TOTAL_W)
        self._dibujar_header()

    def set_filtro_columnas(self, filtro_idx):
        """Activa/desactiva columnas extra ΔFiltro y Saldo según haya filtro seleccionado.

        filtro_idx: índice del filtro (1-N) o None para ocultar las columnas extra.
        """
        if filtro_idx is None:
            self._extra_cols = []
        else:
            self._extra_cols = [
                ('_delta_f', 'ΔFiltro',  95, 'c'),
                ('_saldo_f', 'Saldo',   105, 'c'),
            ]
        # Reconstruir _col_defs con las columnas base + extras
        base = [c for c in COL_DEFS if c[0] != 'analisis'] + self._extra_cols + [COL_DEFS[-1]]
        self._col_defs = [
            (key, label,
             max(col_w, self._fnt_hdr.measure(label) + self._hdr_pad if label else col_w),
             align)
            for key, label, col_w, align in base
        ]
        self.TOTAL_W = sum(c[2] for c in self._col_defs)
        self._cv.configure(width=self.TOTAL_W)
        self._dibujar_header()

    def set_data(self, decisiones):
        self._cv.delete('row')
        n = len(decisiones)
        total_h = HDR_H + n * ROW_H + 4

        _issue_parity = {}
        for _d in decisiones:
            _iss = _d.get('issue')
            if _iss not in _issue_parity:
                _issue_parity[_iss] = len(_issue_parity)
        for row_idx, d in enumerate(reversed(decisiones)):
            y   = HDR_H + row_idx * ROW_H
            bg  = _row_bg(d, _issue_parity.get(d.get('issue'), row_idx))
            fg  = _row_fg(d)
            dot = _dot_color(d)
            vals = _row_values(d, self._analizar_fn)

            # Fondo de fila
            self._cv.create_rectangle(0, y, self.TOTAL_W, y + ROW_H,
                                       fill=bg, outline='', tags='row')

            # Línea separadora horizontal
            self._cv.create_line(0, y + ROW_H, self.TOTAL_W, y + ROW_H,
                                  fill=SEP_CLR, width=1, tags='row')

            # Celdas
            x = 0
            for key, label, col_w, align in self._col_defs:
                cy = y + ROW_H // 2

                if key == '_dot':
                    # Bolita de estado (verde/rojo/magenta/amarillo/gris)
                    cx = x + col_w // 2
                    self._cv.create_oval(cx - DOT_R, cy - DOT_R,
                                          cx + DOT_R, cy + DOT_R,
                                          fill=dot, outline=dot, tags='row')

                elif key in ('_delta_f', '_saldo_f'):
                    # Columnas dinámicas por filtro seleccionado.
                    if key == '_delta_f':
                        v = d.get('_delta_filtro')
                    else:
                        v = d.get('_saldo_filtro')
                    if v is None or v == 0:
                        txt = '—' if v is None else '0.00'
                        col_v = C['muted']
                    else:
                        txt = f"{v:+.2f}"
                        col_v = C['accent2'] if v > 0 else C['accent3']
                    self._cv.create_text(x + col_w // 2, cy, text=txt,
                                          fill=col_v, font=FONT_ROWB,
                                          anchor='center', tags='row')

                elif align == 'br':
                    # Bolita azul/rojo para columnas Color y Ganador
                    val = vals.get(key, '')
                    br = _br_color(val)
                    if br:
                        cx = x + col_w // 2
                        self._cv.create_oval(cx - DOT_R, cy - DOT_R,
                                              cx + DOT_R, cy + DOT_R,
                                              fill=br, outline=br, tags='row')

                else:
                    val = _truncar(vals.get(key, ''), col_w)
                    if align == 'c':
                        tx = x + col_w // 2
                        anc = 'center'
                    else:
                        tx = x + 10
                        anc = 'w'
                    fnt = FONT_ROWB if (key == 'modo' and val == 'SKIP') else FONT_ROW
                    self._cv.create_text(tx, cy, text=val,
                                          fill=fg, font=fnt,
                                          anchor=anc, tags='row')

                # Línea separadora vertical
                self._cv.create_line(x + col_w, y, x + col_w, y + ROW_H,
                                      fill=SEP_CLR, width=1, tags='row')
                x += col_w

        self._cv.configure(scrollregion=(0, 0, self.TOTAL_W, total_h))
        # Scroll al inicio (fila más reciente)
        self._cv.yview_moveto(0)


# ── Ventana principal ─────────────────────────────────────────────────────────

def cargar_decisiones():
    try:
        return json.loads(DECISION_HIST_FILE.read_text(encoding='utf-8'))
    except Exception:
        return []


def cargar_decisiones_long():
    """Lee pnl_filtros_long.jsonl → lista de filas (1 por filtro × ronda)."""
    filas = []
    if not FILTROS_LONG_FILE.exists():
        return filas
    try:
        with FILTROS_LONG_FILE.open('r', encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    filas.append(json.loads(linea))
                except Exception:
                    continue
    except Exception:
        pass
    return filas


def guardar_decisiones(decisiones):
    try:
        DECISION_HIST_FILE.write_text(
            json.dumps(decisiones, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass


class DecisionHistoryWindow(tk.Toplevel):
    """Ventana independiente con histórico de decisiones. Singleton."""

    _instancia = None

    def __init__(self, master, get_decisiones, on_clear=None, get_filtro_nombre=None,
                 get_pnl_ep_umbral=None):
        super().__init__(master)
        self._get_decisiones = get_decisiones
        self._on_clear = on_clear
        self._get_filtro_nombre = get_filtro_nombre
        self._get_pnl_ep_umbral = get_pnl_ep_umbral
        self._geom_after_id = None
        DecisionHistoryWindow._instancia = self

        # Estado del desplegable de filtro
        self._cat_sel = tk.StringVar(value='TODOS')   # TODOS / BASE / FILTROS
        self._sub_sel = tk.StringVar(value='')        # "1: Solo DIRECTO", etc.
        self._om_sub  = None                          # referencia al 2º OptionMenu
        self._user_overrode_filter = False            # evita que REFRESCAR sobrescriba el filtro manual

        self.title("PNL — HISTÓRICO DE DECISIONES")
        self.configure(bg=C['bg'])
        self._restaurar_geometria()
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.bind('<Configure>', self._on_configure)
        self._construir_ui()
        # Sincronizar con el filtro activo del dashboard ya en la primera apertura
        try:
            self._refrescar_con_sync_dashboard()
        except Exception:
            self.refrescar()
        # Cargar anchos de columna desde Sheets en background
        import threading
        def _cargar_anchos(win=self):
            try:
                from configurador import conectar_excel
                ws = conectar_excel().worksheet("COLUMNAS")
                filas = ws.get_all_values()
                widths = {}
                for fila in filas:
                    if (len(fila) >= 3
                            and str(fila[0]).strip().upper() == 'HISTORICO'
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
    def abrir_o_focus(cls, master, get_decisiones, on_clear=None, get_filtro_nombre=None,
                       get_pnl_ep_umbral=None):
        if cls._instancia and cls._instancia.winfo_exists():
            cls._instancia.lift()
            cls._instancia.focus_force()
            cls._instancia._refrescar_con_sync_dashboard()
            return cls._instancia
        return cls(master, get_decisiones, on_clear=on_clear,
                   get_filtro_nombre=get_filtro_nombre,
                   get_pnl_ep_umbral=get_pnl_ep_umbral)

    # ── Geometría ──

    def _restaurar_geometria(self):
        try:
            cfg = json.loads(DECISION_GEOM_FILE.read_text(encoding='utf-8'))
            x, y = int(cfg['x']), int(cfg['y'])
            self.geometry(f"{cfg['w']}x{cfg['h']}+{x}+{y}")
        except Exception:
            self.geometry("2300x700")

    def _on_configure(self, event=None):
        """Guardar geometría debounced: 600 ms después del último evento Configure."""
        if self._geom_after_id:
            try:
                self.after_cancel(self._geom_after_id)
            except Exception:
                pass
        self._geom_after_id = self.after(600, self._guardar_geometria)

    def _guardar_geometria(self):
        import re
        self._geom_after_id = None
        try:
            geo = self.geometry()
            m = re.match(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)', geo)
            if m:
                w, h, x, y = m.groups()
                DECISION_GEOM_FILE.write_text(
                    json.dumps({'w': int(w), 'h': int(h),
                                'x': int(x), 'y': int(y)}),
                    encoding='utf-8')
        except Exception:
            pass

    def _cerrar(self):
        self._guardar_geometria()
        DecisionHistoryWindow._instancia = None
        self.destroy()

    # ── UI ──

    @staticmethod
    def _crear_tooltip(widget, texto):
        """Crea un tooltip al pasar el ratón sobre un widget."""
        tooltip = None
        def _enter(e):
            nonlocal tooltip
            x = e.widget.winfo_rootx() + 20
            y = e.widget.winfo_rooty() + e.widget.winfo_height() + 4
            tooltip = tk.Toplevel(e.widget)
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{x}+{y}")
            lbl = tk.Label(tooltip, text=texto, font=('Consolas', 9),
                           bg='#0A1628', fg='#C8D8E8', relief='solid', bd=1,
                           padx=6, pady=3)
            lbl.pack()
        def _leave(e):
            nonlocal tooltip
            if tooltip:
                tooltip.destroy()
                tooltip = None
        widget.bind('<Enter>', _enter)
        widget.bind('<Leave>', _leave)

    @staticmethod
    def _hablar(texto):
        """Habla un texto, importando hablar localmente para evitar ciclo."""
        from pnl_panels import hablar
        hablar(texto)

    def _refrescar_con_sync_dashboard(self):
        """Lee el filtro activo del dashboard, lo aplica al desplegable y refresca."""
        if self._user_overrode_filter:
            self.refrescar()
            return
        nombre_filtro = self._get_filtro_nombre() if self._get_filtro_nombre else ""
        _nombre_base = FILTROS_CURVA[0][0]  # "Base (todo)"

        if nombre_filtro == _nombre_base:
            self._cat_sel.set('BASE')
            self._om_sub.pack_forget()
            voz = "Base"
        elif nombre_filtro:
            try:
                idx = next(i for i, e in enumerate(FILTROS_CURVA)
                          if e[0] == nombre_filtro)
                self._sub_sel.set(f"{idx}: {nombre_filtro}")
                self._cat_sel.set('FILTROS')
                self._om_sub.pack(side='left', padx=(4, 0))
                voz = f"{idx} {nombre_filtro}"
            except StopIteration:
                self._cat_sel.set('TODOS')
                self._om_sub.pack_forget()
                voz = "Todos"
        else:
            self._cat_sel.set('TODOS')
            self._om_sub.pack_forget()
            voz = "Todos"

        self.refrescar()
        self._hablar(voz)

    def _construir_ui(self):
        # Header
        hf = tk.Frame(self, bg='#020810', height=52)
        hf.pack(fill='x')
        hf.pack_propagate(False)
        tk.Frame(hf, bg=C['accent'], height=2).pack(fill='x', side='top')
        inner = tk.Frame(hf, bg='#020810')
        inner.pack(fill='both', expand=True, padx=12)

        tk.Label(inner, text="HISTÓRICO DE DECISIONES PRE-APUESTA",
                 font=FONT_TITLE, bg='#020810', fg=C['accent']).pack(side='left', pady=10)

        self._lbl_count = tk.Label(inner, text="", font=FONT_SM,
                                    bg='#020810', fg=C['muted'])
        self._lbl_count.pack(side='right', padx=12)

        _tooltip_txt = "Refresca la tabla y sincroniza la selección de filtro con el dashboard principal"
        self._btn_refrescar = tk.Button(inner, text="REFRESCAR", font=('Consolas', 10, 'bold'),
                  bg=C['border'], fg=C['accent2'], relief='flat', cursor='hand2',
                  padx=10, command=self._refrescar_con_sync_dashboard)
        self._btn_refrescar.pack(side='right', padx=3)
        self._crear_tooltip(self._btn_refrescar, _tooltip_txt)
        tk.Button(inner, text="🔊", font=('Consolas', 11),
                  bg=C['border'], fg=C['accent'], relief='flat', cursor='hand2',
                  padx=4, command=lambda txt=_tooltip_txt: self._hablar(txt)).pack(side='right', padx=1)
        tk.Button(inner, text="LIMPIAR", font=('Consolas', 10, 'bold'),
                  bg='#2A0A0A', fg=C['accent3'], relief='flat', cursor='hand2',
                  padx=10, command=self._limpiar).pack(side='right', padx=3)

        # ── Desplegable de filtro ──────────────────────────────────────────────
        # Subfiltros: TODOS + cada filtro excepto Base (idx 0) y BAL.FILTRO
        _subfiltros = ['TODOS'] + [
            f"{i}: {entry[0]}"
            for i, entry in enumerate(FILTROS_CURVA)
            if i > 0 and entry[2] != 'BAL_FILTRO'
        ]
        self._sub_sel.set('TODOS')

        _ver_frame = tk.Frame(inner, bg='#020810')
        _ver_frame.pack(side='right', padx=(4, 8))

        tk.Label(_ver_frame, text="VER:", font=('Consolas', 10, 'bold'),
                 bg='#020810', fg=C['muted']).pack(side='left', padx=(0, 2))

        _om_cat = tk.OptionMenu(_ver_frame, self._cat_sel,
                                'TODOS', 'BASE', 'FILTROS',
                                command=self._on_cat_change)
        _om_cat.config(bg='#0D2137', fg=C['accent'], font=('Consolas', 10),
                       relief='flat', highlightthickness=0, bd=0,
                       activebackground='#1A3050', activeforeground=C['accent2'])
        _om_cat['menu'].config(bg='#050A14', fg=C['accent2'],
                               activebackground='#1A3050',
                               activeforeground=C['accent'],
                               font=('Consolas', 10))
        _om_cat.pack(side='left')

        self._om_sub = tk.OptionMenu(_ver_frame, self._sub_sel,
                                     *(_subfiltros or ['---']),
                                     command=lambda _: self._on_sub_change())
        self._om_sub.config(bg='#0D2137', fg=C['accent2'], font=('Consolas', 10),
                            relief='flat', highlightthickness=0, bd=0,
                            activebackground='#1A3050', activeforeground=C['accent'])
        self._om_sub['menu'].config(bg='#050A14', fg=C['accent2'],
                                    activebackground='#1A3050',
                                    activeforeground=C['accent'],
                                    font=('Consolas', 10))
        # No hacer pack hasta que se seleccione 'FILTROS'

        # Leyenda
        leyenda = tk.Frame(self, bg='#060F1E')
        leyenda.pack(fill='x', padx=0)
        tk.Frame(leyenda, bg='#0D2137', height=1).pack(fill='x')
        leg_inner = tk.Frame(leyenda, bg='#060F1E')
        leg_inner.pack(fill='x', padx=14, pady=5)

        items = [
            ('#00FF88', 'APOSTADA ✓'),
            ('#FF3366', 'APOSTADA ✗'),
            ('#FF00FF', 'INVERSO ✓'),
            ('#FF44AA', 'INVERSO ✗'),
            ('#FFB800', 'OBS (observando)'),
            ('#3A5070', 'SKIP'),
            ('#FFD700', 'Pendiente resultado'),
        ]
        for color, texto in items:
            f = tk.Frame(leg_inner, bg='#060F1E')
            f.pack(side='left', padx=(0, 18))
            c = tk.Canvas(f, width=14, height=14, bg='#060F1E',
                          highlightthickness=0)
            c.pack(side='left', pady=1)
            c.create_oval(2, 2, 12, 12, fill=color, outline=color)
            tk.Label(f, text=texto, font=('Consolas', 10),
                     bg='#060F1E', fg='#C8D8E8').pack(side='left', padx=(3, 0))

        tk.Frame(self, bg='#0D2137', height=1).pack(fill='x')

        # ── Balances: Real  |  sep  |  Filtro activo ──
        bal_frame = tk.Frame(self, bg='#060F1E')
        bal_frame.pack(fill='x', padx=0)

        # Balance real
        self._bal_real_frame = tk.Frame(bal_frame, bg='#060F1E')
        self._bal_real_frame.pack(side='left', padx=18, pady=6)
        tk.Label(self._bal_real_frame, text="BALANCE REAL",
                 font=('Consolas', 11, 'bold'), bg='#060F1E',
                 fg=C['muted']).pack(side='left', padx=(0, 12))
        self._lbl_balance_real = tk.Label(self._bal_real_frame, text="+0.00 €",
                 font=('Consolas', 22, 'bold'), bg='#060F1E', fg=C['accent2'])
        self._lbl_balance_real.pack(side='left')

        # Separador vertical
        self._bal_sep = tk.Frame(bal_frame, bg='#1A3050', width=1)
        self._bal_sep.pack(side='left', fill='y', pady=4)

        # Balance filtro activo
        self._bal_filtro_frame = tk.Frame(bal_frame, bg='#060F1E')
        self._bal_filtro_frame.pack(side='left', padx=18, pady=6)
        tk.Label(self._bal_filtro_frame, text="BALANCE FILTRO ACTIVO",
                 font=('Consolas', 11, 'bold'), bg='#060F1E',
                 fg=C['muted']).pack(side='left', padx=(0, 12))
        self._lbl_filtro_nombre = tk.Label(self._bal_filtro_frame, text="---",
                 font=('Consolas', 12, 'bold'), bg='#060F1E', fg=C['accent'])
        self._lbl_filtro_nombre.pack(side='left', padx=(0, 16))
        self._lbl_balance_filtro = tk.Label(self._bal_filtro_frame, text="+0.00 €",
                 font=('Consolas', 22, 'bold'), bg='#060F1E', fg=C['accent2'])
        self._lbl_balance_filtro.pack(side='left')
        tk.Frame(self, bg='#0D2137', height=1).pack(fill='x')

        # Tabla canvas
        self._tabla = DecisionTable(self, analizar_fn=self._analizar_ronda)
        self._tabla.pack(fill='both', expand=True, padx=0, pady=0)

    # ── Filtrado ──

    def _on_cat_change(self, cat):
        """Muestra/oculta el 2º desplegable según la categoría elegida."""
        self._user_overrode_filter = True
        if cat == 'FILTROS':
            self._om_sub.pack(side='left', padx=(4, 0))
        else:
            self._om_sub.pack_forget()
        self.refrescar()

    def _on_sub_change(self):
        self._user_overrode_filter = True
        self.refrescar()

    @staticmethod
    def _pf_val(d, idx):
        """Lee pnl_filtros[idx] tolerando claves enteras (memoria) y string (JSON)."""
        pf = d.get('pnl_filtros') or {}
        # JSON guarda las claves como strings; en memoria son enteros
        v = pf.get(str(idx))
        if v is None:
            v = pf.get(idx)
        return v

    @staticmethod
    def _analizar_ronda(d):
        """Explica por qué el filtro de esta fila apostó o no."""
        idx = d.get('filtro_idx')
        try:
            entry = FILTROS_CURVA[idx]
            nombre_f, _, filtro_fn, contrarian, raw = entry
        except Exception:
            return ''

        wr    = float(d.get('wr') or 50)
        est   = d.get('est', '')
        modo  = d.get('modo', 'SKIP')
        acel  = float(d.get('acel') or 0)
        rango = d.get('rango', '')
        decision = d.get('decision', '')
        delta    = float(d.get('delta') or 0)
        color_ap = d.get('color_apostado') or ''

        # ── Resolver modo BASE a modo teórico ──────────────────────
        if modo == 'BASE':
            modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')

        # ── Filtros especiales ──────────────────────────────────────
        if filtro_fn is None:          # EP ADAPTATIVO
            return '—'
        if isinstance(filtro_fn, str):
            if filtro_fn == 'EP_UMBRAL':
                if delta != 0:
                    return f"UMBRAL {color_ap} {delta:+.2f}{'✓' if delta>0 else '✗'}"
                return 'UMBRAL sin señal'
            if filtro_fn == 'EP_WR70':
                if delta != 0:
                    c = 'inv' if contrarian else ''
                    return f"EP70{c} {color_ap} {delta:+.2f}"
                return 'EP70 sin señal'
            if filtro_fn == 'BAL_FILTRO':
                return '—'
            return ''

        # ── Condiciones evaluadas ──────────────────────────────────
        fallan = []

        # raw=True (Base): no tiene condiciones, apuesta siempre
        if raw:
            if decision == 'APOSTADA':
                _marca = '✓' if delta > 0 else ('✗' if delta < 0 else '·')
                _delta_s = f"{delta:+.2f}" if delta != 0 else '0.00'
                return f"siempre mayoría→{color_ap} {_delta_s}{_marca}"
            return f"siempre mayoría | sin apuesta ({decision})"

        if modo == 'SKIP':
            fallan.append(f"WR{wr:.0f}→SKIP")
        else:
            if 'WR>=' in nombre_f.upper():
                import re
                m = re.search(r'WR>=(\d+)', nombre_f.upper())
                if m:
                    umbral = int(m.group(1))
                    if wr < umbral:
                        fallan.append(f"WR{wr:.0f}<{umbral}")
            if 'SIN +50' in nombre_f.upper() or 'SIN+50' in nombre_f.upper():
                if rango == '+50':
                    fallan.append("rango=+50❌")
            if 'ESTABLE' in nombre_f.upper() and est != 'ESTABLE':
                fallan.append("VOL❌")
            if 'VOLATIL' in nombre_f.upper() and est != 'VOLATIL':
                fallan.append("EST❌")
            if '|ACEL|<10' in nombre_f.upper():
                if abs(acel) >= 10:
                    fallan.append(f"|acel|={abs(acel):.0f}≥10❌")
            if '(todo)' not in nombre_f and 'CONTRA' not in nombre_f.upper() and 'MAYOR' not in nombre_f.upper():
                if 'DIRECTO' in nombre_f.upper() and modo != 'DIRECTO':
                    fallan.append(f"modo≠DIR")
                if 'INVERSO' in nombre_f.upper() and modo != 'INVERSO':
                    fallan.append(f"modo≠INV")
            if 'MAYORÍA PERDEDORA' in nombre_f.upper() and wr >= 40:
                fallan.append(f"WR{wr:.0f}≥40❌")

        if fallan:
            return ' | '.join(fallan)

        # ── Filtro pasó condiciones, ver si realmente apostó ──────
        if decision != 'APOSTADA':
            return f"WR{wr:.0f}→{modo} | {est}✅ | sin apuesta ({decision})"

        direccion = 'mayoría' if not raw and 'INVERSO' not in nombre_f.upper() and modo != 'INVERSO' else 'minoría'
        if raw:
            direccion = 'siempre'
        if contrarian:
            direccion = 'contra'
        if 'MAYORÍA PERDEDORA' in nombre_f.upper():
            direccion = 'contra'

        _marca  = '✓' if delta > 0 else ('✗' if delta < 0 else '·')
        _delta_s = f"{delta:+.2f}" if delta != 0 else '0.00'
        return f"WR{wr:.0f}→{modo} | {est}✅ | {direccion}→{color_ap} {_delta_s}{_marca}"

    def _aplicar_filtro(self, decisiones):
        """Filtra las filas long por filtro_idx según el selector VER."""
        cat = self._cat_sel.get()
        if cat == 'TODOS':
            return decisiones
        if cat == 'BASE':
            return [d for d in decisiones if d.get('filtro_idx') == 0]
        sub = self._sub_sel.get()
        if not sub or sub == 'TODOS':
            return [d for d in decisiones
                    if d.get('filtro_idx') is not None
                    and d.get('filtro_idx') != 0]
        try:
            idx = int(sub.split(':')[0])
        except Exception:
            return decisiones
        return [d for d in decisiones if d.get('filtro_idx') == idx]

    # ── Datos ──

    def _ocultar_balance_irrelevante(self, filas):
        """Devuelve copias de las filas con el balance no relevante puesto a None."""
        cat = self._cat_sel.get()
        if cat == 'TODOS':
            return filas
        campo_oculto = 'balance_filtro' if cat == 'BASE' else 'balance_real'
        resultado = []
        for d in filas:
            d2 = dict(d)
            d2[campo_oculto] = None
            resultado.append(d2)
        return resultado

    def refrescar(self):
        # Datos long (1 fila por filtro × ronda) para la tabla
        decisiones_long = cargar_decisiones_long()
        self._ultima_lista = decisiones_long
        filtradas = self._aplicar_filtro(decisiones_long)

        # Las filas long ya traen 'delta' y 'saldo' → mapear a las columnas extra
        filas_render = []
        for d in filtradas:
            d2 = dict(d)
            d2['_delta_filtro'] = d.get('delta')
            d2['_saldo_filtro'] = d.get('saldo')
            filas_render.append(d2)

        # Mostrar siempre las columnas extra ΔFiltro/Saldo (en long cada fila ya es per-filtro)
        self._tabla.set_filtro_columnas(0)
        self._tabla.set_data(filas_render)

        # Conteo: nº rondas únicas y nº filas
        n_rondas_tot = len({d.get('issue') for d in decisiones_long if d.get('issue')})
        n_rondas_vis = len({d.get('issue') for d in filtradas if d.get('issue')})
        if len(filtradas) == len(decisiones_long):
            self._lbl_count.config(
                text=f"{len(decisiones_long)} filas · {n_rondas_tot} rondas")
        else:
            self._lbl_count.config(
                text=f"{len(filtradas)} / {len(decisiones_long)} filas · "
                     f"{n_rondas_vis}/{n_rondas_tot} rondas")
        # Balance: se calcula sobre el wide-format para preservar la semántica original
        decisiones_wide = self._get_decisiones()
        self._actualizar_balance(decisiones_wide,
                                  self._aplicar_filtro_wide(decisiones_wide))

    def _aplicar_filtro_wide(self, decisiones):
        """Filtra el wide-format por categoría VER (compatibilidad con _actualizar_balance)."""
        cat = self._cat_sel.get()
        if cat == 'TODOS':
            return decisiones
        if cat == 'BASE':
            _nombre_base = FILTROS_CURVA[0][0]
            return [d for d in decisiones if d.get('filtro', '') == _nombre_base]
        sub = self._sub_sel.get()
        if not sub or sub == 'TODOS':
            _nombre_base = FILTROS_CURVA[0][0]
            return [d for d in decisiones if d.get('filtro', '') != _nombre_base]
        try:
            idx = int(sub.split(':')[0])
            _nombre_filtro = FILTROS_CURVA[idx][0]
        except Exception:
            return decisiones
        return [d for d in decisiones if d.get('filtro', '') == _nombre_filtro]

    def _filtro_idx_seleccionado(self):
        """Devuelve el idx del filtro elegido en sub-dropdown, o None.
        - cat=='FILTROS' y sub_sel parseable → idx
        - cat=='BASE' → 0 (filtro base)
        - resto → None
        """
        cat = self._cat_sel.get()
        if cat == 'BASE':
            return 0
        if cat != 'FILTROS':
            return None
        sub = self._sub_sel.get()
        if not sub or sub == 'TODOS':
            return None
        try:
            return int(sub.split(':')[0])
        except Exception:
            return None

    def aplicar_anchos(self, widths_dict):
        """Actualiza anchos de columna y redibuja la tabla."""
        self._tabla.actualizar_anchos(widths_dict)
        decisiones = getattr(self, '_ultima_lista', None) or cargar_decisiones_long()
        filtradas = self._aplicar_filtro(decisiones)
        filas_render = []
        for d in filtradas:
            d2 = dict(d)
            d2['_delta_filtro'] = d.get('delta')
            d2['_saldo_filtro'] = d.get('saldo')
            filas_render.append(d2)
        self._tabla.set_data(filas_render)

    def _actualizar_balance(self, decisiones, filtradas=None):
        cat = self._cat_sel.get()
        filtro_activo = cat != 'TODOS'

        # Cargar long file (fuente de verdad para saldos por filtro)
        long_filas = getattr(self, '_ultima_lista', None) or cargar_decisiones_long()

        # ── Balance real ──────────────────────────────────────────────────────
        # Último balance_real conocido (no depende del filtro seleccionado)
        real = None
        for d in reversed(long_filas):
            if d.get('balance_real') is not None:
                real = d['balance_real']
                break
        if real is None:
            for d in reversed(decisiones or []):
                if d.get('balance_real') is not None:
                    real = d['balance_real']
                    break
        col_r = C['accent2'] if (real or 0) >= 0 else C['accent3']
        self._lbl_balance_real.config(
            text=f"{real:+.2f} €" if real is not None else "+0.00 €",
            fg=col_r)

        # ── Balance filtro activo ─────────────────────────────────────────────
        # Usar el último 'saldo' de la línea long correspondiente al filtro elegido.
        if filtro_activo:
            if cat == 'BASE':
                idx = 0
            else:
                sub = self._sub_sel.get()
                if not sub or sub == 'TODOS':
                    idx = None
                else:
                    try:
                        idx = int(sub.split(':')[0])
                    except Exception:
                        idx = None
            if idx is not None:
                bal = None
                for r in reversed(long_filas):
                    if r.get('filtro_idx') == idx:
                        bal = r.get('saldo')
                        break
                if bal is None:
                    bal = 0.0
            else:
                # FILTROS:TODOS → suma de saldos finales de cada filtro != 0
                ultimos = {}
                for r in long_filas:
                    fi = r.get('filtro_idx')
                    if fi is not None and fi != 0:
                        ultimos[fi] = r.get('saldo') or 0.0
                bal = round(sum(ultimos.values()), 2)
        else:
            bal = None
            for r in reversed(long_filas):
                if r.get('filtro_idx') == 0 and r.get('saldo') is not None:
                    bal = r.get('saldo')
                    break

        # Nombre del filtro activo
        if filtro_activo:
            if cat == 'BASE':
                nombre = FILTROS_CURVA[0][0]
            else:
                sub = self._sub_sel.get()
                if sub == 'TODOS':
                    nombre = 'Todos los filtros'
                else:
                    try:
                        idx = int(sub.split(':')[0])
                        nombre = FILTROS_CURVA[idx][0]
                    except Exception:
                        nombre = '---'
        elif self._get_filtro_nombre:
            try:
                nombre = self._get_filtro_nombre()
            except Exception:
                nombre = '---'
        else:
            nombre = '---'
            for d in reversed(lista_bal):
                if d.get('filtro_nombre'):
                    nombre = d['filtro_nombre']
                    break

        if bal is None:
            self._lbl_balance_filtro.config(text="+0.00 €", fg=C['accent2'])
        else:
            col = C['accent2'] if bal >= 0 else C['accent3']
            self._lbl_balance_filtro.config(text=f"{bal:+.2f} €", fg=col)
        self._lbl_filtro_nombre.config(text=nombre)

        # ── Visibilidad: BASE → solo real | FILTROS → solo filtro | TODOS → ambos ──
        if cat == 'BASE':
            self._bal_real_frame.pack(side='left', padx=18, pady=6)
            self._bal_sep.pack_forget()
            self._bal_filtro_frame.pack_forget()
        elif cat == 'FILTROS':
            self._bal_real_frame.pack_forget()
            self._bal_sep.pack_forget()
            self._bal_filtro_frame.pack(side='left', padx=18, pady=6)
        else:  # TODOS
            self._bal_real_frame.pack(side='left', padx=18, pady=6)
            self._bal_sep.pack(side='left', fill='y', pady=4)
            self._bal_filtro_frame.pack(side='left', padx=18, pady=6)

    def _limpiar(self):
        cat = self._cat_sel.get()
        sub = self._sub_sel.get()

        # Determinar qué borrar y mensaje de confirmación.
        # Combinar wide (memoria + disco) y deducir rondas presentes también en long
        # para que el contador refleje lo que realmente se ve en la tabla.
        decisiones = list(self._get_decisiones() or [])
        if not decisiones:
            try:
                decisiones = list(cargar_decisiones() or [])
            except Exception:
                decisiones = []

        if cat == 'TODOS':
            filtro_nombre = "TODO el histórico"
            criterio = lambda d: True
        elif cat == 'BASE':
            filtro_nombre = "filtro BASE"
            criterio = lambda d: (d.get('modo') or '').upper() == 'BASE'
        else:  # FILTROS
            if sub == 'TODOS' or not sub:
                filtro_nombre = "todos los FILTROS"
                criterio = lambda d: (d.get('modo') or '').upper() != 'BASE'
            else:
                try:
                    idx = int(sub.split(':')[0])
                    nombre = FILTROS_CURVA[idx][0]
                except Exception:
                    nombre = sub
                filtro_nombre = f"filtro «{nombre}»"
                criterio = lambda d, _n=nombre: d.get('filtro') == _n

        n_match = sum(1 for d in decisiones if criterio(d))
        # Si el wide está vacío o no matchea, mirar el long (la tabla se alimenta de ahí)
        long_filas_cache = cargar_decisiones_long()
        n_match_long_rondas = len({r.get('issue') for r in long_filas_cache
                                    if criterio(r) and r.get('issue')})
        if n_match == 0 and n_match_long_rondas == 0:
            messagebox.showinfo("Sin coincidencias",
                                f"No hay decisiones que coincidan con {filtro_nombre}.",
                                parent=self)
            return
        if n_match == 0:
            n_match = n_match_long_rondas

        if not messagebox.askyesno(
                "Confirmar",
                f"¿Borrar {n_match} decisiones de {filtro_nombre}?\n\n"
                f"Total actual: {len(decisiones)} → quedarán {len(decisiones) - n_match}.\n"
                "Se creará una copia de seguridad antes del borrado.",
                parent=self):
            return

        # ── Copia de seguridad ──────────────────────────────────────
        import datetime as _dt
        backup_dir = DECISION_HIST_FILE.parent / 'backups'
        try:
            backup_dir.mkdir(exist_ok=True)
            ts = _dt.datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = backup_dir / f'pnl_decision_history_{ts}.json'
            backup_file.write_text(
                json.dumps(decisiones, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as exc:
            if not messagebox.askyesno(
                    "Error en backup",
                    f"No se pudo crear la copia de seguridad:\n{exc}\n\n"
                    "¿Continuar con el borrado igualmente?",
                    parent=self):
                return
            backup_file = None

        # Filtrar y guardar
        restantes = [d for d in decisiones if not criterio(d)]
        DECISION_HIST_FILE.write_text(
            json.dumps(restantes, ensure_ascii=False, indent=2), encoding='utf-8')

        # Replicar el borrado sobre pnl_filtros_long.jsonl (mismo criterio sobre
        # los campos de cabecera de cada línea: modo/filtro de la ronda original)
        try:
            long_filas = cargar_decisiones_long()
            if long_filas:
                # Backup long previo
                try:
                    long_bk = backup_dir / f'pnl_filtros_long_{ts}.jsonl'
                    with long_bk.open('w', encoding='utf-8') as _f:
                        for r in long_filas:
                            _f.write(json.dumps(r, ensure_ascii=False) + '\n')
                except Exception:
                    pass
                long_restantes = [r for r in long_filas if not criterio(r)]
                with FILTROS_LONG_FILE.open('w', encoding='utf-8') as _f:
                    for r in long_restantes:
                        _f.write(json.dumps(r, ensure_ascii=False) + '\n')
                # Reiniciar cache de saldos en PanelLive si está accesible (recargará desde disco)
                try:
                    pl = getattr(self.master, 'panel_live', None)
                    if pl is not None and hasattr(pl, '_saldo_filtros'):
                        pl._saldo_filtros = None
                except Exception:
                    pass
        except Exception as _exc:
            print(f"[LIMPIAR] Error filtrando long: {_exc}")

        # Si borramos todo, llamar al callback original
        if cat == 'TODOS' and self._on_clear:
            try:
                self._on_clear()
            except Exception:
                pass
        else:
            # Sincronizar con panel_live: actualizar su lista en memoria
            try:
                pl_dec = self._get_decisiones()
                if isinstance(pl_dec, list):
                    pl_dec[:] = restantes
            except Exception:
                pass

        self.refrescar()

        if backup_file is not None:
            messagebox.showinfo(
                "Borrado completado",
                f"Borradas {n_match} decisiones.\n\n"
                f"Copia de seguridad guardada en:\n{backup_file}",
                parent=self)
