#!/usr/bin/env python3
"""
Monitor en tiempo real de pnl_decision_history.json — HUD CYBERPUNK.

Ventana Canvas con estética HUD que se redibuja cada vez que cambia el JSON.
Muestra análisis interpretativo: estado, métricas, mejores/peores filtros,
diagnóstico textual y alertas.
"""
import json
import math
import shutil
import threading
import tkinter as tk
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from pnl_config import (C, DECISION_HIST_FILE, FILTROS_CURVA, FILTROS_LONG_FILE)


def _saldos_filtros_long(ventana_issues=None):
    """Lee pnl_filtros_long.jsonl y devuelve {filtro_idx: saldo_final}.
    Si ventana_issues está dado (set de issues a considerar), suma deltas
    solo de esas rondas. Si no, devuelve el último 'saldo' visto por filtro
    (saldo acumulado total de sesión)."""
    res = {}
    try:
        if not FILTROS_LONG_FILE.exists():
            return res
        with open(FILTROS_LONG_FILE, encoding='utf-8') as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                idx = r.get('filtro_idx')
                if idx is None:
                    continue
                if ventana_issues is not None:
                    if r.get('issue') not in ventana_issues:
                        continue
                    delta = r.get('delta') or 0.0
                    res[idx] = res.get(idx, 0.0) + delta
                else:
                    s = r.get('saldo')
                    if s is not None:
                        res[idx] = s
    except Exception:
        pass
    return res

def _leer_variables_sheets() -> dict:
    """Lee la pestana Variables de Google Sheets.
    Devuelve {CLAVE: valor_string}. Silencioso ante errores."""
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(
            str(Path(__file__).parent / 'credenciales.json'), scope)
        ws = gspread.authorize(creds).open("Pk_Arena").worksheet("Variables")
        return {str(f[0]).strip().upper(): str(f[1]).strip()
                for f in ws.get_all_values() if len(f) >= 2 and f[0].strip()}
    except Exception:
        return {}

REFRESH_MS = 1500
W, H = 720, 1510

# Geometría natural (sin colapsos) de cada bloque: key, título, y1, h
BLOCKS_GEOM = [
    ('estado',   'ESTADO',                       50,  108),
    ('ultima',   'ÚLTIMA RONDA',                 178, 54),
    ('ep_gate',  'EP GATE',                      252, 84),
    ('metricas', 'MÉTRICAS GLOBALES',            356, 240),
    ('filtros',  'FILTROS',                      616, 136),
    ('rangos',   'RANGOS · 100r',                772, 134),
    ('diag',     'DIAGNÓSTICO',                  926, 124),
    ('alertas',  'ALERTAS',                      1070, 64),
    ('freq',     'FRECUENCIA · PRÓXIMA APUESTA', 1154, 150),
    ('martingala', 'MARTINGALA',                   1324, 160),
]
COLLAPSED_H = 22

# ── Fuente con buen soporte Unicode (cubre ▶▸⏱⬢⬡◤◢ etc.) ──────────────────
# Cascadia Mono viene en Windows 10+. Si no, fallback a Segoe UI Symbol.
# tkinter resuelve la primera fuente disponible. La pasamos como string para
# que respete la cadena de fallback estándar de Tk.
FUENTE = "Cascadia Mono"   # cambia a "Segoe UI" si no se ve bien

def F(size, bold=False):
    return (FUENTE, size, 'bold') if bold else (FUENTE, size)


def _parse_ts(d):
    """Devuelve datetime de la decisión. Prioridad: timestamp ISO > hora (legacy)."""
    ts = d.get('timestamp')
    if ts:
        try:
            return datetime.fromisoformat(ts)
        except Exception:
            pass
    h = d.get('hora')
    if h:
        try:
            # Solo HH:MM:SS — combinar con la fecha de hoy
            t = datetime.strptime(h, '%H:%M:%S').time()
            return datetime.combine(datetime.now().date(), t)
        except Exception:
            pass
    return None


def _analizar_frecuencia(decs: list) -> dict:
    """Calcula métricas temporales: cadencia de rondas, frecuencia de apuestas
    y estimación de próxima apuesta."""
    if not decs:
        return {}
    # Timestamps válidos en orden cronológico
    pares = [(d, _parse_ts(d)) for d in decs]
    pares = [(d, t) for d, t in pares if t is not None]
    if len(pares) < 2:
        return {'pocos_datos': True}

    # ── Cadencia de rondas (todas las decisiones) ─────────────────────
    intervalos_rondas = []
    for i in range(1, len(pares)):
        dt = (pares[i][1] - pares[i-1][1]).total_seconds()
        if 5 <= dt <= 600:   # filtrar huecos absurdos (>10min = sesión cortada)
            intervalos_rondas.append(dt)
    cad_ronda = sum(intervalos_rondas) / len(intervalos_rondas) if intervalos_rondas else 0

    # ── Frecuencia de apuestas (solo APOSTADA) ────────────────────────
    apostadas_ts = [(d, t) for d, t in pares
                    if d.get('decision') == 'APOSTADA']
    intervalos_apuestas = []
    for i in range(1, len(apostadas_ts)):
        dt = (apostadas_ts[i][1] - apostadas_ts[i-1][1]).total_seconds()
        if 5 <= dt <= 1800:   # hasta 30 min entre apuestas
            intervalos_apuestas.append(dt)
    cad_apuesta = sum(intervalos_apuestas) / len(intervalos_apuestas) if intervalos_apuestas else 0

    # Última apuesta y predicción
    ult_apuesta = apostadas_ts[-1][1] if apostadas_ts else None
    desde_ult_apuesta = (datetime.now() - ult_apuesta).total_seconds() if ult_apuesta else None
    proxima_estim = (ult_apuesta + timedelta(seconds=cad_apuesta)) if (ult_apuesta and cad_apuesta) else None
    seg_a_proxima = (proxima_estim - datetime.now()).total_seconds() if proxima_estim else None

    # Frecuencias en /hora
    rondas_hora   = (3600 / cad_ronda) if cad_ronda else 0
    apuestas_hora = (3600 / cad_apuesta) if cad_apuesta else 0
    pct_apuesta   = (len(apostadas_ts) / len(pares) * 100) if pares else 0

    return {
        'pocos_datos': False,
        'cad_ronda':       cad_ronda,
        'cad_apuesta':     cad_apuesta,
        'rondas_hora':     rondas_hora,
        'apuestas_hora':   apuestas_hora,
        'pct_apuesta':     pct_apuesta,
        'ult_apuesta':     ult_apuesta,
        'desde_ult_apuesta': desde_ult_apuesta,
        'proxima_estim':   proxima_estim,
        'seg_a_proxima':   seg_a_proxima,
        'n_intervalos_apuesta': len(intervalos_apuestas),
    }


def _fmt_dur(seg):
    """Formatea segundos como '12s', '3m 20s', '1h 5m'."""
    if seg is None:
        return '—'
    seg = abs(int(seg))
    if seg < 60:
        return f"{seg}s"
    if seg < 3600:
        return f"{seg//60}m {seg%60:02d}s"
    return f"{seg//3600}h {(seg%3600)//60:02d}m"


def _simular_martingala(decs: list, filtro_idx=2, base_apuesta=0.1) -> dict:
    """Simula estrategia Martingala sobre un filtro del historico.
    Duplica la apuesta tras cada perdida. Al ganar, vuelve a la base.
    Evalua la lambda estricta del filtro elegido (sin override de PnL real)."""
    try:
        nombre, _, filtro_fn, contrarian, raw = FILTROS_CURVA[filtro_idx]
    except Exception:
        return {'n_bets': 0, 'error': 'filtro no valido'}

    if filtro_fn is None or isinstance(filtro_fn, str):
        return {'n_bets': 0, 'error': 'filtro especial (sin lambda)'}

    total_pnl = 0.0
    peak = 0.0
    drawdown_max = 0.0
    current_bet = base_apuesta
    max_stake = base_apuesta
    streak_loses = 0
    max_streak = 0
    streaks_hist = {}

    n_bets = 0
    n_wins = 0

    for d in decs:
        winner = (d.get('winner') or '').lower()
        mayor  = (d.get('mayor')  or '').lower()
        if not winner or not mayor:
            continue

        modo = d.get('modo', 'SKIP')
        wr   = float(d.get('wr') or 50)
        if modo == 'BASE':
            modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')

        gano_mayoria = (winner == mayor)
        op = {
            'skip':         modo == 'SKIP',
            'acierto':      bool(d.get('acierto', False)),
            'gano_mayoria': gano_mayoria,
            'modo':         modo,
            'rango':        d.get('rango', '?'),
            'est':          d.get('est', 'ESTABLE'),
            'acel':         float(d.get('acel') or 0),
            'wr':           wr,
            'mult':         float(d.get('mult') or 1),
        }

