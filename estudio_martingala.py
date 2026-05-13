"""
estudio_martingala.py — Estudio de Martingala sobre cada filtro del historial.

Simula la progresion Martingale (doblar tras perdida, reset tras acierto)
para cada uno de los 18 filtros registrados en pnl_filtros_long.jsonl.

Uso:
    python estudio_martingala.py                          # solo consola
    python estudio_martingala.py --cap N                  # max N dobles seguidas
    python estudio_martingala.py --base 1.0               # apuesta base 1.0
    python estudio_martingala.py --grafico                # muestra grafica comparativa
    python estudio_martingala.py --export resultados.json # exportar a JSON
"""
import json
import sys
import argparse
from pathlib import Path

FILTROS = [
    "Base (todo)", "Solo DIRECTO", "Solo INVERSO",
    "DIR WR>=70%", "DIR WR>=80%", "DIR sin +50",
    "DIR ESTABLE", "DIR VOLATIL", "DIR |acel|<10",
    "DIR WR>=70 sin+50",
    "CONTRA TOTAL", "MAYORIA PERDEDORA", "CONTRA ESTABLE",
    "EP ADAPTATIVO", "EP + WR>=70", "EP + WR>=70 INV",
    "EP UMBRAL", "BAL.FILTRO",
]

FILTRO_COLORS = [
    '#4A6080', '#00FF88', '#FF3366', '#00D4FF', '#FFB800',
    '#2B7FFF', '#8B5CF6', '#F97316', '#EC4899', '#06B6D4',
    '#FF6B35', '#C084FC', '#F59E0B', '#FFD700', '#00FFFF',
    '#FF00FF', '#FF8C00', '#E8F4FF',
]

LONGFILE = Path(__file__).parent / 'pnl_filtros_long.jsonl'

PNL_ACIERTO_RATIO = 0.9
PNL_FALLO_RATIO = -1.0


def _habilitar_utf8():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass


# ── ANSI cyberpunk colors ─────────────────────────────────────────────
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
    'mg': '\033[38;2;139;92;246m',
    'cya': '\033[38;2;0;212;255m',
}


def _sec(titulo):
    dashes = '\u2500' * max(1, 70 - 5 - len(titulo))
    return (f'\n{_A["cy"]}  \u25c8{_A["R"]} {_A["B"]}{_A["cy"]}'
            f'{titulo}{_A["R"]} {_A["dm"]}{dashes}{_A["R"]}')

def _barra(pct, ancho=30, col='gn'):
    n = min(ancho, max(0, round(pct / 100 * ancho)))
    return _A[col] + '\u2588' * n + _A['dm'] + '\u2591' * (ancho - n) + _A['R']

def _pnl_str(v):
    if v > 0:
        return f'{_A["gn"]}{_A["B"]}+{v:.2f}{_A["R"]}'
    elif v < 0:
        return f'{_A["rd"]}{_A["B"]}{v:.2f}{_A["R"]}'
    return f'{_A["mu"]}  0.00{_A["R"]}'


def _extraer_delta(op):
    """Obtiene el PnL real por filtro desde el campo 'delta'.
    delta > 0  → acierto
    delta < 0  → fallo
    delta == 0 → no aposto (skip)
    """
    return float(op.get('delta', op.get('pnl', 0)))


MULT_TABLE = [1, 3, 9, 27, 81, 243, 729, 2187]


def mult_martingala(dobles):
    if dobles < len(MULT_TABLE):
        return MULT_TABLE[dobles]
    return MULT_TABLE[-1] * (3 ** (dobles - len(MULT_TABLE) + 1))


MULT_TABLE = [1, 3, 9, 27, 81, 243, 729, 2187]


def mult_martingala(dobles):
    if dobles < len(MULT_TABLE):
        return MULT_TABLE[dobles]
    return MULT_TABLE[-1] * (3 ** (dobles - len(MULT_TABLE) + 1))


