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
Testing context that provides a unified interface for testing with
either mock or real message bus implementations.
"""

from __future__ import annotations

import logging
from typing import Optional, Dict, Any, List, Callable
from contextlib import contextmanager
import gevent

from volttron.types import MessageBus, Connection
from volttrontesting.messagebus_factory import MessageBusFactory, TestingConfig, MessageBusType
from volttrontesting.server_mock import TestServer
from volttrontesting.memory_pubsub import PublishedMessage
from volttrontesting.mock_agent import MockAgent

# Try to import Agent, but don't fail if not available
try:
    from volttron.client import Agent
except ImportError:
    Agent = MockAgent

_log = logging.getLogger(__name__)


class TestingContext:
    """
    Unified testing context that can work with mock or real message buses.
    
    This class provides a consistent API for testing regardless of whether
    you're using a mock or real message bus implementation.
    """
    
    def __init__(self, config: Optional[TestingConfig] = None):
        """
        Initialize testing context.
        
        :param config: Testing configuration. If None, loads from environment.
        """
        self.config = config or TestingConfig.from_env()
        self.factory = MessageBusFactory(self.config)
        self.message_bus = None
        self.test_server = None
        self.agents: Dict[str, Agent] = {}
        self._published_messages: List[PublishedMessage] = []
        self._cleanup_functions: List[Callable] = []
        
    def setup(self):
        """Setup the testing environment"""
        _log.info(f"Setting up testing context with {self.config.bus_type.value} message bus")
        
        # Create message bus
        self.message_bus = self.factory.create_message_bus()
        self.message_bus.start()
        
        # If using mock, create test server for additional features
        if self.factory.is_mock():
            self.test_server = TestServer()
            # Link test server to mock message bus if needed
            mock_bus = self.factory.get_mock_bus()
            if mock_bus:
                # Share the pubsub system
                self.test_server._TestServer__server_pubsub__ = mock_bus.get_pubsub()
                
    def teardown(self):
        """Teardown the testing environment"""
        _log.info("Tearing down testing context")
        
        # Run cleanup functions
        for cleanup_fn in reversed(self._cleanup_functions):
            try:
                cleanup_fn()
            except Exception as e:
                _log.error(f"Error during cleanup: {e}")
                
        # Stop all agents
        for agent in self.agents.values():
            try:
                if hasattr(agent, 'core') and hasattr(agent.core, 'stop'):
                    agent.core.stop()
            except Exception as e:
                _log.error(f"Error stopping agent: {e}")
                
        self.agents.clear()
        
        # Stop message bus
        if self.factory:
            self.factory.stop()
            
    def create_agent(self, identity: str, agent_class: type = None, **kwargs):
        """
        Create an agent with the configured message bus.
        
        :param identity: Agent identity
        :param agent_class: Agent class to instantiate
        :param kwargs: Additional arguments for agent creation
        :return: Agent instance
        """
        if self.factory.is_mock():
            # Always use MockAgent in mock mode
            agent_class = agent_class or MockAgent
            agent = agent_class(identity=identity, **kwargs)
            
            if self.test_server:
                # Connect to test server if available
                if hasattr(agent, 'set_pubsub_handler'):
                    agent.set_pubsub_handler(self.test_server)
                    
                # Only connect if agent has expected interface
                if hasattr(agent, 'core') and hasattr(agent, 'vip'):
                    self.test_server.connect_agent(agent)
                    
            self.agents[identity] = agent
            
        else:
            # Use real Agent class for real bus
            agent_class = agent_class or Agent
            connection = self.factory.create_connection(identity)
            # Need to handle agent creation for real bus
            # This would depend on the actual agent framework
            agent = agent_class(identity=identity, **kwargs)
            # agent.setup_connection(connection)  # This would need implementation
            self.agents[identity] = agent
            
        return agent
    
    def publish(self, topic: str, headers: Optional[Dict[str, Any]] = None, 
                message: Optional[Any] = None, bus: str = ''):
        """
        Publish a message to the message bus.
        
        Works with both mock and real implementations.
        """
        if self.factory.is_mock() and self.test_server:
            self.test_server.publish(topic, headers=headers, message=message, bus=bus)
        else:
            # For real bus, would need to use an agent or direct bus API
            # This is a simplified version
            if self.agents:
                # Use first available agent to publish
                agent = next(iter(self.agents.values()))
                agent.vip.pubsub.publish('pubsub', topic, headers=headers, message=message)
                
    def subscribe(self, pattern: str, callback: Optional[Callable] = None):
        """
        Subscribe to topics matching a pattern.
        
        Works with both mock and real implementations.
        """
        if self.factory.is_mock() and self.test_server:
            return self.test_server.subscribe(pattern, callback=callback)
        else:
            # For real bus, create a temporary agent for subscription
            if not self.agents:
                self.create_agent("test_subscriber")
            agent = next(iter(self.agents.values()))
            return agent.vip.pubsub.subscribe('pubsub', pattern, callback=callback)
    
    def get_published_messages(self) -> List[PublishedMessage]:
        """
        Get list of published messages (only available in mock mode).
        """
        if self.factory.is_mock():
            if self.test_server:
                return self.test_server.get_published_messages()
            elif self.factory.get_mock_bus():
                return self.factory.get_mock_bus().get_pubsub().published_messages
        else:
            _log.warning("Published message tracking not available with real message bus")
            return []
    
    def trigger_agent_lifecycle(self, agent: Agent, event: str, sender: str = '', **kwargs):
        """
        Trigger lifecycle events on an agent.
        
        :param agent: Agent instance
        :param event: Lifecycle event ('setup', 'start', 'stop')
        :param sender: Event sender
        :param kwargs: Additional arguments
        """
        if self.factory.is_mock() and self.test_server:
            if event == 'setup':
                return self.test_server.trigger_setup_event(agent, sender, **kwargs)
            elif event == 'start':
                return self.test_server.trigger_start_event(agent, sender, **kwargs)
            elif event == 'stop':
                return self.test_server.trigger_stop_event(agent, sender, **kwargs)
        else:
            # For real bus, would trigger actual lifecycle
            _log.info(f"Triggering {event} on {agent.core.identity}")
            # This would need real implementation
            
    def add_cleanup(self, cleanup_fn: Callable):
        """Add a cleanup function to run during teardown"""
        self._cleanup_functions.append(cleanup_fn)
        
    @contextmanager
    def agent_context(self, identity: str, agent_class: type = None, **kwargs):
        """
        Context manager for creating and cleaning up an agent.
        
        Usage:
            with context.agent_context("test_agent") as agent:
                # Use agent
                pass
            # Agent is automatically cleaned up
        """
        agent = self.create_agent(identity, agent_class, **kwargs)
        try:
            yield agent
        finally:
            if identity in self.agents:
                del self.agents[identity]
                
    def wait_for_messages(self, count: int, timeout: float = 5.0) -> bool:
        """
        Wait for a certain number of messages to be published.
        
        :param count: Number of messages to wait for
        :param timeout: Timeout in seconds
        :return: True if message count reached, False if timeout
        """
        start = gevent.get_hub().loop.time()
        while gevent.get_hub().loop.time() - start < timeout:
            if len(self.get_published_messages()) >= count:
                return True
            gevent.sleep(0.1)
        return False
    
    @property
    def is_mock_mode(self) -> bool:
        """Check if running in mock mode"""
        return self.factory.is_mock()
    
    @property
    def bus_type(self) -> MessageBusType:
        """Get the current message bus type"""
        return self.config.bus_type