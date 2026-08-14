#!/usr/bin/env python3
"""
Snapshot diario del catálogo público de Remolonas.

Descarga el endpoint público products.json (estándar de Shopify), guarda el JSON
crudo comprimido y normaliza las observaciones a SQLite, con grano una fila por
variante y por día.

Uso:
    python snapshot.py                 # snapshot de hoy
    python snapshot.py --dry-run       # no escribe nada, sólo informa
    python snapshot.py --force         # re-escribe el snapshot de hoy si ya existe

Diseño deliberado:
  - Una única ejecución al día. No es un scraper agresivo.
  - Respeta robots.txt antes de pedir nada.
  - Guarda SIEMPRE el JSON crudo. Las métricas se pueden recalcular; los datos
    de un día que no se guardó, no.
  - Idempotente: correrlo dos veces el mismo día no duplica filas.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://www.remolonas.com"
PRODUCTS_PATH = "/products.json"
PAGE_SIZE = 250          # máximo que admite Shopify
MAX_PAGES = 40           # cortafuegos: 10.000 productos es mucho más de lo que tienen
PAUSE_BETWEEN_PAGES = 1.5  # segundos. Cortesía, no hay prisa.
TIMEOUT = 30

# Identifícate. Si alguien de Remolonas mira sus logs, que sepa quién eres y por qué.
# Pon tu email real antes de la primera ejecución.
USER_AGENT = (
    "remolonas-catalog-study/1.0 "
    "(estudio personal de rotación de catálogo; contacto: TU_EMAIL@ejemplo.com)"
)

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "catalog.sqlite"
RAW_DIR = ROOT / "data" / "raw"


# --------------------------------------------------------------------------- #
# Esquema
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_date TEXT PRIMARY KEY,        -- YYYY-MM-DD (UTC)
    taken_at_utc  TEXT NOT NULL,           -- ISO-8601 completo
    n_products    INTEGER NOT NULL,
    n_variants    INTEGER NOT NULL,
    n_pages       INTEGER NOT NULL,
    raw_file      TEXT NOT NULL,
    ok            INTEGER NOT NULL DEFAULT 1,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS observations (
    snapshot_date     TEXT NOT NULL,
    product_id        INTEGER NOT NULL,
    variant_id        INTEGER NOT NULL,
    handle            TEXT,
    title             TEXT,
    product_type      TEXT,
    vendor            TEXT,
    tags              TEXT,                -- separados por '|'
    published_at      TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    variant_title     TEXT,
    sku               TEXT,
    price             REAL,
    compare_at_price  REAL,
    available         INTEGER,
    grams             INTEGER,
    position          INTEGER,
    n_images          INTEGER,
    PRIMARY KEY (snapshot_date, variant_id)
);

CREATE INDEX IF NOT EXISTS idx_obs_product ON observations(product_id);
CREATE INDEX IF NOT EXISTS idx_obs_date    ON observations(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_obs_type    ON observations(product_type);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# Descarga
# --------------------------------------------------------------------------- #

def robots_allows(url: str) -> bool:
    """Comprueba robots.txt. Si no se puede leer, asumimos permitido (Shopify
    no suele bloquear products.json), pero lo dejamos registrado."""
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{BASE}/robots.txt")
    try:
        rp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"  aviso: no se pudo leer robots.txt ({exc}); continúo", file=sys.stderr)
        return True
    return rp.can_fetch(USER_AGENT, url)


def fetch_page(page: int) -> list[dict]:
    url = f"{BASE}{PRODUCTS_PATH}?limit={PAGE_SIZE}&page={page}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.load(resp)
    return payload.get("products", [])


def fetch_catalog() -> list[dict]:
    """Pagina hasta agotar el catálogo."""
    first_url = f"{BASE}{PRODUCTS_PATH}"
    if not robots_allows(first_url):
        raise SystemExit("robots.txt no permite este endpoint. Abortando por diseño.")

    products: list[dict] = []
    pages = 0
    for page in range(1, MAX_PAGES + 1):
        batch = fetch_page(page)
        pages = page
        if not batch:
            break
        products.extend(batch)
        print(f"  página {page}: {len(batch)} productos (acumulado {len(products)})")
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(PAUSE_BETWEEN_PAGES)
    else:
        print(f"  aviso: alcanzado MAX_PAGES={MAX_PAGES}", file=sys.stderr)
    return products, pages


# --------------------------------------------------------------------------- #
# Normalización
# --------------------------------------------------------------------------- #

def _to_float(value) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize(products: list[dict], snapshot_date: str) -> list[tuple]:
    """Aplana productos y variantes a filas de la tabla observations."""
    rows: list[tuple] = []
    for prod in products:
        tags = prod.get("tags") or []
        if isinstance(tags, str):  # Shopify a veces devuelve string
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        tags_str = "|".join(tags)
        n_images = len(prod.get("images") or [])

        for var in prod.get("variants") or []:
            rows.append((
                snapshot_date,
                prod.get("id"),
                var.get("id"),
                prod.get("handle"),
                prod.get("title"),
                prod.get("product_type"),
                prod.get("vendor"),
                tags_str,
                prod.get("published_at"),
                prod.get("created_at"),
                prod.get("updated_at"),
                var.get("title"),
                var.get("sku"),
                _to_float(var.get("price")),
                _to_float(var.get("compare_at_price")),
                1 if var.get("available") else 0,
                var.get("grams"),
                var.get("position"),
                n_images,
            ))
    return rows


# --------------------------------------------------------------------------- #
# Persistencia
# --------------------------------------------------------------------------- #

def save_raw(products: list[dict], snapshot_date: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"products_{snapshot_date}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(products, fh, ensure_ascii=False)
    return path


def store(conn: sqlite3.Connection, rows: list[tuple], meta: dict, force: bool) -> None:
    cur = conn.cursor()
    if force:
        cur.execute("DELETE FROM observations WHERE snapshot_date = ?", (meta["snapshot_date"],))
        cur.execute("DELETE FROM snapshots    WHERE snapshot_date = ?", (meta["snapshot_date"],))

    cur.executemany(
        "INSERT OR REPLACE INTO observations VALUES (" + ",".join("?" * 19) + ")",
        rows,
    )
    cur.execute(
        "INSERT OR REPLACE INTO snapshots "
        "(snapshot_date, taken_at_utc, n_products, n_variants, n_pages, raw_file, ok, note) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            meta["snapshot_date"], meta["taken_at_utc"], meta["n_products"],
            meta["n_variants"], meta["n_pages"], meta["raw_file"], 1, None,
        ),
    )
    conn.commit()


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot diario del catálogo de Remolonas")
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada")
    ap.add_argument("--force", action="store_true", help="sobrescribe el snapshot de hoy")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    snapshot_date = now.date().isoformat()
    print(f"Snapshot {snapshot_date} ({now.isoformat(timespec='seconds')})")

    conn = connect()
    existing = conn.execute(
        "SELECT n_products FROM snapshots WHERE snapshot_date = ?", (snapshot_date,)
    ).fetchone()
    if existing and not args.force:
        print(f"  ya existe un snapshot de hoy ({existing[0]} productos). Usa --force para rehacerlo.")
        return 0

    try:
        products, pages = fetch_catalog()
    except urllib.error.HTTPError as exc:
        print(f"ERROR HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR de red: {exc}", file=sys.stderr)
        return 1

    rows = normalize(products, snapshot_date)
    print(f"  {len(products)} productos, {len(rows)} variantes")

    if args.dry_run:
        print("  --dry-run: no se ha escrito nada")
        return 0

    raw_path = save_raw(products, snapshot_date)
    store(conn, rows, {
        "snapshot_date": snapshot_date,
        "taken_at_utc": now.isoformat(timespec="seconds"),
        "n_products": len(products),
        "n_variants": len(rows),
        "n_pages": pages,
        "raw_file": str(raw_path.relative_to(ROOT)),
    }, force=args.force)

    total_days = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    print(f"  guardado. Días acumulados en la serie: {total_days}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
