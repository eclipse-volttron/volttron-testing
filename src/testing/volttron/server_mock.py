from dataclasses import dataclass, field
import inspect
from enum import Enum
import re
from typing import Dict, Callable, Any, Tuple, List, Type

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
    called_method: str
    response: Any


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


class TestServer:

    def __init__(self):
        self._config = ServerConfig(vip_address=["tcp://127.0.0.1:22916"])
        self._agent = None
        self._agent_methods = None
        self._agent_core = None
        self._agent_vip = None
        self._life_cycle_methods: Dict[LifeCycleMembers, Callable] = {}

    @property
    def config(self) -> ServerConfig:
        return self._config

    @config.setter
    def config(self, config: ServerConfig):
        self._config = config

    def trigger_setup_event(self, sender, **kwargs) -> ServerResponse:
        fn = self._life_cycle_methods.get(LifeCycleMembers.onsetup)
        if fn is None:
            raise ValueError(f"onsetup lifecycle method is not found in agent {self._agent}")

        resp = fn(sender, **kwargs)
        print(resp)
        return ServerResponse(fn.__name__, resp)

    def connect_agent(self, agent: Agent):
        self._agent = agent

        for name, obj in inspect.getmembers(self._agent):

            # populate hooks for callback metadata for the class object.
            if name == '__class__':
                core_names_found = __find_lifecycle_methods__(obj)
                self.__populate_lifecycle_dict__(core_names_found)
            elif name == 'vip':
                self._vip = obj

    def __populate_lifecycle_dict__(self, core_names_found):
        # Loop over the found lifecycle functions and find the callable associated with it.
        for x, y in core_names_found:
            for m in inspect.getmembers(self._agent):
                if m[0] == y:
                    self._life_cycle_methods[x] = m[1]
                    break
