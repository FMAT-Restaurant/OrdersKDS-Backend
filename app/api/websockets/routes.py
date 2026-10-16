from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

@router.websocket("/ws/kds")
async def kds_websocket(websocket: WebSocket):
    # 1. Aceptamos la conexión
    await websocket.accept()
    
    # 2. Leemos el rol inyectado por el Gateway en los headers
    role = websocket.headers.get("x-user-role")
    
    # 3. Validamos la matriz de roles para /ws/kds
    if role not in ["kitchen", "chef", "admin"]:
        # DoD: mesero en /ws/kds -> 4403
        await websocket.close(code=4403, reason="FORBIDDEN_ROLE")
        return
        
    try:
        # Lógica normal del websocket cuando el rol es correcto
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Mensaje recibido en KDS: {data}")
    except WebSocketDisconnect:
        pass


@router.websocket("/ws/waiter")
async def waiter_websocket(websocket: WebSocket):
    await websocket.accept()
    
    role = websocket.headers.get("x-user-role")
    
    # Validamos la matriz de roles para /ws/waiter
    if role not in ["waiter", "admin"]:
        await websocket.close(code=4403, reason="FORBIDDEN_ROLE")
        return
        
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_text(f"Mensaje recibido en Waiter: {data}")
    except WebSocketDisconnect:
        pass 