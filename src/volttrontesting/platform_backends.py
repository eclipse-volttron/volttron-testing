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
"""The backends of the ``platform`` fixture (volttrontesting.platform_protocol): TestServerPlatform runs agents in
the test process on a TestServer; CliPlatform drives a real platform with vctl; WrapperPlatform drives a real
platform through PlatformWrapper, with its in-process agent for RPC and pubsub.
"""
from __future__ import annotations

import importlib
import json
import logging
import re
import tempfile
import time
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

from volttrontesting.platform_protocol import NotSupported, Observer, PublishedMessage

__all__ = ["TestServerPlatform", "CliPlatform", "WrapperPlatform", "agent_class_for", "DRIVER_IDENTITY",
           "TEST_PEER"]

DRIVER_IDENTITY = "platform.driver"
TEST_PEER = "volttrontesting.platform"        # the caller identity of in-process RPC calls a test makes
KINDS = ("json", "csv", "raw")


def _content(content_or_path: Any, kind: str):
    """What to store: a dict or list as is (``json``), else the text of a file or the string itself."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
    if isinstance(content_or_path, (dict, list)):
        if kind != "json":
            raise ValueError(f"a dict or list is stored as json, not {kind}")
        return content_or_path
    if isinstance(content_or_path, Path) or (isinstance(content_or_path, str) and "\n" not in content_or_path
                                             and Path(content_or_path).is_file()):
        return Path(content_or_path).read_text()
    return str(content_or_path)


class _DriverPoints:
    """The point operations over the Platform Driver's ``get``, ``set`` and ``revert`` RPC exports."""

    def get(self, topic: str) -> dict:
        results, errors = self.rpc(DRIVER_IDENTITY, "get", topic)
        if errors:
            raise RuntimeError(f"get {topic} failed: {errors}")
        return results

    def get_point(self, topic: str):
        values = self.get(topic)
        if topic not in values:
            raise RuntimeError(f"{topic} not returned; got {values}")
        return values[topic]

    def set_point(self, topic: str, value):
        results, errors = self.rpc(DRIVER_IDENTITY, "set", value, topic)
        if errors:
            raise RuntimeError(f"set {topic} failed: {errors}")
        return results.get(topic)

    def revert(self, topic: str):
        return self.rpc(DRIVER_IDENTITY, "revert", topic)


# ---------------------------------------------------------------------------------------------- agent classes
class _Recorded(BaseException):
    """Raised by the stand-in vip_main to stop a console script once it has named its agent class. A
    BaseException, so a main() that catches Exception around vip_main does not swallow it."""

    def __init__(self, agent_class, kwargs):
        super().__init__(agent_class)
        self.agent_class, self.kwargs = agent_class, kwargs


def _console_scripts(source: str) -> list[str]:
    """``module:function`` of the console scripts of a project directory or an installed distribution."""
    path = Path(source)
    if path.is_dir() and (path / "pyproject.toml").is_file():
        try:
            import tomllib
        except ImportError:                          # Python 3.10
            import tomli as tomllib
        project = tomllib.loads((path / "pyproject.toml").read_text())
        scripts = dict(project.get("project", {}).get("scripts", {}))
        scripts.update(project.get("tool", {}).get("poetry", {}).get("scripts", {}))
        return [s if isinstance(s, str) else s.get("reference", s.get("callable", "")) for s in scripts.values()]
    from packaging.requirements import Requirement
    try:
        name = Requirement(source).name
        dist = metadata.distribution(name)
    except Exception:
        return []
    return [ep.value for ep in dist.entry_points if ep.group == "console_scripts"]


