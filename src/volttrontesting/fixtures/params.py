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
"""Fixture parameters for real platforms. Standard library, pytest and the adapter registry only, so the pytest
plugin can use it without importing the wrapper."""
import pytest

from volttrontesting import adapters

__all__ = ["platform_params"]


def platform_params() -> list:
    """Fixture parameters for real platforms: one per selected message bus, with the environment's auth module.

    Buses come from ``adapters.selected_buses()`` (``VOLTTRON_TEST_MESSAGEBUS``, else every available bus); the auth
    module is the one installed (``adapters.selected_auth()``), because the platform uses whichever is installed. A
    pair the auth module does not support is skipped, and an environment with no bus or no auth module yields one
    skipped parameter that says why. Ids read ``<bus>-<auth>``, for example ``zmq-libauth``.
    """
    try:
        buses = adapters.selected_buses()
        auth = adapters.selected_auth()
    except adapters.AdapterError as e:
        return [pytest.param(None, id="no-platform", marks=pytest.mark.skip(reason=str(e)))]
    if not buses:
        return [pytest.param(None, id="no-bus", marks=pytest.mark.skip(
            reason=f"no message bus available; registered: {', '.join(adapters.registered('bus'))}"))]
    params = []
    for bus in buses:
        marks = () if adapters.auth_supports(auth, bus) else \
            pytest.mark.skip(reason=f"auth module {auth!r} does not support message bus {bus!r}")
        params.append(pytest.param(dict(messagebus=bus, ssl_auth=True), id=f"{bus}-{auth}", marks=marks))
    return params
