import pytest

from langfuse_export.backend import select_backend
from langfuse_export.client import ApiError, AuthError, NetworkError, NotFoundError


class FakeClient:
    def __init__(self, health=None, health_exc=None, probe_exc=None):
        self.health = health or {}
        self.health_exc = health_exc
        self.probe_exc = probe_exc
        self.records = []

    def get_json(self, path, params=None):
        if path == "/api/public/health":
            if self.health_exc is not None:
                raise self.health_exc
            return self.health
        if self.probe_exc is not None:
            raise self.probe_exc
        return {"data": []}


def test_health_major_version_selects_backend():
    assert select_backend(FakeClient(health={"version": "4.76.0"})).kind == "v4"
    assert select_backend(FakeClient(health={"version": "3.179.0"})).kind == "v3"


def test_probe_404_means_v3():
    client = FakeClient(health_exc=ApiError("HTTP 500"), probe_exc=NotFoundError("gone"))
    assert select_backend(client).kind == "v3"


def test_probe_405_means_v3():
    client = FakeClient(health_exc=ApiError("HTTP 500"), probe_exc=ApiError("HTTP 405", status=405))
    assert select_backend(client).kind == "v3"


def test_probe_other_errors_mean_v4():
    for exc in (ApiError("HTTP 400 bad", status=400), AuthError("401"), ApiError("HTTP 403", status=403)):
        client = FakeClient(health_exc=ApiError("HTTP 500"), probe_exc=exc)
        assert select_backend(client).kind == "v4"


def test_network_error_on_health_propagates():
    with pytest.raises(NetworkError):
        select_backend(FakeClient(health_exc=NetworkError("boom")))
