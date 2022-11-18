# volttron-testing

[![Run Pytests](https://github.com/eclipse-volttron/volttron-testing/actions/workflows/run-tests.yml/badge.svg)](https://github.com/eclipse-volttron/volttron-testing/actions/workflows/run-tests.yml)
[![pypi version](https://img.shields.io/pypi/v/volttron-testing.svg)](https://pypi.org/project/volttron-testing/)

## Prerequisites

* Python >= 3.8

## Installation

Create a virtual environment

```shell 
python -m venv env
```

Activate the environment

```shell
source env/bin/activate
```

Install volttron-testing

```shell
# Installs volttron and volttron-testing
pip install volttron-testing
```

## Developing with TestServer

The following code snippet shows how to utilize the TestServer's internal pubsub to be able to test
with it outside of the volttron platform.

```python
def test_send_alert():
    """ Test that an agent can send an alert through the pubsub message bus."""
    
    # Create an agent to run the test with
    agent = Agent(identity='test-health')

    # Create the server and connect the agent with the server
    ts = TestServer()
    ts.connect_agent(agent=agent)

    # The health.send_alert should send a pubsub message through the message bus
    agent.vip.health.send_alert("my_alert", Status.build(STATUS_BAD, "no context"))
    
    # We know that there should only be a single message sent through the bus and
    # the specifications of the message to test against.
    messages = ts.get_published_messages()
    assert len(messages) == 1
    headers = messages[0].headers
    message = json.loads(messages[0].message)
    assert headers['alert_key'] == 'my_alert'
    assert message['context'] == 'no context'
    assert message['status'] == 'BAD'

```

Reference the volttrontesting package from within your environment in order to build tests against the TestServer.

