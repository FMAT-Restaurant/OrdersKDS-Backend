# FMAT-RESTAURANT — Guía visual de componentes

**Versión 2.0** · Minimalista · claro · consistente · fácil de implementar

Un UI kit simple para que todos los equipos diseñen con la misma base: colores, controles, cards, navegación, tablas y estados.

> Este documento es la versión en Markdown de `FMAT_RESTAURANT_Guia_Visual_Componentes.pdf` y es la referencia visual para el desarrollo de **todos** los frontends (Shell de Auth, Menú, Sala, Órdenes y KDS, etc.).

### Tabla de contenidos

1. [Paleta de color](#1-paleta-de-color)
2. [Tipografía, espacio y forma](#2-tipografía-espacio-y-forma)
3. [Botones](#3-botones)
4. [Inputs y formularios](#4-inputs-y-formularios)
5. [Cards](#5-cards)
6. [Menús y navegación](#6-menús-y-navegación)
7. [Tablas y listas](#7-tablas-y-listas)
8. [Estados, mensajes y modales](#8-estados-mensajes-y-modales)
9. [Patrones del restaurante](#9-patrones-del-restaurante)
10. [Ejemplos de composición](#10-ejemplos-de-composición)
11. [Responsive](#11-responsive)
12. [Guía rápida para los equipos](#12-guía-rápida-para-los-equipos)
13. [Tokens básicos (CSS)](#13-tokens-básicos-css)

---

## 1. Paleta de color

Pocos colores, mucho espacio en blanco y un naranja reservado para acciones y énfasis.

| Muestra | Nombre | Hex | Uso |
|:---:|---|---|---|
| ![#C2410C](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-C2410C?style=flat-square) | **Primary** | `#C2410C` | Botones y selección |
| ![#9A3412](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-9A3412?style=flat-square) | **Hover** | `#9A3412` | Hover / pressed |
| ![#F97316](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-F97316?style=flat-square) | **Accent** | `#F97316` | Iconos y detalles |
| ![#FFF7ED](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-FFF7ED?style=flat-square) | **Soft** | `#FFF7ED` | Selección suave |
| ![#111827](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-111827?style=flat-square) | **Ink** | `#111827` | Títulos |
| ![#374151](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-374151?style=flat-square) | **Text** | `#374151` | Contenido |
| ![#6B7280](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-6B7280?style=flat-square) | **Muted** | `#6B7280` | Texto secundario |
| ![#E5E7EB](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-E5E7EB?style=flat-square) | **Border** | `#E5E7EB` | Líneas y controles |
| ![#F8FAFC](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-F8FAFC?style=flat-square) | **Canvas** | `#F8FAFC` | Fondo de página |
| ![#FFFFFF](https://img.shields.io/badge/%20%20%20%20%20%20%20%20-FFFFFF?style=flat-square) | **Surface** | `#FFFFFF` | Paneles y cards |

> Las muestras de color son insignias de shields.io; el valor de referencia siempre es el código hexadecimal de la columna **Hex**.

### Estados

La guía define cinco estados semánticos: **Éxito**, **Advertencia**, **Error**, **Información** y **Seleccionado**.

| Estado | Uso | Color |
|---|---|---|
| Éxito | Operación completada, "En stock", "Pagada" | *La guía no fija el hex* |
| Advertencia | "Bajo stock", "Pendiente" | *La guía no fija el hex* |
| Error | Fallos, "Agotado", acciones destructivas | *La guía no fija el hex* |
| Información | Mensajes neutros, "Sincronizando…" | *La guía no fija el hex* |
| Seleccionado | Fondo suave + borde | `Soft` `#FFF7ED` + borde `Primary` `#C2410C` |

> ⚠️ **Pendiente de acordar entre equipos:** el PDF muestra los colores de Éxito, Advertencia, Error e Información solo como imagen, sin códigos hexadecimales. Antes de implementarlos hay que fijar un valor único y agregarlo a los tokens (sección 13) para que ningún equipo invente el suyo. Regla ya establecida: los estados se comunican con **color + texto**, nunca solo con color.

### Cómo se usa

| Proporción | Contenido |
|---|---|
| **70 %** | Blanco y fondos neutros |
| **20 %** | Texto, bordes y estructura |
| **10 %** | Naranja y estados |

**Idea clave:** el naranja guía la atención. No se usa como fondo de cada card ni para decorar toda la pantalla.

---

## 2. Tipografía, espacio y forma

Una sola familia, medidas repetibles y esquinas discretas.

### Tipografía

- **Fuente:** Inter.
- **Fallback:** `system-ui, Segoe UI, Roboto, Arial, sans-serif`.

| Estilo | Tamaño / interlineado | Peso | Uso |
|---|---|---|---|
| **H1** | 28 / 36 px | 700 | Título de pantalla |
| **H2** | 22 / 30 px | 700 | Título de sección |
| **H3** | 18 / 26 px | 600 | Título de componente |
| **Body** | 16 / 24 px | 400 | Texto de interfaz claro y fácil de escanear |
| **Small** | 14 / 20 px | 400 | Texto auxiliar, fechas, etiquetas y descripciones |
| **Caption** | 12 / 16 px | 400 | Texto mínimo |

### Espaciado

Escala repetible basada en una unidad de 4 px:

`4` · `8` · `12` · `16` · `24` · `32` · `48` (px)

### Radios

| Radio | Valor |
|---|---|
| Pequeño | 4 px |
| Medio | 8 px *(controles)* |
| Grande | 12 px *(cards)* |

### Otras medidas

| Propiedad | Regla |
|---|---|
| **Peso** | 400 para texto · 600/700 para jerarquía |
| **Altura** | 40 px en controles · 48 px en touch |
| **Sombra** | Muy suave; solo si separa capas |

---

## 3. Botones

Misma altura, mismo radio y una sola acción principal por bloque.

### Variantes

| Variante | Ejemplo | Uso |
|---|---|---|
| **Primario** | Guardar cambios | Acción principal |
| **Secundario** | Vista previa | Alternativa visible |
| **Terciario** | Cancelar | Baja prioridad |
| **Destructivo** | Eliminar | Acción irreversible |
| **Deshabilitado** | Guardar cambios | Sin interacción |

### Tamaños

| Tamaño | Altura | Ejemplo |
|---|---|---|
| **SM** | 32 px | Filtrar |
| **MD** | 40 px | Guardar |
| **LG** | 48 px | Enviar a cocina |

### Iconos y grupos

- Botón solo icono: **40 × 40 px mínimo** (ejemplos: Anterior, Siguiente).
- Botón con icono y texto: "Agregar producto".
- **Texto: verbo + objeto.** "Cobrar cuenta" funciona mejor que "Aceptar".

### Regla rápida

- Primario **a la derecha**; Cancelar **antes**.
- El estado *loading* evita el doble clic.
- Rojo **solo** para eliminar/cancelar.

---

## 4. Inputs y formularios

Etiqueta visible, ayuda breve y error junto al campo.

### Ejemplo de formulario "Nuevo producto"

| Campo | Obligatorio | Ejemplo |
|---|:---:|---|
| Nombre | Sí (`*`) | Hamburguesa clásica |
| Precio | Sí (`*`) | $145.00 |
| Categoría | Sí (`*`) | Platos fuertes *(select)* |
| Descripción | No | Carne, queso, vegetales y pan artesanal |
| Disponible para venta | — | Interruptor activo/inactivo |

Acciones del formulario: **Cancelar** (terciario) y **Guardar** (primario, a la derecha).

### Estados del campo

| Estado | Ejemplo | Comportamiento |
|---|---|---|
| **Default** | "Escribe aquí" | Placeholder solo como ejemplo, nunca como etiqueta |
| **Focus** | "Tomate" | Foco visible |
| **Error** | Valor `-2` | Mensaje junto al campo: *"Ingresa un valor mayor que 0."* |
| **Deshabilitado** | "Solo lectura" | Sin interacción |

### Controles

Checkbox · Radio · Interruptor (Activo / Inactivo) · Búsqueda (ej. "Buscar producto") · Select (ej. "Selecciona una opción").

### Evitar

- Usar el placeholder como etiqueta.
- Limpiar los datos del usuario por un error de red.
- Mostrar "Dato inválido" sin explicar cómo corregirlo.

---

## 5. Cards

Borde fino, sombra casi imperceptible y contenido que define la jerarquía. **Sin franjas de color laterales.**

### Product card

Contiene: estado (ej. *Disponible*), título ("Hamburguesa clásica"), descripción breve, precio (`$145.00`) y el botón **Agregar**.

### Orden seleccionable

Ejemplo: `Orden #2035 · Mesa 20 · 4 personas · $230.00` con etiqueta de estado (*Pagada* / *Pendiente*).

- **Selected:** fondo suave (`Soft`) **+ borde**.

### Resumen (métricas)

| Métrica | Ejemplo |
|---|---|
| VENTAS HOY | $12,480 (+8.4 %) |
| PEDIDOS | 48 (12 pendientes) + acción "Ver pedidos" |

### Card horizontal

Ejemplo: "Ensalada de la casa · Cantidad: 2 · $236.00".

> Usa imagen solo si ayuda a reconocer el contenido.

---

## 6. Menús y navegación

**El Shell comparte la navegación; cada equipo entrega el contenido de su módulo.**

- Elementos de ejemplo del menú lateral: *Inicio, Catálogo, Pedidos, Usuarios* (bajo la marca **FMAT**).
- Barra superior de módulo con búsqueda y acción principal (ej. "Buscar producto" + "Agregar").
- Filtros/pestañas de ejemplo: *Todos · Disponibles · Agotados*.

### Variantes de la navegación lateral

| Variante | Descripción |
|---|---|
| **Compacto** | Solo iconos |
| **Expandido** | Iconos + texto |

### Complementos

- **Breadcrumb:** `Inicio / Inventario / Productos`.
- **Paginación:** `1 2 3 4 5`.

> Los módulos **no** duplican menú ni topbar: los aporta el Shell.

---

## 7. Tablas y listas

Bordes ligeros, números alineados y acciones secundarias dentro de un menú.

### Tabla

Barra superior con búsqueda ("Buscar ingrediente") y acción ("Agregar").

| Ingrediente | Existencia | Unidad | Estado |
|---|---:|---|---|
| Tomate saladet | 18.50 | kg | En stock |
| Aceite vegetal | 4.00 | L | Bajo stock |
| Queso manchego | 0.00 | kg | Agotado |
| Tortilla de maíz | 240 | pzas | En stock |
| Pan artesanal | 32 | pzas | En stock |

### Lista compacta

| Producto | Precio | Estado |
|---|---:|---|
| Hamburguesa | $145 | Activo |
| Ensalada | $118 | Activo |
| Sándwich | $132 | Activo |
| Postre | $92 | Agotado |

> En móvil, la tabla puede convertirse en lista.

---

## 8. Estados, mensajes y modales

El usuario siempre sabe qué pasó y qué puede hacer después.

### Mensajes

| Tipo | Ejemplo |
|---|---|
| Éxito | Producto guardado correctamente. |
| Advertencia | Quedan 4 unidades en inventario. |
| Error | No se pudo procesar el pago. |
| Información | Sincronizando cambios… |

### Carga y vacío

- **Estado vacío:** "Aún no hay productos" + ayuda: "Agrega el primero para comenzar".
- **Carga:** indicador visible (ej. "Sincronizando cambios…").

### Modal de confirmación

> **Confirmar eliminación**
> Se eliminará "Hamburguesa clásica". Esta acción no se puede deshacer.
>
> Aviso: *Revisa el producto antes de continuar.*
>
> **[Cancelar]  [Eliminar]**

### Toast

Ejemplo: **"Cambios guardados"**.

---

## 9. Patrones del restaurante

Piezas reutilizables; **no son pantallas obligatorias para cada equipo.**

### Mesa

| Estado | Ejemplo |
|---|---|
| **Ocupada** | Mesa 04 · 4 personas · 32 min · acción "Ver mesa" |
| **Disponible** | Mesa 07 · Capacidad: 4 personas |

### Comanda

```
MESA 04 · #184                          12:41
2  Hamburguesa clásica
   Sin cebolla · término 3/4
1  Ensalada de la casa
   Aderezo aparte
3  Agua mineral

[ Marcar como lista ]
```

### Cuenta / recibo

```
Cuenta #1234
Mesa 04 · 22/09/2026 · 18:45

2 × Hamburguesa      $290.00
1 × Ensalada         $118.00
3 × Agua mineral      $90.00
--------------------------------
Total                $498.00

[ Cobrar cuenta ]
```

---

## 10. Ejemplos de composición

Dos composiciones de referencia. **Cada equipo adapta el contenido, no los estilos base.**

### Catálogo

Shell (menú lateral) + módulo "Productos" con barra *Buscar / Agregar* y una cuadrícula de cards de producto (ej. Burger $145, Ensalada $118, Sándwich $132).

### Pedidos

Shell + módulo "Órdenes" con una lista de cards seleccionables (Orden #2035…#2038, Mesa 20, $230) y un **panel de detalle** de la orden seleccionada:

| Concepto | Importe |
|---|---:|
| Hamburguesa × 2 | $290 |
| Ensalada × 1 | $118 |
| Agua × 3 | $90 |

Acción: **Cobrar cuenta**.

---

## 11. Responsive

Los componentes se reacomodan; no se encogen hasta volverse ilegibles.

| Dispositivo | Ancho | Columnas | Composición |
|---|---|:---:|---|
| **Desktop** | ≥ 1200 px | 12 | Sidebar + 3/4 columnas de contenido |
| **Tablet** | 768–1199 px | 8 | Sidebar compacta + 2 columnas |
| **Mobile** | < 768 px | 4 | Una columna + navegación inferior |

---

## 12. Guía rápida para los equipos

Lo mínimo que debe mantenerse igual en cualquier módulo.

| ✅ Sí | ❌ Evitar |
|---|---|
| Usar los mismos tokens y componentes. | Inventar otro naranja o radio. |
| Una acción primaria por bloque. | Poner color al costado de cada card. |
| Cards blancas con borde fino. | Llenar la pantalla de cajas decorativas. |
| Estados con color + texto. | Usar rojo como color principal. |
| Espacio de 8, 16, 24 o 32 px. | Ocultar etiquetas dentro del placeholder. |
| Probar escritorio, tablet y móvil. | Duplicar menú, topbar o estilos globales. |

### Antes de entregar

- [ ] Componentes compartidos
- [ ] Estados *loading* / vacío / error
- [ ] Teclado y foco visibles
- [ ] Contraste y touch de 44 px
- [ ] **Sin estilos globales**
- [ ] Responsive revisado

---

## 13. Tokens básicos (CSS)

Tokens definidos por la guía:

```css
--color-primary: #C2410C;
--color-primary-hover: #9A3412;
--color-primary-soft: #FFF7ED;
--color-text: #111827;
--color-border: #E5E7EB;
--radius-control: 8px;
--radius-card: 12px;
--space-unit: 4px;
```

> **Nota sobre `--color-text`:** en la guía el token `--color-text` vale `#111827`, que en la paleta corresponde al color **Ink** (títulos). El color **Text** de la paleta (contenido) es `#374151`. Conviene acordar si `--color-text` se usa para títulos o para contenido, y nombrar el otro (por ejemplo `--color-ink` / `--color-body`) para evitar confusiones entre equipos.

### Referencia de implementación *(propuesta, no forma parte del PDF)*

Los tokens restantes de la paleta pueden nombrarse con el mismo patrón. Los nombres siguientes son una **propuesta** a validar con los demás equipos:

```css
/* Restantes de la paleta (nombres propuestos) */
--color-accent: #F97316;
--color-muted: #6B7280;
--color-canvas: #F8FAFC;
--color-surface: #FFFFFF;
```

Al aplicar los tokens en un módulo que se carga dentro del Shell (Module Federation), **no declares reglas globales**: sin CSS reset, sin estilos sobre `body` / `:root` y sin selectores sin acotar. Define los tokens en el contenedor raíz de tu módulo:

```css
.orders-kds-root {
  --color-primary: #C2410C;
  --color-primary-hover: #9A3412;
  --color-primary-soft: #FFF7ED;
  --color-text: #111827;
  --color-border: #E5E7EB;
  --radius-control: 8px;
  --radius-card: 12px;
  --space-unit: 4px;
}
```

Ejemplo de mapeo en `tailwind.config.ts` (con `corePlugins: { preflight: false }` para no introducir un reset global):

```ts
import type { Config } from 'tailwindcss';

export default {
  content: ['./src/**/*.{ts,tsx}'],
  corePlugins: { preflight: false },
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: '#C2410C', hover: '#9A3412', accent: '#F97316', soft: '#FFF7ED' },
        ink: '#111827',
        body: '#374151',
        muted: '#6B7280',
        border: '#E5E7EB',
        canvas: '#F8FAFC',
        surface: '#FFFFFF',
      },
      borderRadius: { sm: '4px', control: '8px', card: '12px' },
      fontFamily: { sans: ['Inter', 'system-ui', 'Segoe UI', 'Roboto', 'Arial', 'sans-serif'] },
    },
  },
} satisfies Config;
```

---

*FMAT-RESTAURANT · Guía visual de componentes · v2.0*
