"""
analizar_ep.py — Analizador de Estrategia Perfecta sobre datos en vivo.

Uso:
    python3 analizar_ep.py                          # Analiza pnl_live_history.json
    python3 analizar_ep.py --archivo pnl_live_history.json
    python3 analizar_ep.py --comparar               # Compara con pnl_decision_history.json
    python3 analizar_ep.py --umbrales               # Prueba distintos umbrales
"""

import json
import sys
import os
from collections import deque, Counter
from pathlib import Path

# ── Configuración de EP (misma que ep_core.py) ──────────────────────────────
EP_VENTANA = 50
EP_MIN_OPS = 10
EP_UMBRAL_ESTADO = 53.2
EP_UMBRAL_PRIOR = 50.0
PNL_ACIERTO = 0.9
PNL_FALLO = -1.0

RANGOS_ORDEN = ["0-5", "5-10", "10-15", "15-20", "20-25",
                "25-30", "30-35", "35-40", "40-45", "45-50", "+50"]


# ── Utilidades ──────────────────────────────────────────────────────────────

def ep_mult(conf: float) -> int:
    if conf >= 90: return 7
    if conf >= 85: return 6
    if conf >= 80: return 5
    if conf >= 75: return 4
    if conf >= 70: return 3
    if conf >= 65: return 2
    if conf >= 60: return 1
    return 1


def calcular_rango(dif):
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


# ── Parsers ─────────────────────────────────────────────────────────────────

def parsear_live_history(ruta: str | Path) -> list:
    """Parsea pnl_live_history.json → lista de ops."""
    data = json.loads(Path(ruta).read_text())
    ops_data = data.get('ops', [])
    raw_data = data.get('raw', [])

    if not ops_data and raw_data:
        ops_data = []
        for ev in raw_data:
            acierto = ev.get('acierto')
            wr = ev.get('wr', 50.0)
            modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
            ops_data.append({
                'skip': modo == 'SKIP', 'acierto': acierto,
                'rango': ev.get('rango', '0-5'), 'modo': modo,
                'wr': wr, 'est': ev.get('est', 'ESTABLE'),
                'acel': ev.get('acel', 0.0),
            })

    ops = []
    for entry in ops_data:
        rango = entry.get('rango', '')
        modo = entry.get('modo', 'DIRECTO')
        acierto = entry.get('acierto')
        if not rango or acierto is None:
            continue
        ops.append({
            'rango': rango, 'modo': modo, 'acierto': bool(acierto),
            'ganada': bool(acierto),
            'mult': float(entry.get('mult', 1)),
            'wr': float(entry.get('wr', 50)),
            'est': entry.get('est', 'ESTABLE'),
            'acel': float(entry.get('acel', 0)),
        })
    return ops


def parsear_decision_history(ruta: str | Path) -> list:
    """Parsea pnl_decision_history.json → lista de decisiones."""
    return json.loads(Path(ruta).read_text())


# ── Simulador EP combinado (histórico + ventana rolling) ────────────────────

def simular_ep_combinado(ops: list, ventana: int = EP_VENTANA) -> dict:
    """
    Simula EP combinando UMBRAL acumulado + VENTANA rolling.
    Misma lógica que ep_core.ep_simular_combinado().
    """
    acum = {}
    ventanas = {}
    bal_real = [0.0]
    bal_ep = [0.0]
    n_bets = 0
    n_skips = 0
    detalles = []

    def wr_acum(r, m):
        b = acum.get(r, {}).get(m, {})
        o = b.get('ops', 0)
        return b['ganadas'] / o * 100 if o >= EP_MIN_OPS else None

    def wr_vent(r, m):
        v = ventanas.get(r, {}).get(m, deque())
        n = len(v)
        return sum(v) / n * 100 if n >= EP_MIN_OPS else None

    for op in ops:
        rango = op['rango']
        modo = op['modo']
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
            apostar = best_hist_wr >= EP_UMBRAL_ESTADO
            best_mode = best_hist
            wr_final = best_hist_wr
        elif best_hist == best_vent:
            wr_final = best_vent_wr
            apostar = wr_final >= EP_UMBRAL_ESTADO
            best_mode = best_hist
        else:
            apostar = False
            best_mode = None
            wr_final = 0

        # Curva real
        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO))

        # Curva simulada
        if apostar and best_mode:
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

        detalles.append({
            'rango': rango, 'modo': modo, 'ganada': ganada,
            'best_mode': best_mode, 'apostar': apostar,
        })

        acum[rango][modo]['ops'] += 1
        if ganada:
            acum[rango][modo]['ganadas'] += 1
        ventanas[rango][modo].append(1 if ganada else 0)

    return {
        'bal_real': bal_real, 'bal_ep': bal_ep,
        'n_bets': n_bets, 'n_skips': n_skips, 'n_total': len(ops),
        'saldo_real': bal_real[-1], 'saldo_ep': bal_ep[-1],
        'detalles': detalles,
        'ventanas': ventanas,
    }


