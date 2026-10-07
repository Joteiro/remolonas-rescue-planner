#!/usr/bin/env python3
"""
dashboard.py — genera los gráficos de los hallazgos a partir de catalog.sqlite.

Reproducible: `python dashboard.py` reconstruye todas las imágenes en docs/ desde
la base. Cada gráfico corresponde a un hallazgo de HALLAZGOS.md. Única dependencia
fuera de la stdlib: matplotlib.

Diseño: paleta Okabe-Ito (daltónico-segura, validada), marcas finas, etiquetas
directas, un solo eje por gráfico.
"""

from __future__ import annotations

import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "catalog.sqlite"
OUT = ROOT / "docs"
OUT.mkdir(exist_ok=True)

INK, MUTED, GRID, SURFACE = "#1a1a1a", "#6b6b6b", "#e6e6e3", "#fcfcfb"
AZUL, BERMELLON, VERDE, MORADO, NEUTRO = "#0072B2", "#D55E00", "#009E73", "#8456B0", "#9a9a97"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "font.size": 12, "axes.edgecolor": GRID, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.8, "axes.spines.top": False, "axes.spines.right": False,
    "font.family": "DejaVu Sans",
})

TAGS_ORIGEN = {"Excedente", "Fecha corta", "Rescate", "Encargo cancelado",
               "Exceso", "Cambio de canal", "Innovación"}
# Innovación no es excedente por urgencia: son lanzamientos de producto nuevo
# (incluso no alimentario) con descuento de introducción. Palanca distinta.
PROMO_NOVEDAD = {"Innovación"}
DIAS_ES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def _conn():
    return sqlite3.connect(DB)


def _pids(c, d):
    return {r[0] for r in c.execute(
        "SELECT DISTINCT product_id FROM observations "
        "WHERE snapshot_date=? AND tags NOT LIKE '%rm_caja%'", (d,))}


def _finish(ax, titulo, subt=None):
    ax.text(0, 1.16, titulo, transform=ax.transAxes, fontsize=14.5,
            fontweight="bold", color=INK, va="bottom")
    if subt:
        ax.text(0, 1.05, subt, transform=ax.transAxes, fontsize=10,
                color=MUTED, va="bottom")
    ax.figure.subplots_adjust(top=0.80)
    ax.tick_params(length=0)


def _fecha_eje(ax):
    """Eje x de fechas legible: una marca por semana, sin apilar."""
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(0)
        lbl.set_horizontalalignment("center")


def guardar(fig, nombre):
    fig.tight_layout(rect=[0, 0, 1, 0.80])
    path = OUT / nombre
    fig.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path.relative_to(ROOT)}")


# =========================================================================== #

def g1_catalogo(c, dates):
    xs = [date.fromisoformat(d) for d in dates]
    ys = [len(_pids(c, d)) for d in dates]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.plot(xs, ys, color=AZUL, lw=2)
    ax.fill_between(xs, ys, min(ys) - 8, color=AZUL, alpha=0.06)
    ax.set_ylim(min(ys) - 8, max(ys) + 10)
    for x, y in [(xs[0], ys[0]), (xs[-1], ys[-1])]:
        ax.scatter([x], [y], color=AZUL, s=28, zorder=5)
        ax.annotate(f"{y}", (x, y), textcoords="offset points", xytext=(0, 9),
                    fontsize=11, fontweight="bold", color=AZUL, ha="center")
    ax.set_ylabel("Referencias en catálogo")
    _fecha_eje(ax)
    _finish(ax, "El catálogo es inventario vivo, no un surtido estable",
            f"Referencias disponibles por día · {dates[0]} a {dates[-1]} · despensa, sin la caja")
    guardar(fig, "01_catalogo_vivo.png")


