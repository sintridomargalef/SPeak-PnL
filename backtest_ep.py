"""
backtest_ep.py — BackTesting de la Estrategia Perfecta Simulada.

Genera N ventanas aleatorias (o deslizantes) de M registros consecutivos
desde reconstructor_data_AI.txt, ejecuta la simulación EP en cada una,
y reporta estadísticas de rendimiento.

Uso:
    python3 backtest_ep.py                                    # Default
    python3 backtest_ep.py --sliding                           # Ventanas deslizantes
    python3 backtest_ep.py --multi-sim                         # Compara todas las variantes
    python3 backtest_ep.py --csv resultados.csv                # Exportar CSV
    python3 backtest_ep.py --grafico backtest.png              # Exportar gráfico
    python3 backtest_ep.py --window-size 100 --n-windows 500
    python3 backtest_ep.py --simulador simular_combinado       # Otra variante EP
    python3 backtest_ep.py --apuesta 5.0                       # Apuesta base 5€
    python3 backtest_ep.py --max-mult 3                        # Multiplicador máx 3x
    python3 backtest_ep.py --grafico-ventanas                  # Una gráfica por ventana
"""

import json
import sys
import os
import argparse
import math
from collections import Counter
from pathlib import Path

import numpy as np

# ── Importar simuladores de los módulos existentes ──────────────────────
# Nota: parsear_archivo solo necesita re, no tkinter
import re as _re

# ── Constantes EP (copiadas para evitar importar tkinter) ──────────────
EP_VENTANA = 50
EP_MIN_OPS = 10
from pnl_config import EP_UMBRAL_MIN as EP_UMBRAL_ESTADO
PNL_ACIERTO = 0.9
PNL_FALLO = -1.0

# ── Helpers de visualización ANSI (tema cyberpunk) ─────────────────────
_W = 70  # ancho visible del dashboard

def _habilitar_colores_windows():
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        try:
            os.system('')
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass

_A = {
    'R':  '\033[0m',
    'B':  '\033[1m',
    'cy': '\033[38;2;0;212;255m',
    'gn': '\033[38;2;0;255;136m',
    'rd': '\033[38;2;255;51;102m',
    'am': '\033[38;2;255;184;0m',
    'tx': '\033[38;2;200;216;232m',
    'mu': '\033[38;2;74;96;128m',
    'dm': '\033[38;2;40;65;90m',
}

def _c(k, t):  return _A.get(k, '') + str(t) + _A['R']

def _sec(titulo):
    dashes = '─' * max(1, _W - 5 - len(titulo))
    return (f'\n{_A["cy"]}  ◈{_A["R"]} {_A["B"]}{_A["cy"]}'
            f'{titulo}{_A["R"]} {_A["dm"]}{dashes}{_A["R"]}')

def _barra(pct, ancho=36, col='gn'):
    n = min(ancho, max(0, round(pct / 100 * ancho)))
    return _A[col] + '█' * n + _A['dm'] + '░' * (ancho - n) + _A['R']

def _pnl_str(v):
    if v > 0:   return f'{_A["gn"]}{_A["B"]}+{v:.2f}{_A["R"]}'
    elif v < 0: return f'{_A["rd"]}{_A["B"]}{v:.2f}{_A["R"]}'
    return f'{_A["mu"]}  0.00{_A["R"]}'

def _cp_print(icon, label, value=''):
    print(f'  {_A["cy"]}{icon}{_A["R"]}  {_A["mu"]}{label}{_A["R"]}  {_A["tx"]}{value}{_A["R"]}')

def _fmt_dur(seg):
    """Formatea segundos como '1h 23m 45s' / '23m 45s' / '45s'."""
    if seg is None:
        return '—'
    seg = max(0, int(round(seg)))
    h, r = divmod(seg, 3600)
    m, s = divmod(r, 60)
    if h: return f'{h}h {m:02d}m {s:02d}s'
    if m: return f'{m}m {s:02d}s'
    return f'{s}s'


# ── Copia local de simular_ep_por_rango (sin tkinter) ──────────────────
# No podemos importar analizar_ep.py porque estrategia_perfecta.py importa tkinter
# Así que incorporamos las funciones necesarias aquí mismo.

def ep_mult(conf: float, max_mult: int = 0) -> int:
    """Multiplicador con límite opcional. max_mult=0 = sin límite."""
    if conf >= 90: mult = 7
    elif conf >= 85: mult = 6
    elif conf >= 80: mult = 5
    elif conf >= 75: mult = 4
    elif conf >= 70: mult = 3
    elif conf >= 65: mult = 2
    elif conf >= 60: mult = 1
    else: mult = 1
    if max_mult > 0 and mult > max_mult:
        return max_mult
    return mult


def _calc_tiempos(ops: list, detalles: list | None = None) -> dict:
    """Calcula duración de la ventana e intervalo medio entre apuestas."""
    ts_ini = _ts_a_seg(ops[0].get('timestamp')) if ops else None
    ts_fin = _ts_a_seg(ops[-1].get('timestamp')) if ops else None
    duracion = (ts_fin - ts_ini) if (ts_ini is not None and ts_fin is not None) else None
    intervalo = None
    if detalles:
        ts_b = [_ts_a_seg(d.get('ts')) for d in detalles if d.get('apostar') and d.get('ts')]
        ts_b = [t for t in ts_b if t is not None]
        if len(ts_b) >= 2:
            diffs = [ts_b[i+1] - ts_b[i] for i in range(len(ts_b)-1)]
            intervalo = sum(diffs) / len(diffs)
    return {
        'ts_inicio': ops[0].get('timestamp') if ops else None,
        'ts_fin': ops[-1].get('timestamp') if ops else None,
        'duracion_seg': duracion,
        'intervalo_apuestas_seg': intervalo,
    }


def _cap_mult_saldo(mult: int, saldo_actual: float, apuesta_base: float,
                    saldo_inicial: float) -> int:
    """Limita el multiplicador a lo que el saldo disponible puede arriesgar.
    saldo_inicial=0 → sin límite por saldo (comportamiento original)."""
    if saldo_inicial <= 0 or apuesta_base <= 0:
        return mult
    mult_max = max(1, int(saldo_actual / apuesta_base))
    return min(mult, mult_max)


def simular_ep_por_rango(ops: list, ventana: int = EP_VENTANA,
                         max_mult: int = 0, apuesta_base: float = 1.0,
                         saldo_inicial: float = 0.0) -> dict:
    """
    Ventana rolling por RANGO+MODO, con multiplicador EP por confianza.
    max_mult > 0 limita el multiplicador máximo (ej: max_mult=3 → máx 3x).
    saldo_inicial > 0 limita el multiplicador dinámicamente al bankroll.
    Devuelve primera_pos: índice de la primera apuesta EP (calentamiento).
    """
    from collections import deque
    ventanas = {}
    bal_real = [0.0]
    bal_ep = [0.0]
    n_bets = 0
    n_skips = 0
    detalles = []
    primera_apuesta = None
    saldo = saldo_inicial
    saldo_min = saldo_inicial
    bancarrota = False

    for op in ops:
        rango = op['rango']
        modo = op['modo']
        ganada = op['ganada']

        if rango not in ventanas:
            ventanas[rango] = {}
        if modo not in ventanas[rango]:
            ventanas[rango][modo] = deque(maxlen=ventana)

        v = ventanas[rango][modo]
        n_v = len(v)
        ganada_modo = ganada if modo == 'DIRECTO' else not ganada
        wr_v = sum(v) / n_v * 100 if n_v >= EP_MIN_OPS else 0.0
        mult = ep_mult(wr_v, max_mult) if n_v >= EP_MIN_OPS else 1
        mult = _cap_mult_saldo(mult, saldo, apuesta_base, saldo_inicial)
        pnl_base = (PNL_ACIERTO if ganada_modo else PNL_FALLO) * mult * apuesta_base
        apostar = n_v >= EP_MIN_OPS and wr_v >= EP_UMBRAL_ESTADO

        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            _m_real = op.get('mult', 1)
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO) * _m_real)

        if apostar:
            if primera_apuesta is None:
                primera_apuesta = len(detalles)
            bal_ep.append(bal_ep[-1] + pnl_base)
            saldo += pnl_base
            if saldo < saldo_min:
                saldo_min = saldo
            if saldo_inicial > 0 and saldo < 0:
                bancarrota = True
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        detalles.append({
            'rango': rango, 'modo': modo, 'ganada': ganada,
            'wr_v': wr_v, 'apostar': apostar, 'n_v': n_v,
            'mult': mult, 'pnl': pnl_base if apostar else 0,
            'saldo': saldo, 'ts': op.get('timestamp'),
        })
        v.append(1 if ganada_modo else 0)

    return {
        'bal_real': bal_real, 'bal_ep': bal_ep,
        'n_bets': n_bets, 'n_skips': n_skips, 'n_total': len(ops),
        'saldo_real': bal_real[-1], 'saldo_ep': bal_ep[-1],
        'detalles': detalles,
        'ventanas': ventanas,
        'primera_pos': primera_apuesta,
        'saldo_inicial': saldo_inicial,
        'saldo_final': saldo,
        'saldo_min': saldo_min,
        'bancarrota': bancarrota,
        **_calc_tiempos(ops, detalles),
    }


def simular_ep_rolling(ops: list, ventana: int = 20, umbral: float = 53.2,
                       min_wr_dir: int = 0, contrarian: bool = False,
                       apuesta_base: float = 1.0,
                       saldo_inicial: float = 0.0) -> dict:
    """EP con ventana rolling GLOBAL. Idéntica a analizar_ep.simular_ep_rolling().
    saldo_inicial > 0 limita el multiplicador dinámicamente al bankroll."""
    from collections import deque
    v = deque(maxlen=ventana)
    acum = 0.0
    saldo_real_acum = 0.0
    n_ac = 0
    n_bets = 0
    prev_wr = 50.0
    saldo = saldo_inicial
    saldo_min = saldo_inicial
    bancarrota = False

    for op in ops:
        n_v = len(v)
        if n_v >= EP_MIN_OPS:
            wr = sum(v) / n_v * 100
            if wr >= umbral:
                nueva_dir = 'DIRECTO'
            elif wr <= (100 - umbral):
                nueva_dir = 'INVERSO'
            else:
                v.append(1 if op['acierto'] else 0)
                prev_wr = op.get('wr', 50)
                saldo_real_acum += (PNL_ACIERTO if op['acierto'] else PNL_FALLO) * op.get('mult', 1)
                continue

            dir_efectiva = ('INVERSO' if nueva_dir == 'DIRECTO' else 'DIRECTO') if contrarian else nueva_dir

            if min_wr_dir > 0:
                wr_op = prev_wr
                if dir_efectiva == 'DIRECTO' and wr_op < min_wr_dir:
                    v.append(1 if op['acierto'] else 0)
                    prev_wr = op.get('wr', 50)
                    saldo_real_acum += (PNL_ACIERTO if op['acierto'] else PNL_FALLO) * op.get('mult', 1)
                    continue
                if dir_efectiva == 'INVERSO' and wr_op > (100 - min_wr_dir):
                    v.append(1 if op['acierto'] else 0)
                    prev_wr = op.get('wr', 50)
                    saldo_real_acum += (PNL_ACIERTO if op['acierto'] else PNL_FALLO) * op.get('mult', 1)
                    continue

            gano = op['acierto'] if dir_efectiva == 'DIRECTO' else not op['acierto']
            _m = op.get('mult', 1)
            _m = _cap_mult_saldo(_m, saldo, apuesta_base, saldo_inicial)
            if gano:
                pnl = 0.9 * _m * apuesta_base
                acum += pnl
                saldo += pnl
                n_ac += 1
            else:
                pnl = -1.0 * _m * apuesta_base
                acum += pnl
                saldo += pnl
            if saldo < saldo_min:
                saldo_min = saldo
            if saldo_inicial > 0 and saldo < 0:
                bancarrota = True
            n_bets += 1

        v.append(1 if op['acierto'] else 0)
        prev_wr = op.get('wr', 50)
        saldo_real_acum += (PNL_ACIERTO if op['acierto'] else PNL_FALLO) * op.get('mult', 1)

    return {
        'pnl': acum, 'n_ac': n_ac, 'n_bets': n_bets,
        'saldo_ep': acum, 'saldo_real': saldo_real_acum,
        'n_skips': len(ops) - n_bets, 'n_total': len(ops),
        'saldo_inicial': saldo_inicial,
        'saldo_final': saldo,
        'saldo_min': saldo_min,
        'bancarrota': bancarrota,
        **_calc_tiempos(ops),
    }


def simular_combinado(ops: list, ventana: int = EP_VENTANA,
                      max_mult: int = 0, apuesta_base: float = 1.0,
                      saldo_inicial: float = 0.0) -> dict:
    """EP combinado (histórico + ventana rolling). max_mult limita el multiplicador.
    saldo_inicial > 0 limita el multiplicador dinámicamente al bankroll."""
    from collections import deque
    acum = {}
    ventanas = {}
    bal_real = [0.0]
    bal_ep = [0.0]
    n_bets = 0
    n_skips = 0
    saldo = saldo_inicial
    saldo_min = saldo_inicial
    bancarrota = False

    def wr_acum(r, m):
        b = acum.get(r, {}).get(m, {})
        o = b.get('ops', 0)
        return b['ganadas'] / o * 100 if o >= EP_MIN_OPS else None

    def wr_vent(r, m):
        v = ventanas.get(r, {}).get(m, deque())
        n = len(v)
        return sum(v) / n * 100 if n >= EP_MIN_OPS else None

    for op in ops:
        rango = op['rango']; modo = op['modo']; ganada = op['ganada']
        if rango not in acum: acum[rango] = {}
        if modo not in acum[rango]: acum[rango][modo] = {'ops': 0, 'ganadas': 0}
        if rango not in ventanas: ventanas[rango] = {}
        if modo not in ventanas[rango]: ventanas[rango][modo] = deque(maxlen=ventana)

        d_hist = wr_acum(rango, 'DIRECTO'); i_hist = wr_acum(rango, 'INVERSO')
        d_vent = wr_vent(rango, 'DIRECTO'); i_vent = wr_vent(rango, 'INVERSO')

        if d_hist is not None or i_hist is not None:
            dh = d_hist or 0; ih = i_hist or 0
            best_hist = 'DIRECTO' if dh >= ih else 'INVERSO'
            best_hist_wr = max(dh, ih)
        else: best_hist = None; best_hist_wr = 0

        if d_vent is not None or i_vent is not None:
            dv = d_vent or 0; iv = i_vent or 0
            best_vent = 'DIRECTO' if dv >= iv else 'INVERSO'
            best_vent_wr = max(dv, iv)
        else: best_vent = None; best_vent_wr = 0

        if best_hist is None: apostar = False; best_mode = None; wr_final = 0
        elif best_vent is None:
            apostar = best_hist_wr >= EP_UMBRAL_ESTADO
            best_mode = best_hist; wr_final = best_hist_wr
        elif best_hist == best_vent:
            wr_final = best_vent_wr
            apostar = wr_final >= EP_UMBRAL_ESTADO
            best_mode = best_hist
        else: apostar = False; best_mode = None; wr_final = 0

        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None: bal_real.append(bal_real[-1] + pnl_orig)
        else: bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO))

        if apostar and best_mode:
            mult_real = op.get('mult_real')
            mult = mult_real if (mult_real is not None and mult_real > 0) else ep_mult(wr_final, max_mult)
            mult = _cap_mult_saldo(mult, saldo, apuesta_base, saldo_inicial)
            resultado = ganada if best_mode == modo else not ganada
            pnl = (PNL_ACIERTO if resultado else PNL_FALLO) * mult * apuesta_base
            bal_ep.append(bal_ep[-1] + pnl)
            saldo += pnl
            if saldo < saldo_min:
                saldo_min = saldo
            if saldo_inicial > 0 and saldo < 0:
                bancarrota = True
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        acum[rango][modo]['ops'] += 1
        if ganada: acum[rango][modo]['ganadas'] += 1
        ventanas[rango][modo].append(1 if ganada else 0)

    return {
        'saldo_ep': bal_ep[-1], 'saldo_real': bal_real[-1],
        'n_bets': n_bets, 'n_skips': n_skips, 'n_total': len(ops),
        'bal_ep': bal_ep, 'bal_real': bal_real,
        'saldo_inicial': saldo_inicial,
        'saldo_final': saldo,
        'saldo_min': saldo_min,
        'bancarrota': bancarrota,
        **_calc_tiempos(ops),
    }


