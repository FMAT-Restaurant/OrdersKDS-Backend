# language: es
Característica: Control de acceso PBAC, prefijos de StaffID y bloqueo anti-escalación
  Como gerente de restaurante y controlador PBAC
  Quiero crear roles dinámicos sin privilegios administrativos ni roles vacíos
  Para garantizar REQ-AUTH-003, REQ-AUTH-004, REQ-AUTH-005, REQ-AUTH-006, INV-AUTH-003 e INV-AUTH-005

  Escenario: Bloqueo de escalación a ADMINISTRADOR o wildcard y creación de rol personalizado con prefijo E
    Dado que estoy autenticado como administrador principal
    Cuando intento crear un rol dinámico con permiso wildcard "*"
    Entonces la respuesta HTTP debe ser 400
    Cuando intento crear un colaborador con roles vacíos
    Entonces la respuesta HTTP debe ser 400
    Cuando intento crear un colaborador asignando el rol reservado "ADMINISTRADOR"
    Entonces la respuesta HTTP debe ser 400
    Cuando creo el rol dinámico "ROL_EVENTOS_BDD" con permiso "staff:view" y un colaborador asignado
    Entonces la respuesta HTTP debe ser 201 y el staffId generado debe iniciar con "E" cumpliendo "^[A-Z][0-9]{6}$"
    Cuando un colaborador "MESERO" autenticado consulta "GET /api/v1/staff"
    Entonces la respuesta HTTP debe ser 200 sin exponer el campo "passwordHash"
