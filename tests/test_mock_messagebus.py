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

import sys
from pathlib import Path

# Add src to path if not already there
path_src = Path(__file__).parent.parent.joinpath('src')
if str(path_src.resolve()) not in sys.path:
    sys.path.insert(0, str(path_src.resolve()))

import pytest
from volttrontesting.mock_messagebus import MockMessageBus
from volttrontesting.mock_connection import MockConnection
from volttron.types.message import Message


def test_mock_messagebus_basic():
    """Test basic MockMessageBus functionality"""
    bus = MockMessageBus()
    
    # Test start/stop
    assert not bus.is_running()
    bus.start()
    assert bus.is_running()
    bus.stop()
    assert not bus.is_running()


def test_mock_connection_basic():
    """Test basic MockConnection functionality"""
    conn = MockConnection("test-agent")
    
    assert conn.identity == "test-agent"
    assert not conn.is_connected()
    
    conn.connect()
    assert conn.is_connected()
    
    conn.disconnect()
    assert not conn.is_connected()


def test_connection_registration():
    """Test registering connections with the message bus"""
    bus = MockMessageBus()
    bus.start()
    
    conn1 = MockConnection("agent1")
    conn2 = MockConnection("agent2")
    
    bus.register_connection(conn1)
    bus.register_connection(conn2)
    
    # Connections should have the bus attached
    assert conn1._mock_message_bus == bus
    assert conn2._mock_message_bus == bus
    
    bus.unregister_connection("agent1")
    # conn1 should still have reference but not be in bus registry


def test_pubsub_through_mock():
    """Test pubsub functionality through mock message bus"""
    bus = MockMessageBus()
    bus.start()
    
    # Get the internal pubsub
    pubsub = bus.get_pubsub()
    
    # Test direct pubsub
    pubsub.publish(topic="test/topic", message="test message", headers={"foo": "bar"})
    
    messages = pubsub.published_messages
    assert len(messages) == 1
    assert messages[0].topic == "test/topic"
    assert messages[0].message == "test message"
    assert messages[0].headers == {"foo": "bar"}


def test_message_logging():
    """Test that messages are logged for inspection"""
    bus = MockMessageBus()
    bus.start()
    
    conn = MockConnection("test-agent")
    bus.register_connection(conn)
    conn.connect()
    
    # Create a test message
    msg = Message()
    msg.peer = "other-agent"
    msg.subsystem = "test"
    
    # Send message through connection
    conn.send_vip_message(msg)
    
    # Check the bus logged it
    log = bus.get_message_log()
    assert len(log) > 0
    assert log[0]["action"] == "route"
    assert log[0]["sender"] == "test-agent"


def test_clear_logs():
    """Test clearing logs and resetting state"""
    bus = MockMessageBus()
    bus.start()
    
    pubsub = bus.get_pubsub()
    pubsub.publish(topic="test", message="msg")
    
    assert len(pubsub.published_messages) == 1
    
    bus.clear_logs()
    
    # Should have fresh pubsub
    new_pubsub = bus.get_pubsub()
    assert len(new_pubsub.published_messages) == 0