def simular_umbral(ops: list, max_mult: int = 0, apuesta_base: float = 1.0,
                   saldo_inicial: float = 0.0) -> dict:
    """EP por umbral global por rango. max_mult limita el multiplicador.
    saldo_inicial > 0 limita el multiplicador dinámicamente al bankroll."""
    from collections import deque
    stats = {}
    for op in ops:
        r, m, g = op['rango'], op['modo'], op['ganada']
        if r not in stats: stats[r] = {}
        if m not in stats[r]: stats[r][m] = {'ops': 0, 'ganadas': 0}
        stats[r][m]['ops'] += 1
        if g: stats[r][m]['ganadas'] += 1

    mejor_modo = {}
    for rango, modos in stats.items():
        d = modos.get('DIRECTO', {}); i = modos.get('INVERSO', {})
        d_wr = d['ganadas'] / d['ops'] * 100 if d.get('ops', 0) >= EP_MIN_OPS else 0
        i_wr = i['ganadas'] / i['ops'] * 100 if i.get('ops', 0) >= EP_MIN_OPS else 0
        if d_wr >= EP_UMBRAL_ESTADO or i_wr >= EP_UMBRAL_ESTADO:
            mejor_modo[rango] = ('DIRECTO', d_wr) if d_wr >= i_wr else ('INVERSO', i_wr)
        else:
            mejor_modo[rango] = (None, 0)

    bal_real = [0.0]; bal_ep = [0.0]; n_bets = 0; n_skips = 0
    saldo = saldo_inicial
    saldo_min = saldo_inicial
    bancarrota = False
    for op in ops:
        rango = op['rango']; modo = op['modo']; ganada = op['ganada']
        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None: bal_real.append(bal_real[-1] + pnl_orig)
        else: bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO))

        mm, wr_m = mejor_modo.get(rango, (None, 0))
        if mm and wr_m >= EP_UMBRAL_ESTADO:
            resultado = ganada if mm == modo else not ganada
            mult = ep_mult(wr_m, max_mult)
            mult = _cap_mult_saldo(mult, saldo, apuesta_base, saldo_inicial)
            pnl = (PNL_ACIERTO if resultado else PNL_FALLO) * mult * apuesta_base
            bal_ep.append(bal_ep[-1] + pnl)
            saldo += pnl
            if saldo < saldo_min:
                saldo_min = saldo
            if saldo_inicial > 0 and saldo < 0:
                bancarrota = True
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

    return {
        'saldo_ep': bal_ep[-1], 'saldo_real': bal_real[-1],
        'n_bets': n_bets, 'n_skips': n_skips, 'n_total': len(ops),
        'bal_ep': bal_ep, 'bal_real': bal_real,
        'saldo_inicial': saldo_inicial,
        'saldo_final': saldo,
        'saldo_min': saldo_min,
        'bancarrota': bancarrota,
        **_calc_tiempos(ops),
    }


def simular_umbral_global(ops: list, max_mult: int = 5, apuesta_base: float = 1.0,
                          saldo_inicial: float = 0.0, ops_hist: list | None = None) -> dict:
    """EP UMBRAL global SIN lookahead — réplica de pnl_data.curva_pnl_umbral.

    Stats por (rango, modo) acumuladas desde el inicio (sin ventana rolling).
    Decide con WR previo a la op actual; actualiza stats DESPUÉS.
    `ops_hist` precarga stats sin simular (si se pasa).
    `max_mult` aplica `ep_mult(wr, max_mult)` por apuesta.
    """
    from collections import defaultdict
    stats = defaultdict(lambda: {'DIRECTO': {'ops': 0, 'ganadas': 0},
                                 'INVERSO': {'ops': 0, 'ganadas': 0}})
    # Pre-poblar stats con histórico previo (sin simular)
    for op in (ops_hist or []):
        r = op.get('rango', '')
        m = op.get('modo', '')
        if m in ('DIRECTO', 'INVERSO'):
            stats[r][m]['ops'] += 1
            if op.get('acierto', op.get('ganada', False)):
                stats[r][m]['ganadas'] += 1

    bal_ep = [0.0]
    bal_real = [0.0]
    n_bets = 0
    n_skips = 0
    saldo = saldo_inicial
    saldo_min = saldo_inicial
    bancarrota = False
    detalles = []

    for op in ops:
        rango = op.get('rango', '')
        modo  = op.get('modo', '')
        ganada = op.get('ganada', op.get('acierto', False))
        # gano_mayoria limpio cuando esté disponible (parsers nuevos lo proveen);
        # fallback derivado del par (modo, ganada) cuando el modo es direccional.
        if 'gano_mayoria' in op:
            gano_mayoria = bool(op['gano_mayoria'])
        elif modo == 'DIRECTO':
            gano_mayoria = bool(ganada)
        elif modo == 'INVERSO':
            gano_mayoria = (not bool(ganada))
        else:
            gano_mayoria = bool(ganada)

        # Curva real (mayoría siempre)
        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if gano_mayoria else PNL_FALLO))

        d = stats[rango]['DIRECTO']
        i = stats[rango]['INVERSO']
        d_wr = d['ganadas'] / d['ops'] * 100 if d['ops'] >= EP_MIN_OPS else 0.0
        i_wr = i['ganadas'] / i['ops'] * 100 if i['ops'] >= EP_MIN_OPS else 0.0
        mejor    = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
        mejor_wr = max(d_wr, i_wr)

        if mejor_wr >= EP_UMBRAL_ESTADO:
            # EP apuesta SIEMPRE en la dirección "mejor" (DIRECTO=mayoría, INVERSO=minoría).
            # Su outcome depende SOLO de gano_mayoria, no del modo del op original.
            resultado = gano_mayoria if mejor == 'DIRECTO' else (not gano_mayoria)
            mult = ep_mult(mejor_wr, max_mult)
            mult = _cap_mult_saldo(mult, saldo, apuesta_base, saldo_inicial)
            pnl  = (PNL_ACIERTO if resultado else PNL_FALLO) * mult * apuesta_base
            bal_ep.append(bal_ep[-1] + pnl)
            saldo += pnl
            if saldo < saldo_min:
                saldo_min = saldo
            if saldo_inicial > 0 and saldo < 0:
                bancarrota = True
            n_bets += 1
            detalles.append({'rango': rango, 'modo_op': modo, 'modo_apuesta': mejor,
                             'wr': mejor_wr, 'mult': mult, 'ganada': resultado, 'pnl': pnl,
                             'apostar': True, 'ts': op.get('timestamp')})
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        # Actualizar stats DESPUÉS (no lookahead)
        if modo in ('DIRECTO', 'INVERSO'):
            stats[rango][modo]['ops'] += 1
            if ganada:
                stats[rango][modo]['ganadas'] += 1

    return {
        'saldo_ep': bal_ep[-1], 'saldo_real': bal_real[-1],
        'n_bets': n_bets, 'n_skips': n_skips, 'n_total': len(ops),
        'bal_ep': bal_ep, 'bal_real': bal_real,
        'saldo_inicial': saldo_inicial,
        'saldo_final': saldo,
        'saldo_min': saldo_min,
        'bancarrota': bancarrota,
        'detalles': detalles,
        **_calc_tiempos(ops),
    }


def simular_umbral_adaptativo(ops: list, max_mult: int = 5, apuesta_base: float = 1.0,
                              saldo_inicial: float = 0.0, ops_hist: list | None = None,
                              ventana_regimen: int = 50, warmup: int = 20,
                              umbral_alto: float = 0.55, umbral_bajo: float = 0.45) -> dict:
    """EP UMBRAL adaptativo: alterna EP / anti-EP / SKIP según WR rolling de outcomes EP.

    Mantiene una ventana rolling de los últimos `ventana_regimen` outcomes que el filtro
    base EP UMBRAL hubiera obtenido. Decide:
    - WR > umbral_alto → apostar EP normal.
    - WR < umbral_bajo → invertir (anti-EP).
    - zona neutra      → SKIP.
    Durante `warmup` primeras señales válidas, sólo observa (SKIP).
    Stats por (rango, modo) globales sin lookahead, igual que `simular_umbral_global`.
    """
    from collections import defaultdict, deque
    stats = defaultdict(lambda: {'DIRECTO': {'ops': 0, 'ganadas': 0},
                                 'INVERSO': {'ops': 0, 'ganadas': 0}})
    for op in (ops_hist or []):
        r = op.get('rango', '')
        m = op.get('modo', '')
        if m in ('DIRECTO', 'INVERSO'):
            stats[r][m]['ops'] += 1
            if op.get('acierto', op.get('ganada', False)):
                stats[r][m]['ganadas'] += 1

    ventana = deque(maxlen=ventana_regimen)
    bal_ep = [0.0]
    bal_real = [0.0]
    n_bets = 0
    n_skips = 0
    n_bets_ep = 0
    n_bets_anti = 0
    saldo = saldo_inicial
    saldo_min = saldo_inicial
    bancarrota = False
    detalles = []

    for op in ops:
        rango  = op.get('rango', '')
        modo   = op.get('modo', '')
        ganada = op.get('ganada', op.get('acierto', False))
        if 'gano_mayoria' in op:
            gano_mayoria = bool(op['gano_mayoria'])
        elif modo == 'DIRECTO':
            gano_mayoria = bool(ganada)
        elif modo == 'INVERSO':
            gano_mayoria = (not bool(ganada))
        else:
            gano_mayoria = bool(ganada)

        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if gano_mayoria else PNL_FALLO))

        d = stats[rango]['DIRECTO']
        i = stats[rango]['INVERSO']
        d_wr = d['ganadas'] / d['ops'] * 100 if d['ops'] >= EP_MIN_OPS else 0.0
        i_wr = i['ganadas'] / i['ops'] * 100 if i['ops'] >= EP_MIN_OPS else 0.0
        mejor    = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
        mejor_wr = max(d_wr, i_wr)

        bet_placed = False
        if mejor_wr >= EP_UMBRAL_ESTADO:
            res_ep = gano_mayoria if mejor == 'DIRECTO' else (not gano_mayoria)
            mult = ep_mult(mejor_wr, max_mult)
            mult = _cap_mult_saldo(mult, saldo, apuesta_base, saldo_inicial)
            if len(ventana) >= warmup:
                wr_reg = sum(ventana) / len(ventana)
                if wr_reg > umbral_alto:
                    pnl = (PNL_ACIERTO if res_ep else PNL_FALLO) * mult * apuesta_base
                    bal_ep.append(bal_ep[-1] + pnl)
                    saldo += pnl
                    n_bets += 1
                    n_bets_ep += 1
                    bet_placed = True
                    detalles.append({'rango': rango, 'lado': 'EP', 'wr': mejor_wr,
                                     'mult': mult, 'ganada': res_ep, 'pnl': pnl,
                                     'apostar': True, 'wr_regimen': wr_reg,
                                     'ts': op.get('timestamp')})
                elif wr_reg < umbral_bajo:
                    res_anti = not res_ep
                    pnl = (PNL_ACIERTO if res_anti else PNL_FALLO) * mult * apuesta_base
                    bal_ep.append(bal_ep[-1] + pnl)
                    saldo += pnl
                    n_bets += 1
                    n_bets_anti += 1
                    bet_placed = True
                    detalles.append({'rango': rango, 'lado': 'ANTI', 'wr': mejor_wr,
                                     'mult': mult, 'ganada': res_anti, 'pnl': pnl,
                                     'apostar': True, 'wr_regimen': wr_reg,
                                     'ts': op.get('timestamp')})
                else:
                    bal_ep.append(bal_ep[-1])
                    n_skips += 1
            else:
                bal_ep.append(bal_ep[-1])
                n_skips += 1
            # Append outcome EP a la ventana rolling (siempre que el filtro habría apostado)
            ventana.append(1 if res_ep else 0)
            if saldo < saldo_min:
                saldo_min = saldo
            if saldo_inicial > 0 and saldo < 0:
                bancarrota = True
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        if modo in ('DIRECTO', 'INVERSO'):
            stats[rango][modo]['ops'] += 1
            if ganada:
                stats[rango][modo]['ganadas'] += 1

    return {
        'saldo_ep': bal_ep[-1], 'saldo_real': bal_real[-1],
        'n_bets': n_bets, 'n_skips': n_skips, 'n_total': len(ops),
        'n_bets_ep': n_bets_ep, 'n_bets_anti': n_bets_anti,
        'bal_ep': bal_ep, 'bal_real': bal_real,
        'saldo_inicial': saldo_inicial,
        'saldo_final': saldo,
        'saldo_min': saldo_min,
        'bancarrota': bancarrota,
        'detalles': detalles,
        **_calc_tiempos(ops),
    }


# ── Mapa de simuladores ─────────────────────────────────────────────────
SIMULADORES = {
    'simular_ep_por_rango':     simular_ep_por_rango,
    'simular_combinado':        simular_combinado,
    'simular_umbral':           simular_umbral,
    'simular_umbral_global':    simular_umbral_global,
    'simular_umbral_adaptativo': simular_umbral_adaptativo,
    'simular_ep_rolling':       simular_ep_rolling,
}

SIMULADORES_DESC = {
    'simular_ep_por_rango':      'EP × Rango (rolling por rango+modo, con mult)',
    'simular_combinado':         'EP Combinado (histórico + ventana rolling)',
    'simular_umbral':            'EP Umbral (lookahead global por rango)',
    'simular_umbral_global':     'EP Umbral GLOBAL (sin lookahead, igual que dashboard)',
    'simular_umbral_adaptativo': 'EP Umbral ADAPTATIVO (EP/anti-EP/SKIP por régimen)',
    'simular_ep_rolling':        'EP Rolling (ventana global, sin mult)',
}


# ── Parser ──────────────────────────────────────────────────────────────

_TS_RE = _re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')

def _ts_a_seg(ts: str | None):
    """Convierte 'YYYY-MM-DD HH:MM:SS' a epoch float, o None si falla."""
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(ts, '%Y-%m-%d %H:%M:%S').timestamp()
    except Exception:
        return None


def parsear_json_base(ruta) -> list:
    """Parsea JSONs de historial EP → lista de ops compatible con el backtester.

    Formatos soportados:
    - pnl_decision_history.json : lista raíz, filtra modo=='BASE',
      deriva DIRECTO/INVERSO de color_apostado vs mayor.
    - pnl_live_history.json     : dict raíz con clave 'ops',
      excluye modo=='SKIP', usa modo directamente.
    """
    import json as _json
    ops = []
    ruta = Path(ruta)
    if not ruta.exists():
        print(f"❌ Archivo no encontrado: {ruta}")
        return ops
    with open(ruta, 'r', encoding='utf-8') as f:
        data = _json.load(f)

    # ── Formato pnl_live_history: dict — leer raw, derivar modo desde WR rolling ──
    # Coherente con dashboard._autocargar_live_history: rolling de 20 ops por acierto.
    # Mantenemos SKIP (descartar perdería contexto en stats por (rango, modo)).
    if isinstance(data, dict):
        from collections import deque as _dq
        historial = _dq(maxlen=20)
        for r in data.get('raw', []):
            rango = r.get('rango', '')
            acierto = r.get('acierto')
            if not rango or acierto is None:
                continue
            historial.append(1 if acierto else 0)
            wr = sum(historial) / len(historial) * 100 if historial else 50.0
            modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
            ganada = bool(acierto)
            # En pnl_live_history, `acierto` ya es (winner == mayor) → gano_mayoria
            ops.append({'rango': rango, 'modo': modo,
                        'ganada': ganada, 'acierto': ganada,
                        'gano_mayoria': ganada,
                        'timestamp': r.get('timestamp')})
        return ops

    # ── Formato pnl_decision_history: lista raíz, todas las decisiones con winner ───
    for r in data:
        if r.get('winner') is None:
            continue
        rango = r.get('rango', '')
        if not rango:
            continue
        modo_raw = (r.get('modo') or '').upper()
        if modo_raw == 'BASE':
            # Derivar dirección desde color_apostado vs mayor (lógica original)
            mayor    = r.get('mayor', '')
            apostado = r.get('color_apostado', '')
            modo = 'DIRECTO' if apostado.upper() == mayor.upper() else 'INVERSO'
        elif modo_raw in ('DIRECTO', 'INVERSO', 'SKIP'):
            modo = modo_raw
        else:
            # Sin modo válido: derivar del WR almacenado
            wr = float(r.get('wr') or 50)
            modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
        ganada = bool(r.get('acierto', False))
        # En decision_history, `acierto` es la apuesta (color_apostado vs winner),
        # NO la mayoría. Calcular gano_mayoria explícito desde mayor + winner.
        winner_d = (r.get('winner') or '').lower()
        mayor_d  = (r.get('mayor')  or '').lower()
        if winner_d and mayor_d:
            gano_mayoria = (winner_d == mayor_d)
        elif modo == 'DIRECTO':
            gano_mayoria = ganada              # bet=mayoría → acierto = gano_mayoria
        elif modo == 'INVERSO':
            gano_mayoria = (not ganada)        # bet=minoría → acierto = not gano_mayoria
        else:
            gano_mayoria = ganada              # fallback razonable
        ts = None
        issue = str(r.get('issue', ''))
        hora  = r.get('hora', '')
        if len(issue) >= 8 and hora:
            try:
                ts = f"20{issue[0:2]}-{issue[2:4]}-{issue[4:6]} {hora}"
            except Exception:
                pass
        ops.append({'rango': rango, 'modo': modo,
                    'ganada': ganada, 'acierto': ganada,
                    'gano_mayoria': gano_mayoria, 'timestamp': ts})
    return ops


