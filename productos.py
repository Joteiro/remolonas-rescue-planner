#!/usr/bin/env python3
"""
Enriquecimiento del catálogo: peso real y categoría.

Dos campos que el motor de asignación necesita y que el catálogo no da limpios:

1. PESO. El campo `grams` de Shopify viene a cero en el 25,6 % de las referencias
   (14-ago-2026), concentrado en líquidos: zumos, aceites, caldos, leche. Sin
   peso no se puede llenar una caja de 11 kg. Pero el título casi siempre lleva
   el formato ("Zumo 100% Natural de Manzana 250 ml"), así que se puede
   recuperar parseándolo.

2. CATEGORÍA. `product_type` es inservible (99,6 % 'UPSELLING') y `tags` sólo
   admite un valor por producto, ocupado casi siempre por el motivo comercial.
   Sin categoría no hay exclusiones ("no quiero lácteos") ni variedad mínima
   por caja. Se infiere del título con reglas de palabra clave.

Ambas son heurísticas y como tales se miden: `informe_cobertura()` dice qué
porcentaje se resolvió y con qué método, para que nadie confunda una estimación
con un dato.

    python productos.py            # informe de cobertura sobre el último snapshot
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "catalog.sqlite"

# Supuesto explícito: para alimentos líquidos usamos densidad 1 g/ml.
# Es exacto para caldos y zumos, y sobreestima un ~8 % en aceite (0,92 g/ml).
# Aceptable para llenar cajas; NO usar para cálculos logísticos de peso real.
DENSIDAD_G_POR_ML = 1.0

# Orden importante: la primera regla que casa, gana. Las más específicas arriba.
#
# Las reglas se afinaron contra los 513 títulos reales del 14-ago-2026 hasta
# superar el 90 % de cobertura. Es una heurística de catálogo, no un
# clasificador: con productos nuevos habrá que revisarla, y por eso
# `informe_cobertura()` reporta siempre qué porcentaje quedó sin clasificar.
REGLAS_CATEGORIA: list[tuple[str, tuple[str, ...]]] = [
    # --- Muy específicas primero, para que no las capture una regla general ---
    ("Vinos y alcohol",   ("verdejo", "tinto", "crianza", "rosado", "ribera", "rueda",
                           "roble", "albariño", "reserva", "cava", "espumoso",
                           "prios maximus", "sinfo", "aldor", "granza")),
    ("Frutos secos y semillas",("nuez", "nueces", "almendra", "anacardo", "pistacho",
                           "avellanas", "cacahuete", "pipa", "pasas", "datil",
                           "frutos secos", "chia", "sesamo", "lino", "polen",
                           "ciruelas sin hueso", "desecada")),
    ("Golosinas",         ("nubes", "regaliz", "sugus", "mochi", "peach ring", "sour ",
                           "gominola", "fini", "pelotazos", "tubes", "strips",
                           "galaxy mix", "party mix", "cinema mix", "chuche")),
    ("Especias y condimentos",("perejil", "romero", "tomillo", "pimienta", "pimenton",
                           "sazonador", "azucar", "sal sana", "sal rosa", "oregano",
                           "curry", "comino", "canela", "ajo en polvo", "laurel")),
    ("Conservas vegetales",("esparrago", "alcachofa", "guisante", "champinon", "seta",
                           "remolacha cocida", "fabada", "maiz dulce", "pimiento del",
                           "menestra", "tomate frito", "tomate natural",
                           "tomate triturado")),
    ("Bebé y mascotas",   ("bebe", "potito", "papilla", "tarrito", "pure ecologico",
                           "pañal", "perro", "gato", "mascota", "pienso")),
    ("Vegetal y plant-based",("jackfruit", "veggie", "vegetal", "hummus", "guacamole",
                           "tofu", "seitan", "heura")),
    # --- Generales ---
    ("Lácteos y huevos",  ("leche", "yogur", "queso", "mantequilla", "nata", "kefir",
                           "huevo", "cuajada", "requeson")),
    ("Galletas y dulces", ("galleta", "campurriana", "tostarica", "bizcocho", "magdalena",
                           "chocolate", "turron", "caramelo", "bombon", "filipinos",
                           "napolitana", "cracker", "barquillo", "croissant", "palmerita",
                           "membrillo", "nidos de crema", "fino fino")),
    ("Desayuno y untables",("mermelada", "confitura", "miel", "crema de", "cacao",
                           "pate", "untar", "avellana", "cereales", "granola", "muesli",
                           "krunchy", "espelta", "desayuno", "fuagra", "tapa negra",
                           "paleta seleccion", "virutas ibericas", "pesto")),
    ("Bebidas",           ("zumo", "bebida", "refresco", "agua", "cerveza", "vino",
                           "sidra", "batido", "horchata", "infusion", "te ",
                           "pepsi", "7up", "aquarade", "ice tea", "lipton", "kombucha",
                           "mosto", "cola", "tonica", "frutanesa")),
    ("Café e infusiones", ("cafe", "capsul", "descafein")),
    ("Aceites y vinagres",("aceite", "vinagre", "oliva virgen")),
    ("Conservas de pescado",("mejillon", "atun", "sardina", "berberecho", "anchoa",
                           "bonito", "calamar", "pulpo", "ventresca", "almeja")),
    ("Legumbres y arroz", ("alubia", "lenteja", "garbanzo", "arroz", "soja", "quinoa",
                           "judia", "cocido al vapor")),
    ("Pasta y platos preparados",("pasta", "ravioli", "macarron", "espagueti", "fideo",
                           "lasa", "pizza", "tortellini", "noqui", "gnocchi",
                           "spaghetti", "helices", "tallarines", "penne", "tortilla de",
                           "tortillas de", "relleno de cangrejo")),
    ("Salsas y caldos",   ("salsa", "caldo", "sofrito", "pisto", "tomate frito",
                           "tomate natural", "tomate triturado", "mayonesa", "ketchup",
                           "mostaza", "gazpacho", "crema de verdura")),
    ("Snacks",            ("barrita", "patatas fritas", "snack", "aperitivo", "palomita",
                           "nachos", "tortitas", "doritos", "cortezas", "bagazitos",
                           "rosquilletas", "piscolabis", "aspitos", "bocaditos",
                           "jumpers", "picoteo", "cocktail", "mini tostas", "chiquitillos")),
    ("Fruta y verdura",   ("acelga", "lechuga", "manzana", "pera", "naranja",
                           "limon", "platano", "cebolla", "patata", "zanahoria",
                           "calabacin", "pimiento", "brocoli", "aguacate",
                           "kiwi", "melocoton", "fresa", "uva", "pitahaya", "mango",
                           "jengibre", "ajos", "espinaca", "borraja", "puerro",
                           "cardo", "ensalada", "calabaza", "berenjena", "apio", "col ")),
    ("Encurtidos",        ("aceituna", "pepinillo", "encurtido", "alcaparra")),
    ("Carne y embutido",  ("jamon", "chorizo", "salchichon", "lomo", "cecina",
                           "pollo", "ternera", "cerdo", "morcilla", "bacon")),
    ("Panadería",         ("pan ", "pan,", "tostada", "picos", "regan", "colines")),
]

CATEGORIA_DEFECTO = "Sin clasificar"


def _norm(s: str) -> str:
    """Minúsculas sin tildes, para que las reglas casen sin sorpresas."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# --------------------------------------------------------------------------- #
