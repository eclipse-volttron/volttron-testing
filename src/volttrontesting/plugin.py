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
"""pytest plugin: real-platform fixtures, markers and options for any repository that installs volttron-testing.

Registered as the ``volttrontesting.plugin`` entry point in the ``pytest11`` group, so it loads automatically; a
repository can also name it with ``-p volttrontesting.plugin``, which is harmless when it is already loaded. Loading
it imports nothing heavy: the fixtures import ``PlatformCLI`` when they run, and nothing here imports
``PlatformWrapper`` (whose import gevent-patches the process). The legacy wrapper fixtures (``volttron_instance``,
``platforms``, ...) stay opt-in through ``from volttrontesting.fixtures.volttron_platform_fixtures import *``.

Fixtures (scope ``module`` by default; see ``volttron_platform_scope``):

``platform_cli``
    A started :class:`~volttrontesting.platform_cli.PlatformCLI`. Configure it by overriding the session-scoped
    ``platform_cli_options`` fixture, which returns ``PlatformCLI`` keyword arguments.
``web_platform``
    A separate started ``PlatformCLI`` with the web service and a web user (``web_platform.web_user``). Options from
    ``web_platform_options``, the user from ``web_user``.
``platform_driver``
    The Platform Driver installed on ``platform_cli``, as a :class:`DriverHandle`. ``install_platform_driver(cli)``
    does the same for any platform, for example ``web_platform``.
``certs``
    A :class:`~volttrontesting.certs.CertificateSet` in a temporary directory (function scope).

Each platform fixture runs once per selected message bus (``--messagebus`` or ``VOLTTRON_TEST_MESSAGEBUS``, default
every available bus) and skips, with the reason, when ``VOLTTRON_TESTING_SKIP_PLATFORM`` is set or the environment
cannot start a platform. Tests that use one are marked ``platform``, so ``-m "not platform"`` deselects them.
"""
from __future__ import annotations

import csv
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import pytest

__all__ = ["DriverHandle", "WebUser", "install_platform_driver", "platform_scope", "require_platform", "SKIP_ENV",
           "SCOPE_OPTION", "PLATFORM_FIXTURES"]

SKIP_ENV = "VOLTTRON_TESTING_SKIP_PLATFORM"
SCOPE_OPTION = "volttron_platform_scope"
SCOPES = ("module", "package", "session")
PLATFORM_FIXTURES = ("platform_cli", "web_platform", "platform_driver")
# Tests using these legacy wrapper fixtures also start real platforms, so they are marked ``platform`` too.
LEGACY_PLATFORM_FIXTURES = ("volttron_instance", "platforms")


# ---------------------------------------------------------------------------------------------------- hooks
def pytest_addoption(parser):
    group = parser.getgroup("volttron", "VOLTTRON platform tests (volttron-testing)")
    group.addoption("--messagebus", dest="volttron_messagebus", default=None,
                    help="comma-separated message buses the platform fixtures run on (default: every available "
                    "bus; same as VOLTTRON_TEST_MESSAGEBUS)")
    group.addoption("--volttron-platform-scope", dest="volttron_platform_scope", choices=SCOPES, default=None,
                    help=f"scope of the platform fixtures (default: the {SCOPE_OPTION} ini value, else module)")
    parser.addini(SCOPE_OPTION, f"scope of the volttron-testing platform fixtures: {', '.join(SCOPES)}",
                  default="module")


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    """Apply ``--messagebus`` before conftests are imported, because the legacy fixtures read the selection when
    their module is imported."""
    bus = getattr(early_config.known_args_namespace, "volttron_messagebus", None)
    if bus:
        from volttrontesting.adapters import BUS_ENV
        os.environ[BUS_ENV] = bus


def pytest_configure(config):
    config.addinivalue_line("markers", f"platform: starts a real VOLTTRON platform (set {SKIP_ENV}=1 to skip)")
    config.addinivalue_line("markers", "docker: needs docker and the images the test names")
    config.addinivalue_line("markers", "slow: takes minutes")
    platform_scope("platform_cli", config)    # reject a bad scope value at startup, not at the first fixture


def pytest_collection_modifyitems(config, items):
    names = set(PLATFORM_FIXTURES) | set(LEGACY_PLATFORM_FIXTURES)
    for item in items:
        if names & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.platform)


def pytest_generate_tests(metafunc):
    """Run each platform fixture once per selected message bus (indirect parametrization, so the selection can come
    from the command line)."""
    for name in ("platform_cli", "web_platform"):
        if name in metafunc.fixturenames:
            if any(m.args and m.args[0] == name for m in metafunc.definition.iter_markers("parametrize")):
                continue    # the test parametrizes it itself
            metafunc.parametrize(name, _platform_params(metafunc.config), indirect=True,
                                 scope=platform_scope(name, metafunc.config))


_PARAMS = pytest.StashKey[list]()


def _platform_params(config) -> list:
    """The bus parameters, computed once per session: pytest reuses a wider-scoped parametrized fixture only when
    the next test's parameter is the same object, so every module must get the same list."""
    if _PARAMS not in config.stash:
        from volttrontesting.fixtures.params import platform_params
        config.stash[_PARAMS] = platform_params()
    return config.stash[_PARAMS]


