# volttron-testing

[![Run Pytests](https://github.com/eclipse-volttron/volttron-testing/actions/workflows/run-tests.yml/badge.svg)](https://github.com/eclipse-volttron/volttron-testing/actions/workflows/run-tests.yml)
[![pypi version](https://img.shields.io/pypi/v/volttron-testing.svg)](https://pypi.org/project/volttron-testing/)

The volttron-testing library contains classes and utilities for interacting with a VOLTTRON instance.

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

## Development

Please see the following for contributing guidelines [contributing](https://github.com/eclipse-volttron/volttron-core/blob/develop/CONTRIBUTING.md).

Please see the following helpful guide about [developing modular VOLTTRON agents](https://github.com/eclipse-volttron/volttron-core/blob/develop/DEVELOPING_ON_MODULAR.md)

# Disclaimer Notice

This material was prepared as an account of work sponsored by an agency of the
United States Government.  Neither the United States Government nor the United
States Department of Energy, nor Battelle, nor any of their employees, nor any
jurisdiction or organization that has cooperated in the development of these
materials, makes any warranty, express or implied, or assumes any legal
liability or responsibility for the accuracy, completeness, or usefulness or any
information, apparatus, product, software, or process disclosed, or represents
that its use would not infringe privately owned rights.

Reference herein to any specific commercial product, process, or service by
trade name, trademark, manufacturer, or otherwise does not necessarily
constitute or imply its endorsement, recommendation, or favoring by the United
States Government or any agency thereof, or Battelle Memorial Institute. The
views and opinions of authors expressed herein do not necessarily state or
reflect those of the United States Government or any agency thereof.
