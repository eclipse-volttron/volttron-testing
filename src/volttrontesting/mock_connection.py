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
from queue import Queue, Empty
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
import uuid

from volttron.types import Connection, Message

_log = logging.getLogger(__name__)


@dataclass
class MockMessage:
    """Simple message container for testing"""
    sender: str
    recipient: str
    subsystem: str
    data: Any
    headers: Dict[str, Any] = field(default_factory=dict)


class MockConnection(Connection):
    """Mock implementation of Connection for testing without a real message bus"""
    
    def __init__(self, identity: str = None):
        self._connected = False
        self._identity = identity or f"mock-{uuid.uuid4()}"
        self._inbox: Queue[Message] = Queue()
        self._outbox: List[Message] = []
        self._message_handlers: Dict[str, List[callable]] = {}
        self._mock_message_bus: Optional[MockMessageBus] = None
        
    @property
    def identity(self) -> str:
        return self._identity
        
    @property
    def connected(self) -> bool:
        return self._connected
    
    def connect(self):
        """Simulate connection"""
        self._connected = True
        _log.debug(f"MockConnection {self._identity} connected")
    
    def disconnect(self):
        """Simulate disconnection"""
        self._connected = False
        _log.debug(f"MockConnection {self._identity} disconnected")
    
    def is_connected(self) -> bool:
        return self._connected
    
    def send_vip_message(self, message: Message):
        """Send a VIP message - store it for inspection and route if bus attached"""
        if not self._connected:
            raise RuntimeError(f"Connection {self._identity} not connected")
        
        self._outbox.append(message)
        
        # If connected to mock message bus, route the message
        if self._mock_message_bus:
            self._mock_message_bus.route_message(self._identity, message)
    
    def receive_vip_message(self, timeout: float = None) -> Optional[Message]:
        """Receive a VIP message from inbox"""
        if not self._connected:
            raise RuntimeError(f"Connection {self._identity} not connected")
        
        try:
            if timeout is None:
                return self._inbox.get_nowait()
            else:
                return self._inbox.get(timeout=timeout)
        except Empty:
            return None
    
    def deliver_message(self, message: Message):
        """Deliver a message to this connection's inbox (used by MockMessageBus)"""
        self._inbox.put(message)
    
    def get_sent_messages(self) -> List[Message]:
        """Get all messages sent by this connection (for testing)"""
        return self._outbox.copy()
    
    def clear_messages(self):
        """Clear all stored messages (for testing)"""
        self._outbox.clear()
        while not self._inbox.empty():
            try:
                self._inbox.get_nowait()
            except Empty:
                break
    
    def set_message_bus(self, bus: MockMessageBus):
        """Attach this connection to a mock message bus"""
        self._mock_message_bus = bus