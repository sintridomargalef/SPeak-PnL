"""
rebuild_decision_history.py — Reconstruye COMPLETAMENTE pnl_decision_history.json:

  · Recalcula TODAS las rondas como si EP UMBRAL adaptativo (con los params
    óptimos del último sweep) hubiera sido el filtro activo desde la primera ronda.
  · Llena pnl_filtros con el valor correcto para los 17 filtros (no solo idx 16).
  · Mantiene estadísticas acumuladas por (rango, modo) cronológicamente.
  · Acumula balance_real (always-bet-majority) y balance_filtro (EP UMBRAL).
  · Muestra el color teórico aunque la ronda no se apueste (SKIP_REG/WARMUP),
    para que la columna "Color" no quede vacía cuando hay modo direccional.

Uso:  py rebuild_decision_history.py
"""
import json
import shutil
from collections import defaultdict, deque
from pathlib import Path

from pnl_config import FILTROS_CURVA

DECISION_FILE = Path('pnl_decision_history.json')
BACKUP_FILE   = Path('pnl_decision_history.bak3.json')

# ── Parámetros EP UMBRAL — Opción B (selectiva ganadora) ───────────────────
UMBRAL          = 62.0
MIN_OPS         = 5
VENTANA_REGIMEN = 30
WARMUP          = 10
UMBRAL_ALTO     = 0.55
UMBRAL_BAJO     = 0.50
EP_IDX          = 16        # índice de "EP UMBRAL" en FILTROS_CURVA
PNL_OK          = 0.9
PNL_KO          = -1.0
MAX_MULT        = 5
N_FILTROS       = len(FILTROS_CURVA)


def ep_mult(wr, max_m=MAX_MULT):
    if wr >= 90: return min(7, max_m)
    if wr >= 85: return min(6, max_m)
    if wr >= 80: return min(5, max_m)
    if wr >= 75: return min(4, max_m)
    if wr >= 70: return min(3, max_m)
    if wr >= 65: return min(2, max_m)
    return 1


def calcular_pnl_filtros(op, gano_mayoria, mult_eur):
    """Devuelve dict {idx: pnl} aplicando los 17 filtros de FILTROS_CURVA al op."""
    factor = mult_eur                                 # apuesta_base
    res = {}
    for i, entry in enumerate(FILTROS_CURVA):
        nombre, color, filtro_fn, contrarian, raw = entry
        # Filtros adaptativos / especiales: se rellenan aparte
        if filtro_fn is None:
            res[i] = None
            continue
        if isinstance(filtro_fn, str):
            res[i] = None      # EP_WR70 / EP_UMBRAL / BAL_FILTRO se llenan después
            continue
        # Filtro lambda
        if op['skip'] and not raw:
            res[i] = 0.0
            continue
        if not filtro_fn(op):
            res[i] = 0.0
            continue
        # Cálculo PNL: derivar desde gano_mayoria
        # raw=True → apuesta mayoría siempre
        # raw=False → si modo INVERSO, invertir
        if raw:
            gano = gano_mayoria
        else:
            modo = op.get('modo', '')
            gano = gano_mayoria if modo != 'INVERSO' else (not gano_mayoria)
        if contrarian:
            gano = not gano
        res[i] = round(PNL_OK * factor if gano else PNL_KO * factor, 2)
    return res


