# language: es
Característica: Rotación de RefreshToken, detección de replay y revocación inmediata por desactivación
  Como servicio de autorización centralizado
  Quiero rotar tokens opacos con detección de reuso y revocar sesiones activas al instante
  Para cumplir REQ-AUTH-010, REQ-AUTH-011, REQ-AUTH-015 e INV-AUTH-007

  Escenario: Rotación de RefreshToken e invalidez inmediata tras desactivación
    Dado que el administrador crea y activa un colaborador temporal BDD de sesión corta
    Cuando el colaborador activo rota su sesión con "/api/v1/auth/refresh"
    Entonces la rotación debe devolver HTTP 200 con un nuevo par de tokens distinto al anterior
    Cuando un atacante reutiliza el RefreshToken previo ya rotado
    Entonces el replay attack debe responder HTTP 401
    Cuando el administrador desactiva al colaborador vía "PATCH /api/v1/staff/:id/status"
    Entonces el AccessToken previamente emitido debe responder HTTP 401 en "/api/v1/auth/me"
