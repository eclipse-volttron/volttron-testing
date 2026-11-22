"""The ``testserver`` backend of the platform protocol (step 7): agents in process with configs, RPC, pubsub and
logs, found from a class, ``module:Class``, or a project's console script."""
import logging
import textwrap

import pytest

from volttrontesting.platform_backends import TestServerPlatform, agent_class_for
from volttrontesting.platform_protocol import NotSupported, PlatformUnderTest

AGENT_MODULE = '''
import logging
from volttron.client import Agent
from volttron.client.vip.agent import Core, PubSub, RPC
from volttron.utils import load_config
from volttron.utils.commands import vip_main

_log = logging.getLogger("probe.interface")      # not the agent's own logger


class ProbeAgent(Agent):
    def __init__(self, config_path, greeting="hello", **kwargs):
        super().__init__(**kwargs)
        self.greeting = greeting
        self.agent_config = load_config(config_path) if config_path else {}
        self.configs = {}
        self.heard = []
        self.points = {"campus/building/device/point": 1}
        self.vip.config.subscribe(self.on_config, actions=["NEW", "UPDATE", "DELETE"], pattern="*")

    def on_config(self, name, action, contents):
        self.configs[name] = (action, contents)
        _log.info(f"configured {name} {action}")

    @Core.receiver("onstart")
    def started(self, sender, **kwargs):
        self.vip.pubsub.publish("pubsub", "probe/started", message={"identity": self.core.identity})

    @PubSub.subscribe("pubsub", "to/probe")
    def on_message(self, peer, sender, bus, topic, headers, message):
        self.heard.append((topic, message))

    @RPC.export
    def greet(self, name):
        return f"{self.greeting} {name} from {self.agent_config.get('place', 'nowhere')}"

    @RPC.export
    def get(self, topic=None, regex=None, **kwargs):
        return ({topic: self.points[topic]}, {}) if topic in self.points else ({}, {topic: "no such point"})

    @RPC.export
    def set(self, value, topic=None, regex=None, **kwargs):
        self.points[topic] = value
        return {topic: value}, {}

    @RPC.export
    def revert(self, topic=None, regex=None, **kwargs):
        self.points[topic] = 1
        return {}


def main():
    try:
        vip_main(ProbeAgent, version="0.1", greeting="hi")
    except Exception as e:          # agents often do this; the recorder must get through it
        print(e)
'''


@pytest.fixture
def probe_project(tmp_path, monkeypatch):
    """A project directory with a console script, its package importable from the test process."""
    (tmp_path / "src" / "probeagent").mkdir(parents=True)
    (tmp_path / "src" / "probeagent" / "__init__.py").write_text("")
    (tmp_path / "src" / "probeagent" / "agent.py").write_text(AGENT_MODULE)
    (tmp_path / "pyproject.toml").write_text(textwrap.dedent('''
        [tool.poetry]
        name = "probeagent"
        version = "0.1.0"
        [tool.poetry.scripts]
        probeagent = "probeagent.agent:main"
        '''))
    monkeypatch.syspath_prepend(str(tmp_path / "src"))
    return tmp_path


@pytest.fixture
def platform():
    p = TestServerPlatform()
    yield p
    p.shutdown()


def test_it_is_a_platform_under_test(platform):
    assert isinstance(platform, PlatformUnderTest) and platform.backend == "testserver"


def test_the_class_comes_from_the_console_script(probe_project):
    from probeagent.agent import ProbeAgent
    assert agent_class_for(probe_project) == (ProbeAgent, {"greeting": "hi"})
    assert agent_class_for("probeagent.agent:ProbeAgent") == (ProbeAgent, {})
    assert agent_class_for(ProbeAgent) == (ProbeAgent, {})


def test_an_unknown_source_is_not_supported():
    with pytest.raises(NotSupported, match="testserver"):
        agent_class_for("no-such-distribution-anywhere")


def test_install_with_config_rpc_and_stored_configs(platform, probe_project):
    platform.store_config("probe", "config", {"a": 1})
    platform.store_config("probe", "registry.csv", "Point,Units\nx,F\n", kind="csv")
    started = platform.subscribe("probe/started")
    platform.install_agent(probe_project, "probe", config={"place": "the lab"})
    assert platform.rpc("probe", "greet", "you") == "hi you from the lab"
    agent = platform.agent("probe")
    assert agent.configs == {"config": ("NEW", {"a": 1}), "registry.csv": ("NEW", [{"Point": "x", "Units": "F"}])}
    assert started.wait(1)[0].message == {"identity": "probe"}

    platform.store_config("probe", "config", {"a": 2})
    assert agent.configs["config"] == ("UPDATE", {"a": 2})


def test_restart_builds_a_new_agent_with_the_stored_configs(platform, probe_project):
    platform.install_agent("probeagent.agent:ProbeAgent", "probe")
    platform.store_config("probe", "config", {"a": 3})
    first = platform.agent("probe")
    platform.restart_agent("probe")
    second = platform.agent("probe")
    assert second is not first and second.configs == {"config": ("NEW", {"a": 3})}


def test_publish_reaches_agents(platform, probe_project):
    platform.install_agent(probe_project, "probe")
    platform.publish("to/probe/x", message={"n": 1})
    assert platform.agent("probe").heard == [("to/probe/x", {"n": 1})]


def test_the_log_holds_every_logger(platform, probe_project):
    since = platform.log_size()
    platform.install_agent(probe_project, "probe")
    platform.store_config("probe", "devices/a", {"x": 1})
    assert "probe.interface" in platform.wait_for_log(r"configured devices/a NEW", timeout=5, since=since)
    logging.getLogger("elsewhere").info("unrelated line")
    assert "unrelated line" in platform.log_since(since)


def test_point_operations_use_the_driver_exports(platform, probe_project):
    platform.install_agent(probe_project, "platform.driver")
    topic = "campus/building/device/point"
    assert platform.get(topic) == {topic: 1} and platform.get_point(topic) == 1
    assert platform.set_point(topic, 5) == 5 and platform.get_point(topic) == 5
    platform.revert(topic)
    assert platform.get_point(topic) == 1
    with pytest.raises(RuntimeError, match="no such point"):
        platform.get_point("campus/building/device/missing")
