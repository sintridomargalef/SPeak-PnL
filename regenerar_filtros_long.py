#!/usr/bin/env python3
"""
Regenera pnl_filtros_long.jsonl desde pnl_decision_history.json.

Por cada decisión con winner, escribe N líneas (una por filtro de FILTROS_CURVA)
con: delta, saldo acumulado, datos de contexto.

Útil si:
- Has aplicado backfill_pnl_filtros.py y quieres re-generar el long file
- Has restaurado un backup antiguo y necesitas reconstruir el long file
- El long file se ha corrompido o borrado
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from pnl_config import FILTROS_CURVA, DECISION_HIST_FILE, FILTROS_LONG_FILE


def main():
    if not DECISION_HIST_FILE.exists():
        print(f"[REGEN] No existe {DECISION_HIST_FILE.name} — nada que regenerar.")
        return

    # Backup del long file si existe
    if FILTROS_LONG_FILE.exists():
        backup_dir = Path(__file__).parent / 'backups'
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f'pre_regen_filtros_long_{datetime.now():%Y%m%d_%H%M%S}.jsonl'
        shutil.copy(FILTROS_LONG_FILE, backup)
        print(f"[REGEN] Backup long file → {backup.name}")

    decs = json.loads(DECISION_HIST_FILE.read_text(encoding='utf-8'))
    print(f"[REGEN] {len(decs)} decisiones cargadas")

    saldos: dict = {}
    lineas: list = []
    procesadas = 0

    for d in decs:
        if not d.get('winner'):
            continue
        pnl_f = d.get('pnl_filtros') or {}
        for i, entry in enumerate(FILTROS_CURVA):
            nombre, color = entry[0], entry[1]
            delta = pnl_f.get(str(i))
            if delta is None:
                delta = pnl_f.get(i)
            if delta is None:
                delta = 0.0
            try:
                delta = float(delta)
            except Exception:
                delta = 0.0
            saldos[i] = round(saldos.get(i, 0.0) + delta, 4)
            # ── Copia COMPLETA de la decisión original ──
            entrada = dict(d)
            entrada.pop('pnl_filtros', None)
            entrada['filtro_idx']    = i
            entrada['filtro_nombre'] = nombre
            entrada['filtro_color']  = color
            entrada['delta']         = round(delta, 4)
            entrada['saldo']         = saldos[i]
            lineas.append(entrada)
        procesadas += 1

    # Escritura completa (truncar y reescribir)
    with FILTROS_LONG_FILE.open('w', encoding='utf-8') as f:
        for e in lineas:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')

    print(f"[REGEN] ✅ {procesadas} decisiones → {len(lineas)} líneas escritas en {FILTROS_LONG_FILE.name}")
    print(f"[REGEN] Saldos finales por filtro:")
    for i in sorted(saldos.keys()):
        nombre = FILTROS_CURVA[i][0]
        print(f"  #{i:2}  {nombre[:24]:<24}  {saldos[i]:+8.2f}")


if __name__ == '__main__':
    main()
