from fastapi import APIRouter, Depends
from app.api.dependencies.auth import require_roles, AuthContext

# 1. Importamos el esquema que acabamos de crear
from app.api.schemas.order import OrderUpdateBody 

router = APIRouter()

KITCHEN_ROLES = ["kitchen", "chef", "admin"]

@router.patch("/orders/{orderId}/items/{itemId}/ready")
async def mark_item_ready(
    orderId: str, 
    itemId: str,
    # 2. Agregamos el body a la función
    body: OrderUpdateBody, 
    user_context: AuthContext = Depends(require_roles(KITCHEN_ROLES)) 
):
    # Si llega hasta aquí, estamos seguros de que el usuario tiene el rol correcto
    # Y que NO inyectó campos maliciosos como userId o status en el body
    return {"message": "Platillo marcado como preparado"}

@router.patch("/orders/{orderId}/ready")
async def mark_order_ready(
    orderId: str, 
    # 2. Agregamos el body a la función
    body: OrderUpdateBody, 
    user_context: AuthContext = Depends(require_roles(KITCHEN_ROLES))
):
    return {"message": "Orden marcada como lista"}