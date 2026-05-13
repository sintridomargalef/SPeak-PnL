"""
ANALIZADOR DE GRAFICAS — Acertador Senior Pro
Lee reconstructor_data_AI.txt y genera un dashboard completo de análisis.
"""

import sys
import os
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.patches import FancyBboxPatch
import matplotlib.patches as mpatches
import numpy as np
from collections import defaultdict
from pathlib import Path

# ============================================================
# TEMA
# ============================================================

C = {
    'bg':      '#050A14',
    'panel':   '#0A1628',
    'border':  '#0D2137',
    'accent':  '#00D4FF',
    'accent2': '#00FF88',
    'accent3': '#FF3366',
    'warn':    '#FFB800',
    'text':    '#C8D8E8',
    'muted':   '#4A6080',
    'blue':    '#2B7FFF',
    'red':     '#FF3366',
    'white':   '#E8F4FF',
}

MPL_STYLE = {
    'figure.facecolor':    '#050A14',
    'axes.facecolor':      '#0A1628',
    'axes.edgecolor':      '#0D2137',
    'axes.labelcolor':     '#C8D8E8',
    'axes.titlecolor':     '#00D4FF',
    'axes.titlesize':      11,
    'axes.labelsize':      9,
    'axes.grid':           True,
    'grid.color':          '#0D2137',
    'grid.linewidth':      0.6,
    'xtick.color':         '#4A6080',
    'ytick.color':         '#4A6080',
    'xtick.labelsize':     8,
    'ytick.labelsize':     8,
    'legend.facecolor':    '#0A1628',
    'legend.edgecolor':    '#0D2137',
    'legend.labelcolor':   '#C8D8E8',
    'legend.fontsize':     8,
    'text.color':          '#C8D8E8',
    'lines.linewidth':     1.5,
}

ORDEN_RANGOS = ["0-5","5-10","10-15","15-20","20-25","25-30","30-35","35-40","40-45","45-50","+50"]

# ============================================================
# PARSER
# ============================================================

def parsear(ruta: str) -> list[dict]:
    """Parsea reconstructor_data_AI.txt (formato nuevo: [timestamp] | CAMPO: valor | ...)"""
    registros = []
    with open(ruta, 'r', encoding='utf-8') as f:
        for linea in f:
            linea = linea.strip()
            if not linea.startswith('[') or 'GANADOR:' not in linea:
                continue
            try:
                resultado = 'BLUE' if 'GANADOR: BLUE' in linea else 'RED'
                mayor_gana = 'MAYOR: BLUE' in linea
                rango = linea.split('RANGO: ')[1].split(' |')[0].strip() if 'RANGO: ' in linea else 'desconocido'
                modo = linea.split('MODO: ')[1].split(' |')[0].strip() if 'MODO: ' in linea else 'SKIP'
                winrate_str = linea.split('WINRATE: ')[1].split('%')[0].strip() if 'WINRATE: ' in linea else '50'
                winrate = float(winrate_str)
                acierto = 'ACIERTO: True' in linea
                registros.append({
                    'resultado': resultado,
                    'mayor_gana': mayor_gana,
                    'racha': winrate,
                    'rango': rango,
                    'modo': modo,
                    'fuente': 'reconstructor',
                    'acierto': acierto,
                })
            except Exception:
                pass
    return registros


def parsear_historial(ruta: str) -> list[dict]:
    """Parsea historial_rondas.txt (formato JSON)"""
    registros = []
    with open(ruta, 'r', encoding='utf-8') as f:
        for linea in f:
            try:
                if ']' not in linea:
                    continue
                json_str = linea.split(']', 1)[1].strip()
                datos = json.loads(json_str)
                resultado = datos.get('ganador')
                if not resultado:
                    continue
                modo = datos.get('modo', 'SKIP')
                registros.append({
                    'resultado': resultado,
                    'rango': datos.get('rango', 'desconocido'),
                    'modo': modo,
                    'pnl': datos.get('pnl'),
                    'balance': datos.get('balance'),
                    'acierto': datos.get('acierto'),
                    'mult': datos.get('mult'),
                    'prevision': datos.get('prevision'),
                    'dif': datos.get('dif'),
                    'ep': datos.get('ep'),
                    'estrategia': datos.get('estrategia'),
                    'confianza': datos.get('confianza'),
                    'ronda': datos.get('ronda'),
                    'timestamp': datos.get('timestamp'),
                    'fuente': 'historial',
                })
            except Exception:
                pass
    return registros


def detectar_fuente(registros: list[dict]) -> str:
    """Detecta si los datos vienen de reconstructor o historial"""
    if not registros:
        return 'reconstructor'
    return registros[0].get('fuente', 'reconstructor')


def calcular_pnl_acumulado(registros):
    """PNL acumulado por orden de ronda, solo ops apostadas."""
    fuente = detectar_fuente(registros)
    pnl_acc = []
    acum = 0.0
    for r in registros:
        if r['modo'] == 'SKIP':
            continue
        if fuente == 'historial' and r.get('pnl') is not None:
            acum += r['pnl']
        else:
            if r['modo'] == 'DIRECTO':
                acierto = r.get('mayor_gana', False)
            else:
                acierto = not r.get('mayor_gana', True)
            acum += 0.9 if acierto else -1.0
        pnl_acc.append(acum)
    return pnl_acc


def stats_por_rango(registros):
    fuente = detectar_fuente(registros)
    stats = {r: {'DIRECTO': {'ops':0,'gan':0,'per':0},
                 'INVERSO': {'ops':0,'gan':0,'per':0},
                 'SKIP':    {'ops':0,'mayor_gana':0}}
             for r in ORDEN_RANGOS}
    for rec in registros:
        rango = rec['rango']
        if rango not in stats:
            stats[rango] = {'DIRECTO': {'ops':0,'gan':0,'per':0},
                            'INVERSO': {'ops':0,'gan':0,'per':0},
                            'SKIP':    {'ops':0,'mayor_gana':0}}
        modo = rec['modo']
        if fuente == 'historial':
            acierto = rec.get('acierto')
            if acierto is True:
                if modo == 'DIRECTO':
                    stats[rango]['DIRECTO']['ops'] += 1
                    stats[rango]['DIRECTO']['gan'] += 1
                elif modo == 'INVERSO':
                    stats[rango]['INVERSO']['ops'] += 1
                    stats[rango]['INVERSO']['gan'] += 1
            elif acierto is False:
                if modo == 'DIRECTO':
                    stats[rango]['DIRECTO']['ops'] += 1
                    stats[rango]['DIRECTO']['per'] += 1
                elif modo == 'INVERSO':
                    stats[rango]['INVERSO']['ops'] += 1
                    stats[rango]['INVERSO']['per'] += 1
        else:
            if modo == 'DIRECTO':
                stats[rango]['DIRECTO']['ops'] += 1
                if rec['mayor_gana']: stats[rango]['DIRECTO']['gan'] += 1
                else:                 stats[rango]['DIRECTO']['per'] += 1
            elif modo == 'INVERSO':
                stats[rango]['INVERSO']['ops'] += 1
                if not rec['mayor_gana']: stats[rango]['INVERSO']['gan'] += 1
                else:                     stats[rango]['INVERSO']['per'] += 1
            else:
                stats[rango]['SKIP']['ops'] += 1
                if rec['mayor_gana']: stats[rango]['SKIP']['mayor_gana'] += 1
    return stats


