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
from typing import Optional

from volttron.client import Agent
from volttron.client.vip.agent.subsystems import PeerList, Ping, RPC, Hello, Health, Heartbeat, ConfigStore, Auth
from volttron.types.auth.auth_credentials import Credentials
from volttron.types.agent_context import AgentOptions

from volttrontesting.mock_pubsub_subsystem import MockPubSubSubsystem
from volttrontesting.memory_pubsub import MemoryPubSub

_log = logging.getLogger(__name__)


class MockSubsystems:
    """Custom Subsystems class that uses MockPubSubSubsystem"""
    
    def __init__(self, *, owner: Agent, core, options: AgentOptions):
        self.peerlist = PeerList(core=core)
        self.ping = Ping(core)
        self.rpc = RPC(core=core, owner=owner, peerlist_subsys=self.peerlist)
        self.hello = Hello(core=core)
        
        # Use our mock pubsub instead of the real one
        self.pubsub = MockPubSubSubsystem(
            core=core,
            peerlist_subsys=self.peerlist,
            rpc_subsys=self.rpc,
            owner=self,
            tag_vip_id=options.tag_vip_id if options else None,
            tag_refresh_interval=options.tag_refresh_interval if options else -1
        )
        
        self.health = Health(owner=owner, core=core, rpc=self.rpc)
        self.heartbeat = Heartbeat(
            owner,
            core,
            rpc=self.rpc,
            pubsub=self.pubsub,
            heartbeat_autostart=options.heartbeat_autostart if options else False,
            heartbeat_period=options.heartbeat_period if options else 60
        )
        self.config = ConfigStore(owner, core, self.rpc)
        self.auth = Auth(owner, core, self.rpc)


class MockIntegratedAgent(Agent):
    """Agent class that uses mock subsystems with integrated pubsub"""
    
    def __init__(self, identity: str = None, credentials: Credentials = None, 
                 test_server_pubsub: MemoryPubSub = None, **kwargs):
        """
        Initialize a mock agent with integrated pubsub.
        
        :param identity: Agent identity
        :param credentials: Agent credentials (if not provided, creates from identity)
        :param test_server_pubsub: The test server's MemoryPubSub to integrate with
        :param kwargs: Additional arguments for Agent
        """
        # Set the test server pubsub for routing
        if test_server_pubsub:
            MockPubSubSubsystem.set_test_server_pubsub(test_server_pubsub)
        
        # Create credentials if not provided
        if not credentials and identity:
            credentials = Credentials(identity=identity)
        
        # Initialize with mock core
        kwargs['name'] = 'mock'
        super().__init__(credentials=credentials, **kwargs)
        
        # Replace the subsystems with our mock version after initialization
        # We need to do this after super().__init__ because Agent creates its own
        options = kwargs.get('options', AgentOptions())
        self.vip = MockSubsystems(owner=self, core=self.core, options=options)
        
        # Re-export the version RPC
        self.vip.rpc.export(self.core.version, "agent.version")
        
        _log.info(f"MockIntegratedAgent initialized: {identity}")


def create_mock_agent(identity: str, test_server_pubsub: MemoryPubSub = None, 
                     agent_class=None, **kwargs) -> Agent:
    """
    Factory function to create a mock agent with integrated pubsub.
    
    :param identity: Agent identity
    :param test_server_pubsub: The test server's MemoryPubSub to integrate with
    :param agent_class: Optional custom agent class (must inherit from MockIntegratedAgent)
    :param kwargs: Additional arguments for the agent
    :return: Configured mock agent
    """
    if agent_class is None:
        agent_class = MockIntegratedAgent
    elif not issubclass(agent_class, Agent):
        raise TypeError("agent_class must inherit from Agent")
    
    # For custom agent classes, we need to mix in the mock functionality
    if agent_class != MockIntegratedAgent:
        # Create a dynamic class that inherits from both
        class CustomMockAgent(agent_class):
            def __init__(self, **init_kwargs):
                # Set test server pubsub before initialization
                if test_server_pubsub:
                    MockPubSubSubsystem.set_test_server_pubsub(test_server_pubsub)
                
                # Call the parent constructor
                super().__init__(**init_kwargs)
                
                # Replace subsystems with mock version
                options = init_kwargs.get('options', AgentOptions())
                self.vip = MockSubsystems(owner=self, core=self.core, options=options)
                self.vip.rpc.export(self.core.version, "agent.version")
        
        return CustomMockAgent(identity=identity, name='mock', **kwargs)
    
    return agent_class(identity=identity, test_server_pubsub=test_server_pubsub, **kwargs)