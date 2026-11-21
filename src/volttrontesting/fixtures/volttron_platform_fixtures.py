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

import os
import shutil
from pathlib import Path
from typing import Optional

import psutil
import pytest
from volttrontesting import adapters
from volttrontesting.fixtures.params import platform_params    # noqa: F401  re-exported

# is_web_available
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