def parsear_archivo(ruta: str | Path) -> list:
    """Parsea reconstructor_data_AI.txt → lista de ops.
    Si la ruta termina en .json, delega a parsear_json_base()."""
    ops = []
    ruta = Path(ruta)
    if ruta.suffix.lower() == '.json':
        return parsear_json_base(ruta)
    if not ruta.exists():
        print(f"❌ Archivo no encontrado: {ruta}")
        return ops
    from collections import deque as _deque
    _hist_rolling = _deque(maxlen=20)
    with open(ruta, 'r', encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            m_ts = _TS_RE.search(linea)
            ts = m_ts.group(1) if m_ts else None
            if 'RANGO:' in linea and 'ACIERTO:' in linea:
                m_rango = _re.search(r'RANGO:\s*([0-9+\-]+)', linea)
                m_ac    = _re.search(r'ACIERTO:\s*(True|False)', linea)
                if m_rango and m_ac:
                    acierto = m_ac.group(1) == 'True'
                    _hist_rolling.append(1 if acierto else 0)
                    _wr = sum(_hist_rolling) / len(_hist_rolling) * 100 if _hist_rolling else 50.0
                    modo = 'DIRECTO' if _wr >= 60 else ('INVERSO' if _wr <= 40 else 'SKIP')
                    if modo == 'SKIP':
                        continue
                    ops.append({
                        'rango': m_rango.group(1),
                        'modo':  modo,
                        'ganada': acierto,
                        'acierto': acierto,
                        'gano_mayoria': acierto,   # ACIERTO en reconstructor = (winner==mayor)
                        'timestamp': ts,
                    })
                continue
            if 'RESULTADO:' in linea:
                m_rango = _re.search(r'Rango:\s*([0-9+\-]+)', linea)
                m_dif   = _re.search(r'[Dd]if[:\s]+([\d.]+)', linea)
                if m_rango: rango = m_rango.group(1)
                elif m_dif: rango = _dif_a_rango(float(m_dif.group(1)))
                else: continue
                if 'Modo: DIRECTO' in linea: modo = 'DIRECTO'
                elif 'Modo: INVERSO' in linea: modo = 'INVERSO'
                else: continue
                ganada = 'MayorGana: True' in linea
                # MayorGana = gano_mayoria por construcción
                ops.append({'rango': rango, 'modo': modo, 'ganada': ganada,
                            'acierto': ganada, 'gano_mayoria': ganada,
                            'timestamp': ts})
    return ops


def _dif_a_rango(dif: float) -> str:
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


RANGOS_ORDEN = ["0-5","5-10","10-15","15-20","20-25","25-30","30-35","35-40","40-45","45-50","+50"]


# ── Ventanas ────────────────────────────────────────────────────────────

def ventanas_aleatorias(ops: list, n: int = 200, tamano: int = 200,
                        seed: int = 42) -> list:
    """Genera N ventanas aleatorias de tamaño `tamano`."""
    if len(ops) < tamano:
        print(f"⚠  Datos insuficientes ({len(ops)}), usando única ventana completa")
        return [ops]
    rng = np.random.default_rng(seed)
    max_start = len(ops) - tamano
    indices = rng.integers(0, max_start + 1, size=n)
    return [ops[i:i + tamano] for i in indices]


def ventanas_deslizantes(ops: list, tamano: int = 200, paso: int = 50) -> list:
    """Genera ventanas deslizantes (secuenciales)."""
    if len(ops) < tamano:
        return [ops]
    ventanas = []
    for i in range(0, len(ops) - tamano + 1, paso):
        ventanas.append(ops[i:i + tamano])
    return ventanas


# ── Ejecución ───────────────────────────────────────────────────────────

def ejecutar_simulaciones(ventanas: list, simulador: str = 'simular_ep_por_rango',
                          ventana_ep: int = EP_VENTANA, max_mult: int = 0,
                          apuesta_base: float = 1.0, saldo_inicial: float = 0.0,
                          on_progress=None, **kwargs) -> list:
    """Ejecuta la simulación EP en cada ventana.

    on_progress: callable opcional invocado con (i_completadas, total) tras
    cada ventana procesada; útil para barras de progreso desde la UI.
    """
    if simulador not in SIMULADORES:
        print(f"❌ Simulador desconocido: {simulador}")
        print(f"   Opciones: {', '.join(SIMULADORES.keys())}")
        return []

    fn = SIMULADORES[simulador]
    resultados = []
    n_total_v = len(ventanas)

    for i, ventana in enumerate(ventanas):
        try:
            if simulador == 'simular_ep_rolling':
                res = fn(ventana, ventana=ventana_ep, apuesta_base=apuesta_base,
                         saldo_inicial=saldo_inicial)
            elif simulador == 'simular_umbral':
                res = fn(ventana, max_mult=max_mult, apuesta_base=apuesta_base,
                         saldo_inicial=saldo_inicial)
            elif simulador == 'simular_umbral_global':
                res = fn(ventana, max_mult=max_mult, apuesta_base=apuesta_base,
                         saldo_inicial=saldo_inicial,
                         ops_hist=kwargs.get('ops_hist'))
            elif simulador == 'simular_umbral_adaptativo':
                res = fn(ventana, max_mult=max_mult, apuesta_base=apuesta_base,
                         saldo_inicial=saldo_inicial,
                         ops_hist=kwargs.get('ops_hist'))
            else:
                res = fn(ventana, ventana_ep, max_mult, apuesta_base, saldo_inicial)
        except Exception as e:
            print(f"⚠  Error en ventana {i}: {e}")
            resultados.append({
                'saldo_ep': 0, 'saldo_real': 0,
                'n_bets': 0, 'n_skips': 0, 'n_total': len(ventana),
            })
            if on_progress:
                try: on_progress(i + 1, n_total_v)
                except Exception: pass
            continue

        resultados.append(res)
        if on_progress:
            try: on_progress(i + 1, n_total_v)
            except Exception: pass

    return resultados


def _normalizar_saldo_real(resultados: list, simulador: str) -> list:
    """
    Asegura que todos los resultados tengan saldo_real correcto.
    simular_ep_rolling devuelve saldo_real=0 (no calcula real).
    Para el resto, ya viene calculado.
    """
    for r in resultados:
        if r.get('saldo_real') is None or (simulador == 'simular_ep_rolling' and r.get('saldo_real') == 0):
            # Recalcular saldo_real desde los detalles si disponibles
            if r.get('detalles'):
                real = sum(0.9 if d['ganada'] else -1.0 for d in r['detalles'])
                r['saldo_real'] = real
            # Si no hay detalles, dejarlo como está
    return resultados


# ── Estadísticas ────────────────────────────────────────────────────────

def calcular_estadisticas(resultados: list) -> dict:
    """Calcula estadísticas sobre los resultados de las ventanas."""
    if not resultados:
        return {}

    ep_pnls = np.array([r['saldo_ep'] for r in resultados])
    real_pnls = np.array([r.get('saldo_real', 0) for r in resultados])
    bets = np.array([r['n_bets'] for r in resultados])
    skips = np.array([r['n_skips'] for r in resultados])
    totals = np.array([r.get('n_total', len(r.get('bal_ep', [])) - 1) for r in resultados])

    # Pct bets
    pct_bets = np.divide(bets, totals, out=np.zeros_like(bets, dtype=float), where=totals > 0) * 100

    # EP win rate: cuántas ventanas tienen EP > 0
    ep_win = np.sum(ep_pnls > 0)
    ep_lose = np.sum(ep_pnls <= 0)

    # EP > REAL
    ep_supera = np.sum(ep_pnls > real_pnls)

    # Distribución multiplicadores
    mult_counter = Counter()
    primeras_pos = []
    for r in resultados:
        if r.get('detalles'):
            for d in r['detalles']:
                if d.get('apostar'):
                    mult_counter[d.get('mult', 1)] += 1
        if r.get('primera_pos') is not None:
            primeras_pos.append(r['primera_pos'])

    # ── Saldo / Bankroll ──────────────────────────────────
    saldo_inicial = 0.0
    saldo_finales = [r.get('saldo_final') for r in resultados if r.get('saldo_final') is not None]
    saldo_minimos = [r.get('saldo_min') for r in resultados if r.get('saldo_min') is not None]
    bancarrotas = sum(1 for r in resultados if r.get('bancarrota'))
    if saldo_finales:
        saldo_inicial = float(resultados[0].get('saldo_inicial', 0))

    # ── Tiempos ───────────────────────────────────────────
    tiempo_stats = {}
    duraciones = [r.get('duracion_seg') for r in resultados if r.get('duracion_seg') is not None]
    intervalos = [r.get('intervalo_apuestas_seg') for r in resultados
                  if r.get('intervalo_apuestas_seg') is not None]
    if duraciones:
        dur_arr = np.array(duraciones)
        total_bets_t = int(np.sum(bets))
        tiempo_stats = {
            'duracion_media_seg': float(np.mean(dur_arr)),
            'duracion_total_seg': float(np.sum(dur_arr)),
            'duracion_min_seg':   float(np.min(dur_arr)),
            'duracion_max_seg':   float(np.max(dur_arr)),
            'tiempo_por_apuesta_seg': float(np.sum(dur_arr) / total_bets_t) if total_bets_t > 0 else None,
        }
        if intervalos:
            tiempo_stats['intervalo_apuestas_seg'] = float(np.mean(intervalos))

    saldo_stats = {}
    if saldo_inicial > 0 and saldo_finales:
        finales = np.array(saldo_finales)
        minimos = np.array(saldo_minimos)
        saldo_stats = {
            'saldo_inicial':    saldo_inicial,
            'saldo_final_mean': float(np.mean(finales)),
            'saldo_final_min':  float(np.min(finales)),
            'saldo_final_max':  float(np.max(finales)),
            'saldo_min_mean':   float(np.mean(minimos)),
            'saldo_min_abs':    float(np.min(minimos)),
            'roi_mean':         float(np.mean((finales - saldo_inicial) / saldo_inicial * 100)),
            'bancarrotas':      bancarrotas,
            'bancarrota_pct':   bancarrotas / len(resultados) * 100,
        }

    return {
        'n_windows': len(resultados),
        'ep_mean': float(np.mean(ep_pnls)),
        'ep_median': float(np.median(ep_pnls)),
        'ep_std': float(np.std(ep_pnls)),
        'ep_min': float(np.min(ep_pnls)),
        'ep_max': float(np.max(ep_pnls)),
        'ep_q25': float(np.percentile(ep_pnls, 25)),
        'ep_q75': float(np.percentile(ep_pnls, 75)),
        'ep_win_rate': float(ep_win / len(resultados) * 100),
        'ep_wins': int(ep_win),
        'ep_loses': int(ep_lose),
        'ep_supera_real': float(ep_supera / len(resultados) * 100),
        'real_mean': float(np.mean(real_pnls)),
        'real_median': float(np.median(real_pnls)),
        'diff_mean': float(np.mean(ep_pnls - real_pnls)),
        'bets_mean': float(np.mean(bets)),
        'bets_std': float(np.std(bets)),
        'bets_min': int(np.min(bets)),
        'bets_max': int(np.max(bets)),
        'bets_pct_mean': float(np.mean(pct_bets)),
        'skips_mean': float(np.mean(skips)),
        'primera_pos_media': float(np.mean(primeras_pos)) if primeras_pos else 0,
        'primera_pos_std': float(np.std(primeras_pos)) if primeras_pos else 0,
        'primera_pos_min': int(np.min(primeras_pos)) if primeras_pos else 0,
        'primera_pos_max': int(np.max(primeras_pos)) if primeras_pos else 0,
        'mult_distribucion': dict(mult_counter.most_common()),
        **tiempo_stats,
        **saldo_stats,
    }


# ── Reporte ─────────────────────────────────────────────────────────────

def imprimir_reporte(stats: dict, config: dict):
    """Dashboard cyberpunk del backtest EP."""
    _habilitar_colores_windows()
    s = stats
    c = config
    W = _W

    TITLE = '◈  B A C K T E S T   E P  ─  ESTRATEGIA PERFECTA SIMULADA'
    tpad = ' ' * (W - 2 - len(TITLE))
    print()
    print(f'{_A["dm"]}╔{"═"*W}╗{_A["R"]}')
    print(f'{_A["dm"]}║{_A["R"]}  {_A["B"]}{_A["cy"]}{TITLE}{_A["R"]}{tpad}{_A["dm"]}║{_A["R"]}')
    print(f'{_A["dm"]}╚{"═"*W}╝{_A["R"]}')

    # ── Configuración ────────────────────────────────────
    print(_sec('CONFIGURACIÓN'))
    print()
    sim_desc = SIMULADORES_DESC.get(c['simulador'], c['simulador'])
    max_mult_txt = f"{c.get('max_mult', 0)}x" if c.get('max_mult', 0) > 0 else '—'
    tipo_v = 'deslizantes' if c.get('sliding') else 'aleatorias'
    seed_txt = f"  ·  seed={c['seed']}" if not c.get('sliding') and c.get('seed') is not None else ''

    def _r(lbl, val, w=18):
        print(f'    {_A["mu"]}{lbl:<{w}}{_A["R"]}  {_A["tx"]}{val}{_A["R"]}')

    archivo = c.get('archivo', 'reconstructor_data_AI.txt')
    print(f'    {_A["mu"]}{"archivo":<18}{_A["R"]}  {_A["tx"]}{archivo}{_A["R"]}'
          f'   {_A["mu"]}ops totales{_A["R"]}  {_A["am"]}{_A["B"]}{c["total_ops"]}{_A["R"]}')
    _r('simulador', sim_desc)
    _r('ventanas', f'{c.get("n_windows","?")} {tipo_v}  ·  window={c["window_size"]}{seed_txt}')
    saldo_txt = f"  ·  saldo={c['saldo']:.2f}" if c.get('saldo', 0) > 0 else ''
    _r('parámetros', f'EP={c.get("ventana_ep", EP_VENTANA)}  ·  apuesta={c.get("apuesta",1.0):.2f}  ·  max_mult={max_mult_txt}{saldo_txt}')

    # ── Rendimiento EP ───────────────────────────────────
    print(_sec('RENDIMIENTO EP SIMULADA'))
    print()

    pnl_m = s['ep_mean']
    pcol  = 'gn' if pnl_m > 0 else 'rd'
    arrow = f'{_A[pcol]}{"▲" if pnl_m > 0 else "▼"}{_A["R"]}'

    print(f'    {_A["mu"]}{"PNL medio":<14}{_A["R"]}  '
          f'{_A[pcol]}{_A["B"]}{pnl_m:+.2f}{_A["R"]}  {arrow}'
          f'     {_A["mu"]}win rate{_A["R"]}  '
          f'{_A["am"]}{_A["B"]}{s["ep_win_rate"]:.1f}%{_A["R"]}'
          f'  {_A["dm"]}({s["ep_wins"]}/{s["n_windows"]}){_A["R"]}')

    mc = 'gn' if s['ep_median'] > 0 else 'rd'
    print(f'    {_A["mu"]}{"PNL mediana":<14}{_A["R"]}  {_A[mc]}{s["ep_median"]:+.2f}{_A["R"]}'
          f'        {_A["mu"]}std{_A["R"]}  {_A["tx"]}{s["ep_std"]:.2f}{_A["R"]}')

    mn_c = 'rd' if s['ep_min'] < 0 else 'gn'
    mx_c = 'gn' if s['ep_max'] > 0 else 'rd'
    print(f'    {_A["mu"]}{"min ─── max":<14}{_A["R"]}  '
          f'{_A[mn_c]}{s["ep_min"]:+.2f}{_A["R"]}  {_A["dm"]}───{_A["R"]}  {_A[mx_c]}{s["ep_max"]:+.2f}{_A["R"]}')

    q25c = 'gn' if s['ep_q25'] > 0 else 'rd'
    q75c = 'gn' if s['ep_q75'] > 0 else 'rd'
    print(f'    {_A["mu"]}{"Q25 ─── Q75":<14}{_A["R"]}  '
          f'{_A[q25c]}{s["ep_q25"]:+.2f}{_A["R"]}  {_A["dm"]}───{_A["R"]}  {_A[q75c]}{s["ep_q75"]:+.2f}{_A["R"]}')

    print()
    print(f'    {_barra(s["ep_win_rate"], 36)}'
          f'  {_A["am"]}{_A["B"]}{s["ep_win_rate"]:.1f}%{_A["R"]}'
          f'  {_A["mu"]}ventanas con EP > 0{_A["R"]}')

    # ── EP vs REAL ───────────────────────────────────────
    print(_sec('EP vs REAL'))
    print()

    diff  = s['diff_mean']
    dc    = 'gn' if diff > 0 else 'rd'
    darr  = f'{_A[dc]}{"▲" if diff > 0 else "▼"}{_A["R"]}'
    rc    = 'gn' if s['real_mean'] > 0 else 'rd'

    print(f'    {_A["mu"]}{"REAL PNL medio":<20}{_A["R"]}  {_A[rc]}{s["real_mean"]:+.2f}{_A["R"]}')
    print(f'    {_A["mu"]}{"diferencia EP ─ REAL":<20}{_A["R"]}  '
          f'{_A[dc]}{_A["B"]}{diff:+.2f}{_A["R"]}  {darr}')
    print()

    sup_pct = s['ep_supera_real']
    n_sup   = round(sup_pct * s['n_windows'] / 100)
    print(f'    {_barra(sup_pct, 36, "cy")}'
          f'  {_A["am"]}{_A["B"]}{sup_pct:.1f}%{_A["R"]}'
          f'  {_A["mu"]}EP supera a REAL  ({n_sup}/{s["n_windows"]}){_A["R"]}')

    # ── Actividad ────────────────────────────────────────
    print(_sec('ACTIVIDAD'))
    print()

    bets_pct = s.get('bets_pct_mean', 0)
    print(f'    {_A["mu"]}{"apuestas / ventana":<20}{_A["R"]}  '
          f'{_A["tx"]}{s["bets_mean"]:.1f} ± {s["bets_std"]:.1f}{_A["R"]}'
          f'  {_A["dm"]}({bets_pct:.1f}% activas){_A["R"]}'
          f'   {_A["mu"]}min{_A["R"]} {_A["am"]}{s["bets_min"]}{_A["R"]}'
          f'  {_A["dm"]}·{_A["R"]}  '
          f'{_A["mu"]}max{_A["R"]} {_A["am"]}{s["bets_max"]}{_A["R"]}')

    if s.get('primera_pos_media', 0) > 0:
        print(f'    {_A["mu"]}{"primera apuesta":<20}{_A["R"]}  '
              f'{_A["tx"]}pos {s["primera_pos_media"]:.1f} ± {s["primera_pos_std"]:.1f}{_A["R"]}'
              f'   {_A["mu"]}min{_A["R"]} {_A["am"]}{s["primera_pos_min"]}{_A["R"]}'
              f'  {_A["dm"]}·{_A["R"]}  '
              f'{_A["mu"]}max{_A["R"]} {_A["am"]}{s["primera_pos_max"]}{_A["R"]}')

    # ── Tiempo ───────────────────────────────────────────
    if s.get('duracion_media_seg') is not None:
        print(_sec('TIEMPO'))
        print()
        print(f'    {_A["mu"]}{"duración media ventana":<24}{_A["R"]}  '
              f'{_A["am"]}{_A["B"]}{_fmt_dur(s["duracion_media_seg"])}{_A["R"]}')
        print(f'    {_A["mu"]}{"duración total":<24}{_A["R"]}  '
              f'{_A["tx"]}{_fmt_dur(s["duracion_total_seg"])}{_A["R"]}')
        print(f'    {_A["mu"]}{"min ─── max":<24}{_A["R"]}  '
              f'{_A["tx"]}{_fmt_dur(s["duracion_min_seg"])}{_A["R"]}'
              f'  {_A["dm"]}───{_A["R"]}  '
              f'{_A["tx"]}{_fmt_dur(s["duracion_max_seg"])}{_A["R"]}')
        if s.get('tiempo_por_apuesta_seg') is not None:
            print(f'    {_A["mu"]}{"tiempo medio / apuesta":<24}{_A["R"]}  '
                  f'{_A["am"]}{_fmt_dur(s["tiempo_por_apuesta_seg"])}{_A["R"]}'
                  f'  {_A["dm"]}(duración total / total apuestas){_A["R"]}')
        if s.get('intervalo_apuestas_seg') is not None:
            print(f'    {_A["mu"]}{"intervalo entre apuestas":<24}{_A["R"]}  '
                  f'{_A["tx"]}{_fmt_dur(s["intervalo_apuestas_seg"])}{_A["R"]}'
                  f'  {_A["dm"]}(tiempo medio de espera){_A["R"]}')

    # ── Bankroll / Saldo ─────────────────────────────────
    if s.get('saldo_inicial', 0) > 0:
        print(_sec('BANKROLL'))
        print()

        si       = s['saldo_inicial']
        sf_mean  = s['saldo_final_mean']
        roi      = s['roi_mean']
        roi_col  = 'gn' if roi > 0 else 'rd'
        roi_arr  = '▲' if roi > 0 else '▼'
        sf_col   = 'gn' if sf_mean > si else 'rd'
        sm_min   = s['saldo_min_abs']
        sm_col   = 'rd' if sm_min < 0 else ('am' if sm_min < si * 0.5 else 'gn')

        print(f'    {_A["mu"]}{"saldo inicial":<18}{_A["R"]}  {_A["am"]}{_A["B"]}{si:.2f}{_A["R"]}')
        print(f'    {_A["mu"]}{"saldo final medio":<18}{_A["R"]}  '
              f'{_A[sf_col]}{_A["B"]}{sf_mean:.2f}{_A["R"]}'
              f'  {_A["dm"]}({_A["R"]}{_A[roi_col]}{roi:+.1f}% ROI{_A["R"]} {_A[roi_col]}{roi_arr}{_A["R"]}{_A["dm"]}){_A["R"]}')
        print(f'    {_A["mu"]}{"final min ─ max":<18}{_A["R"]}  '
              f'{_A["rd"]}{s["saldo_final_min"]:.2f}{_A["R"]}'
              f'  {_A["dm"]}───{_A["R"]}  '
              f'{_A["gn"]}{s["saldo_final_max"]:.2f}{_A["R"]}')
        print(f'    {_A["mu"]}{"drawdown medio":<18}{_A["R"]}  '
              f'{_A["tx"]}{s["saldo_min_mean"]:.2f}{_A["R"]}'
              f'    {_A["mu"]}peor caída{_A["R"]}  {_A[sm_col]}{_A["B"]}{sm_min:.2f}{_A["R"]}')

        if s.get('bancarrotas', 0) > 0:
            bp = s['bancarrota_pct']
            print()
            print(f'    {_A["rd"]}{_A["B"]}⚠  bancarrotas{_A["R"]}'
                  f'  {_A["rd"]}{_A["B"]}{s["bancarrotas"]}{_A["R"]} {_A["mu"]}/{_A["R"]} {_A["tx"]}{s["n_windows"]}{_A["R"]}'
                  f'  {_A["dm"]}({bp:.1f}% de las ventanas terminan en saldo negativo){_A["R"]}')
        else:
            print()
            print(f'    {_A["gn"]}◆  sin bancarrotas{_A["R"]}'
                  f'  {_A["mu"]}saldo siempre positivo en las {s["n_windows"]} ventanas{_A["R"]}')

    # ── Multiplicadores ──────────────────────────────────
    if s.get('mult_distribucion'):
        print(_sec('MULTIPLICADORES'))
        print()
        total_m = sum(s['mult_distribucion'].values())
        for mult, count in sorted(s['mult_distribucion'].items()):
            pct = count / total_m * 100
            n_b = round(pct / 100 * 42)
            bar = _A['cy'] + '█' * n_b + _A['dm'] + '░' * (42 - n_b) + _A['R']
            print(f'    {_A["am"]}{_A["B"]}{mult}x{_A["R"]}'
                  f'  {_A["tx"]}{count:>5}{_A["R"]}'
                  f'  {_A["mu"]}{pct:5.1f}%{_A["R"]}  {bar}')

    # ── Footer ───────────────────────────────────────────
    print()
    print(f'  {_A["dm"]}{"─" * W}{_A["R"]}')
    print()


def _calcular_multi_sim(ops: list, n_windows: int, window_size: int, seed: int,
                         max_mult: int = 0, apuesta_base: float = 1.0,
                         saldo_inicial: float = 0.0, on_progress=None) -> tuple:
    """Ejecuta los 4 simuladores sobre las mismas ventanas y devuelve (rows, n_ventanas, phase_data).

    on_progress: callable opcional (done, total) sobre el avance global combinado.
    """
    ventanas = ventanas_aleatorias(ops, n=n_windows, tamano=window_size, seed=seed)
    rows = []
    phase_data = []
    n_v = len(ventanas)
    n_sim = len(SIMULADORES)
    total_steps = max(1, n_sim * n_v)
    step = 0
    for sim_idx, (sim_id, sim_fn) in enumerate(SIMULADORES.items()):
        desc = SIMULADORES_DESC.get(sim_id, sim_id)
        resultados = []
        for i, v in enumerate(ventanas):
            try:
                if sim_id == 'simular_ep_rolling':
                    res = sim_fn(v, apuesta_base=apuesta_base, saldo_inicial=saldo_inicial)
                elif sim_id in ('simular_umbral_global', 'simular_umbral_adaptativo'):
                    res = sim_fn(v, max_mult=max_mult, apuesta_base=apuesta_base,
                                 saldo_inicial=saldo_inicial)
                else:
                    res = sim_fn(v, max_mult=max_mult, apuesta_base=apuesta_base,
                                 saldo_inicial=saldo_inicial)
            except Exception:
                res = {'saldo_ep': 0, 'saldo_real': 0, 'n_bets': 0,
                       'n_skips': 0, 'n_total': len(v)}
            resultados.append(res)
            step += 1
            if on_progress:
                try: on_progress(step, total_steps)
                except Exception: pass
            if sim_id == 'simular_ep_por_rango':
                phase_data.append({
                    'idx': i + 1,
                    'n_total': res.get('n_total', len(v)),
                    'primera_pos': res.get('primera_pos', res.get('n_total', len(v))),
                    'n_bets': res.get('n_bets', 0),
                    'n_skips': res.get('n_skips', 0),
                    'saldo_ep': res.get('saldo_ep', 0.0),
                    'bal_ep': res.get('bal_ep', []),
                })

        ep_arr   = np.array([r['saldo_ep'] for r in resultados])
        real_arr = np.array([r.get('saldo_real', 0) for r in resultados])
        bets_arr = np.array([r['n_bets'] for r in resultados])
        rows.append({
            'id': sim_id, 'desc': desc,
            'ep_mean':   float(np.mean(ep_arr)),
            'ep_median': float(np.median(ep_arr)),
            'ep_std':    float(np.std(ep_arr)),
            'ep_min':    float(np.min(ep_arr)),
            'ep_max':    float(np.max(ep_arr)),
            'win_rate':  float(np.sum(ep_arr > 0) / len(ep_arr) * 100),
            'supera':    float(np.sum(ep_arr > real_arr) / len(ep_arr) * 100),
            'bets_mean': float(np.mean(bets_arr)),
            'real_mean': float(np.mean(real_arr)),
            'n_windows': int(len(ep_arr)),
        })
    return rows, len(ventanas), phase_data


def _imprimir_multi_sim(ops: list, n_windows: int, window_size: int, seed: int,
                         max_mult: int = 0, apuesta_base: float = 1.0,
                         saldo_inicial: float = 0.0):
    """Dashboard cyberpunk — comparativa de todos los simuladores EP (CLI)."""
    _habilitar_colores_windows()
    W = _W

    TITLE = '◈  M U L T I - S I M  ─  COMPARATIVA DE VARIANTES EP'
    tpad = ' ' * (W - 2 - len(TITLE))
    print()
    print(f'{_A["dm"]}╔{"═"*W}╗{_A["R"]}')
    print(f'{_A["dm"]}║{_A["R"]}  {_A["B"]}{_A["cy"]}{TITLE}{_A["R"]}{tpad}{_A["dm"]}║{_A["R"]}')
    print(f'{_A["dm"]}╚{"═"*W}╝{_A["R"]}')
    print()
    print(f'  {_A["mu"]}ventanas{_A["R"]}  {_A["am"]}{n_windows}{_A["R"]}  {_A["dm"]}·{_A["R"]}  '
          f'{_A["mu"]}window{_A["R"]}  {_A["am"]}{window_size}{_A["R"]}  {_A["dm"]}·{_A["R"]}  '
          f'{_A["mu"]}seed{_A["R"]}  {_A["am"]}{seed}{_A["R"]}', end='')
    if apuesta_base != 1.0:
        print(f'  {_A["dm"]}·{_A["R"]}  {_A["mu"]}apuesta{_A["R"]}  {_A["am"]}{apuesta_base:.2f}{_A["R"]}', end='')
    if saldo_inicial > 0:
        print(f'  {_A["dm"]}·{_A["R"]}  {_A["mu"]}saldo{_A["R"]}  {_A["am"]}{saldo_inicial:.2f}{_A["R"]}', end='')
    print()

    rows, n_v, _ = _calcular_multi_sim(ops, n_windows, window_size, seed,
                                       max_mult, apuesta_base, saldo_inicial)
    print(f'\n  {_A["cy"]}◆{_A["R"]}  {_A["mu"]}ventanas generadas{_A["R"]}  {_A["tx"]}{n_v}{_A["R"]}')

    print(_sec('RESULTADOS POR SIMULADOR'))
    print()
    print(f'  {_A["mu"]}{"SIMULADOR":<34}{"EP MEDIO":>9}  {"MEDIANA":>8}  {"STD":>6}  {"WR%":>5}  {"BETS":>5}{_A["R"]}')
    print(f'  {_A["dm"]}{"─"*68}{_A["R"]}')

    for r in sorted(rows, key=lambda x: x['ep_mean'], reverse=True):
        col  = 'gn' if r['ep_mean'] > 0 else 'rd'
        icon = f'{_A[col]}{"▲" if r["ep_mean"] > 0 else "▼"}{_A["R"]}'
        print(f'  {_A["tx"]}{r["desc"]:<34}{_A["R"]}'
              f'{_A[col]}{_A["B"]}{r["ep_mean"]:+8.2f}{_A["R"]}  '
              f'{_A["tx"]}{r["ep_median"]:+7.2f}{_A["R"]}  '
              f'{_A["mu"]}{r["ep_std"]:5.2f}{_A["R"]}  '
              f'{_A["am"]}{r["win_rate"]:4.1f}%{_A["R"]}  '
              f'{_A["mu"]}{r["bets_mean"]:4.0f}{_A["R"]}  {icon}')

    print()
    # Ranking visual con barras
    print(_sec('RANKING EP MEDIO'))
    print()
    sorted_rows = sorted(rows, key=lambda x: x['ep_mean'], reverse=True)
    best = max(abs(r['ep_mean']) for r in rows) or 1
    for rank, r in enumerate(sorted_rows, 1):
        col    = 'gn' if r['ep_mean'] > 0 else 'rd'
        pct    = abs(r['ep_mean']) / best * 100
        n_b    = round(pct / 100 * 28)
        bar    = _A[col] + '█' * n_b + _A['dm'] + '░' * (28 - n_b) + _A['R']
        medal  = ['◆', '◇', '·', '·'][rank - 1]
        print(f'  {_A["am"]}{medal}{_A["R"]}  {_A["tx"]}{r["desc"]:<34}{_A["R"]}'
              f'{bar}  {_A[col]}{_A["B"]}{r["ep_mean"]:+.2f}{_A["R"]}')

    print()
    print(f'  {_A["dm"]}{"─" * W}{_A["R"]}')
    print()


# ── Exportación ─────────────────────────────────────────────────────────

def exportar_csv(resultados: list, ruta: str | Path, simulador: str = ''):
    """Exporta resumen por ventana a CSV."""
    import csv
    ruta = Path(ruta)
    with open(ruta, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['window', 'simulador', 'saldo_ep', 'saldo_real', 'diff_ep_real',
                         'n_bets', 'n_skips', 'n_total', 'pct_bets', 'primera_pos'])
        for i, r in enumerate(resultados):
            total = r.get('n_total', 0)
            pct = r['n_bets'] / total * 100 if total > 0 else 0
            pp = r.get('primera_pos', '')
            writer.writerow([i, simulador,
                             round(r['saldo_ep'], 2), round(r.get('saldo_real', 0), 2),
                             round(r['saldo_ep'] - r.get('saldo_real', 0), 2),
                             r['n_bets'], r['n_skips'], total, round(pct, 1), pp])
    print(f"  ✅ CSV exportado: {ruta}")


def exportar_detalles_csv(resultados: list, ruta: str | Path):
    """Exporta detalle ronda por ronda a CSV."""
    import csv
    ruta = Path(ruta)
    with open(ruta, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['window', 'round_idx', 'rango', 'modo', 'ganada',
                         'apostar', 'wr_v', 'mult', 'pnl'])
        for i, r in enumerate(resultados):
            if not r.get('detalles'):
                continue
            for j, d in enumerate(r['detalles']):
                if d.get('apostar'):
                    writer.writerow([i, j, d['rango'], d['modo'], d['ganada'],
                                     d['apostar'], round(d.get('wr_v', 0), 1),
                                     d.get('mult', 1), round(d.get('pnl', 0), 2)])
    print(f"  ✅ Detalles CSV exportado: {ruta}")


def generar_graficos(resultados: list, stats: dict, ruta_png: str | Path):
    """Genera gráfico PNG con histogramas de resultados."""
    try:
        import matplotlib
        matplotlib.use('Agg', force=True)
        import matplotlib.pyplot as plt
        plt.ioff()
    except ImportError:
        print("  ⚠ matplotlib no disponible. Instálalo con: pip install matplotlib")
        return

    ep_pnls = [r['saldo_ep'] for r in resultados]
    real_pnls = [r.get('saldo_real', 0) for r in resultados]
    bets = [r['n_bets'] for r in resultados]

    fig, axs = plt.subplots(2, 2, figsize=(14, 10))
    fig.patch.set_facecolor('#0A1628')

    for ax in axs.flat:
        ax.set_facecolor('#0A1628')
        ax.tick_params(colors='#4A6080', labelsize=8)
        for spine in ax.spines.values():
            spine.set_edgecolor('#1A2A3A')
        ax.grid(True, color='#1A2A3A', linewidth=0.4)

    # 1. Histograma EP PNL
    ax = axs[0, 0]
    colors = ['#FF3366' if x <= 0 else '#00FF88' for x in ep_pnls]
    ax.hist(ep_pnls, bins=30, color='#00FF88', alpha=0.7, edgecolor='#0A1628', linewidth=0.5)
    ax.axvline(0, color='#FF3366', linewidth=1.5, linestyle='--')
    ax.axvline(stats['ep_mean'], color='#FFD700', linewidth=1.5, linestyle='-',
               label=f"Media: {stats['ep_mean']:+.1f}")
    ax.set_title('Distribución EP PNL por ventana', color='#C8D8E8', fontsize=10, fontfamily='monospace')
    ax.set_xlabel('EP PNL (€)', color='#4A6080', fontsize=8)
    ax.set_ylabel('Ventanas', color='#4A6080', fontsize=8)
    ax.legend(facecolor='#0A1628', edgecolor='#0D2137', labelcolor='#C8D8E8', fontsize=7)

    # 2. Histograma apuestas por ventana
    ax = axs[0, 1]
    ax.hist(bets, bins=20, color='#00D4FF', alpha=0.7, edgecolor='#0A1628', linewidth=0.5)
    ax.axvline(stats['bets_mean'], color='#FFD700', linewidth=1.5, linestyle='-',
               label=f"Media: {stats['bets_mean']:.0f}")
    ax.set_title('Apuestas por ventana', color='#C8D8E8', fontsize=10, fontfamily='monospace')
    ax.set_xlabel('Nº apuestas', color='#4A6080', fontsize=8)
    ax.set_ylabel('Ventanas', color='#4A6080', fontsize=8)
    ax.legend(facecolor='#0A1628', edgecolor='#0D2137', labelcolor='#C8D8E8', fontsize=7)

    # 3. Box plot EP vs REAL
    ax = axs[1, 0]
    bp = ax.boxplot([real_pnls, ep_pnls], tick_labels=['REAL', 'EP SIMULADA'],
                    patch_artist=True, widths=0.5)
    bp['boxes'][0].set_facecolor('#FF3366')
    bp['boxes'][0].set_alpha(0.6)
    bp['boxes'][1].set_facecolor('#00FF88')
    bp['boxes'][1].set_alpha(0.6)
    for whisker in bp['whiskers']: whisker.set_color('#4A6080')
    for cap in bp['caps']: cap.set_color('#4A6080')
    for median in bp['medians']: median.set_color('#FFD700')
    ax.set_title('Comparativa EP vs REAL', color='#C8D8E8', fontsize=10, fontfamily='monospace')
    ax.set_ylabel('PNL (€)', color='#4A6080', fontsize=8)
    ax.tick_params(colors='#C8D8E8', labelsize=8)

    # 4. Scatter: EP PNL vs apuestas
    ax = axs[1, 1]
    colors_scatter = ['#00FF88' if ep > 0 else '#FF3366' for ep in ep_pnls]
    ax.scatter(bets, ep_pnls, c=colors_scatter, alpha=0.6, s=20, edgecolors='none')
    ax.axhline(0, color='#FF3366', linewidth=1, linestyle='--')
    ax.set_title('EP PNL vs Nº Apuestas', color='#C8D8E8', fontsize=10, fontfamily='monospace')
    ax.set_xlabel('Apuestas', color='#4A6080', fontsize=8)
    ax.set_ylabel('EP PNL (€)', color='#4A6080', fontsize=8)

    plt.tight_layout(pad=2.0)
    fig.savefig(ruta_png, dpi=100, bbox_inches='tight', facecolor='#0A1628')
    plt.close(fig)
    print(f"  ✅ Gráfico exportado: {ruta_png}")


# ── Gráfico interactivo por ventana (proceso separado) ──────────────────

def generar_grafico_ventana(detalles: list, ventana_idx: int, apuesta_base: float = 1.0):
    """
    Genera y muestra a pantalla completa la curva de balance de una ventana
    en un proceso separado. Se cierra al pulsar cualquier tecla o cerrar la ventana.
    """
    import matplotlib
    matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 9), gridspec_kw={'height_ratios': [3, 1]})
    fig.patch.set_facecolor('#0A1628')
    manager = plt.get_current_fig_manager()
    try:
        manager.full_screen_toggle()
    except AttributeError:
        try:
            manager.window.state('zoomed')
        except AttributeError:
            pass

    # Curva de balance
    bal_ep = [0.0]
    bal_real = [0.0]
    bets_x = []
    bets_y_ep = []
    bets_y_real = []
    for i, d in enumerate(detalles):
        ganada = d['ganada']
        apostar = d.get('apostar', False)
        mult = d.get('mult', 1)
        pnl_ep = (0.9 if (ganada if d['modo'] == 'DIRECTO' else not ganada) else -1.0) * mult * apuesta_base
        pnl_real = 0.9 if ganada else -1.0
        bal_ep.append(bal_ep[-1] + (pnl_ep if apostar else 0))
        bal_real.append(bal_real[-1] + pnl_real)
        if apostar:
            bets_x.append(i)
            bets_y_ep.append(bal_ep[-1])
            bets_y_real.append(bal_real[-1])

    ax1.plot(bal_ep, color='#00FF88', linewidth=1.5, label='EP Simulada', alpha=0.9)
    ax1.plot(bal_real, color='#FF3366', linewidth=1.0, label='Real', alpha=0.6)
    if bets_x:
        ax1.scatter(bets_x, bets_y_ep, color='#00D4FF', s=15, zorder=5, label='Apuesta EP', alpha=0.7)
    ax1.axhline(0, color='#FFD700', linewidth=0.8, linestyle='--', alpha=0.5)
    ax1.set_title(f'Ventana #{ventana_idx+1} — Balance EP vs REAL', color='#C8D8E8',
                  fontsize=12, fontfamily='monospace')
    ax1.set_ylabel('PNL (€)', color='#C8D8E8', fontsize=10)
    ax1.legend(facecolor='#0A1628', edgecolor='#0D2137', labelcolor='#C8D8E8', fontsize=8)
    ax1.set_facecolor('#0A1628')
    ax1.tick_params(colors='#4A6080', labelsize=8)
    ax1.grid(True, color='#1A2A3A', linewidth=0.4)
    for spine in ax1.spines.values():
        spine.set_edgecolor('#1A2A3A')

    # Señal de apuesta
    apostar_bin = [1 if d.get('apostar', False) else 0 for d in detalles]
    n_total = len(detalles)
    ax2.fill_between(range(n_total), apostar_bin, color='#00D4FF', alpha=0.5, step='pre')
    ax2.set_ylim(-0.1, 1.5)
    ax2.set_ylabel('Apuesta', color='#C8D8E8', fontsize=10)
    ax2.set_xlabel('Ronda', color='#C8D8E8', fontsize=10)
    ax2.set_facecolor('#0A1628')
    ax2.tick_params(colors='#4A6080', labelsize=8)
    ax2.grid(True, color='#1A2A3A', linewidth=0.4)
    for spine in ax2.spines.values():
        spine.set_edgecolor('#1A2A3A')

    plt.tight_layout(pad=2.0)
    print(f"  📊 Ventana #{ventana_idx+1}: cerrando gráfico para continuar...")
    plt.show(block=True)
    plt.close(fig)


