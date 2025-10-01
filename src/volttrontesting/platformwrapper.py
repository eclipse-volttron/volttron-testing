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
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from configparser import ConfigParser
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import CalledProcessError
from typing import Optional, Union, List, Dict, Any

import gevent
import gevent.subprocess as subprocess
import psutil

# import grequests  # Removed - not needed for basic testing
import yaml

# from .agent_additions import add_volttron_central, add_volttron_central_platform
from gevent.fileobject import FileObject
from gevent.subprocess import Popen
from volttron.types.server_config import ServerConfig, ServiceConfigs

# from volttron.platform import packaging
from volttron.utils import jsonapi

# from volttron.utils.keystore import encode_key, decode_key  # Removed - ZMQ specific
from volttrontesting.fixtures.cert_fixtures import certs_profile_2

try:
    from volttron.utils import execute_command, store_message_bus_config, strip_comments
except ImportError:
    strip_comments = None
    store_message_bus_config = None
    execute_command = None

try:
    from volttron.client.known_identities import (
        CONTROL,
        CONTROL_CONNECTION,
        PLATFORM_WEB,
        PROCESS_IDENTITIES,
    )
except ImportError:
    PLATFORM_WEB = "platform.web"
    CONTROL = "control"
    CONTROL_CONNECTION = "control.connection"
    PROCESS_IDENTITIES = []

from volttron.utils.certs import Certs
from volttron.utils.commands import is_volttron_running, wait_for_volttron_startup

try:
    from volttron.utils.logs import setup_logging
except ImportError:
    # Provide a simple logging setup if the volttron logs module is not available
    import logging

    def setup_logging(level=logging.INFO):
        logging.basicConfig(level=level)


try:
    from volttron.server.aip import AIPplatform
except ImportError:
    AIPplatform = None

# Auth components are no longer used in this version
AuthFile = None
AuthEntry = None
AuthFileEntryAlreadyExists = None

# KeyStore and KnownHostsStore are no longer needed
KeyStore = None
KnownHostsStore = None

try:
    from volttron.client import Agent, Connection
except ImportError:
    Agent = None
    Connection = None
# from volttrontesting.fixtures.rmq_test_setup import create_rmq_volttron_setup
# from volttron.utils.rmq_setup import start_rabbit, stop_rabbit
# from volttron.utils.rmq_setup import setup_rabbitmq_volttron
from volttron.utils.context import ClientContext as cc

from volttrontesting.utils import (
    get_hostname_and_random_port,
    get_rand_ip_and_port,
    get_rand_tcp_address,
    get_rand_vip,
)


@dataclass
class InstallAgentOptions:
    """Options for installing an agent on the platform."""
    start: bool = False
    vip_identity: Optional[str] = None
    agent_config: Optional[Union[dict, str]] = None
    startup_time: int = 5
    force: bool = False
    
    # Additional options for compatibility
    tag: Optional[str] = None
    priority: Optional[int] = None


setup_logging()
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
def create_platform_config_file(message_bus, instance_name, address, agent_monitor_frequency,
                                secure_agent_users):
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
        config.set("volttron", "message-bus", message_bus)
        config.set("volttron", "instance-name", instance_name)
        config.set("volttron", "address", address)
        config.set("volttron", "agent-monitor-frequency", str(agent_monitor_frequency))
        config.set("volttron", "secure-agent-users", str(secure_agent_users))
        with open(config_path, "w") as configfile:
            config.write(configfile)
    else:
        if not os.path.exists(v_home):
            os.makedirs(v_home, 0o755)
        config = ConfigParser()
        config.add_section("volttron")
        config.set("volttron", "message-bus", message_bus)
        config.set("volttron", "instance-name", instance_name)
        config.set("volttron", "address", address)
        config.set("volttron", "agent-monitor-frequency", str(agent_monitor_frequency))
        config.set("volttron", "secure-agent-users", str(secure_agent_users))

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
    if wrapper.ssl_auth:
        hostname, port = get_hostname_and_random_port()
        bind_address = 'https://{hostname}:{port}'.format(hostname=hostname, port=port)
    else:
        bind_address = "http://{}".format(get_rand_ip_and_port())

    # Will return https if messagebus rmq
    # bind_address = get_rand_http_address(wrapper.messagebus == 'rmq') if with_http else None
    vc_http = bind_address
    vc_tcp = get_rand_tcp_address() if with_tcp else None

    if add_local_vc_address:
        # KeyStore no longer used, generate placeholder key
        if wrapper.ssl_auth is True:
            volttron_central_address = vc_http
        else:
            volttron_central_address = vc_tcp
            volttron_central_serverkey = "placeholder-serverkey"

    wrapper.startup_platform(address=vc_tcp,
                             bind_web_address=bind_address,
                             volttron_central_address=volttron_central_address,
                             volttron_central_serverkey=volttron_central_serverkey)
    if with_http:
        discovery = "{}/discovery/".format(vc_http)
        # Use urllib instead of grequests for simpler dependency
        import urllib.request
        response = urllib.request.urlopen(discovery)
        assert response.status == 200

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
        os.environ = copy_env
        cc.__volttron_home__ = copy_cc_vhome


