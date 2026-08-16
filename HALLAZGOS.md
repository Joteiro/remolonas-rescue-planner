# Hallazgos

Bitácora de lo que van diciendo los datos. Cada entrada indica qué se observó,
qué se puede concluir y qué **no** se puede concluir.

---

## 2026-08-14 · Día 1 (n = 1 snapshot, 514 referencias)

Un solo día no permite decir nada sobre rotación. Sí permite decir mucho sobre
**cómo está estructurada la información del catálogo**, que resultó ser el
hallazgo más sustancioso — y no requiere esperar.

### H1 · El campo `tags` funciona como categoría única, no como conjunto

514 de 515 variantes llevan **exactamente un tag**. Ninguna lleva dos.

En Shopify `tags` es una lista libre, pensada para acumular etiquetas. Aquí se
está usando como si fuera un desplegable de valor único. La consecuencia es
concreta: un bote de mermelada con fecha corta que además viene de un pequeño
productor local **tiene que elegir** entre `Fecha corta` y `Pequeños productores`.
Se registra una cosa y se pierde la otra.

**Se puede concluir:** el catálogo público no permite cruzar origen del excedente
con procedencia comercial, porque son mutuamente excluyentes en la práctica.

**No se puede concluir:** que internamente no tengan ese dato. Podrían usar
metafields, un ERP o una hoja aparte que no se ve desde fuera. Lo observable es
que no llega al catálogo.

### H2 · El 63,5 % del catálogo no declara por qué es excedente

| Grupo | Referencias | % |
|---|---:|---:|
| Con etiqueta de origen (`Excedente`, `Fecha corta`, `Rescate`, `Encargo cancelado`, `Exceso`, `Cambio de canal`, `Innovación`) | 188 | 36,5 % |
| Sólo etiqueta comercial (`Promoción producto`, `Productor local`, `Pequeños productores`) | 324 | 62,9 % |
| Sin ninguna etiqueta | 1 | 0,2 % |

`Promoción producto` sola se lleva 236 referencias — el 46 % del catálogo. Es una
etiqueta de merchandising: dice que el producto está en promoción, no por qué
existe la oportunidad de comprarlo.

Combinado con H1, el mecanismo queda claro: las etiquetas comerciales y las de
origen compiten por el mismo hueco, y las comerciales ganan casi 2 a 1.

### H3 · Existe una política de precios implícita, y es coherente

Descuento medio sobre PVP según la etiqueta de origen:

| Etiqueta | n | Descuento medio | Mediana |
|---|---:|---:|---:|
| Innovación | 4 | 62,5 % | 62,5 % |
| Cambio de canal | 2 | 43,3 % | 43,3 % |
| Encargo cancelado | 2 | 40,9 % | 40,9 % |
| Exceso | 2 | 26,8 % | 26,8 % |
| **Fecha corta** | **59** | **23,4 %** | **19,4 %** |
| Rescate | 5 | 23,1 % | 15,2 % |
| **Excedente** | **114** | **14,3 %** | **10,6 %** |
| Promoción producto | 236 | 13,4 % | 10,5 % |

El orden tiene sentido de negocio: cuanto más irrecuperable o urgente el motivo,
mayor el descuento. `Fecha corta` está 9 puntos por encima de `Excedente`, que es
exactamente lo que esperarías si la vida útil restante entra en el precio.

**Aquí está lo interesante.** Las categorías con más descuento son las que tienen
n = 2 y n = 4. Las dos que cubren el 68 % del catálogo son las de menor descuento.
Es decir: **la política de precios más fina se aplica a la parte más pequeña del
surtido**, y el grueso cae en cubos genéricos.

**No se puede concluir** que estén dejando margen sobre la mesa. Podría ser que
el 68 % restante realmente sea producto sin urgencia y el precio esté bien. Lo
único demostrado es que **desde el catálogo no hay forma de distinguirlo**, y que
ese es un límite a cualquier automatización de precios.

Cautela adicional: `n` entre 2 y 5 no sostiene ninguna afirmación. Estos números
son una hipótesis a comprobar con más días, no un resultado.

### H4 · Dispersión de descuento de 3,3x

n = 511 con `compare_at_price`.

```
 min  1,2 %   p25  9,7 %   mediana 12,2 %   p75 20,5 %   p90 32,0 %   max 63,7 %
 media 16,5 %                                    p90/p25 = 3,3x
```

