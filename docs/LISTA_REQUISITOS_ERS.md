# Especificación de Requisitos de Software (ERS) — Orders & KDS
## Microservicio de Órdenes y Pantalla de Cocina (KDS)

Documento estructurado de referencia para presentación y evaluación del sistema. Presenta la lista completa de **Requisitos Funcionales (RF)** y **Requisitos No Funcionales (RNF)** con el detalle necesario para su exposición clara ante evaluadores y equipo de desarrollo.

---

# 1. Requisitos Funcionales (RF)

Los requisitos funcionales definen las capacidades de negocio, flujos operativos de meseros y cocineros, y la interacción coreografiada con otros microservicios (Inventario, Menú, Pagos, Sala).

---

### Módulo A: Visualización y Cola de Cocina (KDS)

- **RF-01 · Agrupación visual de comandas por estado**
  - **Descripción:** La pantalla de cocina (KDS) debe clasificar y renderizar dinámicamente las órdenes activas en columnas o secciones bien delimitadas según su fase actual de elaboración (`RESERVED`, `IN_PREPARATION` y `DELIVERED`), garantizando una visión clara de la carga de trabajo en cocina.

- **RF-02 · Ordenamiento por antigüedad en preparación (FIFO)**
  - **Descripción:** Las comandas en la cola de cocina deben presentarse ordenadas con base en el mayor tiempo transcurrido en cocina medido desde `in_preparation_at` (primero la orden con mayor antigüedad), evitando que pedidos atrasados se pierdan de vista en horas pico.

- **RF-03 · Criterio de desempate cronológico**
  - **Descripción:** Cuando dos o más órdenes compartan la misma antigüedad o nivel de prioridad, el sistema desempata de manera predecible y determinista utilizando la marca de tiempo de creación original (`created_at`) y el identificador único de la orden.

- **RF-04 · Retiro automático de comandas pagadas**
  - **Descripción:** En cuanto una comanda pasa al estado terminal `PAID` (tras confirmarse la transacción financiera en el módulo de Pagos), el sistema retira automáticamente su tarjeta del KDS en tiempo real, manteniendo la pantalla despejada.

- **RF-05 · Visualización integral del platillo (modificadores y notas)**
  - **Descripción:** Cada tarjeta de comanda en el KDS debe exponer con total claridad la cantidad, nombre del platillo, modificadores seleccionados (ej. término de carne, salsas) y notas especiales de cocina o alérgenos agregadas por el mesero.

- **RF-06 · Identificador visible de mesa o pedido para llevar**
  - **Descripción:** La tarjeta debe mostrar de manera prominente el número de la mesa física asignada, o indicar claramente la etiqueta "Para llevar" (*Takeaway*) cuando no pertenezca a una mesa del salón comedor.

- **RF-07 · Cronómetro de elaboración y semáforo visual de alerta**
  - **Descripción:** Cada tarjeta expone un cronómetro que mide el tiempo transcurrido desde el ingreso a preparación y cambia el indicador visual a advertencia (*Warning*, amarillo) y crítico (*Late*, rojo) si se superan los umbrales operativos del restaurante.

- **RF-08 · Filtrado operativo por estación culinaria**
  - **Descripción:** Permite a los operadores de cocina filtrar las órdenes o renglones visualizados según la estación física de trabajo (ej. Parrilla, Barra Fría, Bebidas, Freidoras), dirigiendo la atención de cada cocinero a lo que le compete elaborar.

---

### Módulo B: Control de Elaboración y Despacho en Cocina

- **RF-09 · Marcado de preparación por platillo individual ("Listo")**
  - **Descripción:** El cocinero o chef puede marcar un renglón de platillo específico como preparado individualmente, reflejando el progreso parcial de la comanda cuando los tiempos de cocción son asíncronos.

- **RF-10 · Emisión de evento de consumo de insumos (`platillo.preparado`)**
  - **Descripción:** Cada vez que un platillo transita al estado preparado, el backend emite al broker de mensajería el evento `ordenes.platillo.preparado` con su identificador, cantidad e indicador de rehechura (`isRedo`), ordenando al microservicio de Inventario el descuento definitivo de los ingredientes en almacén.