# ── Sincronización del TXT del reconstructor ────────────────────────────
# En cada ronda nueva (cambio detectado en pnl_decision_history.json) se copia
# Z:\Python\Peak\reconstructor_data_AI.txt → carpeta local del proyecto.
RECONSTRUCTOR_REMOTO = Path(r"Z:\Python\Peak\reconstructor_data_AI.txt")
RECONSTRUCTOR_LOCAL  = Path(__file__).parent / "reconstructor_data_AI.txt"

STATE_FILE = Path(__file__).parent / "monitor_decisiones_state.json"


def _sync_reconstructor_txt() -> tuple[bool, str]:
    """Copia el TXT remoto al local. Devuelve (ok, mensaje)."""
    try:
        if not RECONSTRUCTOR_REMOTO.exists():
            return False, f"no existe: {RECONSTRUCTOR_REMOTO}"
        shutil.copy2(RECONSTRUCTOR_REMOTO, RECONSTRUCTOR_LOCAL)
        sz = RECONSTRUCTOR_LOCAL.stat().st_size
        return True, f"copiado {sz} bytes"
    except Exception as e:
        return False, str(e)


def _ep_gate_activo(d) -> bool:
    """Una ronda tiene EP GATE activo cuando emitió señal direccional
    (regímenes EP, ANTI, OK). NO_SIGNAL/SKIP_REG/WARMUP/'' → inactivo."""
    eg = (d.get('ep_gate') or '').strip().upper()
    if not eg:
        return False
    return (eg.startswith('EP ') or eg.startswith('ANTI ') or
            eg.startswith('OK '))


def _ep_regimen(d) -> str:
    """Devuelve el régimen del EP gate: 'EP', 'ANTI', 'OK', 'SKIP_REG', 'NO_SIGNAL', 'OFF'..."""
    eg = (d.get('ep_gate') or '').strip()
    if not eg:
        return 'OFF'
    first = eg.split(' ', 1)[0].upper()
    return first


# ════════════════════════════════════════════════════════════════════════
#                    ANÁLISIS  (lógica idéntica a la versión anterior)
# ════════════════════════════════════════════════════════════════════════

def _delta_teorico(d: dict, i: int) -> float:
    winner = (d.get('winner') or '').lower()
    mayor  = (d.get('mayor')  or '').lower()
    if not winner or not mayor:
        return 0.0
    modo = d.get('modo', 'SKIP')
    mult = float(d.get('mult') or 1)
    wr   = float(d.get('wr') or 50)
    modo_t = modo
    if modo == 'BASE':
        modo_t = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
    op = {
        'skip': modo_t == 'SKIP',
        'acierto': bool(d.get('acierto', False)),
        'gano_mayoria': winner == mayor,
        'modo': modo_t,
        'rango': d.get('rango', '?'),
        'est': d.get('est', 'ESTABLE'),
        'acel': float(d.get('acel') or 0),
        'wr': wr, 'mult': mult,
    }
    try:
        _, _, fn, contrarian, raw = FILTROS_CURVA[i]
    except Exception:
        return 0.0
    if fn is None or isinstance(fn, str):
        return 0.0
    if op['skip'] and not raw:
        return 0.0
    if not fn(op):
        return 0.0
    gano = op['gano_mayoria'] if raw else (
           op['acierto'] if modo_t != 'INVERSO' else not op['acierto'])
    if contrarian:
        gano = not gano
    return round(0.9 * mult if gano else -1.0 * mult, 2)


def analizar(decs: list, martingala_filtro=2) -> dict:
    total = len(decs)
    if total == 0:
        return {'vacio': True}
    apostadas = [d for d in decs if d.get('decision') == 'APOSTADA' and d.get('winner')]
    n_ap = len(apostadas)
    n_ac = sum(1 for d in apostadas if (d.get('pnl') or 0) > 0)
    wr_glob = (n_ac / n_ap * 100) if n_ap else 0
    balance = round(sum((d.get('pnl') or 0) for d in apostadas), 2)
    last20 = apostadas[-20:] if len(apostadas) >= 20 else apostadas
    n_ac_20 = sum(1 for d in last20 if (d.get('pnl') or 0) > 0)
    wr_20 = (n_ac_20 / len(last20) * 100) if last20 else 0
    bal_20 = round(sum((d.get('pnl') or 0) for d in last20), 2)

    racha = 0
    signo_racha = None
    for d in reversed(apostadas):
        p = d.get('pnl') or 0
        s = 'WIN' if p > 0 else ('LOSE' if p < 0 else None)
        if s is None:
            break
        if signo_racha is None:
            signo_racha = s; racha = 1
        elif s == signo_racha:
            racha += 1
        else:
            break

    modos = Counter(d.get('modo') for d in decs)
    decs_validas = [d for d in decs if d.get('winner')]
    ventana = decs_validas[-50:] if len(decs_validas) >= 50 else decs_validas
    # Saldos por filtro: leer desde pnl_filtros_long.jsonl (fuente de verdad,
    # mismo origen que las columnas "Balance filtro"/"Saldo" del panel).
    saldos_full = _saldos_filtros_long()           # acumulado total de sesión
    issues_ventana = {d.get('issue') for d in ventana if d.get('issue')}
    saldos_ventana = _saldos_filtros_long(issues_ventana) if issues_ventana else {}

    pnl_teor = []
    for i in range(1, len(FILTROS_CURVA)):
        try:
            nombre = FILTROS_CURVA[i][0]
        except Exception:
            continue
        # Preferir suma de deltas en la ventana de 50r; si no hay, usar saldo
        # acumulado total. Fallback final: pnl_filtros del record (legacy).
        if i in saldos_full:
            suma = saldos_full[i]
        elif i in saldos_ventana:
            suma = saldos_ventana[i]
        else:
            suma = 0.0
            tiene_datos = False
            for d in ventana:
                pf = d.get('pnl_filtros') or {}
                v = pf.get(str(i))
                if v is None:
                    v = pf.get(i)
                if v is None:
                    continue
                tiene_datos = True
                suma += v
            if not tiene_datos:
                continue
        pnl_teor.append((i, nombre, round(suma, 2)))
    pnl_teor.sort(key=lambda x: x[2], reverse=True)
    top3 = pnl_teor[:3]
    bot3 = pnl_teor[-3:][::-1] if len(pnl_teor) >= 3 else []

    rangos = Counter(d.get('rango') for d in apostadas)
    rango_top = rangos.most_common(1)[0] if rangos else ('-', 0)

    # ── Análisis por rango (sobre últimas 100 rondas apostadas) ───────────
    apostadas_w = apostadas[-100:] if len(apostadas) >= 100 else apostadas
    rng_stats = {}
    for d in apostadas_w:
        rng = d.get('rango', '?')
        s = rng_stats.setdefault(rng, {'n': 0, 'ac': 0, 'pnl': 0.0})
        s['n']   += 1
        s['pnl'] += (d.get('pnl') or 0)
        if (d.get('pnl') or 0) > 0:
            s['ac'] += 1
    rng_list = []
    for rng, s in rng_stats.items():
        if s['n'] >= 2:   # mínimo 2 apuestas para entrar al ranking
            wr_r = s['ac'] / s['n'] * 100
            rng_list.append({'rng': rng, 'n': s['n'], 'wr': wr_r,
                              'pnl': round(s['pnl'], 2)})
    rng_list.sort(key=lambda r: r['pnl'], reverse=True)
    rng_top3 = rng_list[:3]
    rng_bot3 = rng_list[-3:][::-1] if len(rng_list) >= 3 else []

    # ── EP GATE: detección y métricas específicas ──────────────────────────
    apostadas_ep = [d for d in apostadas if _ep_gate_activo(d)]
    n_ep   = len(apostadas_ep)
    n_ac_ep = sum(1 for d in apostadas_ep if (d.get('pnl') or 0) > 0)
    wr_ep  = (n_ac_ep / n_ep * 100) if n_ep else 0
    bal_ep = round(sum((d.get('pnl') or 0) for d in apostadas_ep), 2)

    # Estado actual del gate: mirar últimas 5 rondas (no solo la última, ruido)
    ult5 = decs[-5:]
    regimenes_recientes = Counter(_ep_regimen(d) for d in ult5)
    # Si en las últimas 5 rondas hay algún EP/ANTI/OK → activo
    activos_recientes = sum(regimenes_recientes.get(k, 0) for k in ('EP', 'ANTI', 'OK'))
    ep_actualmente_activo = activos_recientes >= 1
    # Régimen más frecuente reciente
    reg_top = regimenes_recientes.most_common(1)[0][0] if regimenes_recientes else 'OFF'
    # Régimen de la última ronda
    ult_reg = _ep_regimen(decs[-1]) if decs else 'OFF'

    # Racha EP (consecutivas en rondas EP-activas)
    racha_ep = 0
    sig_ep   = None
    for d in reversed(apostadas_ep):
        p = d.get('pnl') or 0
        s = 'WIN' if p > 0 else ('LOSE' if p < 0 else None)
        if s is None: break
        if sig_ep is None:
            sig_ep, racha_ep = s, 1
        elif s == sig_ep:
            racha_ep += 1
        else:
            break

    last = decs[-1]
    ult = {
        'hora': last.get('hora') or last.get('timestamp', '?'),
        'mayor': last.get('mayor', '?'),
        'winner': last.get('winner') or '(en curso)',
        'modo': last.get('modo', '?'),
        'pnl': last.get('pnl'),
        'rango': last.get('rango', '?'),
    }
    estado = 'NORMAL'
    color_estado = C['warn']
    if n_ap >= 5:
        if wr_20 >= 55 and bal_20 > 0:
            estado, color_estado = 'BIEN', C['accent2']
        elif wr_20 <= 40 or bal_20 < -2:
            estado, color_estado = 'MAL', C['accent3']

    diag = []
    if n_ap < 5:
        diag.append(f"Solo {n_ap} apuestas. Muestra insuficiente.")
    else:
        if wr_20 > wr_glob + 5:
            diag.append(f"Tendencia POSITIVA — 20r ({wr_20:.0f}%) > global ({wr_glob:.0f}%)")
        elif wr_20 < wr_glob - 5:
            diag.append(f"Tendencia NEGATIVA — 20r ({wr_20:.0f}%) < global ({wr_glob:.0f}%)")
        else:
            diag.append(f"WR estable ({wr_20:.0f}% ~ media {wr_glob:.0f}%)")
        if racha >= 3:
            verbo = "ganando" if signo_racha == 'WIN' else "perdiendo"
            diag.append(f"Racha {racha}r {verbo}")
        if top3 and top3[0][2] > 1:
            diag.append(f"Mejor filtro 50r: #{top3[0][0]} {top3[0][1]} ({top3[0][2]:+.1f})")

    # ── Diagnóstico EP GATE específico ────────────────────────────────────
    if ep_actualmente_activo and n_ep >= 3:
        if wr_ep >= 55 and bal_ep > 0:
            diag.append(f"EP GATE rinde BIEN: {wr_ep:.0f}% WR en {n_ep} apuestas EP ({bal_ep:+.2f})")
        elif wr_ep <= 40 or bal_ep < -1:
            diag.append(f"EP GATE rinde MAL: {wr_ep:.0f}% WR en {n_ep} apuestas EP ({bal_ep:+.2f})")
        else:
            diag.append(f"EP GATE neutral: {wr_ep:.0f}% WR ({n_ep} ap, {bal_ep:+.2f})")
    elif not ep_actualmente_activo and n_ep > 0:
        diag.append(f"EP GATE inactivo ahora · histórico: {n_ep} ap / WR {wr_ep:.0f}%")

    # Mejor rango
    if rng_top3 and rng_top3[0]['pnl'] > 1:
        r = rng_top3[0]
        diag.append(f"Mejor rango: {r['rng']} · WR {r['wr']:.0f}% · {r['n']} ap · {r['pnl']:+.2f}")
    if rng_bot3 and rng_bot3[0]['pnl'] < -1:
        r = rng_bot3[0]
        diag.append(f"Peor rango: {r['rng']} · WR {r['wr']:.0f}% · {r['n']} ap · {r['pnl']:+.2f}")

    alertas = []
    if bal_20 < -3:
        alertas.append(f"BALANCE 20r MUY NEGATIVO ({bal_20:+.2f})")
    if racha >= 5 and signo_racha == 'LOSE':
        alertas.append(f"RACHA NEGATIVA LARGA ({racha}r)")
    skip_pct = (modos.get('SKIP', 0) / total * 100) if total else 0
    if skip_pct > 70:
        alertas.append(f"{skip_pct:.0f}% RONDAS EN SKIP")
    if ep_actualmente_activo and racha_ep >= 4 and sig_ep == 'LOSE':
        alertas.append(f"EP GATE: {racha_ep} LOSES CONSECUTIVOS")
    if n_ep >= 10 and wr_ep < 35:
        alertas.append(f"EP GATE WR MUY BAJO ({wr_ep:.0f}%)")

    return {
        'vacio': False, 'estado': estado, 'color_estado': color_estado,
        'total': total, 'apostadas': n_ap, 'aciertos': n_ac,
        'wr_global': wr_glob, 'balance': balance,
        'wr_20': wr_20, 'bal_20': bal_20,
        'racha': racha, 'signo_racha': signo_racha,
        'modos': modos, 'top3': top3, 'bot3': bot3,
        'rango_top': rango_top, 'rng_top3': rng_top3, 'rng_bot3': rng_bot3,
        'ultima': ult,
        'diag': diag, 'alertas': alertas, 'skip_pct': skip_pct,
        # EP GATE
        'ep_activo': ep_actualmente_activo,
        'ep_n':      n_ep,
        'ep_wr':     wr_ep,
        'ep_bal':    bal_ep,
        'ep_racha':  racha_ep,
        'ep_signo':  sig_ep,
        'ep_reg_top': reg_top,
        'ep_reg_ult': ult_reg,
        # FRECUENCIA
        'freq': _analizar_frecuencia(decs),
        # MARTINGALA
        'martingala': _simular_martingala(decs, filtro_idx=martingala_filtro),
    }


