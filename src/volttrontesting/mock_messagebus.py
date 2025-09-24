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
from typing import Dict, List, Optional, Any, Callable
from queue import Queue
import re

from volttron.types import MessageBus, Message, MessageBusStopHandler
from volttrontesting.mock_connection import MockConnection
from volttrontesting.memory_pubsub import MemoryPubSub, PublishedMessage

_log = logging.getLogger(__name__)


class MockMessageBus(MessageBus):
    """Mock implementation of MessageBus for testing without a real message bus"""
    
    def __init__(self):
        super().__init__()
        self._running = False
        self._connections: Dict[str, MockConnection] = {}
        self._pubsub = MemoryPubSub()
        self._rpc_handlers: Dict[str, Dict[str, Callable]] = {}
        self._message_log: List[Dict[str, Any]] = []
        
    def start(self):
        """Start the mock message bus"""
        self._running = True
        _log.debug("MockMessageBus started")
    
    def stop(self):
        """Stop the mock message bus"""
        self._running = False
        if hasattr(self, '_stop_handler') and self._stop_handler:
            self._stop_handler.message_bus_shutdown()
        _log.debug("MockMessageBus stopped")
    
    def is_running(self) -> bool:
        """Check if the message bus is running"""
        return self._running
    
    def create_federation_bridge(self):
        """Federation not implemented in mock"""
        return None
    
    def send_vip_message(self, message: Message):
        """Send a VIP message through the bus"""
        self._log_message("send", message)
        # Route based on subsystem
        if message.subsystem == "pubsub":
            self._handle_pubsub_message(message)
        elif message.subsystem == "rpc":
            self._handle_rpc_message(message)
        else:
            # Direct peer-to-peer messaging
            self._route_direct_message(message)
    
    def receive_vip_message(self) -> Message:
        """Not implemented for bus-level - connections handle their own receiving"""
        raise NotImplementedError("Use connection.receive_vip_message() instead")
    
    def register_connection(self, connection: MockConnection):
        """Register a connection with this message bus"""
        self._connections[connection.identity] = connection
        connection.set_message_bus(self)
        _log.debug(f"Registered connection: {connection.identity}")
    
    def unregister_connection(self, identity: str):
        """Unregister a connection from this message bus"""
        if identity in self._connections:
            del self._connections[identity]
            _log.debug(f"Unregistered connection: {identity}")
    
    def route_message(self, sender: str, message: Message):
        """Route a message from a connection through the bus"""
        self._log_message("route", message, sender=sender)
        
        # Handle based on subsystem
        if hasattr(message, 'subsystem'):
            if message.subsystem == "pubsub":
                self._handle_pubsub_message(message, sender)
            elif message.subsystem == "rpc":
                self._handle_rpc_message(message, sender)
            else:
                self._route_direct_message(message, sender)
    
    def _handle_pubsub_message(self, message: Message, sender: str = None):
        """Handle pubsub messages"""
        # Extract pubsub operation from message
        if hasattr(message, 'args') and len(message.args) > 0:
            operation = message.args[0]
            
            if operation == "publish":
                # Extract topic, headers, message from args
                if len(message.args) >= 4:
                    topic = message.args[1]
                    headers = message.args[2] if len(message.args) > 2 else {}
                    msg = message.args[3] if len(message.args) > 3 else None
                    self._pubsub.publish(topic=topic, headers=headers, message=msg)
                    _log.debug(f"Published to {topic}")
            
            elif operation == "subscribe":
                # Handle subscription
                if len(message.args) >= 3:
                    prefix = message.args[1]
                    # Store subscription info for routing
                    _log.debug(f"{sender} subscribed to {prefix}")
    
    def _handle_rpc_message(self, message: Message, sender: str = None):
        """Handle RPC messages"""
        # Extract RPC details from message
        if hasattr(message, 'peer') and hasattr(message, 'method'):
            peer = message.peer
            method = message.method
            args = getattr(message, 'args', [])
            kwargs = getattr(message, 'kwargs', {})
            
            # Look up registered RPC handler
            if peer in self._rpc_handlers and method in self._rpc_handlers[peer]:
                handler = self._rpc_handlers[peer][method]
                try:
                    result = handler(*args, **kwargs)
                    # Send result back to sender
                    self._send_rpc_response(sender, message, result)
                except Exception as e:
                    self._send_rpc_error(sender, message, str(e))
    
    def _route_direct_message(self, message: Message, sender: str = None):
        """Route a direct peer-to-peer message"""
        if hasattr(message, 'peer'):
            peer = message.peer
            if peer in self._connections:
                self._connections[peer].deliver_message(message)
                _log.debug(f"Delivered message from {sender} to {peer}")
    
    def _send_rpc_response(self, recipient: str, original_message: Message, result: Any):
        """Send an RPC response back to the caller"""
        if recipient in self._connections:
            # Create response message
            response = Message(
                peer=recipient,
                subsystem="rpc",
                result=result,
                id=getattr(original_message, 'id', None)
            )
            self._connections[recipient].deliver_message(response)
    
    def _send_rpc_error(self, recipient: str, original_message: Message, error: str):
        """Send an RPC error back to the caller"""
        if recipient in self._connections:
            # Create error message
            error_msg = Message(
                peer=recipient,
                subsystem="rpc",
                error=error,
                id=getattr(original_message, 'id', None)
            )
            self._connections[recipient].deliver_message(error_msg)
    
    def register_rpc_handler(self, identity: str, method: str, handler: Callable):
        """Register an RPC method handler"""
        if identity not in self._rpc_handlers:
            self._rpc_handlers[identity] = {}
        self._rpc_handlers[identity][method] = handler
        _log.debug(f"Registered RPC handler: {identity}.{method}")
    
    def get_pubsub(self) -> MemoryPubSub:
        """Get the internal pubsub system for testing"""
        return self._pubsub
    
    def get_message_log(self) -> List[Dict[str, Any]]:
        """Get the message log for testing"""
        return self._message_log.copy()
    
    def _log_message(self, action: str, message: Message, sender: str = None):
        """Log a message for testing purposes"""
        log_entry = {
            "action": action,
            "sender": sender,
            "message": message
        }
        self._message_log.append(log_entry)
    
    def clear_logs(self):
        """Clear all logs for testing"""
        self._message_log.clear()
        self._pubsub = MemoryPubSub()