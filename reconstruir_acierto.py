"""
Script one-shot para reconstruir el campo 'acierto' en pnl_decision_history.json.
Donde acierto es null pero winner y mayor existen → acierto = (winner == mayor).
Genera backup antes de escribir.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

RUTA = Path(__file__).parent / 'pnl_decision_history.json'
BACKUP = Path(__file__).parent / 'backups' / f'pnl_decision_history_pre_reconstruir_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'

def main():
    if not RUTA.exists():
        print(f"❌ No existe {RUTA}")
        return

    BACKUP.parent.mkdir(exist_ok=True)
    shutil.copy(RUTA, BACKUP)
    print(f"✅ Backup → {BACKUP.name}")

    decs = json.loads(RUTA.read_text(encoding='utf-8'))
    print(f"📂 {len(decs)} registros cargados")

    reconstruidos = 0
    sin_datos    = 0
    ya_tenian    = 0

    for d in decs:
        if d.get('acierto') is not None:
            ya_tenian += 1
            continue
        winner = d.get('winner')
        mayor  = d.get('mayor')
        if not winner or not mayor:
            sin_datos += 1
            continue
        d['acierto'] = (str(winner).lower() == str(mayor).lower())
        reconstruidos += 1

    RUTA.write_text(json.dumps(decs, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f"\n📊 Resumen:")
    print(f"  Ya tenían acierto: {ya_tenian}")
    print(f"  Reconstruidos:     {reconstruidos}")
    print(f"  Sin datos (irrec): {sin_datos}")
    print(f"  Total:             {len(decs)}")
    print(f"\n✅ Guardado en {RUTA.name}")

if __name__ == '__main__':
    main()