def simular_martingala(ops, base_bet=0.1, max_dobles=None):
    curve = [0.0]
    current_bet = base_bet
    max_bet = base_bet
    drawdown = 0.0
    peak = 0.0
    longest_loss_streak = 0
    current_loss_streak = 0
    n_bets = 0
    n_wins = 0
    n_losses = 0
    n_skips = 0
    n_dobles_reales = 0

    for op in ops:
        delta = _extraer_delta(op)

        if delta == 0:
            curve.append(curve[-1])
            n_skips += 1
            continue

        n_bets += 1
        if delta > 0:
            pnl_martingala = round(current_bet * PNL_ACIERTO_RATIO, 2)
            current_bet = base_bet
            n_wins += 1
            current_loss_streak = 0
        else:
            pnl_martingala = round(current_bet * PNL_FALLO_RATIO, 2)
            n_losses += 1
            current_loss_streak += 1
            longest_loss_streak = max(longest_loss_streak, current_loss_streak)
            n_dobles_reales = max(n_dobles_reales, current_loss_streak)
            if max_dobles is not None and current_loss_streak > max_dobles:
                current_bet = base_bet
                current_loss_streak = 0
            else:
                current_bet = round(base_bet * mult_martingala(current_loss_streak), 2)
                max_bet = max(max_bet, current_bet)

        new_balance = round(curve[-1] + pnl_martingala, 2)
        curve.append(new_balance)
        peak = max(peak, new_balance)
        drawdown = min(drawdown, new_balance - peak)

    final_balance = curve[-1] if curve else 0.0
    win_rate = (n_wins / n_bets * 100) if n_bets else 0.0

    return {
        'curve': curve,
        'n_bets': n_bets,
        'n_skips': n_skips,
        'n_total': n_bets + n_skips,
        'n_wins': n_wins,
        'n_losses': n_losses,
        'win_rate': win_rate,
        'final_balance': final_balance,
        'max_bet': max_bet,
        'max_drawdown': round(drawdown, 2),
        'longest_loss_streak': longest_loss_streak,
        'max_dobles_alcanzado': n_dobles_reales,
    }


def simular_real(ops):
    """Calcula PnL real a partir de 'delta' por filtro."""
    curve = [0.0]
    n_bets = 0
    n_wins = 0
    n_losses = 0
    drawdown = 0.0
    peak = 0.0

    for op in ops:
        delta = _extraer_delta(op)
        if delta == 0:
            curve.append(curve[-1])
            continue
        n_bets += 1
        if delta > 0:
            n_wins += 1
        else:
            n_losses += 1
        new_bal = round(curve[-1] + delta, 2)
        curve.append(new_bal)
        peak = max(peak, new_bal)
        drawdown = min(drawdown, new_bal - peak)

    win_rate = (n_wins / n_bets * 100) if n_bets else 0.0
    return {
        'curve': curve,
        'n_bets': n_bets,
        'n_wins': n_wins,
        'n_losses': n_losses,
        'win_rate': win_rate,
        'final_balance': curve[-1],
        'max_drawdown': round(drawdown, 2),
    }


def _pnl_col(v, width):
    """Retorna string coloreado de PnL con ancho fijo (sin ANSI)."""
    if v > 0:
        col = _A['gn'] + _A['B']
        txt = f'+{v:.2f}'
    elif v < 0:
        col = _A['rd'] + _A['B']
        txt = f'{v:.2f}'
    else:
        col = _A['mu']
        txt = ' 0.00'
    return col + txt.rjust(width) + _A['R']


