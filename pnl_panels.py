"""
PNL DASHBOARD â€” Paneles de UI: PanelFiltros y PanelLive.
"""
import tkinter as tk
import subprocess
import json
import datetime
import threading
from pathlib import Path

from pnl_config import (C, FONT_MONO, FONT_MONO_B, FONT_BIG, FONT_SM, FONT_TITLE,
                         LIVE_HIST_FILE, FILTROS_CURVA, EP_UMBRAL_MIN,
                         FILTROS_LONG_FILE)
from pnl_data import curva_pnl, curva_pnl_ep, calcular_rango
from pnl_decision_panel import cargar_decisiones, guardar_decisiones
from telegram_notifier import send_message as _tg_send, _chat_ids as _tg_chat_ids, _bot_token as _tg_bot_token


def hablar(texto):
    from pnl_config import HABLAR_ACTIVADO
    if not HABLAR_ACTIVADO:
        return
    subprocess.Popen(
        ['powershell', '-WindowStyle', 'Hidden', '-Command',
         f'Add-Type -AssemblyName System.Speech; '
         f'$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
         f'$s.Rate = 1; $s.Speak("{texto}")'],
        creationflags=0x08000000
    )


class PanelFiltros(tk.Frame):
    """Columna de filtros: curvas en grafica, seleccion de filtro activo."""

    def __init__(self, parent, on_change, on_auto, ep_gate_var=None, on_filtro_select=None, get_ops=None):
        super().__init__(parent, bg=C['panel'], bd=1, relief='solid')
        self._on_change = on_change
        self._on_auto = on_auto
        self._ep_gate_var = ep_gate_var
        self._on_filtro_select = on_filtro_select   # callback(idx) al cambiar filtro activo
        self._get_ops = get_ops or (lambda: [])

        self._selected_filter = 0
        self._filtro_pnl_positivo = True

        self._filtro_vars = []
        self._filtro_btns = []
        self._filtro_info = []

        self._lbl_sel_name = None
        self._lbl_sel_pnl = None
        self._lbl_sel_detail = None

        self._proj_lbls       = {}   # base (compatibilidad)
        self._proj_lbls_base  = {}
        self._proj_lbls_filtro = {}

        self._base_inv_active = False
        self._btn_base_inv    = None
        self._pinned_filter = False

        self._construir()

    def _toggle_base_inv(self):
        self._base_inv_active = not self._base_inv_active
        if self._btn_base_inv:
            if self._base_inv_active:
                self._btn_base_inv.config(bg='#3A1A00', fg='#FF8800')
            else:
                self._btn_base_inv.config(bg='#1A1A2A', fg=C['muted'])

    def toggle_pin(self):
        self._pinned_filter = not self._pinned_filter
        if self._btn_pin:
            if self._pinned_filter:
                self._btn_pin.config(text='PIN', bg='#3A1A10', fg='#FFD700')
            else:
                self._btn_pin.config(text='PIN', bg=C['panel'], fg=C['warn'])

    # â”€â”€ Propiedades publicas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @property
    def selected_filter(self):
        return self._selected_filter

    @property
    def filtro_pnl_positivo(self):
        return self._filtro_pnl_positivo

    @property
    def filtro_vars(self):
        return self._filtro_vars

    # â”€â”€ Metodos publicos â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def toggle_curva(self, idx):
        var = self._filtro_vars[idx]
        var.set(not var.get())
        on = var.get()
        self._filtro_btns[idx].config(
            text="ON" if on else "OFF",
            bg='#1A3050' if on else C['border'],
            fg=C['accent2'] if on else C['muted'])
        if on:
            # Seleccionar este filtro como activo al activarlo
            self._selected_filter = idx
            if self._on_filtro_select:
                self._on_filtro_select(idx)
        self._on_change()

    def seleccionar_filtro(self, idx):
        """Selecciona filtro activo para resumen+rangos sin activarlo automÃ¡ticamente."""
        self._selected_filter = idx
        if self._on_filtro_select:
            self._on_filtro_select(idx)
        self._on_change()

    def seleccion_rapida(self, indices, seleccionado=None):
        for i, var in enumerate(self._filtro_vars):
            on = i in indices
            var.set(on)
            self._filtro_btns[i].config(
                text="ON" if on else "OFF",
                bg='#1A3050' if on else C['border'],
                fg=C['accent2'] if on else C['muted'])
        # seleccionado permite que el llamador fije el filtro activo (p.ej. mejor â‰  Base)
        if seleccionado is not None:
            self._selected_filter = seleccionado
        elif indices:
            self._selected_filter = indices[0]
        if self._on_filtro_select:
            self._on_filtro_select(self._selected_filter)
        self._on_change()

    def _simular_busqueda(self):
        """Muestra popup con ranking de filtros sin cambiar el activo."""
        ops = self._get_ops()
        if not ops:
            import tkinter.messagebox as mb
            mb.showinfo("SIMULAR", "Sin datos aÃºn. Espera al menos una ronda.")
            return
        ops_all = ops
        ops_v   = ops[-20:] if len(ops) >= 20 else ops
        MIN_OPS_VENTANA = 3
        ranking = []
        for i, entry_f in enumerate(FILTROS_CURVA):
            if i == 0:
                continue
            _, color, filtro, contrario = entry_f[:4]
            raw_f = entry_f[4] if len(entry_f) > 4 else False
            if filtro is None or isinstance(filtro, str):
                continue
            curva_tot, _, n_tot = curva_pnl(ops_all, filtro, contrarian=contrario, raw=raw_f)
            curva_rec, _, n_rec = curva_pnl(ops_v,   filtro, contrarian=contrario, raw=raw_f)
            if n_tot < MIN_OPS_VENTANA:
                continue
            pnl_tot = curva_tot[-1] if curva_tot else 0.0
            pnl_rec = curva_rec[-1] if curva_rec else 0.0
            ratio   = pnl_tot / n_tot if n_tot else 0.0
            ranking.append((i, FILTROS_CURVA[i][0], pnl_tot, pnl_rec, n_tot, ratio, color))
        ranking.sort(key=lambda r: r[2], reverse=True)

        # Ventana popup
        win = tk.Toplevel(self)
        win.title("SIMULACIÃ“N â€” MEJOR FILTRO")
        win.configure(bg='#050A14')
        win.resizable(False, False)
        win.attributes('-topmost', True)

        tk.Label(win, text=f"Total: {len(ops_all)} rondas  |  Reciente: {len(ops_v)}r  â€” ordenado por TOTAL",
                 font=('Consolas', 10, 'bold'), bg='#050A14', fg=C['warn']).pack(padx=16, pady=(10, 4))

        cols = ['#', 'FILTRO', 'TOTAL', '20r', 'OPS', 'PNL/op']
        hdr = tk.Frame(win, bg='#0D2137')
        hdr.pack(fill='x', padx=10)
        for c, w in zip(cols, [3, 22, 7, 7, 5, 7]):
            tk.Label(hdr, text=c, font=('Consolas', 9, 'bold'), bg='#0D2137',
                     fg=C['muted'], width=w, anchor='e').pack(side='left', padx=2)

        for pos, (idx, nombre, pnl_tot, pnl_rec, n_tot, ratio, color) in enumerate(ranking):
            bg = '#0A1628' if pos % 2 == 0 else '#050A14'
            col_tot = C['accent2'] if pnl_tot >= 0 else C['accent3']
            col_rec = C['accent2'] if pnl_rec >= 0 else C['accent3']
            fila = tk.Frame(win, bg=bg)
            fila.pack(fill='x', padx=10)
            activo = ' â—€' if idx == self._selected_filter else ''
            for txt, w, fg in [
                (str(idx),             3,  color),
                (nombre[:22]+activo,  22,  C['text']),
                (f"{pnl_tot:+.2f}",    7,  col_tot),
                (f"{pnl_rec:+.2f}",    7,  col_rec),
                (str(n_tot),           5,  C['muted']),
                (f"{ratio:+.3f}",      7,  col_tot),
            ]:
                tk.Label(fila, text=txt, font=('Consolas', 9), bg=bg,
                         fg=fg, width=w, anchor='e').pack(side='left', padx=2)

        tk.Button(win, text="CERRAR", font=('Consolas', 10),
                  bg='#0D2137', fg=C['muted'], relief='flat', cursor='hand2',
                  command=win.destroy).pack(pady=(8, 10))

    def seleccionar_mejor(self, ops, ventana=20, silencio_si_igual=False):
        import datetime as _dt
        _LOG_FILE = Path(__file__).parent / "auto_mejor_log.txt"
        def _log(msg):
            linea = f"{_dt.datetime.now().strftime('%H:%M:%S')} {msg}\n"
            print(linea, end='')
            with open(_LOG_FILE, 'a', encoding='utf-8') as _f:
                _f.write(linea)
        LOG = "[AUTO-MEJOR]"
        _log(f"{LOG} â”€â”€ INICIO â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")
        _log(f"{LOG} ops totales recibidas: {len(ops)}")
        _log(f"{LOG} silencio_si_igual: {silencio_si_igual}")

        if not ops:
            _log(f"{LOG} âŒ SALIDA: ops vacÃ­o")
            return

        ops_v = ops[-ventana:] if len(ops) >= ventana else ops
        _log(f"{LOG} ops en ventana ({ventana}): {len(ops_v)}  [guard reciente]")

        if len(ops_v) < 5:
            _log(f"{LOG} âŒ SALIDA: ops_v < 5 (insuficiente datos recientes)")
            return

        MIN_OPS_VENTANA = 3
        mejor_idx = None
        mejor_pnl = float('-inf')

        for i, entry_f in enumerate(FILTROS_CURVA):
            if i == 0:
                continue
            _, _, filtro, contrario = entry_f[:4]
            raw_f = entry_f[4] if len(entry_f) > 4 else False
            nombre_f = entry_f[0]

            if filtro is None or isinstance(filtro, str):
                _log(f"{LOG}   [{i:2}] {nombre_f[:20]:<20} → SKIP (filtro especial)")
                continue

            curva, _, n_bets = curva_pnl(ops, filtro, contrarian=contrario, raw=raw_f)
            pnl = curva[-1] if curva else float('-inf')

            if n_bets < MIN_OPS_VENTANA:
                _log(f"{LOG}   [{i:2}] {nombre_f[:20]:<20} → SKIP (n_bets={n_bets} < {MIN_OPS_VENTANA})")
                continue

            marca = " â—€ MEJOR" if pnl > mejor_pnl else ""
            _log(f"{LOG}   [{i:2}] {nombre_f[:20]:<20}  pnl={pnl:+.2f}  n_bets={n_bets}{marca}")

            if pnl > mejor_pnl:
                mejor_pnl = pnl
                mejor_idx = i

        if mejor_idx is None:
            _log(f"{LOG} âŒ SALIDA: ningÃºn filtro pasÃ³ el mÃ­nimo de {MIN_OPS_VENTANA} apuestas")
            return

        nombre = FILTROS_CURVA[mejor_idx][0]
        cambio = (mejor_idx != self._selected_filter)
        _log(f"{LOG} [*] MEJOR: [{mejor_idx}] {nombre}  pnl={mejor_pnl:+.2f}  cambio={cambio}")

        if silencio_si_igual and not cambio:
            _log(f"{LOG} â­ SALIDA: silencio_si_igual=True y filtro no cambia")
            return

        _log(f"{LOG} → seleccion_rapida([0, {mejor_idx}])")
        self.seleccion_rapida([0, mejor_idx], seleccionado=mejor_idx)
        hablar(f"{mejor_idx} {nombre}")
        _log(f"{LOG} â”€â”€ FIN â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")

    def actualizar_infos(self, ops):
        ops_v = ops[-20:] if len(ops) >= 20 else ops
        for i, entry_a in enumerate(FILTROS_CURVA):
            _, color, filtro, contrario = entry_a[:4]
            raw_a = entry_a[4] if len(entry_a) > 4 else False
            # PNL total
            if filtro is None:
                curva_t, _, n_t, _ = curva_pnl_ep(ops)
                curva_r, _, n_r, _ = curva_pnl_ep(ops_v)
            elif filtro == 'EP_WR70':
                curva_t, _, n_t, _ = curva_pnl_ep(ops, min_wr_dir=70)
                curva_r, _, n_r, _ = curva_pnl_ep(ops_v, min_wr_dir=70)
            elif isinstance(filtro, str):
                # Marcadores especiales (BAL_FILTRO, etc.): sin curva teÃ³rica desde ops
                curva_t, n_t = [], 0
                curva_r, n_r = [], 0
            else:
                curva_t, _, n_t = curva_pnl(ops, filtro, contrarian=contrario, raw=raw_a)
                curva_r, _, n_r = curva_pnl(ops_v, filtro, contrarian=contrario, raw=raw_a)
            pnl_t = curva_t[-1] if curva_t else 0
            pnl_r = curva_r[-1] if curva_r else 0
            ratio_r = pnl_r / n_r if n_r else 0.0
            # Color segÃºn PNL reciente (mÃ¡s relevante)
            col_i = C['accent2'] if pnl_r >= 0 else C['accent3']
            txt = f"{pnl_r:+.1f}/{n_r} {ratio_r:+.3f} Â· {pnl_t:+.1f}/{n_t}"
            self._filtro_info[i].config(text=txt, fg=col_i)

    def actualizar_sel(self, nombre, color, pnl, n_total, wr, ratio):
        col_bal = C['accent2'] if pnl >= 0 else C['accent3']
        self._lbl_sel_name.config(text=nombre, fg=color)
        self._lbl_sel_pnl.config(text=f"{pnl:+.2f} EUR", fg=col_bal)
        self._lbl_sel_detail.config(text=f"{n_total} ops | WR {wr:.1f}% | {ratio:+.3f}/op")

    # â”€â”€ Construccion interna â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _construir(self):
        _hdr = tk.Frame(self, bg=C['panel'])
        _hdr.pack(fill='x', pady=8, padx=10)
        tk.Label(_hdr, text="CURVAS EN GRAFICA", font=FONT_TITLE, bg=C['panel'],
                 fg=C['accent']).pack(side='left')
        self._btn_pin = tk.Button(_hdr, text='PIN', font=('Consolas', 11),
                                  bg=C['panel'], fg=C['warn'], relief='flat',
                                  cursor='hand2', padx=4, command=self.toggle_pin)
        self._btn_pin.pack(side='right')
        tk.Frame(self, bg=C['border'], height=1).pack(fill='x', padx=10, pady=(0, 4))

        for i, entry_c in enumerate(FILTROS_CURVA):
            nombre, color = entry_c[0], entry_c[1]
            row = tk.Frame(self, bg=C['panel'])
            row.pack(fill='x', padx=10, pady=1)

            var = tk.BooleanVar(value=(i == 0))
            self._filtro_vars.append(var)

            # Toggle button (curva on/off) â€” a la derecha
            btn = tk.Button(row, text="ON" if i == 0 else "OFF", font=('Consolas', 9, 'bold'),
                            width=3, bg='#1A3050' if i == 0 else C['border'],
                            fg=C['accent2'] if i == 0 else C['muted'],
                            relief='flat', cursor='hand2',
                            command=lambda idx=i: self.toggle_curva(idx))
            btn.pack(side='right', padx=(4, 0))
            self._filtro_btns.append(btn)

            # BotÃ³n INV solo en la fila BASE (i==0)
            if i == 0:
                self._btn_base_inv = tk.Button(row, text="INV", font=('Consolas', 9, 'bold'),
                                               bg='#1A1A2A', fg=C['muted'], relief='flat',
                                               cursor='hand2', padx=4,
                                               command=self._toggle_base_inv)
                self._btn_base_inv.pack(side='right', padx=(2, 0))

            # Color indicator
            ind = tk.Canvas(row, width=14, height=14, bg=C['panel'], highlightthickness=0)
            ind.pack(side='left', padx=(0, 4), pady=2)
            ind.create_rectangle(1, 1, 13, 13, fill=color, outline=color)

            # Nombre clickeable para seleccionar como filtro activo
            name_btn = tk.Button(row, text=nombre, font=('Consolas', 9), anchor='w',
                                 bg=C['panel'], fg=color, relief='flat', bd=0, cursor='hand2',
                                 command=lambda idx=i: self.seleccionar_filtro(idx))
            name_btn.pack(side='left', fill='x', expand=True)

            # Info label oculto (se mantiene para compatibilidad con actualizar_infos)
            info = tk.Label(row, text="", font=('Consolas', 8), bg=C['panel'],
                            fg=C['muted'], anchor='e', width=0)
            self._filtro_info.append(info)

        tk.Frame(self, bg=C['border'], height=1).pack(fill='x', padx=10, pady=6)

        # Seleccion rapida
        tk.Label(self, text="RAPIDO", font=FONT_MONO_B, bg=C['panel'],
                 fg=C['warn']).pack(anchor='w', padx=14, pady=(0, 4))
        qf = tk.Frame(self, bg=C['panel'])
        qf.pack(fill='x', padx=14, pady=(0, 4))
        for txt, indices in [("Base", [0]), ("Modos", [0,1,2]),
                             ("Mejores", [0,1,3,4]), ("Todas", list(range(len(FILTROS_CURVA)))),
                             ("Ninguna", [])]:
            tk.Button(qf, text=txt, font=('Consolas', 10), bg=C['border'], fg=C['text'],
                      relief='flat', cursor='hand2', padx=6,
                      command=lambda ids=indices: self.seleccion_rapida(ids)).pack(side='left', padx=2)

        def _auto_con_beep():
            import winsound, datetime as _dt
            _lf = Path(__file__).parent / "auto_mejor_log.txt"
            def _w(m):
                linea = f"{_dt.datetime.now().strftime('%H:%M:%S')} {m}\n"
                try:
                    with open(_lf, 'a', encoding='utf-8') as _f:
                        _f.write(linea)
                except Exception as _e:
                    pass
            _w("=== BOTON PULSADO ===")
            _w(f"_on_auto callable: {callable(self._on_auto)}")
            winsound.Beep(880, 80)
            try:
                self._on_auto(False)
                _w("_on_auto(False) ejecutado OK")
            except Exception as _ex:
                _w(f"ERROR en _on_auto: {_ex}")

        _btn_row = tk.Frame(self, bg=C['panel'])
        _btn_row.pack(fill='x', padx=14, pady=(0, 4))

        tk.Button(_btn_row, text="â¬†  AUTO â€” MEJOR FILTRO", font=('Consolas', 11, 'bold'),
                  bg='#0A1A30', fg=C['accent'], relief='flat', cursor='hand2',
                  padx=8, pady=4, command=_auto_con_beep).pack(side='left', fill='x', expand=True)

        tk.Button(_btn_row, text="SIM", font=('Consolas', 10, 'bold'),
                  bg='#0A1A30', fg=C['warn'], relief='flat', cursor='hand2',
                  padx=6, pady=4, command=self._simular_busqueda).pack(side='left', padx=(4, 0))

        if self._ep_gate_var is not None:
            tk.Checkbutton(self, text="EP GATE",
                           font=('Consolas', 11, 'bold'),
                           variable=self._ep_gate_var,
                           bg=C['panel'], fg=C['warn'],
                           selectcolor='#1A1A2A',
                           activebackground=C['panel'], activeforeground=C['warn'],
                           cursor='hand2').pack(anchor='w', padx=14, pady=(0, 6))

        tk.Frame(self, bg=C['border'], height=1).pack(fill='x', padx=10, pady=6)

        # Filtro seleccionado info
        tk.Label(self, text="FILTRO SELECCIONADO", font=FONT_MONO_B, bg=C['panel'],
                 fg=C['warn']).pack(anchor='w', padx=14, pady=(0, 2))
        self._lbl_sel_name = tk.Label(self, text="", font=FONT_TITLE, bg=C['panel'], fg=C['accent'])
        self._lbl_sel_name.pack(pady=2)
        self._lbl_sel_pnl = tk.Label(self, text="", font=FONT_BIG, bg=C['panel'], fg=C['accent2'])
        self._lbl_sel_pnl.pack()
        self._lbl_sel_detail = tk.Label(self, text="", font=FONT_MONO, bg=C['panel'], fg=C['text'])
        self._lbl_sel_detail.pack(pady=(0, 6))

        tk.Frame(self, bg=C['border'], height=1).pack(fill='x', padx=10, pady=(0, 6))

        # ProyecciÃ³n de ganancias â€” BASE y FILTRO ACTIVO
        tk.Label(self, text="PROYECCIÃ“N", font=FONT_MONO_B, bg=C['panel'],
                 fg=C['warn']).pack(anchor='w', padx=14, pady=(0, 2))

        _PERIODOS = [
            [('1h','1H'), ('2h','2H'), ('4h','4H'), ('8h','8H')],
            [('dia','DÃA'), ('sem','SEM'), ('mes','MES')],
        ]

        def _bloque_proj(store: dict, titulo: str, color_titulo: str):
            tk.Label(self, text=titulo, font=('Consolas', 8, 'bold'),
                     bg=C['panel'], fg=color_titulo).pack(anchor='w', padx=16, pady=(4, 0))
            for _items in _PERIODOS:
                _fila = tk.Frame(self, bg=C['panel'])
                _fila.pack(fill='x', padx=10, pady=(0, 1))
                for _key, _etiq in _items:
                    _pf = tk.Frame(_fila, bg=C['panel'])
                    _pf.pack(side='left', padx=5)
                    tk.Label(_pf, text=_etiq, font=('Consolas', 8), bg=C['panel'],
                             fg=C['muted']).pack(side='left')
                    _lbl = tk.Label(_pf, text=" 0", font=('Consolas', 10, 'bold'),
                                    bg=C['panel'], fg=C['muted'])
                    _lbl.pack(side='left')
                    store[_key] = _lbl

        _bloque_proj(self._proj_lbls_base,   'BASE',          C['muted'])
        _bloque_proj(self._proj_lbls_filtro, 'FILTRO ACTIVO', C['accent'])
        tk.Frame(self, bg=C['panel'], height=6).pack()