# Peso
# --------------------------------------------------------------------------- #

# Captura "250 ml", "1L", "1,5 L", "300 g", "1 kg", "4x42 g", "2 x 125 g".
_RE_PESO = re.compile(
    r"(?:(?P<mult>\d+)\s*[x×]\s*)?"
    r"(?P<cant>\d+(?:[.,]\d+)?)\s*"
    r"(?P<uni>kg|kilo|kilos|g|gr|gramos|ml|cl|l|litro|litros)\b",
    re.IGNORECASE,
)

_FACTOR_A_GRAMOS = {
    "kg": 1000.0, "kilo": 1000.0, "kilos": 1000.0,
    "g": 1.0, "gr": 1.0, "gramos": 1.0,
    "ml": DENSIDAD_G_POR_ML,
    "cl": 10.0 * DENSIDAD_G_POR_ML,
    "l": 1000.0 * DENSIDAD_G_POR_ML,
    "litro": 1000.0 * DENSIDAD_G_POR_ML,
    "litros": 1000.0 * DENSIDAD_G_POR_ML,
}


def peso_desde_titulo(titulo: str) -> float | None:
    """Extrae el peso en gramos del formato indicado en el título.

    Devuelve None si no hay formato reconocible. Si hay varias coincidencias
    ('Galletas TostaRica Choco Guay 4x42 g'), usa la ÚLTIMA, que en estos
    títulos es siempre el formato del envase — las anteriores suelen ser parte
    del nombre comercial.
    """
    matches = list(_RE_PESO.finditer(titulo or ""))
    if not matches:
        return None
    m = matches[-1]
    try:
        cant = float(m.group("cant").replace(",", "."))
    except ValueError:
        return None
    gramos = cant * _FACTOR_A_GRAMOS[m.group("uni").lower()]
    if m.group("mult"):
        gramos *= int(m.group("mult"))
    # Descarta absurdos: por debajo de 5 g o por encima de 25 kg no es un
    # formato de venta al consumidor, es un error de parseo.
    return gramos if 5 <= gramos <= 25_000 else None


