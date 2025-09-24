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
from typing import Dict, Any, Callable, Optional
from dataclasses import dataclass
import gevent
from gevent.event import Event

from volttron.types import CoreLoop, Message, Connection
from volttron.types.agent_context import AgentContext
from volttron.client.vip.agent.dispatch import Signal
from volttrontesting.mock_connection import MockConnection

_log = logging.getLogger(__name__)


class MockCore(CoreLoop):
    """Mock implementation of Core for testing"""
    
    def __init__(self, context: AgentContext, owner=None):
        self._context = context
        self._owner = owner
        self._identity = context.credentials.identity
        self._connection: Optional[MockConnection] = None
        self._subsystems: Dict[str, Callable] = {}
        self._error_handlers: Dict[str, Callable] = {}
        self._running = False
        self._stop_event = Event()
        
        # Lifecycle signals (like real Core)
        self._onsetup_signal = Signal()
        self._onstart_signal = Signal()
        self._onconnected_signal = Signal()
        self._ondisconnected_signal = Signal()
        
    def setup(self):
        """Setup the mock core"""
        # Create mock connection
        self._connection = MockConnection(self._identity)
        _log.debug(f"MockCore setup for {self._identity}")
        # Send setup signal
        self._onsetup_signal.send(self, **{})
    
    def loop(self, running_event):
        """Main loop for the mock core"""
        self._running = True
        self._connection.connect()
        
        # Trigger onconnected signal
        self._onconnected_signal.send(self, **{})
        
        # Send start signal
        self._onstart_signal.send(self, **{})
        
        # Run until stopped
        while self._running and not self._stop_event.is_set():
            gevent.sleep(0.1)
            
            # Process any incoming messages
            message = self._connection.receive_vip_message(timeout=0.1)
            if message:
                self._handle_message(message)
        
        # Trigger ondisconnected signal
        self._ondisconnected_signal.send(self, **{})
        
        self._connection.disconnect()
    
    def stop(self):
        """Stop the mock core"""
        self._running = False
        self._stop_event.set()
    
    def send_vip(self, message: Message):
        """Send a VIP message"""
        if self._connection:
            self._connection.send_vip_message(message)
    
    def _handle_message(self, message: Message):
        """Handle an incoming message"""
        if hasattr(message, 'subsystem'):
            subsystem = message.subsystem
            if subsystem in self._subsystems:
                try:
                    self._subsystems[subsystem](message)
                except Exception as e:
                    if subsystem in self._error_handlers:
                        self._error_handlers[subsystem](e, message)
                    else:
                        _log.error(f"Error handling message for {subsystem}: {e}")
    
    @property
    def configuration(self):
        # Return a Signal object for configuration changes
        if not hasattr(self, '_configuration_signal'):
            self._configuration_signal = Signal()
        return self._configuration_signal
    
    @property
    def onsetup(self):
        return self._onsetup_signal
    
    @property
    def onstart(self):
        return self._onstart_signal
    
    @property
    def ondisconnected(self):
        return self._ondisconnected_signal
    
    @property
    def onconnected(self):
        return self._onconnected_signal
    
    @property
    def identity(self) -> str:
        return self._identity
    
    @property
    def connection(self) -> Connection:
        return self._connection
    
    @property
    def version(self) -> str:
        """Return a mock version string"""
        return "1.0.0-mock"
    
    def register(self, subsystem: str, handle_subsystem: Callable, handle_error: Callable = None):
        """Register a subsystem handler"""
        self._subsystems[subsystem] = handle_subsystem
        if handle_error:
            self._error_handlers[subsystem] = handle_error
        _log.debug(f"Registered subsystem handler: {subsystem}")