def main():
    if not DECISION_FILE.exists():
        print(f"ERROR: {DECISION_FILE} no existe")
        return
    shutil.copy(DECISION_FILE, BACKUP_FILE)
    print(f"Backup creado: {BACKUP_FILE}")

    with open(DECISION_FILE, 'r', encoding='utf-8') as f:
        decs = json.load(f)
    print(f"Total decisiones: {len(decs)}")

    # Stats acumuladas por (rango, modo) — alimenta tanto EP UMBRAL como FASES EP
    stats = defaultdict(lambda: {'DIRECTO': {'ops': 0, 'ganadas': 0},
                                 'INVERSO': {'ops': 0, 'ganadas': 0}})
    ventana = deque(maxlen=VENTANA_REGIMEN)

    balance_real   = 0.0
    balance_filtro = 0.0
    ep_session_ops = 0

    n_apostadas = 0
    n_aciertos  = 0
    n_warmup    = 0
    n_no_signal = 0
    n_skip_reg  = 0

    for d in decs:
        winner = (d.get('winner') or '').lower()
        mayor  = (d.get('mayor')  or '').upper()
        rango  = d.get('rango', '')
        mult_eur = float(d.get('mult', 1) or 1)
        wr_t36 = float(d.get('wr') or 50)

        # ── Sin winner: limpiar derivados, mantener raw + mostrar mayor ─
        if not winner:
            d['filtro']         = 'EP UMBRAL'
            d['filtro_nombre']  = 'EP UMBRAL'
            d['modo']           = 'SKIP'
            d['ep_gate']        = 'NO_RESULT'
            d['wr_ep']          = 0.0
            d['decision']       = 'SKIP'
            # Color = la mayoría (lo único determinista pre-resultado)
            d['color_apostado'] = mayor if mayor in ('AZUL', 'ROJO') else ''
            d['ep_session_ops'] = ep_session_ops
            d['conf']           = 0
            d['acierto']        = None
            d['acierto_marca']  = '·'
            d['pnl']            = 0.0
            d['pnl_base']       = 0.0
            d['balance_real']   = round(balance_real, 2)
            d['balance_filtro'] = round(balance_filtro, 2)
            pf = {str(i): None for i in range(N_FILTROS)}
            d['pnl_filtros']    = pf
            continue

        gano_mayoria = (winner == mayor.lower())
        ep_session_ops += 1

        # ── 1) EP UMBRAL: stats por rango ANTES de añadir esta ronda ────
        s_d = stats[rango]['DIRECTO']
        s_i = stats[rango]['INVERSO']
        d_wr = s_d['ganadas'] / s_d['ops'] * 100 if s_d['ops'] >= MIN_OPS else 0.0
        i_wr = s_i['ganadas'] / s_i['ops'] * 100 if s_i['ops'] >= MIN_OPS else 0.0
        mejor    = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
        mejor_wr = max(d_wr, i_wr)

        # color base que apostaría EP puro
        if mejor == 'DIRECTO':
            color_ep_base = mayor
        else:
            color_ep_base = 'ROJO' if mayor == 'AZUL' else 'AZUL'
        gano_ep_base = (color_ep_base.lower() == winner)

        # ── 2) Decidir régimen EP UMBRAL ────────────────────────────────
        apostada      = False
        color_final   = ''
        color_teorico = ''
        regimen_label = ''
        wr_reg_pct    = 0.0
        modo_field    = 'SKIP'

        if mejor_wr < UMBRAL:
            regimen_label = 'NO_SIGNAL'
            n_no_signal += 1
            # Aún sin señal, mostramos el color de la MAYORÍA como referencia
            # (es lo que apostaría una estrategia BASE) para que la columna
            # "Color" nunca quede vacía cuando hay winner.
            color_teorico = mayor
        else:
            modo_field    = mejor
            color_teorico = color_ep_base
            if len(ventana) < WARMUP:
                regimen_label = f"WARMUP {len(ventana)}/{WARMUP}"
                n_warmup += 1
            else:
                wr_reg = sum(ventana) / len(ventana)
                wr_reg_pct = wr_reg * 100
                if wr_reg > UMBRAL_ALTO:
                    apostada      = True
                    color_final   = color_ep_base
                    color_teorico = color_ep_base
                    regimen_label = f"EP {wr_reg_pct:.0f}%"
                elif wr_reg < UMBRAL_BAJO:
                    apostada      = True
                    color_final   = 'ROJO' if color_ep_base == 'AZUL' else 'AZUL'
                    color_teorico = color_final
                    regimen_label = f"ANTI {wr_reg_pct:.0f}%"
                else:
                    color_teorico = color_ep_base
                    regimen_label = f"SKIP_REG {wr_reg_pct:.0f}%"
                    n_skip_reg += 1
            ventana.append(1 if gano_ep_base else 0)

        # ── 3) PNL de la apuesta EP UMBRAL ─────────────────────────────
        if apostada:
            acierto      = (color_final.lower() == winner)
            acierto_mark = '✓' if acierto else '✗'
            mult_ep      = ep_mult(mejor_wr)
            pnl_op       = (PNL_OK if acierto else PNL_KO) * mult_eur * mult_ep
            n_apostadas += 1
            if acierto: n_aciertos += 1
        else:
            acierto      = None
            acierto_mark = '·'
            pnl_op       = 0.0

        # baseline always-bet-majority
        pnl_base = (PNL_OK if gano_mayoria else PNL_KO) * mult_eur

        balance_real   += pnl_base
        balance_filtro += pnl_op

        # ── 4) Calcular pnl_filtros para todos los filtros simples ─────
        # Construir op para evaluar las lambdas como en el motor live.
        # modo_op (BASE/DIRECTO/INVERSO/SKIP) según wr_t36.
        if wr_t36 >= 60:
            modo_op = 'DIRECTO'
        elif wr_t36 <= 40:
            modo_op = 'INVERSO'
        else:
            modo_op = 'SKIP'
        op_dict = {
            'skip':         (modo_op == 'SKIP'),
            'acierto':      gano_mayoria,
            'gano_mayoria': gano_mayoria,
            'modo':         modo_op,
            'rango':        rango,
            'est':          d.get('est', 'ESTABLE'),
            'acel':         float(d.get('acel') or 0),
            'wr':           wr_t36,
            'mult':         mult_eur,
        }
        pf_simple = calcular_pnl_filtros(op_dict, gano_mayoria, mult_eur)

        # Llenar EP UMBRAL idx con la pnl simulada (real)
        pf = {}
        for i in range(N_FILTROS):
            v = pf_simple.get(i)
            if i == EP_IDX:
                pf[str(i)] = round(pnl_op, 2) if apostada else 0.0
            elif v is None:
                # Filtros adaptativos no calculados aquí → null
                pf[str(i)] = None
            else:
                pf[str(i)] = v

        # ── 5) Volcar campos derivados al entry ────────────────────────
        d['filtro']         = 'EP UMBRAL'
        d['filtro_nombre']  = 'EP UMBRAL'
        d['modo']           = modo_field
        d['ep_gate']        = regimen_label
        d['wr_ep']          = round(wr_reg_pct, 1)
        d['decision']       = 'APOSTADA' if apostada else 'SKIP'
        # color_apostado: real si apostada, teórico si SKIP/WARMUP/SKIP_REG
        d['color_apostado'] = color_final if apostada else color_teorico
        d['ep_session_ops'] = ep_session_ops
        d['conf']           = round(mejor_wr)
        d['acierto']        = acierto
        d['acierto_marca']  = acierto_mark
        d['pnl']            = round(pnl_op, 2)
        d['pnl_base']       = round(pnl_base, 2)
        d['balance_real']   = round(balance_real, 2)
        d['balance_filtro'] = round(balance_filtro, 2)
        d['pnl_filtros']    = pf

        # ── 6) Actualizar stats POST-decisión (sin lookahead) ──────────
        # Cada ronda alimenta AMBOS modos (DIRECTO y INVERSO) con la
        # semántica limpia de gano_mayoria.
        stats[rango]['DIRECTO']['ops'] += 1
        stats[rango]['INVERSO']['ops'] += 1
        if gano_mayoria:
            stats[rango]['DIRECTO']['ganadas'] += 1
        else:
            stats[rango]['INVERSO']['ganadas'] += 1

    # ── Guardar ─────────────────────────────────────────────────────────
    with open(DECISION_FILE, 'w', encoding='utf-8') as f:
        json.dump(decs, f, ensure_ascii=False, indent=2)

    print()
    print("=== RECONSTRUCCION COMPLETADA ===")
    print(f"  Total filas:                 {len(decs)}")
    print(f"  Apuestas EP UMBRAL:          {n_apostadas}")
    print(f"  Aciertos:                    {n_aciertos}")
    if n_apostadas:
        print(f"  WR:                          {n_aciertos/n_apostadas*100:.1f}%")
    print(f"  Skips por régimen:")
    print(f"    NO_SIGNAL:                 {n_no_signal}")
    print(f"    WARMUP:                    {n_warmup}")
    print(f"    SKIP_REG:                  {n_skip_reg}")
    print()
    print(f"  Balance baseline (mayoría):  {balance_real:+.2f} EUR")
    print(f"  Balance EP UMBRAL (filtro):  {balance_filtro:+.2f} EUR")
    print(f"  Diferencia:                  {balance_filtro - balance_real:+.2f} EUR")
    print()
    print(f"  Backup: {BACKUP_FILE}")
    print(f"  Para revertir: cp {BACKUP_FILE} {DECISION_FILE}")
    print()
    print("  Stats finales por rango (DIRECTO/INVERSO ops):")
    rangos_orden = ["0-5","5-10","10-15","15-20","20-25","25-30",
                    "30-35","35-40","40-45","45-50","+50"]
    for r in rangos_orden:
        if r in stats:
            sd = stats[r]['DIRECTO']
            si = stats[r]['INVERSO']
            d_wr = sd['ganadas']/sd['ops']*100 if sd['ops']>=MIN_OPS else 0
            i_wr = si['ganadas']/si['ops']*100 if si['ops']>=MIN_OPS else 0
            print(f"    {r:>6}:  DIRECTO {sd['ops']:>4} ops {d_wr:>5.1f}%  |  INVERSO {si['ops']:>4} ops {i_wr:>5.1f}%")


if __name__ == '__main__':
    main()
