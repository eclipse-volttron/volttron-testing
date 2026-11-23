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

import configparser as configparser
import logging
import dataclasses
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from configparser import ConfigParser
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from subprocess import CalledProcessError
from typing import List, Optional, Union

import gevent
import gevent.subprocess as subprocess
import grequests
import psutil
import yaml

#from volttrontesting.fixtures.cert_fixtures import certs_profile_2
# from .agent_additions import add_volttron_central, add_volttron_central_platform
from gevent.fileobject import FileObject
from gevent.subprocess import Popen
from volttron.client.known_identities import (
    AUTH,
    CONTROL,
    CONTROL_CONNECTION,
    PLATFORM,
    PLATFORM_WEB,
    PROCESS_IDENTITIES,
)
from volttron.client.vip.agent import Agent
from volttron.server.aip import AIPplatform
from volttron.types.agent_context import AgentOptions
from volttron.types.auth import Authenticator, PKICredentials, VolttronCredentials
from volttron.types.auth.auth_service import AuthService
from volttron.types.server_config import ServerConfig, ServiceConfigs

# from volttron.platform import packaging
from volttron.utils import (
    ClientContext,
    execute_command,
    jsonapi,
    store_message_bus_config,
    strip_comments,
)
from volttron.utils.certs import Certs
from volttron.utils.commands import is_volttron_running, wait_for_volttron_startup

# from volttron.utils.rmq_setup import start_rabbit, stop_rabbit
# from volttron.utils.rmq_setup import setup_rabbitmq_volttron
from volttron.utils.context import ClientContext as cc

#from volttron.client.vip.agent.connection import Connection
from volttrontesting.adapters import NO_AUTH, AdapterError, adapter_class, auth_supports, selected_auth
from volttrontesting.platform_cli import PlatformCLI
from volttrontesting.utils import (
    get_hostname_and_random_port,
    get_rand_ip_and_port,
    get_rand_tcp_address,
    get_rand_vip,
)



@dataclass
class InstallAgentOptions:
    """Options for ``PlatformWrapper.install_agent``, as an alternative to its keyword arguments.

    The fields are the keywords of the same names (``agent_config`` is ``config_file``) and have the same defaults.
    Each field set to something other than its default overrides the keyword; the others leave the keyword as
    given."""
    start: bool = True
    vip_identity: Optional[str] = None
    agent_config: Optional[Union[dict, str]] = None
    startup_time: int = 5
    force: bool = False
    tag: Optional[str] = None
    priority: Optional[int] = None


_log = logging.getLogger(__name__)

RESTRICTED_AVAILABLE = False

# Change the connection timeout to default to 5 seconds rather than the default
# of 30 secondes
DEFAULT_TIMEOUT = 5

auth = None
certs = None

# Filenames for the config files which are created during setup and then
# passed on the command line
TMP_PLATFORM_CONFIG_FILENAME = "config"

# Used to fill in TWISTED_CONFIG template
TEST_CONFIG_FILE = 'base-platform-test.json'

PLATFORM_CONFIG_RESTRICTED = """
mobility-address = {mobility-address}
control-socket = {tmpdir}/run/control
resource-monitor = {resource-monitor}
"""

TWISTED_CONFIG = """
[report 0]
ReportDeliveryLocation = {smap-uri}/add/{smap-key}

[/]
type = Collection
Metadata/SourceName = {smap-source}
uuid = {smap-uuid}

[/datalogger]
type = volttron.drivers.data_logger.DataLogger
interval = 1

"""

UNRESTRICTED = 0
VERIFY_ONLY = 1
RESOURCE_CHECK_ONLY = 2
RESTRICTED = 3

MODES = (UNRESTRICTED, VERIFY_ONLY, RESOURCE_CHECK_ONLY, RESTRICTED)

VOLTTRON_ROOT = os.environ.get("VOLTTRON_ROOT")
if not VOLTTRON_ROOT:
    VOLTTRON_ROOT = '/home/volttron/git/volttron-core'    # dirname(dirname(dirname(os.path.realpath(__file__))))

VSTART = "volttron"
VCTRL = "volttron-ctl"
TWISTED_START = "twistd"

SEND_AGENT = "send"

RUN_DIR = 'run'
PUBLISH_TO = RUN_DIR + '/publish'
SUBSCRIBE_TO = RUN_DIR + '/subscribe'


class PlatformWrapperError(Exception):
    pass


# TODO: This partially duplicates functionality in volttron-core.utils.messagebus.py. These should probably be combined.
def create_platform_config_file(message_bus, instance_name, vip_address, agent_monitor_frequency,
                                agent_isolation_mode):
    # If there is no config file or home directory yet, create volttron_home
    # and config file
    if not instance_name:
        raise ValueError("Instance name should be a valid string and should "
                         "be unique within a network of volttron instances "
                         "that communicate with each other. start volttron "
                         "process with '--instance-name <your instance>' if "
                         "you are running this instance for the first time. "
                         "Or add instance-name = <instance name> in "
                         "vhome/config")

    v_home = cc.get_volttron_home()
    config_path = os.path.join(v_home, "config")
    if os.path.exists(config_path):
        config = ConfigParser()
        config.read(config_path)
        config.set("volttron", "messagebus", message_bus)
        config.set("volttron", "instance-name", instance_name)
        config.set("volttron", "address", vip_address)
        config.set("volttron", "agent-monitor-frequency", str(agent_monitor_frequency))
        config.set("volttron", "agent-isolation-mode", str(agent_isolation_mode))
        with open(config_path, "w") as configfile:
            config.write(configfile)
    else:
        if not os.path.exists(v_home):
            os.makedirs(v_home, 0o755)
        config = ConfigParser()
        config.add_section("volttron")
        config.set("volttron", "messagebus", message_bus)
        config.set("volttron", "instance-name", instance_name)
        config.set("volttron", "address", vip_address)
        config.set("volttron", "agent-monitor-frequency", str(agent_monitor_frequency))
        config.set("volttron", "agent-isolation-mode", str(agent_isolation_mode))

        with open(config_path, "w") as configfile:
            config.write(configfile)
        # all agents need read access to config file
        os.chmod(config_path, 0o744)


def build_address(dest_wrapper, agent):
    """
    Create a usable address with zap parameters embedded in the uri.

    :param dest_wrapper:PlatformWrapper:
        The destination wrapper instance that the agent will be attempting to
        connect to.
    :param agent:Agent
        The agent that is being used to make the connection to dest_wrapper
    :return:
    """
    return "{}:?serverkey={}&publickey={}&secretkey={}".format(dest_wrapper.address,
                                                               dest_wrapper.publickey,
                                                               agent.core.publickey,
                                                               agent.core.secretkey)


def start_wrapper_platform(wrapper,
                           with_http=False,
                           with_tcp=True,
                           volttron_central_address=None,
                           volttron_central_serverkey=None,
                           add_local_vc_address=False):
    """ Customize easily customize the platform wrapper before starting it.
    """
    # Please note, if 'with_http'==True, then instance name needs to be provided
    assert not wrapper.is_running()
    address = get_rand_vip()
    wrapper.startup_platform(address=address)
    assert wrapper.is_running()


