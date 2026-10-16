from pydantic import BaseModel, ConfigDict


class OrderUpdateBody(BaseModel):
    # Aquí definimos los campos que SÍ están permitidos.
    # Por ejemplo, tal vez permites enviar una nota a la cocina.
    # Si tu endpoint no necesita recibir nada en el body, simplemente déjalo así.
    notes: str | None = None

    # LA REGLA DE SEGURIDAD (Mass Assignment):
    # 'extra="forbid"' hace que si alguien envía un campo que no está
    # definido arriba (como "userId" o "status"), FastAPI rechace
    # la petición automáticamente con un error 422.
    model_config = ConfigDict(extra="forbid")
