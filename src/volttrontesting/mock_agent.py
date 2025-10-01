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
Mock Agent implementation for testing without requiring full VOLTTRON infrastructure.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field

from volttron.types.agent_context import AgentContext
from volttron.types.auth.auth_credentials import Credentials
from volttrontesting.mock_core import MockCore as ProperMockCore

_log = logging.getLogger(__name__)


class MockHealth:
    """Mock Health subsystem"""
    
    def __init__(self, owner, core):
        self._owner = owner
        self._core = core
        
    def send_alert(self, alert_key: str, statusobj):
        """Send an alert through pubsub"""
        # Build the topic like the real health subsystem
        agent_class = self._owner.__class__.__name__
        identity = self._owner.identity if hasattr(self._owner, 'identity') else 'unknown'
        topic = f"alerts/{agent_class}/{identity.replace('.', '_')}"
        
        headers = dict(alert_key=alert_key)
        
        # Publish through pubsub
        if hasattr(self._owner, 'vip') and hasattr(self._owner.vip, 'pubsub'):
            result = self._owner.vip.pubsub.publish(
                "pubsub",
                topic=topic,
                headers=headers,
                message=statusobj.as_json() if hasattr(statusobj, 'as_json') else str(statusobj)
            )
            # Return a mock result with get method for compatibility
            if result is None:
                class MockResult:
                    def get(self, timeout=None):
                        return None
                result = MockResult()
            return result


@dataclass  
class MockVIP:
    """Mock VIP subsystems"""
    pubsub: 'MockPubSub' = field(default_factory=lambda: MockPubSub())
    rpc: 'MockRPC' = field(default_factory=lambda: MockRPC())
    health: 'MockHealth' = None
    
    def peerlist(self):
        """Get list of connected peers"""
        return self.rpc.call('control', 'peerlist')
    

class MockPubSub:
    """Mock PubSub subsystem"""
    
    def __init__(self):
        self._subscriptions: Dict[str, List[Callable]] = {}
        self._published: List[Dict[str, Any]] = []
        self._pubsub_handler = None
        
    def set_handler(self, handler):
        """Set the actual pubsub handler (e.g., TestServer)"""
        self._pubsub_handler = handler
        
    def publish(self, peer: str, topic: str, headers: Optional[Dict] = None, 
                message: Optional[Any] = None, bus: str = ''):
        """Mock publish"""
        self._published.append({
            'peer': peer,
            'topic': topic,
            'headers': headers,
            'message': message,
            'bus': bus
        })
        
        # If we have a handler (TestServer), use it
        if self._pubsub_handler and hasattr(self._pubsub_handler, 'publish'):
            return self._pubsub_handler.publish(topic, headers=headers, message=message, bus=bus)
        
        # Mock async result
        class MockResult:
            def get(self, timeout=None):
                return None
        return MockResult()
    
    def subscribe(self, peer: str, prefix: str, callback: Callable, 
                  bus: str = '', all_platforms: bool = False):
        """Mock subscribe"""
        _log.debug(f"MockPubSub.subscribe called: prefix={prefix}, handler={self._pubsub_handler}")
        if prefix not in self._subscriptions:
            self._subscriptions[prefix] = []
        self._subscriptions[prefix].append(callback)
        
        # If we have a handler (TestServer), use it
        if self._pubsub_handler and hasattr(self._pubsub_handler, 'subscribe'):
            # Create wrapper to adapt TestServer callback signature to VIP signature
            def wrapper_callback(topic, headers, message, bus=''):
                # Call with VIP signature (peer, sender, bus, topic, headers, message)
                if headers is None:
                    headers = {}
                sender = headers.get('sender', 'unknown')
                callback(peer, sender, bus, topic, headers, message)
            
            subscriber = self._pubsub_handler.subscribe(prefix, callback=wrapper_callback)
            # Wrap in async result for compatibility
            class MockResult:
                def __init__(self, value):
                    self._value = value
                def get(self, timeout=None):
                    return self._value
            return MockResult(subscriber)
        
        # Mock async result
        class MockResult:
            def get(self, timeout=None):
                return None
        return MockResult()


class MockRPC:
    """Mock RPC subsystem"""
    
    def __init__(self):
        self._exports: Dict[str, Callable] = {}
        self._test_server = None
        self._identity = None
        
    def set_test_server(self, test_server, identity):
        """Set the TestServer reference for RPC operations"""
        self._test_server = test_server
        self._identity = identity
        
    def export(self, method: Callable, name: Optional[str] = None):
        """Export a method for RPC"""
        method_name = name or method.__name__
        self._exports[method_name] = method
        
    def call(self, peer: str, method: str, *args, **kwargs):
        """Make an RPC call"""
        # Mock async result
        class MockResult:
            def __init__(self, value=None):
                self._value = value
                
            def get(self, timeout=None):
                return self._value
        
        # If calling self, execute locally
        if peer == 'self' and method in self._exports:
            result = self._exports[method](*args, **kwargs)
            return MockResult(result)
        
        # Handle peerlist request
        if method == 'peerlist':
            if self._test_server:
                # Get list of connected agents from TestServer
                connected_agents = list(self._test_server.__connected_agents__.keys())
                return MockResult(connected_agents)
            else:
                return MockResult([])
        
        return MockResult()


class MockAgent:
    """
    Simplified mock agent for testing that doesn't require full VOLTTRON.
    
    This provides a compatible interface with volttron.client.Agent but
    without the complex initialization and dependencies.
    """
    
    def __init__(self, identity: str = None, **kwargs):
        """
        Initialize mock agent.
        
        :param identity: Agent identity
        :param kwargs: Additional arguments (ignored for compatibility)
        """
        identity = identity or kwargs.get('identity', 'mock_agent')
        
        # Create a simple mock context that has the required attributes
        class MockAgentContext:
            def __init__(self, identity):
                self.credentials = Credentials(identity=identity)
                self.address = None
                self.message_bus = 'mock'
                self.volttron_home = None
                
        context = MockAgentContext(identity)
        context.address = kwargs.get('address')
        context.message_bus = kwargs.get('message_bus', 'mock')
        context.volttron_home = kwargs.get('volttron_home')
        
        self.core = ProperMockCore(context, owner=self)
        self.vip = MockVIP()
        # Initialize health subsystem
        self.vip.health = MockHealth(owner=self, core=lambda: self.core)
        self._callbacks = {}
        self.identity = identity
        _log.debug(f"Created MockAgent with identity: {identity}")
        
    def set_pubsub_handler(self, handler):
        """Set the pubsub handler (e.g., TestServer)"""
        self.vip.pubsub.set_handler(handler)
        
    def set_test_server(self, test_server):
        """Set the TestServer reference for VIP operations"""
        self.vip.rpc.set_test_server(test_server, self.identity)
        self.vip.pubsub.set_handler(test_server)