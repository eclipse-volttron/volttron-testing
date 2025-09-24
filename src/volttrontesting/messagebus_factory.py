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
Factory for creating MessageBus instances for testing.

This module provides a factory pattern for creating either mock or real
message bus instances based on configuration, allowing tests to run
against either implementation without code changes.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Optional, Dict, Any, Union
from dataclasses import dataclass

from volttron.types import MessageBus, Connection
from volttrontesting.mock_messagebus import MockMessageBus
from volttrontesting.mock_connection import MockConnection

_log = logging.getLogger(__name__)


class MessageBusType(Enum):
    """Enumeration of available message bus types for testing"""
    MOCK = "mock"
    ZMQ = "zmq"
    RMQ = "rmq"
    # Future: could add other implementations


@dataclass
class TestingConfig:
    """Configuration for the testing environment"""
    bus_type: MessageBusType = MessageBusType.MOCK
    bus_config: Dict[str, Any] = None
    volttron_home: Optional[str] = None
    instance_name: Optional[str] = None
    use_process_isolation: bool = False  # New: optionally run agents in separate processes
    
    def __post_init__(self):
        if self.bus_config is None:
            self.bus_config = {}
            
    @classmethod
    def from_env(cls) -> 'TestingConfig':
        """Create config from environment variables"""
        bus_type_str = os.environ.get("VOLTTRON_TEST_BUS", "mock").lower()
        
        # Map string to enum
        type_map = {
            "mock": MessageBusType.MOCK,
            "zmq": MessageBusType.ZMQ,
            "rmq": MessageBusType.RMQ
        }
        bus_type = type_map.get(bus_type_str, MessageBusType.MOCK)
        
        config = {}
        if bus_type != MessageBusType.MOCK:
            # Load real bus configuration from environment
            config["address"] = os.environ.get("VOLTTRON_TEST_BUS_ADDRESS", "tcp://127.0.0.1:22916")
            config["serverkey"] = os.environ.get("VOLTTRON_TEST_SERVERKEY", "")
            
        return cls(
            bus_type=bus_type,
            bus_config=config,
            volttron_home=os.environ.get("VOLTTRON_HOME"),
            instance_name=os.environ.get("VOLTTRON_INSTANCE_NAME", "test-instance")
        )


class MessageBusFactory:
    """
    Factory for creating message bus instances for testing.
    
    This factory allows tests to work with either mock or real message
    bus implementations based on configuration.
    """
    
    def __init__(self, config: Optional[TestingConfig] = None):
        """
        Initialize the factory with configuration.
        
        :param config: Testing configuration. If None, loads from environment.
        """
        self.config = config or TestingConfig.from_env()
        self._bus_instance: Optional[MessageBus] = None
        self._connections: Dict[str, Connection] = {}
        
    def create_message_bus(self) -> MessageBus:
        """
        Create a message bus instance based on configuration.
        
        :return: MessageBus instance (mock or real)
        """
        if self._bus_instance is not None:
            return self._bus_instance
            
        if self.config.bus_type == MessageBusType.MOCK:
            _log.info("Creating MockMessageBus for testing")
            self._bus_instance = MockMessageBus()
            
        elif self.config.bus_type == MessageBusType.ZMQ:
            _log.info("Creating ZMQ MessageBus for testing")
            # Import only when needed to avoid dependency issues
            try:
                from volttron.messagebus.zmq import ZMQMessageBus
                self._bus_instance = ZMQMessageBus()
                # Configure with test settings
                self._bus_instance.configure(self.config.bus_config)
            except ImportError as e:
                _log.error(f"ZMQ MessageBus not available: {e}")
                _log.info("Falling back to MockMessageBus")
                self._bus_instance = MockMessageBus()
                
        elif self.config.bus_type == MessageBusType.RMQ:
            _log.info("Creating RabbitMQ MessageBus for testing")
            # Import only when needed to avoid dependency issues
            try:
                from volttron.messagebus.rmq import RMQMessageBus
                self._bus_instance = RMQMessageBus()
                # Configure with test settings
                self._bus_instance.configure(self.config.bus_config)
            except ImportError as e:
                _log.error(f"RabbitMQ MessageBus not available: {e}")
                _log.info("Falling back to MockMessageBus")
                self._bus_instance = MockMessageBus()
                
        else:
            raise ValueError(f"Unknown bus type: {self.config.bus_type}")
            
        return self._bus_instance
    
    def create_connection(self, identity: str) -> Connection:
        """
        Create a connection for an agent.
        
        :param identity: Agent identity
        :return: Connection instance (mock or real)
        """
        if identity in self._connections:
            return self._connections[identity]
            
        if self.config.bus_type == MessageBusType.MOCK:
            conn = MockConnection(identity)
            # If we have a bus instance, register the connection
            if self._bus_instance and isinstance(self._bus_instance, MockMessageBus):
                self._bus_instance.register_connection(conn)
                
        elif self.config.bus_type == MessageBusType.ZMQ:
            try:
                from volttron.messagebus.zmq import ZMQConnection
                conn = ZMQConnection(identity)
                conn.configure(self.config.bus_config)
            except ImportError:
                _log.warning("ZMQ not available, using MockConnection")
                conn = MockConnection(identity)
                
        elif self.config.bus_type == MessageBusType.RMQ:
            try:
                from volttron.messagebus.rmq import RMQConnection
                conn = RMQConnection(identity)
                conn.configure(self.config.bus_config)
            except ImportError:
                _log.warning("RabbitMQ not available, using MockConnection")
                conn = MockConnection(identity)
        else:
            conn = MockConnection(identity)
            
        self._connections[identity] = conn
        return conn
    
    def start(self):
        """Start the message bus if created"""
        if self._bus_instance:
            self._bus_instance.start()
            
    def stop(self):
        """Stop the message bus and clean up"""
        if self._bus_instance:
            self._bus_instance.stop()
            
        # Disconnect all connections
        for conn in self._connections.values():
            if conn.is_connected():
                conn.disconnect()
                
        self._connections.clear()
        self._bus_instance = None
    
    def is_mock(self) -> bool:
        """Check if using mock implementation"""
        return self.config.bus_type == MessageBusType.MOCK
    
    def get_mock_bus(self) -> Optional[MockMessageBus]:
        """
        Get the mock message bus if using mock mode.
        
        :return: MockMessageBus instance or None if not in mock mode
        """
        if self.is_mock() and isinstance(self._bus_instance, MockMessageBus):
            return self._bus_instance
        return None