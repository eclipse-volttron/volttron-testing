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
Simple test demonstrating the mock message bus and test server capabilities.
This shows how to test messaging patterns without a real VOLTTRON platform.
"""

import sys
from pathlib import Path

# Add src to path if not already there
path_src = Path(__file__).parent.parent.joinpath('src')
if str(path_src.resolve()) not in sys.path:
    sys.path.insert(0, str(path_src.resolve()))

import logging
from volttrontesting.mock_messagebus import MockMessageBus
from volttrontesting.mock_connection import MockConnection
from volttrontesting.memory_pubsub import MemoryPubSub

# Set up logging
logging.basicConfig(level=logging.INFO)


def test_mock_pubsub_messaging():
    """Test publish/subscribe messaging with mock infrastructure"""
    
    # Create a mock message bus
    bus = MockMessageBus()
    bus.start()
    
    # Get the internal pubsub system
    pubsub = bus.get_pubsub()
    
    # Set up subscribers
    messages_received = []
    
    def callback1(topic, headers, message, bus):
        messages_received.append({
            'subscriber': 'sub1',
            'topic': topic,
            'message': message
        })
    
    def callback2(topic, headers, message, bus):
        messages_received.append({
            'subscriber': 'sub2', 
            'topic': topic,
            'message': message
        })
    
    # Subscribe to different patterns
    sub1 = pubsub.subscribe("devices/campus/.*", callback=callback1)
    sub2 = pubsub.subscribe("devices/.*/building1", callback=callback2)
    
    # Publish some messages
    pubsub.publish("devices/campus/building1", message="data1")
    pubsub.publish("devices/campus/building2", message="data2")
    pubsub.publish("devices/campus2/building1", message="data3")
    pubsub.publish("other/topic", message="data4")
    
    # Check subscriptions received correct messages
    assert len(messages_received) == 4  # sub1 gets 2, sub2 gets 2
    
    # Verify sub1 got campus messages
    sub1_messages = [m for m in messages_received if m['subscriber'] == 'sub1']
    assert len(sub1_messages) == 2
    topics = [m['topic'] for m in sub1_messages]
    assert "devices/campus/building1" in topics
    assert "devices/campus/building2" in topics
    
    # Verify sub2 got building1 messages  
    sub2_messages = [m for m in messages_received if m['subscriber'] == 'sub2']
    assert len(sub2_messages) == 2
    topics = [m['topic'] for m in sub2_messages]
    assert "devices/campus/building1" in topics
    assert "devices/campus2/building1" in topics
    
    # Check message history
    published = pubsub.published_messages
    assert len(published) == 4
    
    print("✓ Mock pubsub messaging test passed")


def test_mock_connections():
    """Test connection-based messaging"""
    
    bus = MockMessageBus()
    bus.start()
    
    # Create two mock connections
    conn1 = MockConnection("agent1")
    conn2 = MockConnection("agent2")
    
    # Register with bus
    bus.register_connection(conn1)
    bus.register_connection(conn2)
    
    # Connect both
    conn1.connect()
    conn2.connect()
    
    # Create a message from agent1 to agent2
    from volttron.types.message import Message
    msg = Message()
    msg.peer = "agent2"
    msg.subsystem = "test"
    msg.data = {"test": "data"}
    
    # Send through conn1
    conn1.send_vip_message(msg)
    
    # Agent2 should receive it
    received = conn2.receive_vip_message(timeout=0.1)
    assert received is not None
    assert received.subsystem == "test"
    assert received.data == {"test": "data"}
    
    # Check message was logged
    log = bus.get_message_log()
    assert len(log) == 1
    assert log[0]["sender"] == "agent1"
    
    print("✓ Mock connection messaging test passed")


def test_memory_pubsub():
    """Test the MemoryPubSub component directly"""
    
    pubsub = MemoryPubSub()
    
    # Track received messages
    received = []
    
    def callback(topic, headers, message, bus):
        received.append((topic, message))
    
    # Subscribe with regex pattern
    subscriber = pubsub.subscribe("test/.*", callback=callback)
    
    # Publish matching and non-matching
    pubsub.publish("test/one", message="msg1")
    pubsub.publish("test/two", message="msg2")  
    pubsub.publish("other/topic", message="msg3")
    
    # Check callback was called for matching topics
    assert len(received) == 2
    assert ("test/one", "msg1") in received
    assert ("test/two", "msg2") in received
    
    # Check subscriber's received messages
    sub_messages = subscriber.received_messages()
    assert len(sub_messages) == 2
    
    # Reset and verify clean
    subscriber.reset_received_messages()
    assert len(subscriber.received_messages()) == 0
    
    print("✓ MemoryPubSub test passed")


if __name__ == "__main__":
    test_mock_pubsub_messaging()
    test_mock_connections()
    test_memory_pubsub()
    print("\n✅ All tests passed! Mock MessageBus infrastructure is working.")