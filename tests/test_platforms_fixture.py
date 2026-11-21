"""The platforms(n) fixture: several independent platforms in one session, each usable on its own."""
import gevent
import pytest

from volttrontesting.utils import poll_gevent_sleep


@pytest.mark.timeout(900)
def test_two_platforms_are_independent(platforms):
    a, b = platforms(2)
    assert a.is_running() and b.is_running()
    assert a.volttron_home != b.volttron_home and a.address != b.address
    assert a.serverkey.publickey != b.serverkey.publickey

    # An agent installed through vctl on each, and built agents for RPC and pubsub on each.
    for wrapper in (a, b):
        uuid = wrapper.install_agent(agent_dir="volttron-listener", start=True)
        assert wrapper.is_agent_running(uuid)
        assert "platform.control" in wrapper.dynamic_agent.vip.peerlist().get(timeout=10)

    received = {id(a): [], id(b): []}
    publishers = {}
    for wrapper in (a, b):
        subscriber = wrapper.build_agent(identity="smoke_subscriber")
        subscriber.vip.pubsub.subscribe(
            "pubsub", "smoke", callback=lambda peer, sender, bus, topic, headers, message, key=id(wrapper):
            received[key].append(topic)).get(timeout=10)
        publishers[id(wrapper)] = wrapper.build_agent(identity="smoke_publisher")

    publishers[id(a)].vip.pubsub.publish("pubsub", "smoke/a", message="from a").get(timeout=10)
    assert poll_gevent_sleep(10, lambda: "smoke/a" in received[id(a)])
    gevent.sleep(1)
    assert received[id(b)] == []          # nothing crosses between the platforms