# ════════════════════════════════════════════════════════════════════════
#                              HUD  CYBERPUNK
# ════════════════════════════════════════════════════════════════════════

class HUD:
    def __init__(self, root):
        self.root = root
        self.root.title("◤ MONITOR DECISIONES ◢")
        self.root.configure(bg=C['bg'])
        self.root.geometry(f"{W}x{H+40}")
        self.root.resizable(False, False)

        # ── Barra de control: selector de filtro Martingala ─────────
        self._mg_filtro = 2
        self._mg_nombres = [FILTROS_CURVA[i][0] for i in range(13)]
        self._mg_idx_by_name = {v: k for k, v in enumerate(self._mg_nombres)}
        mg_bar = tk.Frame(root, bg=C['panel'], height=36)
        mg_bar.pack(fill='x', side='top')
        mg_bar.pack_propagate(False)
        tk.Frame(mg_bar, bg=C['accent'], height=2).pack(fill='x')
        inner = tk.Frame(mg_bar, bg=C['panel'])
        inner.pack(fill='x', padx=10, pady=4)
        tk.Label(inner, text="MARTINGALA:", font=(FUENTE, 9, 'bold'),
                 bg=C['panel'], fg=C['muted']).pack(side='left', padx=(2, 6))
        self._mg_btn = tk.Menubutton(inner, text=self._mg_nombres[2],
                                      bg='#0D2137', fg=C['accent'],
                                      font=(FUENTE, 9, 'bold'),
                                      relief='flat', padx=6, pady=1,
                                      activebackground='#1A3050',
                                      activeforeground=C['accent2'])
        self._mg_btn.pack(side='left')
        mg_menu = tk.Menu(self._mg_btn, tearoff=0, bg=C['panel'], fg=C['text'],
                          font=(FUENTE, 10), activebackground='#1A3050',
                          activeforeground=C['accent2'])
        for nombre in self._mg_nombres:
            mg_menu.add_command(label=nombre,
                command=lambda n=nombre: self._on_mg_select(n))
        self._mg_btn.config(menu=mg_menu)

        self.canvas = tk.Canvas(root, width=W, height=H,
                                 bg=C['bg'], highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self._ultimo_mtime = 0
        self._info = None
        self._tick = 0
        self._variables = {}
        self._var_tick = 0
        self._sync_estado = (None, "esperando primera ronda", "—")
        self._last_close_ts = None
        self._gap_minutos = None
        self._inicio_ts = datetime.now()
        self._collapsed = {}            # {block_key: bool}
        self._block_y1_shifted = {}     # {block_key: y1_real_en_canvas}
        self._cum_shift = 0
        self._block_before_items = None
        self._cur_block = None
        self._load_state()
        self._dibujar_estatico()
        self.canvas.bind('<Button-1>', self._on_click)
        self._loop()

    def _load_state(self):
        """Lee STATE_FILE si existe y rellena atributos de estado.
        Si el mtime guardado coincide con el actual del JSON de decisiones,
        evita disparar sync redundante en el primer tick."""
        try:
            if not STATE_FILE.exists():
                return
            st = json.loads(STATE_FILE.read_text(encoding='utf-8'))
            self._ultimo_mtime = float(st.get('last_mtime') or 0)
            ok  = st.get('last_sync_ok')
            msg = st.get('last_sync_msg') or 'estado previo restaurado'
            hor = st.get('last_sync_hora') or '—'
            self._sync_estado = (ok, msg, hor)
            cl = st.get('collapsed') or {}
            if isinstance(cl, dict):
                self._collapsed = {k: bool(v) for k, v in cl.items()}
            lct = st.get('last_close_ts')
            if lct:
                try:
                    self._last_close_ts = datetime.fromisoformat(lct)
                    gap = (self._inicio_ts - self._last_close_ts).total_seconds()
                    if gap > 300:
                        self._gap_minutos = int(gap // 60)
                except Exception:
                    pass
        except Exception:
            pass

    def _save_state(self):
        """Persiste el estado actual a STATE_FILE. Silencioso ante errores."""
        try:
            ok, msg, hor = self._sync_estado
            total = (self._info or {}).get('total') if self._info else None
            data = {
                'last_mtime': self._ultimo_mtime,
                'last_sync_ok': ok,
                'last_sync_msg': msg,
                'last_sync_hora': hor,
                'last_seen_total_rondas': total,
                'last_close_ts': datetime.now().isoformat(timespec='seconds'),
                'collapsed': self._collapsed,
            }
            STATE_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except Exception:
            pass

    # ── helpers de dibujo ───────────────────────────────────────────
    def _hex_to_rgb(self, h):
        h = h.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

    def _rgb_to_hex(self, r, g, b):
        return '#%02X%02X%02X' % (max(0,min(255,int(r))),
                                  max(0,min(255,int(g))),
                                  max(0,min(255,int(b))))

    def _mix(self, hex_color, factor):
        """Mezcla un color con el bg según factor (0=bg, 1=color)."""
        r1, g1, b1 = self._hex_to_rgb(hex_color)
        r2, g2, b2 = self._hex_to_rgb(C['bg'])
        return self._rgb_to_hex(r2+(r1-r2)*factor, g2+(g1-g2)*factor, b2+(b1-b2)*factor)

    def _panel(self, x1, y1, x2, y2, titulo=None, color=None):
        """Dibuja un panel con bordes neón y esquinas chamfered (HUD)."""
        color = color or C['accent']
        c = self.canvas
        t = 'dyn'
        # Fondo del panel
        c.create_rectangle(x1, y1, x2, y2, fill=C['panel'], outline='', width=0, tags=t)
        # Borde con esquinas cortadas (cyberpunk)
        ch = 8  # chamfer
        c.create_line(x1+ch, y1, x2-ch, y1, fill=color, width=1, tags=t)
        c.create_line(x1+ch, y2, x2-ch, y2, fill=color, width=1, tags=t)
        c.create_line(x1, y1+ch, x1, y2-ch, fill=color, width=1, tags=t)
        c.create_line(x2, y1+ch, x2, y2-ch, fill=color, width=1, tags=t)
        # Esquinas chamfered
        c.create_line(x1, y1+ch, x1+ch, y1, fill=color, width=1, tags=t)
        c.create_line(x2-ch, y1, x2, y1+ch, fill=color, width=1, tags=t)
        c.create_line(x1, y2-ch, x1+ch, y2, fill=color, width=1, tags=t)
        c.create_line(x2-ch, y2, x2, y2-ch, fill=color, width=1, tags=t)
        # Marcadores (bolitas) en esquinas — siempre azul, independientes del color del borde
        for cx, cy in [(x1+ch, y1), (x2-ch, y1), (x1+ch, y2), (x2-ch, y2)]:
            c.create_oval(cx-2, cy-2, cx+2, cy+2,
                          fill=C['blue'], outline='', tags=t)
        # Título opcional
        if titulo:
            tw = len(titulo) * 8 + 10
            c.create_rectangle(x1+18, y1-9, x1+18+tw, y1+9,
                                fill=C['bg'], outline=color, width=1, tags=t)
            c.create_text(x1+18+tw//2, y1, text=f"◤ {titulo} ◢",
                          font=(FUENTE, 9, 'bold'),
                          fill=color, tags=t)

    def _grid_bg(self):
        """Cuadrícula sutil de fondo (estética HUD)."""
        c = self.canvas
        col = '#0D2137'
        step = 28
        for x in range(0, W, step):
            c.create_line(x, 0, x, H, fill=col, width=1)
        for y in range(0, H, step):
            c.create_line(0, y, W, y, fill=col, width=1)
        # Marcadores diagonales en esquinas
        for (cx, cy) in [(0,0),(W,0),(0,H),(W,H)]:
            r = 30
            sgnx = 1 if cx == 0 else -1
            sgny = 1 if cy == 0 else -1
            c.create_line(cx, cy+sgny*r, cx+sgnx*r, cy, fill=C['accent'], width=2)

    def _dibujar_estatico(self):
        """Cosas que no cambian: grid, marco principal, header."""
        c = self.canvas
        self._grid_bg()
        # Marco principal
        c.create_rectangle(4, 4, W-4, H-4, outline=C['accent'], width=1)
        c.create_rectangle(2, 2, W-2, H-2, outline=C['border'], width=1)

    def _texto_glow(self, x, y, txt, color, font, anchor='center'):
        """Texto con efecto glow (dibujado dos veces con offset)."""
        c = self.canvas
        # Sombra glow
        for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
            c.create_text(x+dx, y+dy, text=txt, font=font,
                          fill=self._mix(color, 0.35), anchor=anchor, tags='dyn')
        # Texto principal
        c.create_text(x, y, text=txt, font=font, fill=color, anchor=anchor, tags='dyn')

    def _barra(self, x1, y, x2, valor_pct, color):
        """Barra horizontal de progreso (0-100)."""
        c = self.canvas
        c.create_rectangle(x1, y, x2, y+6, fill='#0D2137', outline=C['border'], tags='dyn')
        ancho = (x2-x1) * max(0, min(100, valor_pct)) / 100.0
        if ancho > 0:
            c.create_rectangle(x1, y, x1+ancho, y+6,
                                fill=color, outline=color, tags='dyn')
        # Marca al 50%
        mid = x1 + (x2-x1)*0.5
        c.create_line(mid, y-2, mid, y+8, fill=C['muted'], width=1, tags='dyn')

    # ── soporte de bloques plegables ────────────────────────────────
    def _block_begin(self, key):
        """Marca el inicio del dibujado de un bloque para poder taggear
        después todos los items creados y aplicarles el shift acumulado."""
        self._cur_block = key
        self._block_before_items = set(self.canvas.find_withtag('dyn'))

    def _block_end(self, key, h):
        """Cierra el bloque: taggea los items recién creados con blk_<key>,
        les aplica el shift vertical acumulado, y registra y1 real (para
        detectar clicks). Si el bloque está colapsado, acumula el ahorro
        de espacio para los bloques siguientes."""
        after = set(self.canvas.find_withtag('dyn'))
        new_items = after - (self._block_before_items or set())
        tag = f'blk_{key}'
        for iid in new_items:
            self.canvas.addtag_withtag(tag, iid)
        # Buscar y1 original
        y1_orig = next((y for k, _, y, _ in BLOCKS_GEOM if k == key), None)
        y1_real = (y1_orig or 0) + self._cum_shift
        self._block_y1_shifted[key] = y1_real
        if self._cum_shift:
            self.canvas.move(tag, 0, self._cum_shift)
        if self._collapsed.get(key):
            self._cum_shift -= (h - COLLAPSED_H)

    def _draw_collapsed_bar(self, key, titulo, y1, color):
        """Dibuja una barra delgada con el título y el indicador ▸."""
        c = self.canvas
        x1, x2 = 30, W - 30
        y2 = y1 + COLLAPSED_H
        c.create_rectangle(x1, y1, x2, y2, fill=C['panel'],
                           outline=color, width=1, tags='dyn')
        # Título centrado
        c.create_text(x1 + 18 + 6, (y1 + y2)//2,
                      text=f"▸ {titulo}", fill=color,
                      font=(FUENTE, 9, 'bold'), anchor='w', tags='dyn')
        # Hint de click a la derecha
        c.create_text(x2 - 10, (y1 + y2)//2, text="click para expandir",
                      fill=C['muted'], font=(FUENTE, 8), anchor='e', tags='dyn')

    def _draw_block_toggle(self, key, titulo, y1, color):
        """Dibuja un indicador ▾/▸ en la esquina derecha del chip del título
        para que se vea que el bloque es plegable."""
        c = self.canvas
        x1, x2 = 30, W - 30
        # El chip del título está en x1+18..x1+18+tw, en y1±9
        tw = len(titulo) * 8 + 10
        # Pintamos el indicador junto al borde derecho del chip
        ico = '▾'   # expandido
        c.create_text(x1 + 18 + tw - 8, y1, text=ico,
                      fill=color, font=(FUENTE, 9, 'bold'),
                      anchor='center', tags='dyn')

    def _on_click(self, event):
        """Detecta click en la zona de título de algún bloque y alterna
        su estado de colapsado."""
        ey = event.y
        ex = event.x
        for key, titulo, _, h in BLOCKS_GEOM:
            y1 = self._block_y1_shifted.get(key)
            if y1 is None:
                continue
            # Zona clickable = barra superior del bloque (±12px en torno al título)
            if y1 - 12 <= ey <= y1 + 12 and 30 <= ex <= W - 30:
                self._collapsed[key] = not self._collapsed.get(key, False)
                self._save_state()
                self._redibujar()
                return

    def _on_mg_select(self, nombre):
        idx = self._mg_idx_by_name.get(nombre)
        if idx is None or idx == self._mg_filtro:
            return
        self._mg_filtro = idx
        self._mg_btn.config(text=nombre)
        try:
            decs = json.loads(DECISION_HIST_FILE.read_text(encoding='utf-8'))
            self._info = analizar(decs, martingala_filtro=self._mg_filtro)
        except Exception:
            self._info = {'vacio': True}
        self._redibujar()

    # ── pintado completo ────────────────────────────────────────────
    def _redibujar(self):
        c = self.canvas
        c.delete('dyn')   # solo borrar capa dinámica
        # Re-pintar lo estático SI es necesario (esquinas etc — ya están)

        info = self._info
        if not info or info.get('vacio'):
            c.create_text(W//2, H//2, text="◤ ESPERANDO DATOS ◢",
                          font=(FUENTE, 18, 'bold'),
                          fill=C['muted'], tags='dyn')
            return

        # ════ HEADER ════
        y = 24
        self._texto_glow(W//2, y, "▌ MONITOR DECISIONES ▐",
                          C['accent'], (FUENTE, 16, 'bold'))
        c.create_line(60, y+14, W-60, y+14, fill=C['accent'], width=1, tags='dyn')

        # Tick clock (pequeño, esquina derecha)
        now = datetime.now().strftime('%H:%M:%S')
        c.create_text(W-30, y, text=f"[{now}]", fill=C['muted'],
                       font=(FUENTE, 9, 'bold'), anchor='e', tags='dyn')
        c.create_text(30, y, text=f"[{info['total']} R]", fill=C['muted'],
                       font=(FUENTE, 9, 'bold'), anchor='w', tags='dyn')
        # Indicador de sync del reconstructor
        sync_ok, sync_msg, sync_hora = self._sync_estado
        if sync_ok is True:
            sync_col, sync_ico = C['accent2'], '⬢'
            sync_txt = f"{sync_ico} SYNC OK · {sync_hora}"
        elif sync_ok is False:
            sync_col, sync_ico = C['accent3'], '○'
            sync_txt = f"{sync_ico} SYNC FAIL · {sync_msg[:30]}"
        else:
            sync_col, sync_ico = C['muted'], '○'
            sync_txt = f"{sync_ico} {sync_msg}"
        c.create_text(W-30, y+18, text=sync_txt, fill=sync_col,
                       font=(FUENTE, 8, 'bold'), anchor='e', tags='dyn')

        # Chip "REANUDADO" durante los primeros 30s tras un arranque con gap > 5min
        if self._gap_minutos is not None:
            desde_inicio = (datetime.now() - self._inicio_ts).total_seconds()
            if desde_inicio < 30:
                c.create_text(30, y+18,
                              text=f"⏸ REANUDADO tras {self._gap_minutos}m",
                              fill=C['warn'], font=(FUENTE, 8, 'bold'),
                              anchor='w', tags='dyn')

        self._cum_shift = 0

        # ════ ESTADO BLOCK ════
        self._block_begin('estado')
        if self._collapsed.get('estado'):
            self._draw_collapsed_bar('estado', 'ESTADO', 50, info['color_estado'])
        else:
            bx1, by1, bx2, by2 = 30, 50, W-30, 158
            self._panel(bx1, by1, bx2, by2, "ESTADO", info['color_estado'])
            # Estado grande
            self._texto_glow(W//2, 100, info['estado'],
                              info['color_estado'], (FUENTE, 42, 'bold'))
            # Sub-info
            c.create_text(W//2, 138, text="◢ análisis sobre últimas 20 rondas ◣",
                           fill=C['muted'], font=(FUENTE, 9), tags='dyn')
        self._block_end('estado', 108)

        # ════ ÚLTIMA RONDA ════
        self._block_begin('ultima')
        if self._collapsed.get('ultima'):
            self._draw_collapsed_bar('ultima', 'ÚLTIMA RONDA', 178, C['warn'])
        else:
            bx1, by1, bx2, by2 = 30, 178, W-30, 232
            self._panel(bx1, by1, bx2, by2, "ÚLTIMA RONDA", C['warn'])
            u = info['ultima']
            pnl_txt = f"  Δ={u['pnl']:+.2f}" if isinstance(u['pnl'], (int, float)) else "  Δ=—"
            col_w = C['accent2'] if u['winner'] == u['mayor'] else (
                    C['accent3'] if u['winner'] != '(en curso)' else C['muted'])
            # Línea de datos — usa caracteres seguros en Consolas
            # Color de MAYOR según valor: AZUL azul, ROJO rojo
            mayor_upper = (u['mayor'] or '').upper()
            if mayor_upper == 'AZUL':
                col_m = C['blue']
            elif mayor_upper == 'ROJO':
                col_m = C['red']
            else:
                col_m = C['accent']

            # Color winner: verde si == mayor, rojo si ≠ y resuelto, gris si en curso
            winner_real = u['winner'] and u['winner'] != '(en curso)'
            if winner_real:
                col_w = C['accent2'] if u['winner'].upper() == mayor_upper else C['accent3']
            else:
                col_w = C['muted']

            c.create_text(50, 200, anchor='w', tags='dyn',
                           text=f"⏱ {u['hora']}",
                           fill=C['muted'], font=(FUENTE, 11))
            c.create_text(200, 200, anchor='w', tags='dyn',
                           text="MAYOR ▶", fill=C['text'],
                           font=(FUENTE, 11))
            c.create_text(280, 200, anchor='w', tags='dyn',
                           text=mayor_upper or '-', fill=col_m,
                           font=(FUENTE, 11, 'bold'))
            c.create_text(380, 200, anchor='w', tags='dyn',
                           text="WINNER ▶", fill=C['text'],
                           font=(FUENTE, 11))
            c.create_text(470, 200, anchor='w', tags='dyn',
                           text=u['winner'] or '-', fill=col_w,
                           font=(FUENTE, 11, 'bold'))
            c.create_text(50, 218, anchor='w', tags='dyn',
                           text=f"MODO ▶ {u['modo']}    RANGO ▶ {u['rango']}{pnl_txt}",
                           fill=C['muted'], font=(FUENTE, 10))
        self._block_end('ultima', 54)

        # ════ EP GATE ════
        ep_col = C['accent2'] if info['ep_activo'] else C['muted']
        self._block_begin('ep_gate')
        if self._collapsed.get('ep_gate'):
            self._draw_collapsed_bar('ep_gate', 'EP GATE', 252, ep_col)
        else:
            bx1, by1, bx2, by2 = 30, 252, W-30, 336
            self._panel(bx1, by1, bx2, by2, "EP GATE", ep_col)

            # Indicador grande izquierda: ACTIVO/INACTIVO
            estado_ep = "● ACTIVO" if info['ep_activo'] else "○ INACTIVO"
            self._texto_glow(120, 290, estado_ep, ep_col, (FUENTE, 16, 'bold'))
            # Régimen actual
            reg_txt = f"régimen: {info['ep_reg_ult']}"
            c.create_text(120, 314, text=reg_txt, fill=C['muted'],
                          font=(FUENTE, 10), tags='dyn', anchor='center')

            # Métricas EP a la derecha (4 columnas mini)
            ep_n = info['ep_n']
            if ep_n > 0:
                ep_wr_col  = C['accent2'] if info['ep_wr']  >= 50 else C['accent3']
                ep_bal_col = C['accent2'] if info['ep_bal'] >= 0  else C['accent3']
                cols = [
                    ("APUESTAS EP", f"{ep_n}", C['text']),
                    ("WR EP",       f"{info['ep_wr']:.0f}%", ep_wr_col),
                    ("BAL EP",      f"{info['ep_bal']:+.2f}", ep_bal_col),
                    ("RACHA EP",
                     (f"{info['ep_racha']} {'W' if info['ep_signo']=='WIN' else 'L'}"
                      if info['ep_racha'] >= 1 else "—"),
                     C['accent2'] if info['ep_signo'] == 'WIN' else (
                     C['accent3'] if info['ep_signo'] == 'LOSE' else C['muted'])),
                ]
                x0 = 260
                col_w = 110
                for i, (lbl, val, col_v) in enumerate(cols):
                    xx = x0 + i*col_w
                    c.create_text(xx, 280, text=lbl, fill=C['muted'],
                                  font=(FUENTE, 9, 'bold'),
                                  anchor='center', tags='dyn')
                    c.create_text(xx, 302, text=val, fill=col_v,
                                  font=(FUENTE, 14, 'bold'),
                                  anchor='center', tags='dyn')
            else:
                c.create_text(W//2+80, 294, text="sin apuestas EP todavía",
                              fill=C['muted'], font=(FUENTE, 11), tags='dyn')

            # Barra WR EP
            if ep_n > 0:
                c.create_text(280, 322, text="WR", fill=C['muted'],
                              font=(FUENTE, 9, 'bold'), anchor='w', tags='dyn')
                self._barra(310, 321, W-50, info['ep_wr'],
                            C['accent2'] if info['ep_wr'] >= 50 else C['accent3'])
        self._block_end('ep_gate', 84)

        # ════ MÉTRICAS GRID ════
        self._block_begin('metricas')
        if self._collapsed.get('metricas'):
            self._draw_collapsed_bar('metricas', 'MÉTRICAS GLOBALES', 356, C['accent'])
        else:
            bx1, by1, bx2, by2 = 30, 356, W-30, 596
            self._panel(bx1, by1, bx2, by2, "MÉTRICAS GLOBALES", C['accent'])
            bal_col = C['accent2'] if info['balance'] >= 0 else C['accent3']
            bal20_col = C['accent2'] if info['bal_20'] >= 0 else C['accent3']
            wr_col   = C['accent2'] if info['wr_global'] >= 50 else C['accent3']
            wr20_col = C['accent2'] if info['wr_20'] >= 50 else C['accent3']

            col1_x = 60
            col1_lx = 250
            col2_x = 380
            col2_lx = 580
            labels_lefty = 384
            dy = 28

            v = self._variables
            apuesta_val = v.get('APUESTA', '—')
            obj_val = v.get('OBJETIVO', '—')
            try:
                obj_f = float(obj_val.replace(',', '.'))
                obj_str = f"{obj_f:+.2f} €"
                obj_col = C['accent2'] if obj_f >= 0 else C['accent3']
            except Exception:
                obj_str = obj_val
                obj_col = C['text']

            metricas_l = [
                ("APUESTAS",      f"{info['apostadas']}/{info['total']}", C['text']),
                ("BALANCE",       f"{info['balance']:+.2f} €", bal_col),
                ("WR GLOBAL",     f"{info['wr_global']:.1f}%", wr_col),
                ("RANGO TOP",     f"{info['rango_top'][0]} ({info['rango_top'][1]})", C['text']),
                ("APUESTA",       f"{apuesta_val} €", C['accent']),
            ]
            metricas_r = [
                ("BAL 20R",       f"{info['bal_20']:+.2f} €", bal20_col),
                ("WR 20R",        f"{info['wr_20']:.1f}%", wr20_col),
                ("RACHA",         (f"{info['racha']} {'WINs' if info['signo_racha']=='WIN' else 'LOSEs'}"
                                    if info['racha']>=1 else "—"),
                                  C['accent2'] if info['signo_racha']=='WIN' else (
                                  C['accent3'] if info['signo_racha']=='LOSE' else C['muted'])),
                ("SKIP %",        f"{info['skip_pct']:.0f}%",
                                  C['warn'] if info['skip_pct']>70 else C['text']),
                ("OBJETIVO",      obj_str, obj_col),
            ]
            for i, (lbl, val, col_v) in enumerate(metricas_l):
                yy = labels_lefty + i*dy
                c.create_text(col1_x, yy, text="▸ "+lbl, fill=C['muted'],
                              font=(FUENTE, 10, 'bold'), anchor='w', tags='dyn')
                c.create_text(col1_lx, yy, text=val, fill=col_v,
                              font=(FUENTE, 12, 'bold'), anchor='e', tags='dyn')
            for i, (lbl, val, col_v) in enumerate(metricas_r):
                yy = labels_lefty + i*dy
                c.create_text(col2_x, yy, text="▸ "+lbl, fill=C['muted'],
                              font=(FUENTE, 10, 'bold'), anchor='w', tags='dyn')
                c.create_text(col2_lx, yy, text=val, fill=col_v,
                              font=(FUENTE, 12, 'bold'), anchor='e', tags='dyn')

            # WR bar
            c.create_text(60, 520, text="▸ WR 20r", fill=C['muted'],
                           font=(FUENTE, 10, 'bold'), anchor='w', tags='dyn')
            self._barra(150, 519, W-50, info['wr_20'], wr20_col)
            c.create_text(W-30, 520, text=f"{info['wr_20']:.0f}%",
                           fill=wr20_col, font=(FUENTE, 10, 'bold'),
                           anchor='e', tags='dyn')

            # ── VARS compactas desde Sheets ──
            tg   = v.get('TELEGRAM', '?')
            bots = v.get('BOTS_USO', v.get('BOT_USO', '?'))
            ginit = v.get('EP_GATE_INIT', '?')
            warm = v.get('WARMUP', '?')
            umb_hi = v.get('EP_UMBRAL_HI', v.get('UMBRAL_HI', '?'))
            umb_lo = v.get('EP_UMBRAL_LO', v.get('UMBRAL_LO', '?'))
            blk   = v.get('EP_RANGOS_BLOQUEADOS', v.get('RANGOS_BLOQUEADOS', ''))
            wr70  = v.get('DIR_WR_70', v.get('DIR_WR70', '?'))
            wr80  = v.get('DIR_WR_80', v.get('DIR_WR80', '?'))
            wrper = v.get('WR_PERDEDORA', v.get('WR_MAYORIA', '?'))
            acel  = v.get('ACEL_UMBRAL', v.get('VOLATIL_UMBRAL', '?'))
            ur    = v.get('EP_UMBRAL_POR_RANGO', v.get('UMBRAL_POR_RANGO', ''))
            hacer = v.get('HACER_APUESTA', '?')
            multm = v.get('MULT_MAXIMO', '?')
            fiab  = v.get('FILTRO_FIAB', '?')

            c.create_text(50, 542, anchor='w', tags='dyn',
                          text="▸ VARS", fill=C['accent'],
                          font=(FUENTE, 9, 'bold'))
            c.create_text(90, 542, anchor='w', tags='dyn',
                          text=f"TG={tg}  BOTS={bots}  GATE_INIT={ginit}  WARMUP={warm}  UMBRAL={umb_hi}-{umb_lo}  HACER={hacer}",
                          fill=C['text'], font=(FUENTE, 9))
            c.create_text(50, 558, anchor='w', tags='dyn',
                          text=f"WR70={wr70}  WR80={wr80}  WR_PERD={wrper}  ACEL={acel}  MMAX={multm}  FIAB={fiab}",
                          fill=C['text'], font=(FUENTE, 9))
            if blk or ur:
                extra = f"  BLK=[{blk}]" if blk else ""
                extra += f"  UMBRAL_RNG=[{ur[:20]}]" if ur else ""
                c.create_text(50, 574, anchor='w', tags='dyn',
                              text=extra.strip(),
                              fill=C['muted'], font=(FUENTE, 9))
        self._block_end('metricas', 240)

        # ════ TOP/BOT FILTROS ════
        self._block_begin('filtros')
        if self._collapsed.get('filtros'):
            self._draw_collapsed_bar('filtros', 'FILTROS', 616, C['accent2'])
        else:
            bx1, by1, bx2, by2 = 30, 616, W-30, 752
            self._panel(bx1, by1, bx2, by2, "FILTROS", C['accent2'])
            c.create_text(50, 636, text="▲ MEJORES", fill=C['accent2'],
                           font=(FUENTE, 10, 'bold'), anchor='w', tags='dyn')
            if info['top3']:
                for i, (idx, nombre, pnl) in enumerate(info['top3']):
                    yy = 656 + i*16
                    c.create_text(60,  yy, anchor='w', tags='dyn',
                                   text=f"#{idx:2}  {nombre[:22]:<22}",
                                   fill=C['text'], font=(FUENTE, 10))
                    col_p = C['accent2'] if pnl >= 0 else C['accent3']
                    c.create_text(W//2-10, yy, anchor='e', tags='dyn',
                                   text=f"{pnl:+6.2f}",
                                   fill=col_p, font=(FUENTE, 11, 'bold'))

            # Vertical separator
            c.create_line(W//2, 634, W//2, 744, fill=C['border'], width=1, tags='dyn')

            c.create_text(W//2+20, 636, text="▼ PEORES", fill=C['accent3'],
                           font=(FUENTE, 10, 'bold'), anchor='w', tags='dyn')
            if info['bot3']:
                for i, (idx, nombre, pnl) in enumerate(info['bot3']):
                    yy = 656 + i*16
                    c.create_text(W//2+30, yy, anchor='w', tags='dyn',
                                   text=f"#{idx:2}  {nombre[:18]:<18}",
                                   fill=C['text'], font=(FUENTE, 10))
                    col_p = C['accent2'] if pnl >= 0 else C['accent3']
                    c.create_text(W-50, yy, anchor='e', tags='dyn',
                                   text=f"{pnl:+6.2f}",
                                   fill=col_p, font=(FUENTE, 11, 'bold'))
        self._block_end('filtros', 136)

        # ════ RANGOS · 100r ════
        self._block_begin('rangos')
        if self._collapsed.get('rangos'):
            self._draw_collapsed_bar('rangos', 'RANGOS · 100r', 772, C['accent'])
        else:
            bx1, by1, bx2, by2 = 30, 772, W-30, 906
            self._panel(bx1, by1, bx2, by2, "RANGOS · 100r", C['accent'])
            # Cabecera columnas
            c.create_text(50,  792, text="▲ MEJORES", fill=C['accent2'],
                           font=(FUENTE, 10, 'bold'), anchor='w', tags='dyn')
            c.create_text(W//2+20, 792, text="▼ PEORES", fill=C['accent3'],
                           font=(FUENTE, 10, 'bold'), anchor='w', tags='dyn')
            # Separador vertical
            c.create_line(W//2, 790, W//2, 900, fill=C['border'], width=1, tags='dyn')

            # Sub-cabecera
            c.create_text(60,  808, text="rango   n   WR    PNL",
                           fill=C['muted'], font=(FUENTE, 9), anchor='w', tags='dyn')
            c.create_text(W//2+30, 808, text="rango   n   WR    PNL",
                           fill=C['muted'], font=(FUENTE, 9), anchor='w', tags='dyn')

            # Filas mejores
            if info['rng_top3']:
                for i, r in enumerate(info['rng_top3']):
                    yy = 828 + i*18
                    col_p = C['accent2'] if r['pnl'] >= 0 else C['accent3']
                    col_wr = C['accent2'] if r['wr'] >= 50 else C['accent3']
                    txt = f"{r['rng']:<7}{r['n']:>3}  "
                    c.create_text(60, yy, text=txt, fill=C['text'],
                                   font=(FUENTE, 10), anchor='w', tags='dyn')
                    c.create_text(165, yy, text=f"{r['wr']:.0f}%",
                                   fill=col_wr, font=(FUENTE, 10, 'bold'),
                                   anchor='w', tags='dyn')
                    c.create_text(W//2-10, yy, text=f"{r['pnl']:+6.2f}",
                                   fill=col_p, font=(FUENTE, 11, 'bold'),
                                   anchor='e', tags='dyn')

            # Filas peores
            if info['rng_bot3']:
                for i, r in enumerate(info['rng_bot3']):
                    yy = 828 + i*18
                    col_p = C['accent2'] if r['pnl'] >= 0 else C['accent3']
                    col_wr = C['accent2'] if r['wr'] >= 50 else C['accent3']
                    txt = f"{r['rng']:<7}{r['n']:>3}  "
                    c.create_text(W//2+30, yy, text=txt, fill=C['text'],
                                   font=(FUENTE, 10), anchor='w', tags='dyn')
                    c.create_text(W//2+135, yy, text=f"{r['wr']:.0f}%",
                                   fill=col_wr, font=(FUENTE, 10, 'bold'),
                                   anchor='w', tags='dyn')
                    c.create_text(W-50, yy, text=f"{r['pnl']:+6.2f}",
                                   fill=col_p, font=(FUENTE, 11, 'bold'),
                                   anchor='e', tags='dyn')

            if not info['rng_top3']:
                c.create_text(W//2, 850, text="(sin datos suficientes · mín 2 ap/rango)",
                              fill=C['muted'], font=(FUENTE, 10), tags='dyn')
        self._block_end('rangos', 134)

        # ════ DIAGNÓSTICO ════
        self._block_begin('diag')
        if self._collapsed.get('diag'):
            self._draw_collapsed_bar('diag', 'DIAGNÓSTICO', 926, C['warn'])
        else:
            bx1, by1, bx2, by2 = 30, 926, W-30, 1050
            self._panel(bx1, by1, bx2, by2, "DIAGNÓSTICO", C['warn'])
            if info['diag']:
                for i, txt in enumerate(info['diag'][:5]):
                    yy = 946 + i*20
                    c.create_text(50, yy, anchor='w', tags='dyn',
                                   text="▶", fill=C['warn'],
                                   font=(FUENTE, 11, 'bold'))
                    c.create_text(70, yy, anchor='w', tags='dyn',
                                   text=txt, fill=C['text'],
                                   font=(FUENTE, 10))
        self._block_end('diag', 124)

        # ════ ALERTAS ════
        col_a = C['accent3'] if info['alertas'] else C['muted']
        self._block_begin('alertas')
        if self._collapsed.get('alertas'):
            self._draw_collapsed_bar('alertas', 'ALERTAS', 1070, col_a)
        else:
            bx1, by1, bx2, by2 = 30, 1070, W-30, 1134
            self._panel(bx1, by1, bx2, by2, "ALERTAS", col_a)
            if info['alertas']:
                for i, txt in enumerate(info['alertas'][:3]):
                    yy = 1090 + i*16
                    # Símbolo de alerta parpadeante
                    blink = '◆' if self._tick % 2 == 0 else '◇'
                    c.create_text(50, yy, anchor='w', tags='dyn',
                                   text=blink, fill=C['accent3'],
                                   font=(FUENTE, 11, 'bold'))
                    c.create_text(70, yy, anchor='w', tags='dyn',
                                   text=txt, fill=C['accent3'],
                                   font=(FUENTE, 10, 'bold'))
            else:
                c.create_text(W//2, 1102, text="◇ SIN ALERTAS ◇",
                              fill=C['muted'], font=(FUENTE, 11), tags='dyn')
        self._block_end('alertas', 64)

        # ════ FRECUENCIA · PRÓXIMA APUESTA ════
        freq = info.get('freq') or {}
        col_f = C['accent']
        # Estado del temporizador hacia la próxima apuesta determina el color
        if freq and not freq.get('pocos_datos') and freq.get('seg_a_proxima') is not None:
            sp = freq['seg_a_proxima']
            if sp < 0:    col_f = C['warn']    # ya debería haber apostado
            elif sp < 60: col_f = C['accent2']  # inminente
        self._block_begin('freq')
        if self._collapsed.get('freq'):
            self._draw_collapsed_bar('freq', 'FRECUENCIA · PRÓXIMA APUESTA', 1154, col_f)
        else:
            bx1, by1, bx2, by2 = 30, 1154, W-30, 1304
            self._panel(bx1, by1, bx2, by2, "FRECUENCIA · PRÓXIMA APUESTA", col_f)

            if not freq or freq.get('pocos_datos'):
                c.create_text(W//2, 1230, text="◇ Necesitan ≥ 2 rondas con timestamp ◇",
                              fill=C['muted'], font=(FUENTE, 11), tags='dyn')
            else:
                # Línea 1: cadencias
                c.create_text(60, 1174, text="▸ CADENCIA RONDAS", fill=C['muted'],
                              font=(FUENTE, 9, 'bold'), anchor='w', tags='dyn')
                c.create_text(250, 1174, text=f"{_fmt_dur(freq['cad_ronda'])}/r",
                              fill=C['text'], font=(FUENTE, 11, 'bold'),
                              anchor='w', tags='dyn')
                c.create_text(360, 1174, text=f"({freq['rondas_hora']:.0f}/h)",
                              fill=C['muted'], font=(FUENTE, 10),
                              anchor='w', tags='dyn')

                c.create_text(60, 1194, text="▸ CADENCIA APUESTAS", fill=C['muted'],
                              font=(FUENTE, 9, 'bold'), anchor='w', tags='dyn')
                c.create_text(250, 1194, text=f"{_fmt_dur(freq['cad_apuesta'])}/ap",
                              fill=C['accent'], font=(FUENTE, 11, 'bold'),
                              anchor='w', tags='dyn')
                c.create_text(360, 1194, text=f"({freq['apuestas_hora']:.1f}/h)",
                              fill=C['muted'], font=(FUENTE, 10),
                              anchor='w', tags='dyn')

                c.create_text(60, 1214, text="▸ % APUESTA", fill=C['muted'],
                              font=(FUENTE, 9, 'bold'), anchor='w', tags='dyn')
                pct = freq['pct_apuesta']
                col_pct = C['accent2'] if pct > 25 else (C['warn'] if pct > 10 else C['accent3'])
                c.create_text(250, 1214, text=f"{pct:.1f}%",
                              fill=col_pct, font=(FUENTE, 11, 'bold'),
                              anchor='w', tags='dyn')
                self._barra(310, 1213, W-50, pct, col_pct)

                # Línea hueco
                c.create_line(50, 1234, W-50, 1234, fill=C['border'], width=1, tags='dyn')

                # Última apuesta
                if freq.get('ult_apuesta'):
                    c.create_text(60, 1250, text="▸ ÚLTIMA APUESTA HACE",
                                  fill=C['muted'], font=(FUENTE, 9, 'bold'),
                                  anchor='w', tags='dyn')
                    dur = _fmt_dur(freq['desde_ult_apuesta'])
                    col_d = C['accent2'] if freq['desde_ult_apuesta'] < freq['cad_apuesta']*1.3 else (
                            C['warn'] if freq['desde_ult_apuesta'] < freq['cad_apuesta']*2 else C['accent3'])
                    c.create_text(280, 1250, text=dur,
                                  fill=col_d, font=(FUENTE, 12, 'bold'),
                                  anchor='w', tags='dyn')
                    hora_ult = freq['ult_apuesta'].strftime('%H:%M:%S')
                    c.create_text(400, 1250, text=f"({hora_ult})",
                                  fill=C['muted'], font=(FUENTE, 10),
                                  anchor='w', tags='dyn')

                # Próxima estimada
                if freq.get('proxima_estim'):
                    c.create_text(60, 1276, text="▸ PRÓXIMA ESTIMADA",
                                  fill=C['muted'], font=(FUENTE, 9, 'bold'),
                                  anchor='w', tags='dyn')
                    hora_prox = freq['proxima_estim'].strftime('%H:%M:%S')
                    seg = freq['seg_a_proxima']
                    if seg is not None and seg > 0:
                        txt_seg = f"en {_fmt_dur(seg)}"
                        col_p = C['accent2'] if seg < 60 else C['accent']
                    elif seg is not None:
                        txt_seg = f"hace {_fmt_dur(-seg)}  ⚠ debería ya"
                        col_p = C['warn']
                    else:
                        txt_seg, col_p = "—", C['muted']
                    self._texto_glow(280, 1276, hora_prox, col_p,
                                     (FUENTE, 14, 'bold'), anchor='w')
                    c.create_text(400, 1276, text=txt_seg,
                                  fill=col_p, font=(FUENTE, 11, 'bold'),
                                  anchor='w', tags='dyn')
        self._block_end('freq', 150)

        # ════ MARTINGALA · Solo INVERSO ════
        mg = info.get('martingala') or {}
        col_mg = C['accent2'] if mg.get('pnl', 0) >= 0 else C['accent3']
        self._block_begin('martingala')
        if self._collapsed.get('martingala'):
            self._draw_collapsed_bar('martingala', 'MARTINGALA', 1324, col_mg)
        else:
            bx1, by1, bx2, by2 = 30, 1324, W-30, 1484
            mg_nombre = mg.get('nombre', '?')
            self._panel(bx1, by1, bx2, by2, f"MARTINGALA · {mg_nombre}", col_mg)

            if mg.get('n_bets', 0) == 0:
                c.create_text(W//2, 1404, text="◇ sin operaciones INVERSO todavia ◇",
                              fill=C['muted'], font=(FUENTE, 11), tags='dyn')
            else:
                base = mg['base_apuesta']
                col_pnl_mg   = C['accent2'] if mg['pnl'] >= 0 else C['accent3']
                col_pnl_flat = C['accent2'] if mg['pnl_flat'] >= 0 else C['accent3']
                col_rach     = C['accent3'] if mg['max_streak'] >= 5 else C['warn']
                LX, RX = 60, 380   # columna izquierda, derecha

                # ── Fila 1 ──
                y = 1350
                c.create_text(LX, y, anchor='w', tags='dyn',
                              text=f"▸ Base {base:.2f}€", fill=C['muted'],
                              font=(FUENTE, 10, 'bold'))
                c.create_text(RX, y, anchor='w', tags='dyn',
                              text=f"Ops {mg['n_bets']}  ·  WR {mg['wr']:.0f}%",
                              fill=C['text'], font=(FUENTE, 12, 'bold'))

                # ── Fila 2 ──
                y = 1376
                c.create_text(LX, y, anchor='w', tags='dyn',
                              text="▸ PnL Martingala", fill=C['muted'],
                              font=(FUENTE, 10, 'bold'))
                c.create_text(LX+170, y, anchor='w', tags='dyn',
                              text=f"{mg['pnl']:+.2f}€",
                              fill=col_pnl_mg, font=(FUENTE, 12, 'bold'))
                c.create_text(RX, y, anchor='w', tags='dyn',
                              text="▸ PnL Flat", fill=C['muted'],
                              font=(FUENTE, 10, 'bold'))
                c.create_text(RX+110, y, anchor='w', tags='dyn',
                              text=f"{mg['pnl_flat']:+.2f}€",
                              fill=col_pnl_flat, font=(FUENTE, 12, 'bold'))

                # ── Fila 3 ──
                y = 1402
                c.create_text(LX, y, anchor='w', tags='dyn',
                              text="▸ Max stake", fill=C['muted'],
                              font=(FUENTE, 10, 'bold'))
                c.create_text(LX+130, y, anchor='w', tags='dyn',
                              text=f"{mg['max_stake']:.2f}€",
                              fill=C['warn'], font=(FUENTE, 12, 'bold'))
                c.create_text(RX, y, anchor='w', tags='dyn',
                              text="▸ Max racha", fill=C['muted'],
                              font=(FUENTE, 10, 'bold'))
                c.create_text(RX+130, y, anchor='w', tags='dyn',
                              text=f"{mg['max_streak']} perdidas",
                              fill=col_rach, font=(FUENTE, 11, 'bold'))

                # ── Fila 4 ──
                y = 1428
                c.create_text(LX, y, anchor='w', tags='dyn',
                              text="▸ Drawdown max", fill=C['muted'],
                              font=(FUENTE, 10, 'bold'))
                c.create_text(LX+165, y, anchor='w', tags='dyn',
                              text=f"{mg['drawdown_max']:+.2f}€",
                              fill=C['accent3'], font=(FUENTE, 12, 'bold'))
                c.create_text(RX, y, anchor='w', tags='dyn',
                              text="▸ Capital min", fill=C['muted'],
                              font=(FUENTE, 10, 'bold'))
                c.create_text(RX+145, y, anchor='w', tags='dyn',
                              text=f"{mg['capital_min']:.2f}€",
                              fill=C['text'], font=(FUENTE, 12, 'bold'))

                # ── Separador + Histograma ──
                y_sep = 1450
                c.create_line(50, y_sep, W-50, y_sep, fill=C['border'], width=1, tags='dyn')
                c.create_text(LX, y_sep+14, anchor='w', tags='dyn',
                              text="RACHAS DE PERDIDA",
                              fill=C['muted'], font=(FUENTE, 8, 'bold'))

                streaks = mg.get('streaks', {})
                if streaks:
                    max_v = max(streaks.values())
                    bar_max_w = 90
                    col_x = 60
                    for k, v in sorted(streaks.items()):
                        xx = col_x + ((k-1) % 5) * 120
                        yy = y_sep + 28
                        bar_w = max(4, int(v / max_v * bar_max_w))
                        c.create_text(xx, yy, anchor='w', tags='dyn',
                                      text=f"{k} loss", fill=C['muted'],
                                      font=(FUENTE, 8))
                        c.create_rectangle(xx+5, yy+6, xx+5+bar_w, yy+12,
                                          fill=C['accent3'], outline='', tags='dyn')
                        c.create_text(xx+10+bar_w, yy+9, anchor='w', tags='dyn',
                                      text=f"{v}x", fill=C['text'],
                                      font=(FUENTE, 8, 'bold'))

        self._block_end('martingala', 160)

    def _refresh_vars(self):
        self._variables = _leer_variables_sheets()

    def _loop(self):
        self._tick += 1
        self._var_tick += 1
        # Refrescar variables de Sheets cada ~30 ticks (45s)
        if self._var_tick >= 30:
            self._var_tick = 0
            threading.Thread(target=self._refresh_vars, daemon=True).start()
        # Comprobar cambio de fichero
        try:
            if DECISION_HIST_FILE.exists():
                mtime = DECISION_HIST_FILE.stat().st_mtime
                cambio = (mtime != self._ultimo_mtime)
                if cambio:
                    self._ultimo_mtime = mtime
                    # ── RONDA NUEVA: copiar TXT del reconstructor ──────
                    ok, msg = _sync_reconstructor_txt()
                    self._sync_estado = (ok, msg, datetime.now().strftime('%H:%M:%S'))
                if cambio or self._info is None:
                    try:
                        decs = json.loads(DECISION_HIST_FILE.read_text(encoding='utf-8'))
                        self._info = analizar(decs, martingala_filtro=self._mg_filtro)
                    except Exception as e:
                        self._info = {'vacio': True, '_err': str(e)}
                if cambio:
                    self._save_state()
            elif self._info is None:
                self._info = {'vacio': True}
        except Exception:
            pass
        # Re-pintar siempre (para refrescar reloj y blink de alertas)
        self._redibujar()
        self.root.after(REFRESH_MS, self._loop)


if __name__ == '__main__':
    root = tk.Tk()
    hud = HUD(root)

    def _on_close():
        hud._save_state()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()
