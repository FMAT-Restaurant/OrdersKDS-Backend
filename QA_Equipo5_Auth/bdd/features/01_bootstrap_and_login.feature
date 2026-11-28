# language: es
Característica: Bootstrap inicial de administrador y autenticación dual anti-enumeración
  Como usuario del ecosistema del restaurante
  Quiero iniciar sesión según mi canal exclusivo ADMIN o STAFF sin revelar existencia de cuentas
  Para garantizar el acceso seguro acorde a REQ-AUTH-001, REQ-AUTH-002 y REQ-AUTH-009

  Escenario: Sistema ya inicializado rechaza segundo bootstrap de Gerente
    Cuando consulto el estado público de inicialización
    Entonces la respuesta HTTP debe ser 200 y el campo "configured" debe ser verdadero
    Cuando intento ejecutar un segundo setup-admin con correo "intruso@fmat.edu.mx"
    Entonces la respuesta HTTP debe ser 409

  Escenario: Autenticación dual exitosa y bloqueo de cruce de canales
    Cuando inicio sesión "ADMIN" con el correo del administrador sembrado y contraseña válida
    Entonces la respuesta HTTP debe ser 200 y entregar un AccessToken JWT de tipo "ADMIN"
    Cuando inicio sesión "STAFF" con el StaffID del Mesero sembrado y contraseña válida
    Entonces la respuesta HTTP debe ser 200 y entregar un AccessToken JWT de tipo "STAFF"
    Cuando intento iniciar sesión "ADMIN" enviando el StaffID "ADM000001"
    Entonces la respuesta HTTP debe ser 401 rechazando el cruce de canal sin acceso
    Cuando intento iniciar sesión "STAFF" enviando el correo "mesero@fmat.edu.mx"
    Entonces la respuesta HTTP debe ser 401 rechazando el cruce de canal sin acceso

  Escenario: Respuesta homogénea anti-enumeración en cuenta inexistente, contraseña errónea e inactiva
    Cuando intento login "STAFF" con credenciales inexistentes "Q999999"
    Entonces el rechazo 401 debe coincidir exactamente con el de contraseña incorrecta y cuenta inactiva
