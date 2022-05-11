from testing.volttron import TestServer


def test_instantiate():
    ts = TestServer()
    assert ts
    assert isinstance(ts, TestServer)
