"""Devices (SEC-11) through the real server: a hello names its device, refusals are
counted, and Settings lists and forgets devices."""

from __future__ import annotations

from typing import Any

from starlette.websockets import WebSocketDisconnect

from tests.api.test_server import ORIGIN, home, make
from trillion.security.devices import device_id

__all__ = ["home", "make"]

LAPTOP = "laptop-device-id-0123456789"
PHONE = "phone-device-id-0123456789"


def hello(app: Any, device: str | None, host: str = "chrome") -> None:
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        body = {"type": "hello", "token": app.rt.token, "protocol": 1, "host": host}
        ws.send_json(body | ({"device": device} if device else {}) | {"client": "web"})
        while ws.receive_json()["type"] != "state":
            pass


def test_a_hello_records_its_device_and_settings_lists_it(make: Any) -> None:
    app = make(model=False)
    hello(app, LAPTOP)
    listed = app.client.get("/api/devices", headers=app.auth).json()["devices"]
    kept, _ = device_id(LAPTOP, "chrome")
    assert [(d["id"], d["label"]) for d in listed] == [(kept, "Chrome")]
    # A second device is an event, waiting for the door.
    hello(app, PHONE, "firefox")
    assert app.rt.security is not None
    assert len(app.client.get("/api/devices", headers=app.auth).json()["devices"]) == 2


def test_forget_hides_a_device(make: Any) -> None:
    app = make(model=False)
    hello(app, LAPTOP)
    kept, _ = device_id(LAPTOP, "chrome")
    post = {**app.auth, "content-type": "application/json"}
    assert (
        app.client.post("/api/devices/forget", json={"id": kept}, headers=post).status_code == 200
    )
    assert app.client.get("/api/devices", headers=app.auth).json()["devices"] == []
    again = app.client.post("/api/devices/forget", json={"id": kept}, headers=post)
    assert again.status_code == 404
    bad = app.client.post("/api/devices/forget", json={"id": "../x"}, headers=post)
    assert bad.status_code == 422


def test_refusals_are_counted_never_a_device(make: Any) -> None:
    app = make(model=False)
    with app.client.websocket_connect("/ws", headers=ORIGIN) as ws:
        ws.send_json({"type": "hello", "token": "wrong", "protocol": 1, "device": LAPTOP})
        try:
            ws.receive_json()
            ws.receive_json()
        except WebSocketDisconnect:
            pass
    assert (
        app.client.get("/api/devices", headers={"authorization": "Bearer nope"}).status_code == 401
    )
    security = app.rt.security
    assert security is not None
    assert security._refusals.counts == {"socket:token": 1, "request:token": 1}
    assert security.book.known() == []
