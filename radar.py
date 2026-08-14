#!/usr/bin/env python3
"""
Radar de catálogo: métricas de rotación a partir de los snapshots acumulados.

    python radar.py            # informe en consola
    python radar.py --json     # salida JSON, para alimentar el dashboard

Advertencia estadística importante (léela antes de citar cualquier número):
    La "vida media de una referencia en catálogo" está SESGADA A LA BAJA mientras
    la serie sea corta. Una referencia que entró el día 18 de una serie de 21 días
    y sigue viva se observa con 3 días, no con su vida real. Es censura por la
    derecha. Los números marcados [CENSURADO] son cotas inferiores, no estimaciones.
    A partir de ~3 semanas conviene pasar a Kaplan-Meier (ver km_supervivencia()).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "catalog.sqlite"


def load(conn: sqlite3.Connection) -> dict:
    dates = [r[0] for r in conn.execute(
        "SELECT snapshot_date FROM snapshots ORDER BY snapshot_date").fetchall()]
    if not dates:
        raise SystemExit("No hay snapshots todavía. Corre snapshot.py primero.")
    return {"dates": dates}


# --------------------------------------------------------------------------- #

def serie_diaria(conn) -> list[dict]:
    """Tamaño del catálogo y descuento efectivo, día a día."""
    rows = conn.execute("""
        SELECT snapshot_date,
               COUNT(DISTINCT product_id)                            AS n_prod,
               COUNT(*)                                              AS n_var,
               SUM(available)                                        AS n_disp,
               AVG(CASE WHEN compare_at_price > price AND price > 0
                        THEN 1.0 - price / compare_at_price END)     AS desc_medio,
               SUM(CASE WHEN compare_at_price > price THEN 1 ELSE 0 END) AS n_con_desc
        FROM observations
        GROUP BY snapshot_date
        ORDER BY snapshot_date
    """).fetchall()

    # La media del descuento resultó engañosa con datos reales: la distribución
    # está sesgada a la derecha (mediana 12,2 % frente a media 16,5 %). Añadimos
    # la mediana para no citar sólo el número que más favorece.
    medianas = {}
    for (fecha,) in conn.execute("SELECT DISTINCT snapshot_date FROM observations"):
        ds = [r[0] for r in conn.execute("""
            SELECT 100.0 * (1 - price / compare_at_price) FROM observations
            WHERE snapshot_date = ? AND compare_at_price > price AND price > 0""", (fecha,))]
        medianas[fecha] = round(statistics.median(ds), 1) if ds else None

    return [{
        "fecha": r[0], "productos": r[1], "variantes": r[2],
        "disponibles": r[3],
        "pct_disponible": round(100 * r[3] / r[2], 1) if r[2] else None,
        "descuento_medio_pct": round(100 * r[4], 1) if r[4] else None,
        "descuento_mediana_pct": medianas.get(r[0]),
        "variantes_con_descuento": r[5],
    } for r in rows]


def altas_y_bajas(conn, dates: list[str]) -> list[dict]:
    """Entradas y salidas de referencias entre snapshots consecutivos."""
    por_dia: dict[str, set] = {}
    for d in dates:
        por_dia[d] = {r[0] for r in conn.execute(
            "SELECT DISTINCT product_id FROM observations WHERE snapshot_date = ?", (d,))}

    out = []
    for prev, cur in zip(dates, dates[1:]):
        a, b = por_dia[prev], por_dia[cur]
        altas, bajas = b - a, a - b
        out.append({
            "fecha": cur,
            "altas": len(altas),
            "bajas": len(bajas),
            "estables": len(a & b),
            "churn_catalogo_pct": round(100 * len(altas | bajas) / len(a | b), 1) if (a | b) else None,
        })
    return out


def vida_referencias(conn, dates: list[str]) -> dict:
    """Permanencia observada por referencia, distinguiendo censura."""
    primera, ultima, dias_vistos = {}, {}, defaultdict(int)
    for d in dates:
        for (pid,) in conn.execute(
                "SELECT DISTINCT product_id FROM observations WHERE snapshot_date = ?", (d,)):
            primera.setdefault(pid, d)
            ultima[pid] = d
            dias_vistos[pid] += 1

    primer_dia, ultimo_dia = dates[0], dates[-1]
    completas, censuradas = [], []
    for pid, n in dias_vistos.items():
        # Censurada si ya estaba el primer día (no vimos su alta) o sigue viva hoy.
        if primera[pid] == primer_dia or ultima[pid] == ultimo_dia:
            censuradas.append(n)
        else:
            completas.append(n)

    def resumen(xs):
        if not xs:
            return None
        return {
            "n": len(xs),
            "media_dias": round(statistics.mean(xs), 1),
            "mediana_dias": statistics.median(xs),
        }

    return {
        "vidas_completas": resumen(completas),
        "vidas_censuradas": resumen(censuradas),
        "nota": ("Sólo 'vidas_completas' es interpretable como duración. "
                 "'vidas_censuradas' son cotas inferiores. Con series cortas "
                 "la mayoría estará censurada — es lo esperado, no un error."),
    }


def km_supervivencia(conn, dates: list[str]) -> dict:
    """Kaplan-Meier sobre la permanencia de una referencia en catálogo.

    Requiere `lifelines`. Es el estimador correcto en cuanto la serie pasa de
    ~3 semanas, porque usa la información de las referencias censuradas en vez
    de tirarlas o contarlas mal.
    """
    try:
        from lifelines import KaplanMeierFitter  # noqa: PLC0415
    except ImportError:
        return {"disponible": False, "motivo": "pip install lifelines"}

    primera, ultima = {}, {}
    for d in dates:
        for (pid,) in conn.execute(
                "SELECT DISTINCT product_id FROM observations WHERE snapshot_date = ?", (d,)):
            primera.setdefault(pid, d)
            ultima[pid] = d

    d0 = date.fromisoformat(dates[0])
    dN = date.fromisoformat(dates[-1])
    duraciones, eventos = [], []
    for pid in primera:
        ini = date.fromisoformat(primera[pid])
        fin = date.fromisoformat(ultima[pid])
        if ini == d0:      # entrada no observada -> descartamos (censura por la izquierda)
            continue
        duraciones.append((fin - ini).days + 1)
        eventos.append(0 if fin == dN else 1)   # 1 = salió del catálogo (evento observado)

    if len(duraciones) < 20:
        return {"disponible": False, "motivo": f"sólo {len(duraciones)} referencias con alta observada; espera más días"}

    kmf = KaplanMeierFitter()
    kmf.fit(duraciones, event_observed=eventos)
    return {
        "disponible": True,
        "n_referencias": len(duraciones),
        "n_eventos": int(sum(eventos)),
        "mediana_supervivencia_dias": float(kmf.median_survival_time_),
        "curva": {str(k): round(float(v), 4)
                  for k, v in kmf.survival_function_["KM_estimate"].items()},
    }


def por_categoria(conn) -> list[dict]:
    """Mix y descuento por etiqueta en el último snapshot.

    Nota (14-ago-2026): originalmente esto agrupaba por `product_type`. Los datos
    reales mostraron que el 99,6 % del catálogo lleva el mismo valor ('UPSELLING'),
    así que ese campo no separa nada. La taxonomía real vive en `tags`, y por ahí
    va ahora la agrupación. Ver taxonomia.py para el análisis completo.
    """
    cuenta: dict[str, list] = defaultdict(list)
    for tags, price, cap in conn.execute("""
            SELECT tags, price, compare_at_price FROM observations
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM observations)"""):
        desc = (1 - price / cap) if (cap and price and cap > price) else None
        for t in (tags or "").split("|") or ["(sin etiqueta)"]:
            cuenta[t or "(sin etiqueta)"].append((price, desc))

    out = []
    for tag, vals in cuenta.items():
        descs = [d for _, d in vals if d is not None]
        precios = [p for p, _ in vals if p]
        out.append({
            "categoria": tag,
            "referencias": len(vals),
            "descuento_medio_pct": round(100 * statistics.mean(descs), 1) if descs else None,
            "descuento_mediana_pct": round(100 * statistics.median(descs), 1) if descs else None,
            "precio_medio_eur": round(statistics.mean(precios), 2) if precios else None,
        })
    return sorted(out, key=lambda x: -x["referencias"])


def por_tag(conn) -> list[dict]:
    """Reparto por tag en el último snapshot (Excedente, Rescate, ...)."""
    cuenta: dict[str, set] = defaultdict(set)
    for pid, tags in conn.execute("""
            SELECT DISTINCT product_id, tags FROM observations
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM observations)"""):
        for t in (tags or "").split("|"):
            if t:
                cuenta[t].add(pid)
    return sorted(
        ({"tag": t, "referencias": len(s)} for t, s in cuenta.items()),
        key=lambda x: -x["referencias"],
    )[:25]


# --------------------------------------------------------------------------- #

def construir(conn) -> dict:
    dates = load(conn)["dates"]
    return {
        "dias_de_serie": len(dates),
        "desde": dates[0],
        "hasta": dates[-1],
        "serie_diaria": serie_diaria(conn),
        "altas_y_bajas": altas_y_bajas(conn, dates) if len(dates) > 1 else [],
        "vida_referencias": vida_referencias(conn, dates),
        "kaplan_meier": km_supervivencia(conn, dates) if len(dates) >= 14 else
                        {"disponible": False, "motivo": "menos de 14 días de serie"},
        "por_categoria": por_categoria(conn),
        "por_tag": por_tag(conn),
    }


def imprimir(r: dict) -> None:
    print(f"\n=== RADAR DE CATÁLOGO — {r['desde']} → {r['hasta']} ({r['dias_de_serie']} días) ===\n")

    print("Serie diaria (últimos 10 días):")
    for d in r["serie_diaria"][-10:]:
        print(f"  {d['fecha']}  {d['productos']:>4} prod  {d['variantes']:>4} var  "
              f"{d['pct_disponible']!s:>5}% disp  desc. media {d['descuento_medio_pct']!s:>5}% "
              f"/ mediana {d['descuento_mediana_pct']!s:>5}%")

    if r["altas_y_bajas"]:
        print("\nAltas y bajas:")
        for d in r["altas_y_bajas"][-10:]:
            print(f"  {d['fecha']}  +{d['altas']:<4} -{d['bajas']:<4} "
                  f"estables {d['estables']:<5} rotación {d['churn_catalogo_pct']}%")
        tot_a = sum(d["altas"] for d in r["altas_y_bajas"])
        tot_b = sum(d["bajas"] for d in r["altas_y_bajas"])
        n_dias = len(r["altas_y_bajas"])
        print(f"\n  Media diaria: +{tot_a/n_dias:.1f} altas / -{tot_b/n_dias:.1f} bajas")
        print(f"  Extrapolado a semana: ~{7*tot_a/n_dias:.0f} altas / ~{7*tot_b/n_dias:.0f} bajas")

    v = r["vida_referencias"]
    print("\nPermanencia de referencias:")
    for k in ("vidas_completas", "vidas_censuradas"):
        if v[k]:
            marca = "" if k == "vidas_completas" else "  [CENSURADO — cota inferior]"
            print(f"  {k}: n={v[k]['n']}  media {v[k]['media_dias']}d  "
                  f"mediana {v[k]['mediana_dias']}d{marca}")

    km = r["kaplan_meier"]
    if km.get("disponible"):
        print(f"\n  Kaplan-Meier: mediana de supervivencia "
              f"{km['mediana_supervivencia_dias']} días "
              f"(n={km['n_referencias']}, eventos={km['n_eventos']})")
    else:
        print(f"\n  Kaplan-Meier no disponible: {km.get('motivo')}")

    print("\nPor etiqueta (último snapshot):")
    for c in r["por_categoria"][:14]:
        print(f"  {c['categoria'][:30]:<30} {c['referencias']:>4} ref  "
              f"desc medio {c['descuento_medio_pct']!s:>5}% / mediana {c['descuento_mediana_pct']!s:>5}%  "
              f"precio {c['precio_medio_eur']!s:>6} €")

    print("\nTop tags:")
    for t in r["por_tag"][:12]:
        print(f"  {t['tag'][:34]:<34} {t['referencias']:>4} ref")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="salida JSON")
    ap.add_argument("--out", type=Path, help="guardar el JSON en un fichero")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    r = construir(conn)

    if args.out:
        args.out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"escrito {args.out}")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        imprimir(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