# ── Simulador EP rolling por rango+modo (con multiplicadores) ─────────────

def simular_ep_por_rango(ops: list, ventana: int = EP_VENTANA) -> dict:
    """
    Ventana rolling por RANGO+MODO, con multiplicador EP por confianza.
    Misma lógica exacta que estrategia_perfecta.simular().
    El multiplicador escala según el WR: 60%→1x, 90%→7x.
    Esto dispara el PNL cuando un rango tiene WR consistente.
    """
    ventanas = {}
    bal_real = [0.0]
    bal_ep = [0.0]
    n_bets = 0
    n_skips = 0
    detalles = []

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
        mult = ep_mult(wr_v) if n_v >= EP_MIN_OPS else 1
        pnl_base = (PNL_ACIERTO if ganada_modo else PNL_FALLO) * mult
        apostar = n_v >= EP_MIN_OPS and wr_v >= EP_UMBRAL_ESTADO

        # Curva real (apuesta mayoría siempre, sin filtro)
        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            _m_real = op.get('mult', 1)
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO) * _m_real)

        # Curva EP con multiplicador
        if apostar:
            bal_ep.append(bal_ep[-1] + pnl_base)
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        detalles.append({
            'rango': rango, 'modo': modo, 'ganada': ganada,
            'wr_v': wr_v, 'apostar': apostar, 'n_v': n_v,
            'mult': mult, 'pnl': pnl_base if apostar else 0,
        })
        v.append(1 if ganada_modo else 0)

    return {
        'bal_real': bal_real, 'bal_ep': bal_ep,
        'n_bets': n_bets, 'n_skips': n_skips, 'n_total': len(ops),
        'saldo_real': bal_real[-1], 'saldo_ep': bal_ep[-1],
        'detalles': detalles,
        'ventanas': ventanas,
    }


# ── Simulador EP rolling simple (ventana global) ────────────────────────────

def simular_ep_rolling(ops: list, ventana: int = 20, umbral: float = 53.2,
                       min_wr_dir: int = 0, contrarian: bool = False) -> dict:
    """
    Simula EP con ventana rolling GLOBAL (no por rango).
    Misma lógica que pnl_data.curva_pnl_ep().
    """
    v = deque(maxlen=ventana)
    acum = 0.0
    curva = []
    n_ac = 0
    n_bets = 0
    prev_wr = 50.0

    for op in ops:
        n_v = len(v)
        if n_v >= EP_MIN_OPS:
            wr = sum(v) / n_v * 100
            if wr >= umbral:
                nueva_dir = 'DIRECTO'
            elif wr <= (100 - umbral):
                nueva_dir = 'INVERSO'
            else:
                curva.append(acum)
                v.append(1 if op['acierto'] else 0)
                prev_wr = op.get('wr', 50)
                continue

            dir_efectiva = ('INVERSO' if nueva_dir == 'DIRECTO' else 'DIRECTO') if contrarian else nueva_dir

            if min_wr_dir > 0:
                wr_op = prev_wr
                if dir_efectiva == 'DIRECTO' and wr_op < min_wr_dir:
                    curva.append(acum)
                    v.append(1 if op['acierto'] else 0)
                    prev_wr = op.get('wr', 50)
                    continue
                if dir_efectiva == 'INVERSO' and wr_op > (100 - min_wr_dir):
                    curva.append(acum)
                    v.append(1 if op['acierto'] else 0)
                    prev_wr = op.get('wr', 50)
                    continue

            gano = op['acierto'] if dir_efectiva == 'DIRECTO' else not op['acierto']
            _m = op.get('mult', 1)
            if gano:
                acum += 0.9 * _m
                n_ac += 1
            else:
                acum -= 1.0 * _m
            n_bets += 1

        curva.append(acum)
        v.append(1 if op['acierto'] else 0)
        prev_wr = op.get('wr', 50)

    return {'curva': curva, 'n_ac': n_ac, 'n_bets': n_bets, 'pnl': acum}