def imprimir_tabla(resultados, titulo="MARTINGALA"):
    """Imprime tabla comparativa de resultados."""
    print(_sec(f"{titulo} — Resultados por filtro"))
    print()

    header = (
        f'  {_A["cy"]}{"#":>2}{_A["R"]} '
        f'{_A["mu"]}{"Filtro":<22}{_A["R"]} '
        f'{_A["mu"]}{"Apuestas":>8}{_A["R"]} '
        f'{_A["mu"]}{"WR":>6}{_A["R"]} '
        f'{_A["mu"]}{"Real":>9}{_A["R"]} '
        f'{_A["mu"]}{"Martingala":>12}{_A["R"]} '
        f'{_A["mu"]}{"Diff":>9}{_A["R"]} '
        f'{_A["mu"]}{"MaxBet":>7}{_A["R"]} '
        f'{_A["mu"]}{"DD":>8}{_A["R"]} '
        f'{_A["mu"]}{"Racha":>6}{_A["R"]}'
    )
    sep_line = '  ' + _A['dm'] + '\u2500' * 98 + _A['R']
    print(header)
    print(sep_line)

    for fi in sorted(resultados.keys()):
        r = resultados[fi]
        name = (FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}')[:22]
        diff = r['mart_final'] - r['real_final']

        print(
            f'  {_A["cy"]}{fi:>2d}{_A["R"]} '
            f'{_A["tx"]}{name:<22}{_A["R"]} '
            f'{_A["tx"]}{r["n_bets"]:>8}{_A["R"]} '
            f'{_A["tx"]}{r["win_rate"]:>5.1f}%{_A["R"]} '
            f'{_pnl_col(r["real_final"], 9)} '
            f'{_pnl_col(r["mart_final"], 12)} '
            f'{_pnl_col(diff, 9)} '
            f'{_A["am"]}{r["max_bet"]:>7.2f}{_A["R"]} '
            f'{_pnl_col(r["max_drawdown"], 8)} '
            f'{_A["rd"]}{r["longest_loss"]:>4}{_A["R"]}'
        )

    print(sep_line)
    print()


def _nombre_fi(fi):
    return FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}'


def imprimir_resumen(resultados):
    """Imprime resumen comparativo por filtro (sin sumas entre filtros)."""
    print(_sec("RESUMEN"))
    print()

    # Filtrar los que tienen >= 10 apuestas (datos significativos)
    activos = {fi: r for fi, r in resultados.items() if r['n_bets'] >= 10}
    if not activos:
        activos = resultados

    # Ordenar por Martingale (mejores primero)
    orden = sorted(activos.items(), key=lambda x: x[1]['mart_final'], reverse=True)

    n_pos = sum(1 for _, r in orden if r['mart_final'] > 0)
    n_neg = sum(1 for _, r in orden if r['mart_final'] < 0)
    total = len(orden)
    mejora = sum(1 for _, r in orden if r['mart_final'] > r['real_final'])

    print(f'  {_A["mu"]}Filtros con Martingale positiva:{_A["R"]}  {_A["gn"]}{n_pos}{_A["R"]} {_A["mu"]}de{_A["R"]} {total}')
    print(f'  {_A["mu"]}Filtros con Martingale negativa:{_A["R"]}  {_A["rd"]}{n_neg}{_A["R"]} {_A["mu"]}de{_A["R"]} {total}')
    print(f'  {_A["mu"]}Martingale mejor que Real:{_A["R"]}     {_A["gn"]}{mejora}{_A["R"]} {_A["mu"]}de{_A["R"]} {total}')
    print()

    # Mini-ranking: mejores y peores
    header = (
        f'  {_A["mu"]}{"Filtro":<22}{_A["R"]} '
        f'{_A["mu"]}{"Bets":>5}{_A["R"]} '
        f'{_A["mu"]}{"WR":>5}{_A["R"]} '
        f'{_A["mu"]}{"Real":>9}{_A["R"]} '
        f'{_A["mu"]}{"Mart":>9}{_A["R"]} '
        f'{_A["mu"]}{"MaxBet":>7}{_A["R"]} '
        f'{_A["mu"]}{"Racha":>6}{_A["R"]}'
    )

    def _print_rank_fila(fi, r):
        name = _nombre_fi(fi)[:22]
        print(
            f'  {_A["tx"]}{name:<22}{_A["R"]} '
            f'{_A["tx"]}{r["n_bets"]:>5}{_A["R"]} '
            f'{_A["tx"]}{r["win_rate"]:>4.1f}%{_A["R"]} '
            f'{_pnl_col(r["real_final"], 9)} '
            f'{_pnl_col(r["mart_final"], 9)} '
            f'{_A["am"]}{r["max_bet"]:>7.2f}{_A["R"]} '
            f'{_A["rd"]}{r["longest_loss"]:>4}{_A["R"]}'
        )

    print(f'  {_A["cy"]}{_A["B"]}TOP 5 MARTINGALA{_A["R"]}')
    print(header)
    sep_line = '  ' + _A['dm'] + '\u2500' * 69 + _A['R']
    print(sep_line)
    for fi, r in orden[:5]:
        _print_rank_fila(fi, r)
    print(sep_line)
    print()

    print(f'  {_A["cy"]}{_A["B"]}PEORES 5 MARTINGALA{_A["R"]}')
    print(header)
    print(sep_line)
    for fi, r in reversed(orden[-5:]):
        _print_rank_fila(fi, r)
    print(sep_line)
    print()