def stats_por_racha(registros):
    fuente = detectar_fuente(registros)
    if fuente == 'historial':
        return None, None
    buckets = ['≤20','21-30','31-40','41-50','51-60','61-70','>70']
    def bucket(r):
        if r <= 20:   return '≤20'
        elif r <= 30: return '21-30'
        elif r <= 40: return '31-40'
        elif r <= 50: return '41-50'
        elif r <= 60: return '51-60'
        elif r <= 70: return '61-70'
        else:         return '>70'
    st = {b: {'ops':0,'mayor_gana':0,'directo_gan':0,'directo_ops':0,
               'inverso_gan':0,'inverso_ops':0} for b in buckets}
    for rec in registros:
        racha = rec.get('racha')
        if racha is None:
            continue
        b = bucket(racha)
        st[b]['ops'] += 1
        if rec['mayor_gana']: st[b]['mayor_gana'] += 1
        if rec['modo'] == 'DIRECTO':
            st[b]['directo_ops'] += 1
            if rec['mayor_gana']: st[b]['directo_gan'] += 1
        elif rec['modo'] == 'INVERSO':
            st[b]['inverso_ops'] += 1
            if not rec['mayor_gana']: st[b]['inverso_gan'] += 1
    return st, buckets


# ============================================================
# GRAFICAS
# ============================================================

def grafica_pnl_acumulado(ax, registros):
    fuente = detectar_fuente(registros)
    pnl = calcular_pnl_acumulado(registros)
    if not pnl:
        ax.text(0.5, 0.5, 'Sin datos', ha='center', va='center', color=C['muted'])
        return
    x = range(len(pnl))
    ax.plot(pnl, color=C['accent'], linewidth=1.5, zorder=3)
    ax.fill_between(x, pnl, 0,
                    where=[p >= 0 for p in pnl], color=C['accent2'], alpha=0.15)
    ax.fill_between(x, pnl, 0,
                    where=[p < 0 for p in pnl],  color=C['accent3'], alpha=0.15)
    ax.axhline(0, color=C['white'], linewidth=0.5, alpha=0.3)
    max_v, min_v = max(pnl), min(pnl)
    ax.axhline(max_v, color=C['accent2'], linewidth=0.5, linestyle='--', alpha=0.5)
    ax.axhline(min_v, color=C['accent3'], linewidth=0.5, linestyle='--', alpha=0.5)
    ax.text(len(pnl)*0.01, max_v, f'  Máx: {max_v:+.1f}€', color=C['accent2'], fontsize=7, va='bottom')
    ax.text(len(pnl)*0.01, min_v, f'  Mín: {min_v:+.1f}€', color=C['accent3'], fontsize=7, va='top')
    sufijo = "(PNL Real)" if fuente == 'historial' else "(PNL Simulado)"
    ax.set_title(f'PNL ACUMULADO (solo apuestas) {sufijo}')
    ax.set_xlabel('Nº apuesta')
    ax.set_ylabel('Balance (€)')


def grafica_winrate_por_rango(ax, stats):
    rangos  = [r for r in ORDEN_RANGOS if r in stats]
    wr_dir  = []
    wr_inv  = []
    for r in rangos:
        d = stats[r]['DIRECTO']
        i = stats[r]['INVERSO']
        wr_dir.append(d['gan']/d['ops']*100 if d['ops'] else np.nan)
        wr_inv.append(i['gan']/i['ops']*100 if i['ops'] else np.nan)

    x = np.arange(len(rangos))
    w = 0.35
    bars_d = ax.bar(x - w/2, wr_dir, w, label='DIRECTO', color=C['accent2'], alpha=0.8)
    bars_i = ax.bar(x + w/2, wr_inv, w, label='INVERSO', color=C['accent3'], alpha=0.8)
    ax.axhline(52.6, color=C['warn'], linewidth=1, linestyle='--', alpha=0.7, label='Umbral rentable (52.6%)')
    ax.axhline(50,   color=C['white'], linewidth=0.5, linestyle=':', alpha=0.3)
    ax.set_xticks(x)
    ax.set_xticklabels(rangos, rotation=45, ha='right')
    ax.set_ylim(0, 115)
    ax.set_title('WIN RATE POR RANGO Y MODO')
    ax.set_ylabel('Win Rate (%)')
    ax.legend()
    # Etiquetas encima de barras
    for bar in bars_d:
        h = bar.get_height()
        if not np.isnan(h) and h > 0:
            ax.text(bar.get_x()+bar.get_width()/2, h+1, f'{h:.0f}%',
                    ha='center', va='bottom', fontsize=6, color=C['accent2'])
    for bar in bars_i:
        h = bar.get_height()
        if not np.isnan(h) and h > 0:
            ax.text(bar.get_x()+bar.get_width()/2, h+1, f'{h:.0f}%',
                    ha='center', va='bottom', fontsize=6, color=C['accent3'])


def grafica_pnl_por_rango(ax, stats, registros=None):
    fuente = 'reconstructor' if not registros else detectar_fuente(registros)
    rangos = [r for r in ORDEN_RANGOS if r in stats]
    pnl_d  = [stats[r]['DIRECTO']['gan']*0.9 - stats[r]['DIRECTO']['per'] for r in rangos]
    pnl_i  = [stats[r]['INVERSO']['gan']*0.9 - stats[r]['INVERSO']['per'] for r in rangos]

    if fuente == 'historial' and registros:
        pnl_d_real = []
        pnl_i_real = []
        for r in rangos:
            pd = 0
            pi = 0
            for rec in registros:
                if rec['rango'] == r:
                    if rec['modo'] == 'DIRECTO' and rec.get('pnl') is not None:
                        pd += rec['pnl']
                    elif rec['modo'] == 'INVERSO' and rec.get('pnl') is not None:
                        pi += rec['pnl']
            pnl_d_real.append(pd)
            pnl_i_real.append(pi)
        pnl_d = pnl_d_real
        pnl_i = pnl_i_real

    sufijo = "(PNL Real)" if fuente == 'historial' else "(PNL Simulado)"
    x = np.arange(len(rangos))
    w = 0.35
    colors_d = [C['accent2'] if v >= 0 else C['accent3'] for v in pnl_d]
    colors_i = [C['accent2'] if v >= 0 else C['accent3'] for v in pnl_i]
    ax.bar(x - w/2, pnl_d, w, color=colors_d, alpha=0.85, label='DIRECTO')
    ax.bar(x + w/2, pnl_i, w, color=colors_i, alpha=0.55, label='INVERSO', hatch='//')
    ax.axhline(0, color=C['white'], linewidth=0.6, alpha=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels(rangos, rotation=45, ha='right')
    ax.set_title(f'PNL TOTAL POR RANGO Y MODO {sufijo}')
    ax.set_ylabel('PNL (€)')
    leg = ax.legend()


def grafica_mayor_gana_por_racha(ax, st, buckets):
    if st is None:
        ax.text(0.5, 0.5, 'Datos de Racha no disponibles\npara historial_rondas.txt',
                ha='center', va='center', color=C['muted'], fontsize=12)
        ax.set_title('% MAYOR_GANA POR TRAMO DE RACHA (No disponible)')
        return
    pct = []
    ops = []
    for b in buckets:
        s = st[b]
        pct.append(s['mayor_gana']/s['ops']*100 if s['ops'] else 0)
        ops.append(s['ops'])

    colors = []
    for p in pct:
        if p >= 52.6:   colors.append(C['accent2'])
        elif p <= 47.4: colors.append(C['accent3'])
        else:           colors.append(C['warn'])

    bars = ax.bar(buckets, pct, color=colors, alpha=0.85, edgecolor=C['border'])
    ax.axhline(52.6, color=C['accent2'], linewidth=1, linestyle='--', alpha=0.7, label='Umbral DIRECTO (52.6%)')
    ax.axhline(47.4, color=C['accent3'], linewidth=1, linestyle='--', alpha=0.7, label='Umbral INVERSO (47.4%)')
    ax.axhline(50,   color=C['white'],   linewidth=0.5, linestyle=':', alpha=0.3)
    ax.set_ylim(0, 100)
    ax.set_title('% MAYOR_GANA POR TRAMO DE RACHA')
    ax.set_xlabel('Racha_10r (%)')
    ax.set_ylabel('Mayor gana (%)')
    ax.legend(fontsize=7)
    for bar, o in zip(bars, ops):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1,
                f'n={o}', ha='center', va='bottom', fontsize=7, color=C['muted'])