class PanelLive(tk.Frame):
    """Columna live: websocket en tiempo real, stats, proyeccion, tabla rondas."""

    def __init__(self, parent, monitor, get_filtro_state, on_resultado, on_senal=None,
                 ep_gate_activo=None, on_auto_reeval=None, solo_base_activo=None,
                 get_ep_wr=None):
        super().__init__(parent, bg=C['bg'])
        self._monitor = monitor
        self._get_filtro_state = get_filtro_state
        self._on_resultado = on_resultado
        self._on_senal = on_senal   # callback(nombre_filtro, color) → actualiza tÃ­tulo ventana
        self._ep_gate_activo = ep_gate_activo if ep_gate_activo is not None else (lambda: True)
        self._solo_base_activo = solo_base_activo if solo_base_activo is not None else (lambda: False)
        self._get_ep_wr = get_ep_wr   # callback(rango, modo) → WR% float o None
        self._on_auto_reeval = on_auto_reeval   # callback() → re-evalÃºa mejor filtro cada 5 rondas
        self._rondas_desde_reeval = 0           # contador para re-evaluaciÃ³n automÃ¡tica

        # Estado de datos
        self._live_ops = []
        self._live_all_ops = []
        self._live_pnl = 0.0
        self._live_pnl_real = 0.0   # mayorÃ­a siempre: +0.9/-1.0 cada ronda
        self._live_ac = 0
        self._live_fa = 0
        self._live_inicio_sesion = None

        # Contador de rondas recibidas EN ESTA SESIÃ“N (no incluye historico cargado).
        # Se usa para el perÃ­odo de observaciÃ³n del EP: no apostamos hasta que
        # se hayan jugado `ventana` rondas en vivo desde que se iniciÃ³ la conexiÃ³n.
        self._ep_session_ops = 0
        self._multiplicador_apuesta = 1   # leÃ­do desde hoja "apuesta" B2 al inicio de cada ronda
        self._apuesta_base_var = tk.StringVar(value='0.1')   # apuesta base controlada desde dropdown

        # SincronizaciÃ³n de inicio: proceso externo que espera una ronda completa
        self._proc_espera       = None   # subprocess esperar_ronda.py
        self._bloqueando_inicio = False  # True hasta que _proc_espera termine

        # Control de voz para Balance Filtro
        self._ultimo_bal_hablado = None

        # Auto-apuesta
        self._apuesta_auto_var  = tk.BooleanVar(value=True)
        self._base_inv_activo   = lambda: False   # se sobreescribe desde dashboard
        self._apuesta_enviada   = False   # se resetea en cada ronda

        # Confianza pre-apuesta 1-8 (solo tracking)
        self._conf_apuesta = 4
        self._conf_btns    = []

        # HistÃ³rico de decisiones pre-apuesta
        self._decisiones = cargar_decisiones()
        self._session_decision_start = len(self._decisiones)

        # â”€â”€ Estado EP UMBRAL adaptativo â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Ventana rolling de outcomes EP (1=acierto / 0=fallo) sobre las Ãºltimas
        # seÃ±ales del filtro EP UMBRAL (independiente de si apostamos o no).
        # Se decide EP/anti-EP/SKIP segÃºn el WR de esta ventana.
        from collections import deque as _dq
        self._ep_umbral_outcomes = _dq(maxlen=30)   # ventana_regimen â€” config B (selectiva ganadora)
        self._ep_umbral_warmup   = 10
        self._ep_umbral_hi       = 0.55
        self._ep_umbral_lo       = 0.50
        self._ep_umbral_color_ep = None   # color que el filtro puro habrÃ­a apostado en la ronda actual
        self._ep_umbral_regimen  = ''     # 'EP NN%' / 'ANTI NN%' / 'SKIP_REG NN%' / 'WARMUP K/W'

        # â”€â”€ Bootstrap de la ventana adaptativa EP UMBRAL â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Reconstruye los Ãºltimos 50 outcomes EP usando las decisiones histÃ³ricas
        # con `acierto` y `color_apostado` registrados (sin lookahead, en orden cronolÃ³gico).
        # AsÃ­ al arrancar el dashboard ya hay rÃ©gimen evaluable y no hay fase warmup.
        try:
            for _d in self._decisiones:
                _ep_color = (_d.get('color_apostado') or '').upper()
                _winner   = (_d.get('winner') or '').upper()
                if _ep_color in ('AZUL', 'ROJO') and _winner in ('AZUL', 'ROJO'):
                    self._ep_umbral_outcomes.append(1 if _ep_color == _winner else 0)
        except Exception:
            pass

        # Balances iniciales: heredar del Ãºltimo registro histÃ³rico
        _ul  = next((d for d in reversed(self._decisiones)
                     if d.get('balance_real') is not None), None)
        self._balance_real_inicio    = round(_ul['balance_real'], 2) if _ul else 0.0
        _ulf = next((d for d in reversed(self._decisiones)
                     if d.get('balance_filtro') is not None), None)
        self._balance_filtro_inicio  = round(_ulf['balance_filtro'], 2) if _ulf else 0.0

        self._issue_actual = None
        self._decision_actual_idx = None   # Ã­ndice en _decisiones de la ronda en curso
        self._tg_activo = True
        self._ep_umbral_min = EP_UMBRAL_MIN   # override desde Sheets (clave EP_UMBRAL_MIN)
        self._ep_rangos_bloqueados = set()    # rangos donde no apuesta (Sheets EP_RANGOS_BLOQUEADOS)
        self._ep_umbral_por_rango = {}        # override por rango (Sheets EP_UMBRAL_POR_RANGO)
        self._bots_uso = '1-2'                 # override desde Sheets (clave BOTS_USO): '1'/'2'/'1-2'
        self._apuesta_max_desbloqueada = self._cargar_max_desbloqueada()
        self._validacion_ventaja_min   = 5.0
        self._validacion_min_ops       = 100
        self._lbl_validacion           = None
        self._panel_decision_window = None # set externamente para refresco
        self._saldo_global_historico = 0.0   # actualizado por dashboard tras refrescar historico
        self._balance_historico_inicio = 0.0 # set por dashboard al arrancar y en BALANCES
        self._balance_historico_inicio = 0.0 # set por dashboard al arrancar y en BALANCES

        # Referencias UI (se asignan en _construir)
        self._lbl_live_status = None
        self._lbl_live_ronda = None
        self._lbl_senal = None
        self._lbl_apuesta = None
        self._btn_apostar = None
        self._lbl_live_prev = None
        self._lbl_live_pb = None
        self._lbl_live_pr = None
        self._lbl_live_pnl = None
        self._lbl_live_ops = None
        self._lbl_live_wr = None
        self._lbl_live_ratio = None
        self._lbl_session_timer = None
        self._proj_lbls = {}
        self._live_rows = []
        self._tick_log = None
        self._btn_live = None

        self.cargar_historico()
        self._construir()
        # Anunciar balances iniciales por voz tras breve pausa
        self.after(1200, self._anunciar_balances_inicio)
        # DiagnÃ³stico Telegram al arranque (visible en tick_log)
        self.after(1500, self._tg_diagnostico_arranque)

    def _tg_diagnostico_arranque(self):
        token_ok = bool(_tg_bot_token())
        chats    = _tg_chat_ids()
        if not token_ok:
            self._tg_log("[TG] âš  TELEGRAM_BOT_TOKEN no detectada â€” el .bat/atajo no la estÃ¡ propagando")
        if not chats:
            self._tg_log("[TG] âš  TELEGRAM_CHAT_ID no detectada â€” sin destinatarios configurados")
        if token_ok and chats:
            self._tg_log(f"[TG] OK â€” {len(chats)} chat(s) configurado(s)", 'muted')

    # â”€â”€ Propiedades publicas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @property
    def live_all_ops(self):
        return self._live_all_ops

    @property
    def live_ops(self):
        return self._live_ops

    @property
    def ep_session_ops(self):
        return self._ep_session_ops

    # â”€â”€ Metodos publicos â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def handle_ev(self, ev):
        t = ev.get('ev')
        if t == 'status':
            self._lbl_live_status.config(text=ev['msg'], fg=ev['color'])
        elif t == 'ronda':
            self._on_ronda(ev)
        elif t == 'tick':
            self._on_tick(ev)
        elif t == 'resultado':
            self._on_resultado_ev(ev)

    def actualizar_ui(self):
        # Balance del filtro ACTIVO seleccionado: saldo acumulado de su pnl_filtros[idx]
        # sobre todas las decisiones de la sesiÃ³n. AsÃ­ la voz y el label cambian
        # cuando el usuario (o el auto-mejor) cambia el filtro activo.
        try:
            sel_idx = self._get_filtro_state()[0] if self._get_filtro_state else 0
        except Exception:
            sel_idx = 0
        n, ac, bal_filtro = 0, 0, 0.0
        for _d in self._decisiones[self._session_decision_start:]:
            pf = _d.get('pnl_filtros') or {}
            delta = pf.get(str(sel_idx))
            if delta is None:
                delta = pf.get(sel_idx)
            if delta is not None:
                try:
                    bal_filtro += float(delta)
                except Exception:
                    pass
            if _d.get('decision') == 'APOSTADA' and _d.get('winner') is not None:
                n += 1
                if (_d.get('pnl') or 0) > 0:
                    ac += 1
        bal = round(bal_filtro, 2)
        wr  = (ac / n * 100) if n else 0
        ratio = (bal / n) if n else 0
        col = C['accent2'] if bal >= 0 else C['accent3']
        self._lbl_live_pnl.config(text=f"{bal:+.2f} EUR", fg=col)
        if bal != self._ultimo_bal_hablado:
            self._ultimo_bal_hablado = bal
            _euros = int(abs(bal))
            _cents = int(round(abs(bal) % 1 * 100))
            _signo = 'mÃ¡s' if bal >= 0 else 'menos'
            _texto = f"balance {_signo} {_euros}" + (f" con {_cents}" if _cents else "")
            hablar(_texto)
        self._lbl_live_ops.config(text=f"Ops:      {n}")
        self._lbl_live_wr.config(text=f"WR:       {wr:.1f}%",
                                  fg=C['accent2'] if wr > 52.6 else C['accent3'])
        self._lbl_live_ratio.config(text=f"PNL/op:   {ratio:+.3f}")

        # Proyeccion de ganancias â€” BASE y FILTRO ACTIVO
        _bal_base = None
        for _d in reversed(self._decisiones[self._session_decision_start:]):
            if _bal_base is None and _d.get('balance_real') is not None:
                _bal_base = _d['balance_real']
                break
        _bal_base = _bal_base if _bal_base is not None else self._balance_real_inicio

        _MULT = [('1h',1),('2h',2),('4h',4),('8h',8),('dia',24),('sem',24*7),('mes',24*30)]

        if self._live_inicio_sesion and n > 0:
            segs  = max(1, (datetime.datetime.now() - self._live_inicio_sesion).total_seconds())
            horas = segs / 3600

            ph_base   = _bal_base / horas
            ph_filtro = bal       / horas

            for key, mult in _MULT:
                vb = ph_base   * mult
                vf = ph_filtro * mult
                if self._proj_lbls_base:
                    self._proj_lbls_base[key].config(
                        text=f"{vb:+.1f}", fg=C['accent2'] if vb >= 0 else C['accent3'])
                if self._proj_lbls_filtro:
                    self._proj_lbls_filtro[key].config(
                        text=f"{vf:+.1f}", fg=C['accent2'] if vf >= 0 else C['accent3'])
        else:
            for store in (self._proj_lbls_base, self._proj_lbls_filtro):
                for lbl in store.values():
                    lbl.config(text=" 0", fg=C['muted'])

        # Tabla ultimas rondas
        ultimas = self._live_ops[-12:][::-1]
        for i, row_lbls in enumerate(self._live_rows):
            if i < len(ultimas):
                op = ultimas[i]
                acierto = op['acierto']
                modo = op.get('modo', 'DIRECTO')

                # ðŸ”§ Invertir acierto si modo es INVERSO
                acierto_ajustado = acierto if modo != 'INVERSO' else not acierto

                pnl_r = 0.9 if acierto_ajustado else -1.0
                col_r = C['accent2'] if acierto_ajustado else C['accent3']
                marca = 'V' if acierto_ajustado else 'X'
                row_lbls[0].config(text=marca, fg=col_r)
                row_lbls[1].config(text=op['rango'], fg=C['text'])
                row_lbls[2].config(text=op['mayor'].capitalize(), fg='#2B7FFF' if op['mayor']=='azul' else C['red'])
                row_lbls[3].config(text=op['winner'].capitalize(), fg='#2B7FFF' if op['winner']=='azul' else C['red'])
                row_lbls[4].config(text=f"{pnl_r:+.1f}", fg=col_r)
            else:
                for l in row_lbls:
                    l.config(text="")

    # â”€â”€ Telegram: selecciÃ³n de canales segÃºn BOTS_USO â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    def _tg_targets(self):
        """Devuelve subset de CHAT_IDS segÃºn self._bots_uso ('1'/'2'/'1-2')."""
        uso = (self._bots_uso or '1-2').strip()
        if not _tg_chat_ids():
            return None
        if uso == '1':
            return [_tg_chat_ids()[0]]
        if uso == '2' and len(_tg_chat_ids()) >= 2:
            return [_tg_chat_ids()[1]]
        return list(_tg_chat_ids())

    def _tg_log(self, msg, tag='warn'):
        """Vuelca un mensaje al tick_log (si existe) y a telegram_errors.log."""
        try:
            self._tick_log_append(msg + "\n", tag)
        except Exception:
            pass
        try:
            from pathlib import Path as _P
            log_path = _P(__file__).parent / 'telegram_errors.log'
            import datetime as _dt
            with log_path.open('a', encoding='utf-8') as _f:
                _f.write(f"{_dt.datetime.now().isoformat(timespec='seconds')}  {msg}\n")
        except Exception:
            pass

    def _tg_send_filtrado(self, text):
        if not _tg_bot_token():
            self._tg_log("[TG] âš  TELEGRAM_BOT_TOKEN no detectada (env no propagada)")
            return
        targets = self._tg_targets()
        if not targets:
            self._tg_log("[TG] âš  Sin CHAT_IDS â€” TELEGRAM_CHAT_ID no detectada")
            return
        try:
            resp = _tg_send(text, chat_id=targets)
        except Exception as e:
            self._tg_log(f"[TG EXC] {e!r}")
            return
        # resp puede ser dict (1 target) o list (N targets)
        items = resp if isinstance(resp, list) else [{"chat_id": targets[0], "response": resp}]
        for it in items:
            r = it.get('response') or {}
            if not r.get('ok'):
                detalle = r.get('description') or r.get('error') or str(r)[:120]
                self._tg_log(f"[TG ERROR] chat={it.get('chat_id')} → {detalle}")

    # â”€â”€ ValidaciÃ³n de escalado de apuesta base â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _ESCALADO_VALORES = ('0.1', '0.2', '0.3', '0.5', '1', '2')

    def _cargar_max_desbloqueada(self):
        try:
            import json as _json
            from pnl_config import CONFIG_FILE
            cfg = _json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            return str(cfg.get('apuesta_max_desbloqueada', '0.1'))
        except Exception:
            return '0.1'

    def _guardar_max_desbloqueada(self):
        try:
            import json as _json
            from pnl_config import CONFIG_FILE
            try:
                cfg = _json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
            except Exception:
                cfg = {}
            cfg['apuesta_max_desbloqueada'] = str(self._apuesta_max_desbloqueada)
            CONFIG_FILE.write_text(_json.dumps(cfg), encoding='utf-8')
        except Exception:
            pass

    def _validacion_apuesta_actual(self):
        """Devuelve (cumple, ventaja_eur, ops_apostadas) sobre las Ãºltimas N apostadas no-SKIP."""
        apuestas, primera, ultima = 0, None, None
        for d in reversed(self._decisiones):
            if d.get('decision') == 'SKIP' or d.get('winner') is None:
                continue
            if d.get('balance_filtro') is None or d.get('balance_real') is None:
                continue
            if ultima is None:
                ultima = d
            primera = d
            apuestas += 1
            if apuestas >= self._validacion_min_ops:
                break
        if apuestas < self._validacion_min_ops or primera is None or ultima is None:
            return False, 0.0, apuestas
        delta_filtro = ultima['balance_filtro'] - primera['balance_filtro']
        delta_real   = ultima['balance_real']   - primera['balance_real']
        ventaja = delta_filtro - delta_real
        return (ventaja >= self._validacion_ventaja_min), ventaja, apuestas

    def _refrescar_label_validacion(self):
        if self._lbl_validacion is None:
            return
        cumple, ventaja, ops = self._validacion_apuesta_actual()
        try:
            nivel_actual = float(self._apuesta_base_var.get())
            siguiente = next((v for v in self._ESCALADO_VALORES if float(v) > nivel_actual), None)
        except Exception:
            siguiente = None
        if siguiente is None:
            self._lbl_validacion.config(text="[V] nivel maximo alcanzado", fg='#00FF88')
            return
        if cumple:
            self._lbl_validacion.config(
                text=f"[V] {siguiente} EUR OK ({ventaja:+.1f} EUR/{ops}op)",
                fg='#00FF88')
        else:
            falta_ops = max(0, self._validacion_min_ops - ops)
            if falta_ops > 0:
                txt = f"âš  {siguiente}EUR: faltan {falta_ops}op"
            else:
                txt = f"âš  {siguiente}EUR: {ventaja:+.1f}EUR < {self._validacion_ventaja_min:.0f}EUR"
            self._lbl_validacion.config(text=txt, fg='#FF4444')

    def _on_cambio_apuesta_base(self, *_):
        try:
            nuevo = self._apuesta_base_var.get()
            nuevo_f = float(nuevo)
            max_f   = float(self._apuesta_max_desbloqueada)
        except Exception:
            self._refrescar_label_validacion()
            return
        if nuevo_f <= max_f:
            self._refrescar_label_validacion()
            return
        cumple, ventaja, ops = self._validacion_apuesta_actual()
        if cumple:
            self._apuesta_max_desbloqueada = nuevo
            self._guardar_max_desbloqueada()
            self._refrescar_label_validacion()
        else:
            # Revertir sin disparar recursiÃ³n infinita: el trace se vuelve a llamar pero ya entra por la rama <=max
            self._apuesta_base_var.set(self._apuesta_max_desbloqueada)
            self._refrescar_label_validacion()

    def reset_balance(self):
        self._live_pnl      = 0.0
        self._live_pnl_real = 0.0
        self._live_ac  = 0
        self._live_fa  = 0
        self._live_inicio_sesion = None
        self._session_decision_start = len(self._decisiones)
        self.actualizar_ui()

    def _anunciar_balances_inicio(self):
        pass

    def reset_all(self):
        self._live_ops      = []
        self._live_all_ops  = []
        self._live_pnl      = 0.0
        self._live_pnl_real = 0.0
        self._live_ac       = 0
        self._live_fa       = 0
        self._live_inicio_sesion = None
        self._ep_session_ops = 0   # reiniciar observaciÃ³n EP
        self._rondas_desde_reeval = 0
        self._monitor._historial = []
        self.guardar_historico()
        self.actualizar_ui()

    def cargar_historico(self):
        try:
            data = json.loads(LIVE_HIST_FILE.read_text(encoding='utf-8'))
            self._live_all_ops = data.get('ops', [])
            self._live_ops     = data.get('raw', [])

            # ðŸ”§ Si ops estÃ¡ vacÃ­o pero hay raw events, regenerar ops desde raw
            if not self._live_all_ops and self._live_ops:
                for ev in self._live_ops:
                    acierto = ev.get('acierto')
                    wr = ev.get('wr', 50.0)
                    modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
                    self._live_all_ops.append({
                        'skip': modo == 'SKIP',
                        'acierto': acierto,
                        'rango': ev.get('rango', '0-5'),
                        'modo': modo,
                        'wr': wr,
                        'est': ev.get('est', 'ESTABLE'),
                        'acel': ev.get('acel', 0.0),
                    })

            ac, fa = 0, 0
            pnl_real = 0.0
            for o in self._live_all_ops:
                # Balance real: apuesta mayorÃ­a SIEMPRE â€” acumula en cada ronda sin excepciÃ³n
                _m = float(o.get('mult') or 1)
                if o['acierto']:
                    pnl_real += 0.9 * _m
                else:
                    pnl_real -= 1.0 * _m
                if o.get('skip'):
                    continue   # rondas SKIP no cuentan para filtro (ac/fa)
                gano = o['acierto'] if o.get('modo') != 'INVERSO' else not o['acierto']
                if gano:
                    ac += 1
                else:
                    fa += 1
            self._live_ac       = ac
            self._live_fa       = fa
            self._live_pnl      = ac * 0.9 - fa * 1.0
            self._live_pnl_real = pnl_real
            # Pre-cargar ventana EP con historico: si hay â‰¥20 ops guardadas,
            # EP arranca validado (usa los Ãºltimos 20 para el rolling window)
            self._ep_session_ops = min(len(self._live_all_ops), 20)
        except Exception:
            pass

    def guardar_historico(self):
        try:
            LIVE_HIST_FILE.write_text(
                json.dumps({'ops': self._live_all_ops, 'raw': self._live_ops}, ensure_ascii=False),
                encoding='utf-8'
            )
        except Exception:
            pass

    # â”€â”€ HistÃ³rico de decisiones â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _ep_mult_actual(self, rango, modo):
        """Multiplica apuesta_base por ep_mult(WR_rango, max_mult=_multiplicador_apuesta)."""
        if self._get_ep_wr is None:
            return 1
        try:
            from backtest_ep import ep_mult as _ep_mult
            wr = self._get_ep_wr(rango, modo)
            if wr is None:
                return 1
            return _ep_mult(wr, self._multiplicador_apuesta)
        except Exception:
            return 1

    def _calcular_conf(self):
        """Calcula confianza 1-8 automÃ¡ticamente: 4 criterios Ã— 2 pts = mÃ¡x 8.
        1. WR% (distancia de 50%)   2. Estabilidad   3. |AceleraciÃ³n|   4. EP rolling."""
        wr   = getattr(self._monitor, '_wr_actual', 50.0)
        est  = getattr(self._monitor, '_est',       'VOLATIL')
        acel = abs(getattr(self._monitor, '_acel',   0.0))
        pts  = 0

        # 1. WR% â€” fuerza de la seÃ±al
        dist = abs(wr - 50)
        if dist >= 20:   pts += 2
        elif dist >= 10: pts += 1

        # 2. Estabilidad del mercado
        if est == 'ESTABLE':
            pts += 2

        # 3. AceleraciÃ³n baja (cambio suave)
        if acel < 10:    pts += 2
        elif acel < 20:  pts += 1

        # 4. EP rolling: WR de las Ãºltimas 20 ops de sesiÃ³n
        n_ep = min(len(self._live_all_ops), 20)
        if n_ep >= 10:   # mÃ­nimo 10 ops para que sea significativo
            wr_ep  = sum(1 for o in self._live_all_ops[-n_ep:] if o['acierto']) / n_ep * 100
            dist_ep = abs(wr_ep - 50)
            if dist_ep >= 20:   pts += 2
            elif dist_ep >= 10: pts += 1

        return max(1, min(8, pts))

    def _set_conf(self, v):
        """Selecciona el nivel de confianza pre-apuesta (1-8). v=0 → en blanco."""
        self._conf_apuesta = v
        for i, b in enumerate(self._conf_btns):
            sel = (v > 0 and i + 1 == v)
            b.config(bg='#1A3A5A' if sel else C['border'],
                     fg=C['white'] if sel else C['muted'])

    def _registrar_decision_t36(self, ev, decision, color_apostado, ep_gate_txt):
        """Registra el snapshot de contexto y decisiÃ³n en T36 (antes del resultado)."""
        try:
            sel, _ = self._get_filtro_state()
            nombre_filtro = FILTROS_CURVA[sel][0]
        except Exception:
            nombre_filtro = '?'

        wr_actual = getattr(self._monitor, '_wr_actual', 50.0)
        if self._is_base_mode():
            modo_t36 = 'BASE'
        elif wr_actual >= 60:
            modo_t36 = 'DIRECTO'
        elif wr_actual <= 40:
            modo_t36 = 'INVERSO'
        else:
            modo_t36 = 'SKIP'

        # WR rolling del EP: promedio de aciertos en las Ãºltimas 20 ops
        _hist = self._live_all_ops
        _n_ep = min(len(_hist), 20)
        wr_ep_rolling = round(sum(1 for o in _hist[-_n_ep:] if o['acierto']) / _n_ep * 100, 1) if _n_ep else 0.0

        # Multiplicador EP (1, 2, 3, â€¦) segÃºn rango+modo. SKIP/BASE → 1.
        _rango_reg = calcular_rango(getattr(self._monitor, '_dif_t33', 0.0))
        _ap_base   = float(self._apuesta_base_var.get())
        if modo_t36 in ('DIRECTO', 'INVERSO'):
            _mult_reg = self._ep_mult_actual(_rango_reg, modo_t36)
        else:
            _mult_reg = 1

        # Propagar la seÃ±al del filtro activo (calculada en _calcular_senal) cuando
        # no hubo apuesta real, para que el color y el PNL sean coherentes con
        # `_calcular_pnl_filtros`. SÃ³lo si modo es direccional (DIRECTO/INVERSO/BASE):
        # con modo SKIP no apostamos teÃ³ricamente → no contar PNL ni mover balance.
        if not color_apostado and modo_t36 in ('DIRECTO', 'INVERSO', 'BASE'):
            _signal = getattr(self, '_color_apuesta_actual', None)
            if isinstance(_signal, str) and _signal.upper() in ('AZUL', 'ROJO'):
                color_apostado = _signal.upper()

        # Si el filtro activo es EP UMBRAL adaptativo, sobreescribir el campo
        # ep_gate_txt con el rÃ©gimen actual (EP / ANTI / SKIP_REG / WARMUP)
        # para que sea visible en el histÃ³rico de decisiones.
        try:
            if nombre_filtro == 'EP UMBRAL' and getattr(self, '_ep_umbral_regimen', ''):
                ep_gate_txt = self._ep_umbral_regimen
        except Exception:
            pass

        _now = datetime.datetime.now()
        registro = {
            'timestamp':      _now.isoformat(timespec='seconds'),
            'hora':           _now.strftime('%H:%M:%S'),
            'issue':          self._issue_actual or '---',
            'mayor':          ev.get('mayor', ''),
            'p_b':            ev.get('p_b'),
            'p_r':            ev.get('p_r'),
            'dif':            ev.get('dif'),
            'wr':             round(wr_actual, 1),
            'rango':          calcular_rango(getattr(self._monitor, '_dif_t33', 0.0)),
            'est':            getattr(self._monitor, '_est', 'ESTABLE'),
            'acel':           getattr(self._monitor, '_acel', 0.0),
            'modo':           modo_t36,
            'filtro':         nombre_filtro,
            'filtro_nombre':  nombre_filtro,
            'filtro_idx':     sel,
            'ep_gate':        ep_gate_txt,
            'wr_ep':          wr_ep_rolling,
            'decision':       decision,
            'color_apostado': color_apostado,
            'ep_session_ops': self._ep_session_ops,
            'apuesta':        _ap_base,
            'mult':           _mult_reg,
            'conf':           self._conf_apuesta,   # siempre: Base apuesta todas las rondas
            # Campos a completar en _on_resultado_ev:
            'winner':         None,
            'acierto':        None,
            'acierto_marca':  '',
            'pnl':            None,
            'pnl_base':       None,
        }
        self._decisiones.append(registro)
        self._decision_actual_idx = len(self._decisiones) - 1
        guardar_decisiones(self._decisiones)
        self._refrescar_decision_window()

        # Telegram: enviar predicciÃ³n. SKIP → bolita blanca; con seÃ±al → color.
        _color_filtro = color_apostado or getattr(self, '_color_apuesta_actual', None)
        registro['_tg_envio'] = bool(self._tg_activo)
        registro['_tg_color'] = (color_apostado or _color_filtro or '')
        _no_apuesta = (decision != 'APOSTADA') or registro.get('modo') == 'SKIP'
        # Flag para que _completar_decision_resultado NO envÃ­e el resultado
        # cuando la predicciÃ³n fue bolita blanca (no apuesta).
        registro['_tg_skip_resultado'] = bool(_no_apuesta)
        if registro['_tg_envio']:
            _EMOJI = {'azul': '🔵', 'rojo': '🔴'}
            _cp  = (color_apostado or _color_filtro or '').lower()
            _iss = str(self._issue_actual or '---')[-3:]
            _hr  = registro['hora']
            _prev_txt = '⚪' if _no_apuesta else (_EMOJI.get(_cp, '⚪') if _cp else '⚪')
            try:
                _sel, _ = self._get_filtro_state()
                _nombre_f = FILTROS_CURVA[_sel][0]
            except Exception:
                _sel = '?'
                _nombre_f = '?'
            _msg_pred = f"{_hr} {_iss}  {_prev_txt} {_nombre_f} - {_mult_reg}"
            threading.Thread(target=self._tg_send_filtrado, args=(_msg_pred,), daemon=True).start()

    def _completar_decision_resultado(self, ev):
        """Completa la decisiÃ³n actual con winner/acierto/pnl al recibir el resultado."""
        _hora_resultado = datetime.datetime.now().strftime('%H:%M:%S')
        idx = self._decision_actual_idx
        # Fallback: buscar la entrada mÃ¡s reciente sin resultado asignado
        if idx is None:
            for i in range(len(self._decisiones) - 1, -1, -1):
                if self._decisiones[i].get('winner') is None:
                    idx = i
                    break
        if idx is None:
            return
        try:
            d = self._decisiones[idx]
        except IndexError:
            return

        winner   = ev.get('winner', '')
        acierto  = ev.get('acierto', False)
        decision = d.get('decision', 'SKIP')
        color    = d.get('color_apostado')

        # Derivar color teÃ³rico solo para SKIP (no para OBS: sin datos suficientes)
        # Persistir d['color_apostado'] sÃ³lo si hay seÃ±al direccional clara
        # (â‰¥53.2 DIRECTO o â‰¤46.8 INVERSO) para que la columna Color refleje
        # lo que el filtro habrÃ­a apostado. En zona neutra queda vacÃ­a.
        # Si el filtro activo es EP UMBRAL y `color` ya es None aquÃ­, significa
        # que el filtro NO seÃ±alÃ³ (rango sin stats suficientes) → no aplicar
        # el fallback wr_ep para que PNL=0 y coincida con balance_filtro.
        _filtro_d = (d.get('filtro') or '').upper()
        if not color and decision != 'OBS' and _filtro_d != 'EP UMBRAL':
            mayor_d = (d.get('mayor') or '').upper()
            wr_ep   = d.get('wr_ep') or 0.0
            EP_UMB  = 53.2
            if wr_ep >= EP_UMB and mayor_d:
                color = mayor_d                                        # DIRECTO → mayorÃ­a
                d['color_apostado'] = color
            elif wr_ep <= (100 - EP_UMB) and mayor_d:
                color = 'ROJO' if mayor_d == 'AZUL' else 'AZUL'       # INVERSO → minorÃ­a
                d['color_apostado'] = color
            # else: zona neutra real → color sigue None → marca 'Â·'

        if decision == 'APOSTADA' and color:
            # Apuesta real: comparar color con ganador
            apuesta_correcta = (color.lower() == winner.lower())
            d['acierto']       = apuesta_correcta
            d['acierto_marca'] = 'V' if apuesta_correcta else 'X'
            _ap = float(d.get('apuesta') or 1)
            _m  = float(d.get('mult') or 1)
            d['pnl']           = round((0.9 * _ap * _m) if apuesta_correcta else (-1.0 * _ap * _m), 2)
        elif decision == 'OBS':
            # ObservaciÃ³n: derivar color teÃ³rico del modo; SKIP → usar mayorÃ­a como ref.
            modo_d  = d.get('modo', '')
            mayor_d = (d.get('mayor') or '').upper()
            if modo_d == 'DIRECTO' and mayor_d:
                color = mayor_d
            elif modo_d == 'INVERSO' and mayor_d:
                color = 'ROJO' if mayor_d == 'AZUL' else 'AZUL'
            elif mayor_d:                          # SKIP: mayorÃ­a como referencia
                color = mayor_d
            else:
                color = None
            d['color_apostado'] = color
            if color:
                teorico_correcto   = (color.lower() == winner.lower())
                d['acierto']       = teorico_correcto
                d['acierto_marca'] = 'V' if teorico_correcto else 'X'
            else:
                d['acierto']       = None
                d['acierto_marca'] = 'Â·'
            # PNL teÃ³rico para DIRECTO/INVERSO; SKIP → 0
            if color and modo_d in ('DIRECTO', 'INVERSO'):
                _ap_obs = float(d.get('apuesta') or 1)
                _m_obs  = float(d.get('mult') or 1)
                _t_ok   = (color.lower() == winner.lower())
                d['pnl'] = round((0.9 * _ap_obs * _m_obs) if _t_ok else (-1.0 * _ap_obs * _m_obs), 2)
            else:
                d['pnl'] = 0.0
        elif color:
            # SKIP con color teÃ³rico (filtro seÃ±alÃ³ o ep_dir calculado).
            # Si hay color_apostado, el filtro decidiÃ³ apostar → siempre computar PNL.
            teorico_correcto   = (color.lower() == winner.lower())
            d['acierto']       = teorico_correcto
            d['acierto_marca'] = 'V' if teorico_correcto else 'X'
            _ap_sk = float(d.get('apuesta') or 1)
            _m_sk  = float(d.get('mult') or 1)
            d['pnl'] = round((0.9 * _ap_sk * _m_sk) if teorico_correcto else (-1.0 * _ap_sk * _m_sk), 2)
        else:
            # SKIP zona neutra: derivar color segÃºn modo (DIRECTO=mayor, INVERSO=minorÃ­a).
            # Para EP UMBRAL, si llegamos aquÃ­ es porque el filtro no seÃ±alÃ³ →
            # PNL = 0 y sin marca para coincidir con balance_filtro.
            mayor_d = (d.get('mayor') or '').upper()
            modo_zn = d.get('modo', '')
            if _filtro_d == 'EP UMBRAL':
                d['acierto']       = None
                d['acierto_marca'] = 'Â·'
                d['pnl']           = 0.0
            elif mayor_d:
                if modo_zn == 'INVERSO':
                    color = 'ROJO' if mayor_d == 'AZUL' else 'AZUL'
                else:
                    color = mayor_d
                d['color_apostado'] = color
                teorico_correcto    = (color.lower() == winner.lower())
                d['acierto']        = teorico_correcto
                d['acierto_marca']  = 'V' if teorico_correcto else 'X'
                # Si modo es SKIP → no contar PNL en balance_filtro.
                if modo_zn in ('DIRECTO', 'INVERSO', 'BASE'):
                    _ap_zn = float(d.get('apuesta') or 1)
                    _m_zn  = float(d.get('mult') or 1)
                    d['pnl'] = round((0.9 * _ap_zn * _m_zn) if teorico_correcto else (-1.0 * _ap_zn * _m_zn), 2)
                else:
                    d['pnl'] = 0.0
            else:
                d['acierto']       = None
                d['acierto_marca'] = 'Â·'
                d['pnl']           = 0.0

        d['winner'] = winner

        # Gate final: SKIP/OBS sin apuesta real → forzar pnl=0 y acierto=None
        # (ningÃºn cÃ¡lculo teÃ³rico debe propagarse a la columna PNL visible).
        if d.get('decision') != 'APOSTADA':
            # Conservar el dato teÃ³rico en campos paralelos por si se quiere analizar
            d['_pnl_teorico']     = d.get('pnl', 0.0)
            d['_acierto_teorico'] = d.get('acierto')
            d['pnl']              = 0.0
            d['acierto']          = None
            d['acierto_marca']    = 'Â·'

        # â”€â”€ Alimentar ventana adaptativa EP UMBRAL con outcome teÃ³rico â”€â”€
        # Si en T35 el filtro EP UMBRAL puro habrÃ­a apostado, registrar 1=acierto/0=fallo
        # de esa apuesta teÃ³rica para que el rÃ©gimen se calcule correctamente.
        _ep_color = getattr(self, '_ep_umbral_color_ep', None)
        if _ep_color and isinstance(_ep_color, str):
            _gano_ep = (_ep_color.lower() == (winner or '').lower())
            self._ep_umbral_outcomes.append(1 if _gano_ep else 0)
            self._ep_umbral_color_ep = None   # consumido

        # Un Ãºnico pass reverso para encontrar balance_real y balance_filtro de la sesiÃ³n actual.
        # balance_filtro es filtro-especÃ­fico: sÃ³lo se considera la Ãºltima ronda con el MISMO filtro.
        _filtro_actual = d.get('filtro')
        _prev_real, last_bal, last_nombre = None, None, None
        for _pd in reversed(self._decisiones[self._session_decision_start:-1]):
            if _prev_real is None and _pd.get('balance_real') is not None:
                _prev_real = _pd['balance_real']
            if (last_bal is None
                    and _pd.get('balance_filtro') is not None
                    and _pd.get('filtro') == _filtro_actual):
                last_bal    = _pd['balance_filtro']
                last_nombre = _pd.get('filtro_nombre') or '?'
            if _prev_real is not None and last_bal is not None:
                break
        _prev_real = _prev_real if _prev_real is not None else self._balance_real_inicio

        # balance_real: sÃ³lo se mueve cuando hubo apuesta real (decision==APOSTADA).
        # Factor = apuesta (sin multiplicador: el filtro Base ignora mult)
        _ap_d    = float(d.get('apuesta') or 1)
        _factor_d = _ap_d
        _mayor_s = (d.get('mayor') or '').lower()
        _gano_mayoria_r = (winner.lower() == _mayor_s)
        if d.get('decision') != 'APOSTADA':
            _delta = 0.0
        else:
            _delta = 0.9 * _factor_d if _gano_mayoria_r else -1.0 * _factor_d
        d['pnl_base']    = round(_delta, 2)
        d['balance_real'] = round(_prev_real + _delta, 2)
        d['pnl_filtros'] = self._calcular_pnl_filtros(d)
        # HistÃ³rico LONG: 1 lÃ­nea por filtro Ã— ronda en pnl_filtros_long.jsonl
        self._escribir_filtros_long(d)

        # â”€â”€ Log de resultados por filtro â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        try:
            partes = []
            for i, (nombre, _, filtro_fn, _, _) in enumerate(FILTROS_CURVA):
                if filtro_fn is None or isinstance(filtro_fn, str):
                    continue
                delta = d['pnl_filtros'].get(i)
                if delta is None:
                    continue
                if delta > 0:
                    partes.append(f"#{i} {nombre[:10]} {delta:+.1f}")
                elif delta < 0:
                    partes.append(f"#{i} {nombre[:10]} {delta:+.1f}")
                # delta == 0 (skip): no se loguea para no saturar
            if partes:
                self._tick_log_append("[FILTROS] " + "  |  ".join(partes) + "\n", 'dim')
        except Exception:
            pass

        if last_bal is None:
            # Primera ronda de sesiÃ³n: partir del balance histÃ³rico
            last_bal    = self._balance_filtro_inicio
            last_nombre = '?'

        # balance_filtro: usar el delta teÃ³rico del filtro activo (pnl_filtros)
        # en lugar del PNL real (d['pnl']), para que el balance solo se mueva
        # cuando el filtro realmente habrÃ­a apostado segÃºn su lambda.
        _delta_bal = 0.0
        if d.get('decision') == 'APOSTADA':
            try:
                _act_nombre = d.get('filtro', '')
                _act_idx = next(i for i, e in enumerate(FILTROS_CURVA) if e[0] == _act_nombre)
                _pf = d.get('pnl_filtros') or {}
                _teorico = _pf.get(str(_act_idx))
                if _teorico is None:
                    _teorico = _pf.get(_act_idx)
                if _teorico is not None:
                    _delta_bal = float(_teorico)
            except Exception:
                _delta_bal = (d.get('pnl') or 0.0)
        d['balance_filtro'] = round(last_bal + _delta_bal, 2)
        try:
            self._refrescar_label_validacion()
        except Exception:
            pass

        try:
            _actual = FILTROS_CURVA[self._get_filtro_state()[0]][0]
            d['filtro_nombre'] = (last_nombre
                                  if (last_nombre and last_nombre != '?')
                                  else _actual)
        except Exception:
            d['filtro_nombre'] = (last_nombre
                                  if (last_nombre and last_nombre != '?')
                                  else 'EP UMBRAL')

        guardar_decisiones(self._decisiones)
        self._refrescar_decision_window()

        # NotificaciÃ³n Telegram: enviar resultado sÃ³lo si la predicciÃ³n NO fue bolita blanca.
        if self._tg_activo and d.get('_tg_envio') and not d.get('_tg_skip_resultado'):
            _EMOJI = {'azul': '🔵', 'rojo': '🔴'}
            _w  = (d.get('winner') or '').lower()
            _cp = (d.get('color_apostado') or d.get('_tg_color') or '').lower()
            _marca = d.get('acierto_marca', 'Â·')
            _prev_txt = _EMOJI.get(_cp, '⚪') if _cp else '⚪'
            _win_txt  = _EMOJI.get(_w, '⚪') if _w else '?'
            _issue = str(d.get('issue') or '---')[-3:]
            _copa = "  [T]" if _marca == 'V' else ""
            # Calcular saldo acumulado actual desde las decisiones de sesion
            _tg_saldo = self._balance_historico_inicio
            for _td in self._decisiones[self._session_decision_start:]:
                if _td.get('decision') != 'APOSTADA':
                    continue
                _tp = _td.get('pnl')
                if _tp is None or float(_tp) == 0:
                    continue
                _tg_saldo += float(_tp)
            _msg = (f"{_hora_resultado} {_issue}  {_prev_txt} → {_win_txt}  {_marca}{_copa}\n"
                    f"Saldo: {_tg_saldo:+.2f}")
            threading.Thread(target=self._tg_send_filtrado, args=(_msg,), daemon=True).start()

        # â”€â”€ ANIMACIONES DE VICTORIA â€” gate Ãºnico compartido â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Ambas victorias requieren la MISMA condiciÃ³n de "ganaste dinero
        # de verdad": decision=APOSTADA + acierto=True + pnl>0 + color
        # apostado coincide con el winner. Si cualquiera falla → silencio.
        _color_ap = (d.get('color_apostado') or '').strip().lower()
        _winner_d = (d.get('winner') or '').strip().lower()
        _mayor_d  = (d.get('mayor')  or '').strip().lower()
        _pnl_d    = float(d.get('pnl') or 0)
        _victoria_real = (
            d.get('decision') == 'APOSTADA'
            and d.get('acierto') is True
            and _pnl_d > 0
            and _color_ap
            and _winner_d
            and _color_ap == _winner_d
        )
        if _victoria_real:
            try:
                hablar("Ganada")
            except Exception:
                pass
            def _tocar_trompeta():
                try:
                    import winsound, time
                    notas = [(392, 120), (523, 120), (659, 120), (784, 180),
                             (784, 120), (784, 120), (1047, 350)]
                    for freq, dur in notas:
                        winsound.Beep(freq, dur)
                        time.sleep(0.02)
                except Exception:
                    pass
            import threading as _thr
            _thr.Thread(target=_tocar_trompeta, daemon=True).start()
            # victoria_b.py: ganaste apostando a la MAYORÃA (color_apostado == mayor)
            # victoria_f.py: ganaste apostando a la MINORÃA / contraria (color_apostado != mayor)
            # Antes victoria_b se disparaba siempre que ganaba la mayorÃ­a
            # (independiente de tu apuesta), causando falsos positivos cuando
            # apostabas INVERSO y la mayorÃ­a ganaba (perdÃ­as, pero salÃ­a la
            # animaciÃ³n). Ahora ambas son mutuamente excluyentes y ligadas a
            # tu apuesta real.
            if _mayor_d and _color_ap == _mayor_d:
                _victoria_script = 'victoria_b.py'
            else:
                _victoria_script = 'victoria_f.py'
            subprocess.Popen(['py', str(Path(__file__).parent / _victoria_script)],
                             creationflags=0x08000000)

        self._decision_actual_idx = None

    def _calcular_pnl_filtros(self, d):
        """Calcula el delta PNL teorico de esta ronda para cada filtro simple y lo devuelve como dict.
        Evalua la lambda estricta de cada filtro sin override de PnL real."""
        winner   = (d.get('winner')  or '').lower()
        mayor    = (d.get('mayor')   or '').lower()
        modo     = d.get('modo', 'SKIP')
        apuesta  = float(d.get('apuesta') or 1)
        mult     = float(d.get('mult') or 1)
        factor   = apuesta * mult
        decision = d.get('decision', 'SKIP')

        # Si el modo es BASE (SOLO BASE activo), derivar modo teÃ³rico del WR
        # para que los filtros DIRECTO/INVERSO puedan evaluarse correctamente
        wr = float(d.get('wr') or 50)
        if modo == 'BASE':
            modo_teorico = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
        else:
            modo_teorico = modo

        # skip se basa SOLO en modo_teorico, NO en decision real.
        # AsÃ­ cada filtro produce su delta teÃ³rico aunque la ronda haya sido OBS/SKIP.
        op = {
            'skip':         modo_teorico == 'SKIP',
            'acierto':      bool(d.get('acierto', False)),
            'gano_mayoria': winner == mayor,
            'modo':         modo_teorico,
            'rango':        d.get('rango', '?'),
            'est':          d.get('est', 'ESTABLE'),
            'acel':         float(d.get('acel') or 0),
            'wr':           float(d.get('wr') or 50),
            'mult':         mult,
        }

        # FORZAR cÃ¡lculo teÃ³rico de TODOS los filtros en CADA ronda,
        # ignorando sus condiciones de lambda y `skip`.
        # Cada filtro apuesta siempre a su direcciÃ³n preferida:
        #   - raw → mayor (Base bet siempre a mayorÃ­a)
        #   - nombre con "INVERSO" → minor
        #   - resto → mayor (defecto DIRECTO)
        # Luego se aplica contrarian si el filtro lo tiene.
        # Filtros especiales (None / string) se calculan aparte.
        resultado = {}
        # REGLA ABSOLUTA: si decision != APOSTADA → delta=0 para TODOS los
        # filtros. Sin teÃ³ricos, sin "hubiera apostado". SÃ³lo cuenta el dinero
        # realmente movido en la ronda.
        _no_apuesta = (decision != 'APOSTADA')
        for i, (nombre_fn, _, filtro_fn, contrarian, raw) in enumerate(FILTROS_CURVA):
            if filtro_fn is None:
                resultado[i] = None   # EP ADAPTATIVO: rolling, calculado aparte
                continue
            if _no_apuesta:
                resultado[i] = 0.0
                continue
            if isinstance(filtro_fn, str):
                if filtro_fn == 'EP_UMBRAL':
                    # Usar el color que el filtro decidiÃ³ en T35 (sin lookahead).
                    _eu_pnl = 0.0
                    _color_ap = (d.get('color_apostado') or '').lower()
                    _winner   = (d.get('winner') or '').lower()
                    if _color_ap in ('azul', 'rojo') and _winner:
                        _gano_ep = (_color_ap == _winner)
                        _eu_pnl = round(0.9 * factor if _gano_ep else -1.0 * factor, 2)
                    resultado[i] = _eu_pnl
                else:
                    resultado[i] = 0.0
                continue
            # Si este filtro era el activo en la ronda, usar PnL real
            if i == d.get('filtro_idx'):
                pnl = d.get('pnl')
                if pnl is not None:
                    resultado[i] = float(pnl)
                    continue
            # Sin winner → no se puede calcular
            if not winner or not mayor:
                resultado[i] = 0.0
                continue
            # Respetar la lambda especÃ­fica del filtro (excepto Base = raw=True)
            if not raw:
                try:
                    if not filtro_fn(op):
                        resultado[i] = 0.0
                        continue
                except Exception:
                    resultado[i] = 0.0
                    continue
                if op['skip']:
                    resultado[i] = 0.0
                    continue
            # DirecciÃ³n preferida del filtro â€” alineada con live (`_calcular_senal`):
            #   raw            → mayor (Base apuesta siempre a mayorÃ­a)
            #   nombre INVERSO → minor (filtros forzados a INVERSO)
            #   resto          → sigue op['modo'] (DIRECTO=mayor, INVERSO=minor)
            # DespuÃ©s contrarian invierte la apuesta final.
            if raw:
                gano = op['gano_mayoria']
            elif 'INVERSO' in nombre_fn.upper():
                gano = not op['gano_mayoria']
            elif op['modo'] == 'INVERSO':
                gano = not op['gano_mayoria']
            else:
                gano = op['gano_mayoria']
            if contrarian:
                gano = not gano
            # Filtro Base (idx 0): nunca aplica multiplicador
            _factor_i = apuesta if i == 0 else factor
            resultado[i] = round(0.9 * _factor_i if gano else -1.0 * _factor_i, 2)
        return resultado

    # â”€â”€ HistÃ³rico LONG por filtro (1 lÃ­nea por filtro Ã— ronda) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _cargar_saldos_filtros_long(self):
        """Lee pnl_filtros_long.jsonl y devuelve dict {filtro_idx: ultimo_saldo}."""
        saldos = {}
        if not FILTROS_LONG_FILE.exists():
            return saldos
        try:
            with FILTROS_LONG_FILE.open('r', encoding='utf-8') as f:
                for linea in f:
                    linea = linea.strip()
                    if not linea:
                        continue
                    try:
                        r = json.loads(linea)
                        saldos[r['filtro_idx']] = float(r.get('saldo') or 0.0)
                    except Exception:
                        continue
        except Exception:
            pass
        return saldos

    def _escribir_filtros_long(self, d):
        """Tras computar d['pnl_filtros'], aÃ±ade una lÃ­nea JSON por cada filtro
        con TODOS los campos de la decisiÃ³n original + campos del filtro."""
        if not hasattr(self, '_saldo_filtros') or self._saldo_filtros is None:
            self._saldo_filtros = self._cargar_saldos_filtros_long()
        pnl_f = d.get('pnl_filtros') or {}
        entradas = []
        for i, entry_c in enumerate(FILTROS_CURVA):
            nombre, color = entry_c[0], entry_c[1]
            # Aceptar claves int o str
            delta = pnl_f.get(str(i))
            if delta is None:
                delta = pnl_f.get(i)
            if delta is None:
                # EP ADAPTATIVO (None) → registrar como 0 para mantener una lÃ­nea por filtro
                delta = 0.0
            try:
                delta = float(delta)
            except Exception:
                delta = 0.0
            self._saldo_filtros[i] = round(self._saldo_filtros.get(i, 0.0) + delta, 4)

            # â”€â”€ Copia COMPLETA de la decisiÃ³n original â”€â”€
            entrada = dict(d)
            # Quitar pnl_filtros (dict completo): cada lÃ­nea representa un filtro
            # concreto vÃ­a 'delta'/'filtro_idx'; el dict completo duplicarÃ­a datos.
            entrada.pop('pnl_filtros', None)
            # Campos especÃ­ficos del filtro:
            entrada['filtro_idx']    = i
            entrada['filtro_nombre'] = nombre
            entrada['filtro_color']  = color
            entrada['delta']         = round(delta, 4)
            entrada['saldo']         = self._saldo_filtros[i]
            entradas.append(entrada)

        try:
            with FILTROS_LONG_FILE.open('a', encoding='utf-8') as f:
                for e in entradas:
                    f.write(json.dumps(e, ensure_ascii=False) + '\n')
        except Exception as ex:
            print(f"[FILTROS_LONG] Error escribiendo: {ex}")

    def _refrescar_decision_window(self):
        """Si la ventana de histÃ³rico estÃ¡ abierta, la refresca."""
        try:
            if self._panel_decision_window and self._panel_decision_window.winfo_exists():
                self._panel_decision_window.refrescar()
        except Exception:
            pass

    def _aplicar_anchos_historico(self, widths_dict):
        """Aplica anchos de columna leÃ­dos de Sheets a la ventana de histÃ³rico (si estÃ¡ abierta)."""
        try:
            if self._panel_decision_window and self._panel_decision_window.winfo_exists():
                self._panel_decision_window.aplicar_anchos(widths_dict)
        except Exception:
            pass

    def get_decisiones(self):
        """Lectura externa (la ventana Toplevel obtiene los datos vÃ­a esta callback)."""
        return self._decisiones

    def simular_pnl_ep_umbral_sesion(self, mult_maximo=5, solo_filtro_activo=False):
        """Simula el PNL teÃ³rico de EP UMBRAL ADAPTATIVO aplicando la estrategia
        (sin lookahead). Stats rolling se construyen con TODAS las rondas (para
        WR realista). Aplica rÃ©gimen adaptativo (EP/anti-EP/SKIP) usando los
        parÃ¡metros configurados en `self._ep_umbral_*`.
        - solo_filtro_activo=False: suma PNL en cada ronda con seÃ±al
        - solo_filtro_activo=True : suma PNL solo en rondas con filtro=='EP UMBRAL'
        El multiplicador se calcula con ep_mult(WR, mult_maximo).
        """
        from pnl_config import FILTROS_CURVA
        from collections import defaultdict, deque
        try:
            from backtest_ep import ep_mult
        except Exception:
            ep_mult = lambda conf: 1
        _eu_idx = next((i for i, e in enumerate(FILTROS_CURVA) if e[2] == 'EP_UMBRAL'), None)
        if _eu_idx is None:
            return 0.0

        # Params OpciÃ³n B (selectiva ganadora) â€” coherentes con
        # pnl_data.curva_pnl_umbral y el motor live.
        _EP_MIN = 62.0
        _MIN_OPS = 5
        total = 0.0
        stats = defaultdict(lambda: {'DIRECTO': {'ops': 0, 'gan': 0},
                                     'INVERSO': {'ops': 0, 'gan': 0}})
        # RÃ©gimen adaptativo: ventana rolling de outcomes EP
        _v_max = self._ep_umbral_outcomes.maxlen if hasattr(self, '_ep_umbral_outcomes') else 30
        _w_arm = getattr(self, '_ep_umbral_warmup', 10)
        _u_hi  = getattr(self, '_ep_umbral_hi', 0.55)
        _u_lo  = getattr(self, '_ep_umbral_lo', 0.50)
        ventana = deque(maxlen=_v_max)

        for d in self._decisiones:
            _rango     = d.get('rango', '')
            _winner    = (d.get('winner') or '').lower()
            _mayor     = (d.get('mayor')  or '').lower()
            _modo_real = d.get('modo', '')
            _filtro    = d.get('filtro', '')

            if not _rango or not _winner or not _mayor:
                continue

            # 1. DecisiÃ³n EP UMBRAL con stats PREVIAS (sin lookahead)
            d_st = stats[_rango]['DIRECTO']
            i_st = stats[_rango]['INVERSO']
            d_wr = d_st['gan'] / d_st['ops'] * 100 if d_st['ops'] >= _MIN_OPS else 0.0
            i_wr = i_st['gan'] / i_st['ops'] * 100 if i_st['ops'] >= _MIN_OPS else 0.0
            d_ok = d_wr >= _EP_MIN
            i_ok = i_wr >= _EP_MIN

            pnl_eu = 0.0
            _gano_ep_for_window = None  # outcome EP para alimentar la ventana adaptativa
            if d_ok or i_ok:
                if (d_wr if d_ok else 0) >= (i_wr if i_ok else 0):
                    _color_ep = _mayor
                    wr_m = d_wr
                else:
                    _color_ep = 'rojo' if _mayor == 'azul' else 'azul'
                    wr_m = i_wr
                mult = ep_mult(wr_m, mult_maximo)
                # 'mult' en la decisiÃ³n es la apuesta base del usuario (0.1, 0.2â€¦),
                # NO el multiplicador EP. El nombre histÃ³rico es engaÃ±oso.
                _ap  = float(d.get('mult') or 1)
                _factor = _ap * mult
                _gano_ep = (_color_ep == _winner)
                _gano_ep_for_window = _gano_ep
                # Aplicar rÃ©gimen adaptativo
                if len(ventana) >= _w_arm:
                    _wr_reg = sum(ventana) / len(ventana)
                    if _wr_reg > _u_hi:
                        # EP normal
                        pnl_eu = round(0.9 * _factor if _gano_ep else -1.0 * _factor, 2)
                    elif _wr_reg < _u_lo:
                        # Anti-EP
                        pnl_eu = round(0.9 * _factor if not _gano_ep else -1.0 * _factor, 2)
                    # else zona neutra: pnl_eu = 0 (SKIP)
                # else warmup: pnl_eu = 0 (SKIP)

            # Si solo_filtro_activo, sumar solo cuando filtro=='EP UMBRAL'
            if not solo_filtro_activo or _filtro == 'EP UMBRAL':
                total += pnl_eu

            # Alimentar ventana con outcome EP (siempre que el filtro habrÃ­a apostado)
            if _gano_ep_for_window is not None:
                ventana.append(1 if _gano_ep_for_window else 0)

            # Guardar pnl_filtros teÃ³rico para esta ronda
            if d.get('pnl_filtros') is None:
                d['pnl_filtros'] = {}
            d['pnl_filtros'][_eu_idx] = pnl_eu

            # 2. Actualizar stats POST-decisiÃ³n â€” cada ronda alimenta
            # AMBOS modos (DIRECTO y INVERSO) usando gano_mayoria como base
            # limpia (independiente del modo registrado en la decisiÃ³n).
            # Coherente con rebuild_decision_history.py y curva_pnl_umbral.
            acierto_mayoria = (_winner == _mayor)
            stats[_rango]['DIRECTO']['ops'] += 1
            stats[_rango]['INVERSO']['ops'] += 1
            if acierto_mayoria:
                stats[_rango]['DIRECTO']['gan'] += 1
            else:
                stats[_rango]['INVERSO']['gan'] += 1

        return round(total, 2)

    def set_decision_window(self, win):
        self._panel_decision_window = win

    def _panel_live_clear_decisiones(self):
        """Llamado por la ventana de histÃ³rico al limpiar."""
        self._decisiones = []
        self._decision_actual_idx = None

    # â”€â”€ Handlers internos â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _on_resultado_ev(self, ev):
        acierto = ev['acierto']
        if self._live_inicio_sesion is None:
            self._live_inicio_sesion = datetime.datetime.now()
        _wr   = ev['wr']
        _modo = 'DIRECTO' if _wr >= 60 else ('INVERSO' if _wr <= 40 else 'SKIP')
        self._ep_session_ops += 1   # ronda recibida en vivo (no historico)

        # Balance live: solo cuenta cuando la apuesta se realiza (modo != SKIP).
        # Si modo es INVERSO, invertir el acierto (apostÃ³ a la minorÃ­a)
        _mult = float(self._apuesta_base_var.get())
        if _modo != 'SKIP':
            _acierto_ajustado = acierto if _modo != 'INVERSO' else not acierto
            if _acierto_ajustado:
                self._live_pnl += 0.9 * _mult
                self._live_ac  += 1
            else:
                self._live_pnl -= 1.0 * _mult
                self._live_fa  += 1

        # Balance real: apuesta mayorÃ­a SIEMPRE â€” acumula en cada ronda sin excepciÃ³n
        if acierto:
            self._live_pnl_real += 0.9 * _mult
        else:
            self._live_pnl_real -= 1.0 * _mult

        if 'timestamp' not in ev:
            ev['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._live_ops.append(ev)
        self._live_all_ops.append({
            'skip':         _modo == 'SKIP',
            'acierto':      acierto,
            # En el flujo live `acierto` representa "mayor ganÃ³" (ver
            # _acierto_ajustado y balance real mÃ¡s arriba), asÃ­ que coincide
            # con gano_mayoria. Lo exponemos explÃ­cito para que curva_pnl_ep
            # y _ep_rolling_dir usen una seÃ±al canÃ³nica comÃºn.
            'gano_mayoria': acierto,
            'rango':        ev['rango'],
            'modo':         _modo,
            'wr':           _wr,
            'est':          ev['est'],
            'acel':         ev['acel'],
            'mult':         float(self._apuesta_base_var.get()),
            'timestamp':    ev.get('timestamp'),
        })
        if _modo == 'SKIP':
            marca, tag_r, pnl_r = 'â—Œ SKIP',    'dim',       0.0
        elif acierto:
            marca, tag_r, pnl_r = 'V ACIERTO', 'resultado', +0.9 * _mult
        else:
            marca, tag_r, pnl_r = 'X FALLO',   'perdida',   -1.0 * _mult

        # ðŸ“‹ LOGGING DETALLADO para anÃ¡lisis
        _log_line = (
            f"[RESULTADO] rango={ev['rango']:>6} | winner={ev['winner']:>5} | "
            f"mayor={ev['mayor']:>5} | wr={_wr:>5.1f}% | modo={_modo:>7} | "
            f"acierto={str(acierto):>5} | pnl={pnl_r:>+4.1f} | balance_total={self._live_pnl:>+7.1f}"
        )
        self._tick_log_append(f"{_log_line}\n", 'dim')
        try:
            with open('pnl_apuestas.log', 'a', encoding='utf-8') as f:
                f.write(_log_line + '\n')
        except:
            pass

        # ðŸ”Š ANUNCIO POR VOZ â€” filtro 11 (MAYORÃA PERDEDORA) tiene mensaje propio
        _sel_idx, _ = self._get_filtro_state()
        _es_filtro11 = (_sel_idx == 11)
        _voz_rango  = ev['rango']
        _voz_winner = "Azul" if ev['winner'].lower() == 'azul' else "Rojo"
        _voz_mayor  = "Azul" if ev['mayor'].lower() == 'azul' else "Rojo"

        if _es_filtro11:
            _ult_n = self._live_all_ops[-10:] if len(self._live_all_ops) >= 10 else (self._live_all_ops or [ev])
            _n_mayoria_gano = sum(1 for o in _ult_n if o['acierto'])
            _n_mayoria_perdio = len(_ult_n) - _n_mayoria_gano
            _lado_menos = "Rojo" if ev['mayor'].lower() == 'azul' else "Azul"
            if _modo == 'SKIP':
                _voz_msg = (f"MayorÃ­a perdedora: mayorÃ­a ganÃ³ {_n_mayoria_gano} de las Ãºltimas {len(_ult_n)} rondas. "
                            f"No hay tendencia clara, no se apuesta.")
            else:
                _voz_resultado = "Ganado" if (acierto if _modo != 'INVERSO' else not acierto) else "Perdido"
                _voz_msg = (f"MayorÃ­a perdedora: mayorÃ­a lleva {_n_mayoria_perdio} rondas perdidas. "
                            f"El lado con menos dinero es {_lado_menos}. Apuesta {_voz_resultado}.")
        elif _modo == 'SKIP':
            _voz_msg = f"Rango {_voz_rango}, ganÃ³ {_voz_winner}, Skip"
        else:
            _voz_resultado = "Ganado" if (acierto if _modo != 'INVERSO' else not acierto) else "Perdido"
            _voz_msg = (
                f"Rango {_voz_rango}, ganÃ³ {_voz_winner}, mayorÃ­a {_voz_mayor}, "
                f"modo {_modo}, apostamos {_voz_resultado}"
            )
        self._tick_log_append(
            f"{'â”€'*22}\n{marca}  {ev['winner'].upper()} ganÃ³  {pnl_r:+.1f}\n{'â”€'*22}\n",
            tag_r)
        self.guardar_historico()
        self._completar_decision_resultado(ev)
        self._on_resultado(self._live_all_ops)
        self.actualizar_ui()

    def _tick_timer(self):
        """Actualiza el contador de tiempo de sesiÃ³n cada segundo."""
        if self._live_inicio_sesion:
            delta = datetime.datetime.now() - self._live_inicio_sesion
            total = int(delta.total_seconds())
            h, rem = divmod(total, 3600)
            m, s   = divmod(rem, 60)
            txt = f"SesiÃ³n: {h:02d}:{m:02d}:{s:02d}"
        else:
            txt = "SesiÃ³n: --:--:--"
        try:
            self._lbl_session_timer.config(text=txt)
        except Exception:
            return   # widget destruido: no reprogramar
        self.after(1000, self._tick_timer)

    def _ep_rolling_dir(self, min_wr_dir=0, ventana=20, umbral=53.2):
        """
        Calcula la direcciÃ³n EP usando ventana rolling sobre _live_all_ops.
        Devuelve (ep_dir, pasa, motivo):
          ep_dir  : 'DIRECTO' | 'INVERSO' | 'SKIP' | 'OBS'
          pasa    : True si hay que apostar
          motivo  : texto corto para el log
        """
        hist = self._live_all_ops
        # OBS: exigir que se hayan jugado `ventana` rondas EN ESTA SESIÃ“N
        # (no conta el historico cargado al arrancar)
        n_sesion = self._ep_session_ops
        if n_sesion < ventana:
            return 'OBS', False, f'EP obs ({n_sesion}/{ventana})'
        # WR: ventana rolling sobre las Ãºltimas `ventana` ops (puede incluir historico).
        # SeÃ±al canÃ³nica = gano_mayoria (resultado objetivo, independiente del modo).
        # Fallback a 'acierto' por compatibilidad con histÃ³ricos sin el campo
        # (en live ambos coinciden, ver _on_resultado_ev).
        n = min(len(hist), ventana)
        recientes = hist[-n:]
        wr_rolling = sum(1 for o in recientes
                         if o.get('gano_mayoria', o.get('acierto'))) / n * 100
        if wr_rolling >= umbral:
            ep_dir = 'DIRECTO'
        elif wr_rolling <= (100 - umbral):
            ep_dir = 'INVERSO'
        else:
            return 'SKIP', False, f'EP zona neutra ({wr_rolling:.1f}%)'
        # Filtro de calidad WR individual
        if min_wr_dir > 0:
            wr_op = recientes[-1].get('wr', 50) if recientes else 50
            if ep_dir == 'DIRECTO' and wr_op < min_wr_dir:
                return ep_dir, False, f'WR indiv {wr_op:.0f}%<{min_wr_dir}'
            if ep_dir == 'INVERSO' and wr_op > (100 - min_wr_dir):
                return ep_dir, False, f'WR indiv {wr_op:.0f}%>{100-min_wr_dir}'
        return ep_dir, True, ''

    def _calcular_senal(self, mayor, wr, rango, est, acel):
        """Calcula la seÃ±al de apuesta aplicando el filtro activo."""
        selected_filter, filtro_pnl_positivo = self._get_filtro_state()

        # â”€â”€ Solo INVERSO: siempre a la contra, sin condiciones â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if selected_filter == 2:
            modo = 'INVERSO'
            color_apuesta = 'ROJO' if mayor == 'AZUL' else 'AZUL'
            col = '#2B7FFF' if color_apuesta == 'AZUL' else C['red']
            nombre_filtro = FILTROS_CURVA[selected_filter][0]
            self._lbl_senal.config(text=nombre_filtro[:18], fg=C['accent'])
            self._lbl_apuesta.config(text=f"APOSTAR {color_apuesta}", fg=col)
            self._btn_apostar.config(state='normal',
                                      bg='#2B7FFF' if color_apuesta == 'AZUL' else C['red'],
                                      fg=C['white'])
            self._color_apuesta_actual = color_apuesta
            return

        # Filtros que NO dependen de wr_actual (usan stats por rango / rolling EP).
        # Se evalÃºan antes del corte por wr para que la seÃ±al exista tambiÃ©n en zona neutra.
        _entry_pre = FILTROS_CURVA[selected_filter]
        _, _, _filtro_pre, _ = _entry_pre[:4]
        if _filtro_pre == 'EP_UMBRAL':
            # Resetear color EP y rÃ©gimen de la ronda anterior (si los hubiera).
            self._ep_umbral_color_ep = None
            self._ep_umbral_regimen  = ''
            # Bloqueo por rango (rangos perdedores deshabilitados desde Sheets)
            if rango in getattr(self, '_ep_rangos_bloqueados', set()):
                self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
                self._lbl_apuesta.config(text=f"SKIP rango bloq {rango}", fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                self._ep_umbral_regimen    = f'BLOQ_RANGO {rango}'
                self._color_apuesta_actual = mayor if mayor in ('AZUL', 'ROJO') else None
                return
            # Umbral especÃ­fico por rango si estÃ¡ definido, si no usar el global.
            _umbral_global = getattr(self, '_ep_umbral_min', EP_UMBRAL_MIN)
            _umbral = getattr(self, '_ep_umbral_por_rango', {}).get(rango, _umbral_global)
            _wr_d = self._get_ep_wr(rango, 'DIRECTO') if self._get_ep_wr else None
            _wr_i = self._get_ep_wr(rango, 'INVERSO') if self._get_ep_wr else None
            _wr_d = _wr_d if (_wr_d is not None and _wr_d >= _umbral) else None
            _wr_i = _wr_i if (_wr_i is not None and _wr_i >= _umbral) else None
            if _wr_d is None and _wr_i is None:
                self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
                self._lbl_apuesta.config(text="SKIP", fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                # Setear rÃ©gimen y color por defecto para que el registro de
                # la decisiÃ³n no quede con ep_gate='' ni color_apostado=None.
                self._ep_umbral_regimen    = 'NO_SIGNAL'
                self._color_apuesta_actual = mayor if mayor in ('AZUL', 'ROJO') else None
                return
            if (_wr_d or 0) >= (_wr_i or 0):
                color_apuesta = mayor                                            # DIRECTO
            else:
                color_apuesta = 'ROJO' if mayor == 'AZUL' else 'AZUL'           # INVERSO
            # Guardar el color que el filtro EP UMBRAL puro habrÃ­a apostado;
            # se usa al cierre de la ronda para alimentar la ventana adaptativa.
            self._ep_umbral_color_ep = color_apuesta
            # â”€â”€ RÃ©gimen adaptativo: ajustar la decisiÃ³n segÃºn WR rolling â”€â”€
            _vent = self._ep_umbral_outcomes
            nombre_filtro = FILTROS_CURVA[selected_filter][0]
            if len(_vent) < self._ep_umbral_warmup:
                # Warmup: SKIP sin apostar (sÃ³lo se observarÃ¡ el outcome al cierre)
                self._ep_umbral_regimen = f'WARMUP {len(_vent)}/{self._ep_umbral_warmup}'
                self._lbl_senal.config(text=nombre_filtro[:18], fg=C['muted'])
                self._lbl_apuesta.config(
                    text=f"warmup {len(_vent)}/{self._ep_umbral_warmup}",
                    fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                self._color_apuesta_actual = color_apuesta   # guardar p/ outcome al cierre
                return
            _wr_reg = sum(_vent) / len(_vent)
            # modo base SKIP cuando wr cae en zona neutra (40-60). En ese caso
            # NO permitimos que el rÃ©gimen ANTI dispare apuesta â€” respetamos SKIP.
            _modo_base_skip = (not self._is_base_mode()) and (40 < (wr or 50) < 60)
            if _wr_reg < self._ep_umbral_lo:
                if _modo_base_skip:
                    self._ep_umbral_regimen = f'ANTI {_wr_reg*100:.0f}% (SKIP base)'
                    self._lbl_senal.config(text=nombre_filtro[:18], fg=C['muted'])
                    self._lbl_apuesta.config(
                        text=f"SKIP base + ANTI {_wr_reg*100:.0f}%", fg=C['warn'])
                    self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                    self._color_apuesta_actual = color_apuesta
                    return
                # Anti-EP: invertir la apuesta
                color_apuesta = 'ROJO' if color_apuesta == 'AZUL' else 'AZUL'
                _etiq_modo = 'ANTI'
                self._ep_umbral_regimen = f'ANTI {_wr_reg*100:.0f}%'
            elif _wr_reg > self._ep_umbral_hi:
                _etiq_modo = 'EP'
                self._ep_umbral_regimen = f'EP {_wr_reg*100:.0f}%'
            else:
                # Zona neutra: SKIP rÃ©gimen
                self._ep_umbral_regimen = f'SKIP_REG {_wr_reg*100:.0f}%'
                self._lbl_senal.config(text=nombre_filtro[:18], fg=C['muted'])
                self._lbl_apuesta.config(
                    text=f"SKIP rÃ©gimen {_wr_reg*100:.0f}%", fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                self._color_apuesta_actual = color_apuesta
                return
            col = '#2B7FFF' if color_apuesta == 'AZUL' else C['red']
            self._lbl_senal.config(
                text=f"{nombre_filtro[:14]} ({_etiq_modo})", fg=C['accent'])
            self._lbl_apuesta.config(text=f"APOSTAR {color_apuesta}", fg=col)
            self._btn_apostar.config(state='normal',
                                      bg='#2B7FFF' if color_apuesta == 'AZUL' else C['red'],
                                      fg=C['white'])
            self._color_apuesta_actual = color_apuesta
            return

        # Determinar modo segun WR del tick actual
        if wr >= 60:
            modo = 'DIRECTO'
        elif wr <= 40:
            modo = 'INVERSO'
        else:
            self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
            self._lbl_apuesta.config(text="SKIP", fg=C['muted'])
            self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
            self._color_apuesta_actual = None
            return

        op = {'skip': False, 'modo': modo, 'wr': wr,
              'rango': rango, 'est': est, 'acel': acel}

        _entry_cs = FILTROS_CURVA[selected_filter]
        _, _, filtro, es_contrario = _entry_cs[:4]

        # EP ADAPTATIVO y EP+WR70: ventana rolling real sobre histÃ³rico live
        if filtro is None:
            ep_dir, pasa, motivo = self._ep_rolling_dir()
            if not pasa:
                txt = motivo if ep_dir == 'OBS' else 'SKIP'
                self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
                self._lbl_apuesta.config(text=txt, fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                self._color_apuesta_actual = ep_dir   # 'OBS' o 'SKIP'
                return
            modo = ep_dir
        elif filtro == 'EP_WR70':
            ep_dir, pasa, motivo = self._ep_rolling_dir(min_wr_dir=70)
            if not pasa:
                txt = motivo if ep_dir == 'OBS' else 'SKIP'
                self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
                self._lbl_apuesta.config(text=txt, fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                self._color_apuesta_actual = ep_dir
                return
            modo = ep_dir
        elif filtro == 'EP_UMBRAL':
            # Buscar mejor modo para el rango actual en hist+live (misma lÃ³gica que FASES EP)
            _EP_UMBRAL_MIN = 53.2
            _wr_d = self._get_ep_wr(rango, 'DIRECTO') if self._get_ep_wr else None
            _wr_i = self._get_ep_wr(rango, 'INVERSO') if self._get_ep_wr else None
            _wr_d = _wr_d if (_wr_d is not None and _wr_d >= _EP_UMBRAL_MIN) else None
            _wr_i = _wr_i if (_wr_i is not None and _wr_i >= _EP_UMBRAL_MIN) else None
            if _wr_d is None and _wr_i is None:
                self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
                self._lbl_apuesta.config(text="SKIP", fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                self._color_apuesta_actual = 'SKIP'
                return
            # Elegir el modo con mayor WR
            if (_wr_d or 0) >= (_wr_i or 0):
                modo = 'DIRECTO'
            else:
                modo = 'INVERSO'
        elif filtro == 'BAL_FILTRO':
            # BAL_FILTRO es registro real, no seÃ±al → SKIP
            self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
            self._lbl_apuesta.config(text="SKIP", fg=C['warn'])
            self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
            self._color_apuesta_actual = 'SKIP'
            return
        else:
            if not filtro(op):
                self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
                self._lbl_apuesta.config(text="SKIP", fg=C['warn'])
                self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
                self._color_apuesta_actual = 'SKIP'
                # Filtro 11 (MAYORÃA PERDEDORA): log detallado del motivo
                if selected_filter == 11:
                    _ult_n = self._live_all_ops[-10:] if len(self._live_all_ops) >= 10 else self._live_all_ops
                    _n_gan = sum(1 for o in _ult_n if o['acierto'])
                    _wr_10 = round(_n_gan / len(_ult_n) * 100, 1) if _ult_n else 0
                    self._tick_log_append(
                        f"[FILTRO 11] MayorÃ­a ganÃ³ {_n_gan}/{len(_ult_n)} ({_wr_10}%) â€” "
                        f"requiere <40% → SKIP\n", 'muted')
                return

        # Descartar si el filtro tiene PNL negativo en histÃ³rico
        if not filtro_pnl_positivo:
            self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
            self._lbl_apuesta.config(text="SKIP", fg=C['warn'])
            self._btn_apostar.config(state='disabled', bg='#1A1A2A', fg=C['muted'])
            self._color_apuesta_actual = None
            return

        # Calcular color a apostar
        if modo == 'DIRECTO':
            color_apuesta = mayor
        else:
            color_apuesta = 'ROJO' if mayor == 'AZUL' else 'AZUL'

        if es_contrario:
            color_apuesta = 'ROJO' if color_apuesta == 'AZUL' else 'AZUL'

        col = '#2B7FFF' if color_apuesta == 'AZUL' else C['red']
        nombre_filtro = FILTROS_CURVA[selected_filter][0]
        self._lbl_senal.config(text=nombre_filtro[:18], fg=C['accent'])
        self._lbl_apuesta.config(text=f"APOSTAR {color_apuesta}", fg=col)
        self._btn_apostar.config(state='normal',
                                  bg='#2B7FFF' if color_apuesta == 'AZUL' else C['red'],
                                  fg=C['white'])
        self._color_apuesta_actual = color_apuesta

    def _on_tick(self, ev):
        mayor = ev['mayor']
        col_tag = 'azul' if mayor == 'AZUL' else 'rojo'
        col = '#2B7FFF' if mayor == 'AZUL' else C['red']
        self._lbl_live_prev.config(text=mayor, fg=col)
        self._lbl_live_pb.config(text=f"B: {ev['p_b']}%")
        self._lbl_live_pr.config(text=f"R: {ev['p_r']}%")
        tick_n = ev.get('tick_n', 0)
        dif = ev['dif']

        # DespuÃ©s de apostar ignorar ticks restantes (seÃ±al ya comprometida)
        if self._apuesta_enviada:
            return

        linea     = f"T{tick_n:02d}  B{ev['p_b']:5.1f}% R{ev['p_r']:5.1f}%  Î”{dif:4.1f}  "
        extra_tag = None
        sufijo    = f"{mayor}\n"

        # SeÃ±al: solo en tick 35 (datos estables y tiempo suficiente para apostar)
        if tick_n == 35:
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # â”€â”€ MODO BASE: ciclo COMPLETAMENTE separado de filtros â”€â”€â”€â”€â”€â”€â”€
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            if self._is_base_mode():
                _base_c = self._base_color(mayor)
                _etiq   = 'BASE INV' if self._base_inv_activo() else 'BASE'
                col_b   = '#2B7FFF' if _base_c == 'AZUL' else C['red']
                self._lbl_senal.config(text=_etiq, fg=C['accent'])
                self._lbl_apuesta.config(text=f"APOSTAR {_base_c}", fg=col_b)
                self._btn_apostar.config(state='normal',
                                          bg='#2B7FFF' if _base_c == 'AZUL' else C['red'],
                                          fg=C['white'])
                self._color_apuesta_actual = _base_c

                decision       = 'SKIP'
                color_apostado = _base_c
                ep_gate_txt    = _etiq

                if self._apuesta_auto_var.get():
                    decision              = 'APOSTADA'
                    self._apuesta_enviada = True
                    _ap = Path(__file__).parent / 'apuesta.py'
                    self._tick_log_append(
                        f"[{_etiq}] mayorÃ­a={mayor} → apuesta {_base_c}\n", 'warn')
                    try:
                        proc = subprocess.Popen(
                            ['py', str(_ap), _base_c, str(float(self._apuesta_base_var.get()))],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(_ap.parent))
                        self._tick_log_append(
                            f"[AUTO-APUESTA] {_base_c} â€” PID={proc.pid}\n", 'warn')
                        def _check_ap(p=proc):
                            ret = p.poll()
                            if ret is None:
                                self.after(500, _check_ap)
                            else:
                                err = p.stderr.read().decode(errors='replace').strip()
                                msg = (f"[AUTO-APUESTA] fin cod={ret}"
                                       + (f" ERR: {err}" if err else " OK") + "\n")
                                self._tick_log_append(msg, 'warn' if ret != 0 else 't33')
                        self.after(500, _check_ap)
                    except Exception as exc:
                        self._tick_log_append(f"[AUTO-APUESTA] ERROR: {exc}\n", 'warn')
                else:
                    ep_gate_txt = f'{_etiq} (auto-OFF)'

                self._set_conf(0)
                self._registrar_decision_t36(ev, decision, color_apostado, ep_gate_txt)
                self._tick_log_append(linea, 'muted')
                self._tick_log_append(sufijo, extra_tag or col_tag)
                return

            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            # â”€â”€ CICLO FILTROS (independiente de BASE) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
            _rango_t35 = calcular_rango(self._monitor._dif_t33)
            self._calcular_senal(
                mayor, self._monitor._wr_actual,
                _rango_t35,
                self._monitor._est,
                self._monitor._acel)
            if self._on_senal:
                sel, _ = self._get_filtro_state()
                nombre_f = FILTROS_CURVA[sel][0]
                self._on_senal(nombre_f, getattr(self, '_color_apuesta_actual', None))

            decision       = 'SKIP'
            color_apostado = None
            ep_gate_txt    = ''

            # â”€â”€ SOLO INVERSO: siempre a la contra, sin gate ni condiciones â”€â”€
            _sel_idx = self._get_filtro_state()[0]
            if self._apuesta_auto_var.get() and _sel_idx == 2:
                _inv_color = 'ROJO' if mayor == 'AZUL' else 'AZUL'
                decision       = 'APOSTADA'
                color_apostado = _inv_color
                ep_gate_txt    = 'SOLO INVERSO'
                self._apuesta_enviada = True
                _ap = Path(__file__).parent / 'apuesta.py'
                _ab_inv = round(float(self._apuesta_base_var.get()) * self._ep_mult_actual(_rango_t35, 'INVERSO'), 2)
                self._tick_log_append(
                    f"[SOLO INVERSO] mayorÃ­a={mayor} → apuesta {_inv_color}\n", 'warn')
                try:
                    proc = subprocess.Popen(
                        ['py', str(_ap), _inv_color, str(_ab_inv)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        cwd=str(_ap.parent))
                    self._tick_log_append(
                        f"[AUTO-APUESTA] {_inv_color} â€” PID={proc.pid}\n", 'warn')
                    def _check_ap(p=proc):
                        ret = p.poll()
                        if ret is None:
                            self.after(500, _check_ap)
                        else:
                            err = p.stderr.read().decode(errors='replace').strip()
                            msg = (f"[AUTO-APUESTA] fin cod={ret}"
                                   + (f" ERR: {err}" if err else " OK") + "\n")
                            self._tick_log_append(msg, 'warn' if ret != 0 else 't33')
                    self.after(500, _check_ap)
                except Exception as exc:
                    self._tick_log_append(f"[AUTO-APUESTA] ERROR: {exc}\n", 'warn')
                self._set_conf(self._calcular_conf())
                self._registrar_decision_t36(ev, decision, color_apostado, ep_gate_txt)
                self._tick_log_append(linea, 'muted')
                self._tick_log_append(sufijo, extra_tag or col_tag)
                return

            # â”€â”€ EP UMBRAL adaptativo: usar la decisiÃ³n del filtro (no el gate) â”€
            _filtro_sel_idx, _ = self._get_filtro_state()
            _filtro_sel_fn = FILTROS_CURVA[_filtro_sel_idx][2] if _filtro_sel_idx < len(FILTROS_CURVA) else None
            if (self._apuesta_auto_var.get() and _filtro_sel_fn == 'EP_UMBRAL'):
                color_adapt = getattr(self, '_color_apuesta_actual', None)
                # Solo apostar fÃ­sicamente cuando el rÃ©gimen es EP o ANTI;
                # NO en SKIP_REG / WARMUP / NO_SIGNAL aunque _color_apuesta_actual
                # estÃ© seteado (lo estÃ¡ para fines de display por mi Fix 1).
                _reg = self._ep_umbral_regimen or ''
                _reg_es_apuesta = _reg.startswith('EP ') or _reg.startswith('ANTI ')
                if (isinstance(color_adapt, str)
                        and color_adapt.upper() in ('AZUL', 'ROJO')
                        and _reg_es_apuesta):
                    color_adapt = color_adapt.upper()
                    decision       = 'APOSTADA'
                    color_apostado = color_adapt
                    ep_gate_txt    = self._ep_umbral_regimen
                    self._apuesta_enviada = True
                    _ap = Path(__file__).parent / 'apuesta.py'
                    _modo_adapt = 'DIRECTO' if color_adapt == mayor else 'INVERSO'
                    _ab = round(float(self._apuesta_base_var.get()) *
                                self._ep_mult_actual(_rango_t35, _modo_adapt), 2)
                    self._tick_log_append(
                        f"[EP UMBRAL {self._ep_umbral_regimen}] → apuesta {color_adapt}\n", 'warn')
                    try:
                        proc = subprocess.Popen(
                            ['py', str(_ap), color_adapt, str(_ab)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=str(_ap.parent))
                        self._tick_log_append(
                            f"[AUTO-APUESTA] {color_adapt} â€” PID={proc.pid}\n", 'warn')
                        def _check_ap_adapt(p=proc):
                            ret = p.poll()
                            if ret is None:
                                self.after(500, _check_ap_adapt)
                            else:
                                err = p.stderr.read().decode(errors='replace').strip()
                                msg = (f"[AUTO-APUESTA] fin cod={ret}"
                                       + (f" ERR: {err}" if err else " OK") + "\n")
                                self._tick_log_append(msg, 'warn' if ret != 0 else 't33')
                        self.after(500, _check_ap_adapt)
                    except Exception as exc:
                        self._tick_log_append(f"[AUTO-APUESTA] ERROR: {exc}\n", 'warn')
                else:
                    # SKIP (warmup, zona neutra, sin seÃ±al o color invÃ¡lido)
                    decision    = 'SKIP'
                    ep_gate_txt = self._ep_umbral_regimen or 'EP UMBRAL SKIP'
                    # DiagnÃ³stico: registrar POR QUÃ‰ no apostÃ³ cuando rÃ©gimen era EP/ANTI
                    if _reg_es_apuesta:
                        _diag_color  = repr(color_adapt)
                        _diag_isstr  = isinstance(color_adapt, str)
                        _diag_upper  = (color_adapt.upper() if _diag_isstr else 'N/A')
                        _diag_valid  = (_diag_isstr and _diag_upper in ('AZUL', 'ROJO'))
                        self._tick_log_append(
                            f"[EP UMBRAL SKIP-DIAG] reg='{_reg}' color={_diag_color} "
                            f"is_str={_diag_isstr} upper={_diag_upper} valid={_diag_valid}\n",
                            'warn')
                self._set_conf(self._calcular_conf())
                self._registrar_decision_t36(ev, decision, color_apostado, ep_gate_txt)
                self._tick_log_append(linea, 'muted')
                self._tick_log_append(sufijo, extra_tag or col_tag)
                return

            if self._apuesta_auto_var.get():
                _gate_on = self._ep_gate_activo()
                if not _gate_on:
                    color = getattr(self, '_color_apuesta_actual', None)
                    if color and color not in ('OBS', 'SKIP', None):
                        decision       = 'APOSTADA'
                        color_apostado = color
                        self._apuesta_enviada = True
                        _ap = Path(__file__).parent / 'apuesta.py'
                        _modo_goff = 'DIRECTO' if color == mayor else 'INVERSO'
                        _ab_goff = round(float(self._apuesta_base_var.get()) * self._ep_mult_actual(_rango_t35, _modo_goff), 2)
                        self._tick_log_append(
                            f"[GATE OFF] mayorÃ­a={mayor} → apuesta {color}\n", 'warn')
                        try:
                            proc = subprocess.Popen(
                                ['py', str(_ap), color, str(_ab_goff)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=str(_ap.parent))
                            self._tick_log_append(
                                f"[AUTO-APUESTA] {color} â€” PID={proc.pid}\n", 'warn')
                            def _check_ap(p=proc):
                                ret = p.poll()
                                if ret is None:
                                    self.after(500, _check_ap)
                                else:
                                    err = p.stderr.read().decode(errors='replace').strip()
                                    msg = (f"[AUTO-APUESTA] fin cod={ret}"
                                           + (f" ERR: {err}" if err else " OK") + "\n")
                                    self._tick_log_append(msg, 'warn' if ret != 0 else 't33')
                            self.after(500, _check_ap)
                        except Exception as exc:
                            self._tick_log_append(
                                f"[AUTO-APUESTA] ERROR: {exc}\n", 'warn')
                    else:
                        decision    = 'SKIP'
                else:
                    EP_VENTANA = 20
                    if self._ep_session_ops < EP_VENTANA:
                        decision    = 'OBS'
                        ep_gate_txt = f'OBS {self._ep_session_ops}/{EP_VENTANA}'
                        self._tick_log_append(
                            f"[BLOQUEO ACTIVO] EP obs {self._ep_session_ops}/{EP_VENTANA} â€” sin apuesta\n",
                            'warn')
                    else:
                        ep_dir, ep_pasa, ep_motivo = self._ep_rolling_dir(min_wr_dir=70)
                        if not ep_pasa:
                            decision    = 'SKIP'
                            ep_gate_txt = f'SKIP {ep_motivo or ep_dir}'.strip()
                            if ep_dir in ('DIRECTO', 'INVERSO'):
                                color_apostado = mayor if ep_dir == 'DIRECTO' else (
                                    'ROJO' if mayor == 'AZUL' else 'AZUL')
                            self._tick_log_append(
                                f"[EP GATE] bloqueado â€” {ep_motivo or ep_dir}\n", 'muted')
                        else:
                            color = mayor if ep_dir == 'DIRECTO' else (
                                'ROJO' if mayor == 'AZUL' else 'AZUL')
                            decision       = 'APOSTADA'
                            color_apostado = color
                            ep_gate_txt    = f'OK {ep_dir}'
                            self._apuesta_enviada = True
                            _ap = Path(__file__).parent / 'apuesta.py'
                            _ab_ep = round(float(self._apuesta_base_var.get()) * self._ep_mult_actual(_rango_t35, ep_dir), 2)
                            self._tick_log_append(
                                f"[EP→{ep_dir}] mayorÃ­a={mayor} → apuesta {color}\n", 'warn')
                            try:
                                proc = subprocess.Popen(
                                    ['py', str(_ap), color, str(_ab_ep)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=str(_ap.parent))
                                self._tick_log_append(
                                    f"[AUTO-APUESTA] {color} â€” PID={proc.pid}\n", 'warn')
                                def _check_ap(p=proc):
                                    ret = p.poll()
                                    if ret is None:
                                        self.after(500, _check_ap)
                                    else:
                                        err = p.stderr.read().decode(errors='replace').strip()
                                        msg = (f"[AUTO-APUESTA] fin cod={ret}"
                                               + (f" ERR: {err}" if err else " OK") + "\n")
                                        self._tick_log_append(msg, 'warn' if ret != 0 else 't33')
                                self.after(500, _check_ap)
                            except Exception as exc:
                                self._tick_log_append(
                                    f"[AUTO-APUESTA] ERROR: {exc}\n", 'warn')
            # Auto OFF o gate OFF: ep_gate_txt queda vacÃ­o (no se anota nada)

            self._set_conf(self._calcular_conf())
            self._registrar_decision_t36(ev, decision, color_apostado, ep_gate_txt)

        if tick_n == 25:
            extra_tag = 't25'
            sufijo = f"{mayor} â—„T25\n"
            _prep = Path(__file__).parent / 'preparar.py'
            try:
                proc = subprocess.Popen(
                    ['py', str(_prep)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=str(_prep.parent))
                self._tick_log_append(f"[PREPARAR] lanzado PID={proc.pid}\n", 'warn')
                def _check_prep(p=proc):
                    ret = p.poll()
                    if ret is None:
                        self.after(500, _check_prep)
                    else:
                        err = p.stderr.read().decode(errors='replace').strip()
                        msg = f"[PREPARAR] fin cod={ret}" + (f" ERR: {err}" if err else " OK") + "\n"
                        self._tick_log_append(msg, 'warn' if ret != 0 else 't25')
                self.after(500, _check_prep)
            except Exception as exc:
                self._tick_log_append(f"[PREPARAR] ERROR lanzando: {exc}\n", 'warn')
        elif tick_n == 33:
            extra_tag = 't33'
            sufijo = f"{mayor} â—„T33\n"
        elif tick_n == 35:
            extra_tag = 't33'
            sufijo = f"{mayor} â—„T35 APUESTA\n"
        self._tick_log_append(linea, 'muted')
        self._tick_log_append(sufijo, extra_tag or col_tag)

    def _on_ronda(self, ev):
        issue = ev.get('issue', '---')
        self._issue_actual = issue
        self._decision_actual_idx = None
        self._lbl_live_ronda.config(text=issue)
        self._lbl_live_prev.config(text="---", fg=C['muted'])
        self._lbl_live_pb.config(text="B: --%")
        self._lbl_live_pr.config(text="R: --%")
        self._lbl_senal.config(text="SEÃ‘AL", fg=C['muted'])
        self._lbl_apuesta.config(text="---", fg=C['muted'])
        self._tick_log_clear()
        self._tick_log_append(f"â”€â”€ RONDA {issue} â”€â”€\n", 'sep')
        self._monitor._tick = 0
        self._apuesta_enviada = False

        # Leer multiplicador y anchos de columna en background
        def _fetch_mult(self=self):
            try:
                from configurador import conectar_hojas
                sheet_lector, _ = conectar_hojas()
                spreadsheet = sheet_lector.spreadsheet

                # Multiplicador (Apuestas!B2)
                val = spreadsheet.worksheet("Apuestas").acell('B2').value
                nuevo = max(1, min(10, int(float(str(val).replace(',', '.')))))
                self._multiplicador_apuesta = nuevo
                self._tick_log_append(f"[MULT] multiplicador={nuevo}\n", 'muted')

                # Anchos de columna (COLUMNAS!A:C, secciÃ³n HISTORIAL)
                try:
                    ws_hist = spreadsheet.worksheet("COLUMNAS")
                    filas   = ws_hist.get_all_values()
                    widths  = {}
                    for fila in filas:
                        if (len(fila) >= 3
                                and str(fila[0]).strip().upper() == 'HISTORICO'
                                and fila[1] and fila[2]):
                            try:
                                widths[fila[1].strip()] = int(float(str(fila[2]).replace(',', '.')))
                            except ValueError:
                                pass
                    if widths:
                        self._tick_log_append(f"[HIST] {len(widths)} anchos leÃ­dos\n", 'muted')
                        # Aplicar en el hilo principal de Tkinter
                        self.after(0, lambda w=widths: self._aplicar_anchos_historico(w))
                except Exception as _eh:
                    self._tick_log_append(f"[HIST] error leyendo anchos: {_eh}\n", 'muted')

            except Exception as _e:
                self._tick_log_append(f"[MULT] error leyendo B2: {_e}\n", 'muted')
        threading.Thread(target=_fetch_mult, daemon=True).start()

    def _ejecutar_apuesta(self):
        color = getattr(self, '_color_apuesta_actual', '---')
        self._tick_log_append(f"[APUESTA PENDIENTE] {color}\n", 'warn')

    def _base_color(self, mayor):
        """Devuelve el color a apostar en modo BASE: mayorÃ­a o minorÃ­a si INV activo."""
        if self._base_inv_activo():
            return 'ROJO' if mayor == 'AZUL' else 'AZUL'
        return mayor

    def _is_base_mode(self):
        """BASE activo si: botÃ³n SOLO BASE pulsado O filtro activo es BASE (idx 0)."""
        try:
            if self._solo_base_activo():
                return True
            return self._get_filtro_state()[0] == 0
        except Exception:
            return False

    def _toggle_telegram(self):
        self._tg_activo = not self._tg_activo
        if self._tg_activo:
            self._btn_tg.config(text="TG ON", bg='#0A3320', fg='#00FF88')
        else:
            self._btn_tg.config(text="TG OFF", bg='#2A0A0A', fg='#FF4444')

    def _toggle_live(self):
        if self._monitor._running:
            self._monitor.detener()
            # Matar proceso de espera si sigue activo
            if self._proc_espera and self._proc_espera.poll() is None:
                try:
                    self._proc_espera.terminate()
                except Exception:
                    pass
            self._proc_espera       = None
            self._bloqueando_inicio = False
            self._btn_live.config(text="â—„  CONECTAR", bg='#0A2A10', fg=C['accent2'])
            self._lbl_live_status.config(text='DESCONECTADO', fg=C['accent3'])
        else:
            from pnl_live import LiveMonitor
            # â”€â”€ Lanzar proceso externo que espera una ronda completa â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
            _script = Path(__file__).parent / 'esperar_ronda.py'
            try:
                self._proc_espera = subprocess.Popen(
                    ['py', str(_script)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=str(_script.parent))
                self._bloqueando_inicio = True
                self._lbl_live_status.config(text='â³ ESPERANDO RONDA INICIAL...', fg=C['warn'])
            except Exception:
                self._proc_espera       = None
                self._bloqueando_inicio = False
            # Iniciar el monitor websocket normalmente
            self._monitor = LiveMonitor(self._monitor._q)
            self._monitor.iniciar()
            self._btn_live.config(text="â–   DESCONECTAR", bg='#2A0A0A', fg=C['accent3'])

    def _reset_live_action(self):
        self.reset_all()

    def _reset_balance_action(self):
        self.reset_balance()

    def _tick_log_append(self, texto, tag=None):
        self._tick_log.config(state='normal')
        if tag:
            self._tick_log.insert('end', texto, tag)
        else:
            self._tick_log.insert('end', texto)
        self._tick_log.see('end')
        self._tick_log.config(state='disabled')

    def _tick_log_clear(self):
        self._tick_log.config(state='normal')
        self._tick_log.delete('1.0', 'end')
        self._tick_log.config(state='disabled')

    # â”€â”€ Construccion interna â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _construir(self):
        lf = tk.Frame(self, bg=C['panel'], bd=1, relief='solid')
        lf.pack(fill='both', expand=True)

        # â”€â”€ Header â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        hdr = tk.Frame(lf, bg=C['panel'])
        hdr.pack(fill='x', padx=10, pady=(8, 2))
        tk.Label(hdr, text="LIVE WEBSOCKET", font=FONT_TITLE,
                 bg=C['panel'], fg=C['accent']).pack(side='left')
        self._btn_live = tk.Button(hdr, text="â—„  CONECTAR", font=('Consolas', 11, 'bold'),
                                   bg='#0A2A10', fg=C['accent2'], relief='flat', cursor='hand2',
                                   padx=8, pady=2, command=self._toggle_live)
        self._btn_live.pack(side='right')
        tk.Button(hdr, text="RESET", font=('Consolas', 10), bg=C['border'],
                  fg=C['muted'], relief='flat', cursor='hand2', padx=6,
                  command=self._reset_live_action).pack(side='right', padx=(0, 6))
        self._btn_tg = tk.Button(hdr, text="TG ON", font=('Consolas', 10, 'bold'),
                                 bg='#0A3320', fg='#00FF88', relief='flat', cursor='hand2',
                                 padx=6, command=self._toggle_telegram)
        self._btn_tg.pack(side='right', padx=(0, 6))

        tk.Frame(lf, bg=C['border'], height=1).pack(fill='x', padx=10, pady=(2, 4))

        # â”€â”€ Dos columnas internas â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        body = tk.Frame(lf, bg=C['panel'])
        body.pack(fill='both', expand=True, padx=6, pady=(0, 6))

        # Columna izquierda: estado, prevision, stats, rondas
        left = tk.Frame(body, bg=C['panel'], width=230)
        left.pack(side='left', fill='both', padx=(0, 4))
        left.pack_propagate(False)

        self._lbl_live_status = tk.Label(left, text="DESCONECTADO", font=FONT_MONO_B,
                                          bg=C['panel'], fg=C['accent3'])
        self._lbl_live_status.pack(pady=(0, 2))

        ronda_f = tk.Frame(left, bg=C['panel'])
        ronda_f.pack(fill='x')
        tk.Label(ronda_f, text="RONDA:", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(side='left')
        self._lbl_live_ronda = tk.Label(ronda_f, text="---", font=('Consolas', 10),
                                         bg=C['panel'], fg=C['text'])
        self._lbl_live_ronda.pack(side='left', padx=4)

        # SeÃ±al de apuesta
        tk.Frame(left, bg=C['border'], height=1).pack(fill='x', pady=(6, 2))
        self._lbl_senal = tk.Label(left, text="SEÃ‘AL", font=('Consolas', 13, 'bold'),
                                    bg=C['panel'], fg=C['muted'])
        self._lbl_senal.pack()
        self._lbl_apuesta = tk.Label(left, text="---", font=('Consolas', 16, 'bold'),
                                      bg=C['panel'], fg=C['muted'])
        self._lbl_apuesta.pack()

        self._btn_apostar = tk.Button(left, text="APOSTAR", font=('Consolas', 13, 'bold'),
                                       bg='#1A1A2A', fg=C['muted'], relief='flat',
                                       cursor='hand2', padx=8, pady=4,
                                       state='disabled', command=self._ejecutar_apuesta)
        self._btn_apostar.pack(fill='x', pady=(4, 0))

        # Apuesta base
        _ab_frame = tk.Frame(left, bg=C['panel'])
        _ab_frame.pack(fill='x', pady=(4, 0))
        tk.Label(_ab_frame, text="APUESTA BASE:", font=('Consolas', 9, 'bold'),
                 bg=C['panel'], fg=C['muted']).pack(side='left')
        _ab_om = tk.OptionMenu(_ab_frame, self._apuesta_base_var,
                               *self._ESCALADO_VALORES)
        _ab_om.config(bg='#0D2137', fg=C['accent'], font=('Consolas', 10),
                      relief='flat', highlightthickness=0,
                      activebackground='#1A3050', activeforeground=C['accent2'])
        _ab_om.pack(side='right')

        self._lbl_validacion = tk.Label(left, text="", font=('Consolas', 8),
                                         bg=C['panel'], fg=C['muted'], anchor='w', justify='left',
                                         wraplength=180)
        self._lbl_validacion.pack(fill='x', pady=(2, 0))
        try:
            self._apuesta_base_var.trace_add('write', self._on_cambio_apuesta_base)
        except Exception:
            self._apuesta_base_var.trace('w', self._on_cambio_apuesta_base)
        self.after(200, self._refrescar_label_validacion)

        tk.Checkbutton(left, text="APUESTA AUTO", font=('Consolas', 10, 'bold'),
                       variable=self._apuesta_auto_var,
                       bg=C['panel'], fg=C['warn'], selectcolor='#1A1A2A',
                       activebackground=C['panel'], activeforeground=C['warn'],
                       cursor='hand2').pack(fill='x', pady=(3, 0))

        # Confianza pre-apuesta 1-8
        tk.Label(left, text="CONF PRE-APUESTA", font=('Consolas', 9),
                 bg=C['panel'], fg=C['muted']).pack(anchor='w', pady=(3, 0))
        conf_f = tk.Frame(left, bg=C['panel'])
        conf_f.pack(fill='x')
        self._conf_btns = []
        for v in range(1, 9):
            b = tk.Button(conf_f, text=str(v), font=('Consolas', 10, 'bold'),
                          bg=C['border'], fg=C['muted'], relief='flat',
                          cursor='hand2', command=lambda x=v: self._set_conf(x))
            b.pack(side='left', fill='x', expand=True)
            self._conf_btns.append(b)
        self._set_conf(4)   # resaltar valor inicial

        tk.Frame(left, bg=C['border'], height=1).pack(fill='x', pady=(4, 4))

        tk.Label(left, text="MAYORÃA", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(pady=(0, 0))
        self._lbl_live_prev = tk.Label(left, text="---", font=('Consolas', 22, 'bold'),
                                        bg=C['panel'], fg=C['muted'])
        self._lbl_live_prev.pack()

        bar_f = tk.Frame(left, bg=C['panel'])
        bar_f.pack(fill='x', pady=(2, 0))
        self._lbl_live_pb = tk.Label(bar_f, text="B: --%", font=FONT_MONO_B,
                                      bg=C['panel'], fg='#2B7FFF', anchor='w')
        self._lbl_live_pb.pack(side='left')
        self._lbl_live_pr = tk.Label(bar_f, text="R: --%", font=FONT_MONO_B,
                                      bg=C['panel'], fg=C['red'], anchor='e')
        self._lbl_live_pr.pack(side='right')

        tk.Frame(left, bg=C['border'], height=1).pack(fill='x', pady=4)

        self._lbl_live_pnl = tk.Label(left, text="+0.00 EUR", font=('Consolas', 18, 'bold'),
                                       bg=C['panel'], fg=C['accent2'])
        self._lbl_live_pnl.pack()

        stats_f = tk.Frame(left, bg=C['panel'])
        stats_f.pack(fill='x', pady=2)
        self._lbl_live_ops   = tk.Label(stats_f, text="Ops:    0",    font=FONT_SM, bg=C['panel'], fg=C['text'], anchor='w')
        self._lbl_live_ops.pack(fill='x')
        self._lbl_live_wr    = tk.Label(stats_f, text="WR:     --%",  font=FONT_SM, bg=C['panel'], fg=C['text'], anchor='w')
        self._lbl_live_wr.pack(fill='x')
        self._lbl_live_ratio = tk.Label(stats_f, text="PNL/op: ---",  font=FONT_SM, bg=C['panel'], fg=C['text'], anchor='w')
        self._lbl_live_ratio.pack(fill='x')
        self._lbl_session_timer = tk.Label(stats_f, text="SesiÃ³n: --:--:--",
                                            font=FONT_SM, bg=C['panel'], fg=C['accent'], anchor='w')
        self._lbl_session_timer.pack(fill='x')

        tk.Button(left, text="RESET BALANCE", font=('Consolas', 10), bg='#2A1A00',
                  fg=C['warn'], relief='flat', cursor='hand2',
                  command=self._reset_balance_action).pack(fill='x', pady=(4, 0))

        tk.Frame(left, bg=C['border'], height=1).pack(fill='x', pady=4)

        tk.Label(left, text="ÃšTIMAS RONDAS", font=('Consolas', 10, 'bold'),
                 bg=C['panel'], fg=C['muted']).pack(anchor='w')
        rows_f = tk.Frame(left, bg=C['panel'])
        rows_f.pack(fill='x', pady=2)
        for _ in range(14):
            row = tk.Frame(rows_f, bg=C['panel'])
            row.pack(fill='x', pady=0)
            lbls = []
            for w, anchor in [(3, 'w'), (5, 'w'), (5, 'w'), (5, 'w'), (5, 'e')]:
                l = tk.Label(row, text="", font=('Consolas', 10),
                             bg=C['panel'], fg=C['text'], width=w, anchor=anchor)
                l.pack(side='left')
                lbls.append(l)
            self._live_rows.append(lbls)

        # Columna derecha: log de ticks
        right = tk.Frame(body, bg=C['panel'])
        right.pack(side='left', fill='both', expand=True)

        tk.Label(right, text="TICKS EN VIVO", font=('Consolas', 10, 'bold'),
                 bg=C['panel'], fg=C['muted']).pack(anchor='w', pady=(0, 2))

        self._tick_log = tk.Text(right, bg='#020810', fg=C['text'],
                                  font=('Consolas', 10), state='disabled',
                                  relief='flat', wrap='none')
        self._tick_log.pack(fill='both', expand=True)

        # Tags de colores para el log
        self._tick_log.tag_config('azul',      foreground='#2B7FFF')
        self._tick_log.tag_config('rojo',      foreground=C['red'])
        self._tick_log.tag_config('t25',       foreground=C['warn'])
        self._tick_log.tag_config('t33',       foreground='#8B5CF6')
        self._tick_log.tag_config('resultado', foreground=C['accent2'])
        self._tick_log.tag_config('perdida',   foreground=C['accent3'])
        self._tick_log.tag_config('sep',       foreground=C['border'])
        self._tick_log.tag_config('muted',     foreground=C['muted'])
        self._tick_log.tag_config('warn',      foreground=C['warn'])
        self._tick_log.tag_config('dim',       foreground='#2A3A4A')

        # Arrancar timer de sesiÃ³n (siempre activo desde inicio)
        self.after(1000, self._tick_timer)


class HistoricoApuestasPanel(tk.Frame):
    """Panel scrollable con histÃ³rico de apuestas, solo rondas con delta â‰  0."""

    H_COLS = [
        ('_dot',   '',       30,  'c'),
        ('idx',    '#',      50,  'c'),
        ('issue',  'Ronda', 110,  'c'),
        ('filtro', 'Filtro', 180, 'l'),
        ('delta',  'Î”',      70,  'c'),
        ('saldo',  'Saldo',  90,  'c'),
    ]
    ROW_H  = 26
    HDR_H  = 30
    DOT_R  = 6
    SEP_CLR  = '#1A3050'
    HDR_BG   = '#060F1E'
    HDR_FG   = '#00D4FF'
    FONT_HDR = ('Consolas', 10, 'bold')
    FONT_ROW = ('Consolas', 10)
    FONT_ROWB = ('Consolas', 10, 'bold')

    def __init__(self, parent):
        super().__init__(parent, bg=C['panel'], bd=1, relief='solid')
        tk.Label(self, text="HISTÃ“RICO APOSTADAS", font=('Consolas', 10, 'bold'),
                 bg=C['panel'], fg=C['accent']).pack(anchor='w', padx=8, pady=4)
        self._col_defs = self.H_COLS
        self.TOTAL_W = sum(c[2] for c in self._col_defs)
        cf = tk.Frame(self, bg=C['panel'])
        cf.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        self._cv = tk.Canvas(cf, bg='#07101E', highlightthickness=0,
                             width=self.TOTAL_W, height=400)
        vsb = tk.Scrollbar(cf, orient='vertical', command=self._cv.yview)
        self._cv.configure(yscrollcommand=vsb.set)
        self._cv.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self._cv.bind('<MouseWheel>', self._on_scroll)
        self._cv.bind('<Button-4>', self._on_scroll)
        self._cv.bind('<Button-5>', self._on_scroll)
        self._dibujar_header()

    def _on_scroll(self, ev):
        delta = -1 if (getattr(ev, 'num', 0) == 5 or getattr(ev, 'delta', 0) < 0) else 1
        self._cv.yview_scroll(delta, 'units')

    def _dibujar_header(self):
        self._cv.delete('header')
        x = 0
        self._cv.create_rectangle(0, 0, self.TOTAL_W, self.HDR_H,
                                   fill=self.HDR_BG, outline='', tags='header')
        for key, label, col_w, align in self._col_defs:
            if label:
                tx = x + col_w // 2
                self._cv.create_text(tx, self.HDR_H // 2, text=label,
                                      fill=self.HDR_FG, font=self.FONT_HDR,
                                      anchor='center', tags='header')
            self._cv.create_line(x + col_w, 0, x + col_w, self.HDR_H,
                                  fill=self.SEP_CLR, width=1, tags='header')
            x += col_w
        self._cv.create_line(0, self.HDR_H, self.TOTAL_W, self.HDR_H,
                              fill='#00D4FF', width=1, tags='header')

    def refrescar(self, decisiones, balance_inicial=0.0):
        self._cv.delete('row')
        rows = []
        for d in decisiones:
            if d.get('decision') != 'APOSTADA':
                continue
            pnl = d.get('pnl')
            if pnl is None or float(pnl) == 0:
                continue
            rows.append((d, float(pnl)))
        n = len(rows)
        saldos_acum = []
        saldo = balance_inicial
        for d, delta in rows:
            saldo += delta
            saldos_acum.append(saldo)
        total_h = self.HDR_H + n * self.ROW_H + 4
        for display_idx, src_idx in enumerate(range(n - 1, -1, -1)):
            d, delta = rows[src_idx]
            saldo_row = saldos_acum[src_idx]
            y = self.HDR_H + display_idx * self.ROW_H
            bg = '#0A1628' if display_idx % 2 == 0 else '#07101E'
            self._cv.create_rectangle(0, y, self.TOTAL_W, y + self.ROW_H,
                                       fill=bg, outline='', tags='row')
            self._cv.create_line(0, y + self.ROW_H, self.TOTAL_W, y + self.ROW_H,
                                  fill=self.SEP_CLR, width=1, tags='row')
            winner = d.get('winner')
            acierto = d.get('acierto')
            modo = d.get('modo', '')
            if winner is None:
                dot_clr = '#FFD700'
            elif modo == 'INVERSO':
                dot_clr = '#FF00FF' if acierto else '#FF44AA'
            else:
                dot_clr = '#00FF88' if acierto else '#FF3366'
            vals = {
                '_dot':   '',
                'idx':    str(n - display_idx),
                'issue':  d.get('issue', ''),
                'filtro': d.get('filtro', ''),
                'delta':  f"{delta:+.2f}",
                'saldo':  f"{saldo_row:+.2f}",
            }
            x = 0
            for key, label, col_w, align in self._col_defs:
                cy = y + self.ROW_H // 2
                if key == '_dot':
                    cx = x + col_w // 2
                    self._cv.create_oval(cx - self.DOT_R, cy - self.DOT_R,
                                          cx + self.DOT_R, cy + self.DOT_R,
                                          fill=dot_clr, outline=dot_clr, tags='row')
                else:
                    val = vals.get(key, '')
                    max_c = max(1, (col_w - 10) // 7)
                    if len(val) > max_c:
                        val = val[:max_c - 1] + '\u2026'
                    tx = x + col_w // 2 if align == 'c' else x + 8
                    anc = 'center' if align == 'c' else 'w'
                    if key == 'saldo':
                        fg = (C['accent2'] if saldo_row > 0
                              else (C['accent3'] if saldo_row < 0 else C['muted']))
                        fnt = self.FONT_ROWB
                    elif key == 'delta':
                        fg = (C['accent2'] if delta > 0
                              else (C['accent3'] if delta < 0 else C['muted']))
                        fnt = self.FONT_ROW
                    else:
                        fg = '#CCDDEE'
                        fnt = self.FONT_ROW
                    self._cv.create_text(tx, cy, text=val, fill=fg, font=fnt,
                                          anchor=anc, tags='row')
                self._cv.create_line(x + col_w, y, x + col_w, y + self.ROW_H,
                                      fill=self.SEP_CLR, width=1, tags='row')
                x += col_w
        self._cv.configure(scrollregion=(0, 0, self.TOTAL_W, total_h))
        self._cv.yview_moveto(0)
        return saldo
