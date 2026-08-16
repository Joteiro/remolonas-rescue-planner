#!/usr/bin/env python3
"""
Rotación reconstruida hacia atrás desde `published_at`.

EL ATAJO QUE NO VI AL PRINCIPIO
    Monté `radar.py` para medir altas y bajas comparando snapshots consecutivos,
    asumiendo que había que esperar semanas. Pero cada producto trae su propia
    fecha de publicación, así que el histórico de ALTAS es reconstruible con un
    solo snapshot — doce meses hacia atrás, hoy.

    Lo que NO se puede reconstruir son las BAJAS: un producto que salió del
    catálogo no está en el catálogo, así que no trae su fecha. Esa asimetría es
    la razón por la que la serie diaria sigue teniendo sentido.

EL SESGO QUE HAY QUE DECLARAR SIEMPRE
    Esto es un análisis de SUPERVIVIENTES. Sólo vemos los productos que siguen
    publicados hoy. De los que entraron en marzo y salieron en mayo no queda
    rastro. Por construcción, la distribución está sesgada hacia lo reciente.

    Consecuencia: "el 85 % del catálogo se publicó en los últimos 3 meses" NO
    significa "el 85 % de lo que han vendido nunca es de los últimos 3 meses".
    Significa que lo viejo, o rotó, o nunca existió. Distinguirlo requiere saber
    cuánto tiempo lleva la tienda abierta — ver `contexto_edad_negocio()`.

    python cohortes.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "catalog.sqlite"

# La operación comercial arrancó a mediados de 2024 (prensa; ver HALLAZGOS.md).
# Se usa sólo para separar "rotación" de "negocio joven".
INICIO_OPERACION = date(2024, 7, 1)


def _fecha(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        return None


def cargar(conn, fecha_snapshot: str | None = None):
    if fecha_snapshot is None:
        fecha_snapshot = conn.execute(
            "SELECT MAX(snapshot_date) FROM observations").fetchone()[0]
    rows = conn.execute("""
        SELECT DISTINCT product_id, title, published_at, created_at, tags, vendor
        FROM observations WHERE snapshot_date = ?""", (fecha_snapshot,)).fetchall()
    items = []
    for pid, title, pub, cre, tags, vendor in rows:
        f = _fecha(pub) or _fecha(cre)
        if f:
            items.append({"product_id": pid, "title": title, "publicado": f,
                          "tag": (tags or "").split("|")[0] or None, "vendor": vendor})
    return fecha_snapshot, items


def altas_por_semana(items, hoy: date, n_semanas: int = 16) -> list[dict]:
    """Altas por semana ISO, excluyendo la semana en curso (incompleta)."""
    cuenta = Counter(it["publicado"].isocalendar()[:2] for it in items)
    semana_actual = hoy.isocalendar()[:2]
    filas = [{"semana": f"{a}-W{w:02d}", "altas": n}
             for (a, w), n in sorted(cuenta.items(), reverse=True)
             if (a, w) != semana_actual][:n_semanas]
    return filas


def antiguedad(items, hoy: date) -> dict:
    """Qué fracción del catálogo actual se publicó en cada ventana."""
    n = len(items)
    out = {}
    dias = sorted((hoy - it["publicado"]).days for it in items)
    # Ventanas en días exactos. La primera versión usaba el día 1 del mes, así
    # que "menos de 1 mes" eran en realidad hasta 45 días e inflaba la cifra.
    for tope, etq in [(30, "30_dias"), (90, "90_dias"), (180, "180_dias"), (365, "365_dias")]:
        k = sum(1 for d in dias if d <= tope)
        out[etq] = {"n": k, "pct": round(100 * k / n, 1)}
    out["dias_desde_publicacion"] = {
        "mediana": dias[len(dias) // 2],
        "p25": dias[len(dias) // 4],
        "p75": dias[3 * len(dias) // 4],
        "max": dias[-1],
    }
    return out


def tasa_rotacion(filas_semana: list[dict], n_catalogo: int) -> dict:
    """Rotación semanal, bajo el supuesto de catálogo en estado estacionario.

    Si el catálogo se mantiene en ~N referencias y entran A por semana, entonces
    salen ~A por semana. Ese supuesto NO está verificado — hacen falta más días
    de serie para medir bajas de verdad. Se declara como lo que es.
    """
    if not filas_semana:
        return {}
    recientes = [f["altas"] for f in filas_semana[:8]]
    media = sum(recientes) / len(recientes)
    tasa = media / n_catalogo
    return {
        "altas_semana_media_8s": round(media, 1),
        "altas_semana_min": min(recientes),
        "altas_semana_max": max(recientes),
        "tasa_renovacion_semanal_pct": round(100 * tasa, 1),
        "semanas_para_renovar_la_mitad": round(0.5 / tasa, 1) if tasa else None,
        "supuesto": ("Estado estacionario: se asume que salen tantas referencias "
                     "como entran, porque el tamaño del catálogo es estable. "
                     "NO verificado: hacen falta más días de serie diaria."),
    }


def contexto_edad_negocio(items, hoy: date) -> dict:
    """Separa 'el catálogo rota' de 'la empresa es joven'.

    Si la tienda lleva 26 meses abierta y sólo 1 referencia de 514 tiene más de
    12 meses, la juventud del negocio no lo explica: es rotación.
    """
    meses_operando = (hoy.year - INICIO_OPERACION.year) * 12 + hoy.month - INICIO_OPERACION.month
    mas_12m = sum(1 for it in items
                  if (hoy - it["publicado"]).days > 365)
    return {
        "meses_operando_aprox": meses_operando,
        "referencias_con_mas_de_12_meses": mas_12m,
        "pct": round(100 * mas_12m / len(items), 1),
        "lectura": (
            f"La tienda lleva ~{meses_operando} meses operando, así que podría "
            f"haber referencias de hasta {meses_operando} meses de antigüedad. "
            f"Hay {mas_12m}. Si ese número es muy bajo, la explicación no es que "
            f"el negocio sea joven — es que el surtido se renueva."),
    }


def novedad_percibida(items, hoy: date) -> dict:
    """Cuánto catálogo es NUEVO para un cliente que vuelve tras X días.

    La rotación se suele leer como un problema de operaciones. Tiene una cara
    comercial simétrica que sale del mismo dato: si entran ~37 referencias por
    semana, un suscriptor semanal se encuentra con algo nuevo cada vez que abre
    la tienda, y quien vuelve tras un mes encuentra un tercio del surtido
    cambiado.

    Y la cara incómoda de lo mismo: con una mediana de 46 días publicado, un
    cliente que se engancha a un producto lo pierde en unas siete semanas. Eso
    es una fuga de retención que el modelo de negocio genera por diseño, no un
    fallo operativo.

    Ninguna de las dos cosas se puede cuantificar en euros desde fuera — hace
    falta saber qué compra la gente y por qué se da de baja. Aquí sólo está el
    tamaño del fenómeno.
    """
    dias = sorted((hoy - it["publicado"]).days for it in items)
    n = len(dias)
    ventanas = []
    for x in (7, 14, 30, 60, 90, 180):
        k = sum(1 for v in dias if v <= x)
        ventanas.append({"dias_ausente": x, "referencias_nuevas": k,
                         "pct_catalogo_nuevo": round(100 * k / n, 1)})
    return {
        "ventanas": ventanas,
        "mediana_dias_publicado": dias[n // 2],
        "nota": ("Sesgo de supervivencia: se cuenta lo que sigue publicado. "
                 "La novedad real es mayor, porque además hay referencias que "
                 "entraron y salieron dentro de la ventana."),
    }


def altas_por_dimension(items, hoy: date, clave: str, top: int = 8) -> list[dict]:
    """Antigüedad mediana por etiqueta o proveedor: ¿qué rota más rápido?"""
    grupos: dict[str, list[int]] = {}
    for it in items:
        k = it.get(clave) or "(sin valor)"
        grupos.setdefault(k, []).append((hoy - it["publicado"]).days)
    filas = [{"valor": k, "referencias": len(v),
              "dias_mediana": sorted(v)[len(v) // 2]}
             for k, v in grupos.items() if len(v) >= 5]
    return sorted(filas, key=lambda x: x["dias_mediana"])[:top]


def construir(conn) -> dict:
    fecha_snapshot, items = cargar(conn)
    hoy = date.fromisoformat(fecha_snapshot)
    semanas = altas_por_semana(items, hoy)
    return {
        "fecha": fecha_snapshot,
        "n_referencias": len(items),
        "altas_por_semana": semanas,
        "antiguedad": antiguedad(items, hoy),
        "rotacion": tasa_rotacion(semanas, len(items)),
        "edad_negocio": contexto_edad_negocio(items, hoy),
        "novedad_percibida": novedad_percibida(items, hoy),
        "rotacion_por_etiqueta": altas_por_dimension(items, hoy, "tag"),
        "rotacion_por_proveedor": altas_por_dimension(items, hoy, "vendor"),
    }


def imprimir(r: dict) -> None:
    print(f"\n=== COHORTES DE ALTA — snapshot {r['fecha']}, "
          f"{r['n_referencias']} referencias ===")
    print("  (reconstruido desde published_at; sesgo de supervivencia, ver docstring)\n")

    print("Altas por semana (semana en curso excluida):")
    for f in r["altas_por_semana"][:12]:
        print(f"  {f['semana']}  {f['altas']:>3}  {'#' * f['altas']}")

    rot = r["rotacion"]
    print(f"\nRotación:")
    print(f"  media de las últimas 8 semanas : {rot['altas_semana_media_8s']} altas/semana "
          f"(min {rot['altas_semana_min']}, max {rot['altas_semana_max']})")
    print(f"  tasa de renovación             : {rot['tasa_renovacion_semanal_pct']}% semanal")
    print(f"  media vida del surtido         : ~{rot['semanas_para_renovar_la_mitad']} "
          f"semanas para renovar la mitad")
    print(f"  SUPUESTO: {rot['supuesto']}")

    a = r["antiguedad"]
    print(f"\nAntigüedad del catálogo actual:")
    for etq in ("30_dias", "90_dias", "180_dias", "365_dias"):
        print(f"  publicado hace {etq.replace('_dias', ''):>3} días o menos: "
              f"{a[etq]['n']:>3}  ({a[etq]['pct']}%)")
    d = a["dias_desde_publicacion"]
    print(f"  días desde publicación: p25 {d['p25']}  mediana {d['mediana']}  "
          f"p75 {d['p75']}  max {d['max']}")

    e = r["edad_negocio"]
    print(f"\n{e['lectura']}")

    nv = r["novedad_percibida"]
    print(f"\nNovedad para un cliente que vuelve tras X días:")
    for v in nv["ventanas"]:
        print(f"  {v['dias_ausente']:>3} días fuera -> {v['referencias_nuevas']:>3} "
              f"referencias nuevas para él ({v['pct_catalogo_nuevo']}% del catálogo)")
    print(f"  Cara B: mediana de {nv['mediana_dias_publicado']} días publicado — un "
          f"cliente que se engancha a algo lo pierde en ~7 semanas.")

    print(f"\nRotación más rápida por etiqueta (mediana de días publicado):")
    for f in r["rotacion_por_etiqueta"]:
        print(f"  {f['valor'][:28]:<28} {f['referencias']:>4} ref   {f['dias_mediana']:>4} d")

    print(f"\nProveedores con surtido más reciente:")
    for f in r["rotacion_por_proveedor"]:
        print(f"  {f['valor'][:28]:<28} {f['referencias']:>4} ref   {f['dias_mediana']:>4} d")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    conn = sqlite3.connect(DB_PATH)
    r = construir(conn)
    if args.out:
        args.out.write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str),
                            encoding="utf-8")
        print(f"escrito {args.out}")
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        imprimir(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
