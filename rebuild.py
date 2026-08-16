#!/usr/bin/env python3
"""
Reconstruye data/catalog.sqlite desde los JSON crudos de data/raw/.

POR QUÉ EXISTE
    El .sqlite es un binario. Si lo versionas en git y lo escriben dos sitios —
    tu portátil y el runner de GitHub Actions — tarde o temprano hay un conflicto
    que git no sabe resolver, y la única salida es tirar una de las dos versiones.

    Los .json.gz no tienen ese problema: uno por día, se escriben una vez y no se
    vuelven a tocar. Nunca chocan. Así que la fuente de verdad son ellos, y la
    base de datos pasa a ser un derivado reconstruible.

    Es la misma idea que un lockfile frente a node_modules: versionas la receta,
    no el resultado.

    python rebuild.py            # reconstruye desde cero
    python rebuild.py --check    # verifica que la base coincide con los crudos
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from snapshot import DB_PATH, RAW_DIR, connect, normalize  # noqa: E402


def dias_disponibles() -> list[tuple[str, Path]]:
    out = []
    for p in sorted(RAW_DIR.glob("products_*.json.gz")):
        fecha = p.stem.replace("products_", "").replace(".json", "")
        out.append((fecha, p))
    return out


def reconstruir(destino: Path = DB_PATH, verbose: bool = True) -> int:
    if destino.exists():
        destino.unlink()
    conn = connect(destino)
    total = 0
    for fecha, path in dias_disponibles():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            products = json.load(fh)
        rows = normalize(products, fecha)
        cur = conn.cursor()
        cur.executemany("INSERT OR REPLACE INTO observations VALUES ("
                        + ",".join("?" * 19) + ")", rows)
        cur.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?)",
                    (fecha, f"{fecha}T00:00:00+00:00", len(products), len(rows),
                     0, str(path.relative_to(destino.parent.parent)), 1,
                     "reconstruido desde crudo"))
        conn.commit()
        total += len(rows)
        if verbose:
            print(f"  {fecha}: {len(products)} productos, {len(rows)} variantes")
    if verbose:
        print(f"Reconstruida {destino.name}: {len(dias_disponibles())} días, {total} filas")
    return total


def comprobar() -> int:
    """¿La base coincide con lo que dicen los crudos? Salida 0 si sí, 1 si no."""
    if not DB_PATH.exists():
        print("No hay base de datos. Corre `python rebuild.py`.")
        return 1
    conn = sqlite3.connect(DB_PATH)
    en_db = {r[0] for r in conn.execute("SELECT snapshot_date FROM snapshots")}
    en_raw = {f for f, _ in dias_disponibles()}
    ok = True
    if en_raw - en_db:
        print(f"Días en crudo que faltan en la base: {sorted(en_raw - en_db)}")
        ok = False
    if en_db - en_raw:
        print(f"Días en la base sin crudo (¡no reconstruibles!): {sorted(en_db - en_raw)}")
        ok = False
    for fecha, path in dias_disponibles():
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            n_raw = len(json.load(fh))
        n_db = conn.execute(
            "SELECT COUNT(DISTINCT product_id) FROM observations WHERE snapshot_date=?",
            (fecha,)).fetchone()[0]
        if n_raw != n_db:
            print(f"  {fecha}: crudo {n_raw} productos, base {n_db}")
            ok = False
    print("La base coincide con los crudos." if ok else "Hay discrepancias.")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    raise SystemExit(comprobar() if a.check else (reconstruir() and 0))
