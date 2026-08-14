"""
Valida la lógica de normalización y las métricas del radar sin tocar la red.

Construye un catálogo sintético de 5 días con altas y bajas conocidas y comprueba
que el radar las recupera exactamente. Si esto pasa, lo único no probado es la
llamada HTTP real.

    python -m pytest tests/ -q
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import radar          # noqa: E402
import snapshot       # noqa: E402


def producto(pid: int, titulo: str, precio: str, pvp: str | None,
             tipo: str = "FRUTA", tags=("Excedente",)) -> dict:
    """Producto con la forma exacta que devuelve Shopify."""
    return {
        "id": pid,
        "handle": titulo.lower().replace(" ", "-"),
        "title": titulo,
        "product_type": tipo,
        "vendor": "REMOLONAS",
        "tags": list(tags),
        "published_at": "2026-01-01T00:00:00+01:00",
        "created_at": "2026-01-01T00:00:00+01:00",
        "updated_at": "2026-08-01T00:00:00+02:00",
        "images": [{"src": "x.jpg"}],
        "variants": [{
            "id": pid * 10,
            "title": "Default Title",
            "sku": f"SKU{pid}",
            "price": precio,
            "compare_at_price": pvp,
            "available": True,
            "grams": 1000,
            "position": 1,
        }],
    }


def test_normalize_aplana_variantes():
    prods = [producto(1, "Peras", "1.50", "3.00"),
             producto(2, "Limones", "0.99", None)]
    rows = snapshot.normalize(prods, "2026-08-13")
    assert len(rows) == 2
    assert rows[0][0] == "2026-08-13"      # snapshot_date
    assert rows[0][1] == 1                 # product_id
    assert rows[0][2] == 10                # variant_id
    assert rows[0][13] == 1.50             # price
    assert rows[0][14] == 3.00             # compare_at_price
    assert rows[1][14] is None             # sin compare_at_price


def test_normalize_acepta_tags_como_string():
    """Shopify devuelve tags como lista o como string separado por comas."""
    p = producto(3, "Kiwi", "2.00", "4.00")
    p["tags"] = "Excedente, Rescate"
    rows = snapshot.normalize([p], "2026-08-13")
    assert rows[0][7] == "Excedente|Rescate"


def _db_con_serie(tmp_path: Path) -> sqlite3.Connection:
    """5 días. El producto 1 vive los 5. El 2 sale el día 3. El 3 entra el día 2.
    El 4 entra el día 2 y sale el día 4 -> única vida COMPLETA observable."""
    catalogos = {
        "2026-08-09": [1, 2],
        "2026-08-10": [1, 2, 3, 4],
        "2026-08-11": [1, 3, 4],
        "2026-08-12": [1, 3, 4],
        "2026-08-13": [1, 3],
    }
    conn = snapshot.connect(tmp_path / "t.sqlite")
    for fecha, ids in catalogos.items():
        prods = [producto(i, f"P{i}", "1.00", "2.00") for i in ids]
        rows = snapshot.normalize(prods, fecha)
        snapshot.store(conn, rows, {
            "snapshot_date": fecha, "taken_at_utc": f"{fecha}T06:00:00+00:00",
            "n_products": len(prods), "n_variants": len(rows),
            "n_pages": 1, "raw_file": "n/a",
        }, force=True)
    return conn


def test_altas_y_bajas(tmp_path):
    conn = _db_con_serie(tmp_path)
    dates = radar.load(conn)["dates"]
    ab = {d["fecha"]: d for d in radar.altas_y_bajas(conn, dates)}

    assert ab["2026-08-10"]["altas"] == 2 and ab["2026-08-10"]["bajas"] == 0
    assert ab["2026-08-11"]["altas"] == 0 and ab["2026-08-11"]["bajas"] == 1   # sale el 2
    assert ab["2026-08-13"]["altas"] == 0 and ab["2026-08-13"]["bajas"] == 1   # sale el 4


def test_censura_se_distingue_correctamente(tmp_path):
    """El corazón del asunto: sólo el producto 4 tiene vida completa observada."""
    conn = _db_con_serie(tmp_path)
    dates = radar.load(conn)["dates"]
    v = radar.vida_referencias(conn, dates)

    assert v["vidas_completas"]["n"] == 1          # sólo el producto 4
    assert v["vidas_completas"]["media_dias"] == 3.0
    assert v["vidas_censuradas"]["n"] == 3         # 1 y 2 (ya estaban), 3 (sigue vivo)


def test_descuento_efectivo(tmp_path):
    conn = _db_con_serie(tmp_path)
    serie = radar.serie_diaria(conn)
    assert serie[0]["descuento_medio_pct"] == 50.0     # 1.00 sobre 2.00
    assert serie[0]["productos"] == 2


def test_idempotencia(tmp_path):
    """Correr dos veces el mismo día no duplica filas."""
    conn = snapshot.connect(tmp_path / "t.sqlite")
    prods = [producto(1, "Peras", "1.50", "3.00")]
    meta = {"snapshot_date": "2026-08-13", "taken_at_utc": "2026-08-13T06:00:00+00:00",
            "n_products": 1, "n_variants": 1, "n_pages": 1, "raw_file": "n/a"}
    for _ in range(3):
        snapshot.store(conn, snapshot.normalize(prods, "2026-08-13"), meta, force=False)
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