- **RF-11 · Marcado masivo de comanda completa ("Orden lista")**
  - **Descripción:** Provee una acción rápida (por botón táctil o atajo de teclado) para marcar todos los platillos pendientes de una orden como terminados en un solo paso, transicionando la orden al estado `DELIVERED` para que el mesero la traslade a los comensales.

---

### Módulo C: Gestión de Comandas y Terminal del Mesero

- **RF-12 · Apertura de comanda vinculada a mesa física**
  - **Descripción:** El mesero puede aperturar una nueva comanda asociándola formalmente a una mesa de salón, enlazándola con el identificador de sesión de comensales (`sessionId`) provisto por el microservicio de Sala.

- **RF-13 · Apertura de comanda para consumo fuera del salón ("Para Llevar")**
  - **Descripción:** Permite la apertura de órdenes directas para llevar, las cuales no están atadas a mesas de sala pero siguen el mismo flujo de validación, preparación en cocina y facturación.

- **RF-14 · Captura de renglones, modificadores y notas personalizadas**
  - **Descripción:** Permite al mesero armar la orden agregando platillos desde el catálogo, incrementando cantidades, seleccionando ingredientes opcionales y redactando instrucciones especiales para cocina.

- **RF-15 · Trazabilidad y auditoría mediante identidad JWT**
  - **Descripción:** Toda operación de creación, actualización o anulación extrae de manera forzosa el `user_id` y `role` contenidos en las credenciales JWT del usuario autenticado, asegurando la responsabilidad y autoría de cada cambio.

- **RF-16 · Reasignación o transferencia entre mesas**
  - **Descripción:** Permite reasignar una comanda activa a un número de mesa diferente cuando los clientes cambian de ubicación en el salón comedor, manteniendo intactos todos los ítems y estados asociados.

- **RF-17 · Bloqueo preventivo de platillos no disponibles**
  - **Descripción:** El mesero no puede seleccionar ni agregar a la comanda platillos marcados como inactivos o agotados en el catálogo local; el sistema valida la disponibilidad en tiempo real antes de permitir su ingreso a la orden.

---

### Módulo D: Sincronización, Ciclo de Vida y Transiciones

- **RF-18 · Actualización visual y reactiva de la interfaz (UI en tiempo real)**
  - **Descripción:** Toda transición de estado de una orden (`CREATED`, `RESERVED`, `IN_PREPARATION`, `DELIVERED`, `PAID`, `CANCELLED`, `REJECTED`, `WASTED`, `VOIDED`, `EXPIRED`) se refleja de inmediato en las pantallas de meseros y cocina mediante WebSockets sin requerir recargar la página (*zero polling*).

- **RF-19 · Notificación de eventos a módulos externos**
  - **Descripción:** El sistema notifica asíncronamente a los microservicios suscritos cada hito relevante del ciclo de vida (reservas solicitadas, comandas mermadas, cobros requeridos, cancelaciones).

- **RF-20 · Emisión de solicitud de cuenta con precios congelados**
  - **Descripción:** Al solicitar la cuenta de una mesa, el sistema emite el evento `ordenes.cuenta.solicitada` hacia Pagos incluyendo el importe exacto con los precios unitarios que fueron congelados al momento de confirmar la orden, protegiendo al cliente contra cambios posteriores de la carta.

- **RF-21 · Cierre terminal exclusivo por confirmación de pago**
  - **Descripción:** La comanda únicamente transiciona al estado final `PAID` al consumir el evento `pagos.pago.completado`. No es posible cerrar ni dar por pagada una orden desde la UI sin el respaldo de este evento de dominio.

- **RF-22 · Retorno de platillo a cocina para rehechura (*Redo Item*)**
  - **Descripción:** Permite al mesero devolver un platillo ya entregado al estado `PENDING` para que cocina vuelva a prepararlo. Exige una nota obligatoria de justificación, mantiene la orden en estado `DELIVERED` y al finalizar emite un evento extraordinario de descuento en Inventario con la bandera `isRedo: true`.

