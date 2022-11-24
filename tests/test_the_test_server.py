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

from volttrontesting import TestServer
from volttron.client import Agent

from volttrontesting.memory_pubsub import PublishedMessage


def test_instantiate():
    ts = TestServer()
    assert ts
    assert isinstance(ts, TestServer)
    # assert ts.config is not None
    # assert ts.config.vip_address[0] == 'tcp://127.0.0.1:22916'


def test_agent_subscription_and_logging():
    an_agent = Agent(identity="foo")
    ts = TestServer()
    assert ts

    log = logging.getLogger("an_agent_logger")
    ts.connect_agent(an_agent, log)

    on_messages_found = []

    def on_message(bus, topic, headers, message):
        on_messages_found.append(PublishedMessage(bus=bus, topic=topic, headers=headers, message=message))
        print(bus, topic, headers, message)
    log.debug("Hello World")
    log_message = ts.get_server_log()[0]
    assert log_message.level == logging.DEBUG
    assert log_message.message == "Hello World"
    subscriber = ts.subscribe('achannel')

    an_agent.vip.pubsub.subscribe(peer="pubsub", prefix='bnnel', callback=on_message)
    ts.publish('achannel', message="This is stuff sent through")
    ts.publish('bnnel', message="Second topic")
    ts.publish('bnnel/foobar', message="Third message")
    assert len(on_messages_found) == 2
    assert len(subscriber.received_messages()) == 1


def test_mock_rpc_call():
    pass