# ── Dashboard Tkinter Interactivo ──────────────────────────────────────

# Paleta cyberpunk (igual que acertador.py)
_TK_C = {
    'bg':     '#050A14',
    'panel':  '#0A1628',
    'border': '#0D2137',
    'cy':     '#00D4FF',
    'gn':     '#00FF88',
    'rd':     '#FF3366',
    'am':     '#FFB800',
    'tx':     '#C8D8E8',
    'mu':     '#4A6080',
    'dm':     '#284155',
    'wh':     '#E8F4FF',
}

_ANSI_CODE_TO_TAG = {
    '38;2;0;212;255':   'cy',
    '38;2;0;255;136':   'gn',
    '38;2;255;51;102':  'rd',
    '38;2;255;184;0':   'am',
    '38;2;200;216;232': 'tx',
    '38;2;74;96;128':   'mu',
    '38;2;40;65;90':    'dm',
    '38;2;232;244;255': 'wh',
}


def _ansi_a_segmentos(text: str):
    """Convierte texto con códigos ANSI en lista de (texto, tags_tuple)."""
    import re as _r
    pattern = _r.compile(r'\x1b\[([0-9;]+)m')
    segs = []
    pos = 0
    color = None
    bold = False
    for m in pattern.finditer(text):
        if m.start() > pos:
            tags = []
            if color: tags.append(color)
            if bold: tags.append('bold')
            segs.append((text[pos:m.start()], tuple(tags)))
        code = m.group(1)
        if code == '0':
            color = None
            bold = False
        elif code == '1':
            bold = True
        elif code in _ANSI_CODE_TO_TAG:
            color = _ANSI_CODE_TO_TAG[code]
        pos = m.end()
    if pos < len(text):
        tags = []
        if color: tags.append(color)
        if bold: tags.append('bold')
        segs.append((text[pos:], tuple(tags)))
    return segs


