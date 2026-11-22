"""volttrontesting.platform_cli: the environment project, the platform config, output parsing, and real platforms."""
import json
import os
import subprocess

import pytest

from volttrontesting import platform_cli
from volttrontesting.configs import AgentSources, AgentSpec
from volttrontesting.platform_cli import PlatformCLI, PlatformError, WebOptions, environment_project


# ---------------------------------------------------------------------------- environment_project
class FakeDist:
    def __init__(self, name, version='1.0', direct_url=None):
        self.metadata = {'Name': name}
        self.version = version
        self._direct_url = json.dumps(direct_url) if direct_url else None

    def read_text(self, filename):
        return self._direct_url if filename == 'direct_url.json' else None


def _project(monkeypatch, dists, exclude=()):
    monkeypatch.setattr(platform_cli.metadata, 'distributions', lambda: dists)
    return environment_project(exclude)


def test_project_pins_and_paths(monkeypatch):
    text = _project(monkeypatch, [
        FakeDist('volttron-core', '2.0.0rc40'),
        FakeDist('volttron-lib-modbus-driver', direct_url={'url': 'file:///src/pymodbus', 'dir_info': {'editable': True}}),
        FakeDist('patched-lib', direct_url={'url': 'file:///build/patched', 'dir_info': {}}),
        FakeDist('backports.tarfile', '1.2.0'),
        FakeDist('volttron', '9.0'),
    ])
    assert '"volttron-core" = "2.0.0rc40"' in text
    assert '"volttron-lib-modbus-driver" = {path = "/src/pymodbus", develop = true}' in text
    assert '"patched-lib" = {path = "/build/patched", develop = false}' in text
    assert '"backports.tarfile" = "1.2.0"' in text
    assert '"volttron" =' not in text                      # the project itself is named volttron
    assert 'package-mode = false' in text


def test_project_excludes_by_canonical_name(monkeypatch):
    text = _project(monkeypatch, [FakeDist('volttron_testing'), FakeDist('Protocol.Proxy-DNP3'), FakeDist('keep-me')],
                    exclude=['volttron-testing', 'protocol-proxy-dnp3'])
    assert 'volttron_testing' not in text and 'Protocol.Proxy-DNP3' not in text and '"keep-me"' in text


def test_project_lists_each_distribution_once_preferring_the_checkout(monkeypatch):
    text = _project(monkeypatch, [
        FakeDist('resource-bridge', '0.1.0'),                                         # a checkout's stray egg-info
        FakeDist('resource-bridge', direct_url={'url': 'file:///src/rb', 'dir_info': {'editable': True}}),
        FakeDist('resource-bridge', '0.1.0'),
    ])
    assert text.count('"resource-bridge"') == 1 and 'path = "/src/rb"' in text


# ---------------------------------------------------------------------------- platform_config
def test_default_config_is_ipc_only(tmp_path):
    cli = PlatformCLI(tmp_path / 'volttron_home')
    config = cli.platform_config()
    assert 'instance-name = volttron-test' in config and 'auth-enabled = True' in config
    assert 'address' not in config and '[web]' not in config
    assert cli.bind_address is None and cli.web_port is None


def test_auto_address_and_web(tmp_path):
    cli = PlatformCLI(tmp_path / 'volttron_home', 'web-e2e', address='auto', web=True,
                      rpc_allow_list=['platform.control: peerlist', 'listener: *'], extra_config='[extra]\nkey = 1')
    config = cli.platform_config()
    assert cli.address.startswith('tcp://127.0.0.1:') and f'address = {cli.address}' in config
    assert cli.bind_address.startswith('http://127.0.0.1:') and cli.web_port == int(cli.bind_address.rsplit(':', 1)[1])
    assert f'bind-web-address = {cli.bind_address}' in config and len(cli.web.secret_key) == 64
    assert 'rpc-allow-list =\n    platform.control: peerlist\n    listener: *\n' in config
    assert config.endswith('[extra]\nkey = 1\n')


