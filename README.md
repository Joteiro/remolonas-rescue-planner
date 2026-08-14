# Rescue Planner — estudio de rotación de catálogo

Un supermercado convencional decide su catálogo y luego compra para sostenerlo.
Un supermercado de excedentes hace lo contrario: el catálogo es lo que **resulta**
de lo que aparece cada semana. Eso convierte el surtido en una variable de salida,
no en un punto de partida, y cambia por completo cuál es la decisión difícil del
negocio.

Este repositorio mide esa rotación con datos públicos, y sobre esa base construye
un motor de asignación de lotes entrantes a cajas de suscripción.

**Estado:** M1 (recolección) operativo. M2 y M3 en construcción.

---

## Qué hay aquí

| Módulo | Qué hace | Datos | Estado |
|---|---|---|---|
| **M1 · Radar** | Snapshot diario del catálogo; rotación, altas/bajas, descuento efectivo, supervivencia de referencias | **Reales**, endpoint público | ✅ |
| **M2 · Asignación** | Reparte un lote entrante entre cajas respetando exclusiones, peso y vida útil | Estructura real + suscriptores sintéticos | 🚧 |
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
python radar.py                  # informe (necesita ≥2 días para altas/bajas)
```

Para la recolección diaria, activa el workflow de GitHub Actions
(`.github/workflows/snapshot.yml`): corre a las 06:15 UTC y commitea el resultado.
Alternativa local, si prefieres no depender de Actions:

```cron
15 8 * * *  cd /ruta/al/repo && /usr/bin/python3 snapshot.py >> data/cron.log 2>&1
```

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
snapshot.py                  recolector (M1)
radar.py                     métricas de rotación
tests/test_pipeline.py       6 tests, sin red
data/catalog.sqlite          serie normalizada
data/raw/*.json.gz           JSON crudo diario — nunca se borra
SUPUESTOS.md                 qué es real y qué está simulado
```

## Limitaciones conocidas

- El endpoint público expone el catálogo visible, no el stock real ni las unidades
  disponibles. La rotación medida es de **surtido**, no de inventario.
- `available` en Shopify puede reflejar configuración de la tienda, no existencias.
- No hay datos de pedidos, hogares ni incidencias: todo M3 es simulado.
- Los productos de la caja de fruta y verdura no están necesariamente publicados
  como referencias individuales, así que el radar cubre la Tienda Remolona mejor
  que la caja.
