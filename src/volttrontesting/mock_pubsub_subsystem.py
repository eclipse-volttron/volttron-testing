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

from __future__ import annotations

import logging
from typing import Dict, Any, Callable, Optional, List
import weakref
import re

from volttrontesting.memory_pubsub import MemoryPubSub, MemorySubscriber

_log = logging.getLogger(__name__)


class MockPubSubSubsystem:
    """Mock PubSub subsystem that routes through a TestServer's MemoryPubSub"""
    
    # Class-level reference to the test server's pubsub
    _test_server_pubsub: Optional[MemoryPubSub] = None
    _subscriptions: Dict[str, List[Callable]] = {}
    
    @classmethod
    def set_test_server_pubsub(cls, pubsub: MemoryPubSub):
        """Set the test server's pubsub system to use for routing"""
        cls._test_server_pubsub = pubsub
        cls._subscriptions.clear()
    
    def __init__(self, core, rpc_subsys, peerlist_subsys, owner, **kwargs):
        """Initialize the mock pubsub subsystem"""
        # Store references but don't call parent __init__ to avoid real pubsub setup
        self.core = weakref.ref(core)
        self.rpc = weakref.ref(rpc_subsys)
        self.peerlist = weakref.ref(peerlist_subsys)
        self._owner = weakref.ref(owner)
        self._subscriptions_by_topic: Dict[str, List[Callable]] = {}
        self._my_subscriptions = {'internal': {}}
        
        _log.debug(f"MockPubSubSubsystem initialized for {core.identity}")
        _log.debug(f"Test server pubsub: {self._test_server_pubsub}")
    
    def publish(self, peer: str, topic: str, headers=None, message=None, bus="", **kwargs):
        """Publish a message through the test server's pubsub"""
        _log.debug(f"MockPubSubSubsystem.publish called: peer={peer}, topic={topic}, message={message}")
        
        if headers is None:
            headers = {}
        
        # Add sender information
        headers['sender'] = self.core().identity if self.core() else "unknown"
        
        if self._test_server_pubsub:
            # Route through test server's pubsub
            _log.debug(f"Publishing to test server pubsub: {self._test_server_pubsub}")
            self._test_server_pubsub.publish(topic, headers=headers, message=message, bus=bus)
            _log.debug(f"Published to test server: {topic}")
        else:
            _log.warning("No test server pubsub configured, message not routed")
        
        return None  # Mock doesn't return futures
    
    def subscribe(self, peer, prefix, callback, bus="", all_platforms=False, **kwargs):
        """Subscribe to a topic pattern through the test server's pubsub"""
        if not callback:
            return None
            
        # Convert VOLTTRON pattern to regex if needed
        # "devices/+/+/all" -> "devices/[^/]+/[^/]+/all"
        regex_pattern = prefix.replace('+', '[^/]+').replace('#', '.*')
        
        # Store the subscription locally
        if prefix not in self._subscriptions_by_topic:
            self._subscriptions_by_topic[prefix] = []
        self._subscriptions_by_topic[prefix].append(callback)
        
        # Create a wrapper callback that matches the expected signature
        def wrapper_callback(topic, headers, message, bus):
            # Call the original callback with the expected signature
            # callback(peer, sender, bus, topic, headers, message)
            sender = headers.get('sender', 'unknown')
            try:
                callback(peer, sender, bus, topic, headers, message)
            except Exception as e:
                _log.error(f"Error in subscription callback: {e}")
        
        if self._test_server_pubsub:
            # Subscribe through test server
            subscriber = self._test_server_pubsub.subscribe(regex_pattern, wrapper_callback)
            _log.debug(f"Subscribed to test server: {prefix} -> {regex_pattern}")
            return subscriber
        else:
            _log.warning("No test server pubsub configured, subscription not active")
            return None
    
    def unsubscribe(self, peer, prefix, callback, bus="", all_platforms=False, **kwargs):
        """Unsubscribe from a topic pattern"""
        if prefix in self._subscriptions_by_topic and callback in self._subscriptions_by_topic[prefix]:
            self._subscriptions_by_topic[prefix].remove(callback)
            if not self._subscriptions_by_topic[prefix]:
                del self._subscriptions_by_topic[prefix]
        return None
    
    def list(self, peer, prefix="", bus="", subscribed=True, reverse=False, all_platforms=False):
        """List subscriptions (mock implementation)"""
        return list(self._subscriptions_by_topic.keys())
    
    # Add other required methods as stubs
    def publish_by_tags(self, peer, tag_condition, headers=None, message=None, bus="", **kwargs):
        """Mock implementation of publish_by_tags"""
        _log.debug("publish_by_tags not implemented in mock")
        return None
    
    def subscribe_by_tags(self, peer, tag_condition, callback, bus="", all_platforms=False, **kwargs):
        """Mock implementation of subscribe_by_tags"""
        _log.debug("subscribe_by_tags not implemented in mock")
        return None