# ── Estadísticas ────────────────────────────────────────────────────────────

def stats_rangos(ops: list) -> dict:
    """Calcula estadísticas por rango."""
    stats = {}
    for o in ops:
        r = o.get('rango', '?')
        if r not in stats:
            stats[r] = {'total': 0, 'aciertos': 0, 'skips': 0, 'dir': 0, 'inv': 0}
        stats[r]['total'] += 1
        if o['acierto']:
            stats[r]['aciertos'] += 1
        m = o.get('modo', '')
        if m == 'SKIP':
            stats[r]['skips'] += 1
        elif m == 'DIRECTO':
            stats[r]['dir'] += 1
        elif m == 'INVERSO':
            stats[r]['inv'] += 1
    return stats


def stats_decisiones(decs: list) -> dict:
    """Analiza el histórico de decisiones."""
    apostadas = [d for d in decs if d.get('decision') == 'APOSTADA']
    skips = [d for d in decs if d.get('decision') in ('SKIP', 'OBS')]

    ac_ap = sum(1 for d in apostadas if d.get('acierto') == True)
    fa_ap = sum(1 for d in apostadas if d.get('acierto') == False)
    pnl_ap = sum((d.get('pnl') or 0) for d in apostadas)

    ac_sk = sum(1 for d in skips if d.get('acierto') == True)
    fa_sk = sum(1 for d in skips if d.get('acierto') == False)

    # PNL por modo
    pnl_modo = {}
    for d in decs:
        m = d.get('modo', '?')
        p = d.get('pnl') or 0
        if m not in pnl_modo:
            pnl_modo[m] = {'ops': 0, 'ac': 0, 'fa': 0, 'pnl': 0.0}
        pnl_modo[m]['ops'] += 1
        if d.get('acierto') == True:
            pnl_modo[m]['ac'] += 1
        elif d.get('acierto') == False:
            pnl_modo[m]['fa'] += 1
        pnl_modo[m]['pnl'] += p

    # Balance real y de filtro
    bal_real = None
    bal_filtro = None
    for d in reversed(decs):
        if bal_real is None and d.get('balance_real') is not None:
            bal_real = d['balance_real']
        if bal_filtro is None and d.get('balance_filtro') is not None:
            bal_filtro = d['balance_filtro']
        if bal_real is not None and bal_filtro is not None:
            break

    return {
        'total': len(decs),
        'apostadas': len(apostadas),
        'skips': len(skips),
        'ac_ap': ac_ap, 'fa_ap': fa_ap,
        'wr_ap': ac_ap / max(ac_ap + fa_ap, 1) * 100,
        'pnl_ap': pnl_ap,
        'pnl_por_op': pnl_ap / max(ac_ap + fa_ap, 1),
        'ac_sk': ac_sk, 'fa_sk': fa_sk,
        'wr_sk': ac_sk / max(ac_sk + fa_sk, 1) * 100,
        'pnl_modo': pnl_modo,
        'balance_real': bal_real or 0,
        'balance_filtro': bal_filtro or 0,
    }


# ── Reportes ────────────────────────────────────────────────────────────────

def reporte_separador(titulo: str):
    print()
    print("═" * 70)
    print(f"  {titulo}")
    print("═" * 70)


