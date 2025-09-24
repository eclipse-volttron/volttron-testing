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

_log = logging.getLogger(__name__)


@dataclass
class MockCore:
    """Mock core for testing"""
    identity: str
    
    def stop(self):
        """Stop the core"""
        pass


@dataclass  
class MockVIP:
    """Mock VIP subsystems"""
    pubsub: 'MockPubSub' = field(default_factory=lambda: MockPubSub())
    rpc: 'MockRPC' = field(default_factory=lambda: MockRPC())
    

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
        if prefix not in self._subscriptions:
            self._subscriptions[prefix] = []
        self._subscriptions[prefix].append(callback)
        
        # If we have a handler (TestServer), use it
        if self._pubsub_handler and hasattr(self._pubsub_handler, 'subscribe'):
            return self._pubsub_handler.subscribe(prefix, callback=callback)
        
        # Mock async result
        class MockResult:
            def get(self, timeout=None):
                return None
        return MockResult()


class MockRPC:
    """Mock RPC subsystem"""
    
    def __init__(self):
        self._exports: Dict[str, Callable] = {}
        
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
        self.core = MockCore(identity=identity or "mock_agent")
        self.vip = MockVIP()
        self._callbacks = {}
        _log.debug(f"Created MockAgent with identity: {self.core.identity}")
        
    def set_pubsub_handler(self, handler):
        """Set the pubsub handler (e.g., TestServer)"""
        self.vip.pubsub.set_handler(handler)