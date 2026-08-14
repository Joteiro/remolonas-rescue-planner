# Supuestos: qué es real y qué está simulado

Este fichero existe para que nadie tenga que adivinarlo. Si un número de este
proyecto sale de una simulación, está aquí.

---

## Datos reales

**Origen único:** `https://www.remolonas.com/products.json`, endpoint público
estándar de Shopify.

Campos observados: `id`, `handle`, `title`, `product_type`, `vendor`, `tags`,
`published_at`, `created_at`, `updated_at`, y por variante `id`, `sku`, `price`,
`compare_at_price`, `available`, `grams`, `position`.

Todo lo que aparece en el módulo M1 (Radar) sale de aquí. Nada más.

### Qué NO contienen estos datos

- Unidades en stock. `available` es un booleano de publicación, no una cantidad.
- Ventas, pedidos, ingresos.
- Suscriptores, cajas enviadas, composición real de una caja.
- Incidencias, devoluciones, reembolsos.
- Costes de compra, márgenes.
- Fechas de caducidad o vida útil restante.

Por tanto: **cualquier afirmación sobre demanda, margen o clientes en este
proyecto es simulada.** Sin excepción.

---

## Datos simulados

### M2 · Motor de asignación

La estructura del lote entrante y del catálogo es real. Lo simulado:

| Elemento | Supuesto | Justificación |
|---|---|---|
| Nº de hogares | 10.000 | Cifra pública declarada por la empresa (abril 2026) |
| Reparto Mini / Súper | 60 / 40 | **Inventado.** Sin base pública |
| Frecuencia semanal vs quincenal | 70 / 30 | **Inventado** |
| Hogares con exclusiones activas | 35 % | **Inventado.** La función existe según su FAQ; su uso, no se sabe |
| Nº medio de exclusiones por hogar | 2,1 (Poisson) | **Inventado** |
| Peso objetivo por caja | 6-7 kg Mini, 11 kg Súper | Cifras públicas de su web |
| Vida útil del lote entrante | 3-10 días, uniforme | **Inventado.** Rango plausible para fresco rescatado |

### M3 · Riesgo de baja

**Íntegramente simulado.** No hay ni un dato real de cliente en este módulo.

El generador construye historiales de suscripción con un modelo causal explícito
donde la probabilidad de baja aumenta con: incidencias acumuladas, entregas fuera
de franja, pausas previas, cobros fallidos y repetición de producto en semanas
consecutivas; y disminuye con la antigüedad.

**Consecuencia importante y honesta:** un modelo entrenado sobre estos datos
recupera la estructura que yo mismo metí. El AUC resultante **no es evidencia de
nada** sobre el negocio real. Lo que sí demuestra el módulo es la mecánica: qué
features construir, cómo traducir probabilidad a euros de LTV en riesgo, y qué
aspecto tendría el panel de acción. Con datos reales, los coeficientes cambiarían
y probablemente aparecerían drivers que no anticipé.

### Supuestos económicos

| Parámetro | Valor | Base |
|---|---|---|
| Ticket medio semanal | 11,90 € / 21,90 € | Precios públicos de su web |
| Vida media de suscripción | 9 meses | **Inventado.** Sin referencia pública |
| LTV bruto Mini | ~460 € | Derivado de los dos anteriores |
| Margen bruto | No modelado | No hay ninguna base pública para estimarlo |

No se calcula ni se afirma ningún ahorro en euros para la empresa. No hay datos
para hacerlo, y una cifra inventada invalidaría todo lo demás.

---

## Sesgos y límites del análisis

1. **Censura.** Ver README. La vida media de una referencia está sesgada a la baja
   mientras la serie sea corta.
2. **Surtido ≠ inventario.** Medimos qué se publica, no qué se vende.
3. **Cobertura parcial.** La caja de fruta y verdura no se compone necesariamente
   de referencias publicadas individualmente. El radar refleja mejor la Tienda
   Remolona que la caja.
4. **Frecuencia diaria.** Un producto que entra y sale entre dos snapshots es
   invisible. Con vida útil corta, esto podría no ser raro. La rotación medida es
   una **cota inferior**.
5. **Sin control de causalidad.** Todo lo observado es correlación sobre un
   sistema del que sólo se ve la fachada.