def grafica_distribucion_modos(ax, registros):
    modos = defaultdict(int)
    for r in registros:
        modos[r['modo']] += 1
    labels = list(modos.keys())
    sizes  = list(modos.values())
    colors = [C['accent2'] if l=='DIRECTO' else C['accent3'] if l=='INVERSO' else C['muted']
              for l in labels]
    wedges, texts, autotexts = ax.pie(
        sizes, labels=labels, colors=colors, autopct='%1.1f%%',
        startangle=90, pctdistance=0.75,
        wedgeprops={'edgecolor': C['border'], 'linewidth': 1.5}
    )
    for t in texts:      t.set_color(C['text'])
    for t in autotexts:  t.set_color(C['bg']); t.set_fontsize(9); t.set_fontweight('bold')
    ax.set_title('DISTRIBUCIÓN DE MODOS')


def grafica_ops_por_rango(ax, stats):
    rangos = [r for r in ORDEN_RANGOS if r in stats]
    ops_d  = [stats[r]['DIRECTO']['ops'] for r in rangos]
    ops_i  = [stats[r]['INVERSO']['ops'] for r in rangos]
    ops_s  = [stats[r]['SKIP']['ops']    for r in rangos]
    x = np.arange(len(rangos))
    w = 0.25
    ax.bar(x - w,   ops_d, w, label='DIRECTO', color=C['accent2'], alpha=0.85)
    ax.bar(x,       ops_i, w, label='INVERSO', color=C['accent3'], alpha=0.85)
    ax.bar(x + w,   ops_s, w, label='SKIP',    color=C['muted'],   alpha=0.60)
    ax.set_xticks(x)
    ax.set_xticklabels(rangos, rotation=45, ha='right')
    ax.set_title('VOLUMEN DE OPERACIONES POR RANGO')
    ax.set_ylabel('Nº operaciones')
    ax.legend()


def grafica_heatmap_racha_rango(ax, registros):
    fuente = detectar_fuente(registros)
    if fuente == 'historial':
        ax.text(0.5, 0.5, 'Heatmap no disponible\npara historial_rondas.txt',
                ha='center', va='center', color=C['muted'], fontsize=12)
        ax.set_title('HEATMAP: % MAYOR_GANA (Racha × Rango) (No disponible)')
        return
    buckets = ['≤20','21-30','31-40','41-50','51-60','61-70','>70']
    rangos  = [r for r in ORDEN_RANGOS if any(rec['rango']==r for rec in registros)]

    def bucket(r):
        if r <= 20:   return '≤20'
        elif r <= 30: return '21-30'
        elif r <= 40: return '31-40'
        elif r <= 50: return '41-50'
        elif r <= 60: return '51-60'
        elif r <= 70: return '61-70'
        else:         return '>70'

    mat_count = defaultdict(lambda: defaultdict(int))
    mat_mayor = defaultdict(lambda: defaultdict(int))
    for rec in registros:
        racha = rec.get('racha')
        if racha is None:
            continue
        b = bucket(racha)
        r = rec['rango']
        mat_count[b][r] += 1
        if rec['mayor_gana']: mat_mayor[b][r] += 1

    data = np.full((len(buckets), len(rangos)), np.nan)
    for i, b in enumerate(buckets):
        for j, r in enumerate(rangos):
            if mat_count[b][r] >= 3:
                data[i][j] = mat_mayor[b][r] / mat_count[b][r] * 100

    im = ax.imshow(data, aspect='auto', cmap='RdYlGn', vmin=20, vmax=80,
                   interpolation='nearest')
    ax.set_xticks(range(len(rangos)))
    ax.set_yticks(range(len(buckets)))
    ax.set_xticklabels(rangos, rotation=45, ha='right', fontsize=7)
    ax.set_yticklabels(buckets, fontsize=7)
    ax.set_title('HEATMAP: % MAYOR_GANA (Racha × Rango)')
    ax.set_xlabel('Rango diferencia')
    ax.set_ylabel('Racha_10r (%)')
    plt.colorbar(im, ax=ax, label='% MayorGana', shrink=0.8)

    for i in range(len(buckets)):
        for j in range(len(rangos)):
            if not np.isnan(data[i][j]):
                n = mat_count[buckets[i]][rangos[j]]
                ax.text(j, i, f'{data[i][j]:.0f}%\nn={n}',
                        ha='center', va='center', fontsize=5.5,
                        color='black' if 35 < data[i][j] < 65 else 'white')


def grafica_pnl_simulado_umbrales(ax, registros):
    """Simula PNL con distintas combinaciones de umbrales DIR/INV."""
    fuente = detectar_fuente(registros)
    if fuente == 'historial':
        ax.text(0.5, 0.5, 'Simulación no disponible\npara historial_rondas.txt',
                ha='center', va='center', color=C['muted'], fontsize=12)
        ax.set_title('SIMULACIÓN PNL CON DISTINTOS UMBRALES (No disponible)')
        return
    configs = [
        (70, 30, 'Actual (70/30)',  C['muted']),
        (60, 40, 'Nuevo (60/40)',   C['accent2']),
        (65, 35, 'Medio (65/35)',   C['accent']),
        (60, 30, 'Dir60/Inv30',     C['warn']),
        (70, 40, 'Dir70/Inv40',     C['blue']),
    ]

    def simular(registros, umbral_dir, umbral_inv):
        acum = 0.0
        curva = []
        for rec in registros:
            racha = rec.get('racha')
            if racha is None:
                continue
            if racha >= umbral_dir:
                acierto = rec.get('mayor_gana', False)
                acum += 0.9 if acierto else -1.0
                curva.append(acum)
            elif racha <= umbral_inv:
                acierto = not rec.get('mayor_gana', True)
                acum += 0.9 if acierto else -1.0
                curva.append(acum)
        return curva

    for ud, ui, label, color in configs:
        curva = simular(registros, ud, ui)
        if curva:
            ax.plot(curva, label=f'{label} → {curva[-1]:+.1f}€', color=color,
                    linewidth=1.5 if 'Nuevo' in label else 1.0,
                    linestyle='-' if 'Nuevo' in label or 'Actual' in label else '--')

    ax.axhline(0, color=C['white'], linewidth=0.5, alpha=0.3)
    ax.set_title('SIMULACIÓN PNL CON DISTINTOS UMBRALES')
    ax.set_xlabel('Nº apuesta')
    ax.set_ylabel('PNL acumulado (€)')
    ax.legend(fontsize=7)


def simular_pnl_confianza(registros, conf_umbral, min_ops=3):
    """
    Simula apuestas basándose en win rate histórico por rango+modo.
    Apuesta al modo que supera conf_umbral. Sin multiplicadores.
    Devuelve (curva_pnl, n_ops, n_skips).
    """
    stats = stats_por_rango(registros)
    acum = 0.0
    curva = []
    n_ops = 0
    n_skips = 0
    for rec in registros:
        rango = rec['rango']
        s = stats.get(rango)
        if not s:
            n_skips += 1
            continue
        d = s['DIRECTO']
        i = s['INVERSO']
        d_wr = d['gan'] / d['ops'] * 100 if d['ops'] >= min_ops else 0.0
        i_wr = i['gan'] / i['ops'] * 100 if i['ops'] >= min_ops else 0.0
        # Elegir modo con mayor confianza que supere el umbral
        if d_wr >= conf_umbral and d_wr >= i_wr:
            acierto = rec['mayor_gana']
        elif i_wr >= conf_umbral:
            acierto = not rec['mayor_gana']
        else:
            n_skips += 1
            continue
        acum += 0.9 if acierto else -1.0
        n_ops += 1
        curva.append(acum)
    return curva, n_ops, n_skips


