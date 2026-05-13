"""
estrategia_perfecta.py — Simulador y grafica de la Estrategia Perfecta.
Lee reconstructor_data_AI.txt y compara:
  - Curva SIN filtro (todas las apuestas)
  - Curva CON estrategia perfecta (ventana 50, umbral 53.2%, multiplicador por confianza)
"""
import re
import json
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from collections import deque
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

# ── Configuracion ──────────────────────────────────────────────────────────────
ARCHIVO_RECONSTRUCTOR = Path(__file__).parent / "reconstructor_data_AI.txt"
ARCHIVO_HISTORIAL     = Path(__file__).parent / "historial_rondas.txt"
ARCHIVO_POS           = Path(__file__).parent / "estrategia_perfecta_pos.json"
ARCHIVO_LIVE          = Path(__file__).parent / "pnl_live_history.json"
ARCHIVO_DECISION      = Path(__file__).parent / "pnl_decision_history.json"
PNL_ACIERTO     = 0.9
PNL_FALLO       = -1.0
from ep_core import ep_evaluar, ep_mult, EP_VENTANA, EP_MIN_OPS, EP_UMBRAL_ESTADO, EP_UMBRAL_PRIOR
from pnl_config import FILTROS_CURVA

VENTANA          = EP_VENTANA
MIN_OPS          = EP_MIN_OPS
UMBRAL_PRIORIDAD = EP_UMBRAL_PRIOR
UMBRAL_ESTADO    = EP_UMBRAL_ESTADO

C = {
    'bg':     '#050A14',
    'panel':  '#0A1628',
    'border': '#0D2137',
    'accent': '#00D4FF',
    'green':  '#00FF88',
    'red':    '#FF3366',
    'warn':   '#FFB800',
    'text':   '#C8D8E8',
    'muted':  '#4A6080',
}

RANGOS_ORDEN = ["0-5","5-10","10-15","15-20","20-25","25-30","30-35","35-40","40-45","45-50","+50"]

# ── Voz ────────────────────────────────────────────────────────────────────────
import threading
import pyttsx3

_tts_lock   = threading.Lock()
_tts_thread = None

def _hablar_async(texto: str):
    """Lanza síntesis de voz en hilo separado para no bloquear la UI."""
    global _tts_thread
    def _run():
        with _tts_lock:
            try:
                engine = pyttsx3.init()
                # Buscar voz en español
                for v in engine.getProperty('voices'):
                    if 'es' in v.id.lower():
                        engine.setProperty('voice', v.id)
                        break
                engine.setProperty('rate', 155)
                engine.say(texto)
                engine.runAndWait()
                engine.stop()
            except Exception as exc:
                print(f"[TTS] Error: {exc}")
    # Cancelar hilo anterior si sigue hablando
    if _tts_thread and _tts_thread.is_alive():
        return
    _tts_thread = threading.Thread(target=_run, daemon=True)
    _tts_thread.start()


def detectar_cruces(bal_real: list, bal_ep: list) -> list:
    """
    Devuelve lista de (índice, tipo) donde las dos curvas se cruzan.
    tipo = 'EP_SUPERA'  → la simulada pasa por encima de la real
    tipo = 'EP_CAE'     → la simulada cae por debajo de la real
    Se usa la longitud mínima de ambas curvas.
    """
    n = min(len(bal_real), len(bal_ep))
    cruces = []
    for i in range(1, n):
        diff_prev = bal_ep[i-1] - bal_real[i-1]
        diff_curr = bal_ep[i]   - bal_real[i]
        if diff_prev < 0 and diff_curr >= 0:
            cruces.append((i, 'EP_SUPERA'))
        elif diff_prev >= 0 and diff_curr < 0:
            cruces.append((i, 'EP_CAE'))
    return cruces


def _dif_a_rango(dif: float) -> str:
    if   dif <  5:  return "0-5"
    elif dif < 10:  return "5-10"
    elif dif < 15:  return "10-15"
    elif dif < 20:  return "15-20"
    elif dif < 25:  return "20-25"
    elif dif < 30:  return "25-30"
    elif dif < 35:  return "30-35"
    elif dif < 40:  return "35-40"
    elif dif < 45:  return "40-45"
    elif dif < 50:  return "45-50"
    else:           return "+50"


def mult_por_confianza(conf: float) -> int:
    return ep_mult(conf)


# ── Parser ─────────────────────────────────────────────────────────────────────
def parsear_archivo(ruta: Path) -> list:
    ops = []
    if not ruta.exists():
        return ops
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            lineas = f.readlines()
        for linea in lineas:
            linea = linea.strip()
            if not linea:
                continue

            # Formato nuevo: RANGO: 5-10 | MODO: DIRECTO | ACIERTO: True
            if 'RANGO:' in linea and 'MODO:' in linea and 'ACIERTO:' in linea:
                m_rango = re.search(r'RANGO:\s*([0-9+\-]+)', linea)
                m_modo  = re.search(r'MODO:\s*(DIRECTO|INVERSO)', linea)
                m_ac    = re.search(r'ACIERTO:\s*(True|False)', linea)
                if m_rango and m_modo and m_ac:
                    ops.append({
                        'rango': m_rango.group(1),
                        'modo':  m_modo.group(1),
                        'ganada': m_ac.group(1) == 'True',
                    })
                continue

            # Formato antiguo: [*] RESULTADO: BLUE | MayorGana: True | Rango: 30-35 | Modo: DIRECTO
            if 'RESULTADO:' in linea:
                m_rango = re.search(r'Rango:\s*([0-9+\-]+)', linea)
                m_dif   = re.search(r'[Dd]if[:\s]+([\d.]+)', linea)
                if m_rango:
                    rango = m_rango.group(1)
                elif m_dif:
                    rango = _dif_a_rango(float(m_dif.group(1)))
                else:
                    continue
                if 'Modo: DIRECTO' in linea:
                    modo = 'DIRECTO'
                elif 'Modo: INVERSO' in linea:
                    modo = 'INVERSO'
                else:
                    continue
                ganada = 'MayorGana: True' in linea
                ops.append({'rango': rango, 'modo': modo, 'ganada': ganada})
    except Exception as e:
        print(f"Error parseando: {e}")
    return ops


def parsear_historial_rondas(ruta: Path) -> list:
    """Lee historial_rondas.txt (JSON por linea) y devuelve ops con acierto real."""
    import json as _json
    ops = []
    if not ruta.exists():
        return ops
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            lineas = f.readlines()
        for linea in lineas:
            linea = linea.strip()
            if not linea or '{' not in linea:
                continue
            try:
                entry = _json.loads(linea[linea.index('{'):])
            except Exception:
                continue
            modo    = entry.get('modo', 'SKIP')
            acierto = entry.get('acierto')
            rango   = entry.get('rango', '')
            # Solo entradas con resultado real (no SKIP ni pendiente)
            if modo in ('SKIP', None, '---') or acierto is None or not rango:
                continue
            modo_norm = 'DIRECTO' if 'DIRECTO' in str(modo).upper() else (
                        'INVERSO' if 'INVERSO' in str(modo).upper() else None)
            if not modo_norm:
                continue
            pnl_real = entry.get('pnl', None)
            mult_real = entry.get('mult', None)
            ops.append({'rango': rango, 'modo': modo_norm, 'ganada': bool(acierto),
                        'pnl_real': pnl_real, 'mult_real': mult_real})
    except Exception as e:
        print(f"Error parseando historial: {e}")
    return ops


def parsear_live_history(ruta: Path) -> list:
    """Lee pnl_live_history.json y devuelve ops. Regenera desde raw si ops está vacío."""
    import json as _json
    ops = []
    if not ruta.exists():
        return ops
    try:
        with open(ruta, 'r', encoding='utf-8') as f:
            data = _json.load(f)
        ops_data = data.get('ops', [])
        raw_data = data.get('raw', [])

        # 🔧 Si ops está vacío pero hay raw events, regenerar ops desde raw
        if not ops_data and raw_data:
            ops_data = []
            for ev in raw_data:
                acierto = ev.get('acierto')
                wr = ev.get('wr', 50.0)
                modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
                ops_data.append({
                    'skip': modo == 'SKIP',
                    'acierto': acierto,
                    'rango': ev.get('rango', '0-5'),
                    'modo': modo,
                    'wr': wr,
                })

        for entry in ops_data:
            # 🔧 Incluir TODAS las operaciones (incluso SKIP)
            rango = entry.get('rango', '')
            modo = entry.get('modo', 'DIRECTO')
            acierto = entry.get('acierto')
            if not rango or acierto is None:
                continue
            ops.append({
                'rango': rango,
                'modo': modo,
                'ganada': bool(acierto),
                'mult': float(entry.get('mult', 1)),
            })
    except Exception as e:
        print(f"Error parseando live history: {e}")
    return ops


def parsear_decision_history(ruta: Path) -> list:
    """Lee pnl_decision_history.json y devuelve ops con pnl_real=pnl_base.
    Produce la misma curva que Gráfica BASE del dashboard."""
    import json as _json
    ops = []
    if not ruta.exists():
        return ops
    try:
        decs = _json.loads(ruta.read_text(encoding='utf-8'))
        for d in decs:
            if d.get('winner') is None:
                continue
            pnl_b = d.get('pnl_base')
            if pnl_b is None:
                continue
            modo = d.get('modo', 'DIRECTO')
            # ganada = raw "ganó la mayoría" — igual que el resto de parsers.
            # DIRECTO gana cuando mayoría gana (pnl_b > 0).
            # INVERSO gana cuando minoría gana = mayoría pierde (pnl_b < 0).
            ganada_raw = (pnl_b > 0) if modo == 'DIRECTO' else (pnl_b < 0)
            ops.append({
                'rango':    d.get('rango', '?'),
                'modo':     modo,
                'ganada':   ganada_raw,
                'mult':     float(d.get('mult') or 1),
                'pnl_real': pnl_b,
            })
    except Exception as e:
        print(f"[EP] Error parseando decisions: {e}")
    return ops


def parsear_filtros_decisiones(ruta: Path) -> dict:
    """Lee pnl_decision_history.json y calcula curvas PnL por filtro usando curva_pnl().
    Retorna {idx_filtro: {'curva': [float,...], 'ops': int, 'ac': int, 'pnl': float, 'wr': float, 'none_filter': bool}}
    Los filtros EP (13-15) tienen none_filter=True."""
    import json as _json
    from pnl_data import curva_pnl, curva_pnl_ep, curva_pnl_umbral
    from pnl_config import EP_UMBRAL_MIN

    if not ruta.exists():
        return {}

    try:
        decs = _json.loads(ruta.read_text(encoding='utf-8'))
    except Exception:
        return {}

    # Convertir decisiones a ops para curva_pnl (misma lógica que pnl_dashboard)
    ops = []
    for d in decs:
        if d.get('winner') is None:
            continue
        wr = float(d.get('wr') or 50)
        modo_raw = d.get('modo', 'SKIP')
        # Convertir BASE a modo teórico según WR
        if modo_raw == 'BASE':
            modo = 'DIRECTO' if wr >= 60 else ('INVERSO' if wr <= 40 else 'SKIP')
        else:
            modo = modo_raw
        # Derivar "acierto" como "ganó la mayoría" (independiente del modo)
        # Esto es lo que curva_pnl() espera: ella misma invierte para modo INVERSO
        mayor = (d.get('mayor') or '').lower()
        winner = (d.get('winner') or '').lower()
        gano_mayoria = bool(mayor and winner and mayor == winner)
        skip = (modo == 'SKIP')  # solo saltar si no hay dirección clara (WR 40-60)
        op = {
            'skip':    skip,
            'acierto': gano_mayoria,
            'modo':    modo,
            'rango':   d.get('rango', '?'),
            'est':     d.get('est', 'ESTABLE'),
            'acel':    float(d.get('acel') or 0),
            'wr':      wr,
            'mult':    float(d.get('mult') or 1),
        }
        ops.append(op)

    if not ops:
        return {}

    n_filtros = len(FILTROS_CURVA)
    result = {}
    for i in range(n_filtros):
        entry = FILTROS_CURVA[i]
        filtro_fn = entry[2]
        contrarian = entry[3]
        raw = entry[4]

        if filtro_fn is None:
            # EP ADAPTATIVO
            curva, n_ac, n_total, _ = curva_pnl_ep(ops, contrarian=contrarian)
        elif filtro_fn == 'EP_WR70':
            curva, n_ac, n_total, _ = curva_pnl_ep(ops, min_wr_dir=70, contrarian=contrarian)
        elif filtro_fn == 'EP_UMBRAL':
            # Curva REAL: acumular pnl efectivo de las decisiones jugadas con filtro EP UMBRAL.
            curva, n_ac, n_total = [], 0, 0
            acum = 0.0
            for d in decs:
                if d.get('winner') is None or d.get('decision') == 'SKIP':
                    continue
                if d.get('filtro') != 'EP UMBRAL':
                    continue
                pnl_d = float(d.get('pnl') or 0)
                acum += pnl_d
                curva.append(acum)
                n_total += 1
                if d.get('acierto'):
                    n_ac += 1
        elif filtro_fn == 'BAL_FILTRO':
            # Balance real de sesión: acumular pnl efectivo desde decisiones (no SKIP)
            curva, n_ac, n_total = [], 0, 0
            acum = 0.0
            for d in decs:
                if d.get('winner') is None or d.get('decision') == 'SKIP':
                    continue
                pnl_d = float(d.get('pnl') or 0)
                acum += pnl_d
                curva.append(acum)
                n_total += 1
                if d.get('acierto'):
                    n_ac += 1
        elif isinstance(filtro_fn, str):
            result[i] = {'curva': [], 'ops': 0, 'ac': 0, 'pnl': 0.0, 'wr': 0.0, 'none_filter': True}
            continue
        else:
            curva, n_ac, n_total = curva_pnl(ops, filtro_fn, contrarian=contrarian, raw=raw)

        pnl_v = curva[-1] if curva else 0.0
        wr_v = (n_ac / n_total * 100) if n_total else 0.0
        result[i] = {
            'curva': curva, 'ops': n_total, 'ac': n_ac,
            'pnl': pnl_v, 'wr': wr_v, 'none_filter': False
        }
    return result


