import pytest

# from main import app  <-- (Recuerda importar tu app real luego)
# client = TestClient(app)


@pytest.mark.parametrize(
    "endpoint, role, expected_status",
    [
        ("/orders/123/ready", "waiter", 403),
        ("/orders/123/ready", "kitchen", 200),
        ("/orders/123/ready", "chef", 200),
        ("/orders/123/ready", "admin", 200),
        ("/orders/123/items/456/ready", "waiter", 403),
        ("/orders/123/items/456/ready", "kitchen", 200),
    ],
)
def test_kitchen_endpoints_role_matrix(endpoint, role, expected_status):
    # response = client.patch(endpoint, headers=headers)
    # assert response.status_code == expected_status
    pass


@pytest.mark.parametrize(
    "endpoint, role, should_close_with",
    [
        ("/ws/kds", "waiter", 4403),
        ("/ws/kds", "kitchen", None),
        ("/ws/kds", "chef", None),
        ("/ws/kds", "admin", None),
        ("/ws/waiter", "kitchen", 4403),
        ("/ws/waiter", "waiter", None),
        ("/ws/waiter", "admin", None),
    ],
)
def test_websocket_role_matrix(endpoint, role, should_close_with):
    # if should_close_with is not None:
    #     with pytest.raises(WebSocketDisconnect) as exc_info:
    #         with client.websocket_connect(endpoint, headers=headers) as websocket:
    #             pass
    #     assert exc_info.value.code == should_close_with
    # else:
    #     with client.websocket_connect(endpoint, headers=headers) as websocket:
    #         pass
    pass