def reporte_general(ops: list):
    """Reporte básico del histórico de operaciones."""
    reporte_separador("DATOS GENERALES")
    n_ac = sum(1 for o in ops if o['acierto'])
    n_sk = sum(1 for o in ops if o.get('modo') == 'SKIP')
    n_dir = sum(1 for o in ops if o.get('modo') == 'DIRECTO')
    n_inv = sum(1 for o in ops if o.get('modo') == 'INVERSO')

    print(f"  Total rondas:       {len(ops)}")
    print(f"  Acertadas (mayoría): {n_ac}/{len(ops)}  ({n_ac/max(len(ops),1)*100:.1f}%)")
    print(f"  SKIP: {n_sk}  |  DIRECTO: {n_dir}  |  INVERSO: {n_inv}")
    print()

    # BASE
    pnl_base = sum(0.9 * o.get('mult', 1) if o['acierto'] else -1.0 * o.get('mult', 1) for o in ops)
    print(f"  PNL BASE (mayoría siempre): {pnl_base:+.2f}")

    # Estadísticas por rango
    stats = stats_rangos(ops)
    reporte_separador("ESTADÍSTICAS POR RANGO")
    print(f"  {'RANGO':>8} {'OPS':>5} {'WR%':>7} {'SK':>4} {'DIR':>4} {'INV':>4}")
    print(f"  {'─'*35}")
    for r in RANGOS_ORDEN:
        if r in stats:
            d = stats[r]
            wr = d['aciertos'] / d['total'] * 100 if d['total'] else 0
            marca = " ✅" if wr >= 60 else (" ❌" if wr <= 35 else "")
            print(f"  {r:>8} {d['total']:>5} {wr:>6.1f}%{marca} {d['skips']:>4} {d['dir']:>4} {d['inv']:>4}")


def reporte_ep_combinado(ops: list):
    """Reporte de la simulación EP combinado."""
    res = simular_ep_combinado(ops)
    reporte_separador("SIMULACIÓN EP COMBINADO (histórico + ventana rolling 50)")
    print(f"  Total rondas:           {res['n_total']}")
    print(f"  Balance REAL:            {res['saldo_real']:+.2f}")
    print(f"  Balance SIMULADA EP:     {res['saldo_ep']:+.2f}")
    print(f"  Diferencia EP vs REAL:   {res['saldo_ep'] - res['saldo_real']:+.2f}")
    print(f"  Apuestas realizadas EP:  {res['n_bets']}")
    print(f"  Skips EP:                {res['n_skips']}")

    if res['n_total'] > 0:
        print(f"  % apuestas:              {res['n_bets']/res['n_total']*100:.1f}%")

    reporte_separador("VENTANA ROLLING POR RANGO+MODO")
    print(f"  {'RANGO':>8} {'MODO':>8} {'OPS':>4} {'WR%':>7}")
    print(f"  {'─'*32}")
    for rango in RANGOS_ORDEN:
        if rango in res['ventanas']:
            for modo in ['DIRECTO', 'INVERSO']:
                if modo in res['ventanas'][rango]:
                    dq = res['ventanas'][rango][modo]
                    n = len(dq)
                    if n > 0:
                        wr = sum(dq) / n * 100
                        marca = " ✅" if wr >= 53.2 else (" ⚠" if wr >= 50 else " ❌")
                        print(f"  {rango:>8} {modo:>8} {n:>4} {wr:>6.1f}%{marca}")


def reporte_ep_por_rango(ops: list):
    """Reporte de EP por rango+modo CON multiplicadores (rendimiento real)."""
    res = simular_ep_por_rango(ops)
    reporte_separador("EP POR RANGO+MODO (ventana rolling, CON multiplicadores)")
    print(f"  Balance REAL:            {res['saldo_real']:+.2f}")
    print(f"  Balance SIMULADA:        {res['saldo_ep']:+.2f}  ← rendimiento real")
    print(f"  Diferencia:              {res['saldo_ep'] - res['saldo_real']:+.2f}")
    print(f"  Apuestas:                {res['n_bets']}  ({res['n_bets']/max(res['n_total'],1)*100:.1f}%)")
    print(f"  Skips:                   {res['n_skips']}")
    print()

    # Detalle de multiplicadores
    mults = [d['mult'] for d in res['detalles'] if d['apostar']]
    if mults:
        print(f"  Multiplicadores usados:")
        print(f"    Media: {sum(mults)/len(mults):.1f}x  Max: {max(mults)}x  Min: {min(mults)}x")
        from collections import Counter
        dist = Counter(mults)
        print(f"    Distribución: {dict(sorted(dist.items()))}")

    # Últimas apuestas
    apuestas = [d for d in res['detalles'] if d['apostar']]
    print()
    print(f"  Últimas {min(10, len(apuestas))} apuestas EP:")
    for d in apuestas[-10:]:
        gano = d['ganada'] if d['modo'] == 'DIRECTO' else not d['ganada']
        marca = '✅' if gano else '❌'
        print(f"    {d['rango']:>6} {d['modo']:>8}  wr_v={d['wr_v']:5.1f}%  mult={d['mult']}x  {marca}")

    # WR por rango+modo (última ventana)
    if res['ventanas']:
        print()
        print(f"  WR ACTUAL POR RANGO+MODO (ventana):")
        for rango in RANGOS_ORDEN:
            if rango in res['ventanas']:
                for modo in ['DIRECTO', 'INVERSO']:
                    if modo in res['ventanas'][rango]:
                        dq = res['ventanas'][rango][modo]
                        n = len(dq)
                        if n >= EP_MIN_OPS:
                            wr = sum(dq) / n * 100
                            mult_actual = ep_mult(wr)
                            marca = " ✅" if wr >= EP_UMBRAL_ESTADO else ""
                            print(f"    {rango:>6} {modo:>8}: n={n:2d}  WR={wr:5.1f}%  mult={mult_actual}x{marca}")


