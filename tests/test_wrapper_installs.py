"""PlatformWrapper installs agents and libraries with vctl, from every source vctl accepts (issue #5).

The unit tests check the vctl command lines the wrapper builds; the platform tests install, through the wrapper,
from a local directory (and the reinstall rules for it), from a wheel, from PyPI with a version constraint (and a
``--force`` reinstall of it), and a library from a local directory with ``install_library``.
"""
import dataclasses
import os
import subprocess
import sys
import re
import json
from pathlib import Path

import pytest

from volttrontesting.platform_cli import PlatformCLI
from volttrontesting.platformwrapper import InstallAgentOptions, PlatformWrapper

# --------------------------------------------------------------------------------------------- command lines


@pytest.fixture
def recording_wrapper(tmp_path, monkeypatch):
    """A PlatformWrapper with no platform: every vctl command is recorded and answered as a successful install."""
    monkeypatch.setattr('volttrontesting.platformwrapper.time.sleep', lambda s: None)
    wrapper = object.__new__(PlatformWrapper)
    wrapper.env = dict(os.environ, VOLTTRON_HOME=str(tmp_path))
    wrapper.volttron_home = str(tmp_path)
    wrapper.vctl_exe = 'vctl'
    wrapper.commands, wrapper.started, wrapper.configs = [], [], []

    def vctl(cmd, json_output=False):
        wrapper.commands.append([str(c) for c in cmd])
        if '--agent-config' in cmd:
            config = Path(cmd[cmd.index('--agent-config') + 1])
            wrapper.configs.append(config.read_text())
        if json_output:
            return {'agent_uuid': 'u1'}
        return 'Building from x\nInstalled vtestlib \n' if cmd[1] == 'install-lib' else ''

    wrapper._vctl = vctl
    wrapper.logit = lambda message: None
    wrapper.is_running = lambda: True
    wrapper.__wait_for_control_connection_to_exit__ = lambda timeout=10: None
    wrapper.start_agent = wrapper.started.append
    wrapper.is_agent_running = lambda agent_uuid: True
    return wrapper


@pytest.mark.parametrize('kwargs, source', [
    ({'agent_dir': '/src/listener'}, '/src/listener'),
    ({'agent_dir': Path('/src/listener')}, '/src/listener'),
    ({'agent_wheel': '/dist/listener-1.0-py3-none-any.whl'}, '/dist/listener-1.0-py3-none-any.whl'),
    ({'agent_dir': 'volttron-listener>=2.0.0rc0,<2.0.0rc3'}, 'volttron-listener>=2.0.0rc0,<2.0.0rc3'),
])
def test_every_source_goes_to_vctl_install_unchanged(recording_wrapper, kwargs, source):
    assert recording_wrapper.install_agent(**kwargs) == 'u1'
    assert recording_wrapper.commands == [['vctl', '--json', 'install', source]]
    assert recording_wrapper.started == ['u1']


@pytest.mark.parametrize('kwargs', [{}, {'agent_dir': 'a', 'agent_wheel': 'b.whl'}])
def test_exactly_one_source(recording_wrapper, kwargs):
    with pytest.raises(ValueError, match='exactly one'):
        recording_wrapper.install_agent(**kwargs)
    assert recording_wrapper.commands == []


def test_options_become_vctl_options(recording_wrapper, tmp_path):
    config = tmp_path / 'agent.json'
    config.write_text('{}')
    recording_wrapper.install_agent(agent_wheel='/d/a.whl', config_file=str(config), vip_identity='a.b', force=True,
                                    tag='t1', priority=20, start=False)
    assert recording_wrapper.commands == [['vctl', '--json', 'install', '/d/a.whl', '--agent-config', str(config),
                                           '--force', '--vip-identity', 'a.b', '--tag', 't1', '--priority', '20']]
    assert recording_wrapper.started == []