---

### Módulo E: Cancelaciones y Políticas de Anulación (Mermas)

- **RF-23 · Edición de contenido restringida al estado inicial**
  - **Descripción:** La modificación de platillos, notas o cantidades está estrictamente permitida mientras la orden permanece en estado `CREATED` (o cuando es reabierta tras un rechazo por stock). Una vez que entra a cocina, la orden no admite modificaciones libres de contenido.

- **RF-24 · Cancelación libre y directa por el mesero en estado inicial**
  - **Descripción:** El mesero puede cancelar una orden sin costo alguno ni autorización especial únicamente mientras se encuentre en estado `CREATED`, antes de que se hayan comprometido insumos o iniciado la preparación.

- **RF-25 · Emisión de cancelación y liberación de reservas**
  - **Descripción:** Al cancelarse una orden inicial, se emite el evento `ordenes.orden.cancelada`, instruyendo a Inventario a liberar inmediatamente las pre-reservas de ingredientes y asegurando que no se genere ningún cargo de cobro al comensal.

- **RF-26 · Anulación administrativa exclusiva de administrador (`WASTED`)**
  - **Descripción:** Una comanda en preparación solo puede anularse por motivos ajenos a la cocina (ej. el cliente decide marcharse o cambia intempestivamente de opinión) por un usuario con rol `admin`, transicionando al estado `WASTED` (Mermada).

- **RF-27 · Cobro obligatorio al cliente en anulación administrativa**
  - **Descripción:** La orden en `WASTED` publica `ordenes.comanda.mermada` y `ordenes.cobro.solicitado`: el cliente está legalmente obligado a pagar el monto completo y los insumos se registran como consumidos, ya que la merma no fue responsabilidad del restaurante.

- **RF-28 · Anulación por error de personal de cocina (`VOIDED`)**
  - **Descripción:** Cuando ocurre un fallo imputable a cocina (platillo quemado, contaminación cruzada, error grave de elaboración), un administrador o el chef pueden anular la orden en preparación, cancelando de inmediato la comanda completa hacia el estado `VOIDED`.

- **RF-29 · Justificación obligatoria en error de cocina**
  - **Descripción:** La anulación por error de cocina exige registrar una nota de texto que justifique la causa del fallo (facilitada en el KDS mediante botones táctiles rápidos de motivos comunes y teclado virtual opcional).

- **RF-30 · Registro de pérdida de insumos en cocina**
  - **Descripción:** Al transicionar a `VOIDED`, se emite `ordenes.comanda.mermada` a Inventario para registrar la pérdida definitiva de los insumos físicos utilizados, los cuales no pueden reintegrarse al inventario disponible.

- **RF-31 · Exención total de cobro al cliente en error de cocina**
  - **Descripción:** Una comanda en estado `VOIDED` jamás emite solicitud de cobro (`ordenes.cobro.solicitado` nunca se publica). El comensal queda 100% libre de pago dado que el error fue atribuible al establecimiento.

---

### Módulo F: Coordinación Transaccional (Saga de Inventario)

- **RF-32 · Publicación de solicitud de reserva al confirmar**
  - **Descripción:** Cuando el mesero confirma la orden armada, el sistema fija la marca de tiempo `confirmed_at` y emite de inmediato el evento `ordenes.orden.creada` con los ítems consolidados para solicitar la reserva de stock a Inventario.

- **RF-33 · Transición a cocina sujeta a confirmación de stock**
  - **Descripción:** La comanda pasa al estado `RESERVED` y entra a la cola de trabajo del KDS únicamente tras consumir el evento asíncrono `inventario.stock.reservado`, evitando cocinar órdenes que carecen de insumos en bodega.

- **RF-34 · Manejo de stock insuficiente y alerta inmediata**
  - **Descripción:** Si Inventario responde con el evento `inventario.stock.insuficiente`, la orden transiciona al estado `REJECTED` y el widget emite una alerta visual destacada al mesero indicando los platillos faltantes para su ajuste o re-edición.

