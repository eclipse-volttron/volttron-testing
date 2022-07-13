from testing.volttron import TestClient


def test_instantiate():
    tc = TestClient()
    assert tc
    assert isinstance(tc, TestClient)
