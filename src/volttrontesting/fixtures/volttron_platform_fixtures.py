# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2022 Battelle Memorial Institute
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

import contextlib
import os
import shutil
from pathlib import Path
from typing import Optional

import psutil
import pytest
from volttrontesting import adapters

# is_web_available
#from volttron.platform import update_platform_config
from volttrontesting.fixtures.cert_fixtures import certs_profile_1
from volttrontesting.platformwrapper import (
    PlatformWrapper,
    create_volttron_home,
    with_os_environ,
)
from volttrontesting.utils import (
    get_hostname_and_random_port,
    get_rand_ip_and_port,
    get_rand_vip,
)


PRINT_LOG_ON_SHUTDOWN = False


def platform_params() -> list:
    """Fixture parameters for real platforms: one per selected message bus, with the environment's auth module.

    Buses come from ``adapters.selected_buses()`` (``VOLTTRON_TEST_MESSAGEBUS``, else every available bus); the auth
    module is the one installed (``adapters.selected_auth()``), because the platform uses whichever is installed. A
    pair the auth module does not support is skipped, and an environment with no bus or no auth module yields one
    skipped parameter that says why. Ids read ``<bus>-<auth>``, for example ``zmq-libauth``.
    """
    try:
        buses = adapters.selected_buses()
        auth = adapters.selected_auth()
    except adapters.AdapterError as e:
        return [pytest.param(None, id="no-platform", marks=pytest.mark.skip(reason=str(e)))]
    if not buses:
        return [pytest.param(None, id="no-bus", marks=pytest.mark.skip(
            reason=f"no message bus available; registered: {', '.join(adapters.registered('bus'))}"))]
    params = []
    for bus in buses:
        marks = () if adapters.auth_supports(auth, bus) else \
            pytest.mark.skip(reason=f"auth module {auth!r} does not support message bus {bus!r}")
        params.append(pytest.param(dict(messagebus=bus, ssl_auth=True), id=f"{bus}-{auth}", marks=marks))
    return params


def print_log(volttron_home):
    if PRINT_LOG_ON_SHUTDOWN:
        if os.environ.get('PRINT_LOGS', PRINT_LOG_ON_SHUTDOWN):
            log_path = volttron_home + "/volttron.log"
            if os.path.exists(log_path):
                with open(volttron_home + "/volttron.log") as fin:
                    print(fin.read())
            else:
                print('NO LOG FILE AVAILABLE.')


def build_wrapper(address: str,
                  should_start: bool = True,
                  messagebus: str = 'zmq',
                  remote_platform_ca: Optional[str] = None,
                  instance_name: Optional[str] = None, agent_isolation_mode: bool = False, **kwargs):
    wrapper = PlatformWrapper(ssl_auth=kwargs.pop('ssl_auth', False),
                              messagebus=messagebus,
                              instance_name=instance_name,
                              agent_isolation_mode=agent_isolation_mode,
                              remote_platform_ca=remote_platform_ca)
    if should_start:
        try:
            wrapper.startup_platform(address=address, **kwargs)
        except BaseException:
            # A platform that did not start fails the fixture here, with its own error, rather than the test later
            # with an unrelated one. Clean up what did start first.
            cleanup_wrapper(wrapper)
            raise
    return wrapper


def cleanup_wrapper(wrapper):
    print('Shutting down instance: {0}, MESSAGE BUS: {1}'.format(wrapper.volttron_home,
                                                                 wrapper.messagebus))
    # if wrapper.is_running():
    #     wrapper.remove_all_agents()
    # Shutdown handles case where the platform hasn't started.
    wrapper.shutdown_platform()
    if wrapper.p_process is not None and wrapper.p_process.pid:
        if psutil.pid_exists(wrapper.p_process.pid):
            proc = psutil.Process(wrapper.p_process.pid)
            proc.terminate()
    # Check cleanup only if not in debug mode and directory still exists
    if not wrapper.debug_mode:
        parent_path = Path(wrapper.volttron_home).parent
        if parent_path.exists():
            # Try to clean up manually if shutdown didn't do it
            try:
                shutil.rmtree(str(parent_path))
            except Exception as e:
                print(f"Warning: Could not clean up {parent_path}: {e}")
                # Don't assert on cleanup failures as it's not a test failure
                pass


def cleanup_wrappers(platforms):
    for p in platforms:
        cleanup_wrapper(p)


