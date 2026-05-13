"""
ep_core.py — Logica pura de la Estrategia Perfecta (sin UI, sin matplotlib).
Importable desde cualquier hilo sin efectos secundarios.
"""
from collections import deque

EP_VENTANA        = 50
EP_MIN_OPS        = 10
EP_UMBRAL_ESTADO  = 53.2   # >= esperanza matematica positiva con cuota 1.9
EP_UMBRAL_PRIOR   = 50.0   # > 50% → tiene prioridad


def ep_mult(conf: float) -> int:
    """Multiplicador segun win rate o confianza."""
    if conf >= 90: return 7
    if conf >= 85: return 6
    if conf >= 80: return 5
    if conf >= 75: return 4
    if conf >= 70: return 3
    if conf >= 65: return 2
    if conf >= 60: return 1
    return 1


PNL_ACIERTO = 0.9
PNL_FALLO   = -1.0


def ep_simular_combinado(ops: list, ventana: int = EP_VENTANA) -> dict:
    """
    Simula EP combinando UMBRAL acumulado + VENTANA rolling.
    Misma logica que estrategia_perfecta.simular_combinado() pero sin deps pesadas.
    """
    acum = {}
    ventanas = {}

    bal_real = [0.0]
    bal_ep   = [0.0]
    n_bets   = 0
    n_skips  = 0

    def wr_acum(r, m):
        b = acum.get(r, {}).get(m, {})
        o = b.get('ops', 0)
        return b['ganadas'] / o * 100 if o >= EP_MIN_OPS else None

    def wr_vent(r, m):
        v = ventanas.get(r, {}).get(m, deque())
        n = len(v)
        return sum(v) / n * 100 if n >= EP_MIN_OPS else None

    for op in ops:
        rango  = op['rango']
        modo   = op['modo']
        ganada = op['ganada']

        if rango not in acum:
            acum[rango] = {}
        if modo not in acum[rango]:
            acum[rango][modo] = {'ops': 0, 'ganadas': 0}
        if rango not in ventanas:
            ventanas[rango] = {}
        if modo not in ventanas[rango]:
            ventanas[rango][modo] = deque(maxlen=ventana)

        d_hist = wr_acum(rango, 'DIRECTO')
        i_hist = wr_acum(rango, 'INVERSO')
        d_vent = wr_vent(rango, 'DIRECTO')
        i_vent = wr_vent(rango, 'INVERSO')

        if d_hist is not None or i_hist is not None:
            dh = d_hist or 0
            ih = i_hist or 0
            best_hist = 'DIRECTO' if dh >= ih else 'INVERSO'
            best_hist_wr = max(dh, ih)
        else:
            best_hist = None
            best_hist_wr = 0

        if d_vent is not None or i_vent is not None:
            dv = d_vent or 0
            iv = i_vent or 0
            best_vent = 'DIRECTO' if dv >= iv else 'INVERSO'
            best_vent_wr = max(dv, iv)
        else:
            best_vent = None
            best_vent_wr = 0

        if best_hist is None:
            apostar = False
            best_mode = None
            wr_final = 0
        elif best_vent is None:
            apostas   = best_hist_wr >= EP_UMBRAL_ESTADO
            best_mode = best_hist
            wr_final  = best_hist_wr
        elif best_hist == best_vent:
            wr_final  = best_vent_wr
            apuestas   = wr_final >= EP_UMBRAL_ESTADO
            best_mode = best_hist
        else:
            apostas   = False
            best_mode = None
            wr_final = 0

        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO))

        if best_mode and best_mode == best_hist:
            mult_real = op.get('mult_real')
            if mult_real is not None and mult_real > 0:
                mult = mult_real
            else:
                mult = ep_mult(wr_final)
            resultado = ganada if best_mode == modo else not ganada
            pnl = (PNL_ACIERTO if resultado else PNL_FALLO) * mult
            bal_ep.append(bal_ep[-1] + pnl)
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        acum[rango][modo]['ops'] += 1
        if ganada:
            acum[rango][modo]['ganadas'] += 1
        ventanas[rango][modo].append(1 if ganada else 0)

    return {
        'bal_real':   bal_real,
        'bal_ep':     bal_ep,
        'n_bets':     n_bets,
        'n_skips':    n_skips,
        'n_total':    len(ops),
        'saldo_real': bal_real[-1],
        'saldo_ep':   bal_ep[-1],
    }


def ep_evaluar(ventana_rangos: dict, rango: str, modo: str) -> dict:
    """
    Evalua si rango+modo pasa los filtros de la Estrategia Perfecta.
    Devuelve: {'pasar': bool, 'wr': float, 'n': int, 'nivel': str, 'razon': str}
    """
    v = ventana_rangos.get(rango, {}).get(modo, None)
    n = len(v) if v else 0

    if n < EP_MIN_OPS:
        return {'pasar': True, 'wr': 0.0, 'n': n,
                'nivel': 'SIN_DATOS', 'razon': f'ventana insuficiente ({n} ops)'}

    wr = sum(v) / n * 100
    if wr >= EP_UMBRAL_ESTADO:
        return {'pasar': True,  'wr': wr, 'n': n,
                'nivel': 'BUENO',     'razon': f'{wr:.1f}% >= {EP_UMBRAL_ESTADO}%'}
    elif wr >= EP_UMBRAL_PRIOR:
        return {'pasar': False, 'wr': wr, 'n': n,
                'nivel': 'PRIORIDAD', 'razon': f'{wr:.1f}% sin estado ({EP_UMBRAL_ESTADO}%)'}
    else:
        return {'pasar': False, 'wr': wr, 'n': n,
                'nivel': 'BAJA',      'razon': f'{wr:.1f}% baja prioridad'}