def reporte_ep_rolling(ops: list):
    """Reporte de las variantes EP rolling."""
    reporte_separador("EP ADAPTATIVO (rolling global, ventana=20, umbral=53.2%)")
    res = simular_ep_rolling(ops)
    print(f"  Apuestas: {res['n_bets']} de {len(ops)} ({res['n_bets']/max(len(ops),1)*100:.1f}%)")
    print(f"  Aciertos: {res['n_ac']} ({res['n_ac']/max(res['n_bets'],1)*100:.1f}%)")
    print(f"  PNL:      {res['pnl']:+.2f}")

    res70 = simular_ep_rolling(ops, min_wr_dir=70)
    print()
    print("  EP + WR≥70:")
    print(f"    Apuestas: {res70['n_bets']}  Aciertos: {res70['n_ac']} ({res70['n_ac']/max(res70['n_bets'],1)*100:.1f}%)")
    print(f"    PNL:      {res70['pnl']:+.2f}")

    res70_inv = simular_ep_rolling(ops, min_wr_dir=70, contrarian=True)
    print()
    print("  EP + WR≥70 INV (contrarian):")
    print(f"    Apuestas: {res70_inv['n_bets']}  Aciertos: {res70_inv['n_ac']} ({res70_inv['n_ac']/max(res70_inv['n_bets'],1)*100:.1f}%)")
    print(f"    PNL:      {res70_inv['pnl']:+.2f}")


def reporte_umbrales(ops: list):
    """Prueba distintos umbrales y muestra resultados."""
    reporte_separador("COMPARATIVA POR UMBRAL")

    # Primero pre-calentar ventanas
    ventanas = {}
    for op in ops:
        rango = op['rango']
        modo = op['modo']
        ganada = op['ganada']
        ganada_modo = ganada if modo == 'DIRECTO' else not ganada
        if rango not in ventanas:
            ventanas[rango] = {}
        if modo not in ventanas[rango]:
            ventanas[rango][modo] = deque(maxlen=EP_VENTANA)
        ventanas[rango][modo].append(1 if ganada_modo else 0)

    print(f"  {'Umbral':>8} {'Apuestas':>9} {'Aciertos':>9} {'WR%':>6} {'PNL est.':>10}")
    print(f"  {'─'*46}")

    for umbral in [50.0, 51.0, 52.0, 53.2, 55.0, 56.0, 57.0, 58.0, 60.0, 62.0, 65.0]:
        n_ac = 0
        apuesta_count = 0
        pnl = 0.0
        for op in ops:
            rango = op['rango']
            modo = op['modo']
            ganada = op['ganada']
            ganada_modo = ganada if modo == 'DIRECTO' else not ganada
            v = ventanas.get(rango, {}).get(modo, deque())
            n_v = len(v)
            ac = sum(1 for x in v if x)
            wr_v = ac / n_v * 100 if n_v >= EP_MIN_OPS else 0
            if n_v >= EP_MIN_OPS and wr_v >= umbral:
                apuesta_count += 1
                if ganada_modo:
                    n_ac += 1
                    pnl += 0.9
                else:
                    pnl -= 1.0
        wr = n_ac / max(apuesta_count, 1) * 100
        print(f"  {umbral:>6.1f}%  {apuesta_count:>7}  {n_ac:>7}  {wr:>5.1f}%  {pnl:>+8.2f}")


