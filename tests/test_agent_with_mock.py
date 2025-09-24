# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2022 Battelle Memorial Institute
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy
# of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#
# ===----------------------------------------------------------------------===
# }}}

"""
Integration test showing how to test VOLTTRON agents without a real message bus.
This uses the mock implementations to test agent behavior in isolation.
"""

import sys
from pathlib import Path

# Add src to path if not already there
path_src = Path(__file__).parent.parent.joinpath('src')
if str(path_src.resolve()) not in sys.path:
    sys.path.insert(0, str(path_src.resolve()))

import logging
import gevent

# Import the mock core builder to register it
from volttrontesting.mock_core_builder import MockCoreBuilder
from volttrontesting.mock_messagebus import MockMessageBus
from volttrontesting.mock_connection import MockConnection
from volttrontesting.server_mock import TestServer
from volttrontesting.memory_pubsub import MemoryPubSub
from volttrontesting.mock_agent_factory import MockIntegratedAgent, create_mock_agent
from volttron.client import Agent
from volttron.client.vip.agent import Core, PubSub
from volttron.types.auth.auth_credentials import Credentials

# Set up logging
logging.basicConfig(level=logging.DEBUG)


class TestAgent(Agent):
    """Example agent for testing"""
    
    def __init__(self, identity="test_agent", name="mock", **kwargs):
        # Create credentials for the agent
        credentials = Credentials(identity=identity)
        super().__init__(credentials=credentials, name=name, **kwargs)
        self.received_messages = []
        self.setup_complete = False
        self.started = False
        
    @Core.receiver('onsetup')
    def onsetup(self, sender, **kwargs):
        """Handle setup event"""
        self.setup_complete = True
        logging.info(f"Agent {self.core.identity} setup complete")
        
    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        """Handle start event"""
        self.started = True
        logging.info(f"Agent {self.core.identity} started")
        
    @PubSub.subscribe('pubsub', 'test/topic')
    def on_test_message(self, peer, sender, bus, topic, headers, message):
        """Handle test messages"""
        self.received_messages.append({
            'topic': topic,
            'headers': headers,
            'message': message
        })
        logging.info(f"Received message on {topic}: {message}")


def test_agent_with_mock_messagebus():
    """Test agent behavior using mock message bus"""
    
    # Create mock message bus
    bus = MockMessageBus()
    bus.start()
    
    # Create test server with our existing memory pubsub
    server = TestServer()
    
    # Create and connect test agent using mock core
    agent = TestAgent(identity="test_agent_1", name="mock")
    
    # Connect agent to test server
    server.connect_agent(agent, logging.getLogger("test_agent_1"))
    
    # Trigger lifecycle events
    response = server.trigger_setup_event(agent, sender="test")
    assert response.identity == "test_agent_1"
    assert response.called_method == "onsetup"
    assert agent.setup_complete
    
    response = server.trigger_start_event(agent, sender="test")
    assert response.identity == "test_agent_1"
    assert response.called_method == "onstart"
    assert agent.started
    
    # For now, skip the pubsub test since it requires more integration work
    # between the real Agent's pubsub and our mock server
    
    # Test direct message injection to simulate pubsub
    agent.on_test_message(peer="test", sender="test", bus="", 
                         topic="test/topic", 
                         headers={"test_header": "value"}, 
                         message="Test message 1")
    
    # Check agent received the message
    assert len(agent.received_messages) == 1
    assert agent.received_messages[0]['topic'] == "test/topic"
    assert agent.received_messages[0]['message'] == "Test message 1"
    assert agent.received_messages[0]['headers']['test_header'] == "value"
    
    # Test logging
    log = logging.getLogger("test_agent_1")
    log.info("Test log message")
    
    server_log = server.get_server_log()
    assert len(server_log) > 0
    assert server_log[-1].message == "Test log message"
    assert server_log[-1].level == logging.INFO
    
    # Clean up
    bus.stop()


def test_multiple_agents_interaction():
    """Test interaction between multiple agents using mock infrastructure"""
    
    # Create test server
    server = TestServer()
    
    # Create publisher agent using mock core
    publisher = Agent(credentials=Credentials(identity="publisher"), name="mock")
    server.connect_agent(publisher)
    
    # Create subscriber agent using mock core
    subscriber = TestAgent(identity="subscriber", name="mock")
    server.connect_agent(subscriber)
    
    # Test direct message injection instead of using real pubsub
    # This simulates a pubsub message being received
    subscriber.on_test_message(peer="publisher", sender="publisher", bus="",
                              topic="test/topic/data",
                              headers={"source": "publisher"},
                              message={"data": 42})
    
    # Verify subscriber received it
    assert len(subscriber.received_messages) == 1
    msg = subscriber.received_messages[0]
    assert msg['topic'] == "test/topic/data"
    assert msg['message'] == {"data": 42}
    assert msg['headers']['source'] == "publisher"
    
    # Test server publishing
    server.publish("test/topic/data", 
                   headers={"source": "server"},
                   message={"data": 100})
    
    # Check server tracked the publication
    published = server.get_published_messages()
    assert len(published) > 0
    assert published[-1].topic == "test/topic/data"
    assert published[-1].message == {"data": 100}