def _graficar(curvas_reales, curvas_mart, resultados, max_dobles=None):
    """Genera grafica comparativa con matplotlib."""
    import matplotlib
    matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec

    # Ordenar filtros por balance final de martingala (mejores primero)
    sorted_filters = sorted(
        [fi for fi in curvas_mart.keys()],
        key=lambda fi: resultados[fi]['mart_final'],
        reverse=True
    )

    n = len(sorted_filters)
    cols = 3
    rows = (n + cols - 1) // cols

    fig = plt.figure(figsize=(18, 5 * rows), facecolor='#050A14')
    gs = gridspec.GridSpec(rows, cols, figure=fig, hspace=0.4, wspace=0.35)

    for idx, fi in enumerate(sorted_filters):
        ax = fig.add_subplot(gs[idx])
        ax.set_facecolor('#0A1628')

        real_c = curvas_reales[fi]
        mart_c = curvas_mart[fi]

        # Determinar ejes comunes
        all_vals = real_c + mart_c
        y_min = min(all_vals) - 0.5
        y_max = max(all_vals) + 0.5

        ax.plot(real_c, color='#4A6080', linewidth=1.2, alpha=0.7, label='Real')
        ax.plot(mart_c, color='#00D4FF', linewidth=1.8, label='Martingala')

        ax.set_xlim(0, max(len(real_c), len(mart_c)))
        ax.set_ylim(y_min, y_max)
        ax.axhline(y=0, color='#4A6080', linewidth=0.6, linestyle='--', alpha=0.5)

        name_fi = FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}'
        title_color = '#00FF88' if resultados[fi]['mart_final'] > resultados[fi]['real_final'] else '#FF3366'
        ax.set_title(f'[{fi}] {name_fi}', color=title_color, fontsize=10, fontweight='bold', pad=6)

        ax.tick_params(colors='#4A6080', labelsize=7)
        for spine in ax.spines.values():
            spine.set_color('#0D2137')

        # Stats box
        r = resultados[fi]
        stats_txt = (f'Real: {r["real_final"]:+.2f}  Mart: {r["mart_final"]:+.2f}\n'
                     f'WR: {r["win_rate"]:.1f}%  Bets: {r["n_bets"]}\n'
                     f'MaxBet: {r["max_bet"]:.2f}  DD: {r["max_drawdown"]:.2f}')
        ax.text(0.02, 0.97, stats_txt, transform=ax.transAxes,
                fontsize=7, color='#4A6080', va='top',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#050A14', edgecolor='#0D2137', alpha=0.8))

        ax.legend(loc='lower right', fontsize=7, facecolor='#0A1628', edgecolor='#0D2137',
                  labelcolor=['#4A6080', '#00D4FF'])

        # Eje Y con formato de moneda
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.1f}'))

    # Ocultar subplots sobrantes
    for idx in range(n, rows * cols):
        fig.add_subplot(gs[idx]).set_visible(False)

    fig.suptitle(f'Estudio Martingala — Apuesta base 0.1'
                 + (f' (max {max_dobles} dobles)' if max_dobles else ''),
                 color='#00D4FF', fontsize=14, fontweight='bold', y=0.98)

    plt.show()