def agent_class_for(source: Any) -> tuple[type, dict]:
    """The agent class to build for ``source``, and the keyword arguments its main() gives vip_main.

    ``source`` is a class, ``"module:Class"``, a project directory, or a distribution name (optionally with a
    version constraint) installed in the test environment. For the last two, the package's console script is run
    with vip_main replaced by a recorder, so the class is the one the platform would start.
    """
    if isinstance(source, type):
        return source, {}
    text = str(source)
    if ":" in text and not Path(text).exists():
        module, _, name = text.partition(":")
        return getattr(importlib.import_module(module), name), {}
    entries = _console_scripts(text)
    if not entries:
        raise NotSupported("testserver", "install_agent",
                           f"{text!r} is not an agent class, 'module:Class', or a project or installed distribution "
                           "with a console script")
    errors = []
    for entry in entries:
        module_name, _, function = entry.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            errors.append(f"{module_name}: {e}")
            continue
        try:
            with _recording_vip_main(module):
                getattr(module, function.split(".")[0])()
        except _Recorded as recorded:
            kwargs = {k: v for k, v in recorded.kwargs.items() if k != "version"}
            return recorded.agent_class, kwargs
        except Exception as e:
            errors.append(f"{entry}: {e!r}")
            continue
        errors.append(f"{entry} returned without calling vip_main")
    raise NotSupported("testserver", "install_agent",
                       f"no agent class found for {text!r} (install it into the test environment?): {'; '.join(errors)}")


@contextmanager
def _recording_vip_main(module):
    import volttron.utils.commands as commands

    def recorder(agent_class, *args, **kwargs):
        if args:
            kwargs = {"version": args[0], **kwargs}
        raise _Recorded(agent_class, kwargs)

    targets = [commands]
    try:
        import volttron.utils as utils
        if hasattr(utils, "vip_main"):
            targets.append(utils)
    except ImportError:
        pass
    if hasattr(module, "vip_main"):
        targets.append(module)
    saved = [(t, t.vip_main) for t in targets]
    for target in targets:
        target.vip_main = recorder
    try:
        yield
    finally:
        for target, original in saved:
            target.vip_main = original