def resolver_peso(titulo: str, grams: int | None) -> tuple[float | None, str]:
    """Devuelve (gramos, procedencia). Procedencia ∈ {shopify, titulo, desconocido}."""
    if grams and grams > 0:
        return float(grams), "shopify"
    p = peso_desde_titulo(titulo)
    if p is not None:
        return p, "titulo"
    return None, "desconocido"


# --------------------------------------------------------------------------- #
# Categoría
# --------------------------------------------------------------------------- #

def categoria_desde_titulo(titulo: str) -> str:
    t = _norm(titulo)
    for cat, claves in REGLAS_CATEGORIA:
        if any(k in t for k in claves):
            return cat
    return CATEGORIA_DEFECTO


# --------------------------------------------------------------------------- #

def enriquecer(conn: sqlite3.Connection, fecha: str | None = None) -> list[dict]:
    """Catálogo del último snapshot con peso y categoría resueltos."""
    if fecha is None:
        fecha = conn.execute("SELECT MAX(snapshot_date) FROM observations").fetchone()[0]
    rows = conn.execute("""
        SELECT product_id, variant_id, title, tags, price, compare_at_price, grams
        FROM observations WHERE snapshot_date = ?""", (fecha,)).fetchall()

    out = []
    for pid, vid, title, tags, price, cap, grams in rows:
        tag_list = [t for t in (tags or "").split("|") if t]
        if "rm_caja" in tag_list:        # la caja en sí no es un producto asignable
            continue
        gramos, origen = resolver_peso(title, grams)
        out.append({
            "product_id": pid, "variant_id": vid, "title": title,
            "tags": tag_list, "tag": tag_list[0] if tag_list else None,
            "price": price, "compare_at_price": cap,
            "gramos": gramos, "peso_origen": origen,
            "categoria": categoria_desde_titulo(title),
        })
    return out


def informe_cobertura(items: list[dict]) -> dict:
    n = len(items)
    origen = Counter(it["peso_origen"] for it in items)
    cats = Counter(it["categoria"] for it in items)
    sin_clasificar = cats.get(CATEGORIA_DEFECTO, 0)
    return {
        "n_referencias": n,
        "peso": {
            "de_shopify": origen.get("shopify", 0),
            "recuperado_del_titulo": origen.get("titulo", 0),
            "desconocido": origen.get("desconocido", 0),
            "pct_resuelto": round(100 * (n - origen.get("desconocido", 0)) / n, 1) if n else None,
            "pct_recuperado_del_titulo": round(100 * origen.get("titulo", 0) / n, 1) if n else None,
        },
        "categoria": {
            "n_categorias": len(cats),
            "sin_clasificar": sin_clasificar,
            "pct_clasificado": round(100 * (n - sin_clasificar) / n, 1) if n else None,
            "reparto": cats.most_common(),
        },
    }


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    items = enriquecer(conn)
    r = informe_cobertura(items)

    print(f"\n=== ENRIQUECIMIENTO DEL CATÁLOGO ({r['n_referencias']} referencias) ===\n")
    p = r["peso"]
    print("Peso:")
    print(f"  de `grams` de Shopify     : {p['de_shopify']:>4}")
    print(f"  recuperado del título     : {p['recuperado_del_titulo']:>4}  "
          f"({p['pct_recuperado_del_titulo']}% del catálogo)")
    print(f"  sin resolver              : {p['desconocido']:>4}")
    print(f"  -> cobertura total: {p['pct_resuelto']}%")

    c = r["categoria"]
    print(f"\nCategoría ({c['n_categorias']} categorías, {c['pct_clasificado']}% clasificado):")
    for cat, n in c["reparto"]:
        print(f"  {cat[:30]:<30} {n:>4}")

    if c["sin_clasificar"]:
        print(f"\nEjemplos sin clasificar (revisar reglas):")
        for it in [i for i in items if i["categoria"] == CATEGORIA_DEFECTO][:12]:
            print(f"  {it['title'][:60]}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