_CONFIG_PATH = Path(__file__).parent / 'backtest_ep_config.json'

def _cargar_config_dashboard() -> dict:
    """Carga la última configuración del dashboard si existe."""
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _guardar_config_dashboard(cfg: dict):
    """Persiste la configuración del dashboard en disco."""
    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _crear_mini_card_ventana(parent, pd):
    """Mini-card de una ventana: barra carga/apuestas + sparkline EP."""
    import tkinter as tk
    C = _TK_C
    CARD_BG  = '#0D2137'   # border — algo más claro que panel
    TEXT_BG  = '#0A1628'   # fondo sparkline
    CARGA_CL = '#2A4A7A'   # azul medio para fase carga (visible pero apagado)

    n_total = pd['n_total'] or 1
    primera = min(pd.get('primera_pos') or n_total, n_total)
    pct_carga = primera / n_total

    card = tk.Frame(parent, bg=CARD_BG, pady=3, padx=4)
    card.pack(fill='x', pady=2, padx=2)

    # Header: V01  +12.3
    hdr = tk.Frame(card, bg=CARD_BG)
    hdr.pack(fill='x')
    tk.Label(hdr, text=f"V{pd['idx']:02d}", bg=CARD_BG, fg=C['tx'],
             font=('Consolas', 9, 'bold')).pack(side='left')
    col_ep = C['gn'] if pd['saldo_ep'] > 0 else C['rd']
    tk.Label(hdr, text=f"{pd['saldo_ep']:+.1f}", bg=CARD_BG, fg=col_ep,
             font=('Consolas', 9, 'bold')).pack(side='right')

    # Barra de fases: azul medio=carga, cyan brillante=apuestas
    BAR_W = 238
    bar = tk.Frame(card, bg='#1A2A3A', height=12, width=BAR_W)
    bar.pack(fill='x', pady=(2, 0))
    bar.pack_propagate(False)
    w_c = max(1, int(BAR_W * pct_carga))
    w_a = max(1, BAR_W - w_c)
    tk.Frame(bar, bg=CARGA_CL, width=w_c, height=12).place(x=0, y=0)
    tk.Frame(bar, bg=C['cy'], width=w_a, height=12).place(x=w_c, y=0)

    # Leyenda: C:XX A:YY B:ZZ
    lbl = f"C:{primera}  A:{n_total - primera}  B:{pd['n_bets']}"
    tk.Label(card, text=lbl, bg=CARD_BG, fg=C['mu'],
             font=('Consolas', 8)).pack(anchor='w', pady=(1, 0))

    # Sparkline EP
    bal = pd.get('bal_ep', [])
    if bal and len(bal) > 1:
        SPK_W, SPK_H = 238, 32
        spk = tk.Canvas(card, bg=TEXT_BG, width=SPK_W, height=SPK_H,
                        highlightthickness=0)
        spk.pack(pady=(2, 0))
        mn, mx = min(bal), max(bal)
        rng = (mx - mn) if mx != mn else 1.0
        y0 = SPK_H - max(2, int((0 - mn) / rng * (SPK_H - 4))) - 2
        y0 = max(2, min(SPK_H - 2, y0))
        spk.create_line(0, y0, SPK_W, y0, fill='#284155', dash=(2, 2))
        pts = []
        for k, v in enumerate(bal):
            x = int(k / (len(bal) - 1) * (SPK_W - 2)) + 1
            y = SPK_H - int((v - mn) / rng * (SPK_H - 4)) - 2
            y = max(1, min(SPK_H - 1, y))
            pts.extend([x, y])
        if len(pts) >= 4:
            col_line = C['gn'] if bal[-1] > 0 else C['rd']
            spk.create_line(*pts, fill=col_line, width=2, smooth=True)


