import logging

from testing.volttron import TestServer
from volttron.client import Agent

from testing.volttron.memory_pubsub import PublishedMessage


def test_instantiate():
    ts = TestServer()
    assert ts
    assert isinstance(ts, TestServer)
    # assert ts.config is not None
    # assert ts.config.vip_address[0] == 'tcp://127.0.0.1:22916'


def test_agent_subscription_and_logging():
    an_agent = Agent(identity="foo")
    ts = TestServer()
    assert ts

    log = logging.getLogger("an_agent_logger")
    ts.connect_agent(an_agent, log)

    on_messages_found = []

    def on_message(bus, topic, headers, message):
        on_messages_found.append(PublishedMessage(bus=bus, topic=topic, headers=headers, message=message))
        print(bus, topic, headers, message)
    log.debug("Hello World")
    log_message = ts.get_server_log()[0]
    assert log_message.level == logging.DEBUG
    assert log_message.message == "Hello World"
    subscriber = ts.subscribe('achannel')

    an_agent.vip.pubsub.subscribe(peer="pubsub", prefix='bnnel', callback=on_message)
    ts.publish('achannel', message="This is stuff sent through")
    ts.publish('bnnel', message="Second topic")
    ts.publish('bnnel/foobar', message="Third message")
    assert len(on_messages_found) == 2
    assert len(subscriber.received_messages()) == 1


def test_mock_rpc_call():
    pass