# ── Simulacion ─────────────────────────────────────────────────────────────────
def simular(ops: list, ventana: int = VENTANA) -> dict:
    # --- Ambas curvas usan EP: ventana rolling + multiplicador por WR ---
    ventanas = {}          # {rango: {modo: deque(maxlen=VENTANA)}}
    bal_real = [0.0]       # todas las apuestas con mult EP (sin filtro de umbral)
    bal_ep   = [0.0]       # solo apuestas que pasan WR >= UMBRAL_ESTADO, con mult EP
    n_bets   = 0
    n_skips  = 0
    detalles = []

    for op in ops:
        rango  = op['rango']
        modo   = op['modo']
        ganada = op['ganada']

        if rango not in ventanas:
            ventanas[rango] = {}
        if modo not in ventanas[rango]:
            ventanas[rango][modo] = deque(maxlen=ventana)

        v   = ventanas[rango][modo]
        n_v = len(v)

        # ganada_modo = outcome real de EP: ajusta INVERSO (la ventana guarda WR de EP)
        ganada_modo = ganada if modo == 'DIRECTO' else not ganada

        wr_v  = sum(v) / n_v * 100 if n_v >= MIN_OPS else 0.0
        mult  = mult_por_confianza(wr_v) if n_v >= MIN_OPS else 1
        pnl_base = (PNL_ACIERTO if ganada_modo else PNL_FALLO) * mult

        # Ambas curvas usan el mismo filtro WR >= UMBRAL_ESTADO
        apostar = n_v >= MIN_OPS and wr_v >= UMBRAL_ESTADO
        nivel   = ('BUENO'     if wr_v >= UMBRAL_ESTADO    else
                   'PRIORIDAD' if wr_v >= UMBRAL_PRIORIDAD else
                   'BAJA'      if n_v  >= MIN_OPS          else 'SKIP')

        # Curva real: PNL del registro original si existe, si no usa mult real guardado
        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            _m_real = op.get('mult', 1)
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO) * _m_real)

        # Curva simulada: EP filtro + mult EP recalculado
        if apostar:
            bal_ep.append(bal_ep[-1] + pnl_base)
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        detalles.append({
            'rango': rango, 'modo': modo, 'ganada': ganada,
            'wr_v': wr_v, 'nivel': nivel, 'apostar': apostar,
            'n_v': n_v,
            'modo_efectivo': modo if apostar else 'SKIP',
        })

        v.append(1 if ganada_modo else 0)   # WR real de EP en esta dirección

    # --- Stats globales: TODOS los registros por rango+modo ---
    stats_globales = {}
    for op in ops:
        r, m, g = op['rango'], op['modo'], op['ganada']
        if r not in stats_globales:
            stats_globales[r] = {}
        if m not in stats_globales[r]:
            stats_globales[r][m] = {'ops': 0, 'ganadas': 0}
        stats_globales[r][m]['ops'] += 1
        if g:
            stats_globales[r][m]['ganadas'] += 1

    # --- Stats ventana final (ultimos 50 de cada rango+modo) ---
    stats_ventana = {}
    for rango, modos in ventanas.items():
        stats_ventana[rango] = {}
        for modo, dq in modos.items():
            n = len(dq)
            stats_ventana[rango][modo] = {
                'n':  n,
                'wr': sum(dq) / n * 100 if n > 0 else 0
            }

    # --- Conteo de bets por nivel ---
    niveles = {'BUENO': 0, 'PRIORIDAD': 0, 'BAJA': 0, 'SKIP': 0}
    for d in detalles:
        niveles[d['nivel']] = niveles.get(d['nivel'], 0) + 1

    return {
        'bal_real':       bal_real,
        'bal_ep':         bal_ep,
        'n_bets':         n_bets,
        'n_skips':        n_skips,
        'n_total':        len(ops),
        'stats_globales': stats_globales,
        'stats_ventana':  stats_ventana,
        'niveles':        niveles,
        'saldo_real':     bal_real[-1],
        'saldo_ep':       bal_ep[-1],
        'detalles':       detalles,
    }


def simular_calibrado(ops_base: list, ops_live: list, ventana: int = VENTANA) -> dict:
    """
    Precalienta las ventanas rolling con ops_base (REC) y simula EP solo sobre ops_live.
    Permite ver si los patrones históricos se mantienen en los datos en vivo.
    """
    ventanas = {}

    # ── Fase 1: calentar ventanas con el histórico (sin generar curvas) ──────
    for op in ops_base:
        rango      = op['rango']
        modo       = op['modo']
        ganada     = op['ganada']
        ganada_modo = ganada if modo == 'DIRECTO' else not ganada
        if rango not in ventanas:
            ventanas[rango] = {}
        if modo not in ventanas[rango]:
            ventanas[rango][modo] = deque(maxlen=ventana)
        ventanas[rango][modo].append(1 if ganada_modo else 0)

    # ── Fase 2: simular sobre ops_live con ventanas precalentadas ────────────
    bal_real = [0.0]
    bal_ep   = [0.0]
    n_bets   = 0
    n_skips  = 0
    detalles = []

    for op in ops_live:
        rango      = op['rango']
        modo       = op['modo']
        ganada     = op['ganada']
        ganada_modo = ganada if modo == 'DIRECTO' else not ganada

        if rango not in ventanas:
            ventanas[rango] = {}
        if modo not in ventanas[rango]:
            ventanas[rango][modo] = deque(maxlen=ventana)

        v   = ventanas[rango][modo]
        n_v = len(v)

        wr_v     = sum(v) / n_v * 100 if n_v >= MIN_OPS else 0.0
        mult     = mult_por_confianza(wr_v) if n_v >= MIN_OPS else 1
        pnl_base = (PNL_ACIERTO if ganada_modo else PNL_FALLO) * mult

        apostar = n_v >= MIN_OPS and wr_v >= UMBRAL_ESTADO
        nivel   = ('BUENO'     if wr_v >= UMBRAL_ESTADO    else
                   'PRIORIDAD' if wr_v >= UMBRAL_PRIORIDAD else
                   'BAJA'      if n_v  >= MIN_OPS          else 'SKIP')

        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            _m_real = op.get('mult', 1)
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO) * _m_real)

        if apostar:
            bal_ep.append(bal_ep[-1] + pnl_base)
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        detalles.append({
            'rango': rango, 'modo': modo, 'ganada': ganada,
            'wr_v': wr_v, 'nivel': nivel, 'apostar': apostar, 'n_v': n_v,
            'modo_efectivo': modo if apostar else 'SKIP',
        })
        v.append(1 if ganada_modo else 0)

    # ── Stats ─────────────────────────────────────────────────────────────────
    stats_globales = {}
    for op in ops_live:
        r, m, g = op['rango'], op['modo'], op['ganada']
        if r not in stats_globales: stats_globales[r] = {}
        if m not in stats_globales[r]: stats_globales[r][m] = {'ops': 0, 'ganadas': 0}
        stats_globales[r][m]['ops'] += 1
        if g if m == 'DIRECTO' else not g:
            stats_globales[r][m]['ganadas'] += 1

    stats_ventana = {}
    for rango, modos in ventanas.items():
        stats_ventana[rango] = {}
        for modo, dq in modos.items():
            n = len(dq)
            stats_ventana[rango][modo] = {'n': n, 'wr': sum(dq) / n * 100 if n > 0 else 0}

    niveles = {'BUENO': 0, 'PRIORIDAD': 0, 'BAJA': 0, 'SKIP': 0}
    for d in detalles:
        niveles[d['nivel']] = niveles.get(d['nivel'], 0) + 1

    return {
        'bal_real':       bal_real,
        'bal_ep':         bal_ep,
        'n_bets':         n_bets,
        'n_skips':        n_skips,
        'n_total':        len(ops_live),
        'stats_globales': stats_globales,
        'stats_ventana':  stats_ventana,
        'niveles':        niveles,
        'saldo_real':     bal_real[-1],
        'saldo_ep':       bal_ep[-1],
        'detalles':       detalles,
    }


def simular_umbral(ops: list) -> dict:
    """
    Simulación SIN lookahead alineada con la lógica live (EP GATE) en pnl_dashboard:
    - Mantiene stats acumuladas por (rango, modo) solo con ops PASADAS.
    - En cada op decide con la información disponible HASTA ese momento (no usa el dataset completo).
    - Mínimo 5 ops por (rango, modo) para considerar el WR (igual que _get_wr_rango).
    - Umbral WR ≥ UMBRAL_ESTADO (EP_UMBRAL_MIN, 62 por defecto) en alguno de los dos modos.
    """
    _UMBRAL_MIN_OPS = 5   # mismo umbral que el live (pnl_dashboard._get_wr_rango)

    stats = {}            # {rango: {modo: {ops, ganadas}}}
    bal_real = [0.0]
    bal_ep   = [0.0]
    n_bets   = 0
    n_skips  = 0
    detalles = []

    for op in ops:
        rango  = op['rango']
        modo   = op['modo']
        ganada = op['ganada']

        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO))

        # Decidir con stats ANTERIORES a esta op (sin lookahead).
        modos_stats = stats.get(rango, {})
        d = modos_stats.get('DIRECTO', {})
        i = modos_stats.get('INVERSO',  {})
        d_wr = d['ganadas'] / d['ops'] * 100 if d.get('ops', 0) >= _UMBRAL_MIN_OPS else None
        i_wr = i['ganadas'] / i['ops'] * 100 if i.get('ops', 0) >= _UMBRAL_MIN_OPS else None
        cand_d = d_wr if (d_wr is not None and d_wr >= UMBRAL_ESTADO) else None
        cand_i = i_wr if (i_wr is not None and i_wr >= UMBRAL_ESTADO) else None

        if cand_d is None and cand_i is None:
            best, wr = None, 0
        elif cand_d is not None and (cand_i is None or cand_d >= cand_i):
            best, wr = 'DIRECTO', cand_d
        else:
            best, wr = 'INVERSO', cand_i

        if best is None:
            bal_ep.append(bal_ep[-1])
            n_skips += 1
            modo_ef = 'SKIP'
        else:
            mult_real = op.get('mult_real')
            if mult_real is not None and mult_real > 0:
                mult = mult_real
            else:
                mult = mult_por_confianza(wr)
            resultado = ganada if best == modo else not ganada
            pnl = (PNL_ACIERTO if resultado else PNL_FALLO) * mult
            bal_ep.append(bal_ep[-1] + pnl)
            n_bets += 1
            modo_ef = best

        # Actualizar stats DESPUÉS de decidir (la op actual no se ve a sí misma).
        if rango not in stats:
            stats[rango] = {}
        if modo not in stats[rango]:
            stats[rango][modo] = {'ops': 0, 'ganadas': 0}
        stats[rango][modo]['ops'] += 1
        if ganada:
            stats[rango][modo]['ganadas'] += 1

        detalles.append({
            'rango': rango, 'modo': modo, 'ganada': ganada,
            'modo_efectivo': modo_ef,
        })

    # Construir mejor_modo final (snapshot al cierre — solo informativo).
    mejor_modo = {}
    for rango, modos in stats.items():
        d = modos.get('DIRECTO', {})
        i = modos.get('INVERSO',  {})
        d_wr_f = d['ganadas'] / d['ops'] * 100 if d.get('ops', 0) >= _UMBRAL_MIN_OPS else 0
        i_wr_f = i['ganadas'] / i['ops'] * 100 if i.get('ops', 0) >= _UMBRAL_MIN_OPS else 0
        if d_wr_f >= UMBRAL_ESTADO or i_wr_f >= UMBRAL_ESTADO:
            mejor_modo[rango] = ('DIRECTO', d_wr_f) if d_wr_f >= i_wr_f else ('INVERSO', i_wr_f)
        else:
            mejor_modo[rango] = (None, 0)

    return {
        'bal_real':       bal_real,
        'bal_ep':         bal_ep,
        'n_bets':         n_bets,
        'n_skips':        n_skips,
        'n_total':        len(ops),
        'stats_globales': stats,
        'stats_ventana':  {},
        'niveles':        {},
        'saldo_real':     bal_real[-1],
        'saldo_ep':       bal_ep[-1],
        'mejor_modo':     mejor_modo,
        'detalles':       detalles,
    }


