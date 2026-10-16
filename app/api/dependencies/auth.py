from fastapi import Request, HTTPException, status
from pydantic import BaseModel
from typing import List

# 1. Definimos el AuthContext que pide la issue
class AuthContext(BaseModel):
    user_id: str
    role: str

# 2. Funcion auxiliar para extraer el contexto (Asumiendo que el Gateway usa Headers)
# Si tu Gateway lo envia de otra forma (ej. dentro de un objeto request.state), me avisas y lo cambiamos.
def get_auth_context(request: Request) -> AuthContext:
    user_id = request.headers.get("x-user-id")
    role = request.headers.get("x-user-role")

    if not user_id or not role:
        # DoD: sin token -> 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Faltan credenciales de autenticación"
        )
    
    return AuthContext(user_id=user_id, role=role)

# 3. La dependencia require_roles()
def require_roles(allowed_roles: List[str]):
    def role_checker(request: Request):
        context = get_auth_context(request)
        
        if context.role not in allowed_roles:
            # DoD: mesero en un endpoint de cocina -> 403
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="FORBIDDEN_ROLE"
            )
        return context
    
    return role_checker  