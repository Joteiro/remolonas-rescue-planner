# Rescue Planner — estudio de rotación de catálogo

Un supermercado convencional decide su catálogo y luego compra para sostenerlo.
Un supermercado de excedentes hace lo contrario: el catálogo es lo que **resulta**
de lo que aparece cada semana. Eso convierte el surtido en una variable de salida,
no en un punto de partida, y cambia por completo cuál es la decisión difícil del
negocio.

Este repositorio mide esa rotación con datos públicos, y sobre esa base construye
un motor de asignación de lotes entrantes a cajas de suscripción.

**Estado:** M1 y M2 operativos. M3 en construcción.

---

## Qué hay aquí

| Módulo | Qué hace | Datos | Estado |
|---|---|---|---|
| **M1a · Taxonomía** | Análisis transversal: cobertura del motivo de excedente, política de precios implícita, calidad de campos | **Reales**, endpoint público | ✅ |
| **M1b · Radar** | Snapshot diario; rotación, altas/bajas, descuento efectivo, supervivencia de referencias | **Reales**, endpoint público | ✅ recolectando |
| **M2 · Asignación** | MILP que reparte lotes entrantes entre cajas respetando exclusiones, capacidad, variedad y vida útil | Lotes reales + hogares sintéticos | ✅ |
| **M3 · Riesgo de baja** | Probabilidad de cancelación a 4 entregas y euros de LTV en riesgo | **Sintéticos** | 🚧 |

Lo que es sintético está marcado como sintético, aquí y en la interfaz. Ver
[`SUPUESTOS.md`](SUPUESTOS.md).

## Origen de los datos

`https://www.remolonas.com/products.json` — endpoint público y estándar de
Shopify, el mismo que sirve al buscador de la propia tienda. Una petición al día,
`robots.txt` comprobado antes de cada ejecución, User-Agent identificable con
contacto. No se republica el catálogo íntegro: el repositorio publica métricas
agregadas y la serie temporal necesaria para reproducirlas.

## Arranque

```bash
git clone <este-repo> && cd remolonas-rescue-planner
pip install -r requirements.txt

# edita USER_AGENT en snapshot.py y pon tu email real
python snapshot.py --dry-run     # comprueba que responde
python snapshot.py               # primer snapshot
python taxonomia.py              # funciona con UN solo día
python radar.py                  # necesita ≥2 días para altas/bajas
python cohortes.py               # rotación reconstruida desde published_at
python proveedores.py            # concentración y calidad del campo vendor
python productos.py              # cobertura de peso y categoría
python asignacion.py             # motor de asignación vs heurística
python sensibilidad.py           # ¿la ventaja aguanta otros escenarios?
```

Para la recolección diaria, activa el workflow de GitHub Actions
(`.github/workflows/snapshot.yml`): corre a las 06:15 UTC y commitea el resultado.
Alternativa local, si prefieres no depender de Actions:

```cron
15 8 * * *  cd /ruta/al/repo && /usr/bin/python3 snapshot.py >> data/cron.log 2>&1
```

## Por qué la base de datos no está en git

`data/catalog.sqlite` es un binario. Si lo versionas y lo escriben dos sitios —
tu portátil y el runner de Actions — antes o después hay un conflicto que git no
sabe resolver y hay que tirar una de las dos versiones.

Los `.json.gz` no tienen ese problema: uno por día, escritos una vez, nunca
tocados de nuevo. Así que **la fuente de verdad son los crudos** y la base es un
derivado: `python rebuild.py` la regenera, `python rebuild.py --check` verifica
que coincide. Misma idea que versionar un lockfile en vez de `node_modules`.

## Nota metodológica: censura

La métrica que más apetece citar —"una referencia dura X días en catálogo"— es la
más fácil de calcular mal. Al principio de la serie casi todas las referencias
están **censuradas por la derecha**: siguen vivas cuando dejas de mirar, así que
su duración observada es una cota inferior, no su duración real. Y las que ya
estaban el primer día están censuradas por la izquierda: nunca viste su alta.

Promediar todo junto da un número más bajo que la realidad y con una seguridad
que no existe. `radar.py` separa explícitamente ambos grupos, y a partir de 14
días calcula **Kaplan-Meier**, que sí usa correctamente la información de las
observaciones censuradas.

Consecuencia práctica: **no cites la vida media hasta tener tres semanas de serie.**

## Estructura

```
snapshot.py                  recolector
taxonomia.py                 análisis transversal (1 snapshot basta)
radar.py                     métricas de rotación (serie temporal)
cohortes.py                  rotación hacia atrás desde published_at
proveedores.py               concentración, calidad y perfil por proveedor
productos.py                 peso y categoría inferidos, con cobertura medida
rebuild.py                   reconstruye la base desde data/raw/
asignacion.py                M2 · MILP de asignación + heurística de contraste
sensibilidad.py              barrido de escenarios para falsar el MILP
HALLAZGOS.md                 bitácora de resultados, con sus cautelas
tests/test_pipeline.py       21 tests, sin red
data/raw/*.json.gz           JSON crudo diario — FUENTE DE VERDAD, versionado
data/catalog.sqlite          derivado reconstruible, NO versionado
SUPUESTOS.md                 qué es real y qué está simulado
```

## Sobre el motor de asignación

El MILP se compara siempre contra una heurística codiciosa que hace lo que haría
una hoja de cálculo. Un optimizador sin nada con lo que compararse es una
afirmación, no un resultado. En el barrido de `sensibilidad.py` (6 escenarios,
distintas semillas y niveles de holgura) el MILP gana en los 6, con una mediana
de **+51 puntos** de valor colocado y sin coste en fill rate de urgentes.

Límite conocido: con ~1.100 perfiles distintos de exclusiones, CBC resuelve 60
perfiles al óptimo en segundos pero no cierra 150 en 60 s. Los perfiles que
quedan fuera se cubren con `cubrir_cola()`, que les sirve una caja ya definida
compatible con sus exclusiones (~96 % de esos hogares). En producción tocaría
solver comercial o generación de columnas.

## Limitaciones conocidas

- El endpoint público expone el catálogo visible, no el stock real ni las unidades
  disponibles. La rotación medida es de **surtido**, no de inventario.
- `available` en Shopify puede reflejar configuración de la tienda, no existencias.
- No hay datos de pedidos, hogares ni incidencias: todo M3 es simulado.
- **Confirmado el 14-ago-2026:** la caja de fruta y verdura NO se publica como
  referencias individuales — son 2 variantes con tag `rm_caja`. Todo el análisis
  describe la Tienda Remolona (despensa), no la caja de fresco. Es la limitación
  más seria del proyecto.
- **Confirmado el 14-ago-2026:** `product_type` no es informativo (99,6 % del
  catálogo comparte valor) y `available` tampoco (99,6 % disponible). La
  taxonomía explotable está en `tags`. Ver `HALLAZGOS.md`.