def main():
    _habilitar_utf8()

    parser = argparse.ArgumentParser(description='Estudio de Martingala sobre filtros del historial')
    parser.add_argument('--cap', type=int, default=None,
                        help='Maximo de dobles consecutivas antes de resetear')
    parser.add_argument('--base', type=float, default=0.1,
                        help='Apuesta base (default: 0.1)')
    parser.add_argument('--grafico', action='store_true',
                        help='Mostrar grafica comparativa con matplotlib')
    parser.add_argument('--debug', type=int, default=None,
                        help='Mostrar detalle ronda a ronda para un filtro')
    parser.add_argument('--export', type=str, default=None,
                        help='Exportar resultados a JSON')
    args = parser.parse_args()

    print(f'\n  {_A["cy"]}{_A["B"]}ESTUDIO MARTINGALA{_A["R"]}'
          f'  {_A["dm"]}{_A["B"]}\u2501{_A["R"]}'
          f'  {_A["tx"]}base={args.base}  cap={args.cap or "inf"}{_A["R"]}')

    # ── Cargar datos ─────────────────────────────────────────────────
    if not LONGFILE.exists():
        print(f'  {_A["rd"]}ERROR: No se encuentra {LONGFILE}{_A["R"]}')
        sys.exit(1)

    with open(LONGFILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Agrupar por filtro_idx
    by_idx = {}
    for line in lines:
        d = json.loads(line)
        fi = d.get('filtro_idx')
        if fi is None:
            continue
        if fi not in by_idx:
            by_idx[fi] = []
        by_idx[fi].append(d)

    if not by_idx:
        print(f'  {_A["rd"]}No se encontraron datos en {LONGFILE}{_A["R"]}')
        sys.exit(1)

    # ── Simular ───────────────────────────────────────────────────────
    resultados = {}
    curvas_reales = {}
    curvas_mart = {}

    for fi in sorted(by_idx.keys()):
        ops = by_idx[fi]

        # Simular real
        real = simular_real(ops)

        # Simular Martingala
        mart = simular_martingala(ops, base_bet=args.base, max_dobles=args.cap)

        resultados[fi] = {
            'filtro': FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}',
            'n_bets': mart['n_bets'],
            'n_skips': mart['n_skips'],
            'n_total': mart['n_total'],
            'n_wins': mart['n_wins'],
            'n_losses': mart['n_losses'],
            'win_rate': round(mart['win_rate'], 1),
            'real_final': real['final_balance'],
            'mart_final': mart['final_balance'],
            'max_bet': mart['max_bet'],
            'max_drawdown': mart['max_drawdown'],
            'longest_loss': mart['longest_loss_streak'],
            'max_dobles_alcanzado': mart['max_dobles_alcanzado'],
            'real_wins': real['n_wins'],
            'real_losses': real['n_losses'],
            'real_win_rate': round(real['win_rate'], 1),
        }
        curvas_reales[fi] = real['curve']
        curvas_mart[fi] = mart['curve']

    # ── Mostrar resultados ────────────────────────────────────────────
    imprimir_tabla(resultados,
                   titulo=f"MARTINGALA  (base={args.base}, cap={args.cap or 'inf'})")
    imprimir_resumen(resultados)

    # ── Drawdown extremos ─────────────────────────────────────────────
    print(_sec("FILTROS CON MAYOR DRAWDOWN"))
    print()
    sorted_dd = sorted(resultados.items(), key=lambda x: x[1]['max_drawdown'])
    worst_dd = [fi for fi, _ in sorted_dd[:5]]
    for fi in worst_dd:
        r = resultados[fi]
        dd_pct = abs(r['max_drawdown']) / abs(r['max_bet']) * 100 if r['max_bet'] else 0
        name_fi = FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}'
        print(f'  {_A["rd"]}\u25bc{_A["R"]}  '
              f'{_A["tx"]}{name_fi:<22}{_A["R"]}  '
              f'{_pnl_str(r["max_drawdown"])}  '
              f'{_A["mu"]}({dd_pct:.0f}% de max_bet {r["max_bet"]:.2f}){_A["R"]}')
    print()

    # ── Racha mas larga ───────────────────────────────────────────────
    print(_sec("RACHAS MAS LARGAS DE PERDIDAS"))
    print()
    sorted_rachas = sorted(resultados.items(), key=lambda x: x[1]['longest_loss'], reverse=True)
    for fi, r in sorted_rachas[:5]:
        name_fi = FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}'
        print(f'  {_A["rd"]}\u2717{_A["R"]}  '
              f'{_A["tx"]}{name_fi:<22}{_A["R"]}  '
              f'{_A["rd"]}{r["longest_loss"]} seguidas{_A["R"]}  '
              f'{_A["mu"]}({r["n_losses"]} perdidas / {r["n_bets"]} apuestas){_A["R"]}')
    print()

    # ── Tabla de frecuencias de rachas ────────────────────────────────
    print(_sec("DISTRIBUCION DE RACHAS (todos los filtros)"))
    print()