- **RF-35 · Temporizador de vencimiento de Saga (*Saga Timeout*)**
  - **Descripción:** Si transcurren 60 segundos desde `confirmed_at` sin que Inventario emita confirmación o rechazo, la orden transiciona automáticamente a `EXPIRED` y emite `ordenes.orden.expirada`, liberando cualquier bloqueo y evitando comandas congeladas.

---

### Módulo G: Integración con Microservicios Vecinos

- **RF-36 / RF-37 / RF-38 · Lotes de platillos intermedios pre-elaborados [Opcional / Futuro]**
  - **Descripción:** Permite al chef registrar la elaboración de lotes de bases culinarias o salsas pre-elaboradas fuera del flujo de una comanda de cliente, emitiendo `ordenes.platillo_intermedio.preparado` hacia Inventario bajo una bandera de configuración (*Feature Toggle*).

- **RF-39 · Asociación automática a sesión de salón (`DiningSession`)**
  - **Descripción:** Consume el evento `sala.dining_session.iniciada` emitido por el microservicio de Sala, registrando la apertura de la mesa, el mesero asignado y el identificador de sesión (`sessionId`) para asociar futuras comandas.

- **RF-40 · Proyección y actualización continua de catálogo en caché**
  - **Descripción:** Consume los eventos `menu.catalogo.actualizado` y `menu.platillo.precio_actualizado` para refrescar la vista materializada en Redis con nombres, precios, fotos y disponibilidad de platillos sin llamar síncronamente al microservicio de Menú.

- **RF-41 · Sincronización de arranque y reconciliación de catálogo**
  - **Descripción:** Durante el inicio del backend y de forma periódica, ejecuta una reconciliación contra el catálogo del servicio de Menú para auto-recuperar cualquier evento que se haya perdido durante caídas de red o reinicios de servicios.

---

# 2. Requisitos No Funcionales (RNF)

Los requisitos no funcionales establecen los atributos de calidad, rendimiento, seguridad, confiabilidad, portabilidad y usabilidad que rigen la arquitectura del microservicio.

---

### Categoría 1: Arquitectura y Transaccionalidad

- **RNF-01 · Atomicidad de estado y mensajería (Patrón *Transactional Outbox*)**
  - **Descripción:** Ningún evento de dominio se envía directamente al broker de mensajería desde el controlador web. Todo cambio de estado en la base de datos se almacena en la misma transacción ACID junto con su evento en la tabla `outbox`, garantizando que jamás se emita un evento de una transacción que haya fallado o hecho *rollback*.

- **RNF-02 · Idempotencia en procesamiento y publicación (*At-Least-Once*)**
  - **Descripción:** El sistema asume entrega *al menos una vez* del broker. Todos los consumidores registran los identificadores de eventos procesados en una tabla `processed_events` para ignorar duplicados, y todos los eventos generados utilizan identificadores UUID deterministas (UUIDv5) basados en el recurso y tipo de evento.

- **RNF-09 · Control de concurrencia optimista**
  - **Descripción:** Las entidades clave (`Order`, `OrderItem`) implementan control de concurrencia optimista mediante una columna entera de versión (`version`). Si dos usuarios o procesos intentan modificar la misma comanda simultáneamente con una versión obsoleta, el sistema rechaza la operación con código HTTP `409 VERSION_CONFLICT`, evitando sobrescrituras ciegas (*Lost Updates*).

---

### Categoría 2: Comunicación y Desempeño

- **RNF-04 · Tiempo real y comunicación bidireccional por WebSockets**
  - **Descripción:** La sincronización de estados entre el backend, el KDS de cocina y los widgets de meseros se efectúa a través de WebSockets sin realizar peticiones periódicas (*polling*). El protocolo segmenta los mensajes en canales dedicados (`kds:station:{station}`, `kds:all`, `floor`, `user:{userId}`) y ofrece reconexión automática transparente en caídas de red.

