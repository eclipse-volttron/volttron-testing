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

import logging
import gevent

from volttrontesting.server_mock import TestServer
from volttron.client import Agent
from volttron.types.auth.auth_credentials import Credentials
from volttrontesting.mock_agent import MockAgent
from volttrontesting.memory_pubsub import PublishedMessage
from volttrontesting.pubsub_interceptor import intercept_agent_pubsub


def test_instantiate():
    ts = TestServer()
    assert ts
    assert isinstance(ts, TestServer)
    # assert ts.config is not None
    # assert ts.config.vip_address[0] == 'tcp://127.0.0.1:22916'


def test_agent_subscription_and_logging():
    # Use mock agent or specify name="mock" for Agent to use mock core
    try:
        # Try to create Agent with mock core
        an_agent = Agent(credentials=Credentials(identity="foo"), name="mock")
    except:
        # Fallback to MockAgent if Agent doesn't work
        an_agent = MockAgent(identity="foo")
    
    ts = TestServer()
    assert ts

    log = logging.getLogger("an_agent_logger")
    ts.connect_agent(an_agent, log)
    
    # Set up pubsub interception for the agent
    intercept_agent_pubsub(an_agent, TestServer.__server_pubsub__)

    on_messages_found = []

    def on_message(peer, sender, bus, topic, headers, message):
        on_messages_found.append(PublishedMessage(bus=bus, topic=topic, headers=headers, message=message))
        print(bus, topic, headers, message)
        
    log.debug("Hello World")
    log_message = ts.get_server_log()[0]
    assert log_message.level == logging.DEBUG
    assert log_message.message == "Hello World"
    
    # Subscribe through TestServer directly
    subscriber = ts.subscribe('achannel')

    # Subscribe through agent
    an_agent.vip.pubsub.subscribe(peer="pubsub", prefix='bnnel', callback=on_message)
    
    # Publish messages
    ts.publish('achannel', message="This is stuff sent through")
    ts.publish('bnnel', message="Second topic")
    ts.publish('bnnel/foobar', message="Third message")
    
    # Allow message propagation
    gevent.sleep(0.1)
    
    assert len(on_messages_found) == 2
    assert len(subscriber.received_messages()) == 1


def test_mock_rpc_call():
    pass