# Analizar rachas por filtro
    racha_dist = {}
    for fi in sorted(by_idx.keys()):
        ops = by_idx[fi]
        streak = 0
        for op in ops:
            delta = _extraer_delta(op)
            if delta == 0:
                continue
            if delta < 0:
                streak += 1
            else:
                if streak > 0:
                    racha_dist[streak] = racha_dist.get(streak, 0) + 1
                streak = 0
        if streak > 0:
            racha_dist[streak] = racha_dist.get(streak, 0) + 1

    total_rachas = sum(racha_dist.values())
    max_streak_show = max(racha_dist.keys()) if racha_dist else 0

    for s in range(1, max_streak_show + 1):
        count = racha_dist.get(s, 0)
        pct = count / total_rachas * 100 if total_rachas else 0
        bar = '\u2588' * count if count < 40 else '\u2588' * 39 + f'+{count - 39}'
        label = f'{_A["gn"]}{"OK"}{_A["R"]}' if s <= 3 else f'{_A["am"]}CAP{s}{_A["R"]}' if s == 4 else f'{_A["rd"]}PEL{s}{_A["R"]}'
        print(f'  {label}  {_A["tx"]}{s:>2d}{_A["R"]}  '
              f'{_A["cy"]}{bar}{_A["R"]}  '
              f'{_A["mu"]}{count:>4d} ({pct:>4.1f}%){_A["R"]}')

    print(f'\n  {_A["mu"]}Total rachas: {total_rachas}{_A["R"]}')
    print()

    # ── Exportar JSON ─────────────────────────────────────────────────
    if args.export:
        export_path = Path(args.export)
        export_data = {
            'config': {
                'base_bet': args.base,
                'max_dobles': args.cap,
            },
            'resultados': {},
        }
        for fi, r in sorted(resultados.items()):
            nombre_filtro = FILTROS[fi] if fi < len(FILTROS) else f'Filtro {fi}'
            color_filtro = FILTRO_COLORS[fi] if fi < len(FILTRO_COLORS) else '#FFFFFF'
            export_data['resultados'][fi] = {
                'filtro': nombre_filtro,
                'color': color_filtro,
                'n_bets': r['n_bets'],
                'win_rate': r['win_rate'],
                'real_pnl': r['real_final'],
                'mart_pnl': r['mart_final'],
                'max_bet': r['max_bet'],
                'max_drawdown': r['max_drawdown'],
                'longest_loss_streak': r['longest_loss'],
            }

        with open(export_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        print(f'  {_A["gn"]}Exportado a {export_path}{_A["R"]}')
        print()

    # ── Grafica ────────────────────────────────────────────────────────
    if args.grafico:
        try:
            _graficar(curvas_reales, curvas_mart, resultados, max_dobles=args.cap)
        except Exception as e:
            print(f'  {_A["rd"]}Error al generar grafico: {e}{_A["R"]}')

    print('  Hecho.\n')


if __name__ == '__main__':
    main()