class PlatformWrapper:

    def __init__(self,
                 messagebus=None,
                 ssl_auth=False,
                 instance_name=None,
                 secure_agent_users=False,
                 remote_platform_ca=None):
        """ Initializes a new VOLTTRON instance

        Creates a temporary VOLTTRON_HOME directory with a packaged directory
        for agents that are built.

        :param messagebus: rmq or zmq - determines which dependencies to install
        :param ssl_auth: if message_bus=rmq, authenticate users if True
        """

        # This is hopefully going to keep us from attempting to shutdown
        # multiple times.  For example if a fixture calls shutdown and a
        # lower level fixture calls shutdown, this won't hang.
        self._instance_shutdown = False

        # Determine dependencies based on messagebus type
        self.messagebus = messagebus if messagebus else 'zmq'
        self.dependencies = self._determine_dependencies()
        
        # Initialize mock infrastructure for mock messagebus
        self.mock_config_store = {} if self.messagebus == 'mock' else None
        self.test_server = None  # Will be initialized if mock mode

        self.volttron_home = create_volttron_home()
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
            'HTTPS_PROXY': os.environ.get('HTTPS_PROXY', ''),
            'https_proxy': os.environ.get('https_proxy', '')
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

        # This is used as command line entry replacement.  Especially working
        # with older 2.0 agents.
        self.opts = {}

        self.services = {}

        # KeyStore is no longer used
        self.keystore = None
        self.serverkey = None
        self.publickey = None
        self.secure_agent_users = secure_agent_users
        self.ssl_auth = ssl_auth
        self.instance_name = instance_name
        if not self.instance_name:
            self.instance_name = os.path.basename(os.path.dirname(self.volttron_home))

        with with_os_environ(self.env):
            from volttron.utils import ClientContext
            if store_message_bus_config:
                store_message_bus_config(self.messagebus, self.instance_name)
            else:
                # Manually create config file if store_message_bus_config is not available
                self._create_platform_config_manually()
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

            Path(self.volttron_home).joinpath('certificates').mkdir(exist_ok=True)
            self.certsobj = Certs()    #Path(self.volttron_home).joinpath("certificates"))

            self.debug_mode = self.env.get('DEBUG_MODE', False)
            if not self.debug_mode:
                self.debug_mode = self.env.get('DEBUG', False)
            self.skip_cleanup = self.env.get('SKIP_CLEANUP', False)
            self.server_config = ServerConfig()

    def get_identity_keys(self, identity: str):
        # KeyStore is no longer used, return empty dict
        return {}

    def logit(self, message):
        print('{}: {}'.format(self.volttron_home, message))

    def add_service_config(self, service_name, enabled=True, **kwargs):
        """Add a configuration for an existing service to be configured.

        This must be called before the startup_platform method in order
        for it to have any effect.  kwargs will be transferred into the service_config.yml
        file under the service_name passed.
        """
        service_names = self.get_service_names()
        assert service_name in service_names, f"Only discovered services can be configured: {service_names}."
        self.services[service_name] = {}
        self.services[service_name]["enabled"] = enabled
        self.services[service_name]["kwargs"] = kwargs

    def get_service_names(self):
        """Retrieve the names of services available to configure.
        """
        services = ServiceConfigs(
            Path(self.volttron_home).joinpath("service_config.yml"), ServerConfig())
        return services.get_service_names()

    def allow_all_connections(self):
        """ Add a /.*/ entry to the auth.json file.
        """
        if not AuthFile or not AuthEntry:
            self.logit("AuthFile/AuthEntry not available, skipping allow_all_connections")
            return

        with with_os_environ(self.env):
            entry = AuthEntry(credentials="/.*/", comments="Added by platformwrapper")
            authfile = AuthFile(self.volttron_home + "/auth.json")
            try:
                authfile.add(entry)
            except AuthFileEntryAlreadyExists:
                pass

    def get_agent_identity(self, agent_uuid):
        identity = None
        path = os.path.join(self.volttron_home, 'agents/{}/IDENTITY'.format(agent_uuid))
        with open(path) as f:
            identity = f.read().strip()
        return identity

    def get_agent_by_identity(self, identity):
        for agent in self.list_agents():
            if agent.get('identity') == identity:
                return agent

    def build_connection(self,
                         peer=None,
                         address=None,
                         identity=None,
                         publickey=None,
                         secretkey=None,
                         serverkey=None,
                         capabilities: Optional[dict] = None,
                         **kwargs):
        self.logit('Building connection to {}'.format(peer))
        with with_os_environ(self.env):
            self.allow_all_connections()

            if identity is None:
                # Set identity here instead of AuthEntry creating one and use that identity to create Connection class.
                # This is to ensure that RMQ test cases get the correct current user that matches the auth entry made
                identity = str(uuid.uuid4())
            if address is None:
                self.logit('Default address was None so setting to current instances')
                address = self.address
                serverkey = self.serverkey
            if serverkey is None:
                self.logit("serverkey wasn't set but the address was.")
                raise Exception("Invalid state.")

            if publickey is None or secretkey is None:
                self.logit(
                    'generating new public secret key pair - skipping as KeyStore no longer used')
                # Generate dummy keys for compatibility
                import uuid
                publickey = str(uuid.uuid4())
                secretkey = str(uuid.uuid4())

                if AuthEntry and AuthFile:
                    entry = AuthEntry(capabilities=capabilities,
                                      comments="Added by test",
                                      credentials=keys.public,
                                      user_id=identity,
                                      identity=identity)
                    file = AuthFile(self.volttron_home + "/auth.json")
                    file.add(entry)

            # Connection is no longer used in the new architecture
            # Return None for now as Connection functionality might be integrated into Agent
            self.logit("Connection class is deprecated, returning None")
            return None

    def build_agent(self,
                    address=None,
                    should_spawn=True,
                    identity=None,
                    publickey=None,
                    secretkey=None,
                    serverkey=None,
                    agent_class=None,
                    capabilities: Optional[dict] = None,
                    **kwargs):
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
            if publickey is None:
                self.logit(
                    'generating new public secret key pair - skipping as KeyStore no longer used')
                # Generate dummy keys for compatibility
                publickey = str(uuid.uuid4())
                secretkey = str(uuid.uuid4())

            if address is None:
                self.logit('Using address {address}'.format(address=self.address))
                address = self.address

            if publickey and not serverkey:
                self.logit('using instance serverkey: {}'.format(publickey))
                serverkey = publickey
            self.logit("BUILD agent VOLTTRON HOME: {}".format(self.volttron_home))

            if 'enable_store' not in kwargs:
                kwargs['enable_store'] = False

            if capabilities is None:
                capabilities = dict(edit_config_store=dict(identity=identity))

            # Add auth entry for the agent using vctl auth add
            # Skip auth add for now as control connection credentials aren't being created
            # This is a known issue with the new architecture
            self.logit(
                f"Skipping auth add for {identity} - control connection not implemented yet")

            # allow 2 seconds here for the auth to be updated in auth service
            # before connecting to the platform with the agent.
            gevent.sleep(2)
            
            # For mock mode, always use MockAgent unless a specific agent_class is provided
            if self.messagebus == 'mock' and agent_class is None:
                from volttrontesting.mock_agent import MockAgent
                agent_class = MockAgent
                
            # Make sure we have an agent class (for non-mock mode)
            if agent_class is None:
                agent_class = Agent
                
            if agent_class is None:
                raise ValueError("No Agent class available. Ensure volttron.client.Agent is installed.")
            
            # Set AGENT_VIP_IDENTITY environment variable if using Agent class
            # This is required by the new Agent class initialization
            if agent_class == Agent:
                # Create credentials file for the agent
                cred_dir = Path(self.volttron_home) / "credentials_store"
                cred_dir.mkdir(exist_ok=True)
                cred_file = cred_dir / f"{identity}.json"
                
                # Create basic credentials
                credentials_data = {
                    "identity": identity,
                    "domain": None,
                    "address": address,
                    "serverkey": serverkey,
                    "publickey": publickey,
                    "secretkey": secretkey
                }
                
                with open(cred_file, 'w') as f:
                    jsonapi.dump(credentials_data, f)
                
                # Set environment variable
                if 'AGENT_VIP_IDENTITY' not in os.environ:
                    os.environ['AGENT_VIP_IDENTITY'] = identity
                
            agent = agent_class(address=address,
                                identity=identity,
                                publickey=publickey,
                                secretkey=secretkey,
                                serverkey=serverkey,
                                instance_name=self.instance_name,
                                volttron_home=self.volttron_home,
                                message_bus=self.messagebus,
                                **kwargs)
            self.logit('platformwrapper.build_agent.address: {}'.format(address))
            
            # For mock mode with TestServer, connect the agent and set up pubsub interception
            if self.messagebus == 'mock' and self.test_server:
                self.test_server.connect_agent(agent)
                
                # If using MockAgent, set up test server reference for full VIP functionality
                if hasattr(agent, 'set_test_server'):
                    agent.set_test_server(self.test_server)
                else:
                    # Set up pubsub interception to route through TestServer for regular agents
                    from volttrontesting.pubsub_interceptor import intercept_agent_pubsub
                    intercept_agent_pubsub(agent, self.test_server.__server_pubsub__)
                    
                self.logit(f"Connected agent {identity} to TestServer with pubsub interception")

            if should_spawn:
                self.logit(f'platformwrapper.build_agent spawning for identity {identity}')
                event = gevent.event.Event()
                gevent.spawn(agent.core.run, event)
                event.wait(timeout=2)
                
                # Skip ping for mock mode
                if self.messagebus != 'mock':
                    router_ping = agent.vip.ping("").get(timeout=30)
                    assert len(router_ping) > 0

            agent.publickey = publickey
            return agent

    def _read_auth_file(self):
        auth_path = os.path.join(self.volttron_home, 'auth.json')
        try:
            with open(auth_path, 'r') as fd:
                data = strip_comments(FileObject(fd, close=False).read().decode('utf-8'))
                if data:
                    auth = jsonapi.loads(data)
                else:
                    auth = {}
        except IOError:
            auth = {}
        if 'allow' not in auth:
            auth['allow'] = []
        return auth, auth_path

    def _append_allow_curve_key(self, publickey, identity):
        if not AuthEntry or not AuthFile:
            return

        if identity:
            entry = AuthEntry(user_id=identity,
                              identity=identity,
                              credentials=publickey,
                              capabilities={'edit_config_store': {
                                  'identity': identity
                              }},
                              comments="Added by platform wrapper")
        else:
            entry = AuthEntry(credentials=publickey,
                              comments="Added by platform wrapper. No identity passed")
        authfile = AuthFile(self.volttron_home + "/auth.json")
        authfile.add(entry, no_error=True)

    def add_capabilities(self, publickey, capabilities):
        if not AuthFile:
            self.logit("AuthFile not available, skipping add_capabilities")
            return

        with with_os_environ(self.env):
            if isinstance(capabilities, str) or isinstance(capabilities, dict):
                capabilities = [capabilities]
            auth_path = self.volttron_home + "/auth.json"
            auth = AuthFile(auth_path)
            entry = auth.find_by_credentials(publickey)[0]
            caps = entry.capabilities

            if isinstance(capabilities, list):
                for c in capabilities:
                    self.add_capability(c, caps)
            else:
                self.add_capability(capabilities, caps)
            auth.add(entry, overwrite=True)
            _log.debug("Updated entry is {}".format(entry))
            # Minimum sleep of 2 seconds seem to be needed in order for auth updates to get propagated to peers.
            # This slow down is not an issue with file watcher but rather vip.peerlist(). peerlist times out
            # when invoked in quick succession. add_capabilities updates auth.json, gets the peerlist and calls all peers'
            # auth.update rpc call. So sleeping here instead expecting individual test cases to sleep for long
            gevent.sleep(2)

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

    def set_auth_dict(self, auth_dict):
        if auth_dict:
            with open(os.path.join(self.volttron_home, 'auth.json'), 'w') as fd:
                fd.write(jsonapi.dumps(auth_dict))

    def _determine_dependencies(self):
        """Determine which dependencies to install based on messagebus type."""
        if self.messagebus == 'zmq':
            return ['volttron-lib-zmq', 'volttron-lib-auth']
        elif self.messagebus == 'rmq':
            # Add RMQ-specific dependencies here when needed
            return ['volttron-lib-rmq', 'volttron-lib-auth']
        else:
            return []

    def _create_platform_config_manually(self):
        """Create platform config file manually when store_message_bus_config is not available."""
        config_path = os.path.join(self.volttron_home, "config")
        config = ConfigParser()
        config.add_section("volttron")
        config.set("volttron", "messagebus", self.messagebus)
        config.set("volttron", "instance-name", self.instance_name)
        # address will be set later in startup_platform
        # These other settings may not be needed for the new config format

        with open(config_path, "w") as configfile:
            config.write(configfile)
        # all agents need read access to config file
        os.chmod(config_path, 0o744)

    def install_dependencies(self):
        """Install dependencies using pip in the current Python environment."""
        global store_message_bus_config, execute_command

        if not self.dependencies:
            self.logit(f"No dependencies to install for messagebus: {self.messagebus}")
            return

        with with_os_environ(self.env):
            self.logit(f"Installing {self.messagebus} dependencies: {self.dependencies}")
            for dep in self.dependencies:
                # Check if dependency is already installed
                check_cmd = [self.python, '-m', 'pip', 'show', dep]
                try:
                    result = subprocess.run(check_cmd,
                                            capture_output=True,
                                            text=True,
                                            env=self.env)
                    if result.returncode == 0:
                        self.logit(f"{dep} is already installed, skipping...")
                        continue
                except:
                    pass    # If check fails, try to install anyway

                self.logit(f"Installing {dep}...")
                cmd = [self.python, '-m', 'pip', 'install', dep]
                try:
                    if execute_command:
                        result = execute_command(cmd, env=self.env, logger=_log)
                    else:
                        result = subprocess.run(cmd, capture_output=True, text=True,
                                                env=self.env).stdout
                    self.logit(f"Successfully installed {dep}")
                except Exception as e:
                    self.logit(f"Failed to install {dep}: {e}")
                    raise PlatformWrapperError(f"Failed to install dependency {dep}: {e}")

            self.logit("Dependencies installed successfully")

            # After installing dependencies, try to import missing functions
            global Agent, Connection
            if not store_message_bus_config:
                try:
                    from volttron.utils import execute_command as ec
                    from volttron.utils import store_message_bus_config as smbc
                    store_message_bus_config = smbc
                    execute_command = ec
                    self.logit("Util functions loaded after dependency installation")
                    # Now store the config properly
                    if store_message_bus_config:
                        store_message_bus_config(self.messagebus, self.instance_name)
                except ImportError:
                    self.logit(
                        "Warning: util functions still not available after dependency installation"
                    )

            # Try to import Agent and Connection after dependencies are installed
            global Agent
            if not Agent:
                try:
                    from volttron.client import Agent as A
                    Agent = A
                    self.logit("Agent loaded after dependency installation")
                except ImportError:
                    self.logit("Warning: Agent still not available after dependency installation")

    def startup_platform(
        self,
        address,
        auth_dict=None,
        mode=UNRESTRICTED,
        msgdebug=False,
        setupmode=False,
        agent_monitor_frequency=600,
        timeout=60,
    # Allow the AuthFile to be preauthenticated with keys for service agents.
        perform_preauth_service_agents=True):

        # Install dependencies before starting platform (skip for mock)
        if self.messagebus != 'mock':
            self.install_dependencies()

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

            if perform_preauth_service_agents and AuthFile:
                authfile = AuthFile()
                if not authfile.read_allow_entries():
                    # if this is a brand new auth.json
                    # ZMQ-specific auth setup - commented out as it requires keystore
                    # # pre-seed all of the volttron process identities before starting the platform
                    # for identity in PROCESS_IDENTITIES:
                    #     if identity == PLATFORM_WEB:
                    #         capabilities = dict(allow_auth_modifications=None)
                    #     else:
                    #         capabilities = dict(edit_config_store=dict(identity="/.*/"))

                    #     ks = KeyStore(KeyStore.get_agent_keystore_path(identity))
                    #     entry = AuthEntry(credentials=encode_key(decode_key(ks.public)),
                    #                       user_id=identity,
                    #                       identity=identity,
                    #                       capabilities=capabilities,
                    #                       comments='Added by pre-seeding.')
                    #     authfile.add(entry)

                    # # Control connection needs to be added so that vctl can connect easily
                    # identity = CONTROL_CONNECTION
                    # capabilities = dict(edit_config_store=dict(identity="/.*/"))
                    # ks = KeyStore(KeyStore.get_agent_keystore_path(identity))
                    # entry = AuthEntry(credentials=encode_key(decode_key(ks.public)),
                    #                   user_id=identity,
                    #                   identity=identity,
                    #                   capabilities=capabilities,
                    #                   comments='Added by pre-seeding.')
                    # authfile.add(entry)

                    # identity = "dynamic_agent"
                    # capabilities = dict(edit_config_store=dict(identity="/.*/"), allow_auth_modifications=None)
                    # # Lets cheat a little because this is a wrapper and add the dynamic agent in here as well
                    # ks = KeyStore(KeyStore.get_agent_keystore_path(identity))
                    # entry = AuthEntry(credentials=encode_key(decode_key(ks.public)),
                    #                   user_id=identity,
                    #                   identity=identity,
                    #                   capabilities=capabilities,
                    #                   comments='Added by pre-seeding.')
                    # authfile.add(entry)
                    pass    # Placeholder for ZMQ auth setup

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
            ipc = 'ipc://{}{}/run/'.format('@' if sys.platform.startswith('linux') else '',
                                           self.volttron_home)
            self.local_address = ipc + 'vip.socket'
            self.set_auth_dict(auth_dict)

            if self.remote_platform_ca:
                ca_bundle_file = os.path.join(self.volttron_home, "cat_ca_certs")
                with open(ca_bundle_file, 'w') as cf:
                    if self.ssl_auth:
                        with open(self.certsobj.cert_file(self.certsobj.root_ca_name)) as f:
                            cf.write(f.read())
                    with open(self.remote_platform_ca) as f:
                        cf.write(f.read())
                os.chmod(ca_bundle_file, 0o744)
                self.env['REQUESTS_CA_BUNDLE'] = ca_bundle_file
                os.environ['REQUESTS_CA_BUNDLE'] = self.env['REQUESTS_CA_BUNDLE']
            # This file will be passed off to the main.py and available when
            # the platform starts up.
            self.requests_ca_bundle = self.env.get('REQUESTS_CA_BUNDLE')

            self.opts.update({
                'verify_agents': False,
                'address': address,
                'volttron_home': self.volttron_home,
                'vip_local_address': ipc + 'vip.socket',
                'publish_address': ipc + 'publish',
                'subscribe_address': ipc + 'subscribe',
                'secure_agent_users': self.secure_agent_users,
                'platform_name': None,
                'log': self.log_path,
                'log_config': None,
                'monitor': True,
                'autostart': True,
                'log_level': logging.DEBUG,
                'verboseness': logging.DEBUG,
                'web_ca_cert': self.requests_ca_bundle
            })

            # Known hosts handling is no longer needed without KeyStore
            # Platform will handle authentication through the installed dependencies

            # Only use old config creation if we have the function, otherwise skip as we already created it
            if 'create_platform_config_file' in globals() and callable(
                    create_platform_config_file):
                pass    # Skip for now as it uses wrong format
            # create_platform_config_file(self.messagebus, self.instance_name, self.address, agent_monitor_frequency,
            #                              self.secure_agent_users)
            if self.ssl_auth:
                certsdir = os.path.join(self.volttron_home, 'certificates')

                self.certsobj = Certs(certsdir)

            if self.services:
                with Path(self.volttron_home).joinpath("service_config.yml").open('wt') as fp:
                    yaml.dump(self.services, fp)

            cmd = [self.volttron_exe]
            cmd.append('--messagebus')
            cmd.append(self.messagebus)
            # if msgdebug:
            #     cmd.append('--msgdebug')
            if enable_logging:
                cmd.append('-vv')
            cmd.append('-l{}'.format(self.log_path))
            if setupmode:
                cmd.append('--setup-mode')

            from pprint import pprint
            print('process environment: ')
            pprint(self.env)
            print('popen params: {}'.format(cmd))
            self.p_process = Popen(cmd,
                                   env=self.env,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   universal_newlines=True)

            # A None value means that the process is still running.
            # A negative means that the process exited with an error.
            assert self.p_process.poll() is None

            # The new volttron may not create a PID file, so let's check differently
            # wait_for_volttron_startup(self.volttron_home, timeout)

            # Instead, check if process is running and give it time to initialize
            gevent.sleep(5)
            if self.p_process.poll() is not None:
                # Get stderr for debugging
                _, stderr = self.p_process.communicate()
                self.logit(f"Platform stderr: {stderr}")
                raise PlatformWrapperError(
                    f"Platform process exited with code {self.p_process.poll()}: {stderr}")

            # Create PID file manually for compatibility
            pid_file = os.path.join(self.volttron_home, "VOLTTRON_PID")
            with open(pid_file, "w") as f:
                f.write(str(self.p_process.pid))

            # Create platform credentials for agents to use
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

            # Server key will be handled by the installed dependencies
            self.serverkey = platform_public    # Use the platform public key as server key

            # Use dynamic_agent so we can look and see the agent with peerlist.
            if not setupmode:
                # Wait for platform to stabilize
                gevent.sleep(3)

                # Try to ensure Agent is loaded
                global Agent
                if not Agent:
                    try:
                        from volttron.client import Agent
                        self.logit("Agent imported successfully in startup_platform")
                    except ImportError as e:
                        self.logit(f"Failed to import Agent: {e}")

                if Agent:    # Only build agent if Agent class is available
                    try:
                        self.dynamic_agent = self.build_agent(identity="dynamic_agent")
                        if self.dynamic_agent is not None:
                            assert isinstance(self.dynamic_agent, Agent)
                    except Exception as e:
                        self.logit(f"Failed to build dynamic agent: {e}")
                        self.dynamic_agent = None
                else:
                    self.logit("Agent class not available, skipping dynamic_agent creation")
                    self.dynamic_agent = None
                if self.dynamic_agent:
                    has_control = False
                    times = 0
                    while not has_control and times < 10:
                        times += 1
                        try:
                            has_control = CONTROL in self.dynamic_agent.vip.peerlist().get(
                                timeout=.2)
                            self.logit("Has control? {}".format(has_control))
                        except gevent.Timeout:
                            pass

                    if not has_control:
                        self.shutdown_platform()
                        raise Exception("Couldn't connect to core platform!")
                else:
                    self.logit("Skipping control check as Agent is not available")

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

    def direct_sign_agentpackage_creator(self, package):
        assert RESTRICTED, "Auth not available"
        print("wrapper.certsobj", self.certsobj.cert_dir)
        assert (auth.sign_as_creator(
            package, 'creator', certsobj=self.certsobj)), "Signing as {} failed.".format('creator')

    def direct_sign_agentpackage_admin(self, package):
        assert RESTRICTED, "Auth not available"
        assert (auth.sign_as_admin(
            package, 'admin', certsobj=self.certsobj)), "Signing as {} failed.".format('admin')

    def direct_sign_agentpackage_initiator(self, package, config_file, contract):
        assert RESTRICTED, "Auth not available"
        files = {"config_file": config_file, "contract": contract}
        assert (auth.sign_as_initiator(
            package, 'initiator', files=files,
            certsobj=self.certsobj)), "Signing as {} failed.".format('initiator')

    def _aip(self):
        opts = type('Options', (), self.opts)
        aip = AIPplatform(opts)
        aip.setup()
        return aip

    def __install_agent_wheel__(self, wheel_file, start, vip_identity):
        with with_os_environ(self.env):
            self.__wait_for_control_connection_to_exit__()

            self.logit("VOLTTRON_HOME SETTING: {}".format(self.env['VOLTTRON_HOME']))
            env = self.env.copy()
            cmd = ['volttron-ctl', '--json', 'install', wheel_file]
            if vip_identity:
                cmd.extend(['--vip-identity', vip_identity])

            res = execute_command(cmd, env=env, logger=_log)
            assert res, "failed to install wheel:{}".format(wheel_file)
            res = jsonapi.loads(res)
            agent_uuid = res['agent_uuid']
            self.logit(f"Inside __install_agent_wheel__ res is: {res}")
            self.logit(agent_uuid)
            self.logit(f"After exec install command {self.dynamic_agent.vip.peerlist().get()}")

            if start:
                self.start_agent(agent_uuid)
            return agent_uuid

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
                results = self.install_agent(agent_dir=path, config_file=config, start=start)

        return results

    def install_agent(self,
                      agent_wheel: Optional[str] = None,
                      agent_dir: Optional[str] = None,
                      config_file: Optional[Union[dict, str]] = None,
                      start: bool = True,
                      vip_identity: Optional[str] = None,
                      startup_time: int = 5,
                      force: bool = False,
                      install_options: Optional[InstallAgentOptions] = None):
        """
        Install and optionally start an agent on the instance.

        This function allows installation from an agent wheel or an
        agent directory (NOT BOTH).  If an agent_wheel is specified then
        it is assumed to be ready for installation (has a config file).
        If an agent_dir is specified then a config_file file must be
        specified or if it is not specified then it is assumed that the
        file agent_dir/config is to be used as the configuration file.  If
        none of these exist then an assertion error will be thrown.

        This function will return with a uuid of the installed agent.

        :param agent_wheel:
        :param agent_dir:
        :param config_file:
        :param start:
        :param vip_identity:
        :param startup_time:
            How long in seconds is required for the agent to start up fully
        :param force:
            Should this overwrite the current or not.
        :return:
        """
        # If InstallAgentOptions provided, use those values
        if install_options:
            start = install_options.start
            vip_identity = install_options.vip_identity or vip_identity
            config_file = install_options.agent_config or config_file
            startup_time = install_options.startup_time
            force = install_options.force
            
        with with_os_environ(self.env):
            _log.debug(
                f"install_agent called with params\nagent_wheel: {agent_wheel}\nagent_dir: {agent_dir}"
            )
            self.__wait_for_control_connection_to_exit__()
            assert self.is_running(), "Instance must be running to install agent."
            assert agent_wheel or agent_dir, "Invalid agent_wheel or agent_dir."
            assert isinstance(startup_time, int), "Startup time should be an integer."

            if agent_wheel:
                assert not agent_dir
                assert not config_file
                assert os.path.exists(agent_wheel)
                wheel_file = agent_wheel
                agent_uuid = self.__install_agent_wheel__(wheel_file, False, vip_identity)
                assert agent_uuid

            # Now if the agent_dir is specified.
            temp_config = None
            if agent_dir:
                assert not agent_wheel
                temp_config = os.path.join(self.volttron_home,
                                           os.path.basename(agent_dir) + "_config_file")
                if isinstance(config_file, dict):
                    from os.path import basename, join
                    temp_config = join(self.volttron_home, basename(agent_dir) + "_config_file")
                    with open(temp_config, "w") as fp:
                        fp.write(jsonapi.dumps(config_file))
                    config_file = temp_config
                elif not config_file:
                    if os.path.exists(os.path.join(agent_dir, "config")):
                        config_file = os.path.join(agent_dir, "config")
                    else:
                        from os.path import basename, join
                        temp_config = join(self.volttron_home,
                                           basename(agent_dir) + "_config_file")
                        with open(temp_config, "w") as fp:
                            fp.write(jsonapi.dumps({}))
                        config_file = temp_config
                elif os.path.exists(config_file):
                    pass    # config_file already set!
                else:
                    raise ValueError("Can't determine correct config file.")

                cmd = [
                    self.vctl_exe, "--json", "install", agent_dir, "--agent-config", config_file
                ]

                if force:
                    cmd.extend(["--force"])
                if vip_identity:
                    cmd.extend(["--vip-identity", vip_identity])
                # vctl install with start seem to have a auth issue. For now start after install
                # if start:
                #     cmd.extend(["--start"])
                self.logit(f"Command installation is: {cmd}")
                stdout = execute_command(cmd,
                                         logger=_log,
                                         env=self.env,
                                         err_prefix="Error installing agent")
                self.logit(f"RESPONSE FROM INSTALL IS: {stdout}")
                # Because we are no longer silencing output from the install, the
                # the results object is now much more verbose.  Our assumption is
                # that the result we are looking for is the only JSON block in
                # the output

                match = re.search(r'^({.*})', stdout, flags=re.M | re.S)
                if match:
                    results = match.group(0)
                else:
                    raise ValueError("The results were not found in the command output")
                self.logit("here are the results: {}".format(results))

                #
                # Response from results is expected as follows depending on
                # parameters, note this is a json string so parse to get dictionary
                # {
                #     "started": true,
                #     "agent_pid": 26241,
                #     "starting": true,
                #     "agent_uuid": "ec1fd94e-922a-491f-9878-c392b24dbe50"
                # }
                assert results

                resultobj = jsonapi.loads(str(results))

                # if start:
                #     assert resultobj['started']
                agent_uuid = resultobj['agent_uuid']

                assert resultobj
                self.logit(f"resultobj: {resultobj}")
            assert agent_uuid
            time.sleep(5)
            if start:
                self.logit(f"We are running {agent_uuid}")
                # call start after install for now. vctl install with start seem to have auth issues.
                self.start_agent(agent_uuid)
                assert self.is_agent_running(agent_uuid)

            # remove temp config_file
            if temp_config and os.path.isfile(temp_config):
                os.remove(temp_config)

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
            result = execute_command(cmd, self.env)

            self.__wait_for_control_connection_to_exit__()

            # Confirm agent running
            cmd = [self.vctl_exe, '--json']
            cmd.extend(['status', agent_uuid])
            res = execute_command(cmd, env=self.env)

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
            res = execute_command(cmd,
                                  env=self.env,
                                  logger=_log,
                                  err_prefix="Error stopping agent")
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

    def install_library(self, library_path: Union[str, Path]) -> str:
        """
        Install a library (e.g., a driver library) into the platform.
        
        :param library_path: Path to the library directory or wheel file
        :return: Installation result/output
        """
        library_path = Path(library_path).resolve()
        
        if not library_path.exists():
            raise ValueError(f"Library path does not exist: {library_path}")
            
        with with_os_environ(self.env):
            self.logit(f"Installing library from {library_path}")
            
            # If it's a directory, we need to build/install it
            if library_path.is_dir():
                # Use pip to install in editable mode for development
                cmd = [sys.executable, "-m", "pip", "install", "-e", str(library_path)]
            else:
                # If it's a wheel file, install directly
                cmd = [sys.executable, "-m", "pip", "install", str(library_path)]
                
            result = execute_command(cmd, env=self.env, logger=_log)
            self.logit(f"Library installation complete: {result}")
            return result

    def remove_agent(self, agent_uuid):
        """Remove the agent specified by agent_uuid"""
        with with_os_environ(self.env):
            _log.debug("REMOVING AGENT: {}".format(agent_uuid))
            self.__wait_for_control_connection_to_exit__()
            cmd = [self.vctl_exe]
            cmd.extend(['remove', agent_uuid])
            res = execute_command(cmd,
                                  env=self.env,
                                  logger=_log,
                                  err_prefix="Error removing agent")
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
            res = execute_command(cmd,
                                  env=self.env,
                                  logger=_log,
                                  err_prefix="Error getting agent status")
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

    def restart_platform(self):
        with with_os_environ(self.env):
            original_skip_cleanup = self.skip_cleanup
            self.skip_cleanup = True
            self.shutdown_platform()
            self.skip_cleanup = original_skip_cleanup
            # since this is a restart, we don't want to do an update/overwrite of services.
            self.startup_platform(address=self.address, perform_preauth_service_agents=False)
            # we would need to reset shutdown flag so that platform is properly cleaned up on the next shutdown call
            self._instance_shutdown = False
            gevent.sleep(1)

    def stop_platform(self):
        """
        Stop the platform without cleaning up any agents or context of the
        agent.  This should be paired with restart platform in order to
        maintain the context of the platform.
        :return:
        """
        # Handle mock mode separately
        if self.messagebus == 'mock':
            if not self.is_running():
                return
            self.started = False
            # Stop any mock agents that were created
            if hasattr(self, 'agents'):
                for agent in self.agents:
                    if hasattr(agent, 'core') and hasattr(agent.core, 'stop'):
                        agent.core.stop()
            return
            
        with with_os_environ(self.env):
            if not self.is_running():
                return

            if self.dynamic_agent is not None:
                self.dynamic_agent.vip.rpc(CONTROL, "shutdown").get(timeout=20)
                self.dynamic_agent.core.stop(timeout=20)
            if self.p_process is not None:
                try:
                    gevent.sleep(0.2)
                    self.p_process.terminate()
                    gevent.sleep(0.2)
                except OSError:
                    self.logit('Platform process was terminated.')
            else:
                self.logit("platform process was null")
            #
            # cmd = [self.vctl_exe]
            # cmd.extend(['shutdown', '--platform'])
            # try:
            #     execute_command(cmd, env=self.env, logger=_log,
            #                     err_prefix="Error shutting down platform")
            # except RuntimeError:
            #     if self.p_process is not None:
            #         try:
            #             gevent.sleep(0.2)
            #             self.p_process.terminate()
            #             gevent.sleep(0.2)
            #         except OSError:
            #             self.logit('Platform process was terminated.')
            #     else:
            #         self.logit("platform process was null")
            # gevent.sleep(1)

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
                self.started = False
                self._instance_shutdown = True
                if not self.skip_cleanup:
                    self.__remove_home_directory__()
                return

            if not self.is_running():
                self.logit(
                    f"Instance running {self.is_running()} and skip cleanup: {self.skip_cleanup}")
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

                try:
                    # don't wait indefinitely as shutdown will not throw an error if RMQ is down/has cert errors
                    self.dynamic_agent.vip.rpc(CONTROL, 'shutdown').get(timeout=10)
                    self.dynamic_agent.core.stop(timeout=10)
                except gevent.Timeout:
                    self.logit("Timeout shutting down platform")
                self.dynamic_agent = None

            if self.p_process is not None:
                try:
                    gevent.sleep(0.2)
                    self.p_process.terminate()
                    gevent.sleep(0.2)
                except OSError:
                    self.logit('Platform process was terminated.')
                pid_file = "{vhome}/VOLTTRON_PID".format(vhome=self.volttron_home)
                try:
                    self.logit(f"Remove PID file: {pid_file}")
                    os.remove(pid_file)
                except OSError:
                    self.logit('Error while removing VOLTTRON PID file {}'.format(pid_file))
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
        Restores orignial rabbitmq.conf if volttrontesting with rmq
        :return:
        """

        def stop_rabbit_node():
            """
            Stop RabbitMQ Server
            :param rmq_home: RabbitMQ installation path
            :param env: Environment to run the RabbitMQ command.
            :param quite:
            :return:
            """
            _log.debug("Stop RMQ: {}".format(self.volttron_home))
            cmd = [
                os.path.join(self.rabbitmq_config_obj.rmq_home, "sbin/rabbitmqctl"), "stop", "-n",
                self.rabbitmq_config_obj.node_name
            ]
            execute_command(cmd, env=self.env)
            gevent.sleep(2)
            _log.info("**Stopped rmq node: {}".format(self.rabbitmq_config_obj.node_name))

        if self.messagebus == 'rmq':
            stop_rabbit_node()

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