def test_a_dict_config_is_written_to_a_temporary_file_and_removed(recording_wrapper, tmp_path):
    recording_wrapper.install_agent(agent_dir='volttron-listener', config_file={'message': 'hi'})
    assert [json.loads(c) for c in recording_wrapper.configs] == [{'message': 'hi'}]
    assert not list(tmp_path.glob('agent_config_*'))


def test_install_agent_options_default_like_the_keywords():
    import inspect
    keywords = inspect.signature(PlatformWrapper.install_agent).parameters
    for field in dataclasses.fields(InstallAgentOptions):
        name = 'config_file' if field.name == 'agent_config' else field.name
        assert field.default == keywords[name].default, field.name


def test_install_options_override_only_what_they_set(recording_wrapper):
    recording_wrapper.install_agent(agent_dir='/src/a', vip_identity='from.keyword', tag='kw',
                                    install_options=InstallAgentOptions(start=False, vip_identity='platform.driver',
                                                                        priority=5))
    assert recording_wrapper.commands == [['vctl', '--json', 'install', '/src/a', '--vip-identity', 'platform.driver',
                                           '--tag', 'kw', '--priority', '5']]
    assert recording_wrapper.started == []


def test_install_library_and_remove_library(recording_wrapper):
    assert recording_wrapper.install_library(Path('/src/vtestlib'), force=True, pre_release=True) == 'vtestlib'
    recording_wrapper.remove_library('vtestlib')
    assert recording_wrapper.commands == [['vctl', 'install-lib', '/src/vtestlib', '--force', '--pre-release'],
                                          ['vctl', 'remove-lib', 'vtestlib']]


# --------------------------------------------------------------------------------------------- on a platform

PLATFORM_MARKS = (
    pytest.mark.platform,
    pytest.mark.timeout(1200),
    pytest.mark.skipif(bool(os.environ.get('VOLTTRON_TESTING_SKIP_PLATFORM')),
                       reason='VOLTTRON_TESTING_SKIP_PLATFORM is set'),
    pytest.mark.skipif(not PlatformCLI.available(), reason=f'cannot start a platform here: {PlatformCLI.missing()}'),
)


def platform_test(test):
    for mark in PLATFORM_MARKS:
        test = mark(test)
    return test

AGENT_SOURCE = '''
from volttron.utils.commands import vip_main
from volttron.client.vip.agent import Agent, Core
from volttron.client.messaging.health import STATUS_GOOD


class InstallTestAgent(Agent):
    def __init__(self, config_path, **kwargs):
        super().__init__(**kwargs)

    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        self.vip.health.set_status(STATUS_GOOD, 'status good')


def main():
    vip_main(InstallTestAgent, version='0.1')
'''


def _package(root: Path, name: str, module: str, body: str, dependencies: str, scripts: str = '') -> Path:
    (root / 'src' / module).mkdir(parents=True)
    (root / 'src' / module / '__init__.py').write_text(body)
    (root / 'pyproject.toml').write_text(f'''[tool.poetry]
name = "{name}"
version = "0.1.0"
description = "volttron-testing install test package"
authors = ["VOLTTRON Team"]
packages = [{{ include = "{module}", from = "src" }}]

[tool.poetry.dependencies]
python = ">=3.10,<4.0"
{dependencies}
{scripts}
[build-system]
requires = ["poetry-core>=1.0.0"]
build-backend = "poetry.core.masonry.api"
''')
    return root


@pytest.fixture(scope='module')
def agent_project(tmp_path_factory):
    """A minimal agent project, and its wheel built with poetry."""
    root = _package(tmp_path_factory.mktemp('installtestagent'), 'installtestagent', 'installtestagent',
                    AGENT_SOURCE, 'volttron-core = ">=2.0.0rc16"',
                    '[tool.poetry.scripts]\ninstalltestagent = "installtestagent:main"\n')
    subprocess.run([str(Path(sys.executable).parent / 'poetry'), 'build', '--format', 'wheel'], cwd=root, check=True,
                   capture_output=True)
    return root, next((root / 'dist').glob('*.whl'))