- **RNF-06 · Eficiencia y umbrales de rendimiento bajo carga (k6)**
  - **Descripción:** El sistema garantiza un tiempo de respuesta de API con percentil 95 inferior a 500 ms (`p95 < 500 ms`) y una tasa de error menor al 1% bajo una carga sostenida de 50 usuarios concurrentes (VUs). La latencia de entrega de notificaciones push por WebSocket debe ser inferior a 1 segundo (`p95 < 1 s`).

- **RNF-13 · Compatibilidad y validación estricta de contratos (JSON Schema / OpenAPI)**
  - **Descripción:** Todo mensaje o evento que cruza una frontera de red (eventos RabbitMQ, contratos REST OpenAPI y sobres de WebSocket) está tipado y validado contra esquemas formales JSON Schema Draft-07 y colecciones ejecutables de Newman/Postman integradas al pipeline de integración continua.

---

### Categoría 3: Seguridad y Resiliencia Operativa

- **RNF-03 · Autenticación, autorización RBAC y seguridad defensiva**
  - **Descripción:** Toda petición protegida valida tokens criptográficos JWT provenientes de Auth. Los endpoints aplican autorización estricta basada en roles (`waiter`, `kitchen`, `chef`, `admin`), sanitizan datos contra inyecciones SQL y Cross-Site Scripting (XSS), y retornan respuestas de error estandarizadas sin filtrar información interna o trazas de la base de datos.

- **RNF-05 · Resiliencia y tolerancia a fallos ante caída de dependencias**
  - **Descripción:** Si el microservicio de Menú o la red sufren una interrupción, el sistema de Órdenes continúa operando y procesando comandas apoyándose en la última copia materializada en su caché local de Redis, evitando paradas de servicio en el restaurante.

---

### Categoría 4: Usabilidad, Accesibilidad y Ergonomía en Cocina

- **RNF-07 · Operabilidad industrial híbrida (Táctil y Teclado / Bump Bar)**
  - **Descripción:** La interfaz del KDS está diseñada con paridad operativa total para pantalla horizontal táctil de cocina (1920×1080) y para teclados industriales de cocina (*Bump Bar*), garantizando que los cocineros con guantes o manos húmedas puedan navegar y despachar órdenes con flechas del teclado o mediante toques táctiles de gran tamaño (≥ 64 px).

- **RNF-08 · Modularidad y aislamiento estilístico (Module Federation)**
  - **Descripción:** El widget de mesero (`OrderTicketWidget`) se empaqueta y exporta mediante Vite Module Federation sin declarar estilos CSS globales que contaminen el microfrontend anfitrión (Shell), funcionando como una pieza desacoplada.

- **RNF-11 · Accesibilidad universal e indicaciones de alto contraste (WCAG 2.1 AA)**
  - **Descripción:** Cumplimiento con estándares WCAG AA: contraste cromático mínimo 4.5:1, foco visualmente nítido en navegación por teclado, anuncios audibles para lectores de pantalla mediante atributos `aria-live`, y codificación dual de información crítica (el estado nunca se comunica únicamente por color, sino también por texto o icono).

---

### Categoría 5: Calidad Estática, Mantenibilidad y Observabilidad

- **RNF-10 · Mantenibilidad y cumplimiento de calidad de código (SonarCloud)**
  - **Descripción:** El código fuente se audita en cada integración mediante un *Quality Gate* estricto en SonarCloud: exige al menos 80% de cobertura de código en pruebas automatizadas, 0 vulnerabilidades de seguridad (*Security Hotspots* revisados) y ausencia de duplicaciones o fallas de mantenibilidad severas.

- **RNF-12 · Observabilidad y auditoría de eventos distribuidos**
  - **Descripción:** El microservicio emite registros de log estructurados en formato JSON que incorporan identificadores de correlación (`correlation_id`) propagados entre servicios, permitiendo reconstruir la trazabilidad completa de una orden a lo largo de toda la arquitectura de microservicios.

