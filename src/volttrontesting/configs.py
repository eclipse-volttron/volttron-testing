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
"""Configuration and agent-source helpers shared by platform-level tests.

:class:`AgentSources` turns a short name such as ``"platform-driver"`` into something ``vctl install`` accepts: the
source checkout when one can be found, otherwise the published distribution name. A test therefore says what it
needs, not where it lives, and runs the same against a developer's checkouts and against PyPI in CI.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

__all__ = ["AgentSpec", "SourceEntry", "AgentSources", "editable_source"]


@dataclass(frozen=True)
class AgentSpec:
    """One agent to install: ``source`` is a short name known to :class:`AgentSources`, a path, or a PyPI name."""
    source: str
    identity: str
    tag: str
    config: str | Path | None = None


@dataclass(frozen=True)
class SourceEntry:
    """Where a component's checkout may be found.

    ``distribution`` is the installed/published distribution name. ``tree`` is ``(VARIABLE, subpath)``: the
    environment variable naming a directory of checkouts and the checkout below it. ``monorepo`` is the checkout's
    path below a VOLTTRON modular checkout root. ``published`` is False for components with no PyPI release, which then
    must be found as a checkout.
    """
    distribution: str | None = None
    tree: tuple[str, str] | None = None
    monorepo: str | None = None
    published: bool = True


def editable_source(distribution: str) -> Path | None:
    """The directory a distribution was installed from in editable mode, if it was."""
    try:
        direct_url = metadata.distribution(distribution).read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    info = json.loads(direct_url) if direct_url else {}
    if info.get("dir_info", {}).get("editable"):
        path = Path(info["url"].removeprefix("file://"))
        return path if path.is_dir() else None
    return None


def _driver(dist: str, sub: str) -> SourceEntry:
    return SourceEntry(dist, ("VOLTTRON_MODULAR_DRIVERS", sub), f"drivers/{sub}")


DEFAULT_ENTRIES: dict[str, SourceEntry] = {
    "platform-driver": _driver("volttron-platform-driver", "platform-driver-agent"),
    "fake-driver": _driver("volttron-lib-fake-driver", "interfaces/fake"),
    "modbus-driver": _driver("volttron-lib-modbus-driver", "interfaces/pymodbus"),
    "modbustk-driver": _driver("volttron-lib-modbustk-driver", "interfaces/modbustk"),
    "bacnet-driver": _driver("volttron-lib-bacnet-driver", "interfaces/bacnet"),
    "dnp3-driver": _driver("volttron-lib-dnp3-driver", "interfaces/dnp3"),
    "ieee2030_5-driver": _driver("volttron-lib-ieee2030_5-driver", "interfaces/ieee2030_5"),
    "listener": SourceEntry("volttron-listener"),
}


@dataclass
class AgentSources:
    """Resolve short names to ``vctl install`` sources.

    Resolution order for a known name:

    1. the ``tree`` environment variable (``VOLTTRON_MODULAR_DRIVERS`` for the drivers) when the checkout exists;
    2. the checkout the distribution is installed editable from in the running environment;
    3. the checkout below the modular root: ``VOLTTRON_MODULAR``, or the nearest ancestor of ``anchor`` (pass
       ``__file__`` from a conftest) holding both ``base/`` and ``drivers/``;
    4. the distribution name, so ``vctl install`` fetches it from PyPI, if the component is published.

    Unknown names, paths and plain PyPI names pass through unchanged. :meth:`register` adds project-specific entries.
    """
    anchor: str | Path | None = None
    entries: dict[str, SourceEntry] = field(default_factory=lambda: dict(DEFAULT_ENTRIES))

    def register(self, name: str, *, distribution: str | None = None, tree: tuple[str, str] | None = None,
                 monorepo: str | None = None, published: bool = True) -> "AgentSources":
        self.entries[name] = SourceEntry(distribution, tree, monorepo, published)
        return self

    def modular_root(self) -> Path | None:
        value = os.environ.get("VOLTTRON_MODULAR")
        if value:
            path = Path(value).expanduser()
            return path if path.is_dir() else None
        if self.anchor is None:
            return None
        for parent in Path(self.anchor).resolve().parents:
            if (parent / "base").is_dir() and (parent / "drivers").is_dir():
                return parent
        return None

    def candidates(self, name: str) -> list[tuple[str, Path]]:
        """The checkout locations tried for ``name``, in order, as ``(how, path)``."""
        entry = self.entries.get(name)
        if entry is None:
            return []
        found: list[tuple[str, Path]] = []
        if entry.tree is not None:
            variable, sub = entry.tree
            value = os.environ.get(variable)
            if value:
                found.append((f"${variable}", Path(value).expanduser() / sub))
        if entry.distribution is not None:
            editable = editable_source(entry.distribution)
            if editable is not None:
                found.append((f"editable {entry.distribution}", editable))
        root = self.modular_root()
        if root is not None and entry.monorepo is not None:
            found.append(("modular checkout", root / entry.monorepo))
        return found

    def checkout(self, name: str) -> Path | None:
        """The first existing checkout for ``name`` (for data files that only a source tree has), or None."""
        for _, path in self.candidates(name):
            if (path / "pyproject.toml").exists():
                return path
        return None

    def resolve(self, name: str) -> str:
        if name not in self.entries:
            return name
        path = self.checkout(name)
        if path is not None:
            return str(path)
        entry = self.entries[name]
        if entry.published and entry.distribution is not None:
            return entry.distribution
        tried = ", ".join(f"{how}: {path}" for how, path in self.candidates(name)) or "no locations configured"
        raise LookupError(f"no checkout of {name!r} found ({tried}) and it has no published distribution")
