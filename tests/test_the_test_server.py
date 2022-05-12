from testing.volttron import TestServer


def test_instantiate():
    ts = TestServer()
    assert ts
    assert isinstance(ts, TestServer)
    assert ts.config is not None
    assert ts.config.vip_address[0] == 'tcp://127.0.0.1:22916'