def test_agent_subscription_patterns():
    """Test different subscription patterns with mock"""
    
    server = TestServer()
    agent = TestAgent(identity="pattern_test", name="mock")
    server.connect_agent(agent)
    
    # Subscribe to wildcard pattern
    received_wildcard = []
    def on_wildcard(topic, headers, message, bus):
        received_wildcard.append((topic, message))
    
    # Subscribe using server's subscribe (simulating platform subscription)
    # Convert VOLTTRON wildcard pattern to regex pattern
    # "devices/+/+/all" becomes "devices/[^/]+/[^/]+/all"
    subscriber = server.subscribe("devices/[^/]+/[^/]+/all", callback=on_wildcard)
    
    # Publish to various topics
    server.publish("devices/campus1/building1/all", message="data1")
    server.publish("devices/campus2/building2/all", message="data2")
    server.publish("other/topic", message="data3")
    
    # Check wildcard subscription received appropriate messages
    # Note: subscriber.received_messages() returns the messages
    messages = subscriber.received_messages()
    assert len(messages) == 2
    topics = [msg.topic for msg in messages]
    assert "devices/campus1/building1/all" in topics
    assert "devices/campus2/building2/all" in topics
    assert "other/topic" not in topics


def test_integrated_pubsub_simple():
    """Test agents with intercepted pubsub"""
    from volttrontesting.pubsub_interceptor import intercept_agent_pubsub
    
    # Create test server
    server = TestServer()
    
    # Create a test agent class with decorator-based subscriptions
    class InterceptedTestAgent(Agent):
        def __init__(self, identity):
            credentials = Credentials(identity=identity)
            super().__init__(credentials=credentials, name="mock")
            self.received_messages = []
            
        @PubSub.subscribe('pubsub', 'test/topic')
        def on_test_message(self, peer, sender, bus, topic, headers, message):
            """Handle test messages through intercepted pubsub"""
            self.received_messages.append({
                'peer': peer,
                'sender': sender,
                'topic': topic,
                'headers': headers,
                'message': message
            })
            logging.info(f"Intercepted agent received: {topic}: {message}")
    
    # Create agents with normal initialization
    publisher = Agent(credentials=Credentials(identity="intercepted_publisher"), name="mock")
    subscriber = InterceptedTestAgent(identity="intercepted_subscriber")
    
    # Connect agents to server
    server.connect_agent(publisher)
    server.connect_agent(subscriber)
    
    # Intercept their pubsub to route through test server
    pub_interceptor = intercept_agent_pubsub(publisher, TestServer.__server_pubsub__)
    sub_interceptor = intercept_agent_pubsub(subscriber, TestServer.__server_pubsub__)
    
    # Publisher publishes through intercepted pubsub
    print(f"Publishing through intercepted pubsub...")
    publisher.vip.pubsub.publish("pubsub", "test/topic", 
                                 headers={"source": "intercepted"},
                                 message="Intercepted test message")
    
    # Give a moment for message propagation
    gevent.sleep(0.1)
    
    # Check what messages were published to the server
    server_messages = server.get_published_messages()
    print(f"Server received {len(server_messages)} messages")
    if server_messages:
        print(f"Last message: topic={server_messages[-1].topic}, message={server_messages[-1].message}")
    
    # Check subscriber received the message
    print(f"Subscriber received {len(subscriber.received_messages)} messages")
    if subscriber.received_messages:
        print(f"Received: {subscriber.received_messages[0]}")
    
    assert len(subscriber.received_messages) == 1
    msg = subscriber.received_messages[0]
    assert msg['topic'] == "test/topic"
    assert msg['message'] == "Intercepted test message"
    assert msg['headers']['source'] == "intercepted"
    assert msg['sender'] == "intercepted_publisher"
    
    # Restore original pubsub
    pub_interceptor.restore()
    sub_interceptor.restore()
    
    print("✓ Intercepted pubsub working correctly!")


if __name__ == "__main__":
    # Run tests
    test_agent_with_mock_messagebus()
    print("✓ test_agent_with_mock_messagebus passed")
    
    test_multiple_agents_interaction()
    print("✓ test_multiple_agents_interaction passed")
    
    test_agent_subscription_patterns()
    print("✓ test_agent_subscription_patterns passed")
    
    # Test integrated pubsub
    test_integrated_pubsub_simple()
    print("✓ test_integrated_pubsub_simple passed")
    
    print("\nAll tests passed!")