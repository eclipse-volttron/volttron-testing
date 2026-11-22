"""volttrontesting.configs: AgentSources resolution order."""
import pytest

from volttrontesting import configs
from volttrontesting.configs import AgentSources, AgentSpec


def _checkout(path):
    path.mkdir(parents=True)
    (path / 'pyproject.toml').write_text('[tool.poetry]\n')
    return path


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for variable in ('VOLTTRON_MODULAR', 'VOLTTRON_MODULAR_DRIVERS', 'DER_CONTROL_MODULES'):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(configs, 'editable_source', lambda distribution: None)


def test_unknown_names_and_paths_pass_through():
    sources = AgentSources()
    assert sources.resolve('volttron-sqlite-historian') == 'volttron-sqlite-historian'
    assert sources.resolve('/some/checkout') == '/some/checkout'


def test_published_component_falls_back_to_its_distribution():
    assert AgentSources().resolve('platform-driver') == 'volttron-platform-driver'
    assert AgentSources().resolve('listener') == 'volttron-listener'


def test_tree_variable_wins(tmp_path, monkeypatch):
    drivers = tmp_path / 'drivers'
    _checkout(drivers / 'platform-driver-agent')
    monkeypatch.setenv('VOLTTRON_MODULAR_DRIVERS', str(drivers))
    assert AgentSources().resolve('platform-driver') == str(drivers / 'platform-driver-agent')


def test_tree_variable_without_the_checkout_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv('VOLTTRON_MODULAR_DRIVERS', str(tmp_path / 'nowhere'))
    assert AgentSources().resolve('fake-driver') == 'volttron-lib-fake-driver'


def test_editable_install_is_used(tmp_path, monkeypatch):
    checkout = _checkout(tmp_path / 'fake')
    monkeypatch.setattr(configs, 'editable_source',
                        lambda distribution: checkout if distribution == 'volttron-lib-fake-driver' else None)
    sources = AgentSources()
    assert sources.resolve('fake-driver') == str(checkout)
    assert sources.checkout('fake-driver') == checkout


def test_modular_root_found_from_the_anchor(tmp_path):
    root = tmp_path / 'modular'
    (root / 'base').mkdir(parents=True)
    driver = _checkout(root / 'drivers' / 'platform-driver-agent')
    anchor = _checkout(root / 'web') / 'tests' / 'e2e' / 'conftest.py'
    anchor.parent.mkdir(parents=True)
    anchor.write_text('')
    assert AgentSources(anchor=anchor).resolve('platform-driver') == str(driver)


def test_modular_root_from_the_environment(tmp_path, monkeypatch):
    driver = _checkout(tmp_path / 'm' / 'drivers' / 'interfaces' / 'pymodbus')
    monkeypatch.setenv('VOLTTRON_MODULAR', str(tmp_path / 'm'))
    assert AgentSources().resolve('modbus-driver') == str(driver)


def test_registered_unpublished_component(tmp_path, monkeypatch):
    sources = AgentSources().register('interoperability-service', tree=('DER_CONTROL_MODULES', 'interoperability_service'),
                                      published=False)
    with pytest.raises(LookupError, match=r'\$DER_CONTROL_MODULES'):
        monkeypatch.setenv('DER_CONTROL_MODULES', str(tmp_path))
        sources.resolve('interoperability-service')
    service = _checkout(tmp_path / 'interoperability_service')
    assert sources.resolve('interoperability-service') == str(service)


def test_agent_spec_defaults():
    spec = AgentSpec('platform-driver', 'platform.driver', 'driver')
    assert spec.config is None
