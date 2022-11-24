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
from dataclasses import dataclass, field
import inspect
from enum import Enum
import re
from logging import Logger
from typing import Dict, Callable, Any, Tuple, List, Optional

from gevent.event import AsyncResult

from volttrontesting.memory_pubsub import MemoryPubSub, MemorySubscriber, PublishedMessage

from volttron.client import Agent


@dataclass
class ServerConfig:
    vip_address: field(default_factory=list)


class LifeCycleMembers(Enum):
    onstart = "onstart"
    onsetup = "onsetup"
    onstop = "onstop"


@dataclass
class ServerResponse:
    identity: str
    called_method: str
    response: Any


@dataclass
class LogMessage:
    level: int | str
    message: str
    args: field(default_factory=list)
    kwargs: field(default_factory=dict)


@dataclass
class MessageWrapper:
    headers: field(default_factory=dict)
    message: field(default_factory=dict)
    topic: str


def __find_lifecycle_methods__(agent_class) -> List[Tuple[LifeCycleMembers, str]]:
    class_source = inspect.getsource(agent_class)
    core_names_found: List[Tuple[LifeCycleMembers, str]] = []
    for lcm in LifeCycleMembers:  # .enum_members().items():
        # Search for @Core.receiver('onstart')
        # handle cases for weird spacing and multiple lines
        term = f"@Core.receiver\s*\(\s*['\"]{lcm.value}['\"]\s*\)\s*"
        m = re.search(term, class_source, re.MULTILINE)

        # find the actual function following this
        if m is not None:
            # Subsource is all the code after the match
            subsource = class_source[m.start():]
            # We know that the receiver is decorated on the function so we know
            # that it starts with def and ends with
            m2 = re.search("def\s+.*:$", subsource, re.MULTILINE)
            m3 = re.search("[a-zA-Z_]+[a-zA-Z_0-9]*\(", m2[0], re.MULTILINE)
            # This is the data we truly want so we can look it up on the members
            # to find an instance of the callable method.
            function_name = m2[0][m3.start():m3.end() - 1]
            core_names_found.append((lcm, function_name))

    return core_names_found


def __execute_lifecycle_method__(identity: str,
                                 lifecycle_method: LifeCycleMembers,
                                 members: Dict[LifeCycleMembers, Callable],
                                 sender: str, **kwargs) -> ServerResponse:
    fn = members.get(lifecycle_method)
    if fn is None:
        raise ValueError(f"{lifecycle_method.name} lifecycle method is not found in agent {identity}")
    resp = fn(sender, **kwargs)
    print(resp)
    return ServerResponse(identity, fn.__name__, resp)