def simular_combinado(ops: list, ventana: int = VENTANA) -> dict:
    """
    Combina UMBRAL + VENTANA:
    - UMBRAL acumulado (sin look-ahead): decide el modo base segun historia vista hasta ese momento
    - VENTANA rolling: confirma que el modo base sigue activo recientemente
    - Si ambos coinciden -> apuesta
    - Si discrepan     -> SKIP
    - Si solo hay datos historicos (ventana insuficiente) -> usa solo el historico
    """
    acum    = {}   # stats acumuladas {rango: {modo: {ops, ganadas}}}
    ventanas = {}  # rolling window   {rango: {modo: deque}}

    bal_real = [0.0]
    bal_ep   = [0.0]
    n_bets   = 0
    n_skips  = 0
    detalles = []

    def wr_acum(r, m):
        b = acum.get(r, {}).get(m, {})
        o = b.get('ops', 0)
        return b['ganadas'] / o * 100 if o >= MIN_OPS else None

    def wr_vent(r, m):
        v = ventanas.get(r, {}).get(m, deque())
        n = len(v)
        return sum(v) / n * 100 if n >= MIN_OPS else None

    for op in ops:
        rango  = op['rango']
        modo   = op['modo']
        ganada = op['ganada']

        # --- Inicializar estructuras ---
        if rango not in acum:
            acum[rango] = {}
        if modo not in acum[rango]:
            acum[rango][modo] = {'ops': 0, 'ganadas': 0}
        if rango not in ventanas:
            ventanas[rango] = {}
        if modo not in ventanas[rango]:
            ventanas[rango][modo] = deque(maxlen=ventana)

        # --- Leer estado ANTES de añadir el resultado actual ---
        d_hist = wr_acum(rango, 'DIRECTO')
        i_hist = wr_acum(rango, 'INVERSO')
        d_vent = wr_vent(rango, 'DIRECTO')
        i_vent = wr_vent(rango, 'INVERSO')

        # --- Decisión modo base por historico acumulado ---
        if d_hist is not None or i_hist is not None:
            dh = d_hist or 0
            ih = i_hist or 0
            best_hist = 'DIRECTO' if dh >= ih else 'INVERSO'
            best_hist_wr = max(dh, ih)
        else:
            best_hist = None
            best_hist_wr = 0

        # --- Decisión por ventana rolling ---
        if d_vent is not None or i_vent is not None:
            dv = d_vent or 0
            iv = i_vent or 0
            best_vent = 'DIRECTO' if dv >= iv else 'INVERSO'
            best_vent_wr = max(dv, iv)
        else:
            best_vent = None
            best_vent_wr = 0

        # --- Combinar ---
        if best_hist is None:
            # Sin datos históricos → SKIP
            apostar = False
            best_mode = None
            wr_final = 0
        elif best_vent is None:
            # Solo historico, ventana insuficiente → usar historico si pasa umbral
            apostar   = best_hist_wr >= UMBRAL_ESTADO
            best_mode = best_hist
            wr_final  = best_hist_wr
        elif best_hist == best_vent:
            # Ambos coinciden → apuesta con WR de ventana (más reciente)
            wr_final  = best_vent_wr
            apostar   = wr_final >= UMBRAL_ESTADO
            best_mode = best_hist
        else:
            # Discrepan → SKIP (señal contradictoria)
            apostar   = False
            best_mode = None
            wr_final  = 0

        # --- Curva real ---
        pnl_orig = op.get('pnl_real')
        if pnl_orig is not None:
            bal_real.append(bal_real[-1] + pnl_orig)
        else:
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if ganada else PNL_FALLO))

        # --- Curva simulada combinada ---
        if apostar and best_mode:
            # Usar mult real si está disponible, si no usar teórico
            mult_real = op.get('mult_real')
            if mult_real is not None and mult_real > 0:
                mult = mult_real
            else:
                mult = mult_por_confianza(wr_final)
            resultado = ganada if best_mode == modo else not ganada
            pnl       = (PNL_ACIERTO if resultado else PNL_FALLO) * mult
            bal_ep.append(bal_ep[-1] + pnl)
            n_bets += 1
        else:
            bal_ep.append(bal_ep[-1])
            n_skips += 1

        detalles.append({'rango': rango, 'modo': modo, 'ganada': ganada,
                         'best_mode': best_mode, 'apostar': apostar,
                         'modo_efectivo': best_mode if apostar and best_mode else 'SKIP'})

        # --- Actualizar estructuras con el resultado actual ---
        acum[rango][modo]['ops']     += 1
        if ganada:
            acum[rango][modo]['ganadas'] += 1
        ventanas[rango][modo].append(1 if ganada else 0)

    # Stats globales
    stats_globales = {}
    for op in ops:
        r, m, g = op['rango'], op['modo'], op['ganada']
        if r not in stats_globales: stats_globales[r] = {}
        if m not in stats_globales[r]: stats_globales[r][m] = {'ops': 0, 'ganadas': 0}
        stats_globales[r][m]['ops'] += 1
        if g: stats_globales[r][m]['ganadas'] += 1

    stats_ventana = {}
    for rango, modos in ventanas.items():
        stats_ventana[rango] = {}
        for m, dq in modos.items():
            n = len(dq)
            stats_ventana[rango][m] = {'n': n, 'wr': sum(dq)/n*100 if n > 0 else 0}

    return {
        'bal_real':       bal_real,
        'bal_ep':         bal_ep,
        'n_bets':         n_bets,
        'n_skips':        n_skips,
        'n_total':        len(ops),
        'stats_globales': stats_globales,
        'stats_ventana':  stats_ventana,
        'niveles':        {},
        'saldo_real':     bal_real[-1],
        'saldo_ep':       bal_ep[-1],
        'detalles':       detalles,
    }


# ── Tabla de filtros con Canvas ────────────────────────────────────────────────

