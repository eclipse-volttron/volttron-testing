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
"""The adapter registry, with stub adapters registered the way a third-party bus or auth module registers its own:
an entry point in an installed distribution (here a throwaway ``*.dist-info`` on ``sys.path``)."""
import textwrap

import pytest

from volttrontesting import adapters
from volttrontesting.fixtures.volttron_platform_fixtures import platform_params
from volttrontesting.platform_cli import PlatformCLI

STUB_MODULE = '''
from contextlib import nullcontext

class StubBus:
    api_version = 1
    name = "stub"
    distributions = ("stub-adapters",)
    def write_credentials(self, home, identity): return identity
    def read_credentials(self, home, identity): return identity
    def server_credentials(self, home): return "server"
    def core_builder(self): return nullcontext()

class OldBus(StubBus):
    api_version = 0
    name = "old"

class AbsentBus(StubBus):
    name = "absent"
    distributions = ("no-such-distribution-anywhere",)

class StubAuth:
    api_version = 1
    name = "stubauth"
    distributions = ("stub-adapters",)
    buses = ("stub",)
    enabled = True
    def write_authz(self, home, identity, admin=False): pass
    def grant(self, platform, identity, rpc_capabilities): platform.vctl("stub-grant", identity, *rpc_capabilities)

class ReplacementZmq(StubBus):
    name = "zmq"
    distributions = ()
'''


@pytest.fixture
def stub_distribution(tmp_path, monkeypatch):
    """Install, for this test only, a distribution ``stub-adapters`` whose entry points register the stubs."""
    def install(bus_entries: dict, auth_entries: dict = None):
        (tmp_path / "stub_adapters.py").write_text(STUB_MODULE)
        info = tmp_path / "stub_adapters-1.0.dist-info"
        info.mkdir(exist_ok=True)
        (info / "METADATA").write_text("Metadata-Version: 2.1\nName: stub-adapters\nVersion: 1.0\n")
        lines = [f"[{adapters.BUS_GROUP}]", *(f"{n} = stub_adapters:{c}" for n, c in bus_entries.items())]
        if auth_entries:
            lines += [f"[{adapters.AUTH_GROUP}]", *(f"{n} = stub_adapters:{c}" for n, c in auth_entries.items())]
        (info / "entry_points.txt").write_text("\n".join(lines) + "\n")
        monkeypatch.syspath_prepend(str(tmp_path))
        adapters._load.cache_clear()
    yield install
    adapters._load.cache_clear()


@pytest.fixture(autouse=True)
def clean_selection(monkeypatch):
    monkeypatch.delenv(adapters.BUS_ENV, raising=False)
    monkeypatch.delenv(adapters.AUTH_ENV, raising=False)


def test_built_in_adapters_satisfy_the_protocols():
    assert {"zmq"} <= set(adapters.registered("bus"))
    assert {"libauth", adapters.NO_AUTH} <= set(adapters.registered("auth"))
    assert isinstance(adapters.adapter_class("bus", "zmq")(), adapters.MessageBusAdapter)
    for name in ("libauth", adapters.NO_AUTH):
        assert isinstance(adapters.adapter_class("auth", name)(), adapters.AuthAdapter)
    assert not adapters.adapter_class("auth", adapters.NO_AUTH).enabled


def test_a_registered_bus_is_found_selected_and_parametrized(stub_distribution, monkeypatch):
    stub_distribution({"stub": "StubBus"})
    assert "stub" in adapters.available("bus")
    assert isinstance(adapters.adapter_class("bus", "stub")(), adapters.MessageBusAdapter)
    monkeypatch.setenv(adapters.BUS_ENV, "stub")
    assert adapters.selected_buses() == ["stub"]
    assert [p.id for p in platform_params()] == [f"stub-{adapters.selected_auth()}"]


def test_an_incompatible_or_uninstalled_adapter_is_not_available(stub_distribution):
    stub_distribution({"old": "OldBus", "absent": "AbsentBus"})
    assert {"old", "absent"} <= set(adapters.registered("bus"))
    assert not {"old", "absent"} & set(adapters.available("bus"))
    with pytest.raises(adapters.AdapterError, match="API version 0"):
        adapters.adapter_class("bus", "old")
    with pytest.raises(adapters.AdapterError, match="no-such-distribution-anywhere.*available: "):
        adapters.adapter_class("bus", "absent")
    assert any("absent" in m for m in PlatformCLI.missing(message_bus="absent"))


def test_an_unknown_bus_names_the_registered_ones(monkeypatch):
    with pytest.raises(adapters.AdapterError, match="registered: .*zmq"):
        adapters.adapter_class("bus", "carrier-pigeon")
    monkeypatch.setenv(adapters.BUS_ENV, "carrier-pigeon")
    with pytest.raises(adapters.AdapterError):
        adapters.selected_buses()
    assert [p.id for p in platform_params()] == ["no-platform"]


def test_an_entry_point_replaces_a_built_in_adapter(stub_distribution):
    stub_distribution({"zmq": "ReplacementZmq"})
    assert adapters.adapter_class("bus", "zmq").__name__ == "ReplacementZmq"


def test_one_auth_module_per_environment(stub_distribution, monkeypatch):
    stub_distribution({}, {"stubauth": "StubAuth"})
    # lib-auth and the stub are both "installed" now: the platform could not tell which to use.
    with pytest.raises(adapters.AdapterError, match="exactly one installed auth module"):
        adapters.selected_auth()
    assert [p.id for p in platform_params()] == ["no-platform"]
    monkeypatch.setenv(adapters.AUTH_ENV, "stubauth")
    with pytest.raises(adapters.AdapterError, match="installed auth modules are"):
        adapters.selected_auth()


def test_unsupported_bus_and_auth_pairs_are_skipped(stub_distribution, monkeypatch):
    stub_distribution({"stub": "StubBus"}, {"stubauth": "StubAuth"})
    assert adapters.auth_supports("stubauth", "stub") and not adapters.auth_supports("stubauth", "zmq")
    assert adapters.auth_supports("libauth", "stub")
    monkeypatch.setattr(adapters, "selected_auth", lambda environ=None: "stubauth")
    monkeypatch.setenv(adapters.BUS_ENV, "zmq,stub")
    params = {p.id: p for p in platform_params()}
    assert set(params) == {"zmq-stubauth", "stub-stubauth"}
    assert params["zmq-stubauth"].marks and not params["stub-stubauth"].marks


def test_grant_goes_through_the_auth_adapter(tmp_path, monkeypatch, stub_distribution):
    calls = []
    monkeypatch.setattr(PlatformCLI, "vctl", lambda self, *args, **kw: calls.append(args) or "")
    PlatformCLI(tmp_path / "off", auth_enabled=False).grant("a.b", "x.y")
    assert calls == []
    stub_distribution({}, {"stubauth": "StubAuth"})
    monkeypatch.setattr(adapters, "selected_auth", lambda environ=None: "stubauth")
    PlatformCLI(tmp_path / "on").grant("a.b", "x.y")
    assert calls == [("stub-grant", "a.b", "x.y")]