@pytest.fixture
def instance(volttron_instance: PlatformWrapper):
    yield volttron_instance
    if volttron_instance.is_running():
        volttron_instance.remove_all_agents()


def _status(instance: PlatformWrapper, identity: str) -> dict:
    return instance._vctl(['vctl', '--json', 'status'], json_output=True).get(identity, {})


def _recorded(instance: PlatformWrapper, distribution: str) -> bool:
    """Whether the platform's project (VOLTTRON_HOME/pyproject.toml) lists the distribution as a dependency."""
    text = (Path(instance.volttron_home) / 'pyproject.toml').read_text()
    return re.search(rf'^\s*"?{re.escape(distribution)}"?\s*=', text, flags=re.MULTILINE | re.IGNORECASE) is not None


def _installed_version(instance: PlatformWrapper, distribution: str) -> str:
    code = f"import importlib.metadata as m; print(m.version({distribution!r}))"
    return instance.cli.python(code).strip()


@platform_test
def test_install_from_a_directory_and_reinstall_rules(instance, agent_project):
    """A second install of a taken identity is refused without --force. With --force, core refuses too for an agent
    that is not on PyPI: it reinstalls only when the installed version can be restored from PyPI if the new install
    fails (volttron.server.aip.install_agent_or_lib_source). Either way the installed agent is left as it was."""
    root, _wheel = agent_project
    first = instance.install_agent(agent_dir=root, vip_identity='install.dir', tag='dir')
    assert 'running' in _status(instance, 'install.dir').get('status', '')

    for force in (False, True):
        with pytest.raises(RuntimeError):
            instance.install_agent(agent_dir=root, vip_identity='install.dir', force=force, start=False)
        status = _status(instance, 'install.dir')
        assert status.get('agent_uuid') == first and 'running' in status.get('status', ''), force


@platform_test
def test_install_from_a_wheel(instance, agent_project):
    _root, wheel = agent_project
    instance.install_agent(agent_wheel=wheel, vip_identity='install.wheel', tag='wheel')
    assert 'running' in _status(instance, 'install.wheel').get('status', '')


@platform_test
def test_install_from_pypi_with_a_version_constraint_then_force_reinstall(instance):
    # The constraint selects 2.0.0rc2, an older release than the newest. It is installed but not started: it imports
    # volttron.utils.get_logger, which current volttron-core no longer has. 2.0.0rc3 runs.
    first = instance.install_agent(agent_dir='volttron-listener>=2.0.0rc0,<2.0.0rc3', vip_identity='install.pypi',
                                   config_file={'message': 'install test'}, start=False)
    assert _status(instance, 'install.pypi').get('agent_uuid') == first
    assert _installed_version(instance, 'volttron-listener') == '2.0.0rc2'

    second = instance.install_agent(agent_dir='volttron-listener==2.0.0rc3', vip_identity='install.pypi', force=True)
    status = _status(instance, 'install.pypi')
    assert second != first and status.get('agent_uuid') == second and 'running' in status.get('status', '')
    assert _installed_version(instance, 'volttron-listener') == '2.0.0rc3'


@platform_test
def test_install_library_from_a_directory(instance, tmp_path):
    root = _package(tmp_path / 'vtestinstalllib', 'vtestinstalllib', 'vtestinstalllib', 'VALUE = 42\n', '')
    try:
        assert instance.install_library(root) == 'vtestinstalllib-0.1.0'     # vctl reports name-version
        assert _recorded(instance, 'vtestinstalllib')
        assert instance.cli.python('import vtestinstalllib; print(vtestinstalllib.VALUE)').strip() == '42'
    finally:
        instance.remove_library('vtestinstalllib')
    assert not _recorded(instance, 'vtestinstalllib')
