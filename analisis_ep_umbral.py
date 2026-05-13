"""
ANÁLISIS EP UMBRAL — Verificación del PNL teórico sobre decisiones históricas.

Lee pnl_decision_history.json y simula la estrategia EP UMBRAL ronda a ronda
usando estadísticas rolling por (rango, modo) sin lookahead.

Compara:
  - PNL teórico EP UMBRAL (todas las rondas con señal)
  - PNL solo en rondas donde el filtro activo era EP UMBRAL
  - Reproducción del balance_filtro
  - Valor guardado en el JSON

Uso:
    py analisis_ep_umbral.py
"""
import sys
import json
from collections import defaultdict
from pathlib import Path

# Importar ep_mult de backtest_ep para calcular multiplicador correcto
sys.path.insert(0, str(Path(__file__).parent))
try:
    from backtest_ep import ep_mult
except Exception:
    def ep_mult(conf):
        return 1

# UTF-8 en Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── Parámetros EP UMBRAL ──────────────────────────────────────────────
EP_MIN_WR    = 53.2   # Umbral mínimo de WR para activar señal
MIN_OPS      = 10     # Mínimo de ops por (rango, modo) para considerar WR
MULT_MAXIMO_DEFAULT = 5   # Fallback si falla la lectura de Sheets


def leer_mult_maximo_sheets() -> int:
    """Lee MULT_MAXIMO desde la pestaña Variables de Google Sheets (Pk_Arena).
    Devuelve el valor configurado o MULT_MAXIMO_DEFAULT si falla."""
    try:
        import gspread
        from oauth2client.service_account import ServiceAccountCredentials
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]
        cred_path = str(Path(__file__).parent / 'credenciales.json')
        creds = ServiceAccountCredentials.from_json_keyfile_name(cred_path, scope)
        cliente = gspread.authorize(creds)
        ws = cliente.open("Pk_Arena").worksheet("Variables")
        for fila in ws.get_all_values():
            if fila and str(fila[0]).strip().upper() == 'MULT_MAXIMO':
                val = str(fila[1]).strip().replace(',', '.') if len(fila) > 1 else ''
                return int(float(val)) if val else MULT_MAXIMO_DEFAULT
    except Exception as exc:
        print(f"[Sheets] No se pudo leer MULT_MAXIMO ({exc}). Usando default {MULT_MAXIMO_DEFAULT}.")
    return MULT_MAXIMO_DEFAULT


MULT_MAXIMO  = leer_mult_maximo_sheets()

# ── Ruta del archivo ──────────────────────────────────────────────────
HIST_FILE = Path(__file__).parent / 'pnl_decision_history.json'


def cargar_decisiones(ruta: Path) -> list:
    if not ruta.exists():
        print(f"[ERROR] No existe: {ruta}")
        sys.exit(1)
    with open(ruta, 'r', encoding='utf-8') as f:
        return json.load(f)


def simular(decisiones: list) -> dict:
    """Simula EP UMBRAL ronda a ronda con stats rolling sin lookahead.
    Devuelve un dict con todas las métricas calculadas."""

    # Stats acumulados por (rango, modo) — solo con rondas anteriores
    stats = defaultdict(lambda: {'DIRECTO': {'ops': 0, 'gan': 0},
                                 'INVERSO': {'ops': 0, 'gan': 0}})

    total_pnl_global       = 0.0   # PNL si EP UMBRAL apostara siempre que tenga señal
    total_pnl_filtro       = 0.0   # PNL solo cuando el filtro activo era EP UMBRAL
    n_filtro_ep            = 0     # Rondas con filtro=EP UMBRAL
    n_signal_global        = 0     # Rondas con señal activa
    n_signal_when_filtro   = 0     # Rondas con señal cuando filtro=EP UMBRAL

    balance         = 0.0   # Reproducción de balance_filtro
    last_balance_ep = 0.0   # Último balance en una decisión filtro=EP UMBRAL

    for d in decisiones:
        rango      = d.get('rango', '')
        winner     = (d.get('winner') or '').lower()
        mayor      = (d.get('mayor')  or '').lower()
        modo_real  = d.get('modo', '')
        filtro     = d.get('filtro', '')
        decision   = d.get('decision', '')
        pnl_real   = d.get('pnl') or 0.0
        acierto_my = (winner == mayor)

        # 1. Calcular SEÑAL EP UMBRAL con stats acumulados (sin lookahead)
        d_st = stats[rango]['DIRECTO']
        i_st = stats[rango]['INVERSO']
        d_wr = d_st['gan'] / d_st['ops'] * 100 if d_st['ops'] >= MIN_OPS else 0.0
        i_wr = i_st['gan'] / i_st['ops'] * 100 if i_st['ops'] >= MIN_OPS else 0.0
        d_ok = d_wr >= EP_MIN_WR
        i_ok = i_wr >= EP_MIN_WR

        pnl_eu = 0.0
        if d_ok or i_ok:
            n_signal_global += 1
            if filtro == 'EP UMBRAL':
                n_signal_when_filtro += 1
            # Color que apuesta EP UMBRAL: mayoría si DIRECTO gana, minoría si INVERSO gana
            if (d_wr if d_ok else 0) >= (i_wr if i_ok else 0):
                color_ep = mayor
                wr_m = d_wr
            else:
                color_ep = 'rojo' if mayor == 'azul' else 'azul'
                wr_m = i_wr
            mult = ep_mult(wr_m, MULT_MAXIMO)   # mult basado en WR, capado a MULT_MAXIMO
            gano_ep = (color_ep == winner)
            pnl_eu = round(0.9 * mult if gano_ep else -1.0 * mult, 2)

        total_pnl_global += pnl_eu

        # 2. Reproducir balance_filtro como en _on_resultado_ev
        if filtro == 'EP UMBRAL':
            delta = pnl_eu
            n_filtro_ep += 1
            total_pnl_filtro += pnl_eu
        elif decision != 'SKIP':
            delta = pnl_real
        else:
            delta = 0.0
        balance = round(balance + delta, 2)
        if filtro == 'EP UMBRAL':
            last_balance_ep = balance

        # 3. Actualizar stats con resultado real (mayoría) según modo
        if modo_real in ('DIRECTO', 'INVERSO') and winner and mayor:
            ganada = acierto_my if modo_real == 'DIRECTO' else not acierto_my
            stats[rango][modo_real]['ops'] += 1
            if ganada:
                stats[rango][modo_real]['gan'] += 1

    return {
        'total_decisiones'      : len(decisiones),
        'pnl_global'            : round(total_pnl_global, 2),
        'pnl_filtro_ep'         : round(total_pnl_filtro, 2),
        'n_signal_global'       : n_signal_global,
        'n_signal_when_filtro'  : n_signal_when_filtro,
        'n_filtro_ep'           : n_filtro_ep,
        'balance_reproducido'   : last_balance_ep,
        'stats_finales'         : dict(stats),
    }