def grafica_pnl_confianza(ax, registros, conf_umbral_actual=60.0):
    """Compara curvas de PNL para distintos CONF_UMBRAL del heatmap."""
    fuente = detectar_fuente(registros)
    if fuente == 'historial':
        ax.text(0.5, 0.5, 'Estrategia Confianza no disponible\npara historial_rondas.txt',
                ha='center', va='center', color=C['muted'], fontsize=12)
        ax.set_title(f'PNL SIMULADO — ESTRATEGIA CONFIANZA (No disponible)')
        return
    umbrales = [
        (45, C['accent3'],  '--', 0.7),
        (50, C['warn'],     '--', 0.8),
        (55, C['muted'],    '-',  0.9),
        (60, C['accent'],   '-',  1.5),
        (65, C['accent2'],  '-',  1.5),
        (70, C['blue'],     '-',  1.2),
        (75, C['white'],    '--', 0.8),
    ]
    for umbral, color, ls, lw in umbrales:
        curva, n_ops, n_skips = simular_pnl_confianza(registros, umbral)
        if not curva:
            continue
        resaltado = abs(umbral - conf_umbral_actual) < 1
        label = f'Conf≥{umbral}%  →  {curva[-1]:+.1f}€  ({n_ops} ops)'
        ax.plot(curva, color=color, linewidth=lw * (1.5 if resaltado else 1.0),
                linestyle=ls, alpha=1.0 if resaltado else 0.75,
                label=label,
                zorder=5 if resaltado else 2)
        if resaltado:
            ax.plot(len(curva)-1, curva[-1], 'o', color=color, markersize=8, zorder=6)

    if not any(abs(u - conf_umbral_actual) < 1 for u, *_ in umbrales):
        curva, n_ops, n_skips = simular_pnl_confianza(registros, conf_umbral_actual)
        if curva:
            ax.plot(curva, color='#FF00FF', linewidth=2.0, linestyle='-',
                    label=f'Conf≥{conf_umbral_actual:.0f}% (Sheets) → {curva[-1]:+.1f}€ ({n_ops} ops)',
                    zorder=5)

    ax.axhline(0, color=C['white'], linewidth=0.5, alpha=0.3)
    ax.set_title(f'PNL SIMULADO — ESTRATEGIA CONFIANZA HEATMAP  (umbral Sheets: {conf_umbral_actual:.0f}%)')
    ax.set_xlabel('Nº apuesta')
    ax.set_ylabel('PNL acumulado (€)')
    ax.legend(fontsize=7, loc='upper left')


