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
"""Adapters: what a white-box test needs from a message bus or an auth module that ``vctl`` cannot provide.

A platform runs on one message bus, chosen per platform by the config's ``messagebus`` key, and with one auth module,
whichever is installed (the platform has no setting that picks one, and two cannot be installed together because
both would register the ``platform.auth`` service). ``PlatformCLI`` needs nothing from either beyond the config,
except :meth:`AuthAdapter.grant`. ``PlatformWrapper`` builds agents in the test process, and for that it creates
credentials (the bus's job: lib-zmq owns the credential store) and registers authorization before the platform starts
(the auth module's job). Those steps are a :class:`MessageBusAdapter` and an :class:`AuthAdapter`.

Adapters are found through entry points: group ``volttrontesting.message_buses`` for buses and
``volttrontesting.auth_modules`` for auth modules; the entry name is the adapter's ``name``, the value its class.
volttron-testing's own ``zmq``, ``libauth`` and ``none`` (auth disabled) adapters are built in (:data:`BUILT_IN`);
another bus or auth module registers its adapter from its own distribution. ``docs/source/adapters.rst`` explains how to write one, and why the zmq and
lib-auth adapters live in volttron-testing.

This module uses the standard library only, and imports an adapter's module only when that adapter is asked for.
"""
from __future__ import annotations

import os
from contextlib import AbstractContextManager
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

__all__ = ["API_VERSION", "BUS_GROUP", "AUTH_GROUP", "BUS_ENV", "AUTH_ENV", "NO_AUTH", "AdapterError",
           "MessageBusAdapter", "AuthAdapter", "registered", "available", "adapter_class", "distributions",
           "selected_buses", "selected_auth", "auth_supports", "BUILT_IN"]

API_VERSION = 1
"""The adapter interface version, shared by both kinds. Raised when a method is added, removed or changes meaning; an
adapter declaring another version is reported as incompatible instead of being used."""

BUS_GROUP = "volttrontesting.message_buses"
AUTH_GROUP = "volttrontesting.auth_modules"

BUS_ENV = "VOLTTRON_TEST_MESSAGEBUS"
"""Comma-separated bus names that bus-parametrized fixtures run on. Unset: every available bus."""

AUTH_ENV = "VOLTTRON_TEST_AUTH"
"""The auth module the fixtures expect. Unset: the one available auth module."""

NO_AUTH = "none"
"""The auth adapter for platforms with ``auth-enabled = False``."""


class AdapterError(RuntimeError):
    pass


@runtime_checkable
class MessageBusAdapter(Protocol):
    """The bus-specific steps of building agents in the test process. One instance per platform.

    Implementations need not import volttron-testing: the protocol is structural, so any class with these attributes
    and methods works. ``home`` is always the platform's ``VOLTTRON_HOME``.
    """

    api_version: int
    """The :data:`API_VERSION` the adapter was written for."""

    name: str
    """The bus name: the platform config's ``messagebus`` value, and the entry point name."""

    distributions: tuple[str, ...]
    """Distributions that provide the bus. The bus is available when all are installed."""

    def write_credentials(self, home: Path, identity: str):
        """Create the credentials of ``identity`` in the platform's credential store, or return the existing ones.

        Used before the platform runs (for ``dynamic_agent``), and for every agent when auth is disabled. Returns the
        credentials object the bus's agent core accepts.
        """

    def read_credentials(self, home: Path, identity: str):
        """Return the stored credentials of ``identity``, after the platform's auth service created them."""

    def server_credentials(self, home: Path):
        """Return the platform's own credentials, read after it started. Agents need its public key to connect."""

    def core_builder(self) -> AbstractContextManager:
        """A context manager in which ``Agent(...)`` constructs this bus's agent core for this platform.

        Core picks an agent's core builder from a process-wide cache (volttron-core#361), so the adapter installs its
        builder for the duration of the block and restores the previous state afterwards.
        """


@runtime_checkable
class AuthAdapter(Protocol):
    """The auth-module-specific steps of registering agents. One instance per platform; structural like
    :class:`MessageBusAdapter`."""

    api_version: int
    name: str
    """The entry point name. Not a platform setting: the platform uses whichever auth module is installed."""

    distributions: tuple[str, ...]

    buses: tuple[str, ...] | None
    """Bus names this auth module works with; ``None`` for every bus. Fixtures skip other pairs."""

    enabled: bool
    """Whether platforms run with ``auth-enabled = True``. False only for the ``none`` adapter."""

    def write_authz(self, home: Path, identity: str, admin: bool = False) -> None:
        """Register the authorization of ``identity`` on disk before the platform starts, so the platform has it when
        the agent connects. ``admin`` grants every RPC and pubsub capability."""

    def grant(self, platform, identity: str, rpc_capabilities: Sequence[str]) -> None:
        """Add RPC capabilities to a running platform's agent. ``platform`` is a ``PlatformCLI``; use its ``vctl``."""


