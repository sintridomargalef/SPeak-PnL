#!/usr/bin/env python3
"""
Recalcula pnl_filtros en todos los registros de pnl_decision_history.json
usando la misma lógica que _delta_teorico del dashboard.

Útil para asegurar que registros antiguos tengan pnl_filtros completos
(no plano en OBS/SKIP) tras el fix de _calcular_pnl_filtros.

Genera backup automático antes de tocar el JSON.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from pnl_config import FILTROS_CURVA, DECISION_HIST_FILE


def _delta_teorico(d: dict, i: int) -> float:
    """Delta del filtro i para la ronda d, respetando:
    - decision == APOSTADA (si SKIP/OBS → 0)
    - lambda específica del filtro
    - Base (idx 0) usa raw + ignora multiplicador
    Misma lógica que PnlDashboard._delta_teorico (post-fix)."""
    if d.get('decision') != 'APOSTADA':
        return 0.0
    winner = (d.get('winner') or '').lower()
    mayor  = (d.get('mayor')  or '').lower()
    if not winner or not mayor:
        return 0.0
    try:
        nombre, _, fn, contrarian, raw = FILTROS_CURVA[i]
    except Exception:
        return 0.0
    if fn is None or isinstance(fn, str):
        return 0.0
    modo = d.get('modo', 'SKIP')
    wr_d = float(d.get('wr') or 50)
    if modo == 'BASE':
        modo = 'DIRECTO' if wr_d >= 60 else ('INVERSO' if wr_d <= 40 else 'SKIP')
    gano_mayoria = (winner == mayor)
    op = {
        'skip':         modo == 'SKIP',
        'acierto':      bool(d.get('acierto', False)),
        'gano_mayoria': gano_mayoria,
        'modo':         modo,
        'rango':        d.get('rango', '?'),
        'est':          d.get('est', 'ESTABLE'),
        'acel':         float(d.get('acel') or 0),
        'wr':           wr_d,
        'mult':         float(d.get('mult') or 1),
    }
    if not raw:
        try:
            if not fn(op):
                return 0.0
        except Exception:
            return 0.0
        if op['skip']:
            return 0.0
    apuesta = float(d.get('apuesta') or 1)
    mult    = float(d.get('mult') or 1)
    factor  = apuesta if (i == 0 or raw) else apuesta * mult
    # Dirección igual que _calcular_pnl_filtros: solo según resultado objetivo
    if raw:
        gano = op['gano_mayoria']
    elif 'INVERSO' in nombre.upper():
        gano = not op['gano_mayoria']
    elif op['modo'] == 'INVERSO':
        gano = not op['gano_mayoria']
    else:
        gano = op['gano_mayoria']
    if contrarian:
        gano = not gano
    return round(0.9 * factor if gano else -1.0 * factor, 2)


def main():
    if not DECISION_HIST_FILE.exists():
        print(f"[BACKFILL] No existe {DECISION_HIST_FILE.name} — nada que hacer.")
        return

    backup_dir = Path(__file__).parent / 'backups'
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / f'pre_backfill_pnl_filtros_{datetime.now():%Y%m%d_%H%M%S}.json'
    shutil.copy(DECISION_HIST_FILE, backup)
    print(f"[BACKFILL] Backup → {backup.name}")

    decs = json.loads(DECISION_HIST_FILE.read_text(encoding='utf-8'))
    print(f"[BACKFILL] {len(decs)} registros cargados")

    recalculados = 0
    sin_winner   = 0

    for d in decs:
        if not d.get('winner'):
            sin_winner += 1
            continue
        # Recalcular pnl_base: solo si APOSTADA, sin multiplicador (Base no usa mult)
        if d.get('decision') == 'APOSTADA':
            winner = (d.get('winner') or '').lower()
            mayor  = (d.get('mayor') or '').lower()
            if winner and mayor:
                factor = float(d.get('apuesta') or 1)   # Base ignora mult
                gano   = (winner == mayor)
                d['pnl_base'] = round(0.9 * factor if gano else -1.0 * factor, 2)
            else:
                d['pnl_base'] = 0.0
        else:
            d['pnl_base'] = 0.0
        # Recalcular pnl_filtros para todos (idx 0 incluido)
        pf = {}
        for i in range(len(FILTROS_CURVA)):
            pf[str(i)] = _delta_teorico(d, i)
        d['pnl_filtros'] = pf
        recalculados += 1

    DECISION_HIST_FILE.write_text(
        json.dumps(decs, ensure_ascii=False, indent=2),
        encoding='utf-8')

    print(f"[BACKFILL] ✅ {recalculados} registros recalculados, "
          f"{sin_winner} sin winner (saltados)")


if __name__ == '__main__':
    main()
