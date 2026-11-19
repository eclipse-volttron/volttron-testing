# volttron-testing


[![Eclipse VOLTTRON™](https://img.shields.io/badge/Eclips%20VOLTTRON--red.svg)](https://volttron.readthedocs.io/en/latest/)
![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)
[![Run Pytests](https://github.com/eclipse-volttron/volttron-testing/actions/workflows/run-tests.yml/badge.svg)](https://github.com/eclipse-volttron/volttron-testing/actions/workflows/run-tests.yml)
[![pypi version](https://img.shields.io/pypi/v/volttron-testing.svg)](https://pypi.org/project/volttron-testing/)

The volttron-testing library contains classes and utilities for interacting with a VOLTTRON instance.

## Prerequisites

* Python >= 3.10

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

## Testing Agent Workflows

The volttron-testing library provides multiple approaches for testing VOLTTRON agents, from simple unit tests to complex integration tests with full pubsub communication.

### Quick Start: Testing an Agent

Here's the simplest way to test an agent with mock infrastructure:

```python
from volttrontesting.server_mock import TestServer
from volttrontesting.mock_core_builder import MockCoreBuilder
from volttrontesting.pubsub_interceptor import intercept_agent_pubsub
from volttron.client import Agent
from volttron.client.vip.agent import Core, PubSub
from volttron.types.auth.auth_credentials import Credentials

def test_agent_pubsub():
    """Test agent pubsub communication with full message routing."""
    
    # 1. Create test server
    server = TestServer()
    
    # 2. Create agents with mock core
    publisher = Agent(credentials=Credentials(identity="publisher"), name="mock")
    subscriber = Agent(credentials=Credentials(identity="subscriber"), name="mock")
    
    # 3. Connect agents to server
    server.connect_agent(publisher)
    server.connect_agent(subscriber)
    
    # 4. Intercept pubsub to route through test server
    pub_interceptor = intercept_agent_pubsub(publisher, TestServer.__server_pubsub__)
    sub_interceptor = intercept_agent_pubsub(subscriber, TestServer.__server_pubsub__)
    
    # 5. Set up subscription
    messages_received = []
    def on_message(peer, sender, bus, topic, headers, message):
        messages_received.append((topic, message))
    
    subscriber.vip.pubsub.subscribe("pubsub", "test/topic", on_message)
    
    # 6. Publish message
    publisher.vip.pubsub.publish("pubsub", "test/topic", message="Hello World!")
    
    # 7. Verify message was received
    import gevent
    gevent.sleep(0.1)  # Allow message propagation
    
    assert len(messages_received) == 1
    assert messages_received[0] == ("test/topic", "Hello World!")
```

### Testing Approaches

#### 1. Unit Testing with Direct Method Calls

For simple unit tests, you can test agent methods directly:

```python
class MyAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_points = []
    
    def process_data(self, value):
        """Process incoming data."""
        processed = value * 2
        self.data_points.append(processed)
        return processed

def test_data_processing():
    """Test agent's data processing logic."""
    agent = MyAgent(credentials=Credentials(identity="test"), name="mock")
    
    result = agent.process_data(5)
    assert result == 10
    assert agent.data_points == [10]
```

#### 2. Testing Lifecycle Events

Test agent lifecycle methods (onsetup, onstart, onstop):

```python
class LifecycleAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.setup_complete = False
        self.started = False
    
    @Core.receiver('onsetup')
    def onsetup(self, sender, **kwargs):
        self.setup_complete = True
    
    @Core.receiver('onstart')  
    def onstart(self, sender, **kwargs):
        self.started = True

def test_lifecycle():
    """Test agent lifecycle events."""
    server = TestServer()
    agent = LifecycleAgent(credentials=Credentials(identity="test"), name="mock")
    server.connect_agent(agent)
    
    # Trigger lifecycle events
    response = server.trigger_setup_event(agent, sender="test")
    assert agent.setup_complete
    
    response = server.trigger_start_event(agent, sender="test")
    assert agent.started
```

#### 3. Testing with Decorator-Based Subscriptions

Test agents that use `@PubSub.subscribe` decorators:

```python
class SubscriberAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.received_messages = []
    
    @PubSub.subscribe('pubsub', 'devices/+/+/all')
    def on_device_data(self, peer, sender, bus, topic, headers, message):
        """Handle device data."""
        self.received_messages.append({
            'topic': topic,
            'message': message,
            'sender': sender
        })

def test_decorated_subscriptions():
    """Test agent with decorator-based subscriptions."""
    server = TestServer()
    
    # Create and connect agent
    agent = SubscriberAgent(credentials=Credentials(identity="subscriber"), name="mock")
    server.connect_agent(agent)
    
    # Intercept pubsub for message routing
    interceptor = intercept_agent_pubsub(agent, TestServer.__server_pubsub__)
    
    # Publish matching message through server
    server.publish("devices/campus1/building1/all", 
                   message={"temperature": 72.5})
    
    # Verify message received
    gevent.sleep(0.1)
    assert len(agent.received_messages) == 1
    assert agent.received_messages[0]['topic'] == "devices/campus1/building1/all"
```

### Advanced Testing with PubSub Interceptor

The `pubsub_interceptor` module enables full integration testing by intercepting agent pubsub at the instance level:

```python
from volttrontesting.pubsub_interceptor import PubSubInterceptor

def test_multi_agent_communication():
    """Test complex multi-agent workflows."""
    server = TestServer()
    
    # Create multiple agents
    coordinator = Agent(credentials=Credentials(identity="coordinator"), name="mock")
    worker1 = Agent(credentials=Credentials(identity="worker1"), name="mock")
    worker2 = Agent(credentials=Credentials(identity="worker2"), name="mock")
    
    # Connect all agents
    for agent in [coordinator, worker1, worker2]:
        server.connect_agent(agent)
        intercept_agent_pubsub(agent, TestServer.__server_pubsub__)
    
    # Set up subscriptions
    worker_responses = []
    
    def on_task(peer, sender, bus, topic, headers, message):
        # Workers respond to tasks
        worker_id = headers.get('target')
        if worker_id == 'worker1':
            coordinator.vip.pubsub.publish('pubsub', 'response/worker1', 
                                          message={'result': 'done'})
    
    worker1.vip.pubsub.subscribe('pubsub', 'task/+', on_task)
    
    def on_response(peer, sender, bus, topic, headers, message):
        worker_responses.append(message)
    
    coordinator.vip.pubsub.subscribe('pubsub', 'response/+', on_response)
    
    # Coordinator sends task
    coordinator.vip.pubsub.publish('pubsub', 'task/process', 
                                   headers={'target': 'worker1'},
                                   message={'action': 'process'})
    
    # Verify workflow completed
    gevent.sleep(0.1)
    assert len(worker_responses) == 1
    assert worker_responses[0]['result'] == 'done'
```

### Testing Patterns and Best Practices

1. **Always use mock core for testing**: Pass `name="mock"` when creating agents
2. **Use interceptors for pubsub testing**: This preserves decorator-based subscriptions
3. **Allow time for message propagation**: Use `gevent.sleep(0.1)` after publishing
4. **Clean up interceptors**: Call `interceptor.restore()` when done
5. **Use TestServer for visibility**: Access `server.get_published_messages()` to inspect all messages

### Complete Example: Testing a Realistic Agent

```python
from volttrontesting.server_mock import TestServer
from volttrontesting.pubsub_interceptor import intercept_agent_pubsub
from volttron.client import Agent
from volttron.client.vip.agent import Core, PubSub, RPC
from volttron.types.auth.auth_credentials import Credentials
import gevent

class DataCollectorAgent(Agent):
    """Example agent that collects and aggregates data."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_buffer = []
        self.aggregated_data = None
    
    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        """Start collecting data on agent start."""
        self.core.periodic(self.publish_aggregated, 5)
    
    @PubSub.subscribe('pubsub', 'devices/+/+/all')
    def on_new_data(self, peer, sender, bus, topic, headers, message):
        """Collect incoming data."""
        self.data_buffer.append(message)
        if len(self.data_buffer) >= 10:
            self.aggregate_data()
    
    def aggregate_data(self):
        """Aggregate collected data."""
        if self.data_buffer:
            # Simple average for numeric values
            values = [d.get('value', 0) for d in self.data_buffer if isinstance(d, dict)]
            self.aggregated_data = sum(values) / len(values) if values else 0
            self.data_buffer = []
    
    def publish_aggregated(self):
        """Publish aggregated results."""
        if self.aggregated_data is not None:
            self.vip.pubsub.publish('pubsub', 'analysis/aggregated',
                                   message={'average': self.aggregated_data})
    
    @RPC.export
    def get_buffer_size(self):
        """RPC method to check buffer size."""
        return len(self.data_buffer)

def test_data_collector_agent():
    """Test the complete data collector workflow."""
    
    # Setup
    server = TestServer()
    agent = DataCollectorAgent(credentials=Credentials(identity="collector"), name="mock")
    server.connect_agent(agent)
    interceptor = intercept_agent_pubsub(agent, TestServer.__server_pubsub__)
    
    # Trigger agent start
    server.trigger_start_event(agent, sender="test")
    
    # Simulate incoming data
    for i in range(10):
        server.publish(f"devices/campus/building/all", 
                      message={'value': i * 10, 'timestamp': f'2024-01-01T00:0{i}:00'})
    
    gevent.sleep(0.1)  # Allow message processing
    
    # Verify aggregation occurred
    assert agent.aggregated_data == 45.0  # Average of 0,10,20,...90
    assert len(agent.data_buffer) == 0  # Buffer should be cleared
    
    # Test RPC method
    assert agent.get_buffer_size() == 0
    
    # Cleanup
    interceptor.restore()
```

### TestServer API Reference

The `TestServer` class provides these key methods for testing:

- `connect_agent(agent)`: Connect an agent to the test server
- `publish(topic, headers, message)`: Publish a message through the server
- `subscribe(pattern, callback)`: Subscribe to messages with a pattern
- `trigger_setup_event(agent)`: Trigger agent's onsetup lifecycle event
- `trigger_start_event(agent)`: Trigger agent's onstart lifecycle event
- `trigger_stop_event(agent)`: Trigger agent's onstop lifecycle event
- `get_published_messages()`: Get all messages published through the server
- `get_server_log()`: Get server log messages

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