def create_volttron_home() -> str:
    """
    Creates a VOLTTRON_HOME temp directory for use within a volttrontesting context.
    This function will return a string containing the VOLTTRON_HOME but will not
    set the global variable.

    :return: str: the temp directory
    """
    volttron_home = tempfile.mkdtemp()
    # This is needed to run tests with volttron's secure mode. Without this
    # default permissions for folders under /tmp directory doesn't not have read or execute for group or others
    os.chmod(volttron_home, 0o755)
    # Move volttron_home to be one level below the mkdir so that
    # the volttron.log file is not part of the same folder for
    # observer.
    volttron_home = os.path.join(volttron_home, "volttron_home")
    os.makedirs(volttron_home)
    return volttron_home


@contextmanager
def with_os_environ(update_env: dict):
    """
    Wrapper function for updating os environment and returning it to the previous state.  This function
    should be used whenever a modification to os.environ is necessary.  The restoration of the environment
    after the call will happen automatically

    Exaample::

        with with_os_environ(self.env):
            print('within self.env context now')

    :param update_env:
    :return:
    """
    copy_env = os.environ.copy()
    os.environ.update(update_env)
    vhome = (Path(os.environ.get("VOLTTRON_HOME", "~/.volttron")).expanduser().resolve())
    copy_cc_vhome = cc.__volttron_home__
    cc.__volttron_home__ = vhome

    try:
        yield
    finally:
        # Restore in place: assigning a plain dict to os.environ would leave every later os.environ write without
        # putenv (subprocesses would not see them) and without the str-only check that callers rely on.
        for key in set(os.environ) - set(copy_env):
            del os.environ[key]
        os.environ.update(copy_env)
        cc.__volttron_home__ = copy_cc_vhome


