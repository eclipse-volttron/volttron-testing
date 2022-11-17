import json

from volttron.client import Agent
from volttron.client.messaging.health import Status, STATUS_BAD

from volttrontesting import TestClient, TestServer


def test_send_alert():

    agent = Agent(identity='test-health')

    ts = TestServer()
    ts.connect_agent(agent=agent)

    agent.vip.health.send_alert("my_alert", Status.build(STATUS_BAD, "no context"))
    messages = ts.get_published_messages()
    assert len(messages) == 1
    headers = messages[0].headers
    message = json.loads(messages[0].message)
    assert headers['alert_key'] == 'my_alert'
    assert message['context'] == 'no context'
    assert message['status'] == 'BAD'