class FiltroTable(tk.Frame):
    """Widget de tabla de filtros con Canvas: indicador, nombre, PNL, OPS, WR.
    Lee anchos desde COLUMNAS de Pk_Arena (sección ESTRATEGIA)."""

    HDR_H = 22
    ROW_H = 20
    HDR_BG = '#060E1C'
    HDR_FG = '#4A6080'
    SEP_CLR = '#0D2137'
    BDR_CLR = '#00D4FF'
    COL_DEFS = [
        ('IND',     'IND',    40, 'c'),
        ('NOMBRE', 'NOMBRE', 75, 'w'),
        ('PNL',     'PNL',    76, 'c'),
        ('SALDO',  'SALDO',  65, 'c'),
        ('OPS',     'OPS',    28, 'c'),
        ('WR',      'WR',     34, 'c'),
    ]

    def __init__(self, parent, on_click=None, **kw):
        super().__init__(parent, bg='#0A1628', **kw)
        self._on_click = on_click
        self._filtros_data = {}
        self._filtros_seleccionados: set[int] = set()

        # Ajustar anchos mínimos según texto del encabezado
        import tkinter.font as tkfont
        fnt = tkfont.Font(family='Consolas', size=7, weight='bold')
        HDR_PAD = 10
        self._col_defs = [
            (key, label,
             max(col_w, fnt.measure(label) + HDR_PAD if label else col_w), align)
            for key, label, col_w, align in self.COL_DEFS
        ]
        self.TOTAL_W = sum(c[2] for c in self._col_defs)

        # Canvas
        self._cv = tk.Canvas(self, bg='#0A1628', highlightthickness=0,
                             width=self.TOTAL_W)
        vsb = tk.Scrollbar(self, orient='vertical', command=self._cv.yview)
        self._cv.configure(yscrollcommand=vsb.set)

        vsb.pack(side='right', fill='y')
        self._cv.pack(side='left', fill='both', expand=True)

        self._cv.bind('<MouseWheel>', self._on_scroll)
        self._cv.bind('<Button-4>',   self._on_scroll)
        self._cv.bind('<Button-5>',   self._on_scroll)
        self._cv.bind('<Button-1>',   self._on_click_canvas)

        self._dibujar_header()

    def _on_scroll(self, ev):
        delta = -1 if (getattr(ev, 'num', 0) == 5 or getattr(ev, 'delta', 0) < 0) else 1
        self._cv.yview_scroll(delta, 'units')

    def _dibujar_header(self):
        self._cv.delete('header')
        x = 0
        self._cv.create_rectangle(0, 0, self.TOTAL_W, self.HDR_H,
                                   fill=self.HDR_BG, outline='', tags='header')
        for key, label, col_w, align in self._col_defs:
            if label:
                tx = x + col_w // 2
                self._cv.create_text(tx, self.HDR_H // 2, text=label,
                                      fill=self.HDR_FG,
                                      font=('Consolas', 7, 'bold'),
                                      anchor='center', tags='header')
            self._cv.create_line(x + col_w, 0, x + col_w, self.HDR_H,
                                  fill=self.SEP_CLR, width=1, tags='header')
            x += col_w
        self._cv.create_line(0, self.HDR_H, self.TOTAL_W, self.HDR_H,
                              fill=self.BDR_CLR, width=1, tags='header')

    def actualizar_anchos(self, widths_dict):
        """Aplica anchos desde Sheets y redibuja."""
        import tkinter.font as tkfont
        fnt = tkfont.Font(family='Consolas', size=7, weight='bold')
        HDR_PAD = 10
        self._col_defs = [
            (key, label,
             max(widths_dict.get(key, col_w),
                 fnt.measure(label) + HDR_PAD if label else 0), align)
            for key, label, col_w, align in self.COL_DEFS
        ]
        self.TOTAL_W = sum(c[2] for c in self._col_defs)
        self._cv.configure(width=self.TOTAL_W)
        self._dibujar_header()
        self.set_data(self._filtros_data, self._filtros_seleccionados)

    def _on_click_canvas(self, ev):
        y = self._cv.canvasy(ev.y)
        if y < self.HDR_H:
            return
        row_idx = int((y - self.HDR_H) // self.ROW_H)
        if self._on_click and row_idx < len(self._rows_data):
            self._on_click(self._rows_data[row_idx])

    def set_data(self, filtros_data: dict, seleccionados: set):
        from pnl_config import FILTROS_CURVA
        self._filtros_data = filtros_data
        self._filtros_seleccionados = seleccionados
        self._cv.delete('row')

        n = len(FILTROS_CURVA)
        total_h = self.HDR_H + n * self.ROW_H + 4
        self._rows_data = []

        for i in range(n):
            nombre = FILTROS_CURVA[i][0]
            color  = FILTROS_CURVA[i][1]
            fd     = filtros_data.get(i, {})

            y = self.HDR_H + i * self.ROW_H
            self._rows_data.append(i)

            if fd.get('none_filter', True):
                bg = '#0A1628'
                fg_text = '#666666'
                indicator = '○'
                ind_color = '#555555'
                pnl_txt = 'N/A'
                saldo_txt = '--'
                ops_txt = '--'
                wr_txt = '--'
            else:
                activo = i in seleccionados
                indicator = '■' if activo else '○'
                ind_color = color if activo else '#555555'
                pnl_v = fd.get('pnl', 0.0)
                pnl_txt = f"{pnl_v:+.2f}€"
                saldo_v = fd.get('pnl', 0.0)
                saldo_txt = f"{saldo_v:+.2f}€"
                ops_txt = str(fd.get('ops', 0))
                wr_v = fd.get('wr', 0.0)
                wr_txt = f"{wr_v:.1f}%" if wr_v else '0%'
                fg_text = '#00FF88' if pnl_v >= 0 else '#FF3366'
                if not activo:
                    fg_text = '#4A6080'
                bg = '#0D2137' if activo else '#0A1628'

            # Fondo de fila
            self._cv.create_rectangle(0, y, self.TOTAL_W, y + self.ROW_H,
                                       fill=bg, outline='', tags='row')
            self._cv.create_line(0, y + self.ROW_H, self.TOTAL_W, y + self.ROW_H,
                                  fill=self.SEP_CLR, width=1, tags='row')

            # Mapa de valores por columna
            vals = {'PNL': pnl_txt, 'SALDO': saldo_txt, 'OPS': ops_txt, 'WR': wr_txt}
            x = 0
            row_font = ('Consolas', 8)
            for key, label, col_w, align in self._col_defs:
                cy = y + self.ROW_H // 2

                if key == 'IND':
                    tx = x + col_w // 2
                    self._cv.create_text(tx, cy, text=indicator,
                                          fill=ind_color,
                                          font=('Consolas', 10),
                                          anchor='center', tags='row')
                elif key == 'NOMBRE':
                    tx = x + 6
                    self._cv.create_text(tx, cy, text=nombre,
                                          fill=fg_text,
                                          font=row_font,
                                          anchor='w', tags='row')
                else:
                    tx = x + col_w // 2
                    self._cv.create_text(tx, cy, text=vals.get(key, ''),
                                          fill=fg_text,
                                          font=row_font,
                                          anchor='center', tags='row')

                self._cv.create_line(x + col_w, y, x + col_w, y + self.ROW_H,
                                      fill=self.SEP_CLR, width=1, tags='row')
                x += col_w

        self._cv.configure(scrollregion=(0, 0, self.TOTAL_W, total_h))
        self._cv.yview_moveto(0)


# ── UI ─────────────────────────────────────────────────────────────────────────
class AppEstrategiaPerfecta:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ESTRATEGIA PERFECTA — Simulador")
        self.root.configure(bg=C['bg'])
        self._restaurar_posicion()
        self.root.protocol("WM_DELETE_WINDOW", self._cerrar)
        self.root.bind('<Control-c>', lambda e: self._cerrar())
        self.root.bind('<Control-C>', lambda e: self._cerrar())

        self._fuente_activa  = 'reconstructor'
        self._loop_activo    = False
        self._loop_job       = None
        self._ventana_actual = VENTANA
        self._anim_job = None
        self._voz_activa = False
        # Estado de filtros
        self._filtros_seleccionados: set[int] = set()
        self._filtros_data: dict = {}
        self._mostrar_filtros: bool = True
        self._construir_ui()
        # Inicializar tabla de cambios vacía
        for row in self._tree_cambios.get_children():
            self._tree_cambios.delete(row)
        self._lbl_cambios_count.config(text="0")
        # Iniciar loop automáticamente
        self._toggle_loop()

    def _restaurar_posicion(self):
        try:
            if ARCHIVO_POS.exists():
                with open(ARCHIVO_POS, 'r') as f:
                    pos = json.load(f)
                self.root.geometry(f"{pos['w']}x{pos['h']}+{pos['x']}+{pos['y']}")
                return
        except:
            pass
        self.root.geometry("1450x1050")

    def _cerrar(self):
        try:
            with open(ARCHIVO_POS, 'w') as f:
                json.dump({'w': self.root.winfo_width(), 'h': self.root.winfo_height(),
                           'x': self.root.winfo_x(), 'y': self.root.winfo_y()}, f)
        except:
            pass
        self.root.destroy()

    def _construir_ui(self):
        # ── Header ──
        hf = tk.Frame(self.root, bg='#020810')
        hf.pack(fill='x')
        tk.Frame(hf, bg=C['accent'], height=2).pack(fill='x')
        tk.Label(hf, text="  ESTRATEGIA PERFECTA — Simulador de Backtesting",
                 font=('Consolas', 12, 'bold'), bg='#020810', fg=C['accent'],
                 pady=8).pack(side='left')

        tk.Button(hf, text="RECARGAR", font=('Consolas', 9, 'bold'),
                  bg=C['panel'], fg=C['warn'], relief='flat', padx=12,
                  command=self.cargar_y_dibujar).pack(side='right', padx=10, pady=6)

        self._lbl_fuente = tk.Label(hf, text="", font=('Consolas', 8),
                                    bg='#020810', fg=C['muted'], padx=8)
        self._lbl_fuente.pack(side='right')

        # ── Cruces display (top right) ──
        self._lbl_cruces_frame = tk.Frame(hf, bg='#020810')
        self._lbl_cruces_frame.pack(side='right', padx=10)
        self._lbl_cruces_arriba = tk.Label(self._lbl_cruces_frame, text="▲ 0",
                                          font=('Consolas', 9, 'bold'), bg='#020810', fg=C['green'], padx=6)
        self._lbl_cruces_arriba.pack(side='left')
        self._lbl_cruces_abajo = tk.Label(self._lbl_cruces_frame, text="▼ 0",
                                          font=('Consolas', 9, 'bold'), bg='#020810', fg=C['red'], padx=6)
        self._lbl_cruces_abajo.pack(side='left')
        self._lbl_cruces_total = tk.Label(self._lbl_cruces_frame, text="Σ 0",
                                          font=('Consolas', 9, 'bold'), bg='#020810', fg=C['accent'], padx=6)
        self._lbl_cruces_total.pack(side='left')

        # ── Body: sidebar + contenido ──
        body = tk.Frame(self.root, bg=C['bg'])
        body.pack(fill='both', expand=True)

        # Sidebar izquierda
        sidebar = tk.Frame(body, bg=C['panel'], width=420,
                           highlightbackground=C['border'], highlightthickness=1)
        sidebar.pack(side='left', fill='y', padx=(10, 0), pady=(4, 8))
        sidebar.pack_propagate(False)

        bstyle = dict(font=('Consolas', 9, 'bold'), relief='raised', bd=1,
                      width=8, cursor='hand2')

        # Fila superior: VOZ + AUTO
        top_row = tk.Frame(sidebar, bg=C['panel'])
        top_row.pack(fill='x', pady=(10, 2), padx=4)
        self._btn_voz = tk.Button(top_row, text="VOZ", bg='#1A0A1A', fg=C['muted'], **bstyle,
                           command=self._toggle_voz)
        self._btn_voz.pack(side='left', padx=(0, 4))
        self._btn_loop = tk.Button(top_row, text="AUTO", bg='#0A2218', fg=C['green'], **bstyle,
                                   command=self._toggle_loop)
        self._btn_loop.pack(side='left')

        self._lbl_loop = tk.Label(sidebar, text="", font=('Consolas', 7),
                                  bg=C['panel'], fg=C['muted'])
        self._lbl_loop.pack(pady=0)

        # Fuentes: REC | HIST | COMB | LIVE
        src_row = tk.Frame(sidebar, bg=C['panel'])
        src_row.pack(fill='x', pady=(6, 6), padx=4)
        self._btn_rec = tk.Button(src_row, text="REC", bg='#0A2218', fg=C['accent'], **bstyle,
                  command=lambda: self._presionar_boton('REC'))
        self._btn_rec.pack(side='left', padx=(0, 2))

        self._btn_hist = tk.Button(src_row, text="HIST", bg=C['panel'], fg=C['muted'], **bstyle,
                  command=lambda: self._presionar_boton('HIST'))
        self._btn_hist.pack(side='left', padx=(0, 2))

        self._btn_comb = tk.Button(src_row, text="COMB", bg=C['panel'], fg=C['muted'], **bstyle,
                  command=lambda: self._presionar_boton('COMB'))
        self._btn_comb.pack(side='left', padx=(0, 2))

        self._btn_live = tk.Button(src_row, text="LIVE", bg='#0A1828', fg=C['accent'], **bstyle,
                  command=lambda: self._presionar_boton('LIVE'))
        self._btn_live.pack(side='left', padx=(0, 2))

        self._btn_test = tk.Button(src_row, text="TEST", bg=C['panel'], fg='#FF8C00', **bstyle,
                  command=lambda: self._presionar_boton('TEST'))
        self._btn_test.pack(side='left')

        self._btn_analisis = tk.Button(src_row, text="ANALISIS", bg='#1A0033', fg='#FF66FF', **bstyle,
                  command=self._abrir_analisis)
        self._btn_analisis.pack(side='left', padx=(2, 0))

        # Desplegable modo TEST
        test_modo_row = tk.Frame(sidebar, bg=C['panel'])
        test_modo_row.pack(fill='x', padx=4, pady=(0, 4))
        tk.Label(test_modo_row, text="MODO TEST:", font=('Consolas', 7, 'bold'),
                 bg=C['panel'], fg=C['muted']).pack(side='left', padx=(4, 2))
        self._test_modo = tk.StringVar(value='REC+LIVE')
        opt_test = tk.OptionMenu(test_modo_row, self._test_modo,
                                 'REC+LIVE', 'CALIBRADO', 'COMPARATIVA', 'EP PURO',
                                 command=lambda _: self._on_test_modo_change())
        opt_test.config(font=('Consolas', 8, 'bold'), bg=C['panel'], fg='#FF8C00',
                        activebackground=C['border'], activeforeground='#FF8C00',
                        highlightthickness=0, relief='flat', bd=0)
        opt_test['menu'].config(bg=C['panel'], fg='#FF8C00', font=('Consolas', 8))
        opt_test.pack(side='left')

        # ── Separador y tabla de filtros ──
        sep_f = tk.Frame(sidebar, bg=C['border'], height=1)
        sep_f.pack(fill='x', padx=6, pady=(2, 4))

        # Header de filtros (colapsable)
        self._filtros_header = tk.Frame(sidebar, bg=C['panel'])
        self._filtros_header.pack(fill='x', padx=8, pady=(0, 2))
        self._btn_filtros_toggle = tk.Label(self._filtros_header,
            text="FILTROS ▼", font=('Consolas', 8, 'bold'),
            bg=C['panel'], fg=C['accent'], cursor='hand2')
        self._btn_filtros_toggle.pack(side='left')
        self._btn_filtros_toggle.bind('<Button-1>', lambda e: self._toggle_filtros())
        self._lbl_filtros_count = tk.Label(self._filtros_header,
            text="0 sel", font=('Consolas', 7),
            bg=C['panel'], fg=C['muted'])
        self._lbl_filtros_count.pack(side='right')

        # Contenedor de la tabla de filtros (Canvas widget)
        self._filtros_container = tk.Frame(sidebar, bg=C['panel'])
        self._filtro_table = FiltroTable(
            self._filtros_container,
            on_click=self._on_filtro_click_canvas)
        self._filtro_table.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        self._filtros_container.pack(fill='both', expand=True, padx=4, pady=(0, 4))

        # Cargar anchos desde Sheets en background
        import threading
        def _cargar_anchos(win=self, ft=self._filtro_table):
            try:
                from configurador import conectar_excel
                ws = conectar_excel().worksheet("COLUMNAS")
                filas = ws.get_all_values()
                widths = {}
                for fila in filas:
                    if (len(fila) >= 3
                            and str(fila[0]).strip().upper() == 'ESTRATEGIA'
                            and fila[1] and fila[2]):
                        try:
                            widths[fila[1].strip()] = int(float(str(fila[2]).replace(',', '.')))
                        except ValueError:
                            pass
                if widths:
                    win.after(0, lambda w=widths: win._aplicar_anchos_filtros(w))
            except Exception as exc:
                print(f"[Sheets] Error cargando anchos: {exc}")
        threading.Thread(target=_cargar_anchos, daemon=True).start()

        # Contenido principal
        main = tk.Frame(body, bg=C['bg'])
        main.pack(side='left', fill='both', expand=True)

        # ── KPIs ──
        self._kpi_frame = tk.Frame(main, bg=C['bg'])
        self._kpi_frame.pack(fill='x', padx=10, pady=4)

        # ── Selector modo análisis + Slider ventana ──
        sf = tk.Frame(main, bg=C['bg'])
        sf.pack(fill='x', padx=10, pady=(0, 2))

        tk.Label(sf, text="ANÁLISIS:", font=('Consolas', 8, 'bold'),
                 bg=C['bg'], fg=C['muted']).pack(side='left')
        self._modo_analisis = tk.StringVar(value='VENTANA')
        opt = tk.OptionMenu(sf, self._modo_analisis, 'VENTANA', 'UMBRAL', 'COMBINADO',
                            command=self._on_modo_analisis)
        opt.config(font=('Consolas', 8, 'bold'), bg=C['panel'], fg=C['accent'],
                   activebackground=C['border'], activeforeground=C['accent'],
                   highlightthickness=0, relief='flat', bd=0)
        opt['menu'].config(bg=C['panel'], fg=C['accent'], font=('Consolas', 8))
        opt.pack(side='left', padx=6)

        self._slider_frame = tk.Frame(sf, bg=C['bg'])
        self._slider_frame.pack(side='left')
        tk.Label(self._slider_frame, text="VENTANA:", font=('Consolas', 8, 'bold'),
                 bg=C['bg'], fg=C['muted']).pack(side='left')
        self._lbl_ventana = tk.Label(self._slider_frame, text=f"{VENTANA} rondas",
                                     font=('Consolas', 8, 'bold'),
                                     bg=C['bg'], fg=C['accent'], width=10)
        self._lbl_ventana.pack(side='left', padx=6)
        self._slider_ventana = tk.Scale(
            self._slider_frame, from_=10, to=500, orient='horizontal',
            bg=C['bg'], fg=C['text'], troughcolor=C['panel'],
            highlightthickness=0, bd=0, length=300,
            command=self._on_slider_ventana)
        self._slider_ventana.set(VENTANA)
        self._slider_ventana.pack(side='left', padx=4)

        # ── Record limit selector ──
        tk.Label(sf, text="MOSTRAR:", font=('Consolas', 8, 'bold'),
                 bg=C['bg'], fg=C['muted']).pack(side='left', padx=(20, 4))

        self._record_limit = tk.StringVar(value='TODOS')
        self._opt_records = tk.OptionMenu(
            sf, self._record_limit, '50', '100', '200','300','400','500','600','TODOS',
            command=self._on_record_limit_change
        )
        self._opt_records.config(
            font=('Consolas', 8, 'bold'), bg=C['panel'], fg=C['accent'],
            activebackground=C['border'], activeforeground=C['accent'],
            highlightthickness=0, relief='flat', bd=0
        )
        self._opt_records['menu'].config(bg=C['panel'], fg=C['accent'], font=('Consolas', 8))
        self._opt_records.pack(side='left', padx=6)

        # ── Grafica ──
        graf_frame = tk.Frame(main, bg=C['bg'])
        graf_frame.pack(fill='both', expand=True, padx=10, pady=(0, 4))

        self._fig = plt.Figure(figsize=(13, 5), facecolor=C['bg'])
        self._canvas = FigureCanvasTkAgg(self._fig, master=graf_frame)
        self._canvas.get_tk_widget().pack(fill='both', expand=True)
        self._axes = []

        # ── Tabla rangos ──
        tabla_frame = tk.Frame(main, bg=C['panel'])
        tabla_frame.pack(fill='x', padx=10, pady=(0, 8))

        cols = ('RANGO', 'MODO', 'TOTAL OPS', 'GANADAS', 'PERDIDAS', 'WR TOTAL',
                'OPS V50', 'WR V50', 'PRIORIDAD', 'ESTADO', 'MAESTRO', 'SALDO')
        self._tree = ttk.Treeview(tabla_frame, columns=cols, show='headings', height=8)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('EP.Treeview',
            background=C['panel'], foreground=C['text'],
            fieldbackground=C['panel'], rowheight=20,
            font=('Consolas', 8))
        style.configure('EP.Treeview.Heading',
            background='#060E1C', foreground=C['accent'],
            font=('Consolas', 8, 'bold'), relief='flat')
        style.map('EP.Treeview', background=[('selected', '#1A3A5C')])
        self._tree.configure(style='EP.Treeview')

        anchos = [60, 75, 80, 70, 75, 75, 65, 65, 80, 70, 80, 75]
        for col, ancho in zip(cols, anchos):
            self._tree.heading(col, text=col)
            self._tree.column(col, width=ancho, anchor='center', stretch=False)

        sb = ttk.Scrollbar(tabla_frame, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self._tree.pack(fill='x')

        self._tree.tag_configure('bueno',     background='#0A2218', foreground='#00FF88')
        self._tree.tag_configure('prioridad', background='#1A1500', foreground='#FFD700')
        self._tree.tag_configure('baja',      background='#220A0A', foreground='#FF5555')
        self._tree.tag_configure('sin_datos', background='#141414', foreground='#666666')

        # ── Panel de cambios de modo ──
        cambios_frame = tk.Frame(main, bg=C['panel'],
                                 highlightbackground=C['border'], highlightthickness=1)
        cambios_frame.pack(fill='x', padx=10, pady=(0, 8))

        hdr = tk.Frame(cambios_frame, bg=C['border'])
        hdr.pack(fill='x')
        self._lbl_cambios_titulo = tk.Label(hdr, text="  CAMBIOS DE MODO — RECONSTRUCTOR",
                 font=('Consolas', 9, 'bold'),
                 bg=C['border'], fg=C['accent'], pady=4)
        self._lbl_cambios_titulo.pack(side='left')
        self._lbl_cambios_count = tk.Label(hdr, text="0", font=('Consolas', 8),
                                            bg=C['border'], fg=C['muted'])
        self._lbl_cambios_count.pack(side='right', padx=10)

        cols_cambios = ('OPERACION', 'RANGO', 'DE', 'A')
        self._tree_cambios = ttk.Treeview(cambios_frame, columns=cols_cambios, show='headings', height=12)
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('EP.Treeview', background=C['panel'], foreground=C['text'],
                        fieldbackground=C['panel'], rowheight=18, font=('Consolas', 8))
        style.configure('EP.Treeview.Heading', background='#060E1C', foreground=C['accent'],
                        font=('Consolas', 8, 'bold'), relief='flat')
        self._tree_cambios.configure(style='EP.Treeview')

        for col in cols_cambios:
            self._tree_cambios.heading(col, text=col)
            ancho = 50 if col == 'IDX' else 100
            self._tree_cambios.column(col, width=ancho, anchor='center')

        self._tree_cambios.pack(fill='x')
        self._tree_cambios.tag_configure('directo', background='#0A1A2A', foreground='#00BFFF', font=('Consolas', 11, 'bold'))
        self._tree_cambios.tag_configure('inverso', background='#2A0A0A', foreground='#FF6644', font=('Consolas', 11, 'bold'))

    def _toggle_voz(self):
        self._voz_activa = not self._voz_activa
        if self._voz_activa:
            self._btn_voz.config(text="VOZ: ON", bg='#0A2218', fg=C['green'])
        else:
            self._btn_voz.config(text="VOZ: OFF", bg='#1A0A1A', fg=C['muted'])

    def _on_modo_analisis(self, val):
        if val == 'UMBRAL':
            self._slider_frame.pack_forget()
        else:
            self._slider_frame.pack(side='left')
        self.cargar_y_dibujar()

    def _on_slider_ventana(self, val):
        self._ventana_actual = int(val)
        self._lbl_ventana.config(text=f"{self._ventana_actual} rondas")
        self.cargar_y_dibujar()

    def _on_record_limit_change(self, val):
        self.cargar_y_dibujar()

    def _presionar_boton(self, nombre):
        if nombre == 'REC':
            self._btn_rec.config(bg='#0A2218', fg=C['accent'])
            self._btn_hist.config(bg=C['panel'], fg=C['muted'])
            self._btn_comb.config(bg=C['panel'], fg=C['muted'])
            self._btn_live.config(bg=C['panel'], fg=C['muted'])
            self._btn_test.config(bg=C['panel'], fg='#FF8C00')
            self.cargar_y_dibujar('reconstructor')
            if self._voz_activa:
                _hablar_async("Reconstructor")
        elif nombre == 'HIST':
            self._btn_hist.config(bg='#0A2218', fg=C['green'])
            self._btn_rec.config(bg=C['panel'], fg=C['muted'])
            self._btn_comb.config(bg=C['panel'], fg=C['muted'])
            self._btn_live.config(bg=C['panel'], fg=C['muted'])
            self._btn_test.config(bg=C['panel'], fg='#FF8C00')
            self.cargar_y_dibujar('historial')
            if self._voz_activa:
                _hablar_async("Historial")
        elif nombre == 'COMB':
            self._btn_comb.config(bg='#0A1828', fg=C['warn'])
            self._btn_rec.config(bg=C['panel'], fg=C['muted'])
            self._btn_hist.config(bg=C['panel'], fg=C['muted'])
            self._btn_live.config(bg=C['panel'], fg=C['muted'])
            self._btn_test.config(bg=C['panel'], fg='#FF8C00')
            self.cargar_y_dibujar('combinado')
            if self._voz_activa:
                _hablar_async("Combinado")
        elif nombre == 'LIVE':
            self._btn_live.config(bg='#0A2218', fg=C['green'])
            self._btn_rec.config(bg=C['panel'], fg=C['muted'])
            self._btn_hist.config(bg=C['panel'], fg=C['muted'])
            self._btn_comb.config(bg=C['panel'], fg=C['muted'])
            self._btn_test.config(bg=C['panel'], fg='#FF8C00')
            self.cargar_y_dibujar('live')
            if self._voz_activa:
                _hablar_async("Live")
        elif nombre == 'TEST':
            self._btn_test.config(bg='#1A0A00', fg='#FF8C00')
            self._btn_rec.config(bg=C['panel'], fg=C['muted'])
            self._btn_hist.config(bg=C['panel'], fg=C['muted'])
            self._btn_comb.config(bg=C['panel'], fg=C['muted'])
            self._btn_live.config(bg=C['panel'], fg=C['muted'])
            self.cargar_y_dibujar('test')
            if self._voz_activa:
                _hablar_async("Test")

    def _on_test_modo_change(self):
        if self._fuente_activa == 'test':
            self.cargar_y_dibujar()

    def _abrir_analisis(self):
        """Ventana emergente con analisis completo EP LIVE + proyecciones."""
        try:
            self._generar_analisis()
        except Exception as e:
            import traceback
            print(f"[Analisis] ERROR: {e}")
            traceback.print_exc()

    def _generar_analisis(self):
        try:
            ops_live = parsear_live_history(ARCHIVO_LIVE)
        except Exception as e:
            print(f"[Analisis] Error parseando live: {e}")
            ops_live = []
        if not ops_live:
            _hablar_async("No hay datos en vivo")
            return
        total = len(ops_live)
        aciertos = sum(1 for o in ops_live if o.get('acierto'))
        wr_total = aciertos / total * 100 if total else 0

        # ── Simular EP ──
        ventanas = {}
        bal_real = [0.0]
        bal_ep = [0.0]
        n_bets_ep = 0
        ep_aciertos = 0
        ep_primera_op = None

        for idx, op in enumerate(ops_live):
            r, m, g = op['rango'], op['modo'], op['acierto']
            if r not in ventanas: ventanas[r] = {}
            if m not in ventanas[r]: ventanas[r][m] = deque(maxlen=EP_VENTANA)
            v = ventanas[r][m]
            n_v = len(v)
            gm = g if m == 'DIRECTO' else not g
            wr_v = sum(v)/n_v*100 if n_v>=EP_MIN_OPS else 0
            mult = ep_mult(wr_v) if n_v>=EP_MIN_OPS else 1
            mr = op.get('mult',1)
            bal_real.append(bal_real[-1] + (PNL_ACIERTO if g else PNL_FALLO)*mr)
            apostar = n_v >= EP_MIN_OPS and wr_v >= EP_UMBRAL_ESTADO
            if apostar:
                if ep_primera_op is None: ep_primera_op = idx
                pnl = (PNL_ACIERTO if gm else PNL_FALLO)*mult
                bal_ep.append(bal_ep[-1]+pnl)
                n_bets_ep += 1
                if gm: ep_aciertos += 1
            else:
                bal_ep.append(bal_ep[-1])
            v.append(1 if gm else 0)

        saldo_real = bal_real[-1]
        saldo_ep = bal_ep[-1]
        ep_wr = ep_aciertos/n_bets_ep*100 if n_bets_ep else 0

        # ── Rachas ──
        ep_ganadas = []
        for op in ops_live:
            r, m, g = op['rango'], op['modo'], op['acierto']
            gm = g if m == 'DIRECTO' else not g
            ep_ganadas.append(gm)
        rachas = []
        racha_act = 1
        sentido = ep_ganadas[0] if ep_ganadas else None
        max_win = 0
        max_loss = 0
        for val in ep_ganadas[1:]:
            if val == sentido:
                racha_act += 1
            else:
                rachas.append((sentido, racha_act))
                if sentido: max_win = max(max_win, racha_act)
                else: max_loss = max(max_loss, racha_act)
                sentido = val
                racha_act = 1
        rachas.append((sentido, racha_act))
        if sentido: max_win = max(max_win, racha_act)
        else: max_loss = max(max_loss, racha_act)
        pnl_por_racha = []
        racha_act_pnl = 0
        racha_sentido = ep_ganadas[0] if ep_ganadas else None
        racha_len = 0
        for val in ep_ganadas:
            if val == racha_sentido:
                racha_act_pnl += PNL_ACIERTO if val else PNL_FALLO
                racha_len += 1
            else:
                if racha_len > 0:
                    pnl_por_racha.append((racha_sentido, racha_len, racha_act_pnl))
                racha_sentido = val
                racha_act_pnl = PNL_ACIERTO if val else PNL_FALLO
                racha_len = 1
        if racha_len > 0:
            pnl_por_racha.append((racha_sentido, racha_len, racha_act_pnl))

        # ── Tiempo y proyecciones ──
        dec_data = json.loads(ARCHIVO_DECISION.read_text(encoding='utf-8'))
        horas = [x.get('hora','') for x in dec_data if x.get('hora')]
        if len(horas) >= 2:
            def _to_mins(h):
                p = h.split(':')
                return int(p[0])*60 + int(p[1]) + int(p[2])/60
            t0 = _to_mins(horas[0])
            t1 = _to_mins(horas[-1])
            span = t1 - t0
            if span < 0: span += 24*60
            freq_min = span / len(horas)
            rondas_por_hora = 60 / freq_min
            rondas_por_dia = rondas_por_hora * 24
        else:
            span = 0; freq_min = 4; rondas_por_hora = 15; rondas_por_dia = 360

        avg_pnl_ep_bet = saldo_ep / n_bets_ep if n_bets_ep else 0
        proy_1h   = avg_pnl_ep_bet * rondas_por_hora * (n_bets_ep/total)
        proy_2h   = proy_1h * 2
        proy_4h   = proy_1h * 4
        proy_8h   = proy_1h * 8
        proy_12h  = proy_1h * 12
        proy_1dia = avg_pnl_ep_bet * rondas_por_dia * (n_bets_ep/total)
        proy_1sem = proy_1dia * 7
        proy_1mes = proy_1dia * 30

        # ── Historial mejor modo por rango ──
        mejores_rangos = []
        for rango in RANGOS_ORDEN:
            for modo in ('DIRECTO', 'INVERSO'):
                dq = ventanas.get(rango, {}).get(modo, deque())
                n = len(dq)
                if n < EP_MIN_OPS: continue
                wr = sum(dq)/n*100
                est = '✅' if wr >= EP_UMBRAL_ESTADO else ('⚠️' if wr >= 50 else '❌')
                mejores_rangos.append((rango, modo, n, wr, est))

        # ── Mejor rendimiento por racha ──
        best_racha_pnl = max(pnl_por_racha, key=lambda x: x[2]) if pnl_por_racha else (None,0,0)
        worst_racha_pnl = min(pnl_por_racha, key=lambda x: x[2]) if pnl_por_racha else (None,0,0)

        # ── Apuesta maxima ──
        pnls_dec = [x.get('pnl',0) for x in dec_data if x.get('pnl') is not None]
        max_bet = max(abs(x) for x in pnls_dec) if pnls_dec else 0
        avg_bet = sum(abs(x) for x in pnls_dec)/len(pnls_dec) if pnls_dec else 0
        pnls_ep_reales = [bal_ep[i+1]-bal_ep[i] for i in range(len(bal_ep)-1)]
        max_bet_ep = max(abs(x) for x in pnls_ep_reales) if pnls_ep_reales else 0

        # ── Construir ventana ──
        win = tk.Toplevel(self.root)
        win.title("📊 ANÁLISIS EP — LIVE")
        win.configure(bg='#050A14')
        win.geometry("800x750")
        win.transient(self.root)
        win.grab_set()

        main_f = tk.Frame(win, bg='#050A14')
        main_f.pack(fill='both', expand=True, padx=12, pady=8)

        # Titulo + boton voz
        hdr = tk.Frame(main_f, bg='#0A1628', highlightbackground='#00D4FF', highlightthickness=1)
        hdr.pack(fill='x', pady=(0, 6))
        tk.Label(hdr, text="  ANÁLISIS COMPLETO — EP FILTRO LIVE",
                 font=('Consolas', 11, 'bold'), bg='#0A1628', fg='#FF66FF', pady=8).pack(side='left')

        def _hablar_analisis():
            txt = f"Análisis de estrategia perfecta sobre datos en vivo. "
            txt += f"De un total de {total} operaciones, el saldo real es de {saldo_real:+.2f} euros. "
            txt += f"La estrategia perfecta apuesta solo {n_bets_ep} rondas, con saldo de {saldo_ep:+.2f} euros. "
            txt += f"Win rate del filtro: {ep_wr:.1f} por ciento. "
            txt += f"Primera apuesta en operación número {ep_primera_op}. "
            txt += f"Racha ganadora máxima: {max_win}. Racha perdedora máxima: {max_loss}. "
            txt += f"Proyección a 1 hora: {proy_1h:+.2f} euros. "
            txt += f"Proyección a 1 día: {proy_1dia:+.2f} euros. "
            txt += f"Proyección a 1 mes: {proy_1mes:+.2f} euros."
            _hablar_async(txt)

        btn_voz = tk.Button(hdr, text="🔊 HABLAR", font=('Consolas', 9, 'bold'),
                           bg='#1A0033', fg='#FF66FF', relief='flat', padx=10,
                           command=_hablar_analisis)
        btn_voz.pack(side='right', padx=8)

        btn_ok = tk.Button(hdr, text="OK  ✕", font=('Consolas', 9, 'bold'),
                          bg='#0A1628', fg='#FF3366', relief='flat', padx=10,
                          command=win.destroy)
        btn_ok.pack(side='right', padx=4)

        # ── Canvas con scroll ──
        cv = tk.Canvas(main_f, bg='#050A14', highlightthickness=0)
        vsb = tk.Scrollbar(main_f, orient='vertical', command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        cv.pack(side='left', fill='both', expand=True)

        cont = tk.Frame(cv, bg='#050A14')
        cv.create_window((0,0), window=cont, anchor='nw', width=750)

        def _conf_scroll(ev):
            delta = -1 if (getattr(ev,'num',0)==5 or getattr(ev,'delta',0)<0) else 1
            cv.yview_scroll(delta, 'units')
        cv.bind('<MouseWheel>', _conf_scroll)
        cv.bind('<Button-4>', _conf_scroll)
        cv.bind('<Button-5>', _conf_scroll)
        cont.bind('<Configure>', lambda e: cv.configure(scrollregion=cv.bbox('all')))

        def seccion(titulo, color='#00D4FF'):
            f = tk.Frame(cont, bg='#0A1628', padx=10, pady=6)
            f.pack(fill='x', pady=(6,2))
            tk.Label(f, text=titulo, font=('Consolas', 9, 'bold'),
                     bg='#0A1628', fg=color).pack(anchor='w')
            return f
        def dato(parent, label, valor, color='#C8D8E8'):
            row = tk.Frame(parent, bg='#0A1628')
            row.pack(fill='x', padx=(8,0), pady=1)
            tk.Label(row, text=label, font=('Consolas', 8),
                     bg='#0A1628', fg='#4A6080', width=28, anchor='w').pack(side='left')
            tk.Label(row, text=valor, font=('Consolas', 8, 'bold'),
                     bg='#0A1628', fg=color, anchor='w').pack(side='left', padx=(4,0))

        # ── 1. RESUMEN ──
        s = seccion("📊 RESUMEN GENERAL")
        dato(s, "Total operaciones LIVE:", str(total), '#FF66FF')
        dato(s, "Aciertos totales (sin filtro):", f"{aciertos}/{total} ({wr_total:.1f}%)",
             '#FF3366' if wr_total<50 else '#00FF88')
        dato(s, "Saldo REAL (sin filtro):", f"{saldo_real:+.2f}€",
             '#FF3366' if saldo_real<0 else '#00FF88')
        dato(s, "Apuestas EP:", str(n_bets_ep), '#00D4FF')
        dato(s, "Aciertos EP:", f"{ep_aciertos}/{n_bets_ep} ({ep_wr:.1f}%)",
             '#00FF88' if ep_wr>=53.2 else '#FFB800')
        dato(s, "Saldo EP (filtrado):", f"{saldo_ep:+.2f}€", '#00FF88' if saldo_ep>=0 else '#FF3366')
        dato(s, "Diferencia EP vs REAL:", f"{saldo_ep-saldo_real:+.2f}€", '#00FF88')
        dato(s, "Rondas saltadas:", f"{total-n_bets_ep} ({(total-n_bets_ep)/total*100:.1f}%)", '#4A6080')
        dato(s, "Primera apuesta EP:", f"Op #{ep_primera_op}" if ep_primera_op is not None else "Nunca", '#FFB800')

        # ── 2. RACHAS ──
        s = seccion("⚡ ESTADÍSTICA DE RACHAS")
        dato(s, "Racha ganadora máxima:", str(max_win), '#00FF88')
        dato(s, "Racha perdedora máxima:", str(max_loss), '#FF3366')
        dato(s, "Total cambios de racha:", str(len(rachas)), '#00D4FF')
        # Rendimiento por racha
        if best_racha_pnl[0] is not None:
            dato(s, "Mejor racha (PNL):",
                 f"{best_racha_pnl[1]} ops → {best_racha_pnl[2]:+.2f}€ ({'GANADORA' if best_racha_pnl[0] else 'PERDEDORA'})",
                 '#00FF88')
        if worst_racha_pnl[0] is not None:
            dato(s, "Peor racha (PNL):",
                 f"{worst_racha_pnl[1]} ops → {worst_racha_pnl[2]:+.2f}€ ({'GANADORA' if worst_racha_pnl[0] else 'PERDEDORA'})",
                 '#FF3366')

        # ── 3. APUESTAS ──
        s = seccion("💰 APUESTAS")
        dato(s, "Apuesta máxima (real):", f"{max_bet:.2f}€", '#FFB800')
        dato(s, "Apuesta promedio (real):", f"{avg_bet:.2f}€", '#4A6080')
        dato(s, "Apuesta máxima EP teórica:", f"{max_bet_ep:.2f}€", '#00D4FF')
        dato(s, "PNL promedio por apuesta EP:", f"{avg_pnl_ep_bet:+.4f}€",
             '#00FF88' if avg_pnl_ep_bet>0 else '#FF3366')
        dato(s, "Tasa de acierto EP:", f"{ep_wr:.1f}%",
             '#00FF88' if ep_wr>=53.2 else '#FFB800')
        dato(s, "Tasa de fallo EP:", f"{100-ep_wr:.1f}%", '#FF3366')

        # ── 4. RANGOS ACTIVOS ──
        s = seccion("🎯 RANGOS Y MODOS ACTIVOS")
        for rango, modo, n, wr, est in mejores_rangos:
            c = '#00FF88' if est == '✅' else ('#FFB800' if est == '⚠️' else '#FF3366')
            dato(s, f"{rango} {modo}:", f"{n} ops WR={wr:.1f}% {est}", c)

        # ── 5. PROYECCIONES ──
        s = seccion("🚀 PROYECCIONES EP (basadas en rendimiento actual)")
        tk.Label(s, text=f"Frecuencia: {rondas_por_hora:.1f} rondas/hora ({freq_min:.2f} min/ronda)",
                 font=('Consolas', 7), bg='#0A1628', fg='#4A6080', anchor='w').pack(padx=8, pady=(0,2), anchor='w')

        proy_color = '#00FF88' if proy_1h > 0 else '#FF3366'
        dato(s, "Proyección 1 hora:", f"{proy_1h:+.2f}€", proy_color)
        dato(s, "Proyección 2 horas:", f"{proy_2h:+.2f}€", proy_color)
        dato(s, "Proyección 4 horas:", f"{proy_4h:+.2f}€", proy_color)
        dato(s, "Proyección 8 horas:", f"{proy_8h:+.2f}€", proy_color)
        dato(s, "Proyección 12 horas:", f"{proy_12h:+.2f}€", proy_color)
        dato(s, "", "", '#050A14')
        dato(s, "Proyección 1 día:", f"{proy_1dia:+.2f}€",
             '#00FF88' if proy_1dia>0 else '#FF3366')
        dato(s, "Proyección 1 semana:", f"{proy_1sem:+.2f}€",
             '#00FF88' if proy_1sem>0 else '#FF3366')
        dato(s, "Proyección 1 mes:", f"{proy_1mes:+.2f}€",
             '#00FF88' if proy_1mes>0 else '#FF3366')

        # ── Separador final ──
        tk.Frame(cont, bg='#0D2137', height=1).pack(fill='x', pady=6)

    def _toggle_loop(self):
        if self._loop_activo:
            self._loop_activo = False
            if self._loop_job:
                self.root.after_cancel(self._loop_job)
                self._loop_job = None
            self._btn_loop.config(text="▶ AUTO", bg=C['panel'], fg=C['muted'])
            self._lbl_loop.config(text="")
        else:
            self._loop_activo = True
            self._btn_loop.config(text="⏹ PARAR", bg='#0A2218', fg=C['green'])
            self._tick_loop()

    def _tick_loop(self):
        if not self._loop_activo:
            return
        self.cargar_y_dibujar()
        from datetime import datetime
        self._lbl_loop.config(text=f"↻ {datetime.now().strftime('%H:%M:%S')}")
        self._loop_job = self.root.after(60_000, self._tick_loop)

    def _limpiar_kpis(self):
        for w in self._kpi_frame.winfo_children():
            w.destroy()

    def _kpi(self, parent, label, valor, color):
        f = tk.Frame(parent, bg=C['panel'], padx=12, pady=6)
        f.pack(side='left', padx=4)
        tk.Label(f, text=label, font=('Consolas', 7), bg=C['panel'],
                 fg=C['muted']).pack()
        tk.Label(f, text=valor, font=('Consolas', 11, 'bold'),
                 bg=C['panel'], fg=color).pack()

    def cargar_y_dibujar(self, fuente: str = None):
        if fuente:
            self._fuente_activa = fuente

        # Resetear labels de cruces
        try:
            self._lbl_cruces_arriba.config(text="▲ 0")
            self._lbl_cruces_abajo.config(text="▼ 0")
            self._lbl_cruces_total.config(text="Σ 0")
        except:
            pass

        ops_rec  = parsear_archivo(ARCHIVO_RECONSTRUCTOR)
        ops_hist = parsear_decision_history(ARCHIVO_DECISION)
        ops_live = parsear_live_history(ARCHIVO_LIVE)

        # Aplicar limite de registros
        limite_str = self._record_limit.get()
        if limite_str != 'TODOS':
            n = int(limite_str)
            ops_rec  = ops_rec[-n:]  if len(ops_rec)  > n else ops_rec
            ops_hist = ops_hist[-n:] if len(ops_hist) > n else ops_hist
            ops_live = ops_live[-n:] if len(ops_live) > n else ops_live

        modo = self._modo_analisis.get()
        v = self._ventana_actual
        if modo == 'UMBRAL':
            res_rec  = simular_umbral(ops_rec)       if ops_rec  else None
            res_hist = simular_umbral(ops_hist)      if ops_hist else None
            res_live = simular_umbral(ops_live)      if ops_live else None
        elif modo == 'COMBINADO':
            res_rec  = simular_combinado(ops_rec,  v) if ops_rec  else None
            res_hist = simular_combinado(ops_hist, v) if ops_hist else None
            res_live = simular_combinado(ops_live, v) if ops_live else None
        else:
            res_rec  = simular(ops_rec,  v) if ops_rec  else None
            res_hist = simular(ops_hist, v) if ops_hist else None
            res_live = simular(ops_live, v) if ops_live else None

        # ── Modo TEST: 4 variantes sobre datos en vivo ────────────────────────
        modo_test = self._test_modo.get()
        if modo_test == 'REC+LIVE':
            ops_test = ops_rec + ops_live
            if modo == 'UMBRAL':
                res_test = simular_umbral(ops_test)        if ops_test else None
            elif modo == 'COMBINADO':
                res_test = simular_combinado(ops_test, v)  if ops_test else None
            else:
                res_test = simular(ops_test, v)            if ops_test else None
        elif modo_test == 'CALIBRADO':
            res_test = simular_calibrado(ops_rec, ops_live, v) if ops_live else None
        elif modo_test == 'COMPARATIVA':
            # bal_ep = EP simulado sobre live / bal_real = decisiones reales del HIST
            _ep  = simular(ops_live, v)  if ops_live else None
            _dec = simular(ops_hist, v)  if ops_hist else None
            if _ep:
                res_test = dict(_ep)
                if _dec:
                    res_test['bal_real'] = _dec['bal_real']
            else:
                res_test = None
        else:  # EP PURO — recalcula mejor modo sin usar el modo grabado
            res_test = simular_combinado(ops_live, v) if ops_live else None
        if res_test:
            res_test['_ops_ref'] = ops_live

        total_rec  = len(ops_rec)
        total_hist = len(ops_hist)
        total_live = len(ops_live)
        self._lbl_fuente.config(
            text=f"Rec: {total_rec} ops  |  Hist: {total_hist} ops  |  Live: {total_live} ops")

        # Adjuntar ops originales para marcadores de cambio en grafica
        if res_rec:
            res_rec['_ops_ref'] = ops_rec
        if res_hist:
            res_hist['_ops_ref'] = ops_hist
        if res_live:
            res_live['_ops_ref'] = ops_live
        self._dibujar_grafica(res_rec, res_hist, res_live, res_test)

        # KPIs y tabla de la fuente activa
        if self._fuente_activa == 'historial':
            res_activo = res_hist
            ops_activo = ops_hist
        elif self._fuente_activa == 'combinado':
            res_activo = res_hist if res_hist else res_rec
            ops_activo = ops_hist if ops_hist else ops_rec
        elif self._fuente_activa == 'live':
            res_activo = res_live
            ops_activo = ops_live
        elif self._fuente_activa == 'test':
            res_activo = res_test
            ops_activo = ops_live
        else:
            res_activo = res_rec
            ops_activo = ops_rec
        if res_activo:
            self._dibujar_kpis(res_activo, len(ops_activo))
            self._dibujar_tabla(res_activo['stats_globales'], res_activo['stats_ventana'])

            # Log del sistema - estado de apuestas por rango
            modo_analisis = self._modo_analisis.get()
            print(f"\n{'='*60}")
            print(f"LOG DEL SISTEMA — ESTADO DE APUESTAS POR RANGO [{modo_analisis}]")
            print(f"{'='*60}")
            for rango in RANGOS_ORDEN:
                mejor_wr = 0
                mejor_modo = None
                if modo_analisis == 'UMBRAL':
                    mejor = res_activo.get('mejor_modo', {}).get(rango, (None, 0))
                    mejor_modo, mejor_wr = mejor
                    if mejor_modo is None:
                        print(f"  RANGO {rango}: SIN DATOS SUFICIENTES")
                        continue
                else:
                    stats_v = res_activo.get('stats_ventana', {}).get(rango, {})
                    for modo in ('DIRECTO', 'INVERSO'):
                        s = stats_v.get(modo, {})
                        wr = s.get('wr', 0)
                        if wr > mejor_wr:
                            mejor_wr = wr
                            mejor_modo = modo
                    if mejor_modo is None:
                        print(f"  RANGO {rango}: SIN DATOS")
                        continue
                if mejor_wr >= UMBRAL_ESTADO:
                    icon = '[OK]'
                    color = '\033[92m'
                    msg = 'PUEDE APOSTAR'
                else:
                    icon = '[X]'
                    color = '\033[93m'
                    msg = 'NO PUEDE APOSTAR'
                reset = '\033[0m'
                print(f"  {color}{icon} WR={mejor_wr:.1f}% {'>=' if mejor_wr >= UMBRAL_ESTADO else '<'} UMBRAL={UMBRAL_ESTADO}% - {msg} ({mejor_modo}){reset}")
            print(f"{'='*60}\n")

            # Dibujar cambios de modo desde la fuente activa
            if self._fuente_activa == 'historial':
                self._dibujar_cambios(ops_activo, res_activo.get('detalles', []))

        # Cargar datos de filtros desde historial de decisiones
        self._filtros_data = parsear_filtros_decisiones(ARCHIVO_DECISION)
        n_sel = len(self._filtros_seleccionados)
        self._lbl_filtros_count.config(text=f"{n_sel} sel")
        self._filtro_table.set_data(self._filtros_data, self._filtros_seleccionados)

    # ── Filtros ──────────────────────────────────────────────────────────────────
    def _toggle_filtros(self):
        self._mostrar_filtros = not self._mostrar_filtros
        if self._mostrar_filtros:
            self._filtros_container.pack(fill='both', expand=True, padx=4, pady=(0, 4))
            self._btn_filtros_toggle.config(text="FILTROS ▼")
        else:
            self._filtros_container.pack_forget()
            self._btn_filtros_toggle.config(text="FILTROS ▶")

    def _dibujar_tabla_filtros(self):
        """Actualiza datos en el widget Canvas FiltroTable."""
        self._lbl_filtros_count.config(text=f"{len(self._filtros_seleccionados)} sel")
        self._filtro_table.set_data(self._filtros_data, self._filtros_seleccionados)

    def _on_filtro_click_canvas(self, idx):
        """Callback cuando se hace click en una fila del Canvas FiltroTable."""
        fd = self._filtros_data.get(idx, {})
        if fd.get('none_filter', True):
            return
        if idx in self._filtros_seleccionados:
            self._filtros_seleccionados.discard(idx)
        else:
            self._filtros_seleccionados.add(idx)
        self._dibujar_tabla_filtros()
        self.cargar_y_dibujar()

    def _aplicar_anchos_filtros(self, widths_dict):
        """Aplica anchos de columna leídos desde Sheets al widget Canvas."""
        self._filtro_table.actualizar_anchos(widths_dict)

    def _dibujar_filtros_en_grafica(self, ax):
        """Dibuja curvas overlay de los filtros seleccionados."""
        if not self._filtros_seleccionados or not self._filtros_data:
            return
        for idx in sorted(self._filtros_seleccionados):
            fd = self._filtros_data.get(idx)
            if not fd or fd.get('none_filter', True) or not fd.get('curva'):
                continue
            curva  = fd['curva']
            nombre = FILTROS_CURVA[idx][0]
            color  = FILTROS_CURVA[idx][1]
            pnl    = curva[-1] if curva else 0.0
            ax.plot(curva, color=color, linewidth=1.0,
                    linestyle='--', alpha=0.7,
                    label=f'{nombre}: {pnl:+.1f}')
        # Actualizar leyenda (sin duplicar la existente)
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(handles=handles, labels=labels,
                      facecolor=C['panel'], edgecolor=C['border'],
                      labelcolor=C['text'], fontsize=6)
        ax.figure.canvas.draw_idle()

    def _dibujar_kpis(self, res: dict, total: int):
            self._limpiar_kpis()

    def _dibujar_kpis(self, res: dict, total: int):
        self._limpiar_kpis()
        col_real = C['green'] if res['saldo_real'] >= 0 else C['red']
        col_ep   = C['green'] if res['saldo_ep']  >= 0 else C['red']
        filtro_pct = res['n_skips'] / total * 100 if total else 0

        self._kpi(self._kpi_frame, "TOTAL OPS",      str(total),                          C['text'])
        self._kpi(self._kpi_frame, "REAL",            f"{res['saldo_real']:+.2f}€",        col_real)
        self._kpi(self._kpi_frame, "SIMULADA EP",    f"{res['saldo_ep']:+.2f}€",          col_ep)
        self._kpi(self._kpi_frame, "APUESTAS EP",    str(res['n_bets']),                  C['accent'])
        self._kpi(self._kpi_frame, "SKIPS EP",       f"{res['n_skips']} ({filtro_pct:.0f}%)", C['muted'])
        self._kpi(self._kpi_frame, "BUENO",          str(res['niveles'].get('BUENO', 0)), C['green'])
        self._kpi(self._kpi_frame, "PRIORIDAD",      str(res['niveles'].get('PRIORIDAD', 0)), C['warn'])
        self._kpi(self._kpi_frame, "BAJA/SKIP",      str(res['niveles'].get('BAJA', 0) + res['niveles'].get('SKIP', 0)), C['muted'])

    def _agregar_cambio_tabla(self, rango, antes, despues, idx):
        tag = 'directo' if despues == 'DIRECTO' else 'inverso'
        simbolo = '▲' if despues == 'DIRECTO' else '▼'
        # Usar el número de operación real (idx es el índice en la gráfica)
        item = self._tree_cambios.insert('', 'end', values=(idx, f"{simbolo} {rango}", antes, despues))
        self._tree_cambios.item(item, tags=(tag,))
        self._tree_cambios.see(item)
        # Actualizar contador
        current = int(self._lbl_cambios_count.cget('text')) if self._lbl_cambios_count.cget('text').isdigit() else 0
        self._lbl_cambios_count.config(text=str(current + 1))

    def _dibujar_cambios(self, ops: list, detalles: list):
        for row in self._tree_cambios.get_children():
            self._tree_cambios.delete(row)

        if not ops:
            self._lbl_cambios_count.config(text="0")
            return

        # Calcular mejor modo por rango de forma acumulativa (sin look-ahead).
        # Si solo hay datos DIRECTO, inferir INVERSO como complemento
        # (lo que pierde DIRECTO lo gana INVERSO y viceversa).
        acum = {}   # {rango: {ops, ganadas_dir}}
        cambios = []
        mejor_modo_prev = {}   # {rango: 'DIRECTO'|'INVERSO'}

        for i, op in enumerate(ops):
            rango = op['rango']
            ganada = op['ganada']

            if rango not in acum:
                acum[rango] = {'ops': 0, 'ganadas_dir': 0}
            acum[rango]['ops'] += 1
            if ganada:
                acum[rango]['ganadas_dir'] += 1

            n_ops = acum[rango]['ops']
            if n_ops < MIN_OPS:
                continue

            g_dir = acum[rango]['ganadas_dir']
            d_wr = g_dir / n_ops * 100
            i_wr = (n_ops - g_dir) / n_ops * 100  # inverso = complemento

            mejor = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
            prev = mejor_modo_prev.get(rango)
            if prev is not None and prev != mejor:
                cambios.append((i, rango, prev, mejor))
            mejor_modo_prev[rango] = mejor

        n = len(cambios)
        self._lbl_cambios_count.config(text=f"{n}")

        for idx, rango, antes, despues in cambios:
            tag = 'directo' if despues == 'DIRECTO' else 'inverso'
            simbolo = '▲' if despues == 'DIRECTO' else '▼'
            item = self._tree_cambios.insert('', 'end', values=(idx, f"{simbolo} {rango}", antes, despues))
            self._tree_cambios.item(item, tags=(tag,))

    def _dibujar_grafica(self, res_rec, res_hist, res_live=None, res_test=None):
        # Cancelar animacion anterior si existe
        if self._anim_job:
            self.root.after_cancel(self._anim_job)
            self._anim_job = None

        self._fig.clear()
        self._fig.patch.set_facecolor(C['bg'])

        if self._fuente_activa == 'live':
            graficas = [(res_live, 'LIVE PNL')]
            graficas = [(r, t) for r, t in graficas if r is not None]
            if not graficas:
                self._canvas.draw()
                return
            col = 0
            for res, titulo in graficas:
                ax = self._fig.add_subplot(1, len(graficas), col + 1)
                ax.set_facecolor(C['panel'])
                self._preparar_y_animar(ax, res, titulo)
                col += 1
        elif self._fuente_activa == 'combinado':
            if res_rec is None and res_hist is None:
                self._canvas.draw()
                return
            ax = self._fig.add_subplot(1, 1, 1)
            ax.set_facecolor(C['panel'])
            self._preparar_y_animar_combinado(ax, res_rec, res_hist)
        elif self._fuente_activa == 'historial':
            graficas = [(res_hist, 'HISTORIAL DECISIONES')]
            graficas = [(r, t) for r, t in graficas if r is not None]
            if not graficas:
                self._canvas.draw()
                return
            col = 0
            for res, titulo in graficas:
                ax = self._fig.add_subplot(1, len(graficas), col + 1)
                ax.set_facecolor(C['panel'])
                self._preparar_y_animar(ax, res, titulo)
                col += 1
        elif self._fuente_activa == 'test':
            modo_test = self._test_modo.get()
            titulos = {
                'REC+LIVE':    'TEST: REC + LIVE',
                'CALIBRADO':   'TEST: CALIBRADO (REC→LIVE)',
                'COMPARATIVA': 'TEST: EP LIVE vs DEC REAL',
                'EP PURO':     'TEST: EP PURO (sin modo grabado)',
            }
            titulo = titulos.get(modo_test, f'TEST: {modo_test}')
            if res_test is None:
                self._canvas.draw()
                return
            ax = self._fig.add_subplot(1, 1, 1)
            ax.set_facecolor(C['panel'])
            self._preparar_y_animar(ax, res_test, titulo)
        else:
            graficas = [(res_rec, 'RECONSTRUCTOR')]
            graficas = [(r, t) for r, t in graficas if r is not None]
            if not graficas:
                self._canvas.draw()
                return
            col = 0
            for res, titulo in graficas:
                ax = self._fig.add_subplot(1, len(graficas), col + 1)
                ax.set_facecolor(C['panel'])
                self._preparar_y_animar(ax, res, titulo)
                col += 1

        self._fig.tight_layout(pad=1.5)
        self._canvas.draw()

    def _preparar_y_animar_combinado(self, ax, res_rec, res_hist):
        """Tercera gráfica: línea EP del Reconstructor + línea EP del Historial."""
        self._lbl_cambios_titulo.config(text="  CAMBIOS DE MODO — COMBINADO")

        bal_ep_rec  = res_rec['bal_ep']  if res_rec  else [0]
        bal_ep_hist = res_hist['bal_ep'] if res_hist else [0]

        total = max(len(bal_ep_rec), len(bal_ep_hist))

        ax.axhline(0, color='#333344', linewidth=0.8)
        ax.set_xlim(0, 10)
        ax.set_xlabel('Operacion #', color=C['muted'], fontsize=7)
        ax.set_ylabel('Balance (€)', color=C['muted'], fontsize=7)
        ax.tick_params(colors=C['muted'], labelsize=6)
        for spine in ax.spines.values():
            spine.set_edgecolor('#1A2A3A')
        ax.grid(True, color='#1A2A3A', linewidth=0.4)
        ax.set_title("COMBINADO  |  Cargando...",
                     color=C['text'], fontsize=8, fontfamily='monospace', pad=6)

        line_rec,  = ax.plot([], [], color=C['accent'], linewidth=2,
                             alpha=0.9, label='EP Reconstructor')
        line_hist, = ax.plot([], [], color=C['green'], linewidth=2,
                             linestyle='--', alpha=0.9, label='EP Historial')
        ax.legend(facecolor=C['panel'], edgecolor=C['border'],
                  labelcolor=C['text'], fontsize=7)

        step_size = max(1, total // 150)
        self._anim_idx = 0

        # Limpiar tabla de cambios
        for row in self._tree_cambios.get_children():
            self._tree_cambios.delete(row)
        self._lbl_cambios_count.config(text="0")

        def _step():
            end = min(self._anim_idx + step_size, total)
            x_rec  = list(range(min(end, len(bal_ep_rec))))
            x_hist = list(range(min(end, len(bal_ep_hist))))

            line_rec.set_data(x_rec,  bal_ep_rec[:end])
            line_hist.set_data(x_hist, bal_ep_hist[:end])

            ax.set_xlim(0, end + max(5, end // 10))

            seg_r = bal_ep_rec[:end]   if end <= len(bal_ep_rec)  else bal_ep_rec
            seg_h = bal_ep_hist[:end]  if end <= len(bal_ep_hist) else bal_ep_hist
            all_vals = seg_r + seg_h
            if all_vals:
                y_lo = min(all_vals) - 1
                y_hi = max(all_vals) + 1
                ax.set_ylim(y_lo, y_hi)

            ep_rec_now  = bal_ep_rec[min(end, len(bal_ep_rec)) - 1]   if bal_ep_rec  else 0
            ep_hist_now = bal_ep_hist[min(end, len(bal_ep_hist)) - 1] if bal_ep_hist else 0
            pct = int(end / total * 100)
            ax.set_title(
                f"COMBINADO  |  EP-REC: {ep_rec_now:+.1f}€  EP-HIST: {ep_hist_now:+.1f}€  [{pct}%]",
                color=C['text'], fontsize=8, fontfamily='monospace', pad=6)

            self._canvas.draw_idle()
            self._anim_idx = end

            if self._anim_idx < total:
                self._anim_job = self.root.after(25, _step)
            else:
                # Fill final
                x_full_r = list(range(len(bal_ep_rec)))
                x_full_h = list(range(len(bal_ep_hist)))
                ax.fill_between(x_full_r, bal_ep_rec, 0,
                                where=[v >= 0 for v in bal_ep_rec],
                                alpha=0.06, color=C['accent'])
                ax.fill_between(x_full_r, bal_ep_rec, 0,
                                where=[v < 0  for v in bal_ep_rec],
                                alpha=0.06, color=C['red'])
                ax.fill_between(x_full_h, bal_ep_hist, 0,
                                where=[v >= 0 for v in bal_ep_hist],
                                alpha=0.06, color=C['green'])
                ax.fill_between(x_full_h, bal_ep_hist, 0,
                                where=[v < 0  for v in bal_ep_hist],
                                alpha=0.06, color=C['red'])
                ep_rec_fin  = bal_ep_rec[-1]  if bal_ep_rec  else 0
                ep_hist_fin = bal_ep_hist[-1] if bal_ep_hist else 0
                ax.set_title(
                    f"COMBINADO  |  EP-REC: {ep_rec_fin:+.1f}€  EP-HIST: {ep_hist_fin:+.1f}€",
                    color=C['text'], fontsize=8, fontfamily='monospace', pad=6)
                # Marcar cruces entre las dos EP
                cruces = detectar_cruces(bal_ep_rec, bal_ep_hist)
                for idx_c, tipo_c in cruces:
                    color_c = C['green'] if tipo_c == 'EP_SUPERA' else C['red']
                    marker_c = '^' if tipo_c == 'EP_SUPERA' else 'v'
                    y_c = bal_ep_hist[idx_c] if idx_c < len(bal_ep_hist) else 0
                    ax.plot(idx_c, y_c, marker=marker_c, color=color_c,
                            markersize=8, zorder=6, alpha=0.9)
                n_arr = sum(1 for _, t in cruces if t == 'EP_SUPERA')
                n_aba = sum(1 for _, t in cruces if t == 'EP_CAE')
                try:
                    self._lbl_cruces_arriba.config(text=f"▲ {n_arr}")
                    self._lbl_cruces_abajo.config(text=f"▼ {n_aba}")
                    self._lbl_cruces_total.config(text=f"Σ {len(cruces)}")
                except:
                    pass
                self._canvas.draw_idle()
                self._anim_job = None
                # Overlay de filtros seleccionados
                if self._filtros_seleccionados:
                    self._dibujar_filtros_en_grafica(ax)

        self._anim_job = self.root.after(100, _step)

    # ── Animacion ──────────────────────────────────────────────────────────────
    def _preparar_y_animar(self, ax, res, titulo='RECONSTRUCTOR'):
        """Configura ejes y lanza animacion progresiva."""
        # Actualizar titulo del panel de cambios
        sufijo = 'HISTORIAL' if 'HISTORIAL' in titulo else 'RECONSTRUCTOR'
        self._lbl_cambios_titulo.config(text=f"  CAMBIOS DE MODO — {sufijo}")

        bal_real = res['bal_real']
        bal_ep   = res['bal_ep']
        ops_ref  = res.get('_ops_ref', []) or []
        detalles = res.get('detalles', [])
        es_hist  = 'HISTORIAL' in titulo

        # Configurar ejes
        ax.axhline(0, color='#333344', linewidth=0.8)
        total = max(len(bal_real), len(bal_ep))
        ax.set_xlim(0, 10)
        ax.set_xlabel('Operacion #', color=C['muted'], fontsize=7)
        ax.set_ylabel('Balance (€)', color=C['muted'], fontsize=7)
        ax.tick_params(colors=C['muted'], labelsize=6)
        for spine in ax.spines.values():
            spine.set_edgecolor('#1A2A3A')
        ax.grid(True, color='#1A2A3A', linewidth=0.4)
        ax.set_title(f"{titulo}  |  Cargando...",
                      color=C['text'], fontsize=8, fontfamily='monospace', pad=6)

        # Lineas vacias
        line_real, = ax.plot([], [], color='#4A8ECC', linewidth=1.2,
                             linestyle='--', alpha=0.8, label='Real')
        line_ep,   = ax.plot([], [], color=C['green'], linewidth=2,
                             alpha=0.9, label='Simulada EP')
        ax.legend(facecolor=C['panel'], edgecolor=C['border'],
                  labelcolor=C['text'], fontsize=7)

        # Estado de animacion
        step_size = max(1, total // 150)
        self._anim_idx = 0
        self._anim_modos_rango = {}
        self._anim_cambios_voz = set()
        # Para historial: deteccion de cambios por complemento (acumulativo)
        self._anim_acum = {}   # {rango: {ops, ganadas_dir}}
        # Limpiar tabla de cambios al inicio
        for row in self._tree_cambios.get_children():
            self._tree_cambios.delete(row)
        self._lbl_cambios_count.config(text="0")

        def _detectar_cambio_rec(i):
            """Detecta cambio de modo usando detalles del reconstructor."""
            if i >= len(detalles):
                return
            d = detalles[i]
            me = d.get('modo_efectivo', 'SKIP')
            rango = d.get('rango', '?')
            if me != 'SKIP':
                prev = self._anim_modos_rango.get(rango)
                if prev is not None and prev != me:
                    _marcar_cambio(i, rango, prev, me)
                self._anim_modos_rango[rango] = me

        def _detectar_cambio_hist(i):
            """Detecta cambio de modo inferido por complemento (historial)."""
            if i >= len(ops_ref):
                return
            op = ops_ref[i]
            rango = op['rango']
            ganada = op['ganada']
            if rango not in self._anim_acum:
                self._anim_acum[rango] = {'ops': 0, 'ganadas_dir': 0}
            self._anim_acum[rango]['ops'] += 1
            if ganada:
                self._anim_acum[rango]['ganadas_dir'] += 1
            n_ops = self._anim_acum[rango]['ops']
            if n_ops < MIN_OPS:
                return
            g_dir = self._anim_acum[rango]['ganadas_dir']
            d_wr = g_dir / n_ops * 100
            i_wr = (n_ops - g_dir) / n_ops * 100
            me = 'DIRECTO' if d_wr >= i_wr else 'INVERSO'
            prev = self._anim_modos_rango.get(rango)
            if prev is not None and prev != me:
                _marcar_cambio(i, rango, prev, me)
            self._anim_modos_rango[rango] = me

        def _marcar_cambio(i, rango, prev, me):
            """Marca un cambio de modo en la grafica y la tabla."""
            self._anim_cambios_voz.add(i)
            self._agregar_cambio_tabla(rango, prev, me, i)
            if self._voz_activa:
                _hablar_async(f"Rango {rango}, cambia a {me}")
            y_val = bal_ep[i] if i < len(bal_ep) else 0
            color_m = '#00BFFF' if me == 'DIRECTO' else '#FF6644'
            marker_m = '^' if me == 'DIRECTO' else 'v'
            ax.plot(i, y_val, marker=marker_m, color=color_m,
                    markersize=10, zorder=6, alpha=0.95)
            ax.axvline(i, color=color_m, linewidth=0.7,
                       linestyle=':', alpha=0.4)
            ax.annotate(f"{rango}\n{me[:3]}",
                        xy=(i, y_val), fontsize=5,
                        color=color_m, alpha=0.85,
                        ha='center', va='bottom' if me == 'DIRECTO' else 'top',
                        xytext=(0, 8 if me == 'DIRECTO' else -8),
                        textcoords='offset points')

        detectar_cambio = _detectar_cambio_hist if es_hist else _detectar_cambio_rec

        def _step():
            end = min(self._anim_idx + step_size, total)
            x = np.arange(end)
            line_real.set_data(x, bal_real[:end])
            line_ep.set_data(x, bal_ep[:end])

            # Adaptar ejes al rango visible
            ax.set_xlim(0, end + max(5, end // 10))
            segment_real = bal_real[:end]
            segment_ep = bal_ep[:end]
            y_lo = min(min(segment_real), min(segment_ep)) - 1
            y_hi = max(max(segment_real), max(segment_ep)) + 1
            ax.set_ylim(y_lo, y_hi)

            # Detectar cambios de modo en el segmento nuevo
            for i in range(max(0, self._anim_idx - 1), end):
                if i in self._anim_cambios_voz:
                    continue
                detectar_cambio(i)

            # Actualizar titulo con progreso
            ep_now = bal_ep[end - 1] if end > 0 else 0
            real_now = bal_real[end - 1] if end > 0 else 0
            pct = int(end / total * 100)
            ax.set_title(
                f"{titulo}  |  Real: {real_now:+.1f}€  Sim: {ep_now:+.1f}€  [{pct}%]",
                color=C['text'], fontsize=8, fontfamily='monospace', pad=6)

            self._canvas.draw_idle()
            self._anim_idx = end

            if self._anim_idx < total:
                self._anim_job = self.root.after(25, _step)
            else:
                # Animacion completa: anadir fill y titulo final
                x_full = np.arange(len(bal_ep))
                ax.fill_between(x_full, bal_ep, 0,
                                where=[v >= 0 for v in bal_ep],
                                alpha=0.08, color=C['green'])
                ax.fill_between(x_full, bal_ep, 0,
                                where=[v < 0 for v in bal_ep],
                                alpha=0.08, color=C['red'])
                ax.set_title(
                    f"{titulo}  |  Real: {bal_real[-1]:+.1f}€  Sim: {bal_ep[-1]:+.1f}€",
                    color=C['text'], fontsize=8, fontfamily='monospace', pad=6)
                self._canvas.draw_idle()
                self._anim_job = None
                # Separator final en la tabla
                self._tree_cambios.insert('', 'end', values=('───', '─' * 10, '─' * 10, '─' * 10))
                # Overlay de filtros seleccionados
                if self._filtros_seleccionados:
                    self._dibujar_filtros_en_grafica(ax)

        # Iniciar animacion con pequeno delay
        self._anim_job = self.root.after(100, _step)

    def _dibujar_tabla(self, stats_glob: dict, stats_vent: dict):
        for row in self._tree.get_children():
            self._tree.delete(row)

        for rango in RANGOS_ORDEN:
            if rango not in stats_glob:
                continue
            for modo in ('DIRECTO', 'INVERSO'):
                if modo not in stats_glob[rango]:
                    continue
                g = stats_glob[rango][modo]
                ops     = g['ops']
                ganadas = g['ganadas']
                perdidas = ops - ganadas
                wr_total = ganadas / ops * 100 if ops > 0 else 0
                saldo    = ganadas * PNL_ACIERTO - perdidas * abs(PNL_FALLO)

                v = stats_vent.get(rango, {}).get(modo, {})
                ops_v50 = v.get('n', 0)
                wr_v50  = v.get('wr', 0)

                # Clasificacion basada en todos los registros
                if ops < 3:
                    prioridad = '---'
                    estado    = '---'
                    maestro   = 'SIN DATOS'
                    tag       = 'sin_datos'
                else:
                    prioridad = 'SI' if wr_total >= UMBRAL_PRIORIDAD else 'NO'
                    estado    = 'SI' if wr_total >= UMBRAL_ESTADO    else 'NO'
                    maestro   = 'BUENO' if wr_total >= UMBRAL_ESTADO else (
                                'PRIORIDAD' if wr_total >= UMBRAL_PRIORIDAD else 'MALO')
                    if maestro == 'BUENO':       tag = 'bueno'
                    elif maestro == 'PRIORIDAD': tag = 'prioridad'
                    else:                        tag = 'baja'

                col_saldo = f"{saldo:+.1f}"
                self._tree.insert('', 'end', values=(
                    rango, modo, ops, ganadas, perdidas,
                    f"{wr_total:.1f}%", ops_v50, f"{wr_v50:.1f}%",
                    prioridad, estado, maestro, col_saldo
                ), tags=(tag,))


def main():
    root = tk.Tk()
    AppEstrategiaPerfecta(root)
    root.mainloop()


if __name__ == '__main__':
    main()