def imprimir_reporte(res: dict, decisiones: list):
    n_total = res['total_decisiones']

    # Buscar último balance_filtro guardado en EP UMBRAL
    ep_dec = [d for d in decisiones if d.get('filtro') == 'EP UMBRAL']
    bal_guardado = ep_dec[-1].get('balance_filtro') if ep_dec else None

    print('=' * 64)
    print(f'  ANÁLISIS EP UMBRAL — {n_total} decisiones')
    print(f'  Umbral WR: {EP_MIN_WR}%   |   Min ops: {MIN_OPS}   |   Mult máx: {MULT_MAXIMO}x')
    print('=' * 64)
    print()

    # Sección 1: PNL teórico global
    print('  ── PNL TEÓRICO ──────────────────────────────────────')
    print(f'  EP UMBRAL aplicado a TODAS las rondas con señal:')
    print(f'    PNL acumulado : {res["pnl_global"]:+.2f} €')
    print(f'    Rondas señal  : {res["n_signal_global"]} / {n_total} '
          f'({res["n_signal_global"]/n_total*100:.1f}%)')
    if res['n_signal_global']:
        win_apx = (res['pnl_global'] + res['n_signal_global']) / (1.9 * res['n_signal_global']) * 100
        print(f'    WR aproximado : {win_apx:.1f}%')
    print()

    # Sección 2: PNL en rondas con filtro EP UMBRAL activo
    print('  ── PNL CON FILTRO EP UMBRAL ACTIVO ──────────────────')
    print(f'    Decisiones con filtro=EP UMBRAL : {res["n_filtro_ep"]}')
    print(f'    De ellas, con señal             : {res["n_signal_when_filtro"]}')
    print(f'    PNL acumulado                   : {res["pnl_filtro_ep"]:+.2f} €')
    print()

    # Sección 3: Comparación con balance_filtro guardado
    print('  ── COMPARACIÓN BALANCE_FILTRO ───────────────────────')
    print(f'    Reproducido en simulación : {res["balance_reproducido"]:+.2f} €')
    if bal_guardado is not None:
        print(f'    Guardado en JSON          : {bal_guardado:+.2f} €')
        diff = abs(res['balance_reproducido'] - bal_guardado)
        marca = 'OK' if diff < 0.5 else f'DIVERGE ({diff:.2f} €)'
        print(f'    Diferencia                : {marca}')
    else:
        print('    Guardado en JSON          : (no hay decisiones EP UMBRAL)')
    print()

    # Sección 4: Top rangos
    print('  ── TOP RANGOS POR WR FINAL (≥{} ops) ────────────────'.format(MIN_OPS))
    rangos_wr = []
    for rango, modos in res['stats_finales'].items():
        for modo in ('DIRECTO', 'INVERSO'):
            s = modos[modo]
            if s['ops'] >= MIN_OPS:
                wr = s['gan'] / s['ops'] * 100
                rangos_wr.append((rango, modo, wr, s['ops']))
    rangos_wr.sort(key=lambda x: -x[2])
    print(f'    {"rango":<8} {"modo":<10} {"WR":>7}  {"ops":>5}  {"señal":>6}')
    print(f'    {"-"*8} {"-"*10} {"-"*7}  {"-"*5}  {"-"*6}')
    for rango, modo, wr, ops in rangos_wr[:15]:
        marca = '✓' if wr >= EP_MIN_WR else ' '
        print(f'    {rango:<8} {modo:<10} {wr:>6.1f}%  {ops:>5}  {marca:>6}')
    print()
    print('=' * 64)


def main():
    decisiones = cargar_decisiones(HIST_FILE)
    resultado = simular(decisiones)
    imprimir_reporte(resultado, decisiones)


if __name__ == '__main__':
    main()
