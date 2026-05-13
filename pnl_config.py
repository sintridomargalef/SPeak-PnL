"""
PNL DASHBOARD — Configuracion: constantes, colores, fuentes, filtros.
"""
from pathlib import Path

INPUT_TXT      = Path(__file__).parent / 'reconstructor_data_AI.txt'
INPUT_WS       = Path(__file__).parent / 'websocket_log.txt'
CONFIG_FILE    = Path(__file__).parent / 'pnl_dashboard_cfg.json'
LIVE_HIST_FILE = Path(__file__).parent / 'pnl_live_history.json'
DECISION_HIST_FILE = Path(__file__).parent / 'pnl_decision_history.json'
DECISION_GEOM_FILE = Path(__file__).parent / 'pnl_decision_geom.json'
FILTRO_HIST_FILE   = Path(__file__).parent / 'pnl_filtro_history.json'
FILTROS_LONG_FILE  = Path(__file__).parent / 'pnl_filtros_long.jsonl'   # 1 línea por filtro × ronda
COOLDOWN_FILE      = Path(__file__).parent / 'cooldown_filtros.json'

# Umbral mínimo de win-rate (%) para que EP UMBRAL emita señal.
# Compartido por live (pnl_panels) y backtest (backtest_ep).
EP_UMBRAL_MIN = 62.0

LIVE_WS_URL    = "wss://www.ff2016.vip/game"
LIVE_WS_ORIGIN = "https://www.ff2016.vip"

C = {
    'bg':      '#050A14',
    'panel':   '#0A1628',
    'border':  '#0D2137',
    'accent':  '#00D4FF',
    'accent2': '#00FF88',
    'accent3': '#FF3366',
    'warn':    '#FFB800',
    'text':    '#C8D8E8',
    'muted':   '#4A6080',
    'blue':    '#2B7FFF',
    'red':     '#FF3366',
    'white':   '#E8F4FF',
}

FONT_MONO   = ('Consolas', 12)
FONT_MONO_B = ('Consolas', 12, 'bold')
FONT_BIG    = ('Consolas', 26, 'bold')
FONT_SM     = ('Consolas', 11)
FONT_TITLE  = ('Consolas', 14, 'bold')

ORDEN_RANGOS = ["0-5","5-10","10-15","15-20","20-25","25-30","30-35","35-40","40-45","45-50","+50"]

HABLAR_ACTIVADO = True

# Parámetros dinámicos de filtros (sobrescribibles desde Sheets Variables).
# Las lambdas en FILTROS_CURVA usan estos valores por referencia,
# por lo que al cambiar el dict toman efecto inmediatamente sin reiniciar.
FILTER_PARAMS = {
    'wr_70': 70,      # umbral para DIR WR>=70% y DIR WR>=70 sin+50
    'wr_80': 80,      # umbral para DIR WR>=80%
    'wr_40': 40,      # umbral para MAYORÍA PERDEDORA (wr < este valor)
    'acel_umbral': 3, # umbral para |acel| < X → ESTABLE, ≥ X → VOLATIL
    'vuelta_base': 3, # pérdidas consecutivas antes de volver a Base
}

FILTROS_CURVA = [
    ("Base (todo)",       '#4A6080',  lambda op: True,                                                                     False, True),
    ("Solo DIRECTO",      '#00FF88',  lambda op: op['modo'] == 'DIRECTO',                                                  False, False),
    ("Solo INVERSO",      '#FF3366',  lambda op: op['modo'] == 'INVERSO',                                                  False, False),
    ("DIR WR>=70%",       '#00D4FF',  lambda op: op['modo'] == 'DIRECTO' and op['wr'] >= FILTER_PARAMS['wr_70'],           False, False),
    ("DIR WR>=80%",       '#FFB800',  lambda op: op['modo'] == 'DIRECTO' and op['wr'] >= FILTER_PARAMS['wr_80'],           False, False),
    ("DIR sin +50",       '#2B7FFF',  lambda op: op['modo'] == 'DIRECTO' and op['rango'] != '+50',                         False, False),
    ("DIR ESTABLE",       '#8B5CF6',  lambda op: op['modo'] == 'DIRECTO' and op['est'] == 'ESTABLE',                       False, False),
    ("DIR VOLATIL",       '#F97316',  lambda op: op['modo'] == 'DIRECTO' and op['est'] == 'VOLATIL',                       False, False),
    ("DIR |acel|<10",     '#EC4899',  lambda op: op['modo'] == 'DIRECTO' and abs(op['acel']) < 10,                         False, False),
    ("DIR WR>=70 sin+50", '#06B6D4',  lambda op: op['modo'] == 'DIRECTO' and op['wr'] >= FILTER_PARAMS['wr_70'] and op['rango'] != '+50', False, False),
    # ── Estrategia contraria ─────────────────────────────────────────────────────
    ("CONTRA TOTAL",      '#FF6B35',  lambda op: not op['skip'],                            True,  False),
    ("MAYORÍA PERDEDORA", '#C084FC',  lambda op: op['wr'] < FILTER_PARAMS['wr_40'],           False, False),
    ("CONTRA ESTABLE",    '#F59E0B',  lambda op: not op['skip'] and op['est'] == 'ESTABLE', True,  False),
    # ── EP Adaptativo ──
    ("EP ADAPTATIVO",     '#FFD700',  None,        False, False),
    ("EP + WR≥70",        '#00FFFF',  'EP_WR70',   False, False),
    ("EP + WR≥70 INV",    '#FF00FF',  'EP_WR70',   True,  False),
    ("EP UMBRAL",         '#FF8C00',  'EP_UMBRAL', False, False),
    # ── Balance acumulado real de sesión ──
    ("BAL.FILTRO",        '#E8F4FF',  'BAL_FILTRO', False, False),
]