def _crear_panel_fases(parent, phase_data):
    """Panel scrollable con una mini-card por ventana mostrando fases carga/apuestas."""
    import tkinter as tk
    C = _TK_C

    outer = tk.Frame(parent, bg=C['panel'])
    outer.pack(fill='both', expand=True)

    sc = tk.Canvas(outer, bg=C['panel'], highlightthickness=0)
    sb = tk.Scrollbar(outer, orient='vertical', command=sc.yview)
    inner = tk.Frame(sc, bg=C['panel'])
    sc.create_window((0, 0), window=inner, anchor='nw')
    sc.configure(yscrollcommand=sb.set)
    sb.pack(side='right', fill='y')
    sc.pack(side='left', fill='both', expand=True)

    for pd in sorted(phase_data, key=lambda x: x['saldo_ep'], reverse=True):
        _crear_mini_card_ventana(inner, pd)

    inner.update_idletasks()
    sc.configure(scrollregion=sc.bbox('all'))
    sc.bind('<MouseWheel>', lambda e: sc.yview_scroll(-1 * (e.delta // 120), 'units'))

    inner.update_idletasks()
    sc.configure(scrollregion=sc.bbox('all'))
    sc.bind('<MouseWheel>', lambda e: sc.yview_scroll(-1 * (e.delta // 120), 'units'))


def _construir_canvas_multi_sim(parent, rows, n_ventanas, params):
    """Construye una vista de tarjetas en columnas para la comparativa multi-sim."""
    import tkinter as tk
    C = _TK_C

    # Limpiar contenido previo del parent
    for w in parent.winfo_children():
        w.destroy()

    # ── Cabecera ──────────────────────────────────────
    h = tk.Frame(parent, bg=C['border'])
    h.pack(fill='x')
    tk.Label(h, text='  ◈ COMPARATIVA MULTI-SIM',
             font=('Consolas', 13, 'bold'),
             bg=C['border'], fg=C['cy'], pady=4).pack(side='left')

    # Sub-header con parámetros
    sh = tk.Frame(parent, bg=C['bg'])
    sh.pack(fill='x', pady=(4, 8))
    info = (f'{n_ventanas} ventanas  ·  window={params.get("window_size", "?")}  '
            f'·  seed={params.get("seed", "?")}')
    if params.get('apuesta', 1.0) != 1.0:
        info += f'  ·  apuesta={params["apuesta"]:.2f}'
    if params.get('saldo', 0) > 0:
        info += f'  ·  saldo={params["saldo"]:.2f}'
    if params.get('max_mult', 0) > 0:
        info += f'  ·  max_mult={params["max_mult"]}x'
    tk.Label(sh, text=info, font=('Consolas', 9),
             bg=C['bg'], fg=C['mu']).pack(padx=8)

    # ── Grid de cards ─────────────────────────────────
    grid = tk.Frame(parent, bg=C['bg'])
    grid.pack(fill='both', expand=True, padx=4, pady=(0, 4))

    sorted_rows = sorted(rows, key=lambda x: x['ep_mean'], reverse=True)
    medals = ['◆', '◇', '·', '·']

    for i, r in enumerate(sorted_rows):
        grid.columnconfigure(i, weight=1, uniform='col')
        _crear_card_simulador(grid, r, i, medals[i] if i < 4 else '·')
    grid.rowconfigure(0, weight=1)


def _crear_card_simulador(parent, row, col_idx, medal):
    """Crea una tarjeta vertical con todas las métricas de un simulador."""
    import tkinter as tk
    C = _TK_C
    is_winner = col_idx == 0 and row['ep_mean'] > 0
    border_color = C['gn'] if is_winner else C['border']

    card = tk.Frame(parent, bg=C['panel'],
                    highlightbackground=border_color,
                    highlightthickness=2 if is_winner else 1)
    card.grid(row=0, column=col_idx, sticky='nsew', padx=3, pady=3)

    # ── Header de rank ───────────────────────────
    hdr = tk.Frame(card, bg=C['border'])
    hdr.pack(fill='x')
    rank_color = {0: C['am'], 1: C['cy'], 2: C['mu'], 3: C['mu']}.get(col_idx, C['mu'])
    tk.Label(hdr, text=f' {medal}  RANK #{col_idx+1}',
             font=('Consolas', 10, 'bold'),
             bg=C['border'], fg=rank_color, pady=3).pack(side='left', padx=6)

    # ── Nombre del simulador ─────────────────────
    desc = row['desc']
    if ' (' in desc:
        nombre, detalle = desc.split(' (', 1)
        detalle = detalle.rstrip(')')
    else:
        nombre, detalle = desc, ''
    tk.Label(card, text=nombre, font=('Consolas', 10, 'bold'),
             bg=C['panel'], fg=C['cy']).pack(pady=(8, 2), padx=8)
    if detalle:
        tk.Label(card, text=detalle, font=('Consolas', 8),
                 bg=C['panel'], fg=C['mu'], wraplength=210,
                 justify='center').pack(pady=(0, 4), padx=8)

    # ── EP medio (gigante) ───────────────────────
    ep_color = C['gn'] if row['ep_mean'] > 0 else C['rd']
    arrow = '▲' if row['ep_mean'] > 0 else '▼'
    big = tk.Frame(card, bg=C['panel'])
    big.pack(pady=8, fill='x')
    tk.Label(big, text=f'{row["ep_mean"]:+.2f}',
             font=('Consolas', 24, 'bold'),
             bg=C['panel'], fg=ep_color).pack()
    tk.Label(big, text=f'EP MEDIO  {arrow}', font=('Consolas', 8, 'bold'),
             bg=C['panel'], fg=C['mu']).pack()

    # ── Barras (WR + Supera) ─────────────────────
    _stat_bar(card, 'WIN RATE', f'{row["win_rate"]:.1f}%', row['win_rate'], C['gn'])
    _stat_bar(card, 'SUPERA REAL', f'{row["supera"]:.1f}%', row['supera'], C['cy'])

    # ── Separador ────────────────────────────────
    sep = tk.Frame(card, bg=C['border'], height=1)
    sep.pack(fill='x', padx=8, pady=(8, 4))

    # ── Tabla de stats ───────────────────────────
    stats = [
        ('mediana',     f'{row["ep_median"]:+.2f}',
         C['gn'] if row['ep_median'] > 0 else C['rd']),
        ('std',         f'{row["ep_std"]:.2f}', C['tx']),
        ('min',         f'{row["ep_min"]:+.2f}', C['rd']),
        ('max',         f'{row["ep_max"]:+.2f}', C['gn']),
        ('apuestas/v',  f'{row["bets_mean"]:.0f}', C['am']),
        ('REAL medio',  f'{row["real_mean"]:+.2f}',
         C['gn'] if row['real_mean'] > 0 else C['rd']),
    ]
    for lbl, val, vcolor in stats:
        f = tk.Frame(card, bg=C['panel'])
        f.pack(fill='x', padx=10, pady=1)
        tk.Label(f, text=lbl, font=('Consolas', 9),
                 bg=C['panel'], fg=C['mu']).pack(side='left')
        tk.Label(f, text=val, font=('Consolas', 9, 'bold'),
                 bg=C['panel'], fg=vcolor).pack(side='right')


def _stat_bar(card, label, value_text, pct, color):
    """Barra horizontal con label y valor a los lados."""
    import tkinter as tk
    C = _TK_C
    f = tk.Frame(card, bg=C['panel'])
    f.pack(fill='x', padx=10, pady=(6, 2))

    top = tk.Frame(f, bg=C['panel'])
    top.pack(fill='x')
    tk.Label(top, text=label, font=('Consolas', 8, 'bold'),
             bg=C['panel'], fg=C['mu']).pack(side='left')
    tk.Label(top, text=value_text, font=('Consolas', 9, 'bold'),
             bg=C['panel'], fg=C['am']).pack(side='right')

    bar_outer = tk.Frame(f, bg=C['border'], height=6)
    bar_outer.pack(fill='x', pady=(2, 0))
    bar_outer.pack_propagate(False)
    bar_inner = tk.Frame(bar_outer, bg=color, height=6)
    bar_inner.place(x=0, y=0, relheight=1,
                    relwidth=max(0.0, min(1.0, pct / 100.0)))


def _actualizar_fases_panel(frame, phase_data):
    """Limpia y reconstruye el panel de fases con nuevos datos."""
    import tkinter as tk
    C = _TK_C
    for w in frame.winfo_children():
        w.destroy()
    if not phase_data:
        tk.Label(frame, text='  Sin datos', bg=C['panel'], fg=C['mu'],
                 font=('Consolas', 9)).pack(pady=20)
        return
    _crear_panel_fases(frame, phase_data)


def _abrir_fases_ep_bt(ops):
    """Abre ventana Toplevel con mini-cards por (rango, modo) — igual que pnl_dashboard."""
    import tkinter as tk
    if not ops:
        return
    C = _TK_C
    from pnl_config import EP_UMBRAL_MIN as EP_UMBRAL
    MIN_OPS   = 10
    from collections import defaultdict

    stats = defaultdict(lambda: defaultdict(lambda: {'ops': 0, 'ganadas': 0}))
    for op in ops:
        r, m = op.get('rango', ''), op.get('modo', '')
        if m not in ('DIRECTO', 'INVERSO'):
            continue
        stats[r][m]['ops'] += 1
        if op.get('acierto', False):
            stats[r][m]['ganadas'] += 1

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

    ORDEN = ["0-5","5-10","10-15","15-20","20-25","25-30","30-35","35-40","40-45","45-50","+50"]
    cards = []
    claves_vistas = set()
    for rango in ORDEN:
        for modo in ('DIRECTO', 'INVERSO'):
            if (rango, modo) in claves_vistas:
                continue
            claves_vistas.add((rango, modo))
            st = stats[rango][modo]
            n  = st['ops']
            if n == 0:
                continue
            wr     = st['ganadas'] / n * 100 if n >= MIN_OPS else 0.0
            activo = wr >= EP_UMBRAL
            _mm_val = mejor_modo.get(rango, (None, 0.0))
            mm, wr_m = _mm_val if _mm_val else (None, 0.0)
            bal, acum = [], 0.0
            for op in ops:
                if op.get('rango') != rango or op.get('modo') != modo:
                    continue
                if mm and wr_m >= EP_UMBRAL:
                    # Usar gano_mayoria limpio cuando esté disponible
                    if 'gano_mayoria' in op:
                        gm = bool(op['gano_mayoria'])
                    elif op.get('modo') == 'DIRECTO':
                        gm = bool(op.get('acierto', False))
                    elif op.get('modo') == 'INVERSO':
                        gm = (not bool(op.get('acierto', False)))
                    else:
                        gm = bool(op.get('acierto', False))
                    gano = gm if mm == 'DIRECTO' else (not gm)
                    acum += 0.9 if gano else -1.0
                bal.append(acum)
            cards.append({'rango': rango, 'modo': modo, 'n': n, 'wr': wr,
                          'activo': activo, 'bal': bal})

    cards.sort(key=lambda c: (not c['activo'], -c['wr']))

    top = tk.Toplevel()
    top.title('FASES EP — Por rango/modo')
    top.configure(bg=C['bg'])
    top.geometry('520x680')

    hdr = tk.Frame(top, bg='#020810')
    hdr.pack(fill='x')
    tk.Frame(hdr, bg=C['cy'], height=2).pack(fill='x')
    tk.Label(hdr, text='FASES EP  —  umbral 53.2%', font=('Consolas', 12, 'bold'),
             bg='#020810', fg=C['cy']).pack(side='left', padx=12, pady=6)
    tk.Label(hdr, text=f'total:{len(ops)}', font=('Consolas', 10),
             bg='#020810', fg=C['mu']).pack(side='right', padx=12)

    outer = tk.Frame(top, bg=C['bg'])
    outer.pack(fill='both', expand=True, padx=4, pady=4)
    sc = tk.Canvas(outer, bg=C['bg'], highlightthickness=0)
    sb = tk.Scrollbar(outer, orient='vertical', command=sc.yview)
    inner_f = tk.Frame(sc, bg=C['bg'])
    sc.create_window((0, 0), window=inner_f, anchor='nw')
    sc.configure(yscrollcommand=sb.set)
    sb.pack(side='right', fill='y')
    sc.pack(side='left', fill='both', expand=True)

    CARD_BG = '#0D2137'
    CY = C['cy']; GN = C['gn']; RD = C['rd']; MU = C['mu']

    for cd in cards:
        card = tk.Frame(inner_f, bg=CARD_BG, pady=3, padx=6)
        card.pack(fill='x', pady=2, padx=4)

        hf2 = tk.Frame(card, bg=CARD_BG)
        hf2.pack(fill='x')
        col_rng = CY if cd['activo'] else MU
        tk.Label(hf2, text=f"{cd['rango']:>4}", font=('Consolas', 10, 'bold'),
                 bg=CARD_BG, fg=col_rng).pack(side='left')
        col_mod = GN if cd['modo'] == 'DIRECTO' else RD
        tk.Label(hf2, text=f"  {cd['modo'][:7]}", font=('Consolas', 10),
                 bg=CARD_BG, fg=col_mod).pack(side='left')
        estado_col = GN if cd['activo'] else MU
        tk.Label(hf2, text='ACTIVO' if cd['activo'] else 'SKIP',
                 font=('Consolas', 9, 'bold'), bg=CARD_BG, fg=estado_col).pack(side='right')
        wr_col = GN if cd['wr'] >= 53.2 else (C['am'] if cd['wr'] >= 45 else RD)
        tk.Label(hf2, text=f"WR {cd['wr']:.1f}%", font=('Consolas', 10, 'bold'),
                 bg=CARD_BG, fg=wr_col).pack(side='right', padx=8)

        BAR_W = 490
        bar_f = tk.Frame(card, bg='#0A1020', height=10, width=BAR_W)
        bar_f.pack(fill='x', pady=(2, 1))
        bar_f.pack_propagate(False)
        fill_w = max(1, int(BAR_W * min(cd['wr'], 100) / 100))
        bar_col = GN if cd['activo'] else ('#2A4A7A' if cd['wr'] > 0 else '#1A2A3A')
        tk.Frame(bar_f, bg=bar_col, width=fill_w, height=10).place(x=0, y=0)
        x_umbral = int(BAR_W * 53.2 / 100)
        tk.Frame(bar_f, bg=C['am'], width=2, height=10).place(x=x_umbral, y=0)

        tk.Label(card, text=f"T:{cd['n']}", font=('Consolas', 8),
                 bg=CARD_BG, fg=MU).pack(anchor='w')

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
            spk.create_text(SPK_W - 4, 4, text=f"{bal[-1]:+.1f}", anchor='ne',
                            fill=GN if bal[-1] >= 0 else RD, font=('Consolas', 8))

    inner_f.update_idletasks()
    sc.configure(scrollregion=sc.bbox('all'))
    sc.bind('<MouseWheel>', lambda e: sc.yview_scroll(-1 * (e.delta // 120), 'units'))
    inner_f.bind('<MouseWheel>', lambda e: sc.yview_scroll(-1 * (e.delta // 120), 'units'))


def lanzar_dashboard(archivo_default: str = 'reconstructor_data_AI.txt'):
    """Lanza el dashboard Tkinter interactivo con todos los parámetros."""
    import tkinter as tk
    from tkinter import ttk, filedialog
    import threading
    import io
    import contextlib

    C = _TK_C
    _cfg = _cargar_config_dashboard()
    def _g(k, default):
        return _cfg.get(k, default)
    ops_ref = [None]   # captura ops parseadas para el botón FASES EP
    root = tk.Tk()
    root.title('◈  Backtest EP  ─  Dashboard')
    root.configure(bg=C['bg'])
    root.geometry('1800x920')

    F_TIT = ('Consolas', 13, 'bold')
    F_LBL = ('Consolas', 10)
    F_VAL = ('Consolas', 10, 'bold')
    F_MN  = ('Consolas', 12)

    style = ttk.Style()
    try: style.theme_use('clam')
    except Exception: pass
    style.configure('CB.TCombobox', fieldbackground=C['border'],
                    background=C['border'], foreground=C['tx'],
                    arrowcolor=C['cy'], bordercolor=C['border'])
    style.map('CB.TCombobox',
              fieldbackground=[('readonly', C['border']), ('disabled', C['bg'])],
              foreground=[('readonly', C['tx']), ('disabled', C['mu'])],
              selectforeground=[('readonly', C['tx'])],
              selectbackground=[('readonly', C['border'])])

    # ── Header ─────────────────────────────────────────
    hdr = tk.Frame(root, bg=C['border'], height=48)
    hdr.pack(fill='x')
    hdr.pack_propagate(False)
    tk.Label(hdr,
             text='◈  B A C K T E S T   E P  ─  DASHBOARD INTERACTIVO',
             font=('Consolas', 14, 'bold'),
             bg=C['border'], fg=C['cy']).pack(side='left', padx=20, pady=10)
    estado_lbl = tk.Label(hdr, text='● LISTO', font=F_LBL,
                          bg=C['border'], fg=C['gn'])
    estado_lbl.pack(side='right', padx=20)
    btn_fases_ep = tk.Button(hdr, text='FASES EP', font=('Consolas', 10, 'bold'),
                              bg=C['border'], fg=C['cy'], relief='flat', cursor='hand2',
                              padx=10, state='disabled',
                              command=lambda: _abrir_fases_ep_bt(ops_ref[0]))
    btn_fases_ep.pack(side='right', padx=8, pady=8)

    main = tk.Frame(root, bg=C['bg'])
    main.pack(fill='both', expand=True, padx=8, pady=(8, 4))

    # ── Panel parámetros (izquierda) ───────────────────
    izq = tk.Frame(main, bg=C['panel'],
                   highlightbackground=C['border'], highlightthickness=1, width=400)
    izq.pack(side='left', fill='y', padx=(0, 6))
    izq.pack_propagate(False)

    # Scrollable frame
    canvas = tk.Canvas(izq, bg=C['panel'], highlightthickness=0)
    sb_v = tk.Scrollbar(izq, orient='vertical', command=canvas.yview)
    inner = tk.Frame(canvas, bg=C['panel'])
    inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0, 0), window=inner, anchor='nw', width=380)
    canvas.configure(yscrollcommand=sb_v.set)
    canvas.pack(side='left', fill='both', expand=True)
    sb_v.pack(side='right', fill='y')

    def _seccion(t):
        f = tk.Frame(inner, bg=C['border'])
        f.pack(fill='x', pady=(8, 2), padx=2)
        tk.Label(f, text=f'  ◈ {t}', font=F_TIT,
                 bg=C['border'], fg=C['cy'], pady=4).pack(side='left')

    def _entry(label, default, w=14):
        f = tk.Frame(inner, bg=C['panel'])
        f.pack(fill='x', pady=2, padx=10)
        tk.Label(f, text=label, font=F_LBL, bg=C['panel'], fg=C['mu'],
                 width=w, anchor='w').pack(side='left')
        v = tk.StringVar(value=str(default))
        e = tk.Entry(f, textvariable=v, font=F_VAL,
                     bg=C['border'], fg=C['tx'],
                     insertbackground=C['cy'], relief='flat',
                     highlightthickness=1, highlightbackground=C['border'],
                     highlightcolor=C['cy'])
        e.pack(side='left', fill='x', expand=True)
        return v

    def _check(label, default=False, color=None):
        f = tk.Frame(inner, bg=C['panel'])
        f.pack(fill='x', pady=2, padx=10)
        v = tk.BooleanVar(value=default)
        cb = tk.Checkbutton(f, text=label, variable=v,
                            font=F_LBL, bg=C['panel'],
                            fg=color or C['tx'],
                            selectcolor=C['border'],
                            activebackground=C['panel'],
                            activeforeground=C['cy'],
                            anchor='w', cursor='hand2')
        cb.pack(side='left', fill='x', expand=True)
        return v

    def _file(label, default='', save=False):
        f = tk.Frame(inner, bg=C['panel'])
        f.pack(fill='x', pady=2, padx=10)
        tk.Label(f, text=label, font=F_LBL, bg=C['panel'], fg=C['mu'],
                 width=14, anchor='w').pack(side='left')
        v = tk.StringVar(value=str(default))
        e = tk.Entry(f, textvariable=v, font=F_VAL,
                     bg=C['border'], fg=C['tx'],
                     insertbackground=C['cy'], relief='flat')
        e.pack(side='left', fill='x', expand=True)
        def pick():
            fn = (filedialog.asksaveasfilename if save else filedialog.askopenfilename)(parent=root)
            if fn: v.set(fn)
        tk.Button(f, text='…', font=F_LBL, bg=C['border'], fg=C['cy'],
                  activebackground=C['panel'], activeforeground=C['gn'],
                  relief='flat', bd=0, padx=8, cursor='hand2',
                  command=pick).pack(side='left', padx=(2, 0))
        return v

    # DATOS
    _seccion('DATOS')
    archivo_v      = _file('archivo', _g('archivo', archivo_default))
    archivo_hist_v = _file('histórico previo', _g('archivo_hist', ''))

    # SIMULADOR
    _seccion('SIMULADOR')
    f_sim = tk.Frame(inner, bg=C['panel'])
    f_sim.pack(fill='x', pady=2, padx=10)
    tk.Label(f_sim, text='variante', font=F_LBL, bg=C['panel'], fg=C['mu'],
             width=14, anchor='w').pack(side='left')
    sim_v = tk.StringVar(value=_g('simulador', 'simular_ep_por_rango'))
    sim_cb = ttk.Combobox(f_sim, textvariable=sim_v,
                           values=list(SIMULADORES.keys()),
                           state='readonly', style='CB.TCombobox',
                           font=F_VAL)
    sim_cb.pack(side='left', fill='x', expand=True)

    # VENTANAS
    _seccion('VENTANAS')
    n_windows_v    = _entry('cantidad', _g('n_windows', 200))
    window_size_v  = _entry('tamaño', _g('window_size', 200))
    seed_v         = _entry('seed', _g('seed', 42))
    sliding_v      = _check('usar ventanas deslizantes', _g('sliding', False))
    sliding_paso_v = _entry('paso (sliding)', _g('sliding_paso', 50))
    ultimas_n_v    = _check('usar ÚLTIMAS N ops (N = tamaño)', _g('ultimas_n', False), color=C['am'])

    # ESTRATEGIA
    _seccion('ESTRATEGIA EP')
    ventana_ep_v = _entry('ventana EP', _g('ventana_ep', 50))
    max_mult_v   = _entry('max mult fijo', _g('max_mult', 0))
    apuesta_v    = _entry('apuesta base', _g('apuesta', 1.0))
    saldo_v      = _entry('saldo bankroll', _g('saldo', 0.0))
    multi_sim_v  = _check('multi-sim (compara todos)', _g('multi_sim', False), color=C['am'])

    # EXPORTACIÓN
    _seccion('EXPORTACIÓN')
    csv_check_v   = _check('exportar CSV resumen', _g('csv_check', False))
    csv_path_v    = _file('archivo CSV', _g('csv_path', 'backtest.csv'), save=True)
    det_check_v   = _check('exportar detalles CSV', _g('det_check', False))
    det_path_v    = _file('detalles CSV', _g('det_path', 'detalles.csv'), save=True)
    graf_check_v  = _check('exportar gráfico PNG', _g('graf_check', False))
    graf_path_v   = _file('gráfico PNG', _g('graf_path', 'backtest.png'), save=True)
    graf_vent_v   = _check('gráfica por ventana (interactivo)', _g('graf_vent', False), color=C['am'])

    # ── Panel fases (extremo derecho, siempre visible) ────────────────
    fases_col = tk.Frame(main, bg=C['panel'],
                         highlightbackground=C['border'], highlightthickness=1,
                         width=265)
    fases_col.pack(side='right', fill='y', padx=(6, 0))
    fases_col.pack_propagate(False)

    # Cabecera del panel de fases
    fh = tk.Frame(fases_col, bg=C['border'])
    fh.pack(fill='x')
    tk.Label(fh, text='  ◈ FASES / VENTANA', font=F_TIT,
             bg=C['border'], fg=C['cy'], pady=4).pack(side='left')

    # Área de contenido (se rellena al ejecutar)
    fases_inner = tk.Frame(fases_col, bg=C['panel'])
    fases_inner.pack(fill='both', expand=True)
    tk.Label(fases_inner, text='Ejecuta el backtest\npara ver las fases',
             bg=C['panel'], fg=C['mu'], font=('Consolas', 9),
             justify='center').pack(pady=30)

    # ── Panel resultados (centro) ──────────────────────
    der = tk.Frame(main, bg=C['panel'],
                   highlightbackground=C['border'], highlightthickness=1)
    der.pack(side='left', fill='both', expand=True)

    # Cabecera dinámica (cambia según modo)
    h2 = tk.Frame(der, bg=C['border'])
    h2.pack(fill='x')
    h2_lbl = tk.Label(h2, text='  ◈ RESULTADOS', font=F_TIT,
                       bg=C['border'], fg=C['cy'], pady=4)
    h2_lbl.pack(side='left')

    # Barra de progreso verde (avance del backtest)
    prog_canvas = tk.Canvas(h2, height=14, bg=C['border'],
                            highlightthickness=0, bd=0)
    prog_canvas.pack(side='right', fill='x', expand=True, padx=(8, 8), pady=6)
    prog_bar_id = prog_canvas.create_rectangle(0, 0, 0, 14,
                                                fill='#00FF88', outline='')
    _prog_state = {'pct': 0.0}

    def _set_progreso(pct):
        """pct ∈ [0,1] — fija el ancho del rectángulo verde."""
        try:
            pct = max(0.0, min(1.0, float(pct)))
            _prog_state['pct'] = pct
            w = max(1, prog_canvas.winfo_width())
            prog_canvas.coords(prog_bar_id, 0, 0, int(w * pct), 14)
        except Exception:
            pass

    def _reset_progreso():
        _prog_state['pct'] = 0.0
        try:
            prog_canvas.coords(prog_bar_id, 0, 0, 0, 14)
        except Exception:
            pass

    prog_canvas.bind('<Configure>',
                      lambda e: _set_progreso(_prog_state['pct']))

    # Container que contendrá o el text widget o el canvas multi-sim
    container = tk.Frame(der, bg=C['bg'])
    container.pack(fill='both', expand=True, padx=4, pady=4)

    # Frame texto (default)
    tf = tk.Frame(container, bg=C['bg'])
    tf.pack(fill='both', expand=True)

    txt = tk.Text(tf, bg=C['bg'], fg=C['tx'], font=F_MN,
                  insertbackground=C['cy'],
                  relief='flat', wrap='none', padx=8, pady=4)
    sb_y = tk.Scrollbar(tf, orient='vertical', command=txt.yview)
    sb_x = tk.Scrollbar(tf, orient='horizontal', command=txt.xview)
    txt.config(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
    sb_y.pack(side='right', fill='y')
    sb_x.pack(side='bottom', fill='x')
    txt.pack(side='left', fill='both', expand=True)

    # Frame canvas multi-sim (oculto inicialmente)
    canvas_frame = tk.Frame(container, bg=C['bg'])

    def _mostrar_texto():
        canvas_frame.pack_forget()
        tf.pack(fill='both', expand=True)
        h2_lbl.config(text='  ◈ RESULTADOS')

    def _mostrar_canvas(rows, n_v, params):
        tf.pack_forget()
        canvas_frame.pack(fill='both', expand=True)
        h2_lbl.config(text='  ◈ COMPARATIVA MULTI-SIM')
        _construir_canvas_multi_sim(canvas_frame, rows, n_v, params)

    # Tags de color
    for k, v in C.items():
        if k in ('bg', 'panel', 'border'):
            continue
        txt.tag_config(k, foreground=v)
    txt.tag_config('bold', font=('Consolas', 10, 'bold'))

    # ── Botones acción ─────────────────────────────────
    btnf = tk.Frame(root, bg=C['bg'])
    btnf.pack(fill='x', padx=8, pady=(0, 8))

    def limpiar():
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        txt.config(state='disabled')

    def _snapshot_config() -> dict:
        return {
            'archivo':      archivo_v.get(),
            'archivo_hist': archivo_hist_v.get(),
            'ultimas_n':    ultimas_n_v.get(),
            'simulador':    sim_v.get(),
            'n_windows':    n_windows_v.get(),
            'window_size':  window_size_v.get(),
            'seed':         seed_v.get(),
            'ventana_ep':   ventana_ep_v.get(),
            'max_mult':     max_mult_v.get(),
            'apuesta':      apuesta_v.get(),
            'saldo':        saldo_v.get(),
            'sliding':      sliding_v.get(),
            'sliding_paso': sliding_paso_v.get(),
            'multi_sim':    multi_sim_v.get(),
            'csv_check':    csv_check_v.get(),
            'csv_path':     csv_path_v.get(),
            'det_check':    det_check_v.get(),
            'det_path':     det_path_v.get(),
            'graf_check':   graf_check_v.get(),
            'graf_path':    graf_path_v.get(),
            'graf_vent':    graf_vent_v.get(),
        }

    def ejecutar_backtest():
        estado_lbl.config(text='● EJECUTANDO…', fg=C['am'])
        root.after(0, _reset_progreso)
        _guardar_config_dashboard(_snapshot_config())
        try:
            archivo = archivo_v.get().strip()
            if not os.path.exists(archivo):
                _mostrar_error(f'Archivo no encontrado: {archivo}')
                return

            ops = parsear_archivo(archivo)
            if not ops:
                _mostrar_error('No se pudieron parsear operaciones del archivo.')
                return
            ops_ref[0] = ops
            root.after(0, lambda: btn_fases_ep.config(state='normal'))

            # Histórico previo opcional (para simulares que admiten ops_hist, p.ej. simular_umbral_global)
            archivo_hist = archivo_hist_v.get().strip()
            ops_hist = []
            if archivo_hist and os.path.exists(archivo_hist):
                try:
                    ops_hist = parsear_archivo(archivo_hist)
                except Exception as _e:
                    print(f"⚠ no se pudo cargar histórico previo: {_e}")
                    ops_hist = []

            n_windows   = int(n_windows_v.get())
            window_size = int(window_size_v.get())
            seed        = int(seed_v.get())
            # Si está activo "últimas N ops", recortar antes de generar ventanas.
            if ultimas_n_v.get() and len(ops) > window_size:
                ops = ops[-window_size:]
            ventana_ep  = int(ventana_ep_v.get())
            max_mult    = int(max_mult_v.get())
            apuesta     = float(apuesta_v.get())
            saldo       = float(saldo_v.get())
            sliding     = sliding_v.get()
            sliding_paso = int(sliding_paso_v.get())
            multi_sim   = multi_sim_v.get()
            simulador   = sim_v.get()

            # ── Modo multi-sim → canvas con tarjetas ─────────────
            if multi_sim:
                # Simulación: 0 → 80%
                _on_prog_ms = lambda done, total: root.after(0, _set_progreso, 0.80 * done / max(1, total))
                rows, n_v, phase_data = _calcular_multi_sim(ops, n_windows, window_size, seed,
                                                             max_mult=max_mult,
                                                             apuesta_base=apuesta,
                                                             saldo_inicial=saldo,
                                                             on_progress=_on_prog_ms)
                params = {
                    'window_size': window_size, 'seed': seed,
                    'apuesta': apuesta, 'saldo': saldo,
                    'max_mult': max_mult,
                }
                # Render canvas: 85%
                root.after(0, _set_progreso, 0.85)
                root.after(0, lambda r=rows, nv=n_v, p=params: _mostrar_canvas(r, nv, p))
                # Panel de fases: 95%
                root.after(0, _set_progreso, 0.95)
                root.after(0, lambda pd=phase_data: _actualizar_fases_panel(fases_inner, pd))
                # 100% cuando todo lo encolado vía after(0) ya se haya pintado
                root.after_idle(lambda: _set_progreso(1.0))
                estado_lbl.config(text='● COMPLETADO', fg=C['gn'])
                _guardar_config_dashboard(_snapshot_config())
                return

            # Modo normal → text widget con reporte cyberpunk
            root.after(0, _mostrar_texto)

            phase_data_single = []
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                print(f"\n  {_A['cy']}◆{_A['R']}  {_A['mu']}datos cargados{_A['R']}"
                      f"  {_A['am']}{_A['B']}{len(ops)}{_A['R']} {_A['mu']}rondas{_A['R']}"
                      f"  {_A['dm']}·{_A['R']}  {_A['tx']}{archivo}{_A['R']}")

                if True:
                    if sliding:
                        ventanas = ventanas_deslizantes(ops, tamano=window_size,
                                                         paso=sliding_paso)
                        print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}ventanas{_A['R']}"
                              f"  {_A['am']}{_A['B']}{len(ventanas)}{_A['R']} "
                              f"{_A['mu']}deslizantes{_A['R']}"
                              f"  {_A['dm']}·  paso={sliding_paso}{_A['R']}")
                    else:
                        ventanas = ventanas_aleatorias(ops, n=n_windows,
                                                        tamano=window_size, seed=seed)
                        print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}ventanas{_A['R']}"
                              f"  {_A['am']}{_A['B']}{len(ventanas)}{_A['R']} "
                              f"{_A['mu']}aleatorias{_A['R']}"
                              f"  {_A['dm']}·  seed={seed}{_A['R']}")

                    sim_desc = SIMULADORES_DESC.get(simulador, simulador)
                    saldo_extra = f"  ·  saldo={saldo:.2f}" if saldo > 0 else ""
                    print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}simulando{_A['R']}"
                          f"  {_A['tx']}{sim_desc}{_A['R']}"
                          f"  {_A['dm']}·  EP={ventana_ep}  ·  apuesta={apuesta:.2f}{saldo_extra}{_A['R']}")

                    # Simulación principal: 0 → 60%
                    _on_prog = lambda done, total: root.after(0, _set_progreso, 0.60 * done / max(1, total))
                    resultados = ejecutar_simulaciones(ventanas, simulador=simulador,
                                                       ventana_ep=ventana_ep,
                                                       max_mult=max_mult,
                                                       apuesta_base=apuesta,
                                                       saldo_inicial=saldo,
                                                       on_progress=_on_prog,
                                                       ops_hist=ops_hist)
                    resultados = _normalizar_saldo_real(resultados, simulador)
                    root.after(0, _set_progreso, 0.62)
                    # Capturar datos de fases para el panel lateral
                    if simulador == 'simular_ep_por_rango':
                        for i, r in enumerate(resultados):
                            phase_data_single.append({
                                'idx': i + 1,
                                'n_total': r.get('n_total', 0),
                                'primera_pos': r.get('primera_pos', r.get('n_total', 0)),
                                'n_bets': r.get('n_bets', 0),
                                'n_skips': r.get('n_skips', 0),
                                'saldo_ep': r.get('saldo_ep', 0.0),
                                'bal_ep': r.get('bal_ep', []),
                            })
                    else:
                        # Re-simulación EP × Rango para alimentar el panel de fases: 62 → 78%
                        _nv = len(ventanas)
                        for i, v in enumerate(ventanas):
                            try:
                                r = simular_ep_por_rango(v, max_mult=max_mult,
                                                          apuesta_base=apuesta,
                                                          saldo_inicial=saldo)
                            except Exception:
                                r = {}
                            phase_data_single.append({
                                'idx': i + 1,
                                'n_total': r.get('n_total', len(v)),
                                'primera_pos': r.get('primera_pos', r.get('n_total', len(v))),
                                'n_bets': r.get('n_bets', 0),
                                'n_skips': r.get('n_skips', 0),
                                'saldo_ep': r.get('saldo_ep', 0.0),
                                'bal_ep': r.get('bal_ep', []),
                            })
                            root.after(0, _set_progreso, 0.62 + 0.16 * (i + 1) / max(1, _nv))
                    print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}completadas{_A['R']}"
                          f"  {_A['am']}{_A['B']}{len(resultados)}{_A['R']} "
                          f"{_A['mu']}simulaciones{_A['R']}")

                    root.after(0, _set_progreso, 0.80)
                    stats = calcular_estadisticas(resultados)
                    config = {
                        'total_ops': len(ops),
                        'window_size': window_size,
                        'n_windows': len(ventanas),
                        'simulador': simulador,
                        'ventana_ep': ventana_ep,
                        'max_mult': max_mult,
                        'apuesta': apuesta,
                        'archivo': archivo,
                        'seed': seed,
                        'sliding': sliding,
                        'saldo': saldo,
                    }
                    imprimir_reporte(stats, config)
                    root.after(0, _set_progreso, 0.85)

                    if csv_check_v.get():
                        exportar_csv(resultados, csv_path_v.get(), simulador)
                    if det_check_v.get():
                        exportar_detalles_csv(resultados, det_path_v.get())
                    if graf_check_v.get():
                        generar_graficos(resultados, stats, graf_path_v.get())
                    root.after(0, _set_progreso, 0.90)

                    print(f"  {_A['cy']}◆{_A['R']}  {_A['gn']}{_A['B']}BackTesting completado.{_A['R']}\n")

            output = buf.getvalue()
            _renderizar(output)
            root.after(0, _set_progreso, 0.93)

            # Actualizar panel de fases
            root.after(0, lambda pd=phase_data_single: _actualizar_fases_panel(fases_inner, pd))
            root.after(0, _set_progreso, 0.96)

            # Gráficas por ventana (fuera del redirect — proceso pesado, una subprocess por ventana)
            if not multi_sim and graf_vent_v.get():
                _lanzar_graficas_ventana(resultados, apuesta)

            root.after(0, _set_progreso, 0.98)
            root.after_idle(lambda: _set_progreso(1.0))
            estado_lbl.config(text='● COMPLETADO', fg=C['gn'])
        except Exception as e:
            import traceback
            _mostrar_error(traceback.format_exc())

    def _renderizar(output):
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        for seg, tags in _ansi_a_segmentos(output):
            txt.insert('end', seg, tags)
        txt.config(state='disabled')
        txt.see('1.0')

    def _mostrar_error(msg):
        txt.config(state='normal')
        txt.delete('1.0', 'end')
        txt.insert('end', '\n  ⚠  ERROR\n\n', ('rd', 'bold'))
        txt.insert('end', msg, ('rd',))
        txt.config(state='disabled')
        estado_lbl.config(text='● ERROR', fg=C['rd'])

    def _lanzar_graficas_ventana(resultados, apuesta):
        import subprocess, tempfile
        script = os.path.abspath(__file__)
        n = len(resultados)
        for i, r in enumerate(resultados):
            det = r.get('detalles', [])
            if not det:
                continue
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                              encoding='utf-8', delete=False) as tf:
                json.dump(det, tf, default=str)
                tmppath = tf.name
            try:
                codigo = (
                    "import sys, json, matplotlib\n"
                    "matplotlib.use('TkAgg')\n"
                    f"sys.path.insert(0, {json.dumps(os.path.dirname(script))})\n"
                    "from backtest_ep import generar_grafico_ventana\n"
                    f"with open({json.dumps(tmppath)}, 'r', encoding='utf-8') as f:\n"
                    "    detalles = json.load(f)\n"
                    f"generar_grafico_ventana(detalles, {i}, {apuesta})\n"
                )
                proc = subprocess.Popen([sys.executable, '-c', codigo],
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
                proc.wait()
            finally:
                try: os.unlink(tmppath)
                except Exception: pass
            # Progreso 96% → 98% durante la generación de gráficas por ventana
            root.after(0, _set_progreso, 0.96 + 0.02 * (i + 1) / max(1, n))

    def ejecutar_async():
        threading.Thread(target=ejecutar_backtest, daemon=True).start()

    btn_run = tk.Button(btnf, text='▶  EJECUTAR  BACKTEST',
                        font=('Consolas', 12, 'bold'),
                        bg=C['border'], fg=C['gn'],
                        activebackground=C['panel'], activeforeground=C['cy'],
                        relief='raised', bd=1, cursor='hand2', pady=8,
                        command=ejecutar_async)
    btn_run.pack(side='left', fill='x', expand=True, padx=(0, 4))

    btn_clr = tk.Button(btnf, text='✕  LIMPIAR',
                        font=('Consolas', 10),
                        bg=C['panel'], fg=C['mu'],
                        activebackground=C['border'], activeforeground=C['rd'],
                        relief='flat', cursor='hand2', pady=8, padx=20,
                        command=limpiar)
    btn_clr.pack(side='left', padx=(0, 4))

    def _salir():
        _guardar_config_dashboard(_snapshot_config())
        root.destroy()

    btn_exit = tk.Button(btnf, text='⏻  SALIR',
                         font=('Consolas', 10),
                         bg=C['panel'], fg=C['mu'],
                         activebackground=C['border'], activeforeground=C['rd'],
                         relief='flat', cursor='hand2', pady=8, padx=20,
                         command=_salir)
    btn_exit.pack(side='left')

    root.protocol('WM_DELETE_WINDOW', _salir)

    # Mensaje inicial
    txt.config(state='normal')
    txt.insert('end', '\n  ', ('mu',))
    txt.insert('end', '◆', ('cy', 'bold'))
    txt.insert('end', '  Configura los parámetros y pulsa ', ('mu',))
    txt.insert('end', 'EJECUTAR BACKTEST', ('gn', 'bold'))
    txt.insert('end', '\n', ('mu',))
    txt.config(state='disabled')

    root.mainloop()


# ── Help cyberpunk ──────────────────────────────────────────────────────

def _imprimir_help_cyberpunk():
    """Reemplaza el --help estándar de argparse con un dashboard cyberpunk."""
    _habilitar_colores_windows()
    W = _W

    TITLE = '◈  B A C K T E S T   E P  ─  GUÍA DE USO'
    tpad = ' ' * (W - 2 - len(TITLE))
    print()
    print(f'{_A["dm"]}╔{"═"*W}╗{_A["R"]}')
    print(f'{_A["dm"]}║{_A["R"]}  {_A["B"]}{_A["cy"]}{TITLE}{_A["R"]}{tpad}{_A["dm"]}║{_A["R"]}')
    print(f'{_A["dm"]}╚{"═"*W}╝{_A["R"]}')

    # ── Uso ──────────────────────────────────────────────
    print(_sec('USO'))
    print()
    print(f'    {_A["tx"]}python backtest_ep.py {_A["mu"]}[opciones]{_A["R"]}')

    # ── Argumentos ───────────────────────────────────────
    args_data = [
        ('--archivo, -a',       'reconstructor_data_AI…', 'Archivo de datos histórico'),
        ('--n-windows, -n',     '200',                    'Nº de ventanas aleatorias'),
        ('--window-size, -w',   '200',                    'Registros por ventana'),
        ('--simulador, -s',     'simular_ep_por_rango',   'Variante EP a usar'),
        ('--ventana-ep',        '50',                     'Tamaño rolling EP'),
        ('--seed',              '42',                     'Semilla aleatoria'),
        ('--max-mult',          '0 (sin límite)',         'Multiplicador máximo fijo'),
        ('--apuesta',           '1.0',                    'Apuesta base en unidades'),
        ('--saldo',             '0 (sin límite)',         'Bankroll → cap mult dinámico'),
        ('--sliding',           '—',                      'Usar ventanas deslizantes'),
        ('--sliding-paso',      '50',                     'Paso ventanas deslizantes'),
        ('--multi-sim',         '—',                      'Comparar todas variantes'),
        ('--csv FILE',          '—',                      'Exportar CSV resumen'),
        ('--detalles-csv FILE', '—',                      'Detalle ronda x ronda'),
        ('--grafico FILE',      '—',                      'Exportar gráfico PNG'),
        ('--grafico-ventanas',  '—',                      'Gráfica por ventana'),
        ('--silent',            '—',                      'Sin reporte en terminal'),
        ('--gui',               '—',                      'Dashboard Tkinter interactivo'),
        ('--help, -h',          '—',                      'Mostrar esta ayuda'),
    ]

    print(_sec('ARGUMENTOS'))
    print()
    print(f'  {_A["mu"]}{"ARGUMENTO":<22}{"DEFAULT":<23}DESCRIPCIÓN{_A["R"]}')
    print(f'  {_A["dm"]}{"─"*68}{_A["R"]}')

    for arg, default, desc in args_data:
        a = arg if len(arg) <= 21 else arg[:20] + '…'
        d = default if len(default) <= 22 else default[:21] + '…'
        print(f'  {_A["cy"]}{a:<22}{_A["R"]}'
              f'{_A["am"]}{d:<23}{_A["R"]}'
              f'{_A["tx"]}{desc}{_A["R"]}')

    # ── Simuladores ──────────────────────────────────────
    print(_sec('SIMULADORES DISPONIBLES'))
    print()
    for sim_id, desc in SIMULADORES_DESC.items():
        print(f'  {_A["cy"]}◆{_A["R"]}  {_A["am"]}{_A["B"]}{sim_id:<22}{_A["R"]}  {_A["tx"]}{desc}{_A["R"]}')

    # ── Ejemplos ─────────────────────────────────────────
    print(_sec('EJEMPLOS'))
    print()
    examples = [
        ('Dashboard interactivo Tkinter (todos los parámetros)',
         'python backtest_ep.py --gui'),
        ('Comparativa de todas las variantes EP',
         'python backtest_ep.py --multi-sim'),
        ('Backtest con apuesta y multiplicador limitado',
         'python backtest_ep.py -n 500 -w 200 --apuesta 2.5 --max-mult 3'),
        ('Backtest con bankroll de 50€ (cap mult según saldo)',
         'python backtest_ep.py --saldo 50 --apuesta 1.0'),
        ('Exportar CSV resumen + gráfico PNG',
         'python backtest_ep.py --csv res.csv --grafico ep.png'),
        ('Detalle ronda por ronda (apuestas)',
         'python backtest_ep.py --detalles-csv det.csv'),
        ('Gráficas interactivas por ventana (pantalla completa)',
         'python backtest_ep.py -n 5 --grafico-ventanas'),
        ('Ventanas deslizantes en lugar de aleatorias',
         'python backtest_ep.py --sliding --sliding-paso 50'),
    ]
    for desc, cmd in examples:
        print(f'  {_A["cy"]}▸{_A["R"]} {_A["mu"]}{desc}{_A["R"]}')
        print(f'    {_A["tx"]}{cmd}{_A["R"]}')
        print()

    print(f'  {_A["dm"]}{"─" * W}{_A["R"]}')
    print()


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BackTesting de la Estrategia Perfecta Simulada",
        add_help=True,
    )
    parser.print_help = lambda file=None: _imprimir_help_cyberpunk()

    parser.add_argument('--archivo', '-a', default='reconstructor_data_AI.txt',
                        help='Archivo de datos (default: reconstructor_data_AI.txt)')
    parser.add_argument('--n-windows', '-n', type=int, default=200,
                        help='Número de ventanas aleatorias (default: 200)')
    parser.add_argument('--window-size', '-w', type=int, default=200,
                        help='Registros por ventana (default: 200)')
    parser.add_argument('--simulador', '-s', default='simular_ep_por_rango',
                        choices=list(SIMULADORES.keys()),
                        help='Variante EP a simular (default: simular_ep_por_rango)')
    parser.add_argument('--ventana-ep', type=int, default=EP_VENTANA,
                        help=f'Tamaño ventana rolling EP (default: {EP_VENTANA})')
    parser.add_argument('--seed', type=int, default=42,
                        help='Semilla aleatoria (default: 42)')
    parser.add_argument('--max-mult', type=int, default=0,
                        help='Limitar multiplicador máximo (0=sin límite, ej: 3=límite 3x)')
    parser.add_argument('--apuesta', type=float, default=1.0,
                        help='Apuesta base en unidades (default: 1.0, ej: --apuesta 5.0)')
    parser.add_argument('--saldo', type=float, default=0.0,
                        help='Saldo inicial / bankroll (0=sin límite, ej: --saldo 50)')

    parser.add_argument('--sliding', action='store_true',
                        help='Usar ventanas deslizantes en lugar de aleatorias')
    parser.add_argument('--sliding-paso', type=int, default=50,
                        help='Paso entre ventanas deslizantes (default: 50)')
    parser.add_argument('--ultimas-n', action='store_true',
                        help='Tomar las ÚLTIMAS N ops antes de simular (N=window_size). '
                             'Usar con --window-size, --n-windows 1.')
    parser.add_argument('--archivo-hist', type=str, default='',
                        help='Archivo histórico previo (ops_hist) para simular_umbral_global')

    parser.add_argument('--multi-sim', action='store_true',
                        help='Ejecutar y comparar todos los simuladores')

    parser.add_argument('--csv', type=str, help='Exportar resultados a CSV')
    parser.add_argument('--detalles-csv', type=str, help='Exportar detalle ronda por ronda a CSV')
    parser.add_argument('--grafico', type=str, help='Exportar gráfico PNG')
    parser.add_argument('--grafico-ventanas', action='store_true',
                        help='Mostrar gráfica a pantalla completa de cada ventana (proceso separado)')
    parser.add_argument('--silent', action='store_true', help='Suprimir reporte en terminal')
    parser.add_argument('--gui', action='store_true',
                        help='Abrir dashboard Tkinter interactivo')

    args = parser.parse_args()

    # ── Lanzar GUI si se solicita ──────────────────────────────
    if args.gui:
        lanzar_dashboard(args.archivo)
        return

    # ── Cargar datos ─────────────────────────────────────────────
    if not os.path.exists(args.archivo):
        print(f"❌ Archivo no encontrado: {args.archivo}")
        sys.exit(1)

    _habilitar_colores_windows()
    ops = parsear_archivo(args.archivo)
    if not ops:
        print(f"  {_A['rd']}✖  No se pudieron parsear operaciones.{_A['R']}")
        sys.exit(1)

    print(f"\n  {_A['cy']}◆{_A['R']}  {_A['mu']}datos cargados{_A['R']}"
          f"  {_A['am']}{_A['B']}{len(ops)}{_A['R']} {_A['mu']}rondas{_A['R']}"
          f"  {_A['dm']}·{_A['R']}  {_A['tx']}{args.archivo}{_A['R']}")

    # ── Multi-sim: ejecutar todas las variantes ───────────────────
    if args.multi_sim:
        _imprimir_multi_sim(ops, args.n_windows, args.window_size, args.seed,
                              max_mult=args.max_mult, apuesta_base=args.apuesta,
                              saldo_inicial=args.saldo)
        return

    # ── Recortar a últimas N ops si se solicita ─────────────────
    if args.ultimas_n and len(ops) > args.window_size:
        ops = ops[-args.window_size:]
        print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}últimas N ops{_A['R']}  "
              f"{_A['am']}{_A['B']}{len(ops)}{_A['R']} {_A['mu']}ops{_A['R']}")

    # ── Cargar histórico previo si aplica ───────────────────────
    cli_ops_hist = []
    if args.archivo_hist and os.path.exists(args.archivo_hist):
        cli_ops_hist = parsear_archivo(args.archivo_hist)
        print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}archivo_hist{_A['R']}  "
              f"{_A['am']}{_A['B']}{len(cli_ops_hist)}{_A['R']} {_A['mu']}ops_hist{_A['R']}")

    # ── Generar ventanas ──────────────────────────────────────────
    if args.sliding:
        ventanas = ventanas_deslizantes(ops, tamano=args.window_size, paso=args.sliding_paso)
        print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}ventanas{_A['R']}"
              f"  {_A['am']}{_A['B']}{len(ventanas)}{_A['R']} {_A['mu']}deslizantes{_A['R']}"
              f"  {_A['dm']}·  paso={args.sliding_paso}{_A['R']}")
    else:
        ventanas = ventanas_aleatorias(ops, n=args.n_windows, tamano=args.window_size, seed=args.seed)
        print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}ventanas{_A['R']}"
              f"  {_A['am']}{_A['B']}{len(ventanas)}{_A['R']} {_A['mu']}aleatorias{_A['R']}"
              f"  {_A['dm']}·  seed={args.seed}{_A['R']}")

    # ── Ejecutar simulaciones ─────────────────────────────────────
    sim_desc = SIMULADORES_DESC.get(args.simulador, args.simulador)
    saldo_extra = f"  ·  saldo={args.saldo:.2f}" if args.saldo > 0 else ""
    print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}simulando{_A['R']}"
          f"  {_A['tx']}{sim_desc}{_A['R']}"
          f"  {_A['dm']}·  EP={args.ventana_ep}  ·  apuesta={args.apuesta:.2f}{saldo_extra}{_A['R']}")
    resultados = ejecutar_simulaciones(ventanas, simulador=args.simulador,
                                       ventana_ep=args.ventana_ep, max_mult=args.max_mult,
                                       apuesta_base=args.apuesta,
                                       saldo_inicial=args.saldo,
                                       ops_hist=cli_ops_hist)

    resultados = _normalizar_saldo_real(resultados, args.simulador)
    print(f"  {_A['cy']}◆{_A['R']}  {_A['mu']}completadas{_A['R']}"
          f"  {_A['am']}{_A['B']}{len(resultados)}{_A['R']} {_A['mu']}simulaciones{_A['R']}")

    # ── Calcular estadísticas ─────────────────────────────────────
    stats = calcular_estadisticas(resultados)
    config = {
        'total_ops': len(ops),
        'window_size': args.window_size,
        'n_windows': len(ventanas),
        'simulador': args.simulador,
        'ventana_ep': args.ventana_ep,
        'max_mult': args.max_mult,
        'apuesta': args.apuesta,
        'archivo': args.archivo,
        'seed': args.seed,
        'sliding': args.sliding,
        'saldo': args.saldo,
    }

    # ── Reporte ───────────────────────────────────────────────────
    if not args.silent:
        imprimir_reporte(stats, config)

    # ── Exportar ───────────────────────────────────────────────────
    if args.csv:
        exportar_csv(resultados, args.csv, args.simulador)

    if args.detalles_csv:
        exportar_detalles_csv(resultados, args.detalles_csv)

    if args.grafico:
        generar_graficos(resultados, stats, args.grafico)

    # ── Gráfico interactivo por ventana ────────────────────────────
    if args.grafico_ventanas:
        import subprocess
        import tempfile
        script_path = os.path.abspath(__file__)
        for i, r in enumerate(resultados):
            detalles = r.get('detalles', [])
            if not detalles:
                continue
            # Persistir detalles en archivo temporal (evita límite de longitud
            # de línea de comandos en Windows con ventanas grandes).
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                              encoding='utf-8', delete=False) as tf:
                json.dump(detalles, tf, default=str)
                tmppath = tf.name
            try:
                codigo = (
                    "import sys, json, matplotlib\n"
                    "matplotlib.use('TkAgg')\n"
                    f"sys.path.insert(0, {json.dumps(os.path.dirname(script_path))})\n"
                    "from backtest_ep import generar_grafico_ventana\n"
                    f"with open({json.dumps(tmppath)}, 'r', encoding='utf-8') as f:\n"
                    "    detalles = json.load(f)\n"
                    f"generar_grafico_ventana(detalles, {i}, {args.apuesta})\n"
                )
                proc = subprocess.Popen([sys.executable, '-c', codigo],
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                try:
                    proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
                    raise
            finally:
                try: os.unlink(tmppath)
                except Exception: pass

    print(f"  {_A['cy']}◆{_A['R']}  {_A['gn']}{_A['B']}BackTesting completado.{_A['R']}")
    print()


if __name__ == '__main__':
    main()
