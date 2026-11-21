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
"""Drive a real VOLTTRON platform through its command-line tools, the way an operator does.

The platform runs as a subprocess in a temporary ``VOLTTRON_HOME``, using the Python environment that runs the tests.
Agents are installed with ``vctl install``, configuration is stored with ``vctl config store``, points are read and
written with ``vctl driver`` (the Platform Driver's vctl plugin), and assertions read the platform log. Nothing of the
platform is imported into the test process, so tests written against this class survive platform API changes; for
bus-level assertions use :class:`volttrontesting.PlatformWrapper` instead.

Before the first start the home receives a ``pyproject.toml`` that pins every distribution installed in the running
environment (:func:`environment_project`). The platform records installed agents in that poetry project; left to
build it itself, it adds editable packages before pinning the rest, and resolving their dependencies then upgrades
packages such as volttron-core to the newest PyPI release, changing the environment under the tests. With the file
provided the platform only locks it and installs nothing.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterable

from volttrontesting.configs import AgentSources, AgentSpec
from volttrontesting.harness import free_port

__all__ = ["PlatformCLI", "PlatformError", "WebOptions", "environment_project", "POETRY_LOCAL_CONFIG",
           "MESSAGE_BUS_DISTRIBUTIONS"]

# Keep poetry (which the platform uses to record installed agents) in the environment running the tests.
POETRY_LOCAL_CONFIG = "[virtualenvs]\ncreate = false\n\n[keyring]\nenabled = false\n"

# Distributions that provide a message bus, and with it the CredentialsStore the auth service needs. An environment
# with volttron-core but none of these cannot start a platform.
MESSAGE_BUS_DISTRIBUTIONS = ("volttron-lib-zmq", )


class PlatformError(RuntimeError):
    pass


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def environment_project(exclude: Iterable[str] = ()) -> str:
    """A ``pyproject.toml`` pinning every installed distribution, with directory installs as path dependencies.

    Editable installs become ``develop = true`` path dependencies; non-editable directory installs (a locally built,
    perhaps patched, release) become ``develop = false`` path dependencies, because pinning them by version would make
    poetry replace them with the index's copy. Each distribution appears once even when the environment holds two
    copies (a checkout's ``src/*.egg-info`` beside its editable install), the directory install winning. Names in
    ``exclude`` are left out: distributions with git or path sources poetry cannot lock together, or pins that clash
    with the platform's own requirements. They stay importable; only the platform's record omits them.
    """
    excluded = {_canonical(n) for n in exclude} | {"volttron"}
    entries: dict[str, tuple[bool, str]] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name or _canonical(name) in excluded:
            continue
        direct_url = dist.read_text("direct_url.json")
        info = json.loads(direct_url) if direct_url else {}
        if "dir_info" in info:
            develop = "true" if info["dir_info"].get("editable") else "false"
            line = f'"{name}" = {{path = "{info["url"].removeprefix("file://")}", develop = {develop}}}'
            is_path = True
        else:
            line = f'"{name}" = "{dist.version}"'    # quoted: names may contain dots (backports.tarfile)
            is_path = False
        key = _canonical(name)
        if key not in entries or (is_path and not entries[key][0]):
            entries[key] = (is_path, line)
    paths = sorted(line for is_path, line in entries.values() if is_path)
    pins = sorted(line for is_path, line in entries.values() if not is_path)
    v = sys.version_info
    return "\n".join([
        "[tool.poetry]", 'name = "volttron"', 'version = "0.1.0"', 'description = ""',
        'authors = ["volttron <volttron@pnnl.gov>"]', "package-mode = false", "",
        "[tool.poetry.dependencies]", f'python = "~{v.major}.{v.minor}.{v.micro}"',
        *paths, *pins, "",
        "[[tool.poetry.source]]", 'name = "test-pypi"', 'url = "https://test.pypi.org/simple/"',
        'priority = "supplemental"', "",
        "[build-system]", 'requires = ["poetry-core>=2.0.0,<3.0.0"]', 'build-backend = "poetry.core.masonry.api"', "",
    ])


@dataclass
class WebOptions:
    """Enable the web service. ``bind_address=None`` picks a free local port; ``secret_key=None`` a random key."""
    bind_address: str | None = None
    secret_key: str | None = None


class PlatformCLI:
    """A platform in ``home`` (conventionally ``<tmp>/volttron_home``; the log goes beside it).

    ``address=None`` gives a platform that listens only on its IPC socket inside ``home``, which is all ``vctl``
    needs and lets any number of instances run side by side; pass ``"auto"`` (a free local TCP port) or an address
    for multi-platform tests. ``exclude`` names distributions to leave out of :func:`environment_project`.
    """

    def __init__(self, home: str | Path, instance_name: str = "volttron-test", *, address: str | None = None,
                 web: WebOptions | bool | None = None, rpc_allow_list: Iterable[str] = (), message_bus: str = "zmq",
                 auth_enabled: bool = True, agent_isolation_mode: bool = False, extra_config: str = "",
                 exclude: Iterable[str] = (), env: dict[str, str] | None = None, agent_monitor_frequency: int = 600):
        self.home = Path(home)
        self.instance_name = instance_name
        self.address = f"tcp://127.0.0.1:{free_port()}" if address == "auto" else address
        if web is True:
            web = WebOptions()
        self.web = web or None
        if self.web is not None:
            self.web = WebOptions(bind_address=self.web.bind_address or f"http://127.0.0.1:{free_port()}",
                                  secret_key=self.web.secret_key or secrets.token_hex(32))
        self.rpc_allow_list = list(rpc_allow_list)
        self.message_bus = message_bus
        self.auth_enabled = auth_enabled
        self.agent_isolation_mode = agent_isolation_mode
        self.extra_config = extra_config
        self.exclude = list(exclude)
        self.agent_monitor_frequency = agent_monitor_frequency
        self.log = self.home.parent / "volttron.log"
        self.stdout = self.home.parent / "volttron.stdout"
        self.bin = Path(sys.executable).parent
        base = dict(os.environ if env is None else env)
        self.env = {**base, "VOLTTRON_HOME": str(self.home), "VIRTUAL_ENV": str(self.bin.parent),
                    "PATH": f"{self.bin}:{base.get('PATH', '')}",
                    "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring"}
        self.process: subprocess.Popen | None = None

    # ------------------------------------------------------------------ availability
    @classmethod
    def missing(cls, *, driver: bool = False, web: bool = False) -> list[str]:
        """What the running environment lacks for a platform (and, if asked, ``vctl driver`` or the web service)."""
        bin = Path(sys.executable).parent
        missing = [exe for exe in ("volttron", "vctl") if not (bin / exe).exists()]
        if not any(_installed(name) for name in MESSAGE_BUS_DISTRIBUTIONS):
            missing.append(f"a message bus ({', '.join(MESSAGE_BUS_DISTRIBUTIONS)})")
        if driver and importlib.util.find_spec("volttron.plugins.vctl.driver") is None:
            missing.append("the Platform Driver (for the vctl driver plugin)")
        if web and not _installed("volttron-lib-web"):
            missing.append("volttron-lib-web")
        return missing

    @classmethod
    def available(cls, *, driver: bool = False, web: bool = False) -> bool:
        return not cls.missing(driver=driver, web=web)

    # ------------------------------------------------------------------ configuration
    def platform_config(self) -> str:
        lines = ["[volttron]", f"instance-name = {self.instance_name}",
                 f"agent-isolation-mode = {self.agent_isolation_mode}", f"messagebus = {self.message_bus}",
                 f"auth-enabled = {self.auth_enabled}", "server-messagebus-id = vip.server",
                 f"agent-monitor-frequency = {self.agent_monitor_frequency}"]
        if self.address:
            lines.append(f"address = {self.address}")
        if self.web is not None:
            allow = "".join(f"\n    {line}" for line in self.rpc_allow_list)
            lines += ["", "[web]", f"bind-web-address = {self.web.bind_address}",
                      f"web-secret-key = {self.web.secret_key}", f"rpc-allow-list ={allow}"]
        text = "\n".join(lines) + "\n"
        return text + (self.extra_config if self.extra_config.endswith("\n") or not self.extra_config
                       else self.extra_config + "\n")

    @property
    def bind_address(self) -> str | None:
        return self.web.bind_address if self.web is not None else None

    @property
    def web_port(self) -> int | None:
        return int(self.bind_address.rsplit(":", 1)[1].rstrip("/")) if self.bind_address else None

    # ------------------------------------------------------------------ lifecycle
    def start(self, timeout: float = 480.0) -> "PlatformCLI":
        """Prepare the home and launch the platform: :meth:`prepare` then :meth:`launch`."""
        self.prepare()
        return self.launch(timeout)

    def prepare(self) -> "PlatformCLI":
        """Write the platform config, and on the first start the poetry config and the pinned project.

        The project (:func:`environment_project`) and ``poetry.toml`` are written only if absent: the platform records
        installed agents in that project, so a restart in the same home must keep it. Callers that need files in the
        home before the platform starts (credentials, service config) write them between ``prepare`` and ``launch``.
        """
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config").write_text(self.platform_config())
        if not (self.home / "poetry.toml").exists():
            (self.home / "poetry.toml").write_text(POETRY_LOCAL_CONFIG)
        if not (self.home / "pyproject.toml").exists():
            (self.home / "pyproject.toml").write_text(environment_project(self.exclude))
        return self

    def launch(self, timeout: float = 480.0) -> "PlatformCLI":
        """Start the platform process and wait until ``vctl status`` answers (and the web server, with ``web``).

        ``vctl status`` succeeding proves the router, the control service and the control credentials all work; a
        call costs about 0.35 s and a platform typically answers 3 to 7 s after launch.
        """
        with open(self.stdout, "a") as out:
            self.process = subprocess.Popen([str(self.bin / "volttron"), "-vv", "-l", str(self.log)], env=self.env,
                                            cwd=str(self.home.parent), stdout=out, stderr=subprocess.STDOUT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise PlatformError(f"platform exited with {self.process.returncode}:\n{self.log_tail()}")
            if self._run(["vctl", "status"], check=False, timeout=30).returncode == 0 and self._web_up():
                return self
            time.sleep(1)
        raise PlatformError(f"platform did not become ready within {timeout}s:\n{self.log_tail()}")

    def _web_up(self) -> bool:
        if self.web_port is None:
            return True
        try:
            with socket.create_connection(("127.0.0.1", self.web_port), timeout=2):
                return True
        except OSError:
            return False

    def shutdown(self) -> None:
        if self.process is None:
            return
        self._run(["vctl", "shutdown", "--platform"], check=False, timeout=60)
        try:
            self.process.wait(30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(10)
        self.process = None

    def __enter__(self) -> "PlatformCLI":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # ------------------------------------------------------------------ logs
    def log_tail(self, lines: int = 40) -> str:
        parts = []
        if self.stdout.exists() and self.stdout.read_text(errors="replace").strip():
            parts.append("--- stdout/stderr ---\n" + "\n".join(self.stdout.read_text(errors="replace").splitlines()[-lines:]))
        log = "\n".join(self.log.read_text(errors="replace").splitlines()[-lines:]) if self.log.exists() else "(no log)"
        parts.append("--- log ---\n" + log)
        return "\n".join(parts)

    def log_size(self) -> int:
        """Current length of the platform log, for use as the ``since`` argument of wait_for_log and log_since."""
        return len(self.log.read_text(errors="replace")) if self.log.exists() else 0

    def log_since(self, since: int) -> str:
        """The platform log text appended after the given log_size() value."""
        return self.log.read_text(errors="replace")[since:] if self.log.exists() else ""

    def wait_for_log(self, pattern: str, timeout: float = 90.0, since: int = 0) -> str:
        """Wait for a line matching the regular expression to appear in the platform log (after offset ``since``)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for line in reversed(self.log_since(since).splitlines()):
                if re.search(pattern, line):
                    return line
            time.sleep(1)
        raise PlatformError(f"{pattern!r} did not appear in the log within {timeout}s:\n{self.log_tail()}")

    # ------------------------------------------------------------------ commands
    def _run(self, args, check: bool = True, timeout: float = 120.0) -> subprocess.CompletedProcess:
        args = [str(self.bin / args[0]), *map(str, args[1:])]
        result = subprocess.run(args, env=self.env, capture_output=True, text=True, timeout=timeout)
        if check and result.returncode != 0:
            raise PlatformError(f"{' '.join(args)} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
        return result

    def vctl(self, *args, timeout: float = 180.0) -> str:
        return self._run(["vctl", *args], timeout=timeout).stdout

    def vctl_json(self, *args, timeout: float = 180.0):
        """Run ``vctl --json <args>`` and return the parsed JSON document.

        Tolerates lines printed before the document: ``vctl --json install`` prints progress messages first
        (volttron-core#364).
        """
        output = self.vctl("--json", *args, timeout=timeout)
        starts = [i for i in (output.find("{"), output.find("[")) if i >= 0]
        if not starts:
            raise PlatformError(f"no JSON document in the output of vctl --json {' '.join(map(str, args))}: {output!r}")
        return json.JSONDecoder().raw_decode(output[min(starts):])[0]

    def python(self, code: str, timeout: float = 60.0) -> str:
        """Run a snippet with the platform's environment (``VOLTTRON_HOME`` set)."""
        return self._run(["python", "-c", code], timeout=timeout).stdout

    # ------------------------------------------------------------------ agents
    def install_agent(self, source: str | Path, vip_identity: str, tag: str, *, config: str | Path | None = None,
                      start: bool = True, timeout: float = 400.0) -> None:
        """Install (editable when ``source`` is a directory) and, by default, start an agent; with ``start`` wait
        until it reports running."""
        args = ["install", str(source), "--vip-identity", vip_identity, "--tag", tag]
        if config is not None:
            args += ["--agent-config", str(config)]
        if start:
            args.append("--start")
        self.vctl(*args, timeout=timeout)
        if start:
            self.wait_for_running(vip_identity)

    def install(self, specs: Iterable[AgentSpec], sources: AgentSources | None = None,
                log: Callable[[str], None] | None = None) -> dict[str, float]:
        """Install and start each agent, resolving ``spec.source`` through ``sources``. Returns seconds per identity."""
        sources = sources or AgentSources()
        timings = {}
        for spec in specs:
            t0 = time.time()
            self.install_agent(sources.resolve(spec.source), spec.identity, spec.tag, config=spec.config)
            timings[spec.identity] = time.time() - t0
            if log is not None:
                log(f"installed {spec.identity} in {timings[spec.identity]:.0f}s")
        return timings

    def restart_agent(self, vip_identity: str, tag: str) -> None:
        """Restart an agent, so that it reloads every configuration from the store, and wait until it is running."""
        self.vctl("restart", "--tag", tag)
        self.wait_for_running(vip_identity)

    def wait_for_running(self, vip_identity: str, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if re.search(rf"\s{re.escape(vip_identity)}\s.*running", self.vctl("status")):
                return
            time.sleep(2)
        raise PlatformError(f'{vip_identity} did not start:\n{self.vctl("status")}\n{self.log_tail()}')

    def store_config(self, identity: str, name: str, path: str | Path, csv: bool = False) -> None:
        self.vctl("config", "store", identity, name, str(path), *(["--csv"] if csv else []))

    def grant(self, identity: str, *rpc_capabilities: str) -> None:
        """Add RPC capabilities to an agent's authorization (``vctl authz add agent``)."""
        self.vctl("authz", "add", "agent", identity, "--rpc-capabilities", *rpc_capabilities)

    def add_web_user(self, username: str, password: str, groups: Iterable[str] = ("admin", "vui")) -> None:
        self.python("from volttron.services.web.user_manager import add_web_user; "
                    f"add_web_user({username!r}, {password!r}, {list(groups)!r}, overwrite=True)")

    # ------------------------------------------------------------------ points (vctl driver)
    def driver(self, *args, timeout: float = 90.0):
        """Run a ``vctl driver`` command and return its printed Python literal: a dict, or a (results, errors) pair,
        which the JSON round trip through vctl delivers as a two-element list."""
        return _literal(self._run(["vctl", "driver", *args], timeout=timeout).stdout)

    def get(self, topic: str) -> dict:
        """Values keyed by full topic. For a point topic the dict has one entry."""
        results, errors = self.driver("get", topic)
        if errors:
            raise PlatformError(f"get {topic} failed: {errors}")
        return results

    def get_point(self, topic: str):
        values = self.get(topic)
        if topic not in values:
            raise PlatformError(f"{topic} not returned; got {values}")
        return values[topic]

    def set_point(self, topic: str, value):
        results, errors = self.driver("set", "--", topic, str(value))
        if errors:
            raise PlatformError(f"set {topic} failed: {errors}")
        return results[topic]

    def revert(self, topic: str):
        return self.driver("revert", topic)


def _installed(distribution: str) -> bool:
    try:
        metadata.distribution(distribution)
        return True
    except metadata.PackageNotFoundError:
        return False


def _literal(output: str):
    """The first dict, tuple or list literal in a command's output (vctl may print log lines before it)."""
    start = min((i for i in (output.find(c) for c in "{([") if i >= 0), default=-1)
    if start < 0:
        raise PlatformError(f"unexpected vctl driver output: {output!r}")
    return ast.literal_eval(output[start:].strip())