def g2_altas_bajas(c, dates):
    sem = defaultdict(lambda: [0, 0])
    prev = _pids(c, dates[0])
    for d in dates[1:]:
        cur = _pids(c, d)
        wk = date.fromisoformat(d).isocalendar()
        sem[f"{wk[0]}-W{wk[1]:02d}"][0] += len(cur - prev)
        sem[f"{wk[0]}-W{wk[1]:02d}"][1] += len(prev - cur)
        prev = cur
    keys = sorted(sem)[1:-1]            # descartar semanas parciales
    altas = [sem[k][0] for k in keys]
    bajas = [sem[k][1] for k in keys]
    x = range(len(keys))
    w = 0.4
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.bar([i - w/2 for i in x], altas, w, color=AZUL, label="Altas")
    ax.bar([i + w/2 for i in x], bajas, w, color=BERMELLON, label="Bajas")
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"sem {k.split('-W')[1]}" for k in keys], fontsize=9)
    ax.set_ylabel("Referencias")
    ax.set_ylim(0, max(altas + bajas) * 1.12)
    # leyenda FUERA del área de trazado, debajo
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=2, fontsize=11)
    ma, mb = statistics.mean(altas), statistics.mean(bajas)
    _finish(ax, "Entra y sale producto todas las semanas",
            f"Media semanal: {ma:.0f} altas · {mb:.0f} bajas · semanas ISO completas")
    guardar(fig, "02_altas_bajas.png")


def g3_supervivencia(c, dates):
    d0 = _pids(c, dates[0])
    xs = [date.fromisoformat(d) for d in dates]
    ys = [100 * len(d0 & _pids(c, d)) / len(d0) for d in dates]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.plot(xs, ys, color=VERDE, lw=2.2)
    ax.fill_between(xs, ys, 60, color=VERDE, alpha=0.06)
    ax.set_ylim(60, 101)
    ax.scatter([xs[-1]], [ys[-1]], color=VERDE, s=30, zorder=5)
    ax.annotate(f"{ys[-1]:.0f}%", (xs[-1], ys[-1]), textcoords="offset points",
                xytext=(-6, 9), fontsize=12, fontweight="bold", color=VERDE, ha="right")
    ax.set_ylabel("% de la cohorte inicial aún en catálogo")
    _fecha_eje(ax)
    dias = (xs[-1] - xs[0]).days
    _finish(ax, f"En {dias} días desapareció el {100-ys[-1]:.0f}% del surtido inicial",
            f"Supervivencia de las {len(d0)} referencias presentes el {dates[0]}")
    guardar(fig, "03_supervivencia.png")


def g4_descuento_motivo(c):
    """Descuento por motivo, un valor por producto distinto sobre toda la serie.
    Separa dos palancas: excedente real (gradiente de urgencia) vs promo de novedad."""
    rows = c.execute("""SELECT product_id, tags, price, compare_at_price FROM observations
                        WHERE tags NOT LIKE '%rm_caja%' AND compare_at_price>price AND price>0""").fetchall()
    porprod = defaultdict(lambda: [None, []])
    for pid, tags, p, cp in rows:
        tag = (tags or "").split("|")[0]
        if tag in TAGS_ORIGEN:
            porprod[pid][0] = tag
            porprod[pid][1].append(100 * (1 - p / cp))
    mot = defaultdict(list)
    for _, (tag, ds) in porprod.items():
        if tag:
            mot[tag].append(statistics.median(ds))
    datos = [(t, statistics.mean(v), len(v)) for t, v in mot.items() if len(v) >= 5]
    datos.sort(key=lambda x: x[1])
    labels = [f"{t}  (n={n})" for t, _, n in datos]
    vals = [v for _, v, _ in datos]
    cols = [MORADO if t in PROMO_NOVEDAD else AZUL for t, _, _ in datos]
    fig, ax = plt.subplots(figsize=(9, 4.6))
    bars = ax.barh(labels, vals, color=cols, height=0.62)
    for b, v in zip(bars, vals):
        ax.text(v + 0.5, b.get_y() + b.get_height()/2, f"{v:.0f}%",
                va="center", fontsize=11, fontweight="bold", color=INK)
    ax.set_xlim(0, max(vals) * 1.15)
    ax.set_xlabel("Descuento medio sobre PVP")
    ax.grid(axis="y", visible=False)
    # leyenda de las dos palancas, debajo
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=AZUL, label="Excedente real (gradiente de urgencia)"),
                       Patch(color=MORADO, label="Promo de novedad (lanzamientos)")],
              frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.22),
              ncol=2, fontsize=9.5)
    _finish(ax, "El motivo del excedente marca el precio",
            "Entre los motivos de excedente real, más urgencia = más descuento")
    guardar(fig, "04_descuento_motivo.png")