@pytest.fixture(scope="module", params=platform_params())
def volttron_instance(request, **kwargs):
    """A started platform, once per selected message bus (see ``platform_params``).

    @param request: pytest request object
    @return: volttron platform instance
    """
    address = kwargs.pop("address", get_rand_vip())
    wrapper = build_wrapper(address,
                            messagebus=request.param['messagebus'],
                            ssl_auth=request.param['ssl_auth'],
                            **kwargs)
    # Only get pid if p_process exists (won't in mock mode)
    wrapper_pid = wrapper.p_process.pid if wrapper.p_process else None

    try:
        yield wrapper
    except Exception as ex:
        print(ex.args)
    finally:
        cleanup_wrapper(wrapper)
        if not wrapper.debug_mode:
            assert not Path(wrapper.volttron_home).exists()
        # Final way to kill off the platform wrapper for the tests.
        if wrapper_pid and psutil.pid_exists(wrapper_pid):
            psutil.Process(wrapper_pid).kill()


# Use this fixture to get more than 1 volttron instance for test.
# Usage example:
# def test_function_that_uses_n_instances(request, get_volttron_instances):
#     instances = get_volttron_instances(3)
#
# TODO allow rmq to be added to the multi platform request.
@pytest.fixture(scope="module", params=[dict(messagebus='mock', ssl_auth=False)])
def get_volttron_instances(request):
    """ Fixture to get more than 1 volttron instance for test
    Use this fixture to get more than 1 volttron instance for test. This
    returns a function object that should be called with number of instances
    as parameter to get a list of volttron instnaces. The fixture also
    takes care of shutting down all the instances at the end

    Example Usage:

    def test_function_that_uses_n_instances(get_volttron_instances):
        instance1, instance2, instance3 = get_volttron_instances(3)

    @param request: pytest request object
    @return: function that can used to get any number of
        volttron instances for volttrontesting.
    """
    instances = []

    def get_n_volttron_instances(n, should_start=True, **kwargs):
        nonlocal instances
        get_n_volttron_instances.count = n
        instances = []
        for i in range(0, n):
            address = kwargs.pop("address", get_rand_vip())

            wrapper = build_wrapper(address,
                                    should_start=should_start,
                                    messagebus=request.param['messagebus'],
                                    ssl_auth=request.param['ssl_auth'],
                                    **kwargs)
            instances.append(wrapper)
        if should_start:
            for w in instances:
                assert w.is_running()
        # instances = instances if n > 1 else instances[0]
        # setattr(get_n_volttron_instances, 'instances', instances)
        get_n_volttron_instances.instances = instances if n > 1 else instances[0]
        return instances if n > 1 else instances[0]

    def cleanup():
        nonlocal instances
        print(f"My instances: {get_n_volttron_instances.count}")
        if isinstance(get_n_volttron_instances.instances, PlatformWrapper):
            print('Shutting down instance: {}'.format(get_n_volttron_instances.instances))
            cleanup_wrapper(get_n_volttron_instances.instances)
            return

        for i in range(0, get_n_volttron_instances.count):
            print('Shutting down instance: {}'.format(
                get_n_volttron_instances.instances[i].volttron_home))
            cleanup_wrapper(get_n_volttron_instances.instances[i])

    try:
        yield get_n_volttron_instances
    finally:
        cleanup()


# Use this fixture when you want a single instance of volttron platform for zmq message bus
# test
@contextlib.contextmanager
def get_test_volttron_home(messagebus: str,
                           web_https=False,
                           web_http=False,
                           has_vip=True,
                           volttron_home: str = None,
                           config_params: dict = None,
                           env_options: dict = None):
    """
    Create a full volttronn_home test environment with all of the options available in the environment
    (os.environ) and configuration file (volttron_home/config) in order to test from.

    @param messagebus:
        A message bus name with an available adapter
    @param web_https:
        Determines if https should be used and enabled.  If this is specified then the cert_fixtures.certs_profile_1
        function will be used to generate certificates for  the server and signed ca.  Either web_https or web_http
        may be specified not both.
    @param config_params:
        Configuration parameters that should go into the volttron configuration file, note if the basic ones are
        set via the previous arguments (i.e. web_https) then it is an error to specify bind-web-address (or other)
        duplicate.
    @param env_options:
        Other options that should be specified in the os.environ during the setup of this environment.
    """
    # Make these not None so that we can use set operations on them to see if we have any overlap between
    # common configuration params and environment.
    if config_params is None:
        config_params = {}
    if env_options is None:
        env_options = {}

    # make a copy so we can restore in cleanup
    env_cpy = os.environ.copy()

    # start validating input
    adapters.adapter_class("bus", messagebus)

    if web_http and web_https:
        raise ValueError(
            "Incompatabile tyeps web_https and web_Http cannot both be specified as True")

    default_env_options = ('VOLTTRON_HOME', 'MESSAGEBUS')

    for v in default_env_options:
        if v in env_options:
            raise ValueError(f"Cannot specify {v} in env_options as it is set already.")

    # All is well.Create vhome
    if volttron_home:
        os.makedirs(volttron_home, exist_ok=True)
    else:
        volttron_home = create_volttron_home()

    # Create env
    envs = dict(VOLTTRON_HOME=volttron_home, MESSAGEBUS=messagebus)
    os.environ.update(envs)
    os.environ.update(env_options)

    # make the top level dirs
    os.mkdir(os.path.join(volttron_home, "agents"))
    os.mkdir(os.path.join(volttron_home, "configuration_store"))
    os.mkdir(os.path.join(volttron_home, "keystores"))
    os.mkdir(os.path.join(volttron_home, "run"))

    # create the certs. This will create the certs dirs
    web_certs_dir = os.path.join(volttron_home, "web_certs")
    web_certs = None
    if web_https:
        web_certs = certs_profile_1(web_certs_dir)

    address = None
    bind_web_address = None
    web_ssl_cert = None
    web_ssl_key = None
    web_secret_key = None

    config_file = {}
    if web_http or web_https:
        ip, port = get_rand_ip_and_port()
        address = f"tcp://{ip}:{port}"

    # if web_https:
    #     hostname, port = get_hostname_and_random_port()
    #     bind_web_address = f"https://{hostname}:{port}"
    #     web_ssl_cert = web_certs.server_certs[0].cert_file
    #     web_ssl_key = web_certs.server_certs[0].key_file
    # elif web_http:
    #     hostname, port = get_hostname_and_random_port()
    #     bind_web_address = f"http://{hostname}:{port}"
    #     web_secret_key = get_random_key()

    if address:
        config_file['address'] = address
    if bind_web_address:
        config_file['bind-web-address'] = bind_web_address
    if web_ssl_cert:
        config_file['web-ssl-cert'] = web_ssl_cert
    if web_ssl_key:
        config_file['web-ssl-key'] = web_ssl_key
    if web_secret_key:
        config_file['web-secret-key'] = web_secret_key

    config_intersect = set(config_file).intersection(set(config_params))
    if len(config_intersect) > 0:
        raise ValueError(
            f"passed configuration params {list(config_intersect)} are built internally")

    config_file.update(config_params)

    update_platform_config(config_file)

    try:
        yield volttron_home
    finally:
        os.environ.clear()
        os.environ.update(env_cpy)
        if not os.environ.get("DEBUG", 0) != 1 and not os.environ.get("DEBUG_MODE", 0):
            shutil.rmtree(volttron_home, ignore_errors=True)


@pytest.fixture(scope="module", params=platform_params())
def platforms(request):
    """A factory for independent platforms in one test module: ``a, b = platforms(2)``, once per selected message
    bus (see ``platform_params``).

    Each wrapper has its own VOLTTRON_HOME and a TCP ``address``, and its ``serverkey`` once started, which is what an
    agent on one platform needs to connect to another (not wired up here; forwarder and federation tests will add
    that). Unlike ``build_wrapper``, a platform that fails to start fails the test. Every platform the factory
    started is shut down when the module finishes. Keyword arguments go to ``PlatformWrapper``.
    """
    started = []

    def start(n: int = 1, **kwargs):
        new = []
        for _ in range(n):
            wrapper = PlatformWrapper(messagebus=request.param['messagebus'],
                                      ssl_auth=kwargs.pop('ssl_auth', request.param['ssl_auth']), **kwargs)
            started.append(wrapper)
            wrapper.startup_platform(address=get_rand_vip())
            new.append(wrapper)
        return new

    yield start
    for wrapper in started:
        cleanup_wrapper(wrapper)