def _group(kind: str) -> str:
    if kind == "bus":
        return BUS_GROUP
    if kind == "auth":
        return AUTH_GROUP
    raise ValueError(f"adapter kind must be 'bus' or 'auth', not {kind!r}")


BUILT_IN = {
    "bus": {"zmq": "volttrontesting.adapters.zmq:ZmqAdapter"},
    "auth": {"libauth": "volttrontesting.adapters.auth:LibAuthAdapter",
             NO_AUTH: "volttrontesting.adapters.auth:NoAuthAdapter"},
}
"""volttron-testing's own adapters. They are listed here rather than as entry points so that they work however
volttron-testing is on the path (an editable install with stale metadata, a checkout on ``PYTHONPATH``). An entry
point with the same name replaces a built-in adapter, so a bus or auth module can take over its adapter later
without a volttron-testing change."""


def _entry_points(kind: str) -> dict[str, metadata.EntryPoint]:
    group = _group(kind)
    found = {name: metadata.EntryPoint(name, value, group) for name, value in BUILT_IN[kind].items()}
    found.update({ep.name: ep for ep in metadata.entry_points(group=group)})
    return found


def registered(kind: str) -> list[str]:
    """Names of every registered adapter of a kind (``"bus"`` or ``"auth"``), installed or not."""
    return sorted(_entry_points(kind))


def _installed(distribution: str) -> bool:
    try:
        metadata.distribution(distribution)
        return True
    except metadata.PackageNotFoundError:
        return False


@lru_cache(maxsize=None)
def _load(kind: str, name: str) -> type:
    entry = _entry_points(kind).get(name)
    if entry is None:
        raise AdapterError(f"no {kind} adapter registered as {name!r}; registered: "
                           f"{', '.join(registered(kind)) or 'none'}")
    cls = entry.load()
    version = getattr(cls, "api_version", None)
    if version != API_VERSION:
        raise AdapterError(f"the {kind} adapter {name!r} ({entry.value}) implements adapter API version {version}; "
                           f"volttron-testing needs {API_VERSION}")
    return cls


def distributions(kind: str, name: str) -> tuple[str, ...]:
    """The distributions that provide an adapter's bus or auth module, read without importing them."""
    return tuple(_load(kind, name).distributions)


def available(kind: str) -> list[str]:
    """Registered, compatible adapters of a kind whose distributions are all installed. For ``"auth"`` this
    excludes :data:`NO_AUTH`, which is always available."""
    names = []
    for name in registered(kind):
        if kind == "auth" and name == NO_AUTH:
            continue
        try:
            if all(_installed(d) for d in distributions(kind, name)):
                names.append(name)
        except (AdapterError, ImportError):
            continue
    return names


def adapter_class(kind: str, name: str) -> type:
    """The adapter class for an available bus or auth module; a clear error naming the available ones otherwise."""
    cls = _load(kind, name)
    missing = [d for d in cls.distributions if not _installed(d)]
    if missing:
        raise AdapterError(f"{kind} adapter {name!r} needs {', '.join(missing)}, which is not installed; "
                           f"available: {', '.join(available(kind)) or 'none'}")
    return cls


def selected_buses(environ=None) -> list[str]:
    """The buses bus-parametrized fixtures run on: :data:`BUS_ENV` if set, else every available bus.

    A name in :data:`BUS_ENV` that is not available is an error, so a CI job asking for a bus cannot pass by testing
    nothing.
    """
    value = (os.environ if environ is None else environ).get(BUS_ENV, "").strip()
    if not value:
        return available("bus")
    names = [n.strip() for n in value.split(",") if n.strip()]
    for name in names:
        adapter_class("bus", name)
    return names


def selected_auth(environ=None) -> str:
    """The auth module of this environment, for platforms with auth enabled.

    The platform uses whichever auth module is installed, so there is exactly one per environment. :data:`AUTH_ENV`,
    if set, names the one expected (an error if it is not the installed one). None installed, or several, is an
    error.
    """
    value = (os.environ if environ is None else environ).get(AUTH_ENV, "").strip()
    names = available("auth")
    if value:
        adapter_class("auth", value)
        if names != [value]:
            raise AdapterError(f"{AUTH_ENV}={value} but the installed auth modules are: {', '.join(names) or 'none'}")
        return value
    if len(names) != 1:
        raise AdapterError("platforms with auth enabled need exactly one installed auth module; found: "
                           f"{', '.join(names) or 'none'} (registered: {', '.join(registered('auth'))})")
    return names[0]


def auth_supports(auth: str, bus: str) -> bool:
    """Whether an auth module works with a bus, from the auth adapter's ``buses``."""
    buses = _load("auth", auth).buses
    return buses is None or bus in buses
