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
Simple test of the flexible testing framework without pytest fixtures.
"""

import sys
from pathlib import Path

# Add src to path if not already there
path_src = Path(__file__).parent.parent.joinpath('src')
if str(path_src.resolve()) not in sys.path:
    sys.path.insert(0, str(path_src.resolve()))

from volttrontesting.testing_context import TestingContext
from volttrontesting.messagebus_factory import TestingConfig, MessageBusType


def test_mock_context():
    """Test creating a context with mock message bus"""
    print("\n=== Testing Mock Context ===")
    
    # Create config for mock testing
    config = TestingConfig(bus_type=MessageBusType.MOCK)
    
    # Create testing context
    context = TestingContext(config)
    context.setup()
    
    try:
        # Verify it's in mock mode
        assert context.is_mock_mode
        assert context.bus_type == MessageBusType.MOCK
        print(f"✓ Created context with {context.bus_type.value} message bus")
        
        # Create an agent
        agent1 = context.create_agent("test_agent_1")
        assert agent1 is not None
        assert "test_agent_1" in context.agents
        print("✓ Created test agent")
        
        # Test publishing
        context.publish("test/topic", message="hello world")
        
        # In mock mode, we can check published messages
        messages = context.get_published_messages()
        assert len(messages) > 0
        assert messages[-1].topic == "test/topic"
        assert messages[-1].message == "hello world"
        print("✓ Published and verified message")
        
        # Test subscription
        received = []
        def callback(topic, headers, message, bus):
            received.append((topic, message))
        
        context.subscribe("data/.*", callback=callback)
        context.publish("data/point1", message="value1")
        context.publish("data/point2", message="value2")
        
        # Check published messages
        all_messages = context.get_published_messages()
        data_messages = [m for m in all_messages if m.topic.startswith("data/")]
        assert len(data_messages) == 2
        print("✓ Subscription pattern working")
        
    finally:
        context.teardown()
        print("✓ Context cleaned up")


def test_context_lifecycle():
    """Test context setup and teardown"""
    print("\n=== Testing Context Lifecycle ===")
    
    config = TestingConfig(bus_type=MessageBusType.MOCK)
    context = TestingContext(config)
    
    # Setup
    context.setup()
    assert context.message_bus is not None
    assert context.factory is not None
    print("✓ Context setup completed")
    
    # Create multiple agents
    for i in range(3):
        agent = context.create_agent(f"agent_{i}")
        assert agent is not None
    
    assert len(context.agents) == 3
    print("✓ Created 3 agents")
    
    # Add cleanup function
    cleanup_called = []
    context.add_cleanup(lambda: cleanup_called.append(True))
    
    # Teardown
    context.teardown()
    assert len(cleanup_called) == 1
    assert len(context.agents) == 0
    print("✓ Teardown completed and cleanup called")


def test_agent_context_manager():
    """Test agent context manager"""
    print("\n=== Testing Agent Context Manager ===")
    
    config = TestingConfig(bus_type=MessageBusType.MOCK)
    context = TestingContext(config)
    context.setup()
    
    try:
        # Use context manager for agent
        with context.agent_context("temp_agent") as agent:
            assert agent is not None
            assert "temp_agent" in context.agents
            print("✓ Agent created in context")
        
        # Agent should be cleaned up
        assert "temp_agent" not in context.agents
        print("✓ Agent cleaned up after context")
        
    finally:
        context.teardown()


def test_message_tracking():
    """Test message tracking in mock mode"""
    print("\n=== Testing Message Tracking ===")
    
    config = TestingConfig(bus_type=MessageBusType.MOCK)
    context = TestingContext(config)
    context.setup()
    
    try:
        # Publish several messages
        topics = ["sensor/temp", "sensor/humidity", "control/fan"]
        for i, topic in enumerate(topics):
            context.publish(topic, message=f"value_{i}")
        
        # Check all messages were tracked
        messages = context.get_published_messages()
        assert len(messages) == 3
        
        # Verify message details
        for i, msg in enumerate(messages):
            assert msg.topic == topics[i]
            assert msg.message == f"value_{i}"
        
        print(f"✓ Tracked {len(messages)} messages correctly")
        
    finally:
        context.teardown()


if __name__ == "__main__":
    test_mock_context()
    test_context_lifecycle()
    test_agent_context_manager()
    test_message_tracking()
    print("\n✅ All tests passed!")