def g5_proveedores(c, dates):
    d = dates[-1]
    cnt = Counter(r[0] for r in c.execute(
        "SELECT vendor FROM observations WHERE snapshot_date=? "
        "AND tags NOT LIKE '%rm_caja%'", (d,)))
    n = sum(cnt.values())
    top = cnt.most_common(12)
    labels = [t[0][:22] for t in top][::-1]
    vals = [100 * t[1] / n for t in top][::-1]
    grandes = {"HELIOS", "PEPSICO", "GULLÓN", "PASTAS GALLO", "BORGES", "CARMENCITA"}
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.barh(labels, vals, color=NEUTRO, height=0.66)
    for b, (name, _) in zip(bars, top[::-1]):
        if name in grandes:
            b.set_color(AZUL)
    for b, v in zip(bars, vals):
        ax.text(v + 0.1, b.get_y() + b.get_height()/2, f"{v:.1f}%",
                va="center", fontsize=10, color=MUTED)
    ax.set_xlabel("% del catálogo")
    ax.grid(axis="y", visible=False)
    _finish(ax, "Surtido atomizado, pero inclinado a marcas grandes",
            f"Top 12 de {len(cnt)} proveedores · en azul, marcas conocidas · top 20 = 54% del catálogo")
    guardar(fig, "05_proveedores.png")


def g6_novedad(c, dates):
    D = {d: _pids(c, d) for d in dates}
    def frac(gap):
        fr = []
        for i, di in enumerate(dates):
            d_i = date.fromisoformat(di)
            for dj in dates[i+1:]:
                if (date.fromisoformat(dj) - d_i).days == gap:
                    fr.append(100 * len(D[dj] - D[di]) / len(D[dj]))
                    break
        return statistics.mean(fr) if fr else 0
    vals = [frac(g) for g in (7, 14, 28)]
    labels = ["Vuelve\ncada semana", "Vuelve\ncada 2 semanas", "Vuelve\ncada mes"]
    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    bars = ax.bar(labels, vals, color=VERDE, width=0.58)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width()/2, v + 0.4, f"{v:.0f}%",
                ha="center", fontsize=13, fontweight="bold", color=INK)
    ax.set_ylim(0, max(vals) * 1.25)
    ax.set_ylabel("% del catálogo nuevo para el cliente")
    ax.grid(axis="x", visible=False)
    _finish(ax, "La rotación es un motor de novedad constante",
            "Cuánto del catálogo no estaba la última vez que el cliente entró")
    guardar(fig, "06_novedad.png")


def g7_pulso_semanal(c, dates):
    """Altas y bajas medias por día de la semana — el ritmo operativo."""
    D = {d: _pids(c, d) for d in dates}
    alta = defaultdict(list)
    baja = defaultdict(list)
    for a, b in zip(dates, dates[1:]):
        w = date.fromisoformat(b).weekday()
        alta[w].append(len(D[b] - D[a]))
        baja[w].append(len(D[a] - D[b]))
    ma = [statistics.mean(alta[w]) if alta[w] else 0 for w in range(7)]
    mb = [statistics.mean(baja[w]) if baja[w] else 0 for w in range(7)]
    x = range(7)
    w = 0.4
    fig, ax = plt.subplots(figsize=(9, 4.8))
    ax.bar([i - w/2 for i in x], ma, w, color=AZUL, label="Altas")
    ax.bar([i + w/2 for i in x], mb, w, color=BERMELLON, label="Bajas")
    ax.set_xticks(list(x))
    ax.set_xticklabels(DIAS_ES)
    ax.set_ylabel("Referencias (media por día)")
    # anotar la purga del lunes
    imax = mb.index(max(mb))
    ax.annotate(f"purga del lunes\n−{mb[imax]:.0f}", (imax + w/2, mb[imax]),
                textcoords="offset points", xytext=(28, -6), fontsize=10,
                color=BERMELLON, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=BERMELLON, lw=1.2))
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=2, fontsize=11)
    _finish(ax, "El catálogo tiene un pulso semanal",
            "Se repone entre semana y se purga el lunes · media por día de la semana")
    guardar(fig, "07_pulso_semanal.png")


def main():
    c = _conn()
    dates = [r[0] for r in c.execute(
        "SELECT DISTINCT snapshot_date FROM observations ORDER BY 1")]
    print(f"Generando gráficos ({dates[0]} → {dates[-1]}, {len(dates)} días):")
    g1_catalogo(c, dates)
    g2_altas_bajas(c, dates)
    g3_supervivencia(c, dates)
    g4_descuento_motivo(c)
    g5_proveedores(c, dates)
    g6_novedad(c, dates)
    g7_pulso_semanal(c, dates)
    print("Listo.")


if __name__ == "__main__":
    raise SystemExit(main())
