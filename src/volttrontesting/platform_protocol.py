# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2026 Battelle Memorial Institute
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
"""One protocol for a platform under test, so a test body runs in process, on a real platform driven by vctl, or on
a real platform with agents built in the test process.

A test asks for the ``platform`` fixture (volttrontesting.plugin) and uses only the operations below. A backend that
cannot do one raises :class:`NotSupported`, which the fixture layer turns into a skip naming the backend.

Backends (volttrontesting.platform_backends): ``testserver`` (TestServerPlatform, in process), ``cli``
(CliPlatform, over PlatformCLI) and ``wrapper`` (WrapperPlatform, over PlatformWrapper).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from volttrontesting.memory_pubsub import PublishedMessage

__all__ = ["NotSupported", "Observer", "PlatformUnderTest", "PublishedMessage", "BACKENDS"]

BACKENDS = ("testserver", "cli", "wrapper")


class NotSupported(Exception):
    """The backend cannot perform this operation. The ``platform`` fixture skips the test, with this message."""

    def __init__(self, backend: str, operation: str, reason: str = ""):
        self.backend, self.operation, self.reason = backend, operation, reason
        super().__init__(f"{operation} is not supported on the {backend} backend" + (f": {reason}" if reason else ""))


class Observer:
    """The messages published on a prefix since ``subscribe``. Backends append to ``messages``."""

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.messages: list[PublishedMessage] = []

    def wait(self, count: int = 1, timeout: float = 30.0) -> list[PublishedMessage]:
        """Wait until at least ``count`` messages have arrived and return them all; TimeoutError otherwise."""
        import gevent
        deadline = time.monotonic() + timeout
        while len(self.messages) < count:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{len(self.messages)} of {count} messages on {self.prefix!r} within {timeout}s")
            gevent.sleep(0.05)
        return list(self.messages)

    def clear(self) -> None:
        self.messages.clear()


@runtime_checkable
class PlatformUnderTest(Protocol):
    """What a test may do with a platform. Topics of point operations are full device-point topics without the
    ``devices/`` prefix, as the Platform Driver's ``get``, ``set`` and ``revert`` take them."""

    backend: str                    # "testserver", "cli" or "wrapper"

    def install_agent(self, source: Any, identity: str, tag: str | None = None, *,
                      config: dict | str | Path | None = None) -> None:
        """Install and start an agent. ``source`` is what the backend can install: a directory, wheel or PyPI
        requirement on a real platform; on ``testserver`` an agent class, ``"module:Class"``, or a directory or
        installed distribution whose console script calls ``vip_main``."""

    def restart_agent(self, identity: str) -> None: ...

    def store_config(self, identity: str, name: str, content_or_path: Any, kind: str = "json") -> None:
        """Store a config like ``vctl config store``: a dict or list (JSON), a string, or a path to a file whose
        text is stored. ``kind`` is ``json``, ``csv`` or ``raw``."""

    def rpc(self, identity: str, method: str, *args, timeout: float = 30.0, **kwargs) -> Any: ...

    def get(self, topic: str) -> dict:
        """The Platform Driver's values for a topic or pattern: full topic -> value."""

    def get_point(self, topic: str) -> Any: ...

    def set_point(self, topic: str, value: Any) -> Any: ...

    def revert(self, topic: str) -> None: ...

    def subscribe(self, prefix: str) -> Observer: ...

    def publish(self, topic: str, headers: dict | None = None, message: Any = None) -> None: ...

    def log_size(self) -> int: ...

    def log_since(self, since: int) -> str: ...

    def wait_for_log(self, pattern: str, timeout: float = 90.0, since: int = 0) -> str: ...

    def shutdown(self) -> None: ...