def test_explicit_web_options_are_kept(tmp_path):
    cli = PlatformCLI(tmp_path / 'h', web=WebOptions('http://127.0.0.1:18443', 'k' * 64))
    assert cli.bind_address == 'http://127.0.0.1:18443' and cli.web.secret_key == 'k' * 64


def test_missing_reports_what_the_environment_lacks(monkeypatch):
    from volttrontesting import adapters
    monkeypatch.setattr(platform_cli, '_installed', lambda name: False)
    monkeypatch.setattr(adapters, '_installed', lambda name: False)
    adapters._load.cache_clear()
    monkeypatch.setattr(platform_cli.importlib.util, 'find_spec', lambda name: None)
    missing = PlatformCLI.missing(driver=True, web=True)
    assert any('message bus' in m for m in missing) and any('Platform Driver' in m for m in missing)
    assert any('auth module' in m for m in missing)
    assert 'volttron-lib-web' in missing and not PlatformCLI.available()


# ---------------------------------------------------------------------------- vctl driver output
@pytest.mark.parametrize('output, expected', [
    ("[{'devices/a/b/p': 1.5}, {}]\n", [{'devices/a/b/p': 1.5}, {}]),
    ("INFO something\n({'t': True}, {'t2': 'error'})\n", ({'t': True}, {'t2': 'error'})),
    ("{'devices/a': 'reverted'}\n", {'devices/a': 'reverted'}),
])
def test_driver_output_parsing(output, expected):
    assert platform_cli._literal(output) == expected


def test_driver_output_without_a_literal():
    with pytest.raises(PlatformError, match='unexpected vctl driver output'):
        platform_cli._literal('Platform Driver Agent must be running\n')


def _fake_run(outputs):
    calls = []

    def run(self, args, check=True, timeout=120.0):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, outputs.pop(0), '')
    return run, calls


def test_point_helpers(tmp_path, monkeypatch):
    run, calls = _fake_run(["[{'c/b/d/T': 70.5}, {}]", "[{'c/b/d/T': 72}, {}]", "[{}, {'c/b/d/X': 'no such point'}]"])
    monkeypatch.setattr(PlatformCLI, '_run', run)
    cli = PlatformCLI(tmp_path / 'h')
    assert cli.get_point('c/b/d/T') == 70.5
    assert cli.set_point('c/b/d/T', 72) == 72
    with pytest.raises(PlatformError, match='no such point'):
        cli.get('c/b/d/X')
    assert calls[1] == ['vctl', 'driver', 'set', '--', 'c/b/d/T', '72']


def test_install_resolves_sources_and_passes_configs(tmp_path, monkeypatch):
    installed = []
    monkeypatch.setattr(PlatformCLI, 'install_agent',
                        lambda self, source, identity, tag, config=None: installed.append((source, identity, tag, config)))
    sources = AgentSources().register('my-agent', distribution='my-agent-dist')
    timings = PlatformCLI(tmp_path / 'h').install(
        [AgentSpec('my-agent', 'my.agent', 'mine', config='cfg.json'), AgentSpec('/src/other', 'other', 'o')], sources)
    assert installed == [('my-agent-dist', 'my.agent', 'mine', 'cfg.json'), ('/src/other', 'other', 'o', None)]
    assert set(timings) == {'my.agent', 'other'}


def test_grant_and_install_agent_command_lines(tmp_path, monkeypatch):
    run, calls = _fake_run(['', '', 'UUID  AGENT  IDENTITY  TAG  STATUS\nabc  l-1  my.agent  mine  running [42]\n'])
    monkeypatch.setattr(PlatformCLI, '_run', run)
    cli = PlatformCLI(tmp_path / 'h')
    cli.grant('platform.web', 'platform.config_store.set_config', 'platform.config_store.delete_config')
    cli.install_agent('/src/agent', 'my.agent', 'mine', config='cfg.json')
    assert calls[0] == ['vctl', 'authz', 'add', 'agent', 'platform.web', '--rpc-capabilities',
                        'platform.config_store.set_config', 'platform.config_store.delete_config']
    assert calls[1] == ['vctl', 'install', '/src/agent', '--vip-identity', 'my.agent', '--tag', 'mine',
                        '--agent-config', 'cfg.json', '--start']


