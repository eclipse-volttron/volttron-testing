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
"""The lib-auth adapter (volttron-lib-auth), and the ``none`` adapter for platforms with auth disabled.

The lib-auth adapter lives in volttron-testing for the same reason as the zmq adapter: volttron-testing's own CI runs
with lib-auth, so if lib-auth owned the adapter, an interface change would break volttron-testing's tests until
lib-auth released. See ``docs/source/adapters.rst``. lib-auth is imported inside the methods.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from volttrontesting.adapters import API_VERSION, NO_AUTH


class LibAuthAdapter:
    api_version = API_VERSION
    name = "libauth"
    distributions = ("volttron-lib-auth", )
    buses = None
    enabled = True

    def write_authz(self, home: Path, identity: str, admin: bool = False) -> None:
        import volttron.types.auth.authz_types as authz
        from volttron.auth.authz_manager import VolttronAuthzManager
        from volttron.server.server_options import ServerOptions

        manager = VolttronAuthzManager(options=ServerOptions(volttron_home=Path(home)))
        admin_caps = {}
        if admin:
            # The server's "admin" role, inlined because that role is not created until the server starts.
            admin_caps = dict(
                rpc_capabilities=authz.RPCCapabilities([authz.RPCCapability(resource="/.*/")]),
                pubsub_capabilities=authz.PubsubCapabilities(
                    [authz.PubsubCapability(topic_pattern="/.*/", topic_access="pubsub")]),
            )
        manager.create_or_merge_agent_authz(
            identity=identity,
            agent_roles=authz.AgentRoles([authz.AgentRole("default_rpc_capabilities",
                                                          param_restrictions={"identity": identity})]),
            comments=f"Registered by volttron-testing for {identity}",
            **admin_caps)

    def grant(self, platform, identity: str, rpc_capabilities: Sequence[str]) -> None:
        platform.vctl("authz", "add", "agent", identity, "--rpc-capabilities", *rpc_capabilities)


class NoAuthAdapter:
    """Platforms with ``auth-enabled = False``: nothing to register, every agent may do everything."""
    api_version = API_VERSION
    name = NO_AUTH
    distributions = ()
    buses = None
    enabled = False

    def write_authz(self, home: Path, identity: str, admin: bool = False) -> None:
        pass

    def grant(self, platform, identity: str, rpc_capabilities: Sequence[str]) -> None:
        pass
