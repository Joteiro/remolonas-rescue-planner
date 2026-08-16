#!/usr/bin/env python3
"""
M2 · Motor de asignación de lotes entrantes a cajas de suscripción.

EL PROBLEMA
    Entran lotes de excedente con vida útil corta y cantidad fija. Hay miles de
    hogares suscritos, cada uno con su tipo de caja, sus exclusiones y lo que ya
    recibió la semana pasada. Hay que decidir cuántas unidades de cada lote van
    a cada caja.

    La decisión no es obvia porque los objetivos se pelean: colocar todo lo que
    caduca antes, no repetir lo de la semana pasada, respetar exclusiones, dar
    variedad, y cuadrar el peso de la caja. Hecho a ojo, se prioriza lo urgente
    y se rompe la variedad, o al revés.

POR QUÉ NO SE RESUELVE HOGAR A HOGAR
    514 productos × 10.000 hogares = 5,1 millones de variables enteras. Ningún
    solver libre lo toca en tiempo razonable, y además sería resolver un problema
    que no existe: en la operativa real no se montan 10.000 cajas distintas, se
    montan unas pocas configuraciones y se replican.

    Así que agrupamos los hogares en PERFILES (misma caja + mismas exclusiones +
    mismo historial reciente) y decidimos la composición por perfil, con la
    multiplicidad como peso. El problema baja a ~decenas de lotes × ~decenas de
    perfiles: unos cientos de variables, que CBC resuelve en segundos.

    Esto no es un atajo: es la formulación que corresponde a cómo se opera.

LÍMITES MEDIDOS (14-ago-2026, CBC, 14 lotes, 10.000 hogares)
    60 perfiles  -> óptimo en segundos, cubre el 82 % de los hogares
    150 perfiles -> no cierra en 60 s
    Con 10.000 hogares salen ~1.100 combinaciones distintas de exclusiones, así
    que optimizar todas exactamente no es viable con un solver libre. Los
    perfiles que quedan fuera se cubren con `cubrir_cola()`: se les sirve la
    caja ya definida de más valor que respete sus exclusiones. Si esto fuera a
    producción, el siguiente paso es un solver comercial o una descomposición
    por columnas — no fingir que el problema es más pequeño de lo que es.

FORMULACIÓN (MILP)
    x[i,j] ∈ Z≥0   unidades del lote i en CADA caja del perfil j
    y[i,j] ∈ {0,1} indicador de que el lote i aparece en el perfil j

    max  Σ  m_j · x_ij · valor_i · urgencia_i  −  λ · Σ m_j · x_ij · repetido_ij
    s.a.
      (1) Σ_j m_j · x_ij ≤ disponible_i           no repartir más de lo que hay
      (2a) Σ_i peso_i · x_ij ≤ W_j + tol_j        capacidad de la caja, DURA
      (2b) Σ_i peso_i · x_ij + d⁻_j ≥ W_j − tol_j  suelo de peso, BLANDO
      (3) x_ij = 0  si el perfil j excluye i      exclusiones, duras
      (4) Σ_i y_ij ≥ variedad_min                 no llenar la caja con un producto
      (5) x_ij ≤ max_uds · y_ij                   enlaza x con su indicador
      (6) Σ_i y_ij · [cat_i = c] ≤ max_por_cat    tope por categoría

    urgencia_i crece al acercarse la caducidad: un lote con 2 días de vida vale
    más colocado hoy que uno con 10, aunque el precio sea el mismo.

DATOS
    Los lotes salen del catálogo REAL (título, precio, peso, categoría y motivo
    de excedente). Las cantidades, la vida útil y los hogares son SINTÉTICOS.
    Ver SUPUESTOS.md. Semilla fija: mismo resultado en cada ejecución.

    python asignacion.py                    # escenario por defecto
    python asignacion.py --hogares 10000 --lotes 15 --json
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import pulp

from productos import enriquecer

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "catalog.sqlite"

SEMILLA = 20260814

# --- Parámetros de caja (fuente: web pública de Remolonas, ago-2026) ---------
#
# CORRECCIÓN DE MODELADO (14-ago-2026). La primera versión trataba de llenar la
# caja entera (6 kg Mini / 10 kg Súper) con lotes de despensa, y salía infeasible.
# Con razón: 10.000 hogares × 6 kg son 60 t/semana, y la cifra pública que dan
# es de ~25 t/semana rescatadas en total. Además la caja es fruta y verdura —
# que ni siquiera está en el catálogo público (HALLAZGOS.md · H6).
#
# El problema real es otro: componer la PORCIÓN DE DESPENSA que acompaña a la
# caja. Dimensionada desde sus propias cifras: 25 t/semana ÷ 10.000 hogares
# ≈ 2,5 kg/hogar. Repartido según tamaño de caja:
CAJAS = {
    "Mini":  {"peso_objetivo_g": 1500, "tolerancia_g": 400, "precio": 11.90},
    "Súper": {"peso_objetivo_g": 3000, "tolerancia_g": 700, "precio": 21.90},
}

# --- Parámetros de la política de composición -------------------------------
VARIEDAD_MINIMA = 5          # referencias distintas por caja, como mínimo
MAX_UDS_POR_REFERENCIA = 4   # nadie quiere 9 botes del mismo paté
MAX_REFS_POR_CATEGORIA = 3   # ni una caja que sea sólo galletas
LAMBDA_REPETICION = 0.45     # peso de la penalización por repetir semana anterior

# Vida útil por motivo de excedente. SINTÉTICO, pero ordenado según la lógica que
# los propios datos de precio sugieren (ver HALLAZGOS.md, H3): los motivos con
# más descuento son los más urgentes.
VIDA_UTIL_POR_TAG = {
    "Fecha corta":       (2, 6),
    "Encargo cancelado": (4, 10),
    "Cambio de canal":   (5, 12),
    "Exceso":            (7, 20),
    "Rescate":           (7, 20),
    "Innovación":        (10, 30),
    "Excedente":         (10, 40),
}
VIDA_UTIL_DEFECTO = (15, 60)   # lo que se asume cuando el motivo no está etiquetado


# =========================================================================== #
# Generación del escenario (sintético, con semilla)
# =========================================================================== #

def generar_lotes(catalogo: list[dict], n_lotes: int, rng: random.Random) -> list[dict]:
    """Toma n productos reales del catálogo y les asigna cantidad y vida útil.

    Sesga la selección hacia productos con motivo de excedente etiquetado,
    porque son los que en la práctica llegarían como lote identificable.
    """
    aptos = [p for p in catalogo if p["gramos"] and p["price"]]
    con_tag = [p for p in aptos if p["tag"] in VIDA_UTIL_POR_TAG]
    sin_tag = [p for p in aptos if p["tag"] not in VIDA_UTIL_POR_TAG]

    n_con = min(len(con_tag), int(round(n_lotes * 0.6)))
    elegidos = rng.sample(con_tag, n_con) + rng.sample(sin_tag, min(len(sin_tag), n_lotes - n_con))

    lotes = []
    for p in elegidos:
        lo, hi = VIDA_UTIL_POR_TAG.get(p["tag"], VIDA_UTIL_DEFECTO)
        vida = rng.randint(lo, hi)
        # Cantidad: entre 200 y 4.000 unidades. Los lotes de fecha corta suelen
        # ser mayores (es producto que alguien necesita quitarse de encima).
        base = rng.randint(400, 2500)
        if p["tag"] == "Fecha corta":
            base = int(base * rng.uniform(1.2, 2.0))
        lotes.append({
            **p,
            "vida_util_dias": vida,
            "vida_util_conocida": p["tag"] in VIDA_UTIL_POR_TAG,
            "unidades": base,
        })
    return lotes


def calibrar_lotes(lotes: list[dict], demanda_g: float, holgura: float) -> list[dict]:
    """Escala las cantidades para que la oferta total sea `holgura` × la demanda.

    Sin esto, el tamaño de los lotes sería un número inventado y el resultado
    diría más de esa invención que del algoritmo. Con holgura 1,2 hay un 20 %
    más de producto del que cabe en las cajas: suficiente para que la decisión
    de QUÉ colocar sea real, que es justamente lo que se quiere medir.
    """
    oferta_g = sum(l["unidades"] * l["gramos"] for l in lotes)
    if oferta_g <= 0:
        return lotes
    factor = (holgura * demanda_g) / oferta_g
    for l in lotes:
        l["unidades"] = max(1, int(round(l["unidades"] * factor)))
    return lotes


def generar_hogares(n: int, categorias: list[str], rng: random.Random) -> list[dict]:
    """Hogares sintéticos con tipo de caja, exclusiones e historial."""
    hogares = []
    for _ in range(n):
        tipo = "Mini" if rng.random() < 0.60 else "Súper"
        # 35 % declara exclusiones; media 2,1 categorías (ver SUPUESTOS.md)
        excl: set[str] = set()
        if rng.random() < 0.35:
            k = min(len(categorias), max(1, int(rng.gauss(2.1, 1.0))))
            excl = set(rng.sample(categorias, k))
        hogares.append({"tipo": tipo, "exclusiones": frozenset(excl)})
    return hogares


def agrupar_en_perfiles(hogares: list[dict], historial: dict) -> list[dict]:
    """Colapsa hogares idénticos en perfiles con multiplicidad.

    Aquí es donde el problema pasa de intratable a trivial. Dos hogares con el
    mismo tipo de caja y las mismas exclusiones reciben la misma caja, así que
    no hace falta decidir dos veces.
    """
    grupos: dict[tuple, int] = Counter()
    for h in hogares:
        grupos[(h["tipo"], h["exclusiones"])] += 1

    perfiles = []
    for (tipo, excl), n in grupos.most_common():
        perfiles.append({
            "tipo": tipo,
            "exclusiones": set(excl),
            "hogares": n,
            "recibido_semana_pasada": historial.get((tipo, excl), set()),
        })
    return perfiles


def generar_historial(perfiles_clave: list[tuple], catalogo: list[dict],
                      rng: random.Random) -> dict:
    """Qué recibió cada perfil la semana pasada (para penalizar repetición)."""
    ids = [p["product_id"] for p in catalogo]
    return {clave: set(rng.sample(ids, min(len(ids), 8))) for clave in perfiles_clave}


# =========================================================================== #
# Optimización
# =========================================================================== #

def urgencia(vida_dias: int, horizonte: int = 30) -> float:
    """Cuánto vale colocar hoy un lote según su vida útil restante.

    Lineal decreciente, acotada en [1, 3]. Un lote a 2 días vale ~2,9x lo que
    vale uno a 30. Se eligió lineal a propósito: una exponencial daría números
    más vistosos pero no hay ningún dato que justifique esa forma.
    """
    v = max(0, min(vida_dias, horizonte))
    return 1.0 + 2.0 * (horizonte - v) / horizonte


def resolver(lotes: list[dict], perfiles: list[dict],
             lambda_rep: float = LAMBDA_REPETICION,
             variedad_min: int = VARIEDAD_MINIMA,
             tiempo_max: int = 60) -> dict:
    """Resuelve el MILP. Devuelve la asignación y el estado del solver."""
    prob = pulp.LpProblem("asignacion_excedente", pulp.LpMaximize)

    L, P = range(len(lotes)), range(len(perfiles))
    compat = {(i, j): (lotes[i]["categoria"] not in perfiles[j]["exclusiones"])
              for i in L for j in P}

    x = {(i, j): pulp.LpVariable(f"x_{i}_{j}", 0, MAX_UDS_POR_REFERENCIA, cat="Integer")
         for i in L for j in P if compat[(i, j)]}
    y = {(i, j): pulp.LpVariable(f"y_{i}_{j}", cat="Binary")
         for i in L for j in P if compat[(i, j)]}
    # Déficit de peso por perfil, en gramos. Sólo por defecto: el exceso NO es
    # una desviación penalizable, es una imposibilidad física — en la caja no
    # cabe más. Esa asimetría es el modelo correcto y además evita el fallo de
    # la versión anterior, donde al solver le salía a cuenta pagar la
    # penalización y sobrellenar (colocaba 25 t donde caben 17).
    dneg = {j: pulp.LpVariable(f"dneg_{j}", 0) for j in P}

    # --- Objetivo ---
    terminos = []
    for (i, j), var in x.items():
        m = perfiles[j]["hogares"]
        valor = lotes[i]["price"] * urgencia(lotes[i]["vida_util_dias"])
        repetido = lotes[i]["product_id"] in perfiles[j]["recibido_semana_pasada"]
        coef = valor - (lambda_rep * lotes[i]["price"] if repetido else 0.0)
        terminos.append(m * coef * var)

    # Coste de servir una caja corta de peso: 4 €/kg y hogar. Por debajo de la
    # densidad de valor del producto (~11 €/kg), así que no domina la decisión;
    # suficiente para que el solver prefiera llenar antes que dejar hueco.
    penal_peso = pulp.lpSum(
        perfiles[j]["hogares"] * 4.0 * dneg[j] / 1000.0 for j in P)
    prob += pulp.lpSum(terminos) - penal_peso

    # (1) No repartir más unidades de las disponibles
    for i in L:
        vars_i = [(perfiles[j]["hogares"] * x[(i, j)]) for j in P if (i, j) in x]
        if vars_i:
            prob += pulp.lpSum(vars_i) <= lotes[i]["unidades"], f"stock_{i}"

    for j in P:
        vars_j = [(i, x[(i, j)]) for i in L if (i, j) in x]
        if not vars_j:
            continue
        cfg = CAJAS[perfiles[j]["tipo"]]

        # (2) Peso: techo duro (capacidad de la caja), suelo blando (dneg).
        peso = pulp.lpSum(lotes[i]["gramos"] * v for i, v in vars_j)
        prob += peso <= cfg["peso_objetivo_g"] + cfg["tolerancia_g"], f"capacidad_{j}"
        prob += peso + dneg[j] >= cfg["peso_objetivo_g"] - cfg["tolerancia_g"], f"suelo_{j}"

        # (4) Variedad mínima, acotada por lo que el perfil puede recibir:
        #     exigir 5 referencias a un perfil con 4 lotes compatibles es
        #     declarar el problema infeasible por un error de diseño.
        v_min = min(variedad_min, len(vars_j))
        prob += pulp.lpSum(y[(i, j)] for i, _ in vars_j) >= v_min, f"variedad_{j}"

        # (5) Enlace x-y
        for i, v in vars_j:
            prob += v <= MAX_UDS_POR_REFERENCIA * y[(i, j)], f"link_{i}_{j}"
            prob += v >= y[(i, j)], f"link_min_{i}_{j}"

        # (6) Tope de referencias por categoría
        por_cat: dict[str, list] = defaultdict(list)
        for i, _ in vars_j:
            por_cat[lotes[i]["categoria"]].append(y[(i, j)])
        for cat, ys in por_cat.items():
            if len(ys) > MAX_REFS_POR_CATEGORIA:
                prob += pulp.lpSum(ys) <= MAX_REFS_POR_CATEGORIA, f"cat_{j}_{abs(hash(cat)) % 99999}"

    estado = prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=tiempo_max))
    nombre_estado = pulp.LpStatus[estado]

    # Si el solver no encontró una solución óptima, los valores de las variables
    # no significan nada — pueden violar cualquier restricción. Devolver una
    # asignación vacía en vez de dejar que un llamador los use por descuido.
    # (Este fallo existió: `main` lo comprobaba, pero nadie más.)
    if nombre_estado != "Optimal":
        return {
            "estado": nombre_estado, "objetivo": None, "asignacion": {},
            "n_variables": len(x) + len(y), "n_restricciones": len(prob.constraints),
        }

    asignacion = {(i, j): int(round(v.value() or 0)) for (i, j), v in x.items()}
    return {
        "estado": nombre_estado,
        "objetivo": pulp.value(prob.objective),
        "asignacion": asignacion,
        "n_variables": len(x) + len(y),
        "n_restricciones": len(prob.constraints),
    }


def cubrir_cola(asignacion: dict, lotes: list[dict], perfiles_opt: list[dict],
                perfiles_cola: list[dict]) -> dict:
    """Qué hacer con los hogares cuyo perfil no entró en la optimización.

    Con 10.000 hogares salen ~1.100 combinaciones distintas de exclusiones, y
    resolver todas exactamente no cabe en un tiempo razonable (ver LÍMITES en el
    encabezado). La salida operativa es la misma que aplicaría un almacén: a un
    hogar de la cola se le sirve una de las cajas YA definidas, la de más valor
    que no contenga ninguna de sus categorías excluidas.

    Esto NO reoptimiza ni recomprueba stock: es una comprobación de cobertura.
    Dice cuántos hogares de la cola pueden ser servidos con una caja existente y
    cuántos necesitarían una composición propia.
    """
    cajas = {}
    for j, perfil in enumerate(perfiles_opt):
        cats, valor = set(), 0.0
        for (i, jj), uds in asignacion.items():
            if jj == j and uds > 0:
                cats.add(lotes[i]["categoria"])
                valor += uds * lotes[i]["price"]
        if cats:
            cajas[j] = {"categorias": cats, "valor": valor,
                        "tipo": perfiles_opt[j]["tipo"]}

    cubiertos = sin_caja = 0
    for perfil in perfiles_cola:
        candidatas = [c for c in cajas.values()
                      if c["tipo"] == perfil["tipo"]
                      and not (c["categorias"] & perfil["exclusiones"])]
        if candidatas:
            cubiertos += perfil["hogares"]
        else:
            sin_caja += perfil["hogares"]

    total = cubiertos + sin_caja
    return {
        "hogares_en_cola": total,
        "cubiertos_con_caja_existente": cubiertos,
        "necesitan_composicion_propia": sin_caja,
        "pct_cubiertos": round(100 * cubiertos / total, 1) if total else None,
    }


# =========================================================================== #
# Referencia de comparación
# =========================================================================== #

def heuristica_proporcional(lotes: list[dict], perfiles: list[dict]) -> dict:
    """Lo que haría una hoja de cálculo: repartir proporcionalmente, respetando
    exclusiones, priorizando por vida útil, hasta llenar la caja.

    Existe para que el MILP sea falsable. Un optimizador sin nada con lo que
    compararse es una afirmación, no un resultado.
    """
    asignacion: dict[tuple[int, int], int] = {}
    restante = {i: lotes[i]["unidades"] for i in range(len(lotes))}
    orden = sorted(range(len(lotes)), key=lambda i: lotes[i]["vida_util_dias"])

    for j, perfil in enumerate(perfiles):
        cfg = CAJAS[perfil["tipo"]]
        peso = 0.0
        for i in orden:
            if lotes[i]["categoria"] in perfil["exclusiones"]:
                continue
            if restante[i] < perfil["hogares"]:
                continue
            if peso + lotes[i]["gramos"] > cfg["peso_objetivo_g"] + cfg["tolerancia_g"]:
                continue
            uds = 1
            while (uds < MAX_UDS_POR_REFERENCIA
                   and restante[i] >= perfil["hogares"] * (uds + 1)
                   and peso + lotes[i]["gramos"] * (uds + 1)
                       <= cfg["peso_objetivo_g"] + cfg["tolerancia_g"]):
                uds += 1
            asignacion[(i, j)] = uds
            restante[i] -= perfil["hogares"] * uds
            peso += lotes[i]["gramos"] * uds
            if peso >= cfg["peso_objetivo_g"] - cfg["tolerancia_g"]:
                break
    return {"asignacion": asignacion, "estado": "Heuristic"}


# =========================================================================== #
# Métricas
# =========================================================================== #

def evaluar(asignacion: dict, lotes: list[dict], perfiles: list[dict]) -> dict:
    colocado_uds = Counter()
    valor_total = 0.0
    valor_urgente = 0.0
    repeticiones = 0
    kg_con_vida_conocida = 0.0
    kg_total = 0.0
    refs_por_perfil: dict[int, int] = Counter()
    peso_por_perfil: dict[int, float] = Counter()

    for (i, j), uds in asignacion.items():
        if uds <= 0:
            continue
        m = perfiles[j]["hogares"]
        tot = uds * m
        colocado_uds[i] += tot
        valor_total += tot * lotes[i]["price"]
        kg = tot * lotes[i]["gramos"] / 1000.0
        kg_total += kg
        if lotes[i]["vida_util_conocida"]:
            kg_con_vida_conocida += kg
        if lotes[i]["vida_util_dias"] <= 7:
            valor_urgente += tot * lotes[i]["price"]
        if lotes[i]["product_id"] in perfiles[j]["recibido_semana_pasada"]:
            repeticiones += tot
        refs_por_perfil[j] += 1
        peso_por_perfil[j] += uds * lotes[i]["gramos"]

    disponible_uds = sum(l["unidades"] for l in lotes)
    valor_disponible = sum(l["unidades"] * l["price"] for l in lotes)

    urgentes = [i for i, l in enumerate(lotes) if l["vida_util_dias"] <= 7]
    uds_urgentes = sum(lotes[i]["unidades"] for i in urgentes)
    colocado_urgentes = sum(colocado_uds[i] for i in urgentes)

    hogares_tot = sum(p["hogares"] for p in perfiles)
    hogares_servidos = sum(perfiles[j]["hogares"] for j in refs_por_perfil)

    # Cajas fuera de tolerancia de peso
    fuera = 0
    for j, peso in peso_por_perfil.items():
        cfg = CAJAS[perfiles[j]["tipo"]]
        if abs(peso - cfg["peso_objetivo_g"]) > cfg["tolerancia_g"]:
            fuera += perfiles[j]["hogares"]

    return {
        "unidades_colocadas": sum(colocado_uds.values()),
        "pct_unidades_colocadas": round(100 * sum(colocado_uds.values()) / disponible_uds, 1),
        "valor_colocado_eur": round(valor_total, 2),
        "pct_valor_colocado": round(100 * valor_total / valor_disponible, 1),
        "fill_rate_urgentes_pct": round(100 * colocado_urgentes / uds_urgentes, 1) if uds_urgentes else None,
        "repeticiones_semana_anterior": repeticiones,
        "pct_repeticion": round(100 * repeticiones / sum(colocado_uds.values()), 1) if colocado_uds else None,
        "variedad_media_por_caja": round(statistics.mean(
            [refs_por_perfil[j] for j in refs_por_perfil]), 1) if refs_por_perfil else 0,
        "variedad_minima_observada": min(refs_por_perfil.values()) if refs_por_perfil else 0,
        "hogares_servidos": hogares_servidos,
        "pct_hogares_servidos": round(100 * hogares_servidos / hogares_tot, 1),
        "hogares_con_caja_fuera_de_peso": fuera,
        "kg_colocados": round(kg_total, 1),
        "pct_kg_priorizados_con_vida_conocida": round(
            100 * kg_con_vida_conocida / kg_total, 1) if kg_total else None,
    }


# =========================================================================== #

def construir_escenario(n_hogares: int, n_lotes: int,
                        holgura: float = 1.2,
                        semilla: int = SEMILLA) -> tuple[list, list, list]:
    rng = random.Random(semilla)
    conn = sqlite3.connect(DB_PATH)
    catalogo = [p for p in enriquecer(conn) if p["gramos"] and p["price"]]
    categorias = sorted({p["categoria"] for p in catalogo})

    lotes = generar_lotes(catalogo, n_lotes, rng)
    hogares = generar_hogares(n_hogares, categorias, rng)

    claves = list({(h["tipo"], h["exclusiones"]) for h in hogares})
    historial = generar_historial(claves, catalogo, rng)
    perfiles = agrupar_en_perfiles(hogares, historial)

    demanda_g = sum(CAJAS[h["tipo"]]["peso_objetivo_g"] for h in hogares)
    lotes = calibrar_lotes(lotes, demanda_g, holgura)
    return lotes, perfiles, catalogo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hogares", type=int, default=10_000)
    ap.add_argument("--lotes", type=int, default=14)
    ap.add_argument("--holgura", type=float, default=1.2,
                    help="oferta total / demanda total (1.2 = 20%% más producto del que cabe)")
    ap.add_argument("--max-perfiles", type=int, default=60,
                    help="se resuelven los N perfiles con más hogares")
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    ap.add_argument("--tiempo", type=int, default=120, help="límite del solver, segundos")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    lotes, perfiles, _ = construir_escenario(args.hogares, args.lotes,
                                            args.holgura, args.semilla)
    perfiles_res = perfiles[:args.max_perfiles]
    cobertura = sum(p["hogares"] for p in perfiles_res) / sum(p["hogares"] for p in perfiles)

    print(f"\n=== MOTOR DE ASIGNACIÓN ===")
    print(f"{len(lotes)} lotes · {args.hogares:,} hogares · {len(perfiles)} perfiles distintos")
    print(f"Se optimizan los {len(perfiles_res)} perfiles mayores "
          f"({cobertura:.1%} de los hogares)")
    if cobertura < 0.999:
        print(f"  AVISO: {sum(p['hogares'] for p in perfiles[args.max_perfiles:]):,} hogares "
              f"quedan fuera de esta corrida (cola de perfiles poco frecuentes).")

    r_milp = resolver(lotes, perfiles_res, tiempo_max=args.tiempo)
    if r_milp["estado"] != "Optimal":
        print(f"\nEl solver terminó en estado '{r_milp['estado']}'. "
              f"No se informan métricas: los valores de las variables de una "
              f"solución no óptima no significan nada.")
        return 1
    r_heur = heuristica_proporcional(lotes, perfiles_res)

    m_milp = evaluar(r_milp["asignacion"], lotes, perfiles_res)
    m_heur = evaluar(r_heur["asignacion"], lotes, perfiles_res)

    print(f"\nSolver: {r_milp['estado']}  "
          f"({r_milp['n_variables']} variables, {r_milp['n_restricciones']} restricciones)")

    filas = [
        ("Valor colocado (€)",              "valor_colocado_eur",            "{:,.0f}"),
        ("  % del valor disponible",        "pct_valor_colocado",            "{:.1f}%"),
        ("Fill rate de lotes urgentes",     "fill_rate_urgentes_pct",        "{:.1f}%"),
        ("Kg colocados",                    "kg_colocados",                  "{:,.0f}"),
        ("Repetición vs semana anterior",   "pct_repeticion",                "{:.1f}%"),
        ("Variedad media por caja",         "variedad_media_por_caja",       "{:.1f}"),
        ("Variedad mínima observada",       "variedad_minima_observada",     "{:.0f}"),
        ("Hogares servidos",                "pct_hogares_servidos",          "{:.1f}%"),
        ("Hogares con caja fuera de peso",  "hogares_con_caja_fuera_de_peso","{:,.0f}"),
    ]
    print(f"\n{'':<34}{'Heurística':>14}{'MILP':>14}{'Δ':>12}")
    print("-" * 74)
    for etiqueta, clave, fmt in filas:
        a, b = m_heur.get(clave), m_milp.get(clave)
        if a is None or b is None:
            continue
        delta = b - a
        signo = "+" if delta > 0 else ""
        print(f"{etiqueta:<34}{fmt.format(a):>14}{fmt.format(b):>14}"
              f"{signo + fmt.format(delta):>12}")

    if len(perfiles) > len(perfiles_res):
        cola = cubrir_cola(r_milp["asignacion"], lotes, perfiles_res,
                           perfiles[args.max_perfiles:])
        print(f"\nCola de perfiles no optimizados ({cola['hogares_en_cola']:,} hogares):")
        print(f"  servibles con una caja ya definida : {cola['cubiertos_con_caja_existente']:,}"
              f"  ({cola['pct_cubiertos']}%)")
        print(f"  necesitan composición propia       : {cola['necesitan_composicion_propia']:,}")

    print(f"\nCalidad del dato de entrada:")
    print(f"  Kg priorizados con vida útil conocida: "
          f"{m_milp['pct_kg_priorizados_con_vida_conocida']}%")
    print(f"  El resto se priorizó con el valor por defecto ({VIDA_UTIL_DEFECTO[0]}-"
          f"{VIDA_UTIL_DEFECTO[1]} días), porque su motivo de excedente no está")
    print(f"  etiquetado en el catálogo. Ver HALLAZGOS.md · H2.\n")

    if args.out or args.json:
        salida = {
            "escenario": {"hogares": args.hogares, "lotes": len(lotes),
                          "perfiles_totales": len(perfiles),
                          "perfiles_optimizados": len(perfiles_res),
                          "cobertura_hogares": round(cobertura, 4)},
            "solver": {k: r_milp[k] for k in ("estado", "n_variables", "n_restricciones")},
            "milp": m_milp, "heuristica": m_heur,
            "lotes": [{k: l[k] for k in ("title", "categoria", "tag", "price",
                                         "gramos", "vida_util_dias",
                                         "vida_util_conocida", "unidades")}
                      for l in lotes],
        }
        if args.out:
            args.out.write_text(json.dumps(salida, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            print(f"escrito {args.out}")
        if args.json:
            print(json.dumps(salida, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
