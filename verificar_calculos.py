"""
SPeak - Verificador de calculos historicos
============================================
Uso:  py verificar_calculos.py
Lee pnl_decision_history.json y pnl_filtros_long.jsonl
y muestra el calculo detallado de cada ronda para el filtro elegido.

Cambiar FILTRO_IDX abajo para analizar otro filtro.
  0=Base, 1=Solo DIRECTO, 2=Solo INVERSO, 3=DIR WR>=70%, 4=DIR WR>=80%...
"""

import json
from pathlib import Path

# ── CONFIG ─────────────────────────────────────────────────────
FILTRO_IDX = 4          # 4 = DIR WR>=80%
MAX_RONDAS = 30         # ultimas N rondas a mostrar
# ───────────────────────────────────────────────────────────────

FILTROS = [
    "Base (todo)", "Solo DIRECTO", "Solo INVERSO",
    "DIR WR>=70%", "DIR WR>=80%", "DIR sin +50",
    "DIR ESTABLE", "DIR VOLATIL", "DIR |acel|<10",
    "DIR WR>=70 sin+50", "CONTRA TOTAL", "MAYORIA PERDEDORA",
    "CONTRA ESTABLE", "EP ADAPTATIVO", "EP + WR>=70",
    "EP + WR>=70 INV", "EP UMBRAL", "BAL.FILTRO",
]

RUTA_HIST = Path(__file__).parent / 'pnl_decision_history.json'
RUTA_LONG = Path(__file__).parent / 'pnl_filtros_long.jsonl'


def main():
    nombre = FILTROS[FILTRO_IDX] if FILTRO_IDX < len(FILTROS) else f"Filtro#{FILTRO_IDX}"
    print(f"{'='*70}")
    print(f"  ANALISIS: {nombre} (idx {FILTRO_IDX})")
    print(f"  Fuente: {RUTA_HIST.name} + {RUTA_LONG.name}")
    print(f"{'='*70}")

    if not RUTA_HIST.exists():
        print(f"\nERROR: No se encuentra {RUTA_HIST}")
        return
    if not RUTA_LONG.exists():
        print(f"\nERROR: No se encuentra {RUTA_LONG}")
        return

    # Cargar long file y filtrar por filtro
    entradas = []
    with open(RUTA_LONG, 'r', encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                r = json.loads(linea)
            except:
                continue
            if r.get('filtro_idx') == FILTRO_IDX:
                entradas.append(r)

    if not entradas:
        print(f"\n  No hay entradas para el filtro {FILTRO_IDX}")
        return

    print(f"\n  {len(entradas)} entradas en long file")
    print(f"  Ultimas {min(MAX_RONDAS, len(entradas))} rondas:\n")

    total_acum = 0.0
    for r in entradas[-MAX_RONDAS:]:
        iss = r.get('issue', '?')
        dec = r.get('decision', '?')
        modo = r.get('modo', '?')
        wr = r.get('wr', '?')
        mayor = r.get('mayor', '')
        winner = r.get('winner', '')
        acierto = r.get('acierto')
        pnl = r.get('pnl')
        delta = r.get('delta', 0)
        saldo = r.get('saldo', 0)
        factor = float(r.get('apuesta', 0.1)) * float(r.get('mult', 1))

        # Mostrar detalle
        print(f"  Ronda: {iss[-4:]}")
        print(f"    Decision: {dec}")
        print(f"    Modo: {modo}  WR: {wr}%")
        print(f"    Mayor: {mayor}  Winner: {winner}")
        print(f"    Acierto: {acierto}  PnL real: {pnl}")
        print(f"    Factor (apuesta*mult): {factor:.2f}")
        
        # Explicar delta teorico
        gano_mayoria = (winner or '').lower() == (mayor or '').lower() if winner and mayor else None
        print(f"    Gano mayoria: {gano_mayoria}")
        
        if dec != 'APOSTADA':
            print(f"    Delta teorico: 0.0 (no APOSTADA)")
        elif modo == 'SKIP' and 'CONTRAR' not in nombre and 'MAYOR' not in nombre.upper():
            print(f"    Delta teorico: 0.0 (modo SKIP, filtro no apuesta)")
        elif gano_mayoria is None:
            print(f"    Delta teorico: 0.0 (sin winner/mayor)")
        else:
            print(f"    Delta teorico: {delta:+.2f} (del long file)")
        
        print(f"    Saldo acumulado: {saldo:+.2f}")
        print()

    # Resumen
    print(f"  Saldo inicial (balance_inicio): 0.00")
    if entradas:
        print(f"  Saldo final (ultimo long file): {entradas[-1].get('saldo', 0):+.2f}")
    
    # Mostrar transiciones donde delta != 0
    print(f"\n  Rondas donde delta != 0 (el filtro aposto):")
    print(f"  {'Ronda':>12} {'Delta':>6} {'Saldo':>6} {'Real PnL':>8} {'Modo':>8}")
    print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*8} {'-'*8}")
    for r in entradas[-MAX_RONDAS:]:
        delta = float(r.get('delta', 0))
        if delta != 0:
            print(f"  {r.get('issue','?')[-4:]:>12} {delta:>+6.2f} {float(r.get('saldo',0)):>+6.2f} {float(r.get('pnl',0)):>+8.2f} {str(r.get('modo','')):>8}")


if __name__ == '__main__':
    main()
