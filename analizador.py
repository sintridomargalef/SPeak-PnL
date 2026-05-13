import re
import os
import tkinter as tk
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

class ModoApuesta(Enum):
    DIRECTO = "DIRECTO"
    INVERSO = "INVERSO"
    SKIP = "SKIP"

@dataclass
class ResultadoSim:
    ronda: int
    rango: str
    racha: float
    modo: ModoApuesta
    resultado: str
    mayor_gana: bool
    pnl: float
    balance: float
    apuesta: str = ""
    acierto: bool = False

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
    'skip':     '#888899',
    'ganada':   '#00FF88',
    'perdida':  '#FF3366',
}

FONT_MONO  = ('Consolas', 9)
FONT_MONO_B= ('Consolas', 9, 'bold')
FONT_BIG   = ('Consolas', 22, 'bold')
FONT_MED   = ('Consolas', 13, 'bold')
FONT_SM    = ('Consolas', 8)
FONT_TITLE = ('Consolas', 11, 'bold')

UMBRAL_RENTABLE = 53.2
CUOTA = 1.9

class DashboardAnalizador:
    def __init__(self, root):
        self.root = root
        self.root.title("ANALIZADOR - SIMULADOR DE APUESTAS")
        self.root.configure(bg=C['bg'])
        self.root.geometry("1000x900")
        
        self.estado = {'aciertos': 0, 'fallos': 0, 'ops': 0, 'saldo': 0.0, 'winrate': 0.0}
        self.stats_rangos = {}
        self.historial = []
        self.datos_raw = []

    @property
    def ops_history(self) -> list:
        """Retorna historial en formato compatible con umbral_core."""
        return [
            {
                'rango': r.rango,
                'modo': r.modo.value if hasattr(r.modo, 'value') else str(r.modo),
                'ganada': r.acierto if r.acierto is not None else (r.pnl > 0 if r.pnl != 0 else False),
                'pnl_real': r.pnl,
                'mult_real': 1,
                'racha': getattr(r, 'racha', 50.0),
            }
            for r in self.historial
            if r.modo != ModoApuesta.SKIP
        ]
        self.fig = None
        self.canvas = None
        self.fig_balance = None
        self.canvas_balance = None
        
        # Control de simulación
        self._corriendo = False
        
        self._construir_ui()
        self._cargar_config_ventana()
        self._iniciar_loop()
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)
    
    def _cargar_config_ventana(self):
        config_file = "analizador_geometry.txt"
        if os.path.exists(config_file):
            try:
                with open(config_file, 'r') as f:
                    geom = f.read().strip()
                    self.root.geometry(geom)
            except:
                pass
    
    def _guardar_config_ventana(self):
        config_file = "analizador_geometry.txt"
        with open(config_file, 'w') as f:
            f.write(self.root.geometry())
    
    def _on_closing(self):
        self._guardar_config_ventana()
        self.root.destroy()
    
    def _construir_ui(self):
        main = tk.Frame(self.root, bg=C['bg'])
        main.pack(fill='both', expand=True, padx=10, pady=10)
        
        self._panel_titulo(main)
        self._panel_stats(main)
        self._panel_balance(main)
        self._panel_efectividad(main)
        self._panel_prioridad(main)
        self._panel_rentabilidad(main)
        self._panel_grafica(main)
        self._panel_historial(main)
    
    def _panel_titulo(self, parent):
        f = tk.Frame(parent, bg=C['border'], bd=1, relief='solid')
        f.pack(fill='x', pady=(0, 8))
        
        left = tk.Frame(f, bg=C['border'])
        left.pack(side='left', fill='x', expand=True)
        tk.Label(left, text="◈ ANALIZADOR DE APUESTAS", font=FONT_TITLE,
               bg=C['border'], fg=C['accent']).pack(side='left', padx=8, pady=8)
        
        right = tk.Frame(f, bg=C['border'])
        right.pack(side='right', padx=8)
        
        # Botón INICIO/PARAR
        self._btn_control = tk.Button(right, text="▶ INICIO", font=FONT_SM, bg=C['panel'], fg=C['accent'],
                             command=self._toggle_simulacion, relief='raised', bd=1)
        self._btn_control.pack(side='right', padx=4)
        
        btn = tk.Button(right, text="GRÁFICA", font=FONT_SM, bg=C['panel'], fg=C['accent'],
                     command=self._abrir_grafica, relief='raised', bd=1)
        btn.pack(side='right', padx=4)
        
        tk.Label(right, text="SIMULACIÓN", font=FONT_SM,
               bg=C['border'], fg=C['muted']).pack(side='right', padx=4)
    
    def _panel_stats(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)
        
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ ESTADÍSTICAS DE SIMULACIÓN", font=FONT_TITLE,
               bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        
        right = tk.Frame(tf, bg=C['border'])
        right.pack(side='right', padx=4)
        self._stats_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right, text="◁", variable=self._stats_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_stats)
        btn.pack(side='right')
        
        self._stats_content = tk.Frame(f, bg=C['panel'])
        self._stats_content.pack(fill='x', pady=8, padx=8)
        
        grid = tk.Frame(self._stats_content, bg=C['panel'])
        grid.pack(fill='x')
        
        stats = [
            ('_st_ops', 'OPERACIONES', '0', C['accent']),
            ('_st_aciertos', 'ACIERTOS', '0', C['accent2']),
            ('_st_fallos', 'FALLOS', '0', C['accent3']),
            ('_st_wr', 'WIN RATE', '0%', C['warn']),
            ('_st_saldo', 'REAL', '0.00€', C['green']),
            ('_st_perfecta', 'PERFECTA', '0.00€', C['warn']),
        ]
        
        for i, (attr, lbl, val, col) in enumerate(stats):
            c = tk.Frame(grid, bg=C['border'], padx=16, pady=8)
            c.grid(row=0, column=i, sticky='ew')
            grid.columnconfigure(i, weight=1)
            tk.Label(c, text=lbl, font=FONT_SM, bg=C['border'], fg=C['muted']).pack()
            l = tk.Label(c, text=val, font=FONT_MED, bg=C['border'], fg=col)
            l.pack()
            setattr(self, attr, l)
    
    def _toggle_stats(self):
        if self._stats_oculto.get():
            self._stats_content.pack_forget()
        else:
            self._stats_content.pack(fill='x', pady=8, padx=8)
    
    def _abrir_grafica(self):
        import subprocess
        subprocess.Popen(["py", "grafica_estrategia.py"])
    
    def _toggle_simulacion(self):
        if self._corriendo:
            self._corriendo = False
            self._btn_control.config(text="▶ INICIO")
        else:
            self._corriendo = True
            self._btn_control.config(text="⏹ PARAR")
            self.cargar_datos()
            self.simular()
    
    def _iniciar_loop(self):
        # Bucle cada 85 segundos
        self.root.after(85000, self._loop_actualizacion)
    
    def _loop_actualizacion(self):
        if self._corriendo:
            self.cargar_datos()
            self.simular()
            self._actualizar_ui()
        self.root.after(85000, self._loop_actualizacion)
    
    def _panel_balance(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)
        
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ CURVA DE BALANCE (3 ESTRATEGIAS)", font=FONT_TITLE,
               bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        
        right = tk.Frame(tf, bg=C['border'])
        right.pack(side='right', padx=4)
        self._balance_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right, text="◁", variable=self._balance_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_balance)
        btn.pack(side='right')
        
        self._balance_content = tk.Frame(f, bg=C['panel'])
        self._balance_content.pack(fill='both', expand=True)
        
        self.fig_balance = Figure(figsize=(8, 4), facecolor=C['panel'])
        self.ax_balance = self.fig_balance.add_subplot(111)
        self.ax_balance.set_facecolor(C['panel'])
        
        self.canvas_balance = FigureCanvasTkAgg(self.fig_balance, master=self._balance_content)
        self.canvas_balance.get_tk_widget().pack(fill='both', expand=True)
    
    def _toggle_balance(self):
        if self._balance_oculto.get():
            self._balance_content.pack_forget()
        else:
            self._balance_content.pack(fill='both', expand=True)
    
    def _panel_efectividad(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)
        
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ ANÁLISIS DE EFECTIVIDAD REAL", font=FONT_TITLE,
               bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        
        right = tk.Frame(tf, bg=C['border'])
        right.pack(side='right', padx=4)
        self._efectividad_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right, text="◁", variable=self._efectividad_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_efectividad)
        btn.pack(side='right')
        
        cab = tk.Frame(f, bg='#060E1C')
        cab.pack(fill='x', padx=4, pady=(4, 0))
        for txt, w in [("RANGO", 8), ("TOTAL", 7), ("GANADOS", 9), ("PERDIDOS", 9), ("% EFECTIVIDAD", 12)]:
            tk.Label(cab, text=txt, font=FONT_SM, bg='#060E1C', fg=C['accent'], width=w, anchor='w').pack(side='left', padx=1)
        
        self._efectividad_frame = tk.Frame(f, bg=C['panel'])
        self._efectividad_frame.pack(fill='x', padx=4, pady=2)
    
    def _toggle_efectividad(self):
        if self._efectividad_oculto.get():
            self._efectividad_frame.pack_forget()
        else:
            self._efectividad_frame.pack(fill='x', padx=4, pady=2)
    
    def _panel_prioridad(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)
        
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ CLASIFICACIÓN DE PRIORIDAD", font=FONT_TITLE,
               bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        
        right = tk.Frame(tf, bg=C['border'])
        right.pack(side='right', padx=4)
        self._prioridad_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right, text="◁", variable=self._prioridad_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_prioridad)
        btn.pack(side='right')
        
        self._prioridad_frame = tk.Frame(f, bg=C['panel'])
        self._prioridad_frame.pack(fill='x', padx=4, pady=4)
    
    def _toggle_prioridad(self):
        if self._prioridad_oculto.get():
            self._prioridad_frame.pack_forget()
        else:
            self._prioridad_frame.pack(fill='x', padx=4, pady=4)
    
    def _panel_rentabilidad(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)
        
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ ANÁLISIS DE ESPERANZA MATEMÁTICA", font=FONT_TITLE,
               bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        
        right = tk.Frame(tf, bg=C['border'])
        right.pack(side='right', padx=4)
        self._rentabilidad_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right, text="◁", variable=self._rentabilidad_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_rentabilidad)
        btn.pack(side='right')
        
        cab = tk.Frame(f, bg='#060E1C')
        cab.pack(fill='x', padx=4, pady=(4, 0))
        for txt, w in [("RANGO", 8), ("PROB", 8), ("EV", 8), ("ESTADO", 10)]:
            tk.Label(cab, text=txt, font=FONT_SM, bg='#060E1C', fg=C['accent'], width=w, anchor='w').pack(side='left', padx=1)
        
        self._rentabilidad_frame = tk.Frame(f, bg=C['panel'])
        self._rentabilidad_frame.pack(fill='x', padx=4, pady=2)
        
        self._decision_frame = tk.Frame(f, bg=C['border'])
        self._decision_frame.pack(fill='x', padx=4, pady=4)
    
    def _toggle_rentabilidad(self):
        if self._rentabilidad_oculto.get():
            self._rentabilidad_frame.pack_forget()
            self._decision_frame.pack_forget()
        else:
            self._rentabilidad_frame.pack(fill='x', padx=4, pady=2)
            self._decision_frame.pack(fill='x', padx=4, pady=4)
    
    def _panel_grafica(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)
        
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ GRÁFICA DE EFECTIVIDAD POR RANGO", font=FONT_TITLE,
               bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        
        right = tk.Frame(tf, bg=C['border'])
        right.pack(side='right', padx=4)
        self._grafica_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right, text="◁", variable=self._grafica_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_grafica)
        btn.pack(side='right')
        
        self._grafica_content = tk.Frame(f, bg=C['panel'])
        self._grafica_content.pack(fill='both', expand=True)
        
        self.fig = Figure(figsize=(8, 4), facecolor=C['panel'])
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(C['panel'])
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self._grafica_content)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)
    
    def _toggle_grafica(self):
        if self._grafica_oculto.get():
            self._grafica_content.pack_forget()
        else:
            self._grafica_content.pack(fill='both', expand=True)
    
    def _panel_historial(self, parent):
        f = tk.Frame(parent, bg=C['panel'], bd=1, relief='solid')
        f.pack(fill='x', pady=4)
        
        tf = tk.Frame(f, bg=C['border'])
        tf.pack(fill='x')
        tk.Label(tf, text="  ◈ HISTORIAL DE RONDAS", font=FONT_TITLE,
               bg=C['border'], fg=C['accent'], pady=4).pack(side='left')
        
        right = tk.Frame(tf, bg=C['border'])
        right.pack(side='right', padx=4)
        self._historial_oculto = tk.BooleanVar(value=False)
        btn = tk.Checkbutton(right, text="◁", variable=self._historial_oculto,
                          font=FONT_TITLE, bg=C['border'], fg=C['accent'],
                          selectcolor=C['border'], indicatoron=False,
                          command=self._toggle_historial)
        btn.pack(side='right')
        
        self._historial_content = tk.Frame(f, bg=C['panel'])
        self._historial_content.pack(fill='both', expand=True, padx=4, pady=4)
        
        list_frame = tk.Frame(self._historial_content, bg=C['panel'])
        list_frame.pack(fill='both', expand=True)
        
        sb = tk.Scrollbar(list_frame, bg=C['bg'])
        sb.pack(side='right', fill='y')
        self._hist_canvas = tk.Canvas(list_frame, bg=C['panel'], yscrollcommand=sb.set, highlightthickness=0)
        self._hist_canvas.pack(fill='both', expand=True)
        sb.config(command=self._hist_canvas.yview)
        self._hist_inner = tk.Frame(self._hist_canvas, bg=C['panel'])
        self._hist_canvas.create_window((0, 0), window=self._hist_inner, anchor='nw')
    
    def _toggle_historial(self):
        if self._historial_oculto.get():
            self._historial_content.pack_forget()
        else:
            self._historial_content.pack(fill='both', expand=True, padx=4, pady=4)
        self._hist_inner.bind('<Configure>', lambda e: self._hist_canvas.configure(scrollregion=self._hist_canvas.bbox('all')))
    
    def cargar_datos(self, archivo="reconstructor_data_AI.txt"):
        ruta = Path(archivo)
        if not ruta.exists():
            return []
        
        datos = []
        with open(ruta, 'r', encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if not linea or '*] RESULTADO:' not in linea:
                    continue
                
                resultado = 'BLUE' if 'RESULTADO: BLUE' in linea else 'RED'
                mayor_gana = 'MayorGana: True' in linea
                
                m_rango = re.search(r'Rango:\s*([0-9+-]+)', linea)
                rango = m_rango.group(1) if m_rango else '?'
                
                m_racha = re.search(r'Racha_10r:\s*([0-9.]+)%', linea)
                racha = float(m_racha.group(1)) if m_racha else 50.0
                
                m_modo = re.search(r'Modo:\s*(\w+)', linea)
                modo = ModoApuesta[m_modo.group(1)] if m_modo else ModoApuesta.SKIP
                
                datos.append({'rango': rango, 'racha': racha, 'modo': modo,
                          'resultado': resultado, 'mayor_gana': mayor_gana})
        
        self.datos_raw = datos
        return datos
    
    def simular(self):
        if not self._corriendo:
            return
        
        datos = self.cargar_datos()
        if not datos:
            print("No hay datos para simular")
            return
        
        print(f"Simulando con {len(datos)} rondas...")
        
        stats_rango = {}
        
        for d in datos:
            rng = d['rango']
            if rng not in stats_rango:
                stats_rango[rng] = {'total': 0, 'ganados': 0, 'perdidos': 0}
            
            stats_rango[rng]['total'] += 1
            if d['mayor_gana']:
                stats_rango[rng]['ganados'] += 1
            else:
                stats_rango[rng]['perdidos'] += 1
        
        self.stats_rangos = stats_rango
        
        balance = 0.0
        balance_perfecto = 0.0
        aciertos = fallos = 0
        multiplicador = 1.0
        
        #收集datos de pnl para análisis
        datos_pnl = []
        for d in datos:
            modo = d['modo']
            resultado_real = d['resultado']
            mayor_gana = d['mayor_gana']
            rango = d['rango']
            
            if modo == ModoApuesta.SKIP:
                pnl = 0
            else:
                if modo == ModoApuesta.DIRECTO:
                    ap = 'BLUE' if mayor_gana else 'RED'
                else:
                    ap = 'RED' if mayor_gana else 'BLUE'
                
                if ap == resultado_real:
                    pnl = multiplicador * 0.90
                else:
                    pnl = -multiplicador
            
            datos_pnl.append({'rango': rango, 'modo': modo, 'pnl': pnl})
        
        # Análisis: mejor modo por rango (por puntos)
        stats_modo = {}
        for item in datos_pnl:
            rng = item['rango']
            if rng not in stats_modo:
                stats_modo[rng] = {'directo': 0, 'inverso': 0}
            if item['modo'].value == 'DIRECTO':
                stats_modo[rng]['directo'] += item['pnl']
            elif item['modo'].value == 'INVERSO':
                stats_modo[rng]['inverso'] += item['pnl']
        
        rango_mejor = {}
        for rng, s in stats_modo.items():
            rango_mejor[rng] = 'INVERSO' if s['inverso'] > s['directo'] else 'DIRECTO'
        
        for i, d in enumerate(datos, 1):
            modo = d['modo']
            resultado_real = d['resultado']
            mayor_gana = d['mayor_gana']
            racha = d['racha']
            rango = d['rango']
            
            if modo == ModoApuesta.SKIP:
                pnl = 0
                apuesta = "SKIP"
            else:
                if modo == ModoApuesta.DIRECTO:
                    apuesta = 'BLUE' if mayor_gana else 'RED'
                else:
                    apuesta = 'RED' if mayor_gana else 'BLUE'
                
                if apuesta == resultado_real:
                    pnl = multiplicador * 0.90
                    aciertos += 1
                    acierto_bool = True
                else:
                    pnl = -multiplicador
                    fallos += 1
                    acierto_bool = False
                
                # Calcular PERFECTA
                mejor_modo = rango_mejor.get(rango, 'DIRECTO')
                if mejor_modo == modo.value:
                    pnl_perfecto = pnl
                else:
                    pnl_perfecto = -pnl if pnl != 0 else 0
                
                balance_perfecto += pnl_perfecto
                
                balance += pnl
                self.historial.append(ResultadoSim(
                    ronda=i, rango=rango, racha=racha, modo=modo,
                    resultado=resultado_real, mayor_gana=mayor_gana,
                    pnl=pnl, balance=balance, apuesta=apuesta, acierto=acierto_bool
                ))
        
        total_apuestas = aciertos + fallos
        wr = (aciertos / total_apuestas * 100) if total_apuestas > 0 else 0
        
        self.estado = {
            'aciertos': aciertos, 'fallos': fallos,
            'ops': total_apuestas, 'saldo': balance, 'winrate': wr,
            'perfecta': balance_perfecto
        }
        
        self._actualizar_ui()
        print(f"Simulación completada: {total_apuestas} ops, {wr:.1f}% WR | REAL: {balance:+.2f}€ | PERFECTA: {balance_perfecto:+.2f}€")
    
    def _actualizar_ui(self):
        self._st_ops.config(text=str(self.estado['ops']))
        self._st_aciertos.config(text=str(self.estado['aciertos']))
        self._st_fallos.config(text=str(self.estado['fallos']))
        self._st_wr.config(text=f"{self.estado['winrate']:.1f}%")
        
        saldo = self.estado['saldo']
        col_saldo = C['accent2'] if saldo > 0 else C['accent3']
        self._st_saldo.config(text=f"{saldo:+.2f}€", fg=col_saldo)
        
        perfecta = self.estado.get('perfecta', 0)
        col_perf = C['accent2'] if perfecta > 0 else C['accent3']
        self._st_perfecta.config(text=f"{perfecta:+.2f}€", fg=col_perf)
        
        self._actualizar_efectividad()
        self._actualizar_prioridad()
        self._actualizar_rentabilidad()
        self._dibujar_grafica()
        self._dibujar_balance()
        self._actualizar_historial()
    
    def _dibujar_balance(self):
        if not self.historial:
            return
        
        self.ax_balance.clear()
        self.ax_balance.set_facecolor(C['panel'])
        
        datos_con_apuesta = [r for r in self.historial if r.pnl != 0]
        
        if not datos_con_apuesta:
            return
        
        # Calcular para cada rango: qué modo habría dado mejor resultado (POR PUNTOS, NO POR VICTORIAS)
        stats_mejor = {}
        for r in datos_con_apuesta:
            rng = r.rango
            if rng not in stats_mejor:
                stats_mejor[rng] = {'directo_puntos': 0, 'inverso_puntos': 0}
            
            # Si pnl > 0 (ganó +0.9), el modo usado fue correcto
            # El modo opuesto habría perdido (-1)
            if r.pnl > 0:
                if r.modo.value == 'DIRECTO':
                    stats_mejor[rng]['directo_puntos'] += 0.9
                    stats_mejor[rng]['inverso_puntos'] -= 1.0
                else:  # INVERSO
                    stats_mejor[rng]['inverso_puntos'] += 0.9
                    stats_mejor[rng]['directo_puntos'] -= 1.0
            else:  # pnl < 0 (perdió -1)
                # El modo opuesto habría ganado (+0.9)
                if r.modo.value == 'DIRECTO':
                    stats_mejor[rng]['directo_puntos'] -= 1.0
                    stats_mejor[rng]['inverso_puntos'] += 0.9
                else:  # INVERSO
                    stats_mejor[rng]['inverso_puntos'] -= 1.0
                    stats_mejor[rng]['directo_puntos'] += 0.9
        
        # Para cada rango, elegir el modo con más puntos
        rango_mejor_modo = {}
        for rng, s in stats_mejor.items():
            rango_mejor_modo[rng] = 'INVERSO' if s['inverso_puntos'] > s['directo_puntos'] else 'DIRECTO'
        
        rondas = [r.ronda for r in datos_con_apuesta]
        
        balance_real = 0
        balance_invertido = 0
        balance_perfecto = 0
        balances_real = []
        balances_invertido = []
        balances_perfecto = []
        
        for r in datos_con_apuesta:
            balance_real += r.pnl
            balance_invertido += -r.pnl
            
            # Estrategia perfecta: elegir modo según análisis de puntos del rango
            mejor_modo = rango_mejor_modo.get(r.rango, 'DIRECTO')
            
            # Calcular PnL según el modo elegido vs resultado real
            if mejor_modo == r.modo.value:
                pnl_perfecto = r.pnl
            else:
                pnl_perfecto = -r.pnl
            
            balance_perfecto += pnl_perfecto
            
            balances_real.append(balance_real)
            balances_invertido.append(balance_invertido)
            balances_perfecto.append(balance_perfecto)
        
        # Plot
        self.ax_balance.plot(rondas, balances_perfecto, color=C['warn'], 
                           linewidth=1.5, label='PERFECTA', linestyle=':')
        self.ax_balance.plot(rondas, balances_real, color=C['accent'], 
                           linewidth=1.5, label='REAL')
        self.ax_balance.plot(rondas, balances_invertido, color=C['accent3'], 
                           linewidth=1.5, label='INVERTIDO', linestyle='--')
        
        self.ax_balance.axhline(y=0, color=C['muted'], linestyle='-', linewidth=1, alpha=0.5)
        
        self.ax_balance.set_xlabel('Ronda', fontsize=10, color=C['accent'])
        self.ax_balance.set_ylabel('Balance (€)', fontsize=10, color=C['accent'])
        self.ax_balance.legend(loc='upper left', fontsize=9, facecolor=C['panel'], edgecolor=C['border'], labelcolor=C['text'])
        
        self.ax_balance.tick_params(colors=C['text'])
        self.ax_balance.spines['bottom'].set_color(C['border'])
        self.ax_balance.spines['top'].set_color(C['panel'])
        self.ax_balance.spines['left'].set_color(C['border'])
        self.ax_balance.spines['right'].set_color(C['panel'])
        
        all_balances = balances_real + balances_invertido + balances_perfecto
        self.ax_balance.set_ylim(min(all_balances) - 1, max(all_balances) + 1)
        self.fig_balance.tight_layout()
        self.canvas_balance.draw()
    
    def _dibujar_grafica(self):
        self.ax.clear()
        self.ax.set_facecolor(C['panel'])
        
        def sort_key(x):
            if '-' in x:
                return int(x.split('-')[0])
            return 100
        
        rangos = sorted(self.stats_rangos.keys(), key=sort_key)
        
        efectividad = []
        rentabilidad = []
        colores = []
        
        for rng in rangos:
            s = self.stats_rangos[rng]
            total = s['total']
            ganados = s['ganados']
            
            ef = (ganados / total * 100) if total > 0 else 0
            rent = (ef / 100 * CUOTA) - 1
            
            efectividad.append(ef)
            rentabilidad.append(rent)
            colores.append(C['accent2'] if ef >= 50 else C['accent3'])
        
        x_pos = range(len(rangos))
        
        self.ax.bar(x_pos, efectividad, color=colores, alpha=0.8, edgecolor=C['accent'], linewidth=1)
        
        self.ax.axhline(y=50, color=C['muted'], linestyle='--', linewidth=1, alpha=0.5)
        self.ax.axhline(y=UMBRAL_RENTABLE, color=C['warn'], linestyle='--', linewidth=1, alpha=0.7)
        
        self.ax.set_xticks(x_pos)
        self.ax.set_xticklabels(rangos, rotation=45, fontsize=9, color=C['text'])
        self.ax.set_ylabel('% Efectividad', fontsize=10, color=C['accent'])
        self.ax.set_xlabel('Rango', fontsize=10, color=C['accent'])
        
        self.ax.tick_params(colors=C['text'])
        self.ax.spines['bottom'].set_color(C['border'])
        self.ax.spines['top'].set_color(C['panel'])
        self.ax.spines['left'].set_color(C['border'])
        self.ax.spines['right'].set_color(C['panel'])
        
        for i, (ef, rent) in enumerate(zip(efectividad, rentabilidad)):
            if ef >= UMBRAL_RENTABLE:
                self.ax.annotate(f'EV:{rent:+.2f}', xy=(i, ef), xytext=(0, 5),
                          textcoords='offset points', fontsize=8, color=C['accent2'], ha='center')
        
        self.ax.set_ylim(0, 110)
        self.fig.tight_layout()
        self.canvas.draw()
    
    def _actualizar_efectividad(self):
        for w in self._efectividad_frame.winfo_children():
            w.destroy()
        
        for rng, s in sorted(self.stats_rangos.items()):
            total = s['total']
            ganados = s['ganados']
            perdidos = s['perdidos']
            ef = (ganados / total * 100) if total > 0 else 0
            
            col = C['accent2'] if ef >= 50 else C['accent3']
            
            row = tk.Frame(self._efectividad_frame, bg=C['panel'])
            row.pack(fill='x', pady=1)
            tk.Label(row, text=rng, font=FONT_SM, bg=C['panel'], fg=C['accent'], width=8, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=str(total), font=FONT_SM, bg=C['panel'], fg=C['text'], width=7, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=str(ganados), font=FONT_SM, bg=C['panel'], fg=C['accent2'], width=9, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=str(perdidos), font=FONT_SM, bg=C['panel'], fg=C['accent3'], width=9, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=f"{ef:.1f}%", font=FONT_SM, bg=C['panel'], fg=col, width=12, anchor='w').pack(side='left', padx=1)
    
    def _actualizar_prioridad(self):
        for w in self._prioridad_frame.winfo_children():
            w.destroy()
        
        alta = []
        baja = []
        
        for rng, s in self.stats_rangos.items():
            total = s['total']
            ganados = s['ganados']
            ef = (ganados / total * 100) if total > 0 else 0
            
            if ef > 50:
                alta.append((rng, ef))
            else:
                baja.append((rng, ef))
        
        alta.sort(key=lambda x: x[1], reverse=True)
        baja.sort(key=lambda x: x[1], reverse=True)
        
        row_alta = tk.Frame(self._prioridad_frame, bg=C['panel'])
        row_alta.pack(fill='x', pady=2)
        tk.Label(row_alta, text="PRIORIDAD ALTA:", font=FONT_SM, bg=C['panel'], fg=C['accent2'], width=15, anchor='w').pack(side='left', padx=1)
        
        if alta:
            txt_alta = ", ".join([f"{r} ({p:.1f}%)" for r, p in alta])
            tk.Label(row_alta, text=txt_alta, font=FONT_SM, bg=C['panel'], fg=C['white'], width=50, anchor='w').pack(side='left', padx=1)
        else:
            tk.Label(row_alta, text="Ninguno", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(side='left', padx=1)
        
        row_baja = tk.Frame(self._prioridad_frame, bg=C['panel'])
        row_baja.pack(fill='x', pady=2)
        tk.Label(row_baja, text="PRIORIDAD BAJA:", font=FONT_SM, bg=C['panel'], fg=C['accent3'], width=15, anchor='w').pack(side='left', padx=1)
        
        if baja:
            txt_baja = ", ".join([f"{r} ({p:.1f}%)" for r, p in baja])
            tk.Label(row_baja, text=txt_baja, font=FONT_SM, bg=C['panel'], fg=C['white'], width=50, anchor='w').pack(side='left', padx=1)
        else:
            tk.Label(row_baja, text="Ninguno", font=FONT_SM, bg=C['panel'], fg=C['muted']).pack(side='left', padx=1)
    
    def _actualizar_rentabilidad(self):
        for w in self._rentabilidad_frame.winfo_children():
            w.destroy()
        
        renta = []
        rentable_count = 0
        
        for rng, s in self.stats_rangos.items():
            total = s['total']
            ganados = s['ganados']
            prob = (ganados / total * 100) if total > 0 else 0
            
            ev = (prob / 100 * CUOTA) - 1
            
            estado = "BUENO" if prob >= UMBRAL_RENTABLE else "MALO"
            if estado == "BUENO":
                rentable_count += 1
            
            renta.append((rng, prob, ev, estado))
        
        renta.sort(key=lambda x: x[1], reverse=True)
        
        for rng, prob, ev, estado in renta:
            col = C['accent2'] if estado == "BUENO" else C['accent3']
            col_prob = C['accent2'] if prob >= 50 else C['accent3']
            col_ev = C['accent2'] if ev > 0 else C['accent3']
            
            row = tk.Frame(self._rentabilidad_frame, bg=C['panel'])
            row.pack(fill='x', pady=1)
            tk.Label(row, text=rng, font=FONT_SM, bg=C['panel'], fg=C['accent'], width=8, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=f"{prob:.1f}%", font=FONT_SM, bg=C['panel'], fg=col_prob, width=8, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=f"{ev:+.2f}", font=FONT_SM, bg=C['panel'], fg=col_ev, width=8, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=estado, font=FONT_SM, bg=C['panel'], fg=col, width=10, anchor='w').pack(side='left', padx=1)
        
        for w in self._decision_frame.winfo_children():
            w.destroy()
        
        decision = "SI" if rentable_count > 0 else "NO"
        col_dec = C['accent2'] if decision == "SI" else C['accent3']
        
        row = tk.Frame(self._decision_frame, bg=C['border'])
        row.pack(fill='x', pady=(4, 0))
        tk.Label(row, text="DECISIÓN FINAL:", font=FONT_SM, bg=C['border'], fg=C['white'], width=15, anchor='w').pack(side='left', padx=1)
        tk.Label(row, text=f"HACER_APUESTA -> {decision}", font=FONT_MONO_B, bg=C['border'], fg=col_dec, width=20, anchor='w').pack(side='left', padx=1)
    
    def _actualizar_historial(self):
        for w in self._hist_inner.winfo_children():
            w.destroy()
        
        for r in self.historial[-50:]:
            if r.pnl == 0:
                col = C['skip']
                res_txt = "S"
            elif r.pnl > 0:
                col = C['ganada']
                res_txt = "G"
            else:
                col = C['perdida']
                res_txt = "P"
            
            row = tk.Frame(self._hist_inner, bg=C['panel'])
            row.pack(fill='x', pady=0)
            
            tk.Label(row, text=f"R{r.ronda:04d}", font=FONT_SM, bg=C['panel'], fg=C['muted'], width=6, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=r.rango, font=FONT_SM, bg=C['panel'], fg=C['accent'], width=7, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=f"{r.racha:.0f}%", font=FONT_SM, bg=C['panel'], fg=C['muted'], width=5, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=r.modo.value[:4], font=FONT_SM, bg=C['panel'], fg=C['text'], width=4, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=r.apuesta[:1], font=FONT_SM, bg=C['panel'], fg=C['blue'] if r.apuesta == "BLUE" else C['red'], width=3, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=r.resultado[:1], font=FONT_SM, bg=C['panel'], fg=C['blue'] if r.resultado == "BLUE" else C['red'], width=3, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=res_txt, font=FONT_SM, bg=C['panel'], fg=col, width=2, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=f"{r.pnl:+.2f}", font=FONT_SM, bg=C['panel'], fg=col, width=7, anchor='w').pack(side='left', padx=1)
            tk.Label(row, text=f"{r.balance:+.2f}", font=FONT_SM, bg=C['panel'], fg=C['accent2'] if r.balance > 0 else (C['accent3'] if r.balance < 0 else C['muted']), width=8, anchor='w').pack(side='left', padx=1)

def main():
    root = tk.Tk()
    dash = DashboardAnalizador(root)
    dash.simular()
    root.mainloop()

if __name__ == "__main__":
    main()