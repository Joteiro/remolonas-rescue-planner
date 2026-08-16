#!/usr/bin/env python3
"""
Análisis del campo `vendor`: concentración, calidad y perfil por proveedor.

CORRECCIÓN (15-ago-2026). Al principio di por bueno que `vendor` era "REMOLONAS"
en todo el catálogo — lo leí de un resumen de la primera página y no lo comprobé
contra la base. Es falso: hay 94 proveedores distintos y sólo 12 referencias
llevan la marca propia. `vendor` es, de hecho, el campo más informativo que
expone el catálogo, y el único que da la dimensión de proveedor.

Qué hace este módulo:
  1. Concentración: cuánto depende el surtido de sus mayores proveedores.
  2. Calidad del campo: duplicados por mayúsculas y erratas que romperían
     cualquier informe agregado por proveedor.
  3. Perfil: descuento medio y motivo de excedente dominante por proveedor.

    python proveedores.py
"""

from __future__ import annotations

import argparse
import difflib
import json
import sqlite3
import statistics
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "catalog.sqlite"

UMBRAL_ERRATA = 0.85   # similitud a partir de la cual dos nombres son sospechosos


def _clave(nombre: str) -> str:
    """Normaliza para detectar el mismo proveedor escrito de formas distintas."""
    s = unicodedata.normalize("NFD", (nombre or "").upper().strip())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def cargar(conn, fecha: str | None = None):
    if fecha is None:
        fecha = conn.execute("SELECT MAX(snapshot_date) FROM observations").fetchone()[0]
    rows = conn.execute("""
        SELECT vendor, title, tags, price, compare_at_price
        FROM observations
        WHERE snapshot_date = ? AND IFNULL(tags,'') NOT LIKE '%rm_caja%'""",
        (fecha,)).fetchall()
    items = []
    for vendor, title, tags, price, cap in rows:
        items.append({
            "vendor": vendor, "title": title,
            "tag": (tags or "").split("|")[0] or None,
            "price": price, "compare_at_price": cap,
            "descuento": 100 * (1 - price / cap) if (cap and price and cap > price) else None,
        })
    return fecha, items


def concentracion(items) -> dict:
    n = len(items)
    cnt = Counter(it["vendor"] for it in items)
    ordenados = cnt.most_common()

    def acumulado(k: int) -> float:
        return round(100 * sum(v for _, v in ordenados[:k]) / n, 1)

    # Herfindahl: 0 = surtido atomizado, 10.000 = un solo proveedor.
    hhi = sum((100 * v / n) ** 2 for _, v in ordenados)
    return {
        "n_proveedores": len(cnt),
        "n_referencias": n,
        "top1_pct": acumulado(1),
        "top5_pct": acumulado(5),
        "top10_pct": acumulado(10),
        "top20_pct": acumulado(20),
        "hhi": round(hhi),
        "proveedores_con_una_referencia": sum(1 for v in cnt.values() if v == 1),
        "mediana_referencias_por_proveedor": statistics.median(cnt.values()),
        "ranking": [{"vendor": k, "referencias": v, "pct": round(100 * v / n, 1)}
                    for k, v in ordenados[:20]],
    }


def calidad_campo(items) -> dict:
    """Duplicados por mayúsculas/tildes y erratas probables."""
    cnt = Counter(it["vendor"] for it in items)
    por_clave: dict[str, list[str]] = defaultdict(list)
    for nombre in cnt:
        por_clave[_clave(nombre)].append(nombre)

    variantes = [{"canonico": k, "variantes": sorted(v),
                  "referencias_afectadas": sum(cnt[x] for x in v)}
                 for k, v in por_clave.items() if len(v) > 1]

    claves = sorted(por_clave)
    erratas = []
    for i, a in enumerate(claves):
        for b in claves[i + 1:]:
            ratio = difflib.SequenceMatcher(None, a, b).ratio()
            if UMBRAL_ERRATA < ratio < 1.0:
                erratas.append({
                    "a": a, "b": b, "similitud": round(ratio, 3),
                    "referencias_a": sum(cnt[x] for x in por_clave[a]),
                    "referencias_b": sum(cnt[x] for x in por_clave[b]),
                })

    afectadas = (sum(v["referencias_afectadas"] for v in variantes)
                 + sum(e["referencias_a"] + e["referencias_b"] for e in erratas))
    return {
        "proveedores_crudos": len(cnt),
        "proveedores_tras_normalizar": len(por_clave),
        "variantes_de_mayusculas": variantes,
        "erratas_probables": sorted(erratas, key=lambda x: -x["similitud"]),
        "referencias_afectadas": afectadas,
        "pct_referencias_afectadas": round(100 * afectadas / len(items), 1),
    }


