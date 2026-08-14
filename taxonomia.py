#!/usr/bin/env python3
"""
Análisis transversal de la taxonomía del catálogo (un solo snapshot).

A diferencia de radar.py, que necesita semanas de serie, esto funciona con el
primer día de datos. Responde a una pregunta concreta:

    ¿La información que acompaña a cada referencia permite decidir su precio
    y su prioridad de colocación?

Motivación. En un supermercado de excedentes, el *motivo* por el que un producto
está disponible (fecha corta, encargo cancelado, cambio de canal, sobreproducción)
determina dos cosas: cuánta urgencia hay por colocarlo y cuánto descuento admite.
Si ese motivo no está registrado de forma explotable, ninguna de las dos
decisiones se puede automatizar — hay que tomarlas a mano, una por una.

    python taxonomia.py
    python taxonomia.py --json --out data/taxonomia.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "catalog.sqlite"

# Tags que explican POR QUÉ el producto está disponible como excedente.
# Son los que deberían gobernar precio y urgencia.
TAGS_ORIGEN = {
    "Excedente", "Fecha corta", "Rescate", "Encargo cancelado",
    "Exceso", "Cambio de canal", "Innovación",
}

# Tags que describen la procedencia o el posicionamiento comercial.
# Útiles para merchandising, mudos para la decisión operativa.
TAGS_COMERCIAL = {
    "Promoción producto", "Productor local", "Pequeños productores",
    "Apoyo productor local",
}


def _split(tags: str | None) -> list[str]:
    return [t for t in (tags or "").split("|") if t]


def cargar(conn: sqlite3.Connection, fecha: str | None = None) -> tuple[str, list[dict]]:
    if fecha is None:
        fecha = conn.execute("SELECT MAX(snapshot_date) FROM observations").fetchone()[0]
    if fecha is None:
        raise SystemExit("No hay datos. Corre snapshot.py primero.")
    rows = conn.execute("""
        SELECT product_id, title, product_type, tags, price, compare_at_price,
               available, grams
        FROM observations WHERE snapshot_date = ?""", (fecha,)).fetchall()
    items = [{
        "product_id": r[0], "title": r[1], "product_type": r[2],
        "tags": _split(r[3]), "price": r[4], "compare_at_price": r[5],
        "available": r[6], "grams": r[7],
    } for r in rows]
    for it in items:
        cp, p = it["compare_at_price"], it["price"]
        it["descuento"] = 100 * (1 - p / cp) if (cp and p and cp > p) else None
    return fecha, items


# --------------------------------------------------------------------------- #

def cardinalidad_tags(items: list[dict]) -> dict:
    """¿Cuántos tags lleva cada producto?

    Si la respuesta es 'siempre exactamente uno', el campo tags no se está
    usando como conjunto de etiquetas sino como una categoría única excluyente.
    Eso fuerza a elegir entre marcar el origen del excedente o marcar la
    procedencia comercial — y se pierde una de las dos.
    """
    dist = Counter(len(it["tags"]) for it in items)
    n = len(items)
    max_tags = max(dist) if dist else 0
    return {
        "distribucion": {str(k): v for k, v in sorted(dist.items())},
        "max_tags_por_producto": max_tags,
        "pct_con_un_solo_tag": round(100 * dist.get(1, 0) / n, 1) if n else None,
        "usado_como_categoria_unica": max_tags <= 1,
    }


def cobertura_origen(items: list[dict]) -> dict:
    """¿Qué fracción del catálogo declara por qué es excedente?"""
    n = len(items)
    con = [it for it in items if set(it["tags"]) & TAGS_ORIGEN]
    com = [it for it in items if set(it["tags"]) & TAGS_COMERCIAL]
    sin = [it for it in items if not (set(it["tags"]) & TAGS_ORIGEN)]
    return {
        "total": n,
        "con_tag_origen": len(con),
        "pct_con_tag_origen": round(100 * len(con) / n, 1) if n else None,
        "sin_tag_origen": len(sin),
        "pct_sin_tag_origen": round(100 * len(sin) / n, 1) if n else None,
        "solo_tag_comercial": len([it for it in sin if set(it["tags"]) & TAGS_COMERCIAL]),
        "sin_ningun_tag": len([it for it in items if not it["tags"]]),
        "nota_tags_comerciales": len(com),
    }


def precio_por_motivo(items: list[dict]) -> list[dict]:
    """Descuento aplicado según el tag de origen.

    Si el descuento varía sistemáticamente con el motivo, existe una política
    de precios implícita. Merece la pena hacerla explícita y comprobar si se
    aplica de forma consistente.
    """
    por_tag: dict[str, list[float]] = defaultdict(list)
    conteo: Counter = Counter()
    for it in items:
        for t in it["tags"]:
            conteo[t] += 1
            if it["descuento"] is not None:
                por_tag[t].append(it["descuento"])

    out = []
    for tag, n in conteo.most_common():
        ds = por_tag.get(tag, [])
        out.append({
            "tag": tag,
            "familia": ("origen" if tag in TAGS_ORIGEN
                        else "comercial" if tag in TAGS_COMERCIAL else "otro"),
            "referencias": n,
            "con_descuento": len(ds),
            "descuento_medio_pct": round(statistics.mean(ds), 1) if ds else None,
            "descuento_mediana_pct": round(statistics.median(ds), 1) if ds else None,
            "descuento_p90_pct": round(sorted(ds)[int(0.9 * (len(ds) - 1))], 1) if ds else None,
            "muestra_pequena": n < 10,
        })
    return out


def distribucion_descuento(items: list[dict]) -> dict:
    """La media de un descuento esconde casi todo. Aquí va la forma completa."""
    ds = sorted(it["descuento"] for it in items if it["descuento"] is not None)
    sin_pvp = sum(1 for it in items if it["compare_at_price"] is None)
    if not ds:
        return {"n": 0}

    def pct(q: float) -> float:
        return round(ds[min(int(q * len(ds)), len(ds) - 1)], 1)

    tramos = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 50), (50, 101)]
    return {
        "n_con_descuento": len(ds),
        "n_sin_compare_at_price": sin_pvp,
        "min": round(ds[0], 1), "p25": pct(0.25), "mediana": round(statistics.median(ds), 1),
        "p75": pct(0.75), "p90": pct(0.90), "max": round(ds[-1], 1),
        "media": round(statistics.mean(ds), 1),
        "ratio_p90_p25": round(pct(0.90) / pct(0.25), 1) if pct(0.25) else None,
        "histograma": [{"tramo": f"{lo}-{hi}%",
                        "n": sum(1 for x in ds if lo <= x < hi)} for lo, hi in tramos],
        "pct_por_encima_de_30": round(100 * sum(1 for x in ds if x >= 30) / len(ds), 1),
    }


def calidad_campos(items: list[dict]) -> dict:
    """Qué campos de Shopify llevan información aprovechable y cuáles no."""
    n = len(items)
    tipos = Counter(it["product_type"] or "(vacío)" for it in items)
    dominante, n_dom = tipos.most_common(1)[0]
    disp = sum(1 for it in items if it["available"])
    return {
        "product_type": {
            "valores_distintos": len(tipos),
            "valor_dominante": dominante,
            "pct_dominante": round(100 * n_dom / n, 1),
            "informativo": len(tipos) > 2 and n_dom / n < 0.9,
        },
        "available": {
            "pct_disponible": round(100 * disp / n, 1),
            "n_no_disponible": n - disp,
            "informativo": 0.02 < (n - disp) / n < 0.98,
        },
        "compare_at_price": {
            "pct_presente": round(100 * sum(
                1 for it in items if it["compare_at_price"]) / n, 1),
        },
    }


def precios(items: list[dict]) -> dict:
    ps = sorted(it["price"] for it in items if it["price"])
    if not ps:
        return {}
    return {
        "n": len(ps), "min": ps[0], "mediana": statistics.median(ps),
        "media": round(statistics.mean(ps), 2),
        "p90": ps[int(0.9 * len(ps))], "max": ps[-1],
    }


# --------------------------------------------------------------------------- #

def construir(conn, fecha=None) -> dict:
    fecha, items = cargar(conn, fecha)
    return {
        "fecha": fecha,
        "n_referencias": len({it["product_id"] for it in items}),
        "n_variantes": len(items),
        "cardinalidad_tags": cardinalidad_tags(items),
        "cobertura_origen": cobertura_origen(items),
        "distribucion_descuento": distribucion_descuento(items),
        "precio_por_motivo": precio_por_motivo(items),
        "calidad_campos": calidad_campos(items),
        "precios": precios(items),
    }


def imprimir(r: dict) -> None:
    print(f"\n=== TAXONOMÍA DEL CATÁLOGO — {r['fecha']} ===")
    print(f"{r['n_referencias']} referencias, {r['n_variantes']} variantes\n")

    c = r["cardinalidad_tags"]
    print("Cardinalidad de tags:")
    for k, v in c["distribucion"].items():
        print(f"  {k} tag(s): {v}")
    if c["usado_como_categoria_unica"]:
        print("  -> El campo `tags` se usa como CATEGORÍA ÚNICA, no como conjunto.")
        print("     Un producto no puede ser a la vez 'Fecha corta' y 'Productor local'.")

    co = r["cobertura_origen"]
    print(f"\nCobertura del motivo de excedente:")
    print(f"  con tag de origen : {co['con_tag_origen']:>4}  ({co['pct_con_tag_origen']}%)")
    print(f"  sin tag de origen : {co['sin_tag_origen']:>4}  ({co['pct_sin_tag_origen']}%)")
    print(f"     de ellos, con etiqueta sólo comercial: {co['solo_tag_comercial']}")

    d = r["distribucion_descuento"]
    print(f"\nDescuento sobre PVP (n={d['n_con_descuento']}):")
    print(f"  min {d['min']}%  p25 {d['p25']}%  mediana {d['mediana']}%  "
          f"p75 {d['p75']}%  p90 {d['p90']}%  max {d['max']}%")
    print(f"  media {d['media']}%   dispersión p90/p25 = {d['ratio_p90_p25']}x")
    print(f"  por encima del 30%: {d['pct_por_encima_de_30']}% de las referencias")
    for h in d["histograma"]:
        barra = "#" * int(60 * h["n"] / d["n_con_descuento"])
        print(f"    {h['tramo']:>8} {h['n']:>4}  {barra}")

    print("\nDescuento por etiqueta:")
    print(f"  {'tag':<24} {'fam':<10} {'n':>5} {'medio':>7} {'medi':>6} {'p90':>6}")
    for p in r["precio_por_motivo"]:
        alerta = " (n bajo)" if p["muestra_pequena"] else ""
        print(f"  {p['tag'][:23]:<24} {p['familia']:<10} {p['referencias']:>5} "
              f"{p['descuento_medio_pct']!s:>7} {p['descuento_mediana_pct']!s:>6} "
              f"{p['descuento_p90_pct']!s:>6}{alerta}")

    q = r["calidad_campos"]
    print("\nCalidad de campos:")
    pt = q["product_type"]
    print(f"  product_type : {pt['valores_distintos']} valores, "
          f"'{pt['valor_dominante']}' cubre el {pt['pct_dominante']}% "
          f"-> {'informativo' if pt['informativo'] else 'NO informativo'}")
    av = q["available"]
    print(f"  available    : {av['pct_disponible']}% disponible "
          f"({av['n_no_disponible']} no) "
          f"-> {'informativo' if av['informativo'] else 'NO informativo'}")
    print(f"  compare_at_price presente en el {q['compare_at_price']['pct_presente']}%")

    p = r["precios"]
    print(f"\nPrecios: mediana {p['mediana']} €  media {p['media']} €  "
          f"p90 {p['p90']} €  max {p['max']} €\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--fecha", help="YYYY-MM-DD; por defecto el último snapshot")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH)
    r = construir(conn, args.fecha)
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
