"""TestServer's RPC routing (RPCWrapper) and configuration store (ConfigStoreWrapper), step 7 of the testing plan."""
import gevent
import pytest

from volttrontesting.mock_core_builder import MockCoreBuilder  # noqa: F401  registers the mock core builder
from volttrontesting.server_mock import TestServer

from volttron.client import Agent
from volttron.client.vip.agent import RPC
from volttron.client.vip.agent.errors import Unreachable
from volttron.types.auth.auth_credentials import Credentials
from volttron.utils.jsonrpc import MethodNotFound, RemoteError


class Target(Agent):
    def __init__(self, identity='target', **kwargs):
        super().__init__(credentials=Credentials(identity=identity), name='mock', **kwargs)

    @RPC.export
    def echo(self, value, scale=1):
        return {'value': value, 'scale': scale, 'caller': self.vip.rpc.context.vip_message.peer}

    @RPC.export
    def fail(self):
        raise ValueError('bad value')

    @RPC.export
    def call_back(self):
        return self.vip.rpc.call(self.vip.rpc.context.vip_message.peer, 'echo', 'back').get(timeout=5)


class Configured(Agent):
    """Records every config callback as (name, action, contents)."""

    def __init__(self, identity='configured', **kwargs):
        super().__init__(credentials=Credentials(identity=identity), name='mock', **kwargs)
        self.events = []
        self.vip.config.set_default('config', {'default': True})
        self.vip.config.subscribe(self.on_config, actions=['NEW', 'UPDATE', 'DELETE'], pattern='*')

    def on_config(self, name, action, contents):
        self.events.append((name, action, contents))


def _start(server, agent):
    return server.start_agent(agent)


@pytest.fixture
def server():
    return TestServer(route_rpc=True)


# ------------------------------------------------------------------------------------------------ RPC

def test_a_call_reaches_the_peer_with_the_caller_in_its_context(server):
    caller, _target = _start(server, Target('caller')), _start(server, Target('target'))
    assert caller.vip.rpc.call('target', 'echo', (1, 2), scale=3).get(timeout=5) == \
        {'value': [1, 2], 'scale': 3, 'caller': 'caller'}          # a tuple arrives as a list, as on the wire


def test_errors_are_what_a_platform_caller_gets(server):
    caller = _start(server, Target('caller'))
    _start(server, Target('target'))
    with pytest.raises(MethodNotFound):
        caller.vip.rpc.call('target', 'nonexistent').get(timeout=5)
    with pytest.raises(RemoteError) as info:
        caller.vip.rpc.call('target', 'fail').get(timeout=5)
    assert info.value.message == 'bad value' and info.value.exc_info['exc_type'] == 'ValueError'
    with pytest.raises(Unreachable):
        caller.vip.rpc.call('nobody', 'echo', 1).get(timeout=5)


def test_a_method_may_call_back_into_its_caller(server):
    caller, _target = _start(server, Target('caller')), _start(server, Target('target'))
    assert caller.vip.rpc.call('target', 'call_back').get(timeout=5)['caller'] == 'target'


def test_inspect_works_as_on_a_platform(server):
    caller = _start(server, Target('caller'))
    _start(server, Target('target'))
    assert 'echo' in caller.vip.rpc.call('target', 'inspect').get(timeout=5)['methods']
    params = caller.vip.rpc.call('target', 'echo.inspect').get(timeout=5)['params']
    assert list(params) == ['value', 'scale']


def test_routing_is_off_by_default():
    server = TestServer()
    agent = Target('plain')
    original = agent.vip.rpc.call
    server.connect_agent(agent)
    assert agent.vip.rpc.call == original


def test_a_disconnected_agent_is_unreachable(server):
    caller, target = _start(server, Target('caller')), _start(server, Target('target'))
    server.disconnect_agent(target)
    with pytest.raises(Unreachable):
        caller.vip.rpc.call('target', 'echo', 1).get(timeout=5)
    _start(server, Target('target'))                     # the identity is free again
    assert caller.vip.rpc.call('target', 'echo', 1).get(timeout=5)['value'] == 1


# ------------------------------------------------------------------------------------------------ config store

def test_configs_stored_before_start_arrive_in_the_first_batch(server):
    server.config_store.store('configured', 'config', {'a': 1})
    server.config_store.store('configured', 'registry.csv', 'Point,Value\nx,1\ny,2\n', 'csv')
    agent = _start(server, Configured())
    assert agent.events[0] == ('config', 'NEW', {'a': 1})       # "config" first, as core orders it
    assert ('registry.csv', 'NEW', [{'Point': 'x', 'Value': '1'}, {'Point': 'y', 'Value': '2'}]) in agent.events
    assert agent.vip.config.get('registry.csv')[1] == {'Point': 'y', 'Value': '2'}


def test_without_stored_configs_the_default_arrives(server):
    agent = _start(server, Configured())
    assert agent.events == [('config', 'NEW', {'default': True})]


def test_later_stores_and_deletes_send_update_and_delete(server):
    agent = _start(server, Configured())
    server.config_store.store('configured', 'other', {'b': 1})
    server.config_store.store('configured', 'other', {'b': 2})
    server.config_store.store('configured', 'config', {'a': 9})     # over the default: UPDATE
    server.config_store.delete_config('configured', 'other')
    assert agent.events[1:] == [('other', 'NEW', {'b': 1}), ('other', 'UPDATE', {'b': 2}),
                                ('config', 'UPDATE', {'a': 9}), ('other', 'DELETE', None)]


def test_a_config_that_refers_to_another_is_updated_with_it(server):
    server.config_store.store('configured', 'devices/a', {'registry': 'config://registry.csv'})
    server.config_store.store('configured', 'registry.csv', 'Point\nx\n', 'csv')
    agent = _start(server, Configured())
    assert ('devices/a', 'NEW', {'registry': [{'Point': 'x'}]}) in agent.events
    agent.events.clear()
    server.config_store.store('configured', 'registry.csv', 'Point\nz\n', 'csv')
    assert ('devices/a', 'UPDATE', {'registry': [{'Point': 'z'}]}) in agent.events


def test_the_agents_own_set_and_delete_land_in_the_store(server):
    agent = _start(server, Configured())
    gevent.spawn(agent.vip.config.set, 'saved', {'c': 1}, trigger_callback=True).get(timeout=5)
    assert server.config_store.get('configured', 'saved') == {'c': 1}
    assert ('saved', 'NEW', {'c': 1}) in agent.events
    gevent.spawn(agent.vip.config.delete, 'saved').get(timeout=5)
    assert server.config_store.names('configured') == []


def test_store_errors_are_the_platforms(server):
    with pytest.raises(KeyError):
        server.config_store.delete_config('configured', 'missing')
    with pytest.raises(ValueError):
        server.config_store.store('configured', 'loop', {'self': 'config://loop'})


# ------------------------------------------------------------------------------------------------ live schedules

def test_live_schedules_run_at_their_deadlines():
    from datetime import datetime, timedelta
    from volttron.client.vip.agent.core import ScheduledEvent
    from volttron.utils.scheduling import periodic
    server = TestServer(run_schedules=True)
    agent = server.start_agent(Target('scheduler'))
    calls = []
    once = agent.core.schedule(datetime.now() + timedelta(seconds=0.1), calls.append, 'once')
    repeating = agent.core.schedule(periodic(0.05), calls.append, 'tick')
    cancelled = agent.core.schedule(datetime.now() + timedelta(seconds=0.1), calls.append, 'never')
    cancelled.cancel()
    assert isinstance(once, ScheduledEvent)
    gevent.sleep(0.3)
    repeating.cancel()
    assert 'once' in calls and calls.count('tick') >= 3 and 'never' not in calls and once.finished
    later = agent.core.spawn_later(0.05, calls.append, 'spawned')
    later.join(timeout=1)
    assert calls[-1] == 'spawned'
    server.stop_agent(agent)                     # cancels what is left