class PlatformWrapper:
    def __init__(self, messagebus=None, ssl_auth=False, instance_name=None,
                 agent_isolation_mode=False, remote_platform_ca=None):
        """ Initializes a new VOLTTRON instance

        Creates a temporary VOLTTRON_HOME directory with a packaged directory
        for agents that are built.

        :param messagebus: a bus with a registered adapter (``volttrontesting.adapters.available('bus')``; ``zmq`` by
            default), or ``mock`` for the in-process TestServer. An unavailable bus raises ``AdapterError``.
        :param ssl_auth: enable the platform's authentication and authorization (``auth-enabled``), with the
            environment's auth module (``volttrontesting.adapters.selected_auth()``)
        """

        # This is hopefully going to keep us from attempting to shutdown
        # multiple times.  For example if a fixture calls shutdown and a
        # lower level fixture calls shutdown, this won't hang.
        self._instance_shutdown = False

        self.messagebus = messagebus if messagebus else 'zmq'
        
        # Initialize mock infrastructure for mock messagebus
        self.mock_config_store = {} if self.messagebus == 'mock' else None
        self.test_server = None  # Will be initialized if mock mode
        # Every agent build_agent() returned. shutdown_platform() stops them: an agent left running after its platform is
        # gone keeps reconnecting, and with such agents alive the next instance's agents never connect (seen with the
        # pubsub and vctl modules, which build agents they do not stop).
        self._built_agents = []
        # The bus-specific steps of building agents in this process (credentials, agent core); None in mock mode.
        # Found by name through the volttrontesting.message_buses entry points, see volttrontesting.buses.
        self.bus = None if self.messagebus == 'mock' else adapter_class("bus", self.messagebus)()
        # The authorization steps; the 'none' adapter when auth is disabled. The platform uses whichever auth module
        # is installed, so with auth enabled this is the environment's one auth module (adapters.selected_auth).
        if self.messagebus == 'mock':
            self.auth = None
        else:
            self.auth = adapter_class("auth", selected_auth() if ssl_auth else NO_AUTH)()
            if not auth_supports(self.auth.name, self.messagebus):
                raise AdapterError(f"auth module {self.auth.name!r} does not support message bus {self.messagebus!r}")

        self.volttron_home = create_volttron_home()

        # Each PlatformWrapper manages its own volttron_home.  The global
        # service_repo is a module-level singleton whose Singleton entries get
        # their _resolved cache populated on the first build_agent() call (with
        # the first wrapper's ServerOptions).  Without a reset, the second
        # wrapper's build_agent() would receive stale service instances that
        # still hold references to the first wrapper's volttron_home/paths.
        # reset_instances() removes those cached live objects and any direct
        # add_instance() values (ServerOptions etc.) while preserving all
        # class-factory registrations (@service decorators), so the next
        # resolve() call builds fresh instances against the new ServerOptions.
        from volttron.server.containers import service_repo
        service_repo.reset_instances()
        # Reset the cached CoreBuilder singleton so a fresh ZmqCoreBuilder is
        # created for this wrapper.  ZmqCoreBuilder now holds agent_contexts and
        # connection_builders as instance-level dicts, so a new instance gives
        # this wrapper a completely isolated context — no stale server addresses
        # or connection builders from a previous wrapper leak through.
        from volttron.client.decorators import reset_core_builder
        reset_core_builder()
        # this is the user home directory that will be used for this instance
        self.user_home = Path(self.volttron_home).parent.resolve().as_posix()
        # log file is one level above volttron_home now
        self.log_path = os.path.join(os.path.dirname(self.volttron_home), "volttron.log")

        self.packaged_dir = os.path.join(self.volttron_home, "packaged")
        os.makedirs(self.packaged_dir)

        bin_dir = str(Path(sys.executable).parent)
        path = os.environ['PATH']
        if bin_dir not in path:
            path = bin_dir + ":" + path
        if VOLTTRON_ROOT not in path:
            path = VOLTTRON_ROOT + ":" + path
        # in the context of this platform it is very important not to
        # use the main os.environ for anything.
        self.env = {
            'HOME': self.user_home,
            'VOLTTRON_HOME': self.volttron_home,
            # poetry, run by the platform to install agents, uses this environment (see PlatformCLI.prepare)
            'VIRTUAL_ENV': str(Path(sys.executable).parent.parent),
            'PYTHON_KEYRING_BACKEND': 'keyring.backends.null.Keyring',
            'MESSAGEBUS': messagebus if messagebus else 'zmq',
            'AGENT_ISOLATION_MODE': "true" if agent_isolation_mode else "false",
            'PACKAGED_DIR': self.packaged_dir,
            'DEBUG_MODE': os.environ.get('DEBUG_MODE', ''),
            'DEBUG': os.environ.get('DEBUG', ''),
            'SKIP_CLEANUP': os.environ.get('SKIP_CLEANUP', ''),
            'PATH': path,
        # Elixir (rmq pre-req) requires locale to be utf-8
            'LANG': "en_US.UTF-8",
            'LC_ALL': "en_US.UTF-8",
            'PYTHONDONTWRITEBYTECODE': '1',
            'VOLTTRON_ROOT': VOLTTRON_ROOT,
            # Proxy settings — needed for poetry/pip to reach PyPI through corporate proxies.
            'HTTP_PROXY': os.environ.get('HTTP_PROXY', ''),
            'http_proxy': os.environ.get('http_proxy', ''),
            'HTTPS_PROXY': os.environ.get('HTTPS_PROXY', ''),
            'https_proxy': os.environ.get('https_proxy', ''),
            'NO_PROXY': os.environ.get('NO_PROXY', ''),
            'no_proxy': os.environ.get('no_proxy', ''),
            # SSL certificate bundles — required for HTTPS (PyPI) on systems with
            # corporate or custom CA certs (e.g. PNNL).  poetry and pip both honour
            # these; omitting them causes SSL verification failures when the server
            # subprocess runs "poetry add <agent>" during agent installation.
            'SSL_CERT_FILE': os.environ.get('SSL_CERT_FILE', ''),
            'CURL_CA_BUNDLE': os.environ.get('CURL_CA_BUNDLE', ''),
            'REQUESTS_CA_BUNDLE': os.environ.get('REQUESTS_CA_BUNDLE', ''),
            'PIP_CERT': os.environ.get('PIP_CERT', ''),
            # Custom PyPI index (e.g. Artifactory mirror) — pass through so
            # poetry/pip resolve packages from the same source as the host shell.
            'PIP_INDEX_URL': os.environ.get('PIP_INDEX_URL', ''),
            'PIP_EXTRA_INDEX_URL': os.environ.get('PIP_EXTRA_INDEX_URL', ''),
            # HOME is a temp dir, so point poetry at the real user cache/config or every startup re-resolves from PyPI.
            'POETRY_CACHE_DIR': os.environ.get('POETRY_CACHE_DIR',
                                               os.path.expanduser('~/.cache/pypoetry')),
            'POETRY_CONFIG_DIR': os.environ.get('POETRY_CONFIG_DIR',
                                                os.path.expanduser('~/.config/pypoetry')),
        }
        self.volttron_root = VOLTTRON_ROOT
        self.vctl_exe = 'volttron-ctl'
        self.volttron_exe = 'volttron'
        self.python = sys.executable

        self.serverkey = None

        # The main volttron process will be under this variable
        # after startup_platform happens.
        self.p_process = None

        self.started_agent_pids = []
        self.local_address = None
        self.address = None
        self.logit('Creating platform wrapper')

        # Added restricted code properties
        self.certsobj = None

        # Control whether the instance directory is cleaned up when shutdown.
        # if the environment variable DEBUG is set to a True value then the
        # instance is not cleaned up.
        self.skip_cleanup = False
        self.opts = {}

        self.services = {}

        # keystorefile = os.path.join(self.volttron_home, 'keystore')
        # self.keystore = KeyStore(keystorefile)
        # self.keystore.generate()
        self.messagebus = messagebus if messagebus else 'zmq'
        self.agent_isolation_mode = agent_isolation_mode
        self.ssl_auth = ssl_auth
        self.instance_name = instance_name
        if not self.instance_name:
            self.instance_name = os.path.basename(os.path.dirname(self.volttron_home))
        # Starts, stops and talks to the platform (config, poetry setup, process, readiness, vctl, logs); this
        # wrapper adds agents built in the test process and several instances per session on top.
        self.cli = PlatformCLI(self.volttron_home, self.instance_name, auth_enabled=ssl_auth,
                               agent_isolation_mode=agent_isolation_mode, env=self.env,
                               message_bus=self.messagebus)

        with with_os_environ(self.env):
            from volttron.utils import ClientContext
            # TODO - think below is not needed
            #store_message_bus_config(self.messagebus, self.instance_name)
            ClientContext.__load_config__()
            # Writes the main volttron config file for this instance.

            self.remote_platform_ca = remote_platform_ca
            self.requests_ca_bundle = None
            self.dynamic_agent: Optional[Agent] = None

            # if self.messagebus == 'rmq':
            #     self.rabbitmq_config_obj = create_rmq_volttron_setup(vhome=self.volttron_home,
            #                                                          ssl_auth=self.ssl_auth,
            #                                                          env=self.env,
            #                                                          instance_name=self.instance_name,
            #                                                          secure_agent_users=secure_agent_users)

            # Path(self.volttron_home).joinpath('certificates').mkdir(exist_ok=True)
            # self.certsobj = Certs()#Path(self.volttron_home).joinpath("certificates"))

            self.debug_mode = self.env.get('DEBUG_MODE', True)
            if not self.debug_mode:
                self.debug_mode = self.env.get('DEBUG', True)
            self.skip_cleanup = self.env.get('SKIP_CLEANUP', False)
            self.server_config = ServerConfig()


    @property
    def p_process(self):
        """The platform process (the CLI's), read by the fixtures and downstream tests."""
        cli = self.__dict__.get('cli')
        return cli.process if cli is not None else None

    @p_process.setter
    def p_process(self, value):
        cli = self.__dict__.get('cli')
        if cli is not None:
            cli.process = value

    def logit(self, message):
        print('{}: {}'.format(self.volttron_home, message))

    def _write_agent_credentials(self, identity: str, admin: bool = False):
        """Write credentials (the bus adapter) and authz (the auth adapter) for *identity* directly to disk.

        This is the bootstrap path used when no dynamic_agent is connected (pre-registering before the server starts,
        or building the first agent), and the only path when auth is disabled. Existing credentials are reused.
        Returns the credentials object.
        """
        vhome = Path(self.volttron_home)
        cred = self.bus.write_credentials(vhome, identity)
        self.auth.write_authz(vhome, identity, admin=admin)
        self.logit(f"Credentials and authz ready for {identity}")
        return cred

    # def get_agent_identity(self, agent_uuid):
    #     identity = None
    #     path = os.path.join(self.volttron_home, 'agents/{}/IDENTITY'.format(agent_uuid))
    #     with open(path) as f:
    #         identity = f.read().strip()
    #     return identity

    def get_agent_by_identity(self, identity):
        for agent in self.list_agents():
            if agent.get('identity') == identity:
                return agent

    # def build_connection(self, peer=None, address=None, identity=None,
    #                      publickey=None, secretkey=None, serverkey=None,
    #                      capabilities: Optional[dict] = None, **kwargs):
    #     self.logit('Building connection to {}'.format(peer))
    #     with with_os_environ(self.env):
    #         self.allow_all_connections()

    #         if identity is None:
    #             # Set identity here instead of AuthEntry creating one and use that identity to create Connection class.
    #             # This is to ensure that RMQ test cases get the correct current user that matches the auth entry made
    #             identity = str(uuid.uuid4())
    #         if address is None:
    #             self.logit(
    #                 'Default address was None so setting to current instances')
    #             address = self.vip_address
    #             serverkey = self.serverkey
    #         if serverkey is None:
    #             self.logit("serverkey wasn't set but the address was.")
    #             raise Exception("Invalid state.")

    #         if publickey is None or secretkey is None:
    #             self.logit('generating new public secret key pair')
    #             keyfile = tempfile.mktemp(".keys", "agent", self.volttron_home)
    #             keys = KeyStore(keyfile)
    #             keys.generate()
    #             publickey = keys.public
    #             secretkey = keys.secret

    #             entry = AuthEntry(capabilities=capabilities,
    #                               comments="Added by test",
    #                               credentials=keys.public,
    #                               user_id=identity,
    #                               identity=identity)
    #             file = AuthFile(self.volttron_home + "/auth.json")
    #             file.add(entry)

    #         conn = Connection(address=address, peer=peer, publickey=publickey,
    #                           secretkey=secretkey, serverkey=serverkey,
    #                           instance_name=self.instance_name,
    #                           message_bus=self.messagebus,
    #                           volttron_home=self.volttron_home,
    #                           identity=identity)

    #         return conn

    def build_agent(self, address=None, should_spawn=True, identity=None,
                    publickey=None, secretkey=None, serverkey=None,
                    agent_class=Agent, capabilities: Optional[dict] = None, **kwargs) -> Agent:
        """ Build an agent connnected to the passed bus.

        By default the current instance that this class wraps will be the
        vip address of the agent.

        :param address:
        :param should_spawn:
        :param identity:
        :param publickey:
        :param secretkey:
        :param serverkey:
        :param agent_class: Agent class to build
        :return:
        """
        self.logit("Building generic agent.")
        # Update OS env to current platform's env so get_home() call will result
        # in correct home director. Without this when more than one test instance are created, get_home()
        # will return home dir of last started platform wrapper instance
        with with_os_environ(self.env):
            use_ipc = kwargs.pop('use_ipc', False)

            # Make sure we have an identity or things will mess up
            identity = identity if identity else str(uuid.uuid4())

            if serverkey is None:
                serverkey = self.serverkey
            if self.messagebus == 'mock':
                return self._build_mock_agent(identity, address or self.address, agent_class, should_spawn, **kwargs)
            if publickey is None:
                if self.dynamic_agent is not None and self.auth.enabled:
                    # A connected dynamic_agent already exists — delegate to the
                    # server's live AuthService via RPC.  This is the preferred
                    # path because it updates the server's in-memory credential
                    # and authz maps immediately (no file-watch latency, no
                    # risk of the ZAP authenticator's publickey map being stale).
                    self.dynamic_agent.vip.rpc.call(
                        AUTH, 'create_credentials', identity=identity
                    ).get(timeout=10)
                    # get_credentials is not @RPC.export, so read the file that
                    # the server just wrote to get the Credentials object.
                    agent_cred = self.bus.read_credentials(Path(self.volttron_home), identity)
                else:
                    # Bootstrap path: no connected agent yet (we are creating
                    # dynamic_agent itself).  Write credentials and authz
                    # directly to disk so the server's credential store
                    # can pick them up.  We deliberately avoid resolving
                    # AuthService through service_repo here because that would
                    # pull in ZapAuthenticator as a constructor dependency,
                    # binding inproc://zeromq.zap.01 in the test process and
                    # leaking the socket/greenlet across test runs.
                    agent_cred = self._write_agent_credentials(identity)

            if address is None:
                address = self.address

            self.logit(f"Using vip-address {address}")
            self.logit(f"Got agent credentials for identity {identity} as {agent_cred}")
            # Any other AgentOptions field (tag_refresh_interval, tag_vip_id, reconnect_interval, ...) passes through;
            # dropping them silently made agents ignore the options a test asked for.
            option_names = {f.name for f in dataclasses.fields(AgentOptions)} - {'volttron_home'}
            agent_opts = AgentOptions(heartbeat_autostart=kwargs.pop('heartbeat_autostart', False),
                                      heartbeat_period=kwargs.pop('heartbeat_period', 60),
                                      volttron_home=self.volttron_home,
                                      enable_store=kwargs.pop('enable_store', False),
                                      **{k: kwargs.pop(k) for k in list(kwargs) if k in option_names})
            with self.bus.core_builder():
                agent = agent_class(credentials=agent_cred,
                                    options=agent_opts, address=address)
            self.logit('platformwrapper.build_agent.address: {}'.format(address))
            
            if should_spawn:
                self.logit(f'platformwrapper.build_agent spawning for identity {identity}')
                event = gevent.event.Event()
                gevent.spawn(agent.core.run, event)
                connected = event.wait(timeout=15)
                self.logit(f'build_agent event.wait returned connected={connected} for {identity}')
                router_ping = agent.vip.ping("").get(timeout=30)
                self.logit(f'build_agent router_ping done for {identity}: {len(router_ping)} responses')
                assert len(router_ping) > 0

            self._built_agents.append(agent)
            return agent

    def _vctl(self, cmd, json_output=False):
        """Run a vctl command list (``[vctl_exe | 'vctl' | 'volttron-ctl', *args]``, as the methods below build them)
        through this platform's PlatformCLI. With ``json_output`` the parsed JSON document is returned; a leading
        ``--json`` in ``cmd`` is implied. Raises PlatformError, a RuntimeError, on a non-zero exit."""
        assert cmd and Path(str(cmd[0])).name in ('vctl', 'volttron-ctl'), cmd
        args = [str(a) for a in cmd[1:]]
        if json_output:
            return self.cli.vctl_json(*(args[1:] if args and args[0] == '--json' else args))
        return self.cli.vctl(*args)

    def _stop_agent_core(self, agent, timeout=5):
        """Stop an in-process agent's core without touching the bus, and kill whatever is left after ``timeout``.

        ``Core.stop()`` first sends ``agentstop`` to the router; on a DEALER socket whose router is already gone that
        send never returns (and ``ZmqCore.connected`` is never cleared, its setter is a no-op), so a core stopped the
        plain way after its platform went down becomes a zombie whose monitor greenlet sits in that send for good.
        ``platform_shutdown=True`` skips the send. ``MockCore.stop()`` takes no arguments."""
        core = agent.core
        name = getattr(core, 'identity', '?')
        try:
            core.stop(timeout=timeout, platform_shutdown=True)
        except TypeError:
            core.stop()
        except Exception as e:
            self.logit(f"stopping {name} raised {e!r}")
        glt = getattr(core, 'greenlet', None)
        if glt is not None and not glt.ready():
            self.logit(f"core greenlet of {name} did not finish; killing it")
            glt.kill(block=True, timeout=timeout)
        for g in list(getattr(core, 'spawned_greenlets', ())):
            if not g.ready():
                g.kill(block=False)

    def _stop_built_agents(self):
        """Stop every agent this wrapper built (not ``dynamic_agent``, which shutdown handles itself)."""
        for agent in self._built_agents:
            if agent is self.dynamic_agent:    # appended before startup_platform assigned it; shutdown stops it itself
                continue
            self._stop_agent_core(agent)
        self._built_agents = []

    def _build_mock_agent(self, identity, address, agent_class, should_spawn, **kwargs):
        """``messagebus='mock'``: no platform process and no credentials; the agent is a ``MockAgent`` (unless a class was
        given) wired to this wrapper's ``TestServer``."""
        if agent_class is None or agent_class is Agent:
            from volttrontesting.mock_agent import MockAgent
            agent_class = MockAgent
        agent = agent_class(identity=identity, address=address, message_bus='mock', volttron_home=self.volttron_home,
                            **kwargs)
        if self.test_server:
            self.test_server.connect_agent(agent)
            if hasattr(agent, 'set_test_server'):
                agent.set_test_server(self.test_server)
            else:
                from volttrontesting.pubsub_interceptor import intercept_agent_pubsub
                intercept_agent_pubsub(agent, self.test_server.__server_pubsub__)
            self.logit(f"Connected agent {identity} to TestServer")
        if should_spawn:
            event = gevent.event.Event()
            gevent.spawn(agent.core.run, event)
            event.wait(timeout=2)
        self._built_agents.append(agent)
        return agent

    def add_capabilities(self, publickey, capabilities):
        """Capabilities are authz roles on the modular platform; ``auth.json`` and ``AuthFile`` no longer exist.
        Grant RPC capabilities with ``self.cli.grant(identity, *capabilities)``, which goes through the auth
        adapter."""
        raise NotImplementedError(
            "PlatformWrapper.add_capabilities: use wrapper.cli.grant(identity, *rpc_capabilities) on the modular platform")

    @staticmethod
    def add_capability(entry, capabilites):
        if isinstance(entry, str):
            if entry not in capabilites:
                capabilites[entry] = None
        elif isinstance(entry, dict):
            capabilites.update(entry)
        else:
            raise ValueError(
                "Invalid capability {}. Capability should be string or dictionary or list of string"
                "and dictionary.")

    def startup_platform(self, address,
                         mode=UNRESTRICTED,
                         msgdebug=False,
                         enable_logging=False,
                         agent_monitor_frequency=600,
                         timeout=100,
                         agent_isolation_mode = False,
                         ):

        # Update OS env to current platform's env so get_home() call will result
        # in correct home director. Without this when more than one test instance are created, get_home()
        # will return home dir of last started platform wrapper instance.
        with with_os_environ(self.env):
            # For mock messagebus, we don't need a real platform running
            if self.messagebus == 'mock':
                self.address = address
                self.logit("Mock platform startup - simulating platform without actual process")
                
                # Create TestServer for handling pubsub in mock mode
                from volttrontesting.server_mock import TestServer
                self.test_server = TestServer()
                self.logit("Created TestServer for mock pubsub handling")
                
                # Create platform credentials for mock mode
                cred_dir = Path(self.volttron_home) / "credentials_store"
                cred_dir.mkdir(exist_ok=True)
                platform_cred = cred_dir / "platform.json"
                
                # Generate dummy keys for platform
                platform_public = str(uuid.uuid4())
                platform_secret = str(uuid.uuid4())
                
                platform_data = {
                    "identity": "platform",
                    "domain": None,
                    "address": self.address,
                    "serverkey": platform_public,
                    "publickey": platform_public,
                    "secretkey": platform_secret
                }
                
                with open(platform_cred, 'w') as f:
                    jsonapi.dump(platform_data, f)
                
                # Set serverkey for mock mode
                self.serverkey = platform_public
                
                # Update config file with address for mock mode
                config_path = os.path.join(self.volttron_home, "config")
                if os.path.exists(config_path):
                    config = ConfigParser()
                    config.read(config_path)
                    if not config.has_option("volttron", "address"):
                        config.set("volttron", "address", address)
                        with open(config_path, "w") as configfile:
                            config.write(configfile)
                
                # Set up minimal required attributes for mock mode
                self.p_process = None
                self.t_process = None
                self.started = True
                return
                
            # Add check and raise error if the platform is already running for this instance.
            if self.is_running():
                raise PlatformWrapperError("Already running platform")

            self.address = address

            # Update config file with the address now that we know it
            config_path = os.path.join(self.volttron_home, "config")
            if os.path.exists(config_path):
                config = ConfigParser()
                config.read(config_path)
                if not config.has_option("volttron", "address"):
                    config.set("volttron", "address", address)
                    with open(config_path, "w") as configfile:
                        config.write(configfile)
            self.mode = mode

            msgdebug = self.env.get('MSG_DEBUG', False)
            enable_logging = self.env.get('ENABLE_LOGGING', False)

            if self.debug_mode:
                self.skip_cleanup = True
                enable_logging = True
                msgdebug = True

            self.logit("Starting Platform: {}".format(self.volttron_home))
            assert self.mode in MODES, 'Invalid platform mode set: ' + str(mode)
            opts = None

            # see main.py for how we handle pub sub addresses.
            ipc = 'ipc://{}{}/run/'.format(
                '@' if sys.platform.startswith('linux') else '',
                self.volttron_home)
            self.local_vip_address = ipc + 'vip.socket'
            # if self.remote_platform_ca:
            #     ca_bundle_file = os.path.join(self.volttron_home, "cat_ca_certs")
            #     with open(ca_bundle_file, 'w') as cf:
            #         if self.ssl_auth:
            #             with open(self.certsobj.cert_file(self.certsobj.root_ca_name)) as f:
            #                 cf.write(f.read())
            #         with open(self.remote_platform_ca) as f:
            #             cf.write(f.read())
            #     os.chmod(ca_bundle_file, 0o744)
            #     self.env['REQUESTS_CA_BUNDLE'] = ca_bundle_file
            #     os.environ['REQUESTS_CA_BUNDLE'] = self.env['REQUESTS_CA_BUNDLE']
            # # This file will be passed off to the main.py and available when
            # # the platform starts up.
            # self.requests_ca_bundle = self.env.get('REQUESTS_CA_BUNDLE')

            self.opts.update({
                'volttron_home': self.volttron_home,
                'instance_name': self.instance_name,
                'address': self.address,
                'local_address': ipc + 'vip.socket',
                'agent_isolation_mode': "true" if self.agent_isolation_mode else "false",
                'messagebus': self.messagebus,
                'auth_enabled': self.ssl_auth,
                'log_level': logging.DEBUG,
                'verboseness': logging.DEBUG,
            })


            # Add platform's public key to known hosts file
            # publickey = self.keystore.public
            # known_hosts_file = os.path.join(self.volttron_home, 'known_hosts')
            # known_hosts = KnownHostsStore(known_hosts_file)
            # known_hosts.add(self.opts['vip_local_address'], publickey)
            # known_hosts.add(self.opts['vip_address'], publickey)

            # The platform itself is started by this wrapper's PlatformCLI: config file, poetry setup and pinned
            # project, process, output file and readiness are the CLI's, so there is one way to start a platform.
            self.cli.address = self.address
            self.cli.auth_enabled = self.ssl_auth
            self.cli.agent_isolation_mode = self.agent_isolation_mode
            self.cli.agent_monitor_frequency = agent_monitor_frequency
            self.cli.prepare()

            if self.ssl_auth:
                certsdir = os.path.join(self.volttron_home, 'certificates')
                self.certsobj = Certs(certsdir)

            if self.services:
                with Path(self.volttron_home).joinpath("service_config.yml").open('wt') as fp:
                    yaml.dump(self.services, fp)

            # Pre-register dynamic_agent credentials and authz BEFORE the server
            # starts so the server reads them from disk on startup.  This ensures
            # the server's in-memory authz map already contains "dynamic_agent"
            # when the agent connects, avoiding a ValueError in the
            # ConfigStore._onconfig background greenlet.
            self._write_agent_credentials("dynamic_agent", admin=True)

            self.cli.launch(timeout)
            self.serverkey = self.bus.server_credentials(Path(self.volttron_home))
            assert self.serverkey
            self.logit(f"Server platform publickey: {self.serverkey.publickey}")

            # Use dynamic_agent so we can look and see the agent with peerlist.
            gevent.sleep(2)
            self.dynamic_agent = self.build_agent(identity="dynamic_agent")
            assert self.dynamic_agent is not None
            assert isinstance(self.dynamic_agent, Agent)
            has_control = False
            times = 0
            while not has_control and times < 10:
                times += 1
                try:
                    has_control = CONTROL in self.dynamic_agent.vip.peerlist().get(timeout=2)
                    self.logit("Has control? {}".format(has_control))
                except gevent.Timeout:
                    pass

            if not has_control:
                self.shutdown_platform()
                raise Exception("Couldn't connect to core platform!")

            # def subscribe_to_all(peer, sender, bus, topic, headers, messages):
            #     logged = "{} --------------------Pubsub Message--------------------\n".format(
            #         utils.format_timestamp(datetime.now()))
            #     logged += "PEER: {}\n".format(peer)
            #     logged += "SENDER: {}\n".format(sender)
            #     logged += "Topic: {}\n".format(topic)
            #     logged += "headers: {}\n".format([str(k) + '=' + str(v) for k, v in headers.items()])
            #     logged += "message: {}\n".format(messages)
            #     logged += "-------------------------------------------------------\n"
            #     self.logit(logged)
            #
            # self.dynamic_agent.vip.pubsub.subscribe('pubsub', '', subscribe_to_all).get()

        if self.is_running():
            self._instance_shutdown = False

    def is_running(self):
        # For mock messagebus, check if we've "started" it
        if self.messagebus == 'mock':
            return getattr(self, 'started', False)
            
        with with_os_environ(self.env):
            return is_volttron_running(self.volttron_home)

    def install_multiple_agents(self, agent_configs):
        """
        Installs mutltiple agents on the platform.

        :param agent_configs:list
            A list of 3-tuple that allows the configuration of a platform
            in a single go.  The tuple order is
            1. path to the agent directory.
            2. configuration data (either file or json data)
            3. Whether the agent should be started or not.

        :return:list:
            A list of uuid's associated with the agents that were installed.


        :Note:
            In order for this method to be called the platform must be
            currently running.
        """
        results = []
        with with_os_environ(self.env):
            if not self.is_running():
                raise PlatformWrapperError("Instance isn't running!")

            for path, config, start in agent_configs:
                results.append(self.install_agent(agent_dir=path, config_file=config,
                                             start=start))

        return results

    def install_agent(self,
                      agent_wheel: Optional[str] = None,
                      agent_dir: Optional[str] = None,
                      config_file: Optional[Union[dict, str]] = None,
                      start: bool = True,
                      vip_identity: Optional[str] = None,
                      startup_time: int = 5,
                      force: bool = False,
                      install_options: Optional[InstallAgentOptions] = None,
                      tag: Optional[str] = None,
                      priority: Optional[int] = None):
        """
        Install an agent on the instance with ``vctl install``, and by default start it. Returns the agent's uuid.

        :param agent_dir: what to install, in any form ``vctl install`` accepts: a directory with a
            ``pyproject.toml`` (installed editable), a wheel file, or a PyPI requirement, a name with an optional
            version constraint such as ``volttron-listener>=2.0.0rc0``.
        :param agent_wheel: the same as ``agent_dir``, under the name Monolithic's wrapper uses for a wheel. Give
            one of the two.
        :param config_file: the agent's configuration, as a file path or a dict (written to a temporary file).
        :param start: start the agent after installing it, and check that it is running.
        :param vip_identity: the agent's identity (``--vip-identity``).
        :param startup_time: kept for compatibility; unused.
        :param force: replace an agent installed with the same identity (``--force``).
        :param install_options: an InstallAgentOptions; each field it sets overrides the keyword of that name.
        :param tag: the agent's tag (``--tag``).
        :param priority: the agent's startup priority (``--priority``), which also enables it.
        """
        if install_options is not None:
            keywords = {'start': start, 'vip_identity': vip_identity, 'agent_config': config_file,
                        'startup_time': startup_time, 'force': force, 'tag': tag, 'priority': priority}
            for field in dataclasses.fields(install_options):
                value = getattr(install_options, field.name)
                if value != field.default:
                    keywords[field.name] = value
            start, vip_identity, config_file = keywords['start'], keywords['vip_identity'], keywords['agent_config']
            force, tag, priority = keywords['force'], keywords['tag'], keywords['priority']

        if (agent_wheel is None) == (agent_dir is None):
            raise ValueError('Give exactly one of agent_dir and agent_wheel: what vctl install installs.')
        source = str(agent_wheel if agent_wheel is not None else agent_dir)

        with with_os_environ(self.env):
            _log.debug(f"install_agent called with source {source}")
            self.__wait_for_control_connection_to_exit__()
            assert self.is_running(), "Instance must be running to install agent."

            temp_config = None
            if isinstance(config_file, dict):
                temp_config = os.path.join(self.volttron_home, f"agent_config_{uuid.uuid4().hex}.json")
                with open(temp_config, "w") as fp:
                    fp.write(jsonapi.dumps(config_file))
                config_file = temp_config
            elif config_file and not os.path.exists(config_file):
                raise ValueError(f"Config file {config_file} doesn't exist.")

            cmd = [self.vctl_exe, "--json", "install", source]
            if config_file:
                cmd.extend(["--agent-config", str(config_file)])
            if force:
                cmd.append("--force")
            if vip_identity:
                cmd.extend(["--vip-identity", vip_identity])
            if tag:
                cmd.extend(["--tag", tag])
            if priority is not None:
                cmd.extend(["--priority", str(priority)])
            # The agent is started separately below rather than with --start, as before.
            self.logit(f"Command installation is: {cmd}")
            try:
                resultobj = self._vctl(cmd, json_output=True)
            finally:
                if temp_config and os.path.isfile(temp_config):
                    os.remove(temp_config)
            self.logit(f"RESPONSE FROM INSTALL IS: {resultobj}")
            agent_uuid = resultobj['agent_uuid']
            assert agent_uuid
            time.sleep(5)
            if start:
                self.logit(f"We are running {agent_uuid}")
                self.start_agent(agent_uuid)
                assert self.is_agent_running(agent_uuid)
            return agent_uuid

    def __wait_for_control_connection_to_exit__(self, timeout: int = 10):
        """
        Call the dynamic agent's peerlist method until the control connection is no longer connected or
        timeout is reached
        :param timeout:
        :return:
        """
        with with_os_environ(self.env):
            self.logit("Waiting for control_connection to exit")
            disconnected = False
            timer_start = time.time()
            while not disconnected:
                try:
                    peers = self.dynamic_agent.vip.peerlist().get(timeout=10)
                except gevent.Timeout:
                    self.logit("peerlist call timed out. Exiting loop. "
                               "Not waiting for control connection to exit.")
                    break
                disconnected = CONTROL_CONNECTION not in peers
                if disconnected:
                    break
                if time.time() - timer_start > timeout:
                    # raise PlatformWrapperError(f"Failed for {CONTROL_CONNECTION} to exit in a timely manner.")
                    # See https://githb.com/VOLTTRON/volttron/issues/2938
                    self.logit("Control connection did not exit")
                    break
                time.sleep(0.5)
            # See https://githb.com/VOLTTRON/volttron/issues/2938
            # if not disconnected:
            #     raise PlatformWrapperError("Control connection did not stop properly")

    def start_agent(self, agent_uuid):
        with with_os_environ(self.env):
            self.logit('Starting agent {}'.format(agent_uuid))
            self.logit("VOLTTRON_HOME SETTING: {}".format(self.env['VOLTTRON_HOME']))
            if not self.is_running():
                raise PlatformWrapperError("Instance must be running before starting agent")

            self.__wait_for_control_connection_to_exit__()

            cmd = [self.vctl_exe, '--json']
            cmd.extend(['start', agent_uuid])
            self._vctl(cmd)

            self.__wait_for_control_connection_to_exit__()

            # Confirm agent running
            cmd = [self.vctl_exe, '--json']
            cmd.extend(['status', agent_uuid])
            res = self._vctl(cmd)

            result = jsonapi.loads(res)
            # 776 TODO: Timing issue where check fails
            time.sleep(3)
            self.logit("Subprocess res is {}".format(res))
            assert 'running' in res
            pidpos = res.index('[') + 1
            pidend = res.index(']')
            pid = int(res[pidpos:pidend])

            assert psutil.pid_exists(pid), \
                "The pid associated with agent {} does not exist".format(pid)

            self.started_agent_pids.append(pid)

            self.__wait_for_control_connection_to_exit__()

            return pid

    def stop_agent(self, agent_uuid):
        with with_os_environ(self.env):
            # Confirm agent running
            self.__wait_for_control_connection_to_exit__()

            _log.debug("STOPPING AGENT: {}".format(agent_uuid))

            cmd = [self.vctl_exe]
            cmd.extend(['stop', agent_uuid])
            res = self._vctl(cmd)
            return self.agent_pid(agent_uuid)

    def list_agents(self):
        with with_os_environ(self.env):
            agent_list = self.dynamic_agent.vip.rpc(CONTROL, 'list_agents').get(timeout=10)
            return agent_list

    def run_command(self, command: List[str], **kwargs) -> str:
        """
        Run a command in the platform's environment.
        
        For mock messagebus, simulates config store operations without
        actually running vctl commands.
        
        :param command: List of command arguments (e.g., ["vctl", "status"])
        :param kwargs: Additional arguments to pass to execute_command
        :return: Command output as string
        """
        # Handle mock messagebus - simulate config operations
        if self.messagebus == 'mock' and command and command[0] in ['vctl', 'volttron-ctl']:
            return self._handle_mock_vctl_command(command)
            
        with with_os_environ(self.env):
            # Ensure vctl commands use the correct executable
            if command and command[0] == "vctl":
                command[0] = self.vctl_exe
                
            result = execute_command(command, env=self.env, **kwargs)
            return result
    
    def _handle_mock_vctl_command(self, command: List[str]) -> str:
        """
        Handle mock vctl commands for testing without real platform.
        
        :param command: vctl command list
        :return: Simulated command output
        """
        if len(command) < 2:
            return "Mock vctl: command successful"
            
        # Handle config store commands
        if command[1] == "config" and len(command) > 2:
            if command[2] == "store" and len(command) >= 6:
                # vctl config store <agent> <config_name> <file> [--json|--csv]
                agent = command[3]
                config_name = command[4]
                
                # Initialize agent's config store if needed
                if agent not in self.mock_config_store:
                    self.mock_config_store[agent] = {}
                    
                # Store the config (we'll just track that it was stored)
                self.mock_config_store[agent][config_name] = {
                    'stored': True,
                    'type': 'json' if '--json' in command else 'csv' if '--csv' in command else 'raw'
                }
                
                self.logit(f"Mock config store: Stored {config_name} for {agent}")
                return f"Mock: Config {config_name} stored for {agent}"
                
            elif command[2] == "get" and len(command) >= 5:
                # vctl config get <agent> <config_name>
                agent = command[3]
                config_name = command[4]
                
                if agent in self.mock_config_store and config_name in self.mock_config_store[agent]:
                    return f"Mock: Config {config_name} for {agent}: {self.mock_config_store[agent][config_name]}"
                return f"Mock: Config {config_name} not found for {agent}"
                
        # Handle other vctl commands
        elif command[1] == "status":
            return "Mock: Platform running (mock mode)"
            
        elif command[1] == "list":
            return "Mock: No agents installed (mock mode)"
            
        # Default response
        self.logit(f"Mock vctl command: {' '.join(command)}")
        return f"Mock: Command executed successfully"

    def install_library(self, source: Union[str, Path], force: bool = False, pre_release: bool = False) -> str:
        """
        Install a library (a driver interface, say) into the platform's environment with ``vctl install-lib``, so
        the platform records it as it records an agent. Returns the installed library's name as vctl reports it.

        :param source: anything ``vctl install-lib`` accepts: a directory with a ``pyproject.toml`` (built into a
            wheel), a wheel file, or a PyPI requirement, with an optional version constraint.
        :param force: reinstall the library (``--force``).
        :param pre_release: allow pre-releases (``--pre-release``).
        """
        with with_os_environ(self.env):
            self.logit(f"Installing library from {source}")
            cmd = [self.vctl_exe, "install-lib", str(source)]
            if force:
                cmd.append("--force")
            if pre_release:
                cmd.append("--pre-release")
            output = self._vctl(cmd)
            self.logit(f"Library installation complete: {output}")
            installed = re.findall(r"^Installed (\S+)", output, flags=re.MULTILINE)
            return installed[-1] if installed else output.strip()

    def remove_library(self, library: str) -> None:
        """Remove a library installed with ``install_library`` (``vctl remove-lib``), by its distribution name."""
        with with_os_environ(self.env):
            self._vctl([self.vctl_exe, "remove-lib", library])

    def remove_agent(self, agent_uuid):
        """Remove the agent specified by agent_uuid"""
        with with_os_environ(self.env):
            _log.debug("REMOVING AGENT: {}".format(agent_uuid))
            self.__wait_for_control_connection_to_exit__()
            cmd = [self.vctl_exe]
            cmd.extend(['remove', agent_uuid])
            res = self._vctl(cmd)
            pid = None
            try:
                pid = self.agent_pid(agent_uuid)
            except RuntimeError:
                self.logit("Runtime error occurred successfully as it was expected")
            finally:
                if pid is not None:
                    raise RuntimeError(
                        f"Expected runtime error for looking at removed agent. {agent_uuid}")

    def remove_all_agents(self):
        with with_os_environ(self.env):
            if self._instance_shutdown:
                return
            agent_list = self.dynamic_agent.vip.rpc(CONTROL, 'list_agents').get(timeout=10)
            for agent_props in agent_list:
                self.dynamic_agent.vip.rpc(CONTROL, 'remove_agent',
                                           agent_props['uuid']).get(timeout=10)
                time.sleep(0.2)

    def is_agent_running(self, agent_uuid):
        with with_os_environ(self.env):
            return self.agent_pid(agent_uuid) is not None

    def agent_pid(self, agent_uuid):
        """
        Returns the pid of a running agent or None

        :param agent_uuid:
        :return:
        """
        self.__wait_for_control_connection_to_exit__()
        # Confirm agent running
        cmd = [self.vctl_exe]
        cmd.extend(['status', agent_uuid])
        pid = None
        try:
            res = self._vctl(cmd)
            try:
                pidpos = res.index('[') + 1
                pidend = res.index(']')
                pid = int(res[pidpos:pidend])
            except:
                pid = None
        except CalledProcessError as ex:
            _log.error("Exception: {}".format(ex))

        # Handle the following exception that seems to happen when getting a
        # pid of an agent during the platform shutdown phase.
        #
        # Logged from file platformwrapper.py, line 797
        #   AGENT             IDENTITY          TAG STATUS
        # Traceback (most recent call last):
        #   File "/usr/lib/python2.7/logging/__init__.py", line 882, in emit
        #     stream.write(fs % msg)
        #   File "/home/volttron/git/volttron/env/local/lib/python2.7/site-packages/_pytest/capture.py", line 244, in write
        #     self.buffer.write(obj)
        # ValueError: I/O operation on closed file
        except ValueError:
            pass
        return pid

    # def build_agentpackage(self, agent_dir, config_file={}):
    #     if isinstance(config_file, dict):
    #         cfg_path = os.path.join(agent_dir, "config_temp")
    #         with open(cfg_path, "w") as tmp_cfg:
    #             tmp_cfg.write(jsonapi.dumps(config_file))
    #         config_file = cfg_path
    #
    #     # Handle relative paths from the volttron git directory.
    #     if not os.path.isabs(agent_dir):
    #         agent_dir = os.path.join(self.volttron_root, agent_dir)
    #
    #     assert os.path.exists(config_file)
    #     assert os.path.exists(agent_dir)
    #
    #     wheel_path = packaging.create_package(agent_dir,
    #                                           self.packaged_dir)
    #     packaging.add_files_to_package(wheel_path, {
    #         'config_file': os.path.join('volttron/', config_file)
    #     })
    #
    #     return wheel_path

    def confirm_agent_running(self, agent_name, max_retries=5, timeout_seconds=2):
        running = False
        retries = 0
        while not running and retries < max_retries:
            status = self.test_aip.status_agents()
            print("Status", status)
            if len(status) > 0:
                status_name = status[0][1]
                assert status_name == agent_name

                assert len(status[0][2]) == 2, 'Unexpected agent status message'
                status_agent_status = status[0][2][1]
                running = not isinstance(status_agent_status, int)
            retries += 1
            time.sleep(timeout_seconds)
        return running

    # def setup_federation(self, config_path):
    #     """
    #     Set up federation using the given config path
    #     :param config_path: path to federation config yml file.
    #     """
    #     with with_os_environ(self.env):
    #         print(f"VHOME WITH with_os_environ: {os.environ['VOLTTRON_HOME']}")
    #         setup_rabbitmq_volttron('federation',
    #                                 verbose=False,
    #                                 prompt=False,
    #                                 instance_name=self.instance_name,
    #                                 rmq_conf_file=self.rabbitmq_config_obj.rmq_conf_file,
    #                                 max_retries=5,
    #                                 env=self.env)
    #
    #
    # def setup_shovel(self, config_path):
    #     """
    #     Set up shovel using the given config path
    #     :param config_path: path to shovel config yml file.
    #     """
    #     with with_os_environ(self.env):
    #         print(f"VHOME WITH with_os_environ: {os.environ['VOLTTRON_HOME']}")
    #         setup_rabbitmq_volttron('shovel',
    #                                 verbose=False,
    #                                 prompt=False,
    #                                 instance_name=self.instance_name,
    #                                 rmq_conf_file=self.rabbitmq_config_obj.rmq_conf_file,
    #                                 max_retries=5,
    #                                 env=self.env)

    def stop_platform(self):
        """Stop the platform process but keep VOLTTRON_HOME, installed agents and credentials, so that
        ``restart_platform`` can bring the same instance back."""
        with with_os_environ(self.env):
            original_skip_cleanup = self.skip_cleanup
            self.skip_cleanup = True
            try:
                self.shutdown_platform()
            finally:
                self.skip_cleanup = original_skip_cleanup

    def restart_platform(self):
        with with_os_environ(self.env):
            self.stop_platform()
            # since this is a restart, we don't want to do an update/overwrite of services.
            self.startup_platform(address=self.address)
            # we would need to reset shutdown flag so that platform is properly cleaned up on the next shutdown call
            self._instance_shutdown = False
            gevent.sleep(1)

    def __remove_home_directory__(self):
        self.logit('Removing {}'.format(self.volttron_home))
        shutil.rmtree(Path(self.volttron_home).parent, ignore_errors=True)

    def shutdown_platform(self):
        """
        Stop platform here.  First grab a list of all of the agents that are
        running on the platform, then shutdown, then if any of the listed agent
        pids are still running then kill them.
        """

        with with_os_environ(self.env):
            # Handle cascading calls from multiple levels of fixtures.
            if self._instance_shutdown:
                self.logit(f"Instance already shutdown {self._instance_shutdown}")
                return

            # Handle mock messagebus shutdown
            if self.messagebus == 'mock':
                self.logit("Shutting down mock platform")
                self._stop_built_agents()
                self.started = False
                self._instance_shutdown = True
                if not self.skip_cleanup:
                    self.__remove_home_directory__()
                return

            self._stop_built_agents()
            if not self.is_running():
                self.logit(
                    f"Instance running {self.is_running()} and skip cleanup: {self.skip_cleanup}")
                if self.dynamic_agent is not None:    # the platform died under it; do not leave its core behind
                    self._stop_agent_core(self.dynamic_agent)
                    self.dynamic_agent = None
                if not self.skip_cleanup:
                    self.__remove_home_directory__()
                return

            running_pids = []
            if self.dynamic_agent:    # because we are not creating dynamic agent in setupmode
                try:
                    for agnt in self.list_agents():
                        pid = self.agent_pid(agnt['uuid'])
                        if pid is not None and int(pid) > 0:
                            running_pids.append(int(pid))
                    if not self.skip_cleanup:
                        self.remove_all_agents()
                except gevent.Timeout:
                    self.logit("Timeout getting list of agents")
                except RuntimeError as e:
                    if not self.is_running():
                        self.logit("Unable to shutdown agent. instance is already shutdown")
                    self.logit(f"Error shutting down agent {e}")

                # Stop the in-process agent first (without the agentstop send, see _stop_agent_core); the CLI then
                # shuts the platform down.
                self._stop_agent_core(self.dynamic_agent)
                self.dynamic_agent = None

            if self.p_process is not None:
                # vctl shutdown --platform, wait, and kill if it does not exit
                self.cli.shutdown()
                pid_file = os.path.join(self.volttron_home, "VOLTTRON_PID")
                if os.path.exists(pid_file):    # left behind only when the platform had to be killed
                    os.remove(pid_file)
            else:
                self.logit("platform process was null")

            for pid in running_pids:
                if psutil.pid_exists(pid):
                    self.logit("TERMINATING: {}".format(pid))
                    proc = psutil.Process(pid)
                    proc.terminate()

            self.logit(f"VHOME: {self.volttron_home}, Skip clean up flag is {self.skip_cleanup}")
            # if self.messagebus == 'rmq':
            #     self.logit("Calling rabbit shutdown")
            #     stop_rabbit(rmq_home=self.rabbitmq_config_obj.rmq_home, env=self.env, quite=True)
            if not self.skip_cleanup:
                self.__remove_home_directory__()

            self._instance_shutdown = True

    def __repr__(self):
        return str(self)

    def __str__(self):
        data = []
        data.append('volttron_home: {}'.format(self.volttron_home))
        return '\n'.join(data)

    def cleanup(self):
        """
        Cleanup all resources created for test purpose if debug_mode is false.
        :return:
        """

        if not self.debug_mode:
            shutil.rmtree(self.volttron_home, ignore_errors=True)


def mergetree(src, dst, symlinks=False, ignore=None):
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            mergetree(s, d, symlinks, ignore)
        else:
            if not os.path.exists(d) or os.stat(src).st_mtime - os.stat(dst).st_mtime > 1:
                shutil.copy2(s, d)
