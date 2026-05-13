"""
PNL DASHBOARD — Funciones de parseo y calculo de datos.
"""
import json
from collections import deque


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


def parsear_websocket(ruta):
    """Parsea websocket_log.txt y reconstruye rondas como reconstructor_dashboard.py"""
    ops = []
    historial_aciertos = []
    tick = 0
    blue_pre, red_pre = 0, 0
    dif_t25, dif_t33, dif_actual = 0, 0, 0
    acel, est = 0.0, "ESTABLE"

    with open(ruta, 'r', encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                if 'MENSAJE RECIBIDO:' in linea:
                    linea = linea.split('MENSAJE RECIBIDO:', 1)[1].strip()
                data = json.loads(linea)
            except Exception:
                continue
            t = data.get('type', '')

            if t in ('open_draw', 'start', 'game_start', 'game_init'):
                tick = 0
                dif_t25, dif_t33, dif_actual = 0, 0, 0
                acel, est = 0.0, "ESTABLE"
                continue

            if t == 'total_bet':
                tick += 1
                blue = float(data.get('blue', 0))
                red = float(data.get('red', 0))
                total = blue + red
                if total > 0:
                    blue_p = (blue / total) * 100
                    red_p = (red / total) * 100
                    dif_actual = round(abs(blue_p - red_p), 2)
                blue_pre, red_pre = blue, red
                if tick == 25:
                    dif_t25 = dif_actual
                if tick == 33:
                    dif_t33 = dif_actual
                    acel = round(dif_actual - dif_t25, 2)
                    est = "ESTABLE" if abs(acel) < 3 else "VOLATIL"
                continue

            if t == 'drawed':
                ganador = 'BLUE' if 'blue' in data.get('result', '').lower() else 'RED'
                mayor = 'BLUE' if blue_pre > red_pre else 'RED'
                acierto = (ganador == mayor)
                historial_aciertos.append(acierto)
                if len(historial_aciertos) > 10:
                    historial_aciertos.pop(0)
                wr = (sum(historial_aciertos) / len(historial_aciertos)) * 100
                rango_dif = dif_t33 if dif_t33 > 0 else dif_actual
                rango = calcular_rango(rango_dif)
                modo = "DIRECTO" if wr >= 60 else ("INVERSO" if wr <= 40 else "SKIP")

                ops.append({
                    'skip': modo == "SKIP",
                    'acierto': acierto,
                    'rango': rango,
                    'modo': modo,
                    'wr': wr,
                    'est': est,
                    'acel': acel,
                })
    return ops


def parsear(ruta):
    ops = []
    historial = deque(maxlen=20)
    with open(ruta, 'r', encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if not linea or 'GANADOR:' not in linea:
                continue
            try:
                acierto = 'ACIERTO: True' in linea
                rango = linea.split('RANGO: ')[1].split(' |')[0].strip() if 'RANGO: ' in linea else '?'
                est = 'VOLATIL' if 'EST: VOLATIL' in linea else 'ESTABLE'
                acel_str = linea.split('ACEL: ')[1].split(' |')[0].strip() if 'ACEL: ' in linea else '0'
                historial.append(1 if acierto else 0)
                wr = sum(historial) / len(historial) * 100 if historial else 50.0
                modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
                ops.append({
                    'skip': modo == 'SKIP', 'acierto': acierto, 'rango': rango,
                    'modo': modo, 'wr': round(wr, 2), 'est': est, 'acel': float(acel_str),
                })
            except Exception:
                pass
    return ops


def curva_pnl_ep(ops, ventana=20, umbral=53.2, min_ops=20, min_wr_dir=0, contrarian=False):
    """
    Curva adaptativa con ventana rolling GLOBAL — replica `_ep_rolling_dir`.
    - Observa las últimas `ventana` rondas con outcome válido.
    - Solo actúa cuando hay >= min_ops rondas en la ventana (tendencia definida).
    - WR >= umbral       → apuesta mayoría  (DIRECTO)
    - WR <= 100-umbral   → apuesta minoría  (INVERSO: tendencia negativa)
    - Zona media         → SKIP (línea plana)
    - SKIP de la op      → no se apuesta y NO entra en la ventana (mismo criterio
      que live: si no se llega a calcular señal, no aporta histórico).
    - contrarian=True    → invierte la dirección de la apuesta.

    Señal canónica: `op['gano_mayoria']` (resultado objetivo, independiente del
    modo). Si el campo falta, la op se descarta (no entra en ventana ni apuesta)
    para evitar contaminación por fallback con `acierto` (semántica distinta).

    NO LOOKAHEAD: la decisión usa la ventana `v` con los outcomes ANTERIORES;
    la op actual solo se añade a `v` DESPUÉS de apostar.

    Devuelve (curva, n_ac, n_bets, cambios).
    """
    v = deque(maxlen=ventana)
    acum = 0.0
    curva = []
    n_ac = 0
    n_bets = 0
    cambios = []
    dir_actual = None
    prev_wr = 50.0

    for op in ops:
        gm = op.get('gano_mayoria')
        # Sin outcome objetivo (gano_mayoria=None): la op no entra en la ventana
        # ni se apuesta. NOTA: op['skip'] (skip del modo live) NO bloquea EP
        # ADAPTATIVO — este filtro tiene su propio gate (rolling WR) y debe
        # evaluar todas las rondas con resultado, igual que `_ep_rolling_dir`
        # mira las últimas `ventana` ops de live_all_ops sin filtrar por skip.
        if gm is None:
            curva.append(acum)
            prev_wr = op.get('wr', prev_wr)
            continue

        n_v = len(v)
        if n_v >= min_ops:
            wr = sum(v) / n_v * 100
            if wr >= umbral:
                nueva_dir = 'DIRECTO'
            elif wr <= (100 - umbral):
                nueva_dir = 'INVERSO'
            else:
                # Zona neutral: no apuesta. La ventana SÍ se actualiza con la
                # op actual (su outcome es información objetiva para futuras
                # decisiones, igual que en live se sigue acumulando histórico).
                curva.append(acum)
                v.append(1 if gm else 0)
                prev_wr = op.get('wr', 50)
                continue

            dir_efectiva = 'INVERSO' if (nueva_dir == 'DIRECTO' and contrarian) else (
                          'DIRECTO' if (nueva_dir == 'INVERSO' and contrarian) else nueva_dir)

            # Filtro de calidad usando WR del op ANTERIOR (paralelo a gate live)
            if min_wr_dir > 0:
                wr_op = prev_wr
                if dir_efectiva == 'DIRECTO' and wr_op < min_wr_dir:
                    curva.append(acum)
                    v.append(1 if gm else 0)
                    prev_wr = op.get('wr', 50)
                    continue
                if dir_efectiva == 'INVERSO' and wr_op > (100 - min_wr_dir):
                    curva.append(acum)
                    v.append(1 if gm else 0)
                    prev_wr = op.get('wr', 50)
                    continue

            if dir_efectiva != dir_actual:
                cambios.append((len(curva), dir_efectiva))
                dir_actual = dir_efectiva

            # Apuesta del filtro: DIRECTO gana si mayoría ganó; INVERSO si perdió.
            gano = bool(gm) if dir_efectiva == 'DIRECTO' else (not bool(gm))
            _m = op.get('mult', 1)
            if gano:
                acum += 0.9 * _m
                n_ac += 1
            else:
                acum -= 1.0 * _m
            n_bets += 1
        # Fase observación (n_v < min_ops): no apuesta pero acumula histórico.

        curva.append(acum)
        # Actualizar ventana DESPUÉS de la decisión (NO LOOKAHEAD)
        v.append(1 if gm else 0)
        prev_wr = op.get('wr', 50)

    return curva, n_ac, n_bets, cambios


def curva_pnl_umbral(ops, umbral=53.2, min_ops=10, ops_hist=None, mult_maximo=5,
                     adaptativo=False, ventana_regimen=50, warmup=20,
                     umbral_alto=0.55, umbral_bajo=0.45):
    """EP por umbral rolling por rango — sin lookahead.
    ops_hist: ops previas para inicializar WR (no se simulan).
    Multiplicador calculado con ep_mult(WR, mult_maximo) — no usa op['mult'] (contaminado).
    Decide con el WR acumulado HASTA ese momento; actualiza stats después.
    Línea plana cuando no hay señal (igual que en live).

    Si `adaptativo=True`: mantiene una ventana rolling de outcomes EP de tamaño
    `ventana_regimen` y decide:
      - WR > umbral_alto → apostar EP normal.
      - WR < umbral_bajo → invertir (anti-EP).
      - zona neutra      → SKIP.
    Durante `warmup` primeras señales, sólo observa.
    """
    from collections import defaultdict, deque
    try:
        from backtest_ep import ep_mult
    except Exception:
        ep_mult = lambda conf, max_m=0: 1
    stats = defaultdict(lambda: {'DIRECTO': {'ops': 0, 'ganadas': 0},
                                 'INVERSO': {'ops': 0, 'ganadas': 0}})
    ventana_outcomes = deque(maxlen=ventana_regimen) if adaptativo else None

    def _gano_ep_op(op, mejor):
        """Outcome objetivo de la apuesta pura EP (`mejor` = DIRECTO|INVERSO) sobre op.
        Usa `gano_mayoria` como única señal canónica:
          - DIRECTO gana si mayor ganó.
          - INVERSO gana si mayor perdió.
        Si `gano_mayoria` es None (no se pudo determinar) → devuelve None y el
        caller debe ignorar la op (no apostar, no alimentar ventana).
        Sin fallback a `acierto`+`modo`: esa fórmula contamina porque `modo`
        depende del WR live, creando un sesgo direccional auto-referente."""
        gm = op.get('gano_mayoria')
        if gm is None:
            return None
        gm = bool(gm)
        return gm if mejor == 'DIRECTO' else (not gm)

    # Pre-poblar stats Y ventana rolling con histórico, para que la sesión
    # nueva arranque ya calibrada (sin WARMUP cada vez que se reinicia).
    # NO LOOKAHEAD: la decisión usa stats acumuladas ANTES de esta op; los
    # stats se incrementan DESPUÉS.
    for op in (ops_hist or []):
        r, m = op.get('rango', ''), op.get('modo', '')
        if adaptativo and ventana_outcomes is not None:
            d_h = stats[r]['DIRECTO']
            i_h = stats[r]['INVERSO']
            d_wr_h = d_h['ganadas'] / d_h['ops'] * 100 if d_h['ops'] >= min_ops else 0.0
            i_wr_h = i_h['ganadas'] / i_h['ops'] * 100 if i_h['ops'] >= min_ops else 0.0
            mejor_h = 'DIRECTO' if d_wr_h >= i_wr_h else 'INVERSO'
            mejor_wr_h = max(d_wr_h, i_wr_h)
            if mejor_wr_h >= umbral:
                _go = _gano_ep_op(op, mejor_h)
                if _go is not None:        # ignorar ops sin gano_mayoria válido
                    ventana_outcomes.append(1 if _go else 0)
        # Stats por (rango, modo) — mismas semánticas que `_get_wr_rango` en live.
        if m in ('DIRECTO', 'INVERSO'):
            stats[r][m]['ops'] += 1
            if op.get('acierto', False):
                stats[r][m]['ganadas'] += 1

    acum = 0.0
    curva = []
    n_ac = 0
    n_bets = 0
    for op in ops:
        rango = op.get('rango', '')
        modo_op = op.get('modo', '')
        # Decidir con WR de ops ANTERIORES (sin incluir la actual)
        d = stats[rango]['DIRECTO']
        i = stats[rango]['INVERSO']
        d_wr = d['ganadas'] / d['ops'] * 100 if d['ops'] >= min_ops else 0.0
        i_wr = i['ganadas'] / i['ops'] * 100 if i['ops'] >= min_ops else 0.0
        mejor = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
        mejor_wr = max(d_wr, i_wr)
        if mejor_wr >= umbral:
            gano_ep = _gano_ep_op(op, mejor)
            # Sin gano_mayoria válido → no se puede evaluar: SKIP (no apuesta,
            # no entra en ventana_outcomes). Evita contaminar el régimen.
            if gano_ep is None:
                curva.append(acum)
                continue
            _m = ep_mult(mejor_wr, mult_maximo)
            if adaptativo:
                if len(ventana_outcomes) >= warmup:
                    wr_reg = sum(ventana_outcomes) / len(ventana_outcomes)
                    if wr_reg > umbral_alto:
                        gano = gano_ep
                        acum += 0.9 * _m if gano else -1.0 * _m
                        if gano: n_ac += 1
                        n_bets += 1
                    elif wr_reg < umbral_bajo:
                        gano = not gano_ep
                        acum += 0.9 * _m if gano else -1.0 * _m
                        if gano: n_ac += 1
                        n_bets += 1
                    # else: zona neutra → SKIP (línea plana)
                # else: warmup → SKIP
                # NO LOOKAHEAD: la ventana se actualiza DESPUÉS de la apuesta.
                ventana_outcomes.append(1 if gano_ep else 0)
            else:
                acum += 0.9 * _m if gano_ep else -1.0 * _m
                if gano_ep:
                    n_ac += 1
                n_bets += 1
        curva.append(acum)
        # Actualizar stats DESPUÉS de decidir (no lookahead)
        if modo_op in ('DIRECTO', 'INVERSO'):
            stats[rango][modo_op]['ops'] += 1
            if op.get('acierto', False):
                stats[rango][modo_op]['ganadas'] += 1
    return curva, n_ac, n_bets, []


def curva_pnl(ops, filtro, contrarian=False, raw=False):
    """
    Calcula curva PNL acumulada.
    raw=True  → apuesta mayoría siempre sin ajuste de modo (línea "Real" bruta).
    raw=False → ajusta por modo: INVERSO usa gano = not acierto.
    """
    acum = 0.0
    curva = []
    n_ac = 0
    n_bets = 0
    for op in ops:
        if op.get('skip'):
            # SKIP/NO APUESTA: ningún filtro (incluido Base) acumula nada
            curva.append(acum)
            continue
        if not filtro(op):
            curva.append(acum)   # línea plana cuando no apuesta
            continue
        # Resolver BASE mode igual que _calcular_pnl_filtros
        modo = op.get('modo', '')
        if modo == 'BASE':
            wr = float(op.get('wr') or 50)
            modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
        if modo == 'SKIP':
            curva.append(acum)
            continue
        if raw:
            gano = op['acierto']   # apuesta mayoría siempre, sin corrección
        else:
            gano = op['acierto'] if modo != 'INVERSO' else not op['acierto']
        if contrarian:
            gano = not gano
        # Base (raw=True) ignora multiplicador; el resto sí lo aplica
        _m = 1 if raw else op.get('mult', 1)
        if gano:
            acum += 0.9 * _m
            n_ac += 1
        else:
            acum -= 1.0 * _m
        n_bets += 1
        curva.append(acum)
    return curva, n_ac, n_bets