La distribución está sesgada a la derecha: media 16,5 %, mediana 12,2 %. Sólo el
**11,5 %** de las referencias supera el 30 % de descuento.

**Importante para cualquier lectura externa:** esto no contradice su comunicación.
"Hasta un 55 %" describe correctamente la cola de la distribución (el máximo
observado es 63,7 %). Y `compare_at_price` suele ser PVP recomendado, no el precio
real de un supermercado, así que el ahorro percibido por el cliente frente a su
alternativa real puede ser distinto del que sale aquí. **No usar este dato como
si fuera una contradicción — no lo es.**

Lo que sí plantea una pregunta legítima: un factor 3,3x entre p25 y p90 es mucha
variación. ¿La gobierna la urgencia, o es histórica? H3 sugiere que en parte sí
la gobierna, en la parte del catálogo que está etiquetada.

### H5 · Dos campos de Shopify están vacíos de información

- **`product_type`**: 2 valores distintos; `UPSELLING` cubre el 99,6 %. No separa
  nada. *(Consecuencia práctica: hubo que reescribir la agregación por categoría
  de `radar.py`, que originalmente se apoyaba en este campo.)*
- **`available`**: 99,6 % disponible; sólo 2 referencias marcadas como agotadas,
  ambas de motivo urgente (`Fecha corta`, `Excedente`). Con n = 2 no hay señal
  aprovechable. Confirma que la rotación hay que medirla por **desaparición de la
  referencia entre snapshots**, no por este flag.

### H6 · La caja de fruta y verdura queda fuera de este análisis

Sólo 2 variantes tienen `product_type = "Frutas y verduras"`, con el tag `rm_caja`
y precio 16,90 €. Son la caja como producto, no su contenido.

**Por tanto: todo lo anterior describe la Tienda Remolona (despensa), no la caja
de fresco.** La caja es probablemente el corazón del negocio y es invisible desde
el catálogo público. Es la limitación más seria del proyecto y hay que decirla
en voz alta, no esconderla.

### H7 · El catálogo es mayor de lo que dice la prensa

514 referencias frente a las "300, con objetivo de llegar a 800" publicadas en
diciembre de 2025. Están más cerca del objetivo de lo que se ha contado.

### H8 · El peso falta en una de cada cuatro referencias, y se puede recuperar

`grams` viene a cero en **132 de 515 variantes (25,6 %)**, concentrado en
líquidos: zumos, aceites, caldos, leche. Sin peso no se puede componer una caja.

Pero el título casi siempre lleva el formato — "Zumo 100% Natural de Manzana
250 ml", "Aceite Minerva 1L", "Galletas TostaRica 4x42 g" — así que se recupera
parseándolo. Resultado: **cobertura del 96,9 %**, de la cual un 22,6 % procede
del título. Quedan 16 referencias sin resolver, casi todas vendidas por unidades
("Fini Pop 5 Unidades", "Picoteo Playero").

Mismo patrón que H5: el dato existe, pero no en el campo donde debería estar.

---

## 2026-08-15 · Día 2 (n = 2 snapshots)

### H9 · CORRECCIÓN — `vendor` sí es informativo, y me equivoqué

El día 1 di por bueno que `vendor` valía "REMOLONAS" en todo el catálogo. Lo
leí de un resumen de la primera página y no lo comprobé contra la base. **Es
falso.**

Hay **94 proveedores distintos**. Sólo 12 referencias llevan la marca propia.

| | |
|---|---:|
| Top 1 (HELIOS) | 9,4 % |
| Top 5 | 23,4 % |
| Top 10 | 35,9 % |
| Top 20 | 54,4 % |
| HHI | 244 |
| Proveedores con 1 sola referencia | 21 |
| Mediana de referencias por proveedor | 3,5 |

Un HHI de 244 es un surtido **muy atomizado** — ningún proveedor manda. Coherente
con un modelo que compra excedente donde aparece, no con acuerdos de suministro.
Los nombres confirman lo publicado en prensa: HELIOS, PEPSICO, GULLÓN, PASTAS
GALLO, BORGES, CARMENCITA.

