import pytest
from growpath_observability import CORRELATION_HEADER, CorrelationIdMiddleware, get_correlation_id
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


async def echo_id(_: Request) -> JSONResponse:
    return JSONResponse({"correlation_id": get_correlation_id()})


def make_client() -> TestClient:
    app = Starlette(routes=[Route("/", echo_id)])
    app.add_middleware(CorrelationIdMiddleware)
    return TestClient(app)


def test_reuses_incoming_correlation_id():
    response = make_client().get("/", headers={CORRELATION_HEADER: "gateway-123"})

    assert response.json() == {"correlation_id": "gateway-123"}
    assert response.headers[CORRELATION_HEADER] == "gateway-123"


def test_generates_correlation_id_when_missing():
    response = make_client().get("/")

    generated = response.headers[CORRELATION_HEADER]
    assert generated
    assert response.json() == {"correlation_id": generated}


@pytest.mark.parametrize("bad_id", ["has space", "x" * 129, "semi;colon"])
def test_rejects_malformed_incoming_id(bad_id):
    response = make_client().get("/", headers={CORRELATION_HEADER: bad_id})

    assert response.headers[CORRELATION_HEADER] != bad_id
    assert response.json() == {"correlation_id": response.headers[CORRELATION_HEADER]}


def test_correlation_id_does_not_leak_between_requests():
    client = make_client()
    client.get("/", headers={CORRELATION_HEADER: "first"})

    assert get_correlation_id() is None
    assert client.get("/").headers[CORRELATION_HEADER] != "first"