def perfil(items, min_refs: int = 6) -> list[dict]:
    """Descuento y motivo dominante por proveedor."""
    grupos: dict[str, list] = defaultdict(list)
    for it in items:
        grupos[_clave(it["vendor"])].append(it)

    filas = []
    for vendor, xs in grupos.items():
        if len(xs) < min_refs:
            continue
        ds = [x["descuento"] for x in xs if x["descuento"] is not None]
        tags = Counter(x["tag"] for x in xs if x["tag"])
        dominante, n_dom = tags.most_common(1)[0] if tags else (None, 0)
        filas.append({
            "vendor": vendor,
            "referencias": len(xs),
            "descuento_medio_pct": round(statistics.mean(ds), 1) if ds else None,
            "descuento_mediana_pct": round(statistics.median(ds), 1) if ds else None,
            "precio_medio_eur": round(statistics.mean(
                [x["price"] for x in xs if x["price"]]), 2),
            "motivo_dominante": dominante,
            "pct_motivo_dominante": round(100 * n_dom / len(xs), 1) if tags else None,
        })
    return sorted(filas, key=lambda x: -(x["descuento_medio_pct"] or 0))


def construir(conn) -> dict:
    fecha, items = cargar(conn)
    return {
        "fecha": fecha,
        "concentracion": concentracion(items),
        "calidad_campo": calidad_campo(items),
        "perfil_por_proveedor": perfil(items),
    }


def imprimir(r: dict) -> None:
    c = r["concentracion"]
    print(f"\n=== PROVEEDORES — {r['fecha']} ===\n")
    print(f"{c['n_referencias']} referencias de {c['n_proveedores']} proveedores")
    print(f"  top 1  : {c['top1_pct']}%      top 5  : {c['top5_pct']}%")
    print(f"  top 10 : {c['top10_pct']}%     top 20 : {c['top20_pct']}%")
    print(f"  HHI    : {c['hhi']}  (0 = atomizado, 10.000 = monopolio)")
    print(f"  mediana de referencias por proveedor: {c['mediana_referencias_por_proveedor']}")
    print(f"  proveedores con una sola referencia : {c['proveedores_con_una_referencia']}")

    print(f"\nTop 12:")
    for i, f in enumerate(c["ranking"][:12], 1):
        print(f"  {i:>2}. {f['vendor'][:30]:<30} {f['referencias']:>3}  ({f['pct']}%)")

    q = r["calidad_campo"]
    print(f"\nCalidad del campo: {q['proveedores_crudos']} valores crudos -> "
          f"{q['proveedores_tras_normalizar']} tras normalizar")
    if q["variantes_de_mayusculas"]:
        print("  Mismo proveedor escrito de varias formas:")
        for v in q["variantes_de_mayusculas"]:
            print(f"    {v['variantes']}  ({v['referencias_afectadas']} ref)")
    if q["erratas_probables"]:
        print("  Erratas probables (revisar a mano, no fusionar a ciegas):")
        for e in q["erratas_probables"]:
            print(f"    '{e['a']}' ({e['referencias_a']}) ~ '{e['b']}' "
                  f"({e['referencias_b']})   similitud {e['similitud']}")
    print(f"  Referencias afectadas: {q['referencias_afectadas']} "
          f"({q['pct_referencias_afectadas']}%)")

    print(f"\nProveedores con mayor descuento medio (≥6 referencias):")
    print(f"  {'proveedor':<28}{'ref':>5}{'desc':>7}{'medi':>7}{'precio':>8}  motivo dominante")
    for f in r["perfil_por_proveedor"][:14]:
        print(f"  {f['vendor'][:27]:<28}{f['referencias']:>5}"
              f"{f['descuento_medio_pct']!s:>7}{f['descuento_mediana_pct']!s:>7}"
              f"{f['precio_medio_eur']!s:>8}  {f['motivo_dominante']} "
              f"({f['pct_motivo_dominante']}%)")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path)
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