# ---------------------------------------------------------------------------- real platforms
@pytest.mark.platform
@pytest.mark.timeout(1200)
@pytest.mark.skipif(bool(os.environ.get('VOLTTRON_TESTING_SKIP_PLATFORM')), reason='VOLTTRON_TESTING_SKIP_PLATFORM is set')
@pytest.mark.skipif(not PlatformCLI.available(), reason=f'cannot start a platform here: {PlatformCLI.missing()}')
def test_two_platforms_side_by_side_and_an_agent(tmp_path):
    """Two IPC-only platforms in one test, an agent installed by short name, a restart observed in the log."""
    first = PlatformCLI(tmp_path / 'one' / 'volttron_home', 'cli-one')
    second = PlatformCLI(tmp_path / 'two' / 'volttron_home', 'cli-two')
    with first, second:
        assert 'platform.control' in first.vctl('peerlist') and 'platform.control' in second.vctl('peerlist')
        first.install([AgentSpec('listener', 'listener', 'listener')])
        assert 'listener' in first.vctl('status') and 'listener' not in second.vctl('status')
        since = first.log_size()
        first.restart_agent('listener', 'listener')
        assert first.wait_for_log(r'listener', timeout=60, since=since)
    assert first.process is None and second.process is None


# ---------------------------------------------------------------------------- prepare / launch / vctl_json
def test_prepare_keeps_the_recorded_project(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_cli, 'environment_project', lambda exclude=(): 'pinned project\n')
    cli = PlatformCLI(tmp_path / 'volttron_home', 'prep')
    cli.prepare()
    assert (cli.home / 'pyproject.toml').read_text() == 'pinned project\n'
    assert 'instance-name = prep' in (cli.home / 'config').read_text()
    (cli.home / 'pyproject.toml').write_text('pinned project\n"an-installed-agent" = "1.0"\n')    # what the platform records
    cli.instance_name = 'renamed'
    cli.prepare()
    assert 'an-installed-agent' in (cli.home / 'pyproject.toml').read_text()       # a restart keeps installed agents
    assert 'instance-name = renamed' in (cli.home / 'config').read_text()          # the config follows the settings


def test_env_base_is_used(tmp_path):
    cli = PlatformCLI(tmp_path / 'h', env={'PATH': '/opt/bin', 'MARKER': 'from the wrapper'})
    assert cli.env['MARKER'] == 'from the wrapper' and cli.env['PATH'].endswith(':/opt/bin')
    assert cli.env['VOLTTRON_HOME'] == str(tmp_path / 'h')


def test_monitor_frequency_in_config(tmp_path):
    assert 'agent-monitor-frequency = 30' in PlatformCLI(tmp_path / 'h', agent_monitor_frequency=30).platform_config()


@pytest.mark.parametrize('output, expected', [
    ('{\n    "agent_uuid": "u1"\n}\n', {'agent_uuid': 'u1'}),
    ('Installing from wheel /x.whl\n{\n    "agent_uuid": "u2"\n}\n', {'agent_uuid': 'u2'}),     # volttron-core#364
    ('[{"a": 1}]', [{'a': 1}]),
])
def test_vctl_json(tmp_path, monkeypatch, output, expected):
    run, calls = _fake_run([output])
    monkeypatch.setattr(PlatformCLI, '_run', run)
    assert PlatformCLI(tmp_path / 'h').vctl_json('install', '/x.whl') == expected
    assert calls[0][:2] == ['vctl', '--json']


def test_vctl_json_without_a_document(tmp_path, monkeypatch):
    run, _ = _fake_run(['Agent installed\n'])
    monkeypatch.setattr(PlatformCLI, '_run', run)
    with pytest.raises(PlatformError, match='no JSON document'):
        PlatformCLI(tmp_path / 'h').vctl_json('status')