# ---------------------------------------------------------------------------------------------------- helpers
def platform_scope(fixture_name: str, config) -> str:
    """The platform fixtures' scope: ``--volttron-platform-scope``, else the ``volttron_platform_scope`` ini value,
    else ``module``. An options fixture a conftest overrides must be at least this wide (session is always safe)."""
    value = config.getoption("volttron_platform_scope", None) or config.getini(SCOPE_OPTION) or "module"
    if value not in SCOPES:
        raise pytest.UsageError(f"{SCOPE_OPTION} must be one of {', '.join(SCOPES)}, not {value!r}")
    return value


def require_platform(**needs) -> None:
    """Skip unless a real platform can start here (``needs``: ``driver=True``, ``web=True``, ``message_bus=...``)."""
    if os.environ.get(SKIP_ENV):
        pytest.skip(f"{SKIP_ENV} is set")
    from volttrontesting.platform_cli import PlatformCLI
    missing = PlatformCLI.missing(**needs)
    if missing:
        pytest.skip(f"cannot start a platform here; missing: {'; '.join(missing)}")


def _started(request, tmp_path_factory, label: str, options: dict, **needs):
    from volttrontesting.platform_cli import PlatformCLI
    param = getattr(request, "param", None) or {}
    options = dict(options)
    options.setdefault("message_bus", param.get("messagebus", "zmq"))
    options.setdefault("auth_enabled", param.get("ssl_auth", True))
    require_platform(message_bus=options["message_bus"], auth=options["auth_enabled"], **needs)
    home = tmp_path_factory.mktemp(label) / "volttron_home"
    instance_name = options.pop("instance_name", f"volttron-{label}")
    return PlatformCLI(home, instance_name, **options).start()


@dataclass
class WebUser:
    username: str
    password: str
    groups: Sequence[str] = ("admin", "vui")


@dataclass
class DriverHandle:
    """The Platform Driver on a platform: store registries and device configs, read and write points."""
    cli: object
    identity: str = "platform.driver"
    _files: Path = field(default=None, repr=False)

    def __post_init__(self):
        if self._files is None:
            self._files = Path(self.cli.home).parent / "driver_configs"
        self._files.mkdir(parents=True, exist_ok=True)

    def store_registry(self, name: str, rows_or_path: Iterable[dict] | str | Path) -> None:
        """Store a registry CSV under ``name`` (for example ``registry_configs/meter.csv``), from a file or rows."""
        if isinstance(rows_or_path, (str, Path)):
            path = Path(rows_or_path)
        else:
            rows = list(rows_or_path)
            path = self._files / Path(name).name
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        self.cli.store_config(self.identity, name, path, csv=True)

    def store_device(self, topic: str, config: dict | str | Path) -> None:
        """Store a device config as ``devices/<topic>``, from a file or a dict."""
        if isinstance(config, (str, Path)):
            path = Path(config)
        else:
            path = self._files / (topic.replace("/", "__") + ".json")
            path.write_text(json.dumps(config))
        self.cli.store_config(self.identity, f"devices/{topic}", path)

    def get(self, topic: str):
        return self.cli.get_point(topic)

    def set(self, topic: str, value):
        return self.cli.set_point(topic, value)

    def revert(self, topic: str):
        return self.cli.revert(topic)


def install_platform_driver(cli, source=None, *, anchor=None, identity: str = "platform.driver",
                            tag: str = "driver") -> DriverHandle:
    """Install the Platform Driver on a started platform and return its handle. ``source`` defaults to
    ``AgentSources(anchor=anchor).resolve("platform-driver")``: the checkout when one is found, else PyPI."""
    if source is None:
        from volttrontesting.configs import AgentSources
        source = AgentSources(anchor=anchor).resolve("platform-driver")
    cli.install_agent(source, vip_identity=identity, tag=tag)
    return DriverHandle(cli, identity)


# ---------------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(scope="session")
def platform_cli_options() -> dict:
    """``PlatformCLI`` keyword arguments for ``platform_cli``. Override in a conftest (keep it session-scoped)."""
    return {}


@pytest.fixture(scope="session")
def web_platform_options() -> dict:
    """``PlatformCLI`` keyword arguments for ``web_platform`` (``web=True`` is added). Override in a conftest."""
    return {}


@pytest.fixture(scope="session")
def web_user() -> WebUser:
    """The web user ``web_platform`` creates. Override in a conftest for fixed credentials."""
    return WebUser("admin", secrets.token_urlsafe(16))


@pytest.fixture(scope=platform_scope)
def platform_cli(request, tmp_path_factory, platform_cli_options):
    cli = _started(request, tmp_path_factory, "platform", platform_cli_options)
    try:
        yield cli
    finally:
        cli.shutdown()


@pytest.fixture(scope=platform_scope)
def web_platform(request, tmp_path_factory, web_platform_options, web_user):
    options = {"web": True, **web_platform_options}
    cli = _started(request, tmp_path_factory, "web", options, web=True)
    try:
        cli.add_web_user(web_user.username, web_user.password, web_user.groups)
        cli.web_user = web_user
        yield cli
    finally:
        cli.shutdown()


@pytest.fixture(scope=platform_scope)
def platform_driver(request, platform_cli) -> DriverHandle:
    require_platform(driver=True)
    return install_platform_driver(platform_cli, anchor=request.config.rootpath)


@pytest.fixture
def certs(tmp_path):
    from volttrontesting.certs import CertificateSet
    return CertificateSet(tmp_path / "certs")
