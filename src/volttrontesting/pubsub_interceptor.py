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
import inspect

from volttrontesting.memory_pubsub import MemoryPubSub

_log = logging.getLogger(__name__)


class PubSubInterceptor:
    """
    Intercepts pubsub calls on an existing agent instance and routes them through
    a test server's MemoryPubSub while preserving decorator-created subscriptions.
    """
    
    def __init__(self, agent, test_server_pubsub: MemoryPubSub):
        """
        Initialize the interceptor for a specific agent instance.
        
        :param agent: The agent whose pubsub to intercept
        :param test_server_pubsub: The test server's MemoryPubSub to route through
        """
        self.agent = agent
        self.test_server_pubsub = test_server_pubsub
        self.original_pubsub = agent.vip.pubsub
        self.intercepted_subscriptions = {}
        
        # Store original methods before replacing
        self._original_publish = self.original_pubsub.publish
        self._original_subscribe = self.original_pubsub.subscribe
        
        _log.debug(f"PubSubInterceptor initialized for agent {agent.core.identity}")
    
    def intercept(self):
        """
        Replace the agent's pubsub methods with intercepted versions.
        This preserves existing subscriptions created by decorators.
        """
        # First, find all existing subscriptions created by decorators
        self._capture_existing_subscriptions()
        
        # Replace the publish method
        def intercepted_publish(peer: str, topic: str, headers=None, message=None, bus="", **kwargs):
            _log.debug(f"Intercepted publish: topic={topic}, message={message}")
            
            # Add sender information
            if headers is None:
                headers = {}
            headers['sender'] = self.agent.core.identity
            
            # Route through test server
            self.test_server_pubsub.publish(topic, headers=headers, message=message, bus=bus)
            
            # Return None to simulate async result
            return None
        
        # Replace the subscribe method  
        def intercepted_subscribe(peer, prefix, callback, bus="", all_platforms=False, **kwargs):
            _log.debug(f"Intercepted subscribe: prefix={prefix}")
            
            # Store the subscription
            if prefix not in self.intercepted_subscriptions:
                self.intercepted_subscriptions[prefix] = []
            self.intercepted_subscriptions[prefix].append(callback)
            
            # Create wrapper for the callback
            def wrapper_callback(topic, headers, message, bus):
                # Call original callback with expected signature
                sender = headers.get('sender', 'unknown')
                try:
                    callback(peer, sender, bus, topic, headers, message)
                except Exception as e:
                    _log.error(f"Error in subscription callback: {e}")
            
            # Subscribe through test server
            # Convert VOLTTRON pattern to regex if needed
            regex_pattern = prefix.replace('+', '[^/]+').replace('#', '.*')
            return self.test_server_pubsub.subscribe(regex_pattern, wrapper_callback)
        
        # Replace the methods on the instance
        self.original_pubsub.publish = intercepted_publish
        self.original_pubsub.subscribe = intercepted_subscribe
        
        # Re-register existing subscriptions with the test server
        self._register_existing_subscriptions()
        
        _log.info(f"PubSub intercepted for agent {self.agent.core.identity}")
    
    def _capture_existing_subscriptions(self):
        """
        Find and capture subscriptions created by @PubSub.subscribe decorators.
        """
        from volttron.client.vip.agent.decorators import annotations
        
        # Look for decorated methods using the annotations function
        for name in dir(self.agent):
            if name.startswith('_'):
                continue
            
            try:
                attr = getattr(self.agent, name)
                if callable(attr):
                    # Check for pubsub.subscriptions annotations
                    for peer, bus, prefix, all_platforms, queue in annotations(attr, set, "pubsub.subscriptions"):
                        if prefix not in self.intercepted_subscriptions:
                            self.intercepted_subscriptions[prefix] = []
                        self.intercepted_subscriptions[prefix].append(attr)
                        _log.debug(f"Found decorated subscription: {prefix} -> {name}")
            except Exception as e:
                pass
        
        # Also check the agent's pubsub internal subscription tracking
        if hasattr(self.original_pubsub, '_my_subscriptions'):
            # _my_subscriptions is structured as {'internal': {bus: {prefix: callbacks}}}
            for platform in self.original_pubsub._my_subscriptions.values():
                for bus_subs in platform.values():
                    for prefix, callbacks in bus_subs.items():
                        if isinstance(callbacks, list):
                            for callback in callbacks:
                                if prefix not in self.intercepted_subscriptions:
                                    self.intercepted_subscriptions[prefix] = []
                                self.intercepted_subscriptions[prefix].append(callback)
                                _log.debug(f"Found internal subscription: {prefix}")
                        elif callable(callbacks):
                            if prefix not in self.intercepted_subscriptions:
                                self.intercepted_subscriptions[prefix] = []
                            self.intercepted_subscriptions[prefix].append(callbacks)
                            _log.debug(f"Found internal subscription: {prefix}")
    
    def _register_existing_subscriptions(self):
        """
        Register captured subscriptions with the test server's pubsub.
        """
        for prefix, callbacks in self.intercepted_subscriptions.items():
            for callback in callbacks:
                # Create wrapper for each callback
                def make_wrapper(cb):
                    def wrapper_callback(topic, headers, message, bus):
                        # Call original callback with expected signature
                        sender = headers.get('sender', 'unknown')
                        try:
                            # Check if it's a bound method that expects self
                            if hasattr(cb, '__self__'):
                                cb('pubsub', sender, bus, topic, headers, message)
                            else:
                                cb('pubsub', sender, bus, topic, headers, message)
                        except Exception as e:
                            _log.error(f"Error in re-registered callback: {e}")
                    return wrapper_callback
                
                # Convert VOLTTRON pattern to regex
                regex_pattern = prefix.replace('+', '[^/]+').replace('#', '.*')
                self.test_server_pubsub.subscribe(regex_pattern, make_wrapper(callback))
                _log.debug(f"Re-registered subscription: {prefix} -> {regex_pattern}")
    
    def restore(self):
        """
        Restore the original pubsub methods.
        """
        self.original_pubsub.publish = self._original_publish
        self.original_pubsub.subscribe = self._original_subscribe
        _log.info(f"PubSub restored for agent {self.agent.core.identity}")


def intercept_agent_pubsub(agent, test_server_pubsub: MemoryPubSub) -> PubSubInterceptor:
    """
    Convenience function to intercept an agent's pubsub.
    
    :param agent: The agent whose pubsub to intercept
    :param test_server_pubsub: The test server's MemoryPubSub
    :return: The PubSubInterceptor instance
    """
    interceptor = PubSubInterceptor(agent, test_server_pubsub)
    interceptor.intercept()
    return interceptor