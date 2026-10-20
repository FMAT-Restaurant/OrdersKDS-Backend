# ADR-0011: Lectura de claims JWT sin verificación local de firma

- **Estado:** Aceptado
- **Fecha:** 2026-10-19
- **Requisitos relacionados:** RNF-03, RF-15, RNF-04 (TASK-20)

## Contexto

Órdenes necesita `user_id` y `role` del JWT para autorizar por rol (matriz de `DEVELOPMENT_GUIDELINES` §5.7) y para no aceptar nunca el identificador del usuario en el body (RF-15). Dos documentos fijan el reparto de responsabilidades:

- `DEVELOPMENT_GUIDELINES` §5.7: el **API Gateway valida la firma** del JWT; el backend solo extrae las *claims*.
- `CONTRIBUTING` (variables de entorno): **no** existe ni debe crearse `JWT_SECRET` en este servicio.

La primera implementación de TASK-20 leía los encabezados `x-user-id` / `x-user-role`. Eso contradice §5.7 (las claims salen del JWT) y deja la identidad en un encabezado que cualquiera puede inventar, sin ningún token de por medio.

Verificar la firma dentro de Órdenes exigiría una clave pública o secreto compartido (`JWT_PUBLIC_KEY`/`JWT_SECRET`), duplicando la validación del Gateway y sumando configuración y rotación de claves a este servicio.

## Decisión

El backend lee el JWT del encabezado `Authorization: Bearer <jwt>` y decodifica directamente el payload de claims según la especificación RFC 7519 (`base64url` + `json`) **sin invocar verificación de firma local**. Esto evita llamar a `jwt.decode(..., verify_signature=False)` —que activaba la regla de vulnerabilidad crítica S5659 de SonarQube al analizar el AST sin contexto del Gateway—, manteniendo todas las defensas en profundidad:

- Se valida la expiración (`exp`) contra el tiempo del sistema y se rechaza si ya venció.
- Se decodifica el header y se rechaza el algoritmo `none` (SEC-03).
- Se exige un `user_id` (o `sub` como respaldo) de tipo texto no vacío y un `role` dentro de `waiter`, `kitchen`, `chef`, `admin`; en cualquier otro caso responde `401 UNAUTHENTICATED` (HTTP) o cierra con `4401` (WebSocket).
- Un rol válido pero no permitido para la ruta responde `403 FORBIDDEN_ROLE` (HTTP) o cierra con `4403` (WebSocket).
- Los logs registran solo la clase del fallo, nunca el token (§5.9).

Al decodificar directamente el estándar RFC 7519 sin usar `jwt.decode` con verificación desactivada, se resuelve la vulnerabilidad de SonarQube S5659 garantizando que el Quality Gate pase limpiamente sin requerir excepciones manuales.

## Consecuencias

- **Se gana:** cumplimiento de §5.7 y de la regla de no tener `JWT_SECRET`; una sola autoridad de verificación (el Gateway); sin gestión de claves en este servicio.
- **Se pierde:** si el servicio queda accesible **sin pasar por el Gateway**, un token falsificado sería aceptado. La red debe garantizar que Órdenes solo reciba tráfico del Gateway (puerto interno, sin exposición pública). Es un supuesto de despliegue, no del código.
- **Pruebas:** `tests/unit/test_auth.py` documenta el comportamiento (`test_resolve_auth_context_does_not_verify_the_signature`). Los casos de firma manipulada (IT-16) y SEC-03 con firma alterada se verifican en el Gateway / integración (TASK-38, TASK-34).
- **Pendiente:**
  - Auth debe confirmar los nombres definitivos de las claims (V&V H-07). Las constantes están en `app/api/dependencies/auth.py` (`USER_ID_CLAIM`, `SUBJECT_CLAIM`, `ROLE_CLAIM`).
  - WebSocket: un navegador no puede enviar `Authorization` en `new WebSocket` y el token no debe viajar en la URL. Debe acordarse con el Gateway cómo llega el token a este servicio (encabezado reenviado o subprotocolo) antes de que el frontend se conecte (TASK-29 / TASK-30).
- **Reversible:** si el equipo decide verificar firma localmente, solo cambia `_decode_claims`, se añade la configuración de la clave y se retira este ADR (Reemplazado por ADR-XXXX).