def grafica_confianza_heatmap(ax, registros, conf_umbral=60.0, min_ops=3):
    """Heatmap de win rate por rango×modo, resaltando celdas que superan el umbral."""
    fuente = detectar_fuente(registros)
    if fuente == 'historial':
        ax.text(0.5, 0.5, 'Heatmap no disponible\npara historial_rondas.txt',
                ha='center', va='center', color=C['muted'], fontsize=12)
        ax.set_title(f'WIN RATE POR RANGO × MODO (No disponible)')
        return
    stats = stats_por_rango(registros)
    rangos = [r for r in ORDEN_RANGOS if r in stats]
    modos  = ['DIRECTO', 'INVERSO']

    data = np.full((2, len(rangos)), np.nan)
    for j, rng in enumerate(rangos):
        s = stats[rng]
        for i, modo in enumerate(modos):
            m = s[modo]
            if m['ops'] >= min_ops:
                data[i][j] = m['gan'] / m['ops'] * 100

    im = ax.imshow(data, aspect='auto', cmap='RdYlGn', vmin=30, vmax=80,
                   interpolation='nearest')
    ax.set_xticks(range(len(rangos)))
    ax.set_yticks([0, 1])
    ax.set_xticklabels(rangos, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(modos, fontsize=9)
    ax.set_title(f'WIN RATE POR RANGO × MODO  (umbral confianza: {conf_umbral:.0f}%)')
    plt.colorbar(im, ax=ax, label='Win Rate (%)', shrink=0.8)

    for i in range(2):
        for j in range(len(rangos)):
            if np.isnan(data[i][j]):
                continue
            v = data[i][j]
            txt_col = 'black' if 35 < v < 65 else 'white'
            supera = v >= conf_umbral
            peso   = 'bold' if supera else 'normal'
            borde  = '★' if supera else ''
            ax.text(j, i, f'{borde}{v:.0f}%{borde}', ha='center', va='center',
                    fontsize=8, color=txt_col, fontweight=peso)
            if supera:
                rect = plt.Rectangle((j-0.5, i-0.5), 1, 1,
                                      linewidth=2, edgecolor='#00FF88',
                                      facecolor='none', zorder=3)
                ax.add_patch(rect)


def grafica_rachas_consecutivas(ax, registros):
    """Distribución de rachas de aciertos y fallos consecutivos en apuestas."""
    fuente = detectar_fuente(registros)
    aciertos_run = []
    fallos_run   = []
    cur_ac = 0
    cur_fa = 0
    for rec in registros:
        if rec['modo'] == 'SKIP':
            continue
        if fuente == 'historial':
            ok = rec.get('acierto') is True
        else:
            if rec['modo'] == 'DIRECTO':
                ok = rec['mayor_gana']
            else:
                ok = not rec['mayor_gana']
        if ok:
            if cur_fa > 0: fallos_run.append(cur_fa); cur_fa = 0
            cur_ac += 1
        else:
            if cur_ac > 0: aciertos_run.append(cur_ac); cur_ac = 0
            cur_fa += 1
    if cur_ac > 0: aciertos_run.append(cur_ac)
    if cur_fa > 0: fallos_run.append(cur_fa)

    if not aciertos_run and not fallos_run:
        ax.text(0.5, 0.5, 'Sin apuestas', ha='center', va='center', color=C['muted'])
        ax.set_title('RACHAS CONSECUTIVAS DE ACIERTOS/FALLOS')
        return

    max_run = max(max(aciertos_run, default=0), max(fallos_run, default=0))
    bins = range(1, max_run + 2)
    ax.hist(aciertos_run, bins=bins, alpha=0.7, color=C['accent2'], label='Rachas aciertos', align='left')
    ax.hist(fallos_run,   bins=bins, alpha=0.7, color=C['accent3'], label='Rachas fallos',   align='left')
    ax.set_title('RACHAS CONSECUTIVAS DE ACIERTOS/FALLOS')
    ax.set_xlabel('Longitud de racha')
    ax.set_ylabel('Frecuencia')
    ax.legend()
    if aciertos_run:
        ax.text(0.98, 0.95, f'Máx aciertos: {max(aciertos_run)}\nMáx fallos: {max(fallos_run, default=0)}',
                transform=ax.transAxes, ha='right', va='top', fontsize=8, color=C['text'])


def simular_estrategia_real(registros, tipo='actual'):
    """Simula estrategias usando PNL real de historial."""
    fuente = detectar_fuente(registros)
    if fuente != 'historial':
        return None, 0, 0
    
    stats = stats_por_rango(registros)
    acum = 0.0
    curva = []
    n_ops = 0
    
    for rec in registros:
        if rec['modo'] == 'SKIP':
            continue
        if rec.get('pnl') is None:
            continue
        
        rango = rec['rango']
        modo = rec['modo']
        pnl_real = rec['pnl']
        
        if tipo == 'actual':
            acum += pnl_real
            n_ops += 1
        elif tipo == 'optima_rango':
            s = stats.get(rango, {})
            d = s.get('DIRECTO', {'gan': 0, 'per': 0})
            i = s.get('INVERSO', {'gan': 0, 'per': 0})
            d_pnl = d['gan'] - d['per']
            i_pnl = i['gan'] - i['per']
            mejor = 'DIRECTO' if d_pnl >= i_pnl else 'INVERSO'
            if mejor == modo:
                acum += pnl_real
                n_ops += 1
        elif tipo == 'solo_directo':
            if modo == 'DIRECTO':
                acum += pnl_real
                n_ops += 1
        elif tipo == 'solo_inverso':
            if modo == 'INVERSO':
                acum += pnl_real
                n_ops += 1
        elif tipo == 'alta_conf':
            mult = rec.get('mult', 1)
            if mult >= 2:
                acum += pnl_real
                n_ops += 1
        
        curva.append(acum)
    
    return curva, n_ops, 0


def grafica_estrategia_optima(ax, registros):
    """Compara estrategias usando PNL real y marca puntos de cruce."""
    fuente = detectar_fuente(registros)
    if fuente != 'historial':
        ax.text(0.5, 0.5, 'Estrategia Óptima solo disponible\npara historial_rondas.txt (datos reales)',
                ha='center', va='center', color=C['muted'], fontsize=11)
        ax.set_title('ESTRATEGIA ÓPTIMA vs REAL (No disponible)')
        return
    
    estrategias = [
        ('actual', C['accent'], '-', 1.5, 'Estrategia Real'),
        ('optima_rango', C['accent2'], '-', 1.8, 'Óptima por Rango'),
        ('solo_directo', C['accent3'], '--', 1.0, 'Solo DIRECTO'),
        ('solo_inverso', C['warn'], '--', 1.0, 'Solo INVERSO'),
    ]
    
    curvas = {}
    for tipo, color, ls, lw, label in estrategias:
        curva, n_ops, _ = simular_estrategia_real(registros, tipo)
        if curva:
            curvas[tipo] = curva
            pnl_final = curva[-1]
            ax.plot(curva, color=color, linewidth=lw, linestyle=ls,
                    label=f'{label} → {pnl_final:+.1f}€ ({n_ops} ops)',
                    zorder=3 if 'optima' in tipo else 2)
    
    if 'actual' in curvas and 'optima_rango' in curvas:
        act = curvas['actual']
        opt = curvas['optima_rango']
        cruces = []
        for i in range(1, min(len(act), len(opt))):
            if (act[i-1] <= opt[i-1] and act[i] > opt[i]) or (act[i-1] >= opt[i-1] and act[i] < opt[i]):
                cruces.append(i)
        for cx in cruces:
            ax.axvline(cx, color=C['white'], linewidth=0.8, alpha=0.4, linestyle=':')
            ax.plot(cx, act[cx], 'o', color=C['white'], markersize=6, zorder=5)
        if cruces:
            ax.text(0.02, 0.98, f'Cruces: {len(cruces)}', transform=ax.transAxes,
                    fontsize=8, color=C['white'], va='top')
    
    ax.axhline(0, color=C['white'], linewidth=0.5, alpha=0.3)
    ax.set_title('ESTRATEGIA ÓPTIMA vs REAL (PNL Real con Multiplicadores)')
    ax.set_xlabel('Nº apuesta')
    ax.set_ylabel('PNL acumulado (€)')
    ax.legend(fontsize=7, loc='upper left')


def grafica_pnl_real_heatmap(ax, registros):
    """Heatmap de PNL real por rango × modo."""
    fuente = detectar_fuente(registros)
    if fuente != 'historial':
        ax.text(0.5, 0.5, 'Heatmap PNL Real solo disponible\npara historial_rondas.txt',
                ha='center', va='center', color=C['muted'], fontsize=11)
        ax.set_title('PNL REAL POR RANGO × MODO (No disponible)')
        return
    
    rangos = ORDEN_RANGOS
    rangos_presentes = [r for r in rangos if any(rec['rango'] == r and rec.get('pnl') is not None for rec in registros)]
    
    data_directo = []
    data_inverso = []
    for r in rangos_presentes:
        pnl_d = sum(rec['pnl'] for rec in registros if rec['rango'] == r and rec['modo'] == 'DIRECTO' and rec.get('pnl') is not None)
        pnl_i = sum(rec['pnl'] for rec in registros if rec['rango'] == r and rec['modo'] == 'INVERSO' and rec.get('pnl') is not None)
        data_directo.append(pnl_d)
        data_inverso.append(pnl_i)
    
    x = np.arange(len(rangos_presentes))
    w = 0.35
    colors_d = [C['accent2'] if v >= 0 else C['accent3'] for v in data_directo]
    colors_i = [C['accent2'] if v >= 0 else C['accent3'] for v in data_inverso]
    
    ax.bar(x - w/2, data_directo, w, color=colors_d, alpha=0.85, label='DIRECTO (PNL Real)')
    ax.bar(x + w/2, data_inverso, w, color=colors_i, alpha=0.55, label='INVERSO (PNL Real)', hatch='//')
    ax.axhline(0, color=C['white'], linewidth=0.6, alpha=0.4)
    ax.set_xticks(x)
    ax.set_xticklabels(rangos_presentes, rotation=45, ha='right')
    ax.set_title('PNL REAL POR RANGO × MODO (Con Multiplicadores)')
    ax.set_ylabel('PNL (€)')
    ax.legend()
    
    pnl_total_d = sum(data_directo)
    pnl_total_i = sum(data_inverso)
    ax.text(0.98, 0.02, f'Total DIR: {pnl_total_d:+.1f}€ | Total INV: {pnl_total_i:+.1f}€',
            transform=ax.transAxes, ha='right', va='bottom', fontsize=8, color=C['text'])


# ============================================================
# APP PRINCIPAL
# ============================================================


# ============================================================
# EXPLICACIONES POR GRÁFICA
# ============================================================

EXPLICACIONES = {
    'pnl': {
        'titulo': '📈 PNL Acumulado',
        'texto': (
            "Muestra la evolución de tu balance apuesta a apuesta, "
            "en orden cronológico.\n\n"
            "Cada acierto suma +0.90€ y cada fallo resta 1.00€. "
            "La curva refleja si el sistema es rentable a largo plazo.\n\n"
            "▸  Tendencia ALCISTA → sistema rentable\n"
            "▸  Tendencia BAJISTA → sistema pierde dinero\n"
            "▸  Las zonas en VERDE son períodos de beneficio\n"
            "▸  Las zonas en ROJO son drawdowns (pérdidas consecutivas)\n\n"
            "Las líneas discontinuas marcan el máximo y mínimo histórico. "
            "Cuanto más plana y ascendente sea la curva, más estable y "
            "rentable es el sistema."
        ),
    },
    'winrate': {
        'titulo': '🎯 Win Rate por Rango',
        'texto': (
            "Muestra el porcentaje de aciertos de cada modo (DIRECTO e INVERSO) "
            "en cada rango de diferencia entre bandos.\n\n"
            "▸  DIRECTO acierta cuando gana el bando mayor\n"
            "▸  INVERSO acierta cuando gana el bando menor\n\n"
            "La línea amarilla marca el 52.6% — el umbral mínimo de "
            "rentabilidad. Con PNL asimétrico (+0.90 / -1.00) necesitas "
            "superar ese porcentaje para ganar dinero a largo plazo.\n\n"
            "▸  Barras por ENCIMA de la línea → modo rentable en ese rango\n"
            "▸  Barras por DEBAJO → modo pierde dinero en ese rango\n\n"
            "Úsalo para identificar en qué rangos funciona cada modo "
            "y cuáles deberían excluirse."
        ),
    },
    'pnl_rango': {
        'titulo': '💰 PNL por Rango',
        'texto': (
            "Muestra el beneficio o pérdida total acumulada de cada rango "
            "de diferencia, separado por modo.\n\n"
            "▸  Barra VERDE → ese rango y modo genera dinero\n"
            "▸  Barra ROJA  → ese rango y modo pierde dinero\n\n"
            "Las barras sólidas son DIRECTO, las rayadas son INVERSO. "
            "A diferencia del Win Rate, aquí ves el impacto real en euros, "
            "no solo el porcentaje.\n\n"
            "Un rango con muchas operaciones y PNL negativo es "
            "especialmente peligroso — considera excluirlo. "
            "Un rango con PNL positivo consistente es tu zona de confort."
        ),
    },
    'racha': {
        'titulo': '⚡ Análisis de Racha',
        'texto': (
            "Muestra qué porcentaje de veces gana el bando MAYOR según "
            "el tramo de Racha_10r (las últimas 10 rondas).\n\n"
            "▸  VERDE  → el bando mayor gana más del 52.6% → apostar DIRECTO\n"
            "▸  ROJO   → el bando mayor gana menos del 47.4% → apostar INVERSO\n"
            "▸  NARANJA → zona muerta, resultado aleatorio → SKIP\n\n"
            "La n= sobre cada barra indica cuántas rondas hay en ese tramo. "
            "Tramos con pocas muestras (n<30) son menos fiables.\n\n"
            "Esta gráfica es la base para calibrar los umbrales UMBRAL_DIRECTO "
            "y UMBRAL_INVERSO en tu configuración."
        ),
    },
    'heatmap': {
        'titulo': '🔥 Heatmap Racha × Rango',
        'texto': (
            "Cruza dos variables simultáneamente: el tramo de Racha_10r "
            "(eje Y) y el rango de diferencia entre bandos (eje X).\n\n"
            "Cada celda muestra el % de veces que ganó el bando mayor "
            "en esa combinación concreta. Solo se muestran celdas con "
            "al menos 3 muestras.\n\n"
            "▸  VERDE intenso → bando mayor gana mucho → DIRECTO\n"
            "▸  ROJO intenso  → bando mayor pierde mucho → INVERSO\n"
            "▸  AMARILLO      → zona de incertidumbre → SKIP\n\n"
            "Es la gráfica más avanzada: permite detectar combinaciones "
            "específicas de racha + rango que son especialmente rentables "
            "o peligrosas, más allá de analizar cada variable por separado."
        ),
    },
    'simulacion': {
        'titulo': '🔬 Simulación de Umbrales',
        'texto': (
            "Simula cómo habría evolucionado el balance con distintas "
            "combinaciones de UMBRAL_DIRECTO y UMBRAL_INVERSO, usando "
            "los mismos datos históricos.\n\n"
            "Cada línea es una configuración diferente:\n"
            "▸  Actual (70/30)  → configuración original\n"
            "▸  Nuevo (60/40)   → configuración optimizada por datos\n"
            "▸  Medio (65/35)   → punto intermedio\n"
            "▸  Dir60/Inv30     → solo mejora DIRECTO\n"
            "▸  Dir70/Inv40     → solo mejora INVERSO\n\n"
            "El PNL final de cada configuración aparece en la leyenda. "
            "Úsalo para decidir qué umbrales configurar en Sheets sin "
            "arriesgar dinero real."
        ),
    },
    'rachas': {
        'titulo': '🔗 Rachas Consecutivas',
        'texto': (
            "Muestra con qué frecuencia aparecen rachas de aciertos "
            "o fallos consecutivos en las apuestas.\n\n"
            "▸  VERDE → distribución de rachas de aciertos seguidos\n"
            "▸  ROJO  → distribución de rachas de fallos seguidos\n\n"
            "Barras altas a la izquierda (rachas cortas) indican que "
            "el sistema alterna resultados con frecuencia — buena señal "
            "de estabilidad. Barras altas a la derecha indican períodos "
            "de pérdidas prolongadas que debes preparar psicológicamente.\n\n"
            "El máximo de fallos consecutivos te dice cuánto bankroll "
            "mínimo necesitas para sobrevivir al peor drawdown histórico."
        ),
    },
    'confianza': {
        'titulo': '🎯 Simulación por Confianza',
        'texto': (
            "Simula apuestas basándose en el win rate histórico por rango "
            "y modo (DIRECTO/INVERSO), usando el CONF_UMBRAL de Sheets.\n\n"
            "Para cada ronda, calcula el win rate histórico de DIRECTO e "
            "INVERSO en ese rango. Si alguno supera el umbral, apuesta "
            "al de mayor confianza. Si ninguno lo supera, SKIP.\n\n"
            "▸  La gráfica superior compara varias curvas de PNL según "
            "distintos umbrales (45% a 75%)\n"
            "▸  La gráfica inferior es el heatmap de win rate con las "
            "celdas que superan el umbral marcadas en verde\n\n"
            "Umbral actual tomado del campo CONF_UMBRAL en Sheets "
            "(por defecto 60%). Puedes cambiarlo en el campo de texto "
            "y pulsar Recalcular.\n\n"
            "▸  A mayor umbral → menos apuestas pero más selectivas\n"
            "▸  A menor umbral → más apuestas pero más ruido\n\n"
            "La curva resaltada es siempre la del umbral actual."
        ),
    },
    'resumen': {
        'titulo': '📊 Distribución y Volumen',
        'texto': (
            "Dos gráficas complementarias sobre el volumen de operaciones.\n\n"
            "▸  IZQUIERDA (pie): qué proporción del total son DIRECTO, "
            "INVERSO y SKIP. Un exceso de SKIP puede indicar umbrales "
            "demasiado estrictos que dejan pasar oportunidades.\n\n"
            "▸  DERECHA (barras): cuántas operaciones hay en cada rango "
            "de diferencia, desglosadas por modo.\n\n"
            "Rangos con muy pocas operaciones (n<15) tienen estadísticas "
            "poco fiables — sus resultados pueden deberse al azar. "
            "Fiarse más de los rangos con mayor volumen."
        ),
    },
    'estrategia': {
        'titulo': '🏆 Estrategia Óptima',
        'texto': (
            "Compara el PNL real de diferentes estrategias usando los "
            "multiplicadores reales de cada apuesta.\n\n"
            "▸  ESTRATEGIA REAL: lo que realmente apostaste (modo + mult)\n"
            "▸  ÓPTIMA POR RANGO: elige DIRECTO o INVERSO según qué modo "
            "tuvo mejor PNL histórico en ese rango\n"
            "▸  SOLO DIRECTO / SOLO INVERSO: apostarlo todo a un solo modo\n\n"
            "Los puntos de CRUCE (círculos blancos) muestran dónde la "
            "estrategia óptima supera a la real. Esos puntos indican "
            "oportunidades de mejora.\n\n"
            "Esta gráfica solo está disponible para historial_rondas.txt "
            "porque usa PNL real con multiplicadores."
        ),
    },
    'pnl_real': {
        'titulo': '💵 PNL Real Heatmap',
        'texto': (
            "Muestra el PNL real acumulado por cada combinación de "
            "RANGO × MODO (DIRECTO/INVERSO), usando los multiplicadores "
            "reales de las apuestas.\n\n"
            "A diferencia del Win Rate que solo cuenta aciertos, aquí "
            "ves el BENEFICIO REAL en euros.\n\n"
            "▸  VERDE → ese rango+modo generó beneficios\n"
            "▸  ROJO → ese rango+modo generó pérdidas\n\n"
            "Usa esta gráfica para decidir qué rangos excluir o favoring, "
            "porque refleja el resultado económico exacto, no solo "
            "porcentajes de acierto."
        ),
    },
}

class AnalizadorApp:
    def __init__(self, root, archivo=None):
        self.root = root
        self.root.title("◈ ANALIZADOR DE DATOS — Acertador Senior Pro")
        self.root.configure(bg=C['bg'])
        self.root.geometry("1500x950")
        self.registros = []
        self.archivo   = archivo
        self.fuente_actual = 'reconstructor'

        self._construir_ui()

        if archivo and Path(archivo).exists():
            self._cargar(archivo)
        else:
            self._auto_abrir()

    def _construir_ui(self):
        # Header
        hf = tk.Frame(self.root, bg='#020810', height=52)
        hf.pack(fill='x')
        hf.pack_propagate(False)
        tk.Frame(hf, bg=C['accent'], height=2).pack(fill='x', side='top')
        inner = tk.Frame(hf, bg='#020810')
        inner.pack(fill='both', expand=True, padx=12)
        tk.Label(inner, text="◈ ANALIZADOR DE DATOS", font=('Consolas', 13, 'bold'),
                 bg='#020810', fg=C['accent']).pack(side='left', pady=10)

        btn_frame = tk.Frame(inner, bg='#020810')
        btn_frame.pack(side='right', pady=8)

        self._fuente_var = tk.StringVar(value='reconstructor')
        tk.Label(btn_frame, text="📊 Fuente:", font=('Consolas', 9),
                 bg='#020810', fg=C['muted']).pack(side='left', padx=(0, 4))
        rb_recons = tk.Radiobutton(btn_frame, text="Reconstructor", variable=self._fuente_var,
                   value='reconstructor', bg='#020810', fg=C['accent'], selectcolor=C['panel'],
                   activebackground='#020810', font=('Consolas', 8),
                   command=self._cambiar_fuente).pack(side='left', padx=2)
        rb_hist = tk.Radiobutton(btn_frame, text="Historial", variable=self._fuente_var,
                   value='historial', bg='#020810', fg=C['accent'], selectcolor=C['panel'],
                   activebackground='#020810', font=('Consolas', 8),
                   command=self._cambiar_fuente).pack(side='left', padx=2)

        tk.Frame(btn_frame, bg=C['muted'], width=1, height=20).pack(side='left', padx=8)

        tk.Button(btn_frame, text="📂 ABRIR .TXT", font=('Consolas', 9, 'bold'),
                  bg=C['panel'], fg=C['accent'], relief='raised', bd=1,
                  command=self._abrir_archivo).pack(side='left', padx=4)

        tk.Button(btn_frame, text="💾 EXPORTAR PNG", font=('Consolas', 9, 'bold'),
                  bg=C['panel'], fg=C['accent2'], relief='raised', bd=1,
                  command=self._exportar).pack(side='left', padx=4)

        tk.Label(btn_frame, text="  CONF_UMBRAL:", font=('Consolas', 9),
                 bg='#020810', fg=C['muted']).pack(side='left', padx=(12, 2))
        self._conf_var = tk.StringVar(value='60')
        tk.Entry(btn_frame, textvariable=self._conf_var, width=5,
                 font=('Consolas', 9), bg=C['panel'], fg=C['accent'],
                 insertbackground=C['accent'], relief='flat').pack(side='left')
        tk.Label(btn_frame, text="%", font=('Consolas', 9),
                 bg='#020810', fg=C['muted']).pack(side='left')
        tk.Button(btn_frame, text="↺ Recalcular", font=('Consolas', 9, 'bold'),
                  bg=C['panel'], fg=C['warn'], relief='raised', bd=1,
                  command=self._recalcular_confianza).pack(side='left', padx=4)

        self._lbl_archivo = tk.Label(inner, text="Sin archivo cargado",
                                     font=('Consolas', 8), bg='#020810', fg=C['muted'])
        self._lbl_archivo.pack(side='left', padx=16)

        self._lbl_fuente = tk.Label(inner, text="",
                                     font=('Consolas', 8), bg='#020810', fg=C['accent2'])
        self._lbl_fuente.pack(side='left', padx=8)

        # Tabs
        style = ttk.Style()
        style.theme_use('default')
        style.configure('Dark.TNotebook',        background=C['bg'],    borderwidth=0)
        style.configure('Dark.TNotebook.Tab',    background=C['panel'], foreground=C['muted'],
                         font=('Consolas', 9, 'bold'), padding=[12, 6])
        style.map('Dark.TNotebook.Tab',
                  background=[('selected', C['border'])],
                  foreground=[('selected', C['accent'])])

        self.nb = ttk.Notebook(self.root, style='Dark.TNotebook')
        self.nb.pack(fill='both', expand=True, padx=6, pady=6)

        self.tabs = {}
        tabs_def = [
            ('pnl',        '📈 PNL Acumulado'),
            ('winrate',    '🎯 Win Rate'),
            ('pnl_rango',  '💰 PNL por Rango'),
            ('racha',      '⚡ Análisis Racha'),
            ('heatmap',    '🔥 Heatmap'),
            ('simulacion', '🔬 Simulación Umbrales'),
            ('confianza',  '🎯 Confianza'),
            ('rachas',     '🔗 Rachas Consecutivas'),
            ('resumen',    '📊 Distribución'),
            ('estrategia', '🏆 Estrategia Óptima'),
            ('pnl_real',   '💵 PNL Real'),
        ]
        for key, label in tabs_def:
            frame = tk.Frame(self.nb, bg=C['bg'])
            self.nb.add(frame, text=label)
            self.tabs[key] = frame

        self._lbl_info = tk.Label(self.root,
                                  text="Carga un archivo reconstructor_data_AI.txt para comenzar",
                                  font=('Consolas', 10), bg=C['bg'], fg=C['muted'])
        self._lbl_info.pack(pady=4)

    def _auto_abrir(self):
        """Auto-detecta y carga el archivo según la fuente seleccionada."""
        base = Path(__file__).parent
        fuente = self._fuente_var.get()
        if fuente == 'reconstructor':
            ruta = base / 'reconstructor_data_AI.txt'
        else:
            ruta = base / 'historial_rondas.txt'
        if ruta.exists() and ruta.stat().st_size > 0:
            self._cargar(str(ruta))

    def _hablar(self, texto):
        import threading
        import subprocess
        # Limpiar el texto de símbolos especiales para la voz
        import re
        limpio = re.sub(r'[▸◈📈🎯💰⚡🔥🔬🔗📊▲►•→←]', '', texto)
        limpio = re.sub(r'\n+', '. ', limpio).strip()
        def _run():
            try:
                subprocess.run(
                    [r'c:\Python\voice.exe', limpio],
                    capture_output=True, timeout=60
                )
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _cambiar_fuente(self):
        self.fuente_actual = self._fuente_var.get()
        self._lbl_fuente.config(text=f"[{self.fuente_actual.upper()}]", fg=C['accent2'])
        self._auto_abrir()

    def _abrir_archivo(self):
        fuente = self._fuente_var.get()
        if fuente == 'reconstructor':
            ruta = filedialog.askopenfilename(
                title="Seleccionar reconstructor_data_AI.txt",
                filetypes=[("Archivos de texto", "*.txt"), ("Todos", "*.*")],
                initialfile="reconstructor_data_AI.txt"
            )
        else:
            ruta = filedialog.askopenfilename(
                title="Seleccionar historial_rondas.txt",
                filetypes=[("Archivos de texto", "*.txt"), ("Todos", "*.*")],
                initialfile="historial_rondas.txt"
            )
        if ruta:
            self._cargar(ruta)

    def _cargar(self, ruta):
        try:
            nombre = Path(ruta).name.lower()
            if 'historial' in nombre:
                self.registros = parsear_historial(ruta)
                self.fuente_actual = 'historial'
                self._fuente_var.set('historial')
            else:
                self.registros = parsear(ruta)
                self.fuente_actual = 'reconstructor'
                self._fuente_var.set('reconstructor')
            self.archivo   = ruta
            self._lbl_archivo.config(text=f"✓ {nombre}", fg=C['accent2'])
            self._lbl_fuente.config(text=f"[{self.fuente_actual.upper()}]", fg=C['accent2'])

            ops   = sum(1 for r in self.registros if r['modo'] != 'SKIP')
            skips = sum(1 for r in self.registros if r['modo'] == 'SKIP')
            pnl_tot = calcular_pnl_acumulado(self.registros)
            pnl_final = pnl_tot[-1] if pnl_tot else 0

            pnl_label = "(Real)" if self.fuente_actual == 'historial' else "(Simulado)"
            self._lbl_info.config(
                text=f"✓  {len(self.registros)} rondas  |  {ops} apostadas  |  {skips} skips  |  PNL: {pnl_final:+.2f}€ {pnl_label}",
                fg=C['accent']
            )
            self._renderizar_todo()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo leer el archivo:\n{e}")

    def _limpiar_tab(self, key):
        for w in self.tabs[key].winfo_children():
            w.destroy()

    def _embed(self, fig, key):
        frame = self.tabs[key]

        # Layout: gráfica izquierda, panel explicación derecha
        container = tk.Frame(frame, bg=C['bg'])
        container.pack(fill='both', expand=True)

        left = tk.Frame(container, bg=C['bg'])
        left.pack(side='left', fill='both', expand=True)

        # Panel explicación
        right = tk.Frame(container, bg=C['panel'], width=240)
        right.pack(side='right', fill='y')
        right.pack_propagate(False)

        exp = EXPLICACIONES.get(key, {})
        tk.Frame(right, bg=C['accent'], height=2).pack(fill='x')

        # Cabecera título + botón voz
        cab = tk.Frame(right, bg=C['panel'])
        cab.pack(fill='x', padx=12, pady=(12, 4))
        tk.Label(cab, text=exp.get('titulo', ''), font=('Consolas', 9, 'bold'),
                 bg=C['panel'], fg=C['accent'], wraplength=170,
                 justify='left').pack(side='left', anchor='w')

        texto_exp = exp.get('texto', '')
        btn_voz = tk.Button(cab, text="🔊", font=('Consolas', 11),
                            bg=C['panel'], fg=C['warn'], relief='flat', bd=0,
                            cursor='hand2',
                            command=lambda t=texto_exp: self._hablar(t))
        btn_voz.pack(side='right', anchor='e')

        tk.Frame(right, bg=C['border'], height=1).pack(fill='x', padx=8)

        txt = tk.Text(right, font=('Consolas', 8), bg=C['panel'], fg=C['text'],
                      wrap='word', relief='flat', bd=0,
                      padx=10, pady=10, cursor='arrow')
        txt.insert('1.0', texto_exp)
        txt.config(state='disabled')
        txt.pack(fill='both', expand=True, padx=2, pady=6)

        # Canvas + toolbar en el lado izquierdo
        canvas = FigureCanvasTkAgg(fig, master=left)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, left)
        toolbar.config(bg=C['panel'])
        toolbar.update()
        canvas.get_tk_widget().pack(fill='both', expand=True)

    def _renderizar_todo(self):
        if not self.registros:
            return
        plt.rcParams.update(MPL_STYLE)

        stats  = stats_por_rango(self.registros)
        st_r, buckets = stats_por_racha(self.registros)

        # --- Tab PNL acumulado ---
        self._limpiar_tab('pnl')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_pnl_acumulado(ax, self.registros)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'pnl')

        # --- Tab Win Rate ---
        self._limpiar_tab('winrate')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_winrate_por_rango(ax, stats)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'winrate')

        # --- Tab PNL por rango ---
        self._limpiar_tab('pnl_rango')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_pnl_por_rango(ax, stats, self.registros)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'pnl_rango')

        # --- Tab Análisis racha ---
        self._limpiar_tab('racha')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_mayor_gana_por_racha(ax, st_r, buckets)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'racha')

        # --- Tab Heatmap ---
        self._limpiar_tab('heatmap')
        fig, ax = plt.subplots(figsize=(13, 6))
        grafica_heatmap_racha_rango(ax, self.registros)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'heatmap')

        # --- Tab Simulación ---
        self._limpiar_tab('simulacion')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_pnl_simulado_umbrales(ax, self.registros)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'simulacion')

        # --- Tab Rachas consecutivas ---
        self._limpiar_tab('rachas')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_rachas_consecutivas(ax, self.registros)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'rachas')

        # --- Tab Distribución (pie + ops) ---
        self._limpiar_tab('resumen')
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
        grafica_distribucion_modos(ax1, self.registros)
        grafica_ops_por_rango(ax2, stats)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'resumen')

        # --- Tab Confianza ---
        try:
            umbral_conf = float(self._conf_var.get().replace(',', '.'))
        except ValueError:
            umbral_conf = 60.0
        self._renderizar_confianza(umbral_conf)

        # --- Tab Estrategia Óptima ---
        self._limpiar_tab('estrategia')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_estrategia_optima(ax, self.registros)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'estrategia')

        # --- Tab PNL Real Heatmap ---
        self._limpiar_tab('pnl_real')
        fig, ax = plt.subplots(figsize=(13, 5))
        grafica_pnl_real_heatmap(ax, self.registros)
        fig.tight_layout(pad=1.5)
        self._embed(fig, 'pnl_real')

    def _recalcular_confianza(self):
        if not self.registros:
            return
        try:
            umbral = float(self._conf_var.get().replace(',', '.'))
        except ValueError:
            umbral = 60.0
        self._renderizar_confianza(umbral)

    def _renderizar_confianza(self, conf_umbral=60.0):
        self._limpiar_tab('confianza')
        plt.rcParams.update(MPL_STYLE)

        frame = self.tabs['confianza']
        fuente = detectar_fuente(self.registros)

        ctrl = tk.Frame(frame, bg=C['bg'])
        ctrl.pack(fill='x', padx=8, pady=(6, 0))

        if fuente == 'historial':
            tk.Label(ctrl,
                     text="Estrategia Confianza no disponible para historial",
                     font=('Consolas', 9, 'bold'), bg=C['bg'], fg=C['warn']).pack(side='left')
            fig, ax = plt.subplots(figsize=(13, 7))
            ax.text(0.5, 0.5, 'Estrategia Confianza no disponible\npara historial_rondas.txt\n\nEsta estrategia requiere datos de Racha_10r.',
                   ha='center', va='center', color=C['muted'], fontsize=12)
            ax.set_title('PNL SIMULADO — ESTRATEGIA CONFIANZA (No disponible)')
            fig.tight_layout(pad=1.8)
            self._embed(fig, 'confianza')
            return

        curva_act, n_ops, n_skips = simular_pnl_confianza(self.registros, conf_umbral)
        pnl_final = curva_act[-1] if curva_act else 0.0
        col_pnl = C['accent2'] if pnl_final >= 0 else C['accent3']
        tk.Label(ctrl,
                 text=f"Umbral {conf_umbral:.0f}%  →  {n_ops} apuestas  |  {n_skips} skips  |  PNL: {pnl_final:+.2f}€",
                 font=('Consolas', 9, 'bold'), bg=C['bg'], fg=col_pnl).pack(side='left')

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9),
                                        gridspec_kw={'height_ratios': [1.4, 1]})
        grafica_pnl_confianza(ax1, self.registros, conf_umbral)
        grafica_confianza_heatmap(ax2, self.registros, conf_umbral)
        fig.tight_layout(pad=1.8)
        self._embed(fig, 'confianza')

    def _exportar(self):
        if not self.registros:
            messagebox.showwarning("Sin datos", "Carga un archivo primero.")
            return
        ruta = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png")],
            initialfile="analisis_acertador.png"
        )
        if not ruta:
            return
        plt.rcParams.update(MPL_STYLE)
        stats = stats_por_rango(self.registros)
        st_r, buckets = stats_por_racha(self.registros)

        fig = plt.figure(figsize=(24, 28))
        gs  = gridspec.GridSpec(4, 2, figure=fig, hspace=0.45, wspace=0.3)

        grafica_pnl_acumulado(        fig.add_subplot(gs[0, :]),   self.registros)
        grafica_winrate_por_rango(    fig.add_subplot(gs[1, 0]),   stats)
        grafica_pnl_por_rango(        fig.add_subplot(gs[1, 1]),   stats)
        grafica_mayor_gana_por_racha( fig.add_subplot(gs[2, 0]),   st_r, buckets)
        grafica_pnl_simulado_umbrales(fig.add_subplot(gs[2, 1]),   self.registros)
        grafica_heatmap_racha_rango(  fig.add_subplot(gs[3, 0]),   self.registros)
        grafica_rachas_consecutivas(  fig.add_subplot(gs[3, 1]),   self.registros)

        fig.savefig(ruta, dpi=120, bbox_inches='tight', facecolor=C['bg'])
        plt.close(fig)
        messagebox.showinfo("Exportado", f"Guardado en:\n{ruta}")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == '__main__':
    archivo = sys.argv[1] if len(sys.argv) > 1 else None
    root = tk.Tk()
    app  = AnalizadorApp(root, archivo=archivo)
    root.mainloop()
