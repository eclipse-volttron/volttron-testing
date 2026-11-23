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
"""The zmq message bus adapter (volttron-lib-zmq).

It lives in volttron-testing, not in volttron-lib-zmq, because volttron-testing's own CI tests the wrapper on zmq: if
lib-zmq owned the adapter, a change to the adapter interface would break volttron-testing's tests until lib-zmq
released, and that release would need the new volttron-testing to test against. See
``docs/source/adapters.rst``.

lib-zmq is imported inside the methods, so registering and listing this adapter works without lib-zmq installed.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from volttrontesting.adapters import API_VERSION


class ZmqAdapter:
    api_version = API_VERSION
    name = "zmq"
    distributions = ("volttron-lib-zmq", )

    def __init__(self):
        self._builder = None

    @staticmethod
    def _store(home: Path):
        from volttron.auth.zap.credential_store import FileBasedCredentialStore
        return FileBasedCredentialStore(credentials_store_repository=Path(home) / "credentials_store")

    def write_credentials(self, home: Path, identity: str):
        from volttron.auth.zap.credentials_creator import VolttronCredentialsCreator
        from volttron.types.auth.auth_credentials import IdentityNotFound
        store = self._store(home)
        try:
            return store.retrieve_credentials(identity=identity)
        except (IdentityNotFound, KeyError):
            credentials = VolttronCredentialsCreator().create(identity)
            store.store_credentials(credentials=credentials)
            return credentials

    def read_credentials(self, home: Path, identity: str):
        return self._store(home).retrieve_credentials(identity=identity)

    def server_credentials(self, home: Path):
        from volttron.client.known_identities import PLATFORM
        from volttron.types.auth import VolttronCredentials
        return VolttronCredentials.load_from_file(Path(home) / "credentials_store" / f"{PLATFORM}.json")

    @contextmanager
    def core_builder(self):
        """Construct agents with this platform's ``ZmqCoreBuilder``, and leave core's builder cache as it was.

        ``Agent.__init__`` asks ``get_core_builder()`` for the process-wide cached builder. Once a mock-mode test has
        imported ``volttrontesting.mock_core_builder`` the registry holds two builders, and an unnamed lookup either
        cannot choose or hands a real agent a ``MockConnection``. The cache also wins over an explicit name, so a
        builder left in it by one test is what every later agent gets, real or mock (volttron-core#361). The adapter
        therefore installs its own builder only for the duration of the block and restores the previous cache; all
        of a platform's agents share that one builder.

        ``ZmqCoreBuilder`` also takes the server's public key from the ``CredentialsStore`` in core's service
        registry when one resolves (lib-zmq registers it on import), and the registry caches the first instance.
        With several platforms in one process, agents built for one got the key of whichever platform resolved first
        and never connected, so the cached instances are reset here, inside the caller's ``VOLTTRON_HOME``.
        """
        from volttron.client import decorators
        from volttron.server.containers import service_repo
        from volttron.messagebus.zmq.zmq_core import ZmqCoreBuilder
        service_repo.reset_instances()
        previous = decorators.__core_builder__
        if self._builder is None:
            # Constructed here rather than through get_core_builder(), whose caching differs between core releases
            # (one builder per process before volttron-core#361, one per name after): each platform needs its own.
            self._builder = ZmqCoreBuilder()
        decorators.__core_builder__ = self._builder
        try:
            yield
        finally:
            decorators.__core_builder__ = previous
