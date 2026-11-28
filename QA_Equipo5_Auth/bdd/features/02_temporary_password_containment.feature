# language: es
Característica: Contención temporal de contraseña y transición irreversible a ACTIVE
  Como administrador de seguridad
  Quiero que toda cuenta creada o restablecida permanezca aislada en estado TEMPORARY hasta cambiar su clave
  Para cumplir REQ-AUTH-012, REQ-AUTH-013, INV-AUTH-002 e INV-AUTH-006

  Escenario: Colaborador nuevo TEMPORARY queda bloqueado en endpoints operativos y activa su cuenta con BVA válido
    Dado que el administrador crea un colaborador temporal BDD con rol "CAJERO"
    Cuando ese colaborador inicia sesión por primera vez con su contraseña temporal
    Entonces el claim JWT "mustChangePassword" debe ser verdadero
    Cuando ese colaborador temporal consulta "GET /api/v1/staff"
    Entonces debe recibir código HTTP 403 con código de error "PASSWORD_CHANGE_REQUIRED"
    Cuando ese colaborador intenta establecer la contraseña límite inválida "Abc123!" de 7 caracteres
    Entonces la respuesta HTTP debe ser 400
    Cuando ese colaborador cambia su contraseña inicial por una válida compleja de 8 caracteres "Qa8!Zx2@"
    Entonces la respuesta HTTP debe ser 200 y su estado en "/api/v1/auth/me" debe pasar a "ACTIVE" conservando el mismo staffId
