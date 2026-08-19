# Rescue Planner — estudio de rotación de catálogo

Un supermercado convencional decide su catálogo y luego compra para sostenerlo.
Un supermercado de excedentes hace lo contrario: el catálogo es lo que **resulta**
de lo que aparece cada semana. Eso convierte el surtido en una variable de salida,
no en un punto de partida, y cambia por completo cuál es la decisión difícil del
negocio.

Este repositorio mide esa dinámica con datos públicos y extrae de ahí insights de
negocio: cómo se etiqueta y se precia el excedente, cómo se concentran los
proveedores, y a qué ritmo rota el surtido.

**Estado:** análisis de catálogo operativo (recolección + taxonomía, precios, rotación y proveedores).

---

## Qué hay aquí

| Módulo | Qué hace | Datos | Estado |
|---|---|---|---|
| **Taxonomía** | Cobertura del motivo de excedente, política de precios implícita, calidad de campos | **Reales**, endpoint público | ✅ |
| **Radar** | Snapshot diario; rotación, altas/bajas, descuento efectivo, supervivencia de referencias | **Reales**, endpoint público | ✅ recolectando |
| **Cohortes** | Antigüedad del surtido reconstruida desde `published_at` (con sus cautelas) | **Reales**, endpoint público | ✅ |
| **Proveedores** | Concentración, calidad del campo `vendor` y perfil por proveedor | **Reales**, endpoint público | ✅ |


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
HALLAZGOS.md                 bitácora de resultados, con sus cautelas
tests/test_pipeline.py       16 tests, sin red
data/raw/*.json.gz           JSON crudo diario — FUENTE DE VERDAD, versionado
data/catalog.sqlite          derivado reconstruible, NO versionado
```


## Limitaciones conocidas

- El endpoint público expone el catálogo visible, no el stock real ni las unidades
  disponibles. La rotación medida es de **surtido**, no de inventario.
- `available` en Shopify puede reflejar configuración de la tienda, no existencias.
- **Confirmado el 14-ago-2026:** la caja de fruta y verdura NO se publica como
  referencias individuales — son 2 variantes con tag `rm_caja`. Todo el análisis
  describe la Tienda Remolona (despensa), no la caja de fresco. Es la limitación
  más seria del proyecto.
- **Confirmado el 14-ago-2026:** `product_type` no es informativo (99,6 % del
  catálogo comparte valor) y `available` tampoco (99,6 % disponible). La
  taxonomía explotable está en `tags`. Ver `HALLAZGOS.md`.