# ---------------------------------------------------------------------------------------------- in process
class _LogBuffer(logging.Handler):
    """Every record logged in the process while the platform is up, formatted like volttron.log."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.setFormatter(logging.Formatter("%(asctime)s %(name)s(%(lineno)d) %(levelname)s: %(message)s"))
        self.text = ""

    def emit(self, record):
        try:
            self.text += self.format(record) + "\n"
        except Exception:
            self.handleError(record)


class TestServerPlatform(_DriverPoints):
    """Agents run in the test process on a TestServer with RPC routing and a configuration store.

    Agents are built as vip_main builds them, with credentials for their identity and the path of their agent
    config, on MockCoreBuilder cores. The log is every record logged in the process (all loggers, not only the
    agents'), from INFO, as a platform's volttron.log would hold.
    """
    __test__ = False
    backend = "testserver"

    def __init__(self, log_level: int = logging.INFO):
        from volttrontesting.server_mock import TestServer
        self.server = TestServer(route_rpc=True)
        self._factories: dict[str, Callable[[], Any]] = {}
        self._files = Path(tempfile.mkdtemp(prefix="volttrontesting-testserver-"))
        self._log = _LogBuffer()
        root = logging.getLogger()
        self._root_level = root.level
        root.addHandler(self._log)
        if root.getEffectiveLevel() > log_level:
            root.setLevel(log_level)

    # agents
    def install_agent(self, source, identity: str, tag: str | None = None, *, config=None) -> None:
        agent_class, kwargs = agent_class_for(source)
        config_path = self._agent_config(identity, config)

        def build():
            from volttron.types.auth.auth_credentials import Credentials
            with _mock_core_builder():
                return agent_class(credentials=Credentials(identity=identity), config_path=config_path,
                                   address=None, **kwargs)

        self._factories[identity] = build
        self.server.start_agent(build())

    def agent(self, identity: str):
        """The in-process agent object, for assertions a protocol operation cannot make."""
        return self.server.connected_agent(identity)

    def restart_agent(self, identity: str) -> None:
        if identity not in self._factories:
            raise KeyError(f"{identity} was not installed on this platform")
        self.server.stop_agent(identity)
        self.server.start_agent(self._factories[identity]())

    def _agent_config(self, identity: str, config) -> str | dict:
        if config is None:
            return {}
        if isinstance(config, (dict, list)):
            path = self._files / f"{identity}.config"
            path.write_text(json.dumps(config))
            return str(path)
        return str(config)

    # configs and RPC
    def store_config(self, identity: str, name: str, content_or_path, kind: str = "json") -> None:
        self.server.config_store.store(identity, name, _content(content_or_path, kind), kind)

    def rpc(self, identity: str, method: str, *args, timeout: float = 30.0, **kwargs):
        import gevent
        return gevent.spawn(self.server.handle_rpc, TEST_PEER, identity, method, args, kwargs).get(timeout=timeout)

    # pubsub
    def subscribe(self, prefix: str) -> Observer:
        observer = Observer(prefix)
        self.server.subscribe(prefix, lambda topic, headers, message, bus='': observer.messages.append(
            PublishedMessage(topic=topic, headers=headers, message=message, bus=bus)))
        return observer

    def publish(self, topic: str, headers: dict | None = None, message=None) -> None:
        self.server.publish(topic, headers=headers, message=message)

    # logs
    def log_size(self) -> int:
        return len(self._log.text)

    def log_since(self, since: int) -> str:
        return self._log.text[since:]

    def wait_for_log(self, pattern: str, timeout: float = 90.0, since: int = 0) -> str:
        import gevent
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for line in reversed(self.log_since(since).splitlines()):
                if re.search(pattern, line):
                    return line
            gevent.sleep(0.05)
        raise TimeoutError(f"{pattern!r} was not logged within {timeout}s")

    def shutdown(self) -> None:
        for identity in list(self._factories):
            if self.server.connected_agent(identity) is not None:
                try:
                    self.server.stop_agent(identity)
                except Exception as e:
                    logging.getLogger(__name__).warning(f"stopping {identity}: {e}")
        root = logging.getLogger()
        root.removeHandler(self._log)
        root.setLevel(self._root_level)
        reset_protocol_proxies()


@contextmanager
def _mock_core_builder():
    """Build agents on MockCoreBuilder cores, whatever other builders the process has registered, and leave core's
    builder cache as it was (the wrapper pins ZmqCoreBuilder the same way)."""
    from volttron.client import decorators
    from volttrontesting.mock_core_builder import MockCoreBuilder
    previous = decorators.__core_builder__
    decorators.__core_builder__ = MockCoreBuilder()
    try:
        yield
    finally:
        decorators.__core_builder__ = previous


def reset_protocol_proxies() -> None:
    """Stop every protocol proxy manager the process has started, and the proxies they launched, and forget them,
    so the next test's interface starts fresh. The managers are a process-wide cache keyed by proxy type."""
    try:
        from protocol_proxy.manager.base import ProtocolProxyManager
    except ImportError:
        return
    for manager in list(ProtocolProxyManager.managers.values()):
        try:
            manager.stop()
        except Exception as e:
            logging.getLogger(__name__).warning(f"stopping protocol proxy manager {manager}: {e}")
    ProtocolProxyManager.managers.clear()


# ---------------------------------------------------------------------------------------------- real platforms
class CliPlatform:
    """A real platform driven by vctl (PlatformCLI). RPC needs ``vctl rpc call`` (volttron-core#386)."""
    __test__ = False
    backend = "cli"

    def __init__(self, cli):
        self.cli = cli
        self._tags: dict[str, str] = {}
        self._rpc_call: bool | None = None

    def install_agent(self, source, identity: str, tag: str | None = None, *, config=None) -> None:
        tag = tag or identity
        self._tags[identity] = tag
        if isinstance(config, (dict, list)):
            path = Path(self.cli.home).parent / f"{identity}.agent-config.json"
            path.write_text(json.dumps(config))
            config = path
        self.cli.install_agent(str(source), identity, tag, config=config)

    def restart_agent(self, identity: str) -> None:
        self.cli.restart_agent(identity, self._tags.get(identity, identity))

    def store_config(self, identity: str, name: str, content_or_path, kind: str = "json") -> None:
        content = _content(content_or_path, kind)
        path = Path(self.cli.home).parent / "stored_configs" / f"{identity}__{name.replace('/', '__')}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content) if isinstance(content, (dict, list)) else content)
        self.cli.vctl("config", "store", identity, name, str(path), f"--{kind}")

    def rpc(self, identity: str, method: str, *args, timeout: float = 30.0, **kwargs):
        if self._rpc_call is None:
            self._rpc_call = "call" in self.cli._run(["vctl", "rpc", "--help"], check=False).stdout
        if not self._rpc_call:
            raise NotSupported(self.backend, "rpc", "this volttron-core's vctl has no 'rpc call' (volttron-core#386)")
        output = self.cli.vctl("--json", "rpc", "call", identity, method, "--json-args", json.dumps(list(args)),
                               "--json-kwargs", json.dumps(kwargs), timeout=timeout + 30)
        return json.loads(output)

    def get(self, topic: str) -> dict:
        return self.cli.get(topic)

    def get_point(self, topic: str):
        return self.cli.get_point(topic)

    def set_point(self, topic: str, value):
        return self.cli.set_point(topic, value)

    def revert(self, topic: str):
        return self.cli.revert(topic)

    def subscribe(self, prefix: str) -> Observer:
        raise NotSupported(self.backend, "subscribe", "vctl has no machine-readable subscribe; use the wrapper backend")

    def publish(self, topic: str, headers: dict | None = None, message=None) -> None:
        # vctl publish sends its data as a string, so publish through a control connection with JSON values. It
        # uses the control connection's identity, so it is never run alongside another vctl command.
        payload = json.dumps({"topic": topic, "headers": headers or {}, "message": message})
        self.cli.python("import json; "
                        "from volttron.client.commands.connection import ControlConnection; "
                        "from volttron.utils import ClientContext as cc; "
                        f"p = json.loads({payload!r}); c = ControlConnection(cc.get_address()); "
                        "c.server.vip.pubsub.publish('pubsub', p['topic'], headers=p['headers'], "
                        "message=p['message']).get(timeout=10); c.kill()")

    def log_size(self) -> int:
        return self.cli.log_size()

    def log_since(self, since: int) -> str:
        return self.cli.log_since(since)

    def wait_for_log(self, pattern: str, timeout: float = 90.0, since: int = 0) -> str:
        return self.cli.wait_for_log(pattern, timeout=timeout, since=since)

    def shutdown(self) -> None:
        self.cli.shutdown()


class WrapperPlatform(_DriverPoints):
    """A real platform through PlatformWrapper: vctl for installs and configs, its in-process ``dynamic_agent`` for
    RPC, publishing and subscribing."""
    __test__ = False
    backend = "wrapper"

    def __init__(self, wrapper):
        self.wrapper = wrapper
        self.cli = CliPlatform(wrapper.cli)

    def install_agent(self, source, identity: str, tag: str | None = None, *, config=None) -> None:
        self.cli.install_agent(source, identity, tag, config=config)

    def restart_agent(self, identity: str) -> None:
        self.cli.restart_agent(identity)

    def store_config(self, identity: str, name: str, content_or_path, kind: str = "json") -> None:
        self.cli.store_config(identity, name, content_or_path, kind)

    def rpc(self, identity: str, method: str, *args, timeout: float = 30.0, **kwargs):
        return self.wrapper.dynamic_agent.vip.rpc.call(identity, method, *args, **kwargs).get(timeout=timeout)

    def subscribe(self, prefix: str) -> Observer:
        observer = Observer(prefix)

        def on_message(peer, sender, bus, topic, headers, message):
            observer.messages.append(PublishedMessage(topic=topic, headers=headers, message=message, bus=bus))

        self.wrapper.dynamic_agent.vip.pubsub.subscribe("pubsub", prefix, on_message).get(timeout=10)
        return observer

    def publish(self, topic: str, headers: dict | None = None, message=None) -> None:
        self.wrapper.dynamic_agent.vip.pubsub.publish("pubsub", topic, headers=headers, message=message).get(timeout=10)

    def log_size(self) -> int:
        return self.cli.log_size()

    def log_since(self, since: int) -> str:
        return self.cli.log_since(since)

    def wait_for_log(self, pattern: str, timeout: float = 90.0, since: int = 0) -> str:
        return self.cli.wait_for_log(pattern, timeout=timeout, since=since)

    def shutdown(self) -> None:
        self.wrapper.shutdown_platform()
