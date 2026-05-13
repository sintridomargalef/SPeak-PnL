"""Validación del cálculo de EP ADAPTATIVO (idx 13) y EP UMBRAL (idx 16).

Compara los saldos registrados en pnl_filtros_long.jsonl con la curva
regenerada usando las funciones de pnl_data y verifica que el WR resulte
plausible (no inflado por look-ahead o fallback contaminado).
"""
import json
from pathlib import Path
from pnl_data import curva_pnl_ep, curva_pnl_umbral

LONG_FILE = Path(__file__).parent / 'pnl_filtros_long.jsonl'


def _gano_mayoria(d):
    w = (d.get('winner') or '').lower()
    m = (d.get('mayor') or '').lower()
    wn = 'AZUL' if 'blue' in w or 'azul' in w else ('ROJO' if 'red' in w or 'rojo' in w else '')
    mn = 'AZUL' if 'blue' in m or 'azul' in m else ('ROJO' if 'red' in m or 'rojo' in m else '')
    if not wn or not mn:
        return None
    return wn == mn


def _evaluar(nombre, n_bets, n_ac, saldo_calc, saldo_real):
    wr = (n_ac / n_bets * 100) if n_bets else 0.0
    print(f"\n=== {nombre} ===")
    print(f"[REAL] suma delta long.jsonl:  {saldo_real:+.4f} EUR")
    print(f"[CALC] apuestas / aciertos:    {n_bets} / {n_ac}")
    print(f"[CALC] WR:                     {wr:.1f}%")
    print(f"[CALC] saldo final:            {saldo_calc:+.4f} EUR")
    print(f"[DIFF] real - calc:            {saldo_real - saldo_calc:+.4f} EUR")
    if n_bets == 0:
        print("[WARN] n_bets=0: warmup no completado o todas en zona neutra.")
    elif abs(wr - 94) < 2:
        print("[FAIL] WR ~94% - bug NO resuelto.")
    elif 40 <= wr <= 70:
        print("[OK] WR en rango plausible (40-70 %).")
    else:
        print(f"[WARN] WR fuera del rango plausible: {wr:.1f}%")


def main():
    if not LONG_FILE.exists():
        print(f"No existe {LONG_FILE}")
        return

    saldos_real = {13: 0.0, 16: 0.0}
    n_filas = {13: 0, 16: 0}
    ops = []
    seen = set()
    for linea in LONG_FILE.read_text(encoding='utf-8').splitlines():
        if not linea.strip():
            continue
        try:
            d = json.loads(linea)
        except Exception:
            continue
        idx = d.get('filtro_idx')
        if idx in saldos_real:
            saldos_real[idx] += float(d.get('delta') or 0.0)
            n_filas[idx] += 1
        if idx == 0:
            key = (d.get('issue'), d.get('timestamp'))
            if key in seen:
                continue
            seen.add(key)
            modo = d.get('modo', 'SKIP')
            wr = float(d.get('wr') or 50)
            if modo == 'BASE':
                modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
            gm = _gano_mayoria(d)
            decision = d.get('decision', 'SKIP')
            skip = (modo == 'SKIP') or (decision != 'APOSTADA')
            ops.append({
                'skip': skip,
                'acierto': bool(d.get('acierto', False)),
                'gano_mayoria': gm,
                'modo': modo,
                'rango': d.get('rango', '?'),
                'est': d.get('est', 'ESTABLE'),
                'acel': float(d.get('acel') or 0),
                'wr': wr,
                'mult': float(d.get('mult') or 1),
            })

    print(f"Ops reconstruidas: {len(ops)}")

    # EP ADAPTATIVO (idx 13) - curva_pnl_ep
    curva, n_ac, n_bets, _ = curva_pnl_ep(ops)
    _evaluar("EP ADAPTATIVO (idx 13)", n_bets, n_ac, curva[-1] if curva else 0.0, saldos_real[13])

    # EP UMBRAL (idx 16) - curva_pnl_umbral con los params usados en _calcular_curva
    curva_u, n_ac_u, n_bets_u, _ = curva_pnl_umbral(
        ops, umbral=62.0, min_ops=5, ops_hist=None, mult_maximo=5,
        adaptativo=True, ventana_regimen=30, warmup=10,
        umbral_alto=0.55, umbral_bajo=0.50)
    _evaluar("EP UMBRAL (idx 16)", n_bets_u, n_ac_u,
             curva_u[-1] if curva_u else 0.0, saldos_real[16])


if __name__ == '__main__':
    main()