class TestServer:
    __test__ = False
    __connected_agents__: Dict[str, Agent]
    __lifecycle_methods__: Dict[str, Dict[LifeCycleMembers, Callable]]
    __methods__: Dict[str, Callable]
    __server_pubsub__: MemoryPubSub
    __pubsub_wrappers__: Dict[str, PubSubWrapper]

    def __new__(cls, *args, **kwargs):
        TestServer.__connected_agents__ = {}
        TestServer.__lifecycle_methods__ = {}
        TestServer.__methods__ = {}
        TestServer.__pubsub_wrappers__ = {}
        TestServer.__server_pubsub__ = MemoryPubSub()
        TestServer.__server_log__ = ServerLogWrapper()
        return super(TestServer, cls).__new__(cls)

    def __init__(self):
        self._subscribers: List[MemorySubscriber] = []

    @property
    def config(self) -> ServerConfig:
        return self._config

    @config.setter
    def config(self, config: ServerConfig):
        self._config = config

    def _trigger_dispatch(self):
        for s in self.__pubsub_wrappers__.values():
            for p in s._subscriptions.values():
                try:
                    msg = next(p.anysub_subscriber)
                    print(msg)
                except StopIteration:
                    pass

    def subscribe(self, prefix: str, callback: Optional[Callable] = None) -> MemorySubscriber:
        subscriber = self.__server_pubsub__.subscribe(prefix, callback)
        self._subscribers.append(subscriber)
        return subscriber

    def publish(self, topic: str, headers: Optional[Dict[str, Any]] = None, message: Optional[Any] = None,
                bus: str = ''):
        self.__server_pubsub__.publish(topic, headers=headers, message=message, bus=bus)

    def get_published_messages(self) -> List[PublishedMessage]:
        return self.__server_pubsub__.published_messages

    def get_server_log(self) -> List[LogMessage]:
        return self.__server_log__.log_queue

    def __check_connected__(self, identity: str):
        """
        Raises ValueError if an agent hasn't been connected.  This method should
        be called any time a dependency of self._agent is necessary.

        :return:
        """
        if not self.__connected_agents__.get(identity):
            # TODO inspect the stack to get the metod that called this one.
            raise ValueError("connect_agent must be called before the called method")

    def __get_lifecycle_members__(self,
                                  identity_or_agent: [str, Agent]) -> Tuple[str, Dict[LifeCycleMembers, Callable]]:
        identity = identity_or_agent
        if isinstance(identity_or_agent, Agent):
            identity = identity_or_agent.core.identity
        self.__check_connected__(identity)
        # Make sure there is a setup function defined on the agent.
        members = self.__lifecycle_methods__.get(identity)
        if members is None:
            raise ValueError(f"Lifecycle methods not populated for agent: ({identity})")
        return identity, members

    def trigger_setup_event(self, identity_or_agent: [str, Agent], sender: str = '', **kwargs) -> ServerResponse:
        """
        Executes the @Core.receiver('onsetup') marked method, if it was found on the
        connected agent.

        :param identity_or_agent:
        :param sender:
        :param kwargs:
        :return:
        """
        identity, members = self.__get_lifecycle_members__(identity_or_agent)
        resp = __execute_lifecycle_method__(identity, LifeCycleMembers.onsetup,
                                            members=members, sender=sender, **kwargs)
        return resp

    def trigger_start_event(self, identity_or_agent: [str, Agent], sender: str = '', **kwargs) -> ServerResponse:
        """
        Executes the @Core.receiver('onstart') marked method, if it was found on the
        connected agent.

        :param identity_or_agent:
        :param sender:
        :param kwargs:
        :return:
        """
        identity, members = self.__get_lifecycle_members__(identity_or_agent)
        resp = __execute_lifecycle_method__(identity, LifeCycleMembers.onstart,
                                            members=members, sender=sender, **kwargs)
        return resp

    def trigger_stop_event(self, identity_or_agent: [str, Agent], sender: str, **kwargs) -> ServerResponse:
        """
        Executes the @Core.receiver('onstop') marked method, if it was found on the
        connected agent.

        :param identity_or_agent:
        :param sender:
        :param kwargs:
        :return:
        """
        identity, members = self.__get_lifecycle_members__(identity_or_agent)
        resp = __execute_lifecycle_method__(identity, LifeCycleMembers.onstop,
                                            members=members, sender=sender, **kwargs)
        return resp

    def connect_agent(self, agent: Agent, logger: Optional[Logger] = None):
        """
        The connect_agent function sets up the server to work with this agent.  This method
        will parse the source of the agent looking for key features such as lifecycle methods,
        pubsub decorators etc. and create events for executing them.

        :param agent:
        :param logger:
        """
        if not agent.core.identity:
            raise ValueError("Agent identity must be set to use this test server.")

        if agent.core.identity in self.__connected_agents__:
            raise ValueError(f"Agent {agent.core.identity} is already on server.")

        self.__connected_agents__[agent.core.identity] = agent

        for name, obj in inspect.getmembers(agent):

            # populate hooks for callback metadata for the class object.
            if name == '__class__':
                core_names_found = __find_lifecycle_methods__(obj)
                self.__lifecycle_methods__[agent.core.identity] = self.__get_lifecycle_dict__(agent, core_names_found)

        if PubSubWrapper.__wrapper__ is None:
            PubSubWrapper.__wrapper__ = self.__server_pubsub__
        self.__pubsub_wrappers__[agent.core.identity] = PubSubWrapper(agent, self)
        self.__server_log__.add_agent_log(agent, logger)

    def __get_lifecycle_dict__(self, agent, core_names_found) -> Dict[LifeCycleMembers, Callable]:
        # Loop over the found lifecycle functions and find the callable associated with it.
        lcm: Dict[LifeCycleMembers, Callable] = {}
        for x, y in core_names_found:
            for m in inspect.getmembers(agent):
                if m[0] == y:
                    lcm[x] = m[1]
                    break
        return lcm


class SubSystemWrapper:
    pass


class ServerLogWrapper:
    def __init__(self):
        self._agent_log: Dict[str, Logger] = {}
        self._log_messages: List[LogMessage] = []

    @property
    def log_queue(self) -> List[LogMessage]:
        return self._log_messages

    def add_agent_log(self, identity_or_agent: [str, Agent], logger: Logger):
        # TODO modify passed logger to handle the different fn for that
        identity = identity_or_agent
        if isinstance(identity_or_agent, Agent):
            identity = identity_or_agent.core.identity

        def wrapper(level) -> Callable:
            def fn_wrapper(msg, *args, **kwargs):
                self._log_messages.append(LogMessage(level=level, message=msg, args=args, kwargs=kwargs))
            return fn_wrapper
        if logger is None:
            logger = logging.getLogger()
        logger.debug = wrapper(logging.DEBUG)
        logger.info = wrapper(logging.INFO)
        logger.error = wrapper(logging.ERROR)
        logger.warning = wrapper(logging.WARNING)

        self._agent_log[identity] = logger


@dataclass
class Subscription:
    prefix: str
    callback: Callable
    anysub_subscriber: MemorySubscriber


class HeartBeatWrapper(SubSystemWrapper):
    pass


class PubSubWrapper(SubSystemWrapper):
    __wrapper__: MemoryPubSub | None = None

    def __init__(self, agent: Agent, server: TestServer):
        super().__init__()
        self._test_server = server
        self._subscriptions: Dict[str, List[Subscription]] = {}
        agent.vip.pubsub.publish = self._do_publish
        agent.vip.pubsub.subscribe = self._do_subscribe

    def _on_message(self, bus, topic, headers, message):
        print("on message")

    def _do_publish(self, peer: str, topic: str, headers=None, message=None, bus=""):
        self.__wrapper__.publish(topic=topic, headers=headers, message=message)
        result = AsyncResult()
        result.set(topic)
        return result

    def _do_subscribe(self, peer, prefix, callback, bus="", all_platforms=False, persistent_queue=None):
        anysub = self._test_server.subscribe(prefix, callback=callback)
        subscription = Subscription(prefix, callback=callback, anysub_subscriber=anysub)
        if prefix in self._subscriptions:
            self._subscriptions[prefix].append(subscription)
        else:
            self._subscriptions[prefix] = [subscription]