def reporte_decisiones(ruta_dec: str | Path):
    """Reporte del histórico de decisiones."""
    decs = parsear_decision_history(ruta_dec)
    if not decs:
        print("\n  ⚠ No se encontró pnl_decision_history.json")
        return

    s = stats_decisiones(decs)

    reporte_separador("HISTÓRICO DE DECISIONES")
    print(f"  Total registros:       {s['total']}")
    print(f"  APOSTADAS:             {s['apostadas']}  ({s['apostadas']/max(s['total'],1)*100:.1f}%)")
    print(f"  SKIP/OBS:              {s['skips']}  ({s['skips']/max(s['total'],1)*100:.1f}%)")
    print()
    print(f"  ── APOSTADAS ──")
    print(f"    Aciertos: {s['ac_ap']}  Fallos: {s['fa_ap']}  WR: {s['wr_ap']:.1f}%")
    print(f"    PNL total: {s['pnl_ap']:+.2f}  PNL/op: {s['pnl_por_op']:+.4f}")
    print()
    print(f"  ── SKIP/OBS (hubieran acertado) ──")
    print(f"    Aciertos teóricos: {s['ac_sk']}  Fallos teóricos: {s['fa_sk']}  WR: {s['wr_sk']:.1f}%")
    print()
    print(f"  ── PNL POR MODO ──")
    for modo in ['BASE', 'DIRECTO', 'INVERSO', 'SKIP']:
        if modo in s['pnl_modo']:
            d = s['pnl_modo'][modo]
            wr = d['ac'] / max(d['ops'], 1) * 100
            print(f"    {modo:>8}: {d['ops']:3d} ops  {d['ac']:3d} ac.  {d['fa']:3d} fa.  "
                  f"WR={wr:5.1f}%  PNL={d['pnl']:+.2f}")

    # Últimas 30 apostadas
    apostadas = [d for d in decs if d.get('decision') == 'APOSTADA']
    ult30 = [d for d in apostadas[-30:] if d.get('acierto') is not None]
    if ult30:
        ac30 = sum(1 for d in ult30 if d.get('acierto') == True)
        print(f"\n  Últimas {len(ult30)} APOSTADAS: WR={ac30/max(len(ult30),1)*100:.1f}%")

    print()
    print(f"  ── BALANCES ──")
    print(f"    Balance REAL (base):     {s['balance_real']:+.2f}")
    print(f"    Balance FILTRO (activo): {s['balance_filtro']:+.2f}")
    print(f"    Diferencia filtro vs base: {s['balance_filtro'] - s['balance_real']:+.2f}")

    # EP Gate stats
    gates = Counter(d.get('ep_gate', '') for d in decs)
    print()
    print(f"  ── ESTADOS EP GATE (top 15) ──")
    for estado, count in gates.most_common(15):
        print(f"    {estado:>35}: {count:3d} ({count/max(len(decs),1)*100:5.1f}%)")


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Analizador de Estrategia Perfecta para PNL Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python3 analizar_ep.py                           # Análisis completo
  python3 analizar_ep.py --archivo pnl_live_history.json
  python3 analizar_ep.py --comparar                # Incluye comparación con decisiones
  python3 analizar_ep.py --umbrales                # Prueba distintos umbrales
  python3 analizar_ep.py --solo-rolling            # Solo EP rolling (no combinado)
  python3 analizar_ep.py --json reporte.json       # Exporta a JSON
        """,
    )

    grupo_archivo = parser.add_mutually_exclusive_group()
    grupo_archivo.add_argument(
        '--archivo', '-a',
        default='pnl_live_history.json',
        help='Archivo live history a analizar (default: pnl_live_history.json)',
    )
    grupo_archivo.add_argument(
        '--live', '-l',
        default='pnl_live_history.json',
        help=argparse.SUPPRESS,
    )

    parser.add_argument(
        '--comparar', '-c',
        action='store_true',
        help='Comparar con pnl_decision_history.json',
    )
    parser.add_argument(
        '--umbrales', '-u',
        action='store_true',
        help='Mostrar comparativa detallada de umbrales',
    )
    parser.add_argument(
        '--solo-rolling',
        action='store_true',
        help='Solo mostrar EP rolling (omitir EP combinado)',
    )
    parser.add_argument(
        '--json', '-j',
        type=str,
        help='Exportar resultados a archivo JSON',
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Modo silencioso (solo mostrar resumen)',
    )

    args = parser.parse_args()

    # ── Cargar datos ─────────────────────────────────────────────
    ruta_live = args.archivo
    if not os.path.exists(ruta_live):
        print(f"❌ Archivo no encontrado: {ruta_live}")
        sys.exit(1)

    ops = parsear_live_history(ruta_live)
    if not ops:
        print("❌ No se pudieron parsear operaciones del archivo.")
        sys.exit(1)

    # ── Reportes ─────────────────────────────────────────────────
    if args.quiet:
        # Modo resumen compacto
        n_ac = sum(1 for o in ops if o['acierto'])
        pnl_base = sum(0.9 if o['acierto'] else -1.0 for o in ops)
        res_comb = simular_ep_combinado(ops)
        res_roll = simular_ep_rolling(ops)
        res_rango = simular_ep_por_rango(ops)

        print(f"Resumen EP — {len(ops)} rondas")
        print(f"  BASE: {pnl_base:+.2f}  ({n_ac}/{len(ops)} = {n_ac/max(len(ops),1)*100:.1f}%)")
        print(f"  EP Combinado:      {res_comb['saldo_ep']:+.2f}  ({res_comb['n_bets']} ap.)")
        print(f"  EP Rolling:        {res_roll['pnl']:+.2f}  ({res_roll['n_bets']} ap.)")
        print(f"  EP × Rango (×mult): {res_rango['saldo_ep']:+.2f}  ({res_rango['n_bets']} ap.)  ← rendimiento real")
        mults = [d['mult'] for d in res_rango['detalles'] if d['apostar']]
        if mults:
            print(f"    Mult. medio: {sum(mults)/len(mults):.1f}x  max: {max(mults)}x")

        if args.comparar and os.path.exists('pnl_decision_history.json'):
            decs = parsear_decision_history('pnl_decision_history.json')
            s = stats_decisiones(decs)
            print(f"  Decisiones: {s['apostadas']} ap. WR={s['wr_ap']:.1f}% PNL={s['pnl_ap']:+.2f}")

        return

    # Modo completo
    print()
    print("╔" + "═" * 68 + "╗")
    print("║            ANALIZADOR DE ESTRATEGIA PERFECTA — LIVE DATA            ║")
    print("╚" + "═" * 68 + "╝")
    print(f"\n  Archivo: {os.path.abspath(ruta_live)}")
    print(f"  Rondas:  {len(ops)}")

    reporte_general(ops)

    if not args.solo_rolling:
        try:
            reporte_ep_combinado(ops)
        except Exception as e:
            print(f"\n  ⚠ Error en EP combinado: {e}")

    reporte_ep_rolling(ops)

    if not args.solo_rolling:
        try:
            reporte_ep_por_rango(ops)
        except Exception as e:
            print(f"\n  ⚠ Error en EP por rango: {e}")

    if args.umbrales:
        reporte_umbrales(ops)

    if args.comparar:
        ruta_dec = 'pnl_decision_history.json'
        if os.path.exists(ruta_dec):
            reporte_decisiones(ruta_dec)
        else:
            print(f"\n  ⚠ Archivo de decisiones no encontrado: {ruta_dec}")
            print("     Usa --comparar solo cuando exista pnl_decision_history.json")

    # ── Exportar JSON ────────────────────────────────────────────
    if args.json:
        resultado = {
            'total_rondas': len(ops),
            'base': {
                'aciertos': sum(1 for o in ops if o['acierto']),
                'total': len(ops),
                'pnl': sum(0.9 if o['acierto'] else -1.0 for o in ops),
            },
            'ep_combinado': simular_ep_combinado(ops),
            'ep_rolling': simular_ep_rolling(ops),
            'ep_rolling_wr70': simular_ep_rolling(ops, min_wr_dir=70),
        }

        # Limpiar datos no serializables
        if 'ventanas' in resultado['ep_combinado']:
            v_serializable = {}
            for r, modos in resultado['ep_combinado']['ventanas'].items():
                v_serializable[r] = {}
                for m, dq in modos.items():
                    v_serializable[r][m] = list(dq)
            resultado['ep_combinado']['ventanas'] = v_serializable

        Path(args.json).write_text(json.dumps(resultado, indent=2, ensure_ascii=False))
        print(f"\n  ✅ Reporte exportado a: {args.json}")

    print()
    print("╔" + "═" * 68 + "╗")
    print("║                           FIN DEL ANÁLISIS                        ║")
    print("╚" + "═" * 68 + "╝")
    print()


if __name__ == '__main__':
    main()