**Calidad del campo:** 94 valores crudos → 93 tras normalizar. `DANTZA`/`Dantza`
son el mismo proveedor partido en dos. Y dos erratas probables: `MAYA ORGANIC`/
`MAYA ORGANICS` (similitud 0,96) y `MUELOLIVA`/`MUEOLIVA` (0,94). En total **24
referencias afectadas (4,7 %)** — poco volumen, suficiente para descuadrar
cualquier informe agregado por proveedor.

### H10 · `updated_at` se reescribe en bloque: no sirve como señal de cambio

Entre el 14 y el 15 de agosto, `updated_at` cambió en **las 515 variantes**, y
todas al mismo segundo exacto: `2026-08-15T08:55:51+02:00`.

Eso no es actividad de negocio. Es un proceso automático — sincronización desde
un ERP, o una app de Shopify — que toca el catálogo entero cada mañana.

**Consecuencia práctica:** cualquiera que intente responder "¿qué productos
cambiaron hoy?" usando `updated_at` obtendrá los 515, todos los días. Es el
tercer campo de Shopify que en este catálogo no significa lo que su nombre
sugiere, después de `product_type` y `available`.

Cambios reales entre los dos días: **uno**. Un `compare_at_price`. Cero altas,
cero bajas, cero cambios de precio.

### H11 · La rotación se puede reconstruir hacia atrás — y es alta

Monté `radar.py` asumiendo que había que esperar semanas para medir rotación.
No hacía falta: cada producto trae su `published_at`, así que el histórico de
**altas** sale de un solo snapshot.

Altas por semana, últimas 8 semanas: **media 36,9** (min 28, max 50).

| Antigüedad del catálogo actual | Referencias | % |
|---|---:|---:|
| ≤ 30 días | 181 | 35,2 % |
| ≤ 90 días | 433 | 84,2 % |
| ≤ 180 días | 491 | 95,5 % |
| ≤ 365 días | 513 | 99,8 % |

Mediana: **46 días** desde publicación (p25 22, p75 72).

Sobre un catálogo estable de ~514 referencias, 37 altas semanales son una **tasa
de renovación del 7,2 % semanal**: la mitad del surtido se renueva en unas
**7 semanas**.

**El control que hace falta.** ¿No será que el catálogo parece joven porque la
empresa lo es? La operación comercial arrancó a mediados de 2024, hace ~25 meses.
De 514 referencias, exactamente **1** tiene más de 12 meses. Si el surtido no
rotara, hoy habría producto de 2024 en el catálogo. No lo hay. **La juventud del
negocio no lo explica: es rotación.**

**El sesgo que hay que declarar siempre:** esto es un análisis de supervivientes.
Sólo se ven los productos que siguen publicados. De los que entraron en marzo y
salieron en mayo no queda rastro, así que la distribución está sesgada hacia lo
reciente por construcción. La conclusión sobre la tasa de altas es sólida; la
inferencia de que salen tantas como entran descansa en que el catálogo esté en
estado estacionario, y eso **sólo se confirma con más días de serie**.

Rotación por etiqueta (mediana de días publicado): `Rescate` 2 d · `Fecha corta`
30 d · `Excedente` 46 d · `Promoción producto` 50 d · `Productor local` 57 d ·
`Pequeños productores` 127 d. El orden vuelve a tener sentido: lo urgente entra
y sale rápido; lo de pequeños productores es surtido estable.

---

## Qué cambia esto en el plan

**El mensaje ya no depende de esperar tres semanas.** H1–H3 forman un argumento
completo con un solo día de datos: hay una lógica de precios sensata, aplicada
sobre una taxonomía que no puede sostenerla.

La serie temporal sigue en marcha y añadirá la dimensión de rotación, que es
complementaria. Pero deja de ser el bloqueante.

**Prioridad revisada de los módulos:**

1. Taxonomía y precio por motivo — *listo, con datos reales*
2. Rotación — *resuelta el día 2* vía `published_at`, sin esperar. La serie
   diaria sigue corriendo porque es la única forma de medir **bajas**, pero ya
   no bloquea nada.
3. Motor de asignación — *construido*. Y cierra el círculo con H2: en el escenario
   por defecto sólo el **84,7 % de los kg colocados** se prioriza con vida útil
   conocida. El resto se coloca con un valor por defecto, porque su motivo de
   excedente no está etiquetado. El motor puede priorizar por urgencia; el
   catálogo sólo se lo permite en parte.
