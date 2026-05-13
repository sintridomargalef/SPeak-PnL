"""
Backfill: recalcula balance_filtro en pnl_decision_history.json usando
pnl_filtros[activo_idx] en vez de d['pnl'] (fix de balance_filtro).
El balance por filtro se trackea independientemente (igual que la lógica live).
"""
import json
from pathlib import Path
from pnl_config import FILTROS_CURVA, DECISION_HIST_FILE

DEC = Path(DECISION_HIST_FILE)
ts = __import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')
backup = DEC.with_stem(DEC.stem + f"_bak_{ts}")

with open(DEC, 'r', encoding='utf-8') as f:
    decisions = json.load(f)

# Backup
with open(backup, 'w', encoding='utf-8') as f:
    json.dump(decisions, f, ensure_ascii=False)

balances = {}
contador = 0

for d in decisions:
    filtro = d.get('filtro', '')
    decision = d.get('decision', '')
    pf = d.get('pnl_filtros') or {}

    actual_bal = balances.get(filtro, 0.0)

    idx = None
    try:
        idx = next(i for i, e in enumerate(FILTROS_CURVA) if e[0] == filtro)
    except StopIteration:
        pass

    delta = 0.0
    if decision == 'APOSTADA' and idx is not None:
        teorico = pf.get(str(idx))
        if teorico is None:
            teorico = pf.get(idx)
        if teorico is not None:
            delta = float(teorico)

    if d.get('winner') is not None:
        actual_bal = round(actual_bal + delta, 2)
        balances[filtro] = actual_bal

    old_bal = d.get('balance_filtro')
    if old_bal is None or abs(old_bal - actual_bal) > 0.001:
        contador += 1
    d['balance_filtro'] = actual_bal

DEC.write_text(json.dumps(decisions, ensure_ascii=False), encoding='utf-8')

print(f"Backfill: {contador} registros corregidos de {len(decisions)} totales")
print(f"Backup: {backup}")