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
"""Test utilities for VOLTTRON agents.

The top-level names are imported on first use, not when the package is imported: ``PlatformWrapper`` pulls in the
platform's server modules and ``grequests``, which gevent-patches ``socket`` and ``ssl`` as an import side effect.
Code that only needs ``volttrontesting.platform_cli`` or ``volttrontesting.harness`` must not pay for that.
"""
import importlib

__all__ = ["TestClient", "TestServer", "PlatformWrapper"]

_LAZY = {
    "TestClient": "volttrontesting.client_mock",
    "TestServer": "volttrontesting.server_mock",
    "PlatformWrapper": "volttrontesting.platformwrapper",
}


def __getattr__(name):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
