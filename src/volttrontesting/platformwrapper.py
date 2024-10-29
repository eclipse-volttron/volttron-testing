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
from __future__ import annotations

from attr import dataclass
from gevent import monkey
from gevent.greenlet import Greenlet
from volttron.types.agent_context import AgentOptions

monkey.patch_thread()

import logging
import os
import queue
import threading
from copy import copy
from dataclasses import asdict
from pathlib import Path
from queue import Queue

import psutil
import shutil
import sys
import tempfile
import time
import re

from contextlib import contextmanager
from subprocess import CalledProcessError

import gevent
import gevent.subprocess as subprocess

from volttron.server.server_options import ServerOptions
from volttron.types import Identity, PathStr, AgentContext
from volttron.types.server_config import ServiceConfigs, ServerConfig
from gevent.subprocess import Popen

from volttron.types import AgentUUID
from volttron.client import Agent
import volttron.utils.jsonapi as jsonapi


from volttron.client.known_identities import CONTROL, CONTROL_CONNECTION

from volttron.utils.commands import wait_for_volttron_startup, is_volttron_running
from volttrontesting.utils import get_rand_vip
from volttron.utils.context import ClientContext as cc
from pytest_virtualenv import VirtualEnv
from git import Repo

_log = logging.getLogger(__name__)

# Change the connection timeout to default to 5 seconds rather than the default
# of 30 seconds
DEFAULT_TIMEOUT = 5

AgentT = type(Agent)

class PlatformWrapperError(Exception):
    pass


def create_server_options(messagebus="zmq") -> ServerOptions:
    """
    Create a new `ServerOptions` object to be used with the PlatformWrapper.  This object allows configuration
    of volttron via a object interface rather than through dictionary keys.  The defaut version will create
    a new address and volttron_home each time this method is called.

    :param messagebus:
    :return: ServerOptions
    """
    # TODO: We need to make local-address generic
    server_options = ServerOptions(volttron_home=Path(create_volttron_home()),
                                   address=get_rand_vip(),
                                   local_address="ipc://@$VOLTTRON_HOME/run/vip.socket",
                                   messagebus=messagebus)
    return server_options

# # TODO: This partially duplicates functionality in volttron-core.utils.messagebus.py. These should probably be combined.
# def create_platform_config_file(message_bus, instance_name, vip_address, agent_monitor_frequency,
#                                 secure_agent_users):
#     # If there is no config file or home directory yet, create volttron_home
#     # and config file
#     if not instance_name:
#         raise ValueError("Instance name should be a valid string and should "
#                          "be unique within a network of volttron instances "
#                          "that communicate with each other. start volttron "
#                          "process with '--instance-name <your instance>' if "
#                          "you are running this instance for the first time. "
#                          "Or add instance-name = <instance name> in "
#                          "volttron_home/config")
#
#     v_home = cc.get_volttron_home()
#     config_path = os.path.join(v_home, "config")
#     if os.path.exists(config_path):
#         config = ConfigParser()
#         config.read(config_path)
#         config.set("volttron", "message-bus", message_bus)
#         config.set("volttron", "instance-name", instance_name)
#         config.set("volttron", "vip-address", vip_address)
#         config.set("volttron", "agent-monitor-frequency", str(agent_monitor_frequency))
#         config.set("volttron", "secure-agent-users", str(secure_agent_users))
#         with open(config_path, "w") as configfile:
#             config.write(configfile)
#     else:
#         if not os.path.exists(v_home):
#             os.makedirs(v_home, 0o755)
#         config = ConfigParser()
#         config.add_section("volttron")
#         config.set("volttron", "message-bus", message_bus)
#         config.set("volttron", "instance-name", instance_name)
#         config.set("volttron", "vip-address", vip_address)
#         config.set("volttron", "agent-monitor-frequency", str(agent_monitor_frequency))
#         config.set("volttron", "secure-agent-users", str(secure_agent_users))
#
#         with open(config_path, "w") as configfile:
#             config.write(configfile)
#         # all agents need read access to config file
#         os.chmod(config_path, 0o744)


def build_vip_address(dest_wrapper, agent):
    """
    Create a usable vip address with zap parameters embedded in the uri.

    :param dest_wrapper:PlatformWrapper:
        The destination wrapper instance that the agent will be attempting to
        connect to.
    :param agent:Agent
        The agent that is being used to make the connection to dest_wrapper
    :return:
    """
    return "{}:?serverkey={}&publickey={}&secretkey={}".format(
        dest_wrapper.vip_address, dest_wrapper.publickey,
        agent.core.publickey, agent.core.secretkey
    )


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

DEFAULT_START: bool = True
@dataclass(frozen=True)
class InstallAgentOptions:
    config_file: dict | PathStr = None
    start: bool = DEFAULT_START
    vip_identity: Identity = None
    startup_time: int = 5
    force: bool = False

    @staticmethod
    def create(**kwargs) -> InstallAgentOptions:
        return InstallAgentOptions(**kwargs)


DefaultAgentInstallOptions = InstallAgentOptions()

class PlatformWrapper:
    def __init__(self, options: ServerOptions, project_toml_file: Path | str, start_platform: bool = True,
                 skip_cleanup: bool = False, environment_updates: dict[str, str] = None, enable_sys_queue: bool = False):
        """
        Initializes a new VOLTTRON instance

        Creates a temporary VOLTTRON_HOME directory with a packaged directory for agents that are built.

        :options: The environment that the platform will run under.
        :project_toml_file: The pyproject.toml file to use as a base for this platform wrapper and it's environment.
        :start_platform: Should the platform be started before returning from this constructor
        :skip_cleanup: Should the environment not be cleaned up (even when cleanup method is called)
        :environment_updates: A dictionary of environmental variables to use during execution.  Will be merged with
            existing variables so these will overwrite if there is a collision.
        :enable_sys_queue: Should stdout be intercepted to be analysed by calls to pop_stdout_queue method

        """

        # We need to use the toml file as a template for executing the proper environment of the
        # agent under test.
        if isinstance(project_toml_file, str):
            project_toml_file = Path(project_toml_file)
        if not project_toml_file.exists():
            raise ValueError(f"Toml file {project_toml_file} does not exist.")
        self._project_toml_file = project_toml_file.expanduser()

        self._volttron_exe = "volttron"
        self._vctl_exe = "vctl"
        self._log_path = options.volttron_home.parent.as_posix() + "/volttron.log"
        self._home_toml_file = options.volttron_home / "pyproject.toml"
        # Virtual environment path for the running environment.
        self._venv = options.volttron_home.parent / ".venv"

        # Should we clean up when this platform stops or leave the directory for debugging purposes.
        self._skip_cleanup = skip_cleanup

        self._server_options = options

        self._server_options.store()

        # These will be set from startup_platform call as a response from popen.
        self._platform_process = None
        self._virtual_env: VirtualEnv | None = None
        # in the context of this platform it is very important not to
        # use the main os.environ for anything.
        self._platform_environment = {
             'HOME': Path("~").expanduser().as_posix(),
             'VOLTTRON_HOME': self._server_options.volttron_home.as_posix(),
             # Elixir (rmq pre-req) requires locale to be utf-8
             'LANG': "en_US.UTF-8",
             'LC_ALL': "en_US.UTF-8",
             'PYTHONDONTWRITEBYTECODE': '1',
             'HTTPS_PROXY': os.environ.get('HTTPS_PROXY', ''),
             'https_proxy': os.environ.get('https_proxy', ''),
             #'POETRY_VIRTUALENVS_IN_PROJECT': 'false',
             #'POETRY_VIRTUALENVS_PATH': self._server_options.volttron_home.parent / "venv"
             # Use this virtual
             'VIRTUAL_ENV': self._venv.as_posix(),
             'PATH': f":{self._venv.as_posix()}/bin:" + os.environ.get("PATH", "")
        }

        # Allow debug override of skip_cleanup parameter.
        if 'DEBUG' in os.environ:
            self._skip_cleanup = True

        if environment_updates is not None:
            if not isinstance(environment_updates, dict):
                raise ValueError(f"environmental_update must be: dict[str, str] not type {type(environment_updates)}")
            self._platform_environment.update(environment_updates)

        # Create the volttron home as well as the new virtual environment for
        # this PlatformWrapper instance.
        self._setup_testing_environment()

        # Every instance comes with a dynamic_agent that will help to do
        # platform level things.
        self._dynamic_agent: Agent | None = None
        #self._dynamic_agent_task: Greenlet | None = None
        self._built_agent_tasks: list[Greenlet] = []

        self._enable_sys_queue = enable_sys_queue
        self._stdout_queue = Queue()
        self._stdout_thread: threading.Thread | None = None

        # Start the platform and include a dynamic agent to begin with if true.
        if start_platform:
            self.startup_platform()

        else:
            print("Not starting platform during constructor")

        # State variable to handling cascading shutdowns for this environment
        self._instance_shutdown = False
        # When install from GitHub is called this will be populated with the local path
        # so that it can be removed during shutdown.
        self._added_from_github: list[PathStr] = []

    @property
    def dynamic_agent(self) -> Agent:
        if self._dynamic_agent is None:
            # This is done so the dynamic agent can connect to the bus.
            # self._create_credentials(identity="dynamic")
            agent = self.build_agent(identity="dynamic")
            self._dynamic_agent = agent
            # self._built_agent_tasks.append(task)
            # self._dynamic_agent_task = task

        return self._dynamic_agent

    def pop_stdout_queue(self) -> str:
        if not self._enable_sys_queue:
            raise ValueError(f"SysQueue not enabled, pass True to PlatformWrapper constructor for enable_sys_queue "
                             f"argument.")
        try:
            yield self._stdout_queue.get_nowait()
        except queue.Empty:
            raise StopIteration()

    def clear_stdout_queue(self):
        if not self._enable_sys_queue:
            raise ValueError(f"SysQueue not enabled, pass True to PlatformWrapper constructor for enable_sys_queue "
                             f"argument.")
        try:
            while True:
                self._stdout_queue.get_nowait()
        except queue.Empty:
            print("done clearing stdout queue")

    @property
    def volttron_home(self) -> str:
        return self._server_options.volttron_home.as_posix()

    @property
    def volttron_address(self) -> list[str]:
        return copy(self._server_options.address)

    @property
    def skip_cleanup(self) -> bool:
        return self._skip_cleanup

    def logit(self, message):
        print('{}: {}'.format(self._server_options.volttron_home.as_posix(), message))



    def _create_credentials(self, identity: Identity):
        print(f"Creating Credentials for: {identity}")
        cmd = ['vctl', 'auth', 'add', identity]
        res = self._virtual_env.run(args=cmd, capture=True, env=self._platform_environment)
        print(f"Response from create credentials")
        print(res)

    # def add_service_config(self, service_name, enabled=True, **kwargs):
    #     """Add a configuration for an existing service to be configured.
    #
    #     This must be called before the startup_platform method in order
    #     for it to have any effect.  kwargs will be transferred into the service_config.yml
    #     file under the service_name passed.
    #     """
    #     service_names = self.get_service_names()
    #     assert service_name in service_names, f"Only discovered services can be configured: {service_names}."
    #     self.services[service_name] = {}
    #     self.services[service_name]["enabled"] = enabled
    #     self.services[service_name]["kwargs"] = kwargs

    def get_service_names(self):
        """Retrieve the names of services available to configure.
        """
        services = ServiceConfigs(Path(self.volttron_home).joinpath("service_config.yml"),
                                  ServerConfig())
        return services.get_service_names()

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

    # def build_connection(self, peer=None, address=None, identity=None,
    #                      publickey=None, secretkey=None, serverkey=None,
    #                      capabilities: Optional[dict] = None, **kwargs):
    #     self.logit('Building connection to {}'.format(peer))
    #     with with_os_environ(self.env):
    #         self.allow_all_connections()
    #
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
    #
    #         if publickey is None or secretkey is None:
    #             self.logit('generating new public secret key pair')
    #             keyfile = tempfile.mktemp(".keys", "agent", self.volttron_home)
    #             keys = KeyStore(keyfile)
    #             keys.generate()
    #             publickey = keys.public
    #             secretkey = keys.secret
    #
    #             entry = AuthEntry(capabilities=capabilities,
    #                               comments="Added by test",
    #                               credentials=keys.public,
    #                               user_id=identity,
    #                               identity=identity)
    #             file = AuthFile(self.volttron_home + "/auth.json")
    #             file.add(entry)
    #
    #         conn = Connection(address=address, peer=peer, publickey=publickey,
    #                           secretkey=secretkey, serverkey=serverkey,
    #                           instance_name=self.instance_name,
    #                           message_bus=self.messagebus,
    #                           volttron_home=self.volttron_home,
    #                           identity=identity)
    #
    #         return conn

    def build_agent(self, identity: Identity, agent_class: AgentT = Agent, options: AgentOptions = None) -> Agent:
        """
        Build an agent with a connection to the current platform.

        :param identity:
        :param agent_class: Agent class to build
        :return: AbstractAgent
        """
        from volttron.types.auth.auth_credentials import CredentialsFactory

        self.logit("Building generic agent.")

        # We need a copy because we are going to change it based upon the identity so the agent can start
        copy_env = copy(self._platform_environment)

        # Update OS env to current platform's env so get_home() call will result
        # in correct home director. Without this when more than one test instance are created, get_home()
        # will return home dir of last started platform wrapper instance
        with with_os_environ(copy_env):

            self._create_credentials(identity)

            os.environ["AGENT_VIP_IDENTITY"] = identity
            os.environ["VOLTTRON_PLATFORM_ADDRESS"] = self.volttron_address[0]
            os.environ["AGENT_CREDENTIALS"] = str(self._server_options.volttron_home / f"credentials_store/{identity}.json")
            creds = CredentialsFactory.load_from_environ()

            if options is None:
                options = AgentOptions()

            agent = agent_class(credentials=creds, config_path={}, address=self.volttron_address[0], options=options)

            try:
                run = agent.run
            except AttributeError:
                run = agent.core.run
            task = gevent.spawn(run)
            gevent.sleep(1)
            self._built_agent_tasks.append(task)

            return agent

    def run_command(self, cmd: list, cwd: Path | str = None) -> str:
        """
        Execute a shell command within the virtual environment.  This will run
        in the platformwrapper's context.

        if cwd is not set then the cwd will be set to `self.volttron_home`

        :raises CalledProcessError: If subprocess return value is not 0.
        :param cmd: list passed to subprocess
        :param cwd: directory to run the command in.
        :return: response of the call.
        """
        if cwd is None:
            cwd = self.volttron_home
        elif isinstance(cwd, Path):
            cwd = cwd.as_posix()

        try:
            output = self._virtual_env.run(args=cmd, capture=True, cwd=cwd, env=self._platform_environment, text=True)
        except CalledProcessError as e:
            print(f"Error:\n{e.output}")
            raise

        return output

    def install_library(self, library: str | Path, version: str = "latest"):

        if isinstance(library, Path):
            library = library.resolve()  # Ensure we have an absolute path
            if library.is_file() and library.suffix == ".whl":
                # Install the wheel file directly
                cmd = f"poetry add {library}"
            elif library.is_dir() and (library / "pyproject.toml").exists():
                # Install from a directory with pyproject.toml
                cmd = f"poetry add {library}"
            elif library.is_dir() and (library / "setup.py").exists():
                # Install from a directory with setup.py (legacy support)
                cmd = f"poetry add {library}"
            else:
                raise ValueError("The specified path is not a valid wheel file or project directory.")
        else:
            if version != "latest":
                cmd = f"poetry add {library}=={version}"
            else:
                cmd = f"poetry add {library}@latest"

        try:
            output = self._virtual_env.run(args=cmd, env=self._platform_environment, capture=True,
                                           cwd=self.volttron_home)
        except CalledProcessError as e:
            print(f"Error:\n{e.output}")
            raise

    def show(self) -> list[str]:

        cmd = f"poetry show"

        try:
            output = self._virtual_env.run(args=cmd, capture=True, cwd=self.volttron_home, text=True)
        except CalledProcessError as e:
            print(f"Error:\n{e.output}")
            raise

        return output.split("\n")

    # def _read_auth_file(self):
    #     auth_path = os.path.join(self.volttron_home, 'auth.json')
    #     try:
    #         with open(auth_path, 'r') as fd:
    #             data = strip_comments(FileObject(fd, close=False).read().decode('utf-8'))
    #             if data:
    #                 auth = jsonapi.loads(data)
    #             else:
    #                 auth = {}
    #     except IOError:
    #         auth = {}
    #     if 'allow' not in auth:
    #         auth['allow'] = []
    #     return auth, auth_path
    #
    # def _append_allow_curve_key(self, publickey, identity):
    #
    #     if identity:
    #         entry = AuthEntry(user_id=identity, identity=identity, credentials=publickey,
    #                           capabilities={'edit_config_store': {'identity': identity}},
    #                           comments="Added by platform wrapper")
    #     else:
    #         entry = AuthEntry(credentials=publickey, comments="Added by platform wrapper. No identity passed")
    #     authfile = AuthFile(self.volttron_home + "/auth.json")
    #     authfile.add(entry, no_error=True)
    #
    # def add_capabilities(self, publickey, capabilities):
    #     with with_os_environ(self.env):
    #         if isinstance(capabilities, str) or isinstance(capabilities, dict):
    #             capabilities = [capabilities]
    #         auth_path = self.volttron_home + "/auth.json"
    #         auth = AuthFile(auth_path)
    #         entry = auth.find_by_credentials(publickey)[0]
    #         caps = entry.capabilities
    #
    #         if isinstance(capabilities, list):
    #             for c in capabilities:
    #                 self.add_capability(c, caps)
    #         else:
    #             self.add_capability(capabilities, caps)
    #         auth.add(entry, overwrite=True)
    #         _log.debug("Updated entry is {}".format(entry))
    #         # Minimum sleep of 2 seconds seem to be needed in order for auth updates to get propagated to peers.
    #         # This slow down is not an issue with file watcher but rather vip.peerlist(). peerlist times out
    #         # when invoked in quick succession. add_capabilities updates auth.json, gets the peerlist and calls all peers'
    #         # auth.update rpc call. So sleeping here instead expecting individual test cases to sleep for long
    #         gevent.sleep(2)
    #
    # @staticmethod
    # def add_capability(entry, capabilites):
    #     if isinstance(entry, str):
    #         if entry not in capabilites:
    #             capabilites[entry] = None
    #     elif isinstance(entry, dict):
    #         capabilites.update(entry)
    #     else:
    #         raise ValueError("Invalid capability {}. Capability should be string or dictionary or list of string"
    #                          "and dictionary.")
    #
    # def set_auth_dict(self, auth_dict):
    #     if auth_dict:
    #         with open(os.path.join(self.volttron_home, 'auth.json'), 'w') as fd:
    #             fd.write(jsonapi.dumps(auth_dict))

    def _setup_testing_environment(self):
        """
        Creates a new testing environment (virtual environment) that the platform will run from. This function
        populates the field self._virtual_env which is the environment that is created.  We use the
        self._platform_environment for the key information for the platform such as VOLTTRON_HOME environment
        and other header information.  This method will also copy the current "agent" pyproject.toml file into the
        home directory and install the entire current project in the environment.  It will update relative packages
        with full paths to the package from the context of the new pyproject.toml.
        """
        print("Creating new test virtual environment")


        self._virtual_env = VirtualEnv(env=self._platform_environment,
                                       name=".venv",
                                       workspace=self._server_options.volttron_home.parent.as_posix(),
                                       delete_workspace=not self.skip_cleanup,
                                       python='/usr/bin/python3')
        self._virtual_env.install_package("poetry", version="1.8.3")

        # Make the volttron_home dir so we can copy to it.
        self._server_options.volttron_home.mkdir(parents=True, exist_ok=True)
        shutil.copy(self._project_toml_file,
                    self._server_options.volttron_home / "pyproject.toml")

        print("Updating new pyproject.toml file with absolute paths.")
        import tomli
        import tomli_w

        toml_obj = tomli.loads((self._server_options.volttron_home / "pyproject.toml").read_text())

        # First change the package name so we can install the package without an error
        toml_obj['tool']['poetry']['name'] = "testing-" + toml_obj['tool']['poetry']['name']

        if 'readme' in toml_obj['tool']['poetry']:
            cwd = os.getcwd()
            os.chdir(self._project_toml_file.parent)
            readme = Path(toml_obj['tool']['poetry']['readme']).resolve().absolute()
            toml_obj['tool']['poetry']['readme'] = readme.as_posix()
            os.chdir(cwd)

        # Make sure we don't change the directory and not change it back for the environment.
        cwd = os.getcwd()
        os.chdir(self._project_toml_file.parent)
        for pkg in toml_obj['tool']['poetry']['packages']:
            print(pkg)
            new_pkg_src = Path(pkg["from"]).absolute().as_posix()
            pkg['from'] = new_pkg_src

        os.chdir(cwd)
        # Make the paths be full paths from the pyproject.toml file that was loaded in the
        # source of running the test.
        for dep, value in toml_obj['tool']['poetry']['dependencies'].items():
            if 'path' in value:
                cwd = os.getcwd()
                os.chdir(self._project_toml_file.parent)
                dep_path = Path(value['path']).resolve().absolute()
                toml_obj['tool']['poetry']['dependencies'][dep]['path'] = dep_path.as_posix()
                os.chdir(cwd)
        has_groups: list[str] = []
        if 'group' in toml_obj['tool']['poetry']:
            for group in toml_obj['tool']['poetry']['group']:
                has_groups.append(group)
                dependencies = toml_obj['tool']['poetry']['group'][group]['dependencies']
                for dep, value in dependencies.items():
                    if 'path' in value:
                        cwd = os.getcwd()
                        os.chdir(self._project_toml_file.parent)
                        dep_path = Path(value['path']).resolve().absolute()
                        dependencies[dep]['path'] = dep_path.as_posix()
                        os.chdir(cwd)
        tomli_w.dump(toml_obj, self._home_toml_file.open("wb"))

        cmd = f"poetry install".split()
        if has_groups:
            cmd.extend(["--with", ",".join(has_groups)])

        try:
            output = self._virtual_env.run(args=cmd, capture=True, cwd=self.volttron_home)
        except CalledProcessError as e:
            print(f"Error:\n{e.output}")
            raise
        self.logit(output)

    def startup_platform(self, timeout:int = 30):
        """
        Start the platform using the options passed to the constructor.
        :param timeout: The amount of time to wait for the platform to start up once popen is called.
        :return:
        """

        def capture_stdout(queue: Queue, process):
            for line in process.stdout:
                sys.stdout.write(line)
                queue.put(line.strip())


        # # Make sure that the python that's executing is on the path.
        # bin_dir = str(Path(sys.executable).parent)
        # path = os.environ['PATH']
        # if bin_dir not in path:
        #     path = bin_dir + ":" + path

        # We want to make sure that we know what pyproject.toml file we are going to use.
        #if not (self._server_options.poetry_project_path / "pyproject.toml").exists():
            # poetry path is in the root of volttron-testing repository.
        #    self._server_options.poetry_project_path = Path(__file__).parent.parent.parent



        # Update OS env to current platform's env so get_home() call will result
        # in correct home director. Without this when more than one test instance are created, get_home()
        # will return home dir of last started platform wrapper instance.
        with with_os_environ(self._platform_environment):

            # Add check and raise error if the platform is already running for this instance.
            if self.is_running():
                raise PlatformWrapperError("Already running platform")

            cmd = [self._volttron_exe, '-vv', "-l", self._log_path]

            from pprint import pprint
            print('process environment: ')
            pprint(self._platform_environment)
            print('popen params: {}'.format(cmd))
            print(f"server options:")
            # noinspection PyTypeChecker
            pprint(asdict(self._server_options))

            print(f"Command is: {cmd}")

            self._platform_process = Popen(cmd,
                                           env=self._platform_environment,
                                           stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT,
                                           universal_newlines=True,
                                           text=True)
            time.sleep(1)

            if self._enable_sys_queue:
                # Set up a background thread to gather queue
                self._stdout_thread = threading.Thread(target=capture_stdout,
                                                       args=(self._stdout_queue, self._platform_process),
                                                       daemon=True)
                self._stdout_thread.start()
                gevent.sleep(0.1)

            # A None value means that the process is still running.
            # A negative means that the process exited with an error.
            #print(self._platform_process.poll())
            assert self._platform_process.poll() is None, f"The start platform failed with command:\n{cmd}\nusing environment:\n{self._platform_environment}"

            try:
                wait_for_volttron_startup(self._server_options.volttron_home, timeout)
            except Exception as ex:
                if self._platform_process.poll() is None:
                    print("Wait is still executing.")
                else:
                    print("Process was dead")
                sys.exit(1)

            if self.is_running():
                self._instance_shutdown = False

            # self.vip_address = vip_address
            # self.mode = mode
            #
            # if perform_preauth_service_agents:
            #     authfile = AuthFile()
            #     if not authfile.read_allow_entries():
            #         # if this is a brand new auth.json
            #         # pre-seed all of the volttron process identities before starting the platform
            #         for identity in PROCESS_IDENTITIES:
            #             if identity == PLATFORM_WEB:
            #                 capabilities = dict(allow_auth_modifications=None)
            #             else:
            #                 capabilities = dict(edit_config_store=dict(identity="/.*/"))
            #
            #             ks = KeyStore(KeyStore.get_agent_keystore_path(identity))
            #             entry = AuthEntry(credentials=encode_key(decode_key(ks.public)),
            #                               user_id=identity,
            #                               identity=identity,
            #                               capabilities=capabilities,
            #                               comments='Added by pre-seeding.')
            #             authfile.add(entry)
            #
            #         # Control connection needs to be added so that vctl can connect easily
            #         identity = CONTROL_CONNECTION
            #         capabilities = dict(edit_config_store=dict(identity="/.*/"))
            #         ks = KeyStore(KeyStore.get_agent_keystore_path(identity))
            #         entry = AuthEntry(credentials=encode_key(decode_key(ks.public)),
            #                           user_id=identity,
            #                           identity=identity,
            #                           capabilities=capabilities,
            #                           comments='Added by pre-seeding.')
            #         authfile.add(entry)
            #
            #         identity = "dynamic_agent"
            #         capabilities = dict(edit_config_store=dict(identity="/.*/"), allow_auth_modifications=None)
            #         # Lets cheat a little because this is a wrapper and add the dynamic agent in here as well
            #         ks = KeyStore(KeyStore.get_agent_keystore_path(identity))
            #         entry = AuthEntry(credentials=encode_key(decode_key(ks.public)),
            #                           user_id=identity,
            #                           identity=identity,
            #                           capabilities=capabilities,
            #                           comments='Added by pre-seeding.')
            #         authfile.add(entry)
            #
            # msgdebug = self.env.get('MSG_DEBUG', False)
            #enable_logging = self.env.get('ENABLE_LOGGING', False)
            #
            # if self.debug_mode:
            #     self.skip_cleanup = True
            #     enable_logging = True
            #     msgdebug = True
            #
            # self.logit("Starting Platform: {}".format(self.volttron_home))
            # assert self.mode in MODES, 'Invalid platform mode set: ' + str(mode)
            # opts = None
            #
            # # see main.py for how we handle pub sub addresses.
            # ipc = 'ipc://{}{}/run/'.format(
            #     '@' if sys.platform.startswith('linux') else '',
            #     self.volttron_home)
            # self.local_vip_address = ipc + 'vip.socket'
            # self.set_auth_dict(auth_dict)
            #
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
            #
            # self.opts.update({
            #     'verify_agents': False,
            #     'vip_address': vip_address,
            #     'volttron_home': self.volttron_home,
            #     'vip_local_address': ipc + 'vip.socket',
            #     'publish_address': ipc + 'publish',
            #     'subscribe_address': ipc + 'subscribe',
            #     'secure_agent_users': self.secure_agent_users,
            #     'platform_name': None,
            #     'log': self.log_path,
            #     'log_config': None,
            #     'monitor': True,
            #     'autostart': True,
            #     'log_level': logging.DEBUG,
            #     'verboseness': logging.DEBUG,
            #     'web_ca_cert': self.requests_ca_bundle
            # })
            #
            # # Add platform's public key to known hosts file
            # publickey = self.keystore.public
            # known_hosts_file = os.path.join(self.volttron_home, 'known_hosts')
            # known_hosts = KnownHostsStore(known_hosts_file)
            # known_hosts.add(self.opts['vip_local_address'], publickey)
            # known_hosts.add(self.opts['vip_address'], publickey)
            #
            # create_platform_config_file(self.messagebus, self.instance_name, self.vip_address, agent_monitor_frequency,
            #                              self.secure_agent_users)
            # if self.ssl_auth:
            #     certsdir = os.path.join(self.volttron_home, 'certificates')
            #
            #     self.certsobj = Certs(certsdir)
            #
            # if self.services:
            #     with Path(self.volttron_home).joinpath("service_config.yml").open('wt') as fp:
            #         yaml.dump(self.services, fp)
            #



            # self.serverkey = self.keystore.public
            # assert self.serverkey
            #
            # # Use dynamic_agent so we can look and see the agent with peerlist.
            # if not setupmode:
            #     gevent.sleep(2)
            #     self.dynamic_agent = self.build_agent(identity="dynamic_agent")
            #     assert self.dynamic_agent is not None
            #     assert isinstance(self.dynamic_agent, Agent)
            #     has_control = False
            #     times = 0
            #     while not has_control and times < 10:
            #         times += 1
            #         try:
            #             has_control = CONTROL in self.dynamic_agent.vip.peerlist().get(timeout=.2)
            #             self.logit("Has control? {}".format(has_control))
            #         except gevent.Timeout:
            #             pass
            #
            #     if not has_control:
            #         self.shutdown_platform()
            #         raise Exception("Couldn't connect to core platform!")

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



    def is_running(self):
        return is_volttron_running(self._server_options.volttron_home)

    def __install_agent_wheel__(self, wheel_file, start, vip_identity):
        with with_os_environ(self._platform_environment):

            self.__wait_for_control_connection_to_exit__()

            self.logit("VOLTTRON_HOME SETTING: {}".format(
                self.env['VOLTTRON_HOME']))

            cmd = f"vctl --json install {wheel_file}".split()
            #cmd = ['volttron-ctl', '--json', 'install', wheel_file]

            if vip_identity:
                cmd.extend(['--vip-identity', vip_identity])

            res = self._virtual_env.run(cmd, capture=True, env=self._platform_environment)
            #res = execute_command(cmd, env=env, logger=_log)
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
                results = self.install_agent(agent_dir=path, config_file=config,
                                             start=start)

        return results

    def install_from_github(self, *, org: str, repo: str, branch: str | None = None) -> AgentUUID:
        """
        Install an agent from a github repository directly.

        org: str: The name of the organization example: eclipse-volttron
        repo: str: The name of the repository example: volttron-listener
        branch: str: The branch that should be checked out to install from.
        """

        repo_path = Path(f"/tmp/{org}-{repo}-{branch}")
        if repo_path.is_dir():
            shutil.rmtree(repo_path)

        repo = Repo.clone_from(f"https://github.com/{org}/{repo}.git", to_path=repo_path)
        if branch:
            repo.git.checkout(branch)
            assert repo.active_branch.name == branch

        self._added_from_github.append(repo_path)

        return self.install_agent(agent_dir=repo_path)



    def install_agent(self, agent_wheel: PathStr = None,
                      agent_dir: PathStr = None,
                      start: bool = None,
                      install_options: DefaultAgentInstallOptions = DefaultAgentInstallOptions) -> AgentUUID:
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

        :param start:
        :param agent_wheel:
        :param agent_dir:
        :param install_options: The options available for installing an agent on the platform.
        :return: AgentUUD: The uuid of the installed agent.
        """
        io = install_options
        if start is not None:
            io.start = start

        with with_os_environ(self._platform_environment):
            _log.debug(f"install_agent called with params\nagent_wheel: {agent_wheel}\nagent_dir: {agent_dir}")
            self.__wait_for_control_connection_to_exit__()
            assert self.is_running(), "Instance must be running to install agent."
            assert agent_wheel or agent_dir, "Invalid agent_wheel or agent_dir."
            assert isinstance(io.startup_time, int), "Startup time should be an integer."

            if agent_wheel:
                # Cast to string until we make everything paths
                if isinstance(agent_wheel, Path):
                    agent_wheel = str(agent_wheel)
                assert not agent_dir
                assert not io.config_file
                assert os.path.exists(agent_wheel)
                wheel_file = agent_wheel
                agent_uuid = self.__install_agent_wheel__(wheel_file, False, io.vip_identity)
                assert agent_uuid

            # Now if the agent_dir is specified.
            temp_config = None
            if agent_dir:
                # Cast to string until we make everything paths
                if isinstance(agent_dir, Path):
                    agent_dir = str(agent_dir.expanduser().absolute())
                assert not agent_wheel
                temp_config = os.path.join(self.volttron_home,
                                           os.path.basename(agent_dir) + "_config_file")
                if isinstance(io.config_file, dict):
                    from os.path import join, basename
                    temp_config = join(self.volttron_home,
                                       basename(agent_dir) + "_config_file")
                    with open(temp_config, "w") as fp:
                        fp.write(jsonapi.dumps(io.config_file))
                    config_file = temp_config

                print(f"Before vctl call {os.environ['PATH']}")
                cmd = f"vctl --json install {agent_dir}".split()

                if io.config_file:
                    cmd.extend(["--agent-config", config_file])

                #cmd = [self.vctl_exe, "--json", "install", agent_dir, "--agent-config", config_file]

                if io.force:
                    cmd.extend(["--force"])
                if io.vip_identity:
                    cmd.extend(["--vip-identity", io.vip_identity])
                # vctl install with start seem to have a auth issue. For now start after install
                if io.start:
                    cmd.extend(["--start"])

                self.logit(f"Command installation is: {cmd}")
                try:
                    output = self._virtual_env.run(args=cmd, env=self._platform_environment, capture=True)
                except CalledProcessError as e:
                    self.logit(e.output)
                    raise

                # stdout = execute_command(cmd, logger=_log, env=self.env,
                #                          err_prefix="Error installing agent")
                self.logit(f"RESPONSE FROM INSTALL IS: {output}")
                # Because we are no longer silencing output from the install, the
                # the results object is now much more verbose.  Our assumption is
                # that the result we are looking for is the only JSON block in
                # the output

                match = re.search(r'^({.*})', output, flags=re.M | re.S)
                if match:
                    results = match.group(0)
                else:
                    raise ValueError(
                        "The results were not found in the command output")
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
            if io.start:
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
        with with_os_environ(self._platform_environment):
            # This happens if we are waiting before the platform actually has started up so we capture
            # it here.

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
                print(peers)
                disconnected = CONTROL_CONNECTION not in peers
                if disconnected:
                    break
                if time.time() - timer_start > timeout:
                    # raise PlatformWrapperError(f"Failed for {CONTROL_CONNECTION} to exit in a timely manner.")
                    # See https://githb.com/VOLTTRON/volttron/issues/2938
                    self.logit("Control connection did not exit")
                    break
                time.sleep(1)
                gevent.sleep(1)
            # See https://githb.com/VOLTTRON/volttron/issues/2938
            # if not disconnected:
            #     raise PlatformWrapperError("Control connection did not stop properly")

    def start_agent(self, agent_uuid):
        with with_os_environ(self._platform_environment):
            self.logit('Starting agent {}'.format(agent_uuid))
            self.logit("VOLTTRON_HOME SETTING: {}".format(
                self._platform_environment['VOLTTRON_HOME']))
            if not self.is_running():
                raise PlatformWrapperError("Instance must be running before starting agent")

            self.__wait_for_control_connection_to_exit__()

            cmd = "vctl --json".split()
            cmd.extend(['start', agent_uuid])
            result = self._virtual_env.run(cmd, capture=True, env=self._platform_environment)
            #result = execute_command(cmd, self.env)

            self.__wait_for_control_connection_to_exit__()

            # Confirm agent running
            cmd = "vctl --json".split()
            cmd.extend(['status', agent_uuid])
            res = self._virtual_env.run(cmd, capture=True, env=self._platform_environment)
            #print(res)
            #result = jsonapi.loads(res)
            #print(result)
            # 776 TODO: Timing issue where check fails
            time.sleep(3)
            self.logit("Subprocess res is {}".format(res))
            assert 'running' in res
            pidpos = res.index('[') + 1
            pidend = res.index(']')
            pid = int(res[pidpos: pidend])

            assert psutil.pid_exists(pid), \
                "The pid associated with agent {} does not exist".format(pid)

            #self.started_agent_pids.append(pid)

            self.__wait_for_control_connection_to_exit__()

            return pid

    def stop_agent(self, agent_uuid):
        with with_os_environ(self._platform_environment):
            # Confirm agent running
            self.__wait_for_control_connection_to_exit__()

            _log.debug("STOPPING AGENT: {}".format(agent_uuid))

            cmd = f"vctl stop {agent_uuid}".split()
            res = self._virtual_env.run(cmd, capture=True, env=self._platform_environment)
            return self.agent_pid(agent_uuid)

    def list_agents(self):
        with with_os_environ(self._platform_environment):
            agent_list = self.dynamic_agent.vip.rpc(CONTROL, 'list_agents').get(timeout=10)
            return agent_list

    def remove_agent(self, agent_uuid):
        """Remove the agent specified by agent_uuid"""
        with with_os_environ(self._platform_environment):
            _log.debug("REMOVING AGENT: {}".format(agent_uuid))
            self.__wait_for_control_connection_to_exit__()

            cmd = ["vctl", "remove", agent_uuid]
            res = self._virtual_env.run(cmd, env=self._platform_process, capture=True)
            pid = None
            try:
                pid = self.agent_pid(agent_uuid)
            except RuntimeError:
                self.logit("Runtime error occurred successfully as it was expected")
            finally:
                if pid is not None:
                    raise RuntimeError(f"Expected runtime error for looking at removed agent. {agent_uuid}")

    def remove_all_agents(self):
        with with_os_environ(self._platform_environment):
            if self._instance_shutdown:
                return
            agent_list = self.dynamic_agent.vip.rpc(CONTROL, 'list_agents').get(timeout=10)
            for agent_props in agent_list:
                self.dynamic_agent.vip.rpc(CONTROL, 'remove_agent', agent_props['uuid']).get(timeout=10)
                time.sleep(0.2)

    def is_agent_running(self, agent_uuid):
        with with_os_environ(self._platform_environment):
            return self.agent_pid(agent_uuid) is not None

    def agent_pid(self, agent_uuid):
        """
        Returns the pid of a running agent or None

        :param agent_uuid:
        :return:
        """
        self.__wait_for_control_connection_to_exit__()
        # Confirm agent running
        cmd = ['vctl', 'status', agent_uuid]
        pid = None
        try:
            res = self._virtual_env.run(cmd, capture=True, env=self._platform_environment)

            try:
                pidpos = res.index('[') + 1
                pidend = res.index(']')
                pid = int(res[pidpos: pidend])
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

    # def confirm_agent_running(self, agent_name, max_retries=5,
    #                           timeout_seconds=2):
    #     running = False
    #     retries = 0
    #     while not running and retries < max_retries:
    #         status = self.test_aip.status_agents()
    #         print("Status", status)
    #         if len(status) > 0:
    #             status_name = status[0][1]
    #             assert status_name == agent_name
    #
    #             assert len(status[0][2]) == 2, 'Unexpected agent status message'
    #             status_agent_status = status[0][2][1]
    #             running = not isinstance(status_agent_status, int)
    #         retries += 1
    #         time.sleep(timeout_seconds)
    #     return running

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
        with with_os_environ(self._platform_environment):
            self.stop_platform()

            # since this is a restart, we don't want to do an update/overwrite of services.
            self.startup_platform()

            gevent.sleep(1)

    def stop_platform(self):
        """
        Stop the platform without cleaning up any agents or context of the
        agent.  This should be paired with restart platform in order to
        maintain the context of the platform.
        :return:
        """
        with with_os_environ(self._platform_environment):
            if not self.is_running():
                return
            cmd = "vctl shutdown --platform".split()
            self._virtual_env.run(cmd, capture=True, env=self._platform_environment)
            # self.dynamic_agent.vip.rpc(CONTROL, "shutdown").get(timeout=20)
            if self._dynamic_agent:
                self._dynamic_agent.core.stop(timeout=5)
            if self._platform_process is not None:
                try:
                    gevent.sleep(0.2)
                    self._platform_process.terminate()
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

    def __remove_environment_directory__(self):
        self.logit('Removing {}'.format(self._server_options.volttron_home.parent))
        shutil.rmtree(Path(self._server_options.volttron_home).parent, ignore_errors=True)

        for d in self._added_from_github:
            print(f"Removing {d}")
            shutil.rmtree(d, ignore_errors=True)

    def cleanup(self):
        if self.is_running():
            raise ValueError("Cannot cleanup until after shutdown.")

        if self._skip_cleanup:
            self.logit("Skipping cleanup")
            return

        self.__remove_environment_directory__()


    def shutdown_platform(self):
        """
        Stop platform here.  First grab a list of all of the agents that are
        running on the platform, then shutdown, then if any of the listed agent
        pids are still running then kill them.
        """

        with with_os_environ(self._platform_environment):
            # Handle cascading calls from multiple levels of fixtures.
            if self._instance_shutdown:
                self.logit(f"Instance already shutdown {self._instance_shutdown}")
                return

            if not self.is_running():
                self.logit(f"Instance is not running.")
                return

            running_pids = []
            if self._dynamic_agent:
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
                self._dynamic_agent = None

            for g in self._built_agent_tasks:
                g.kill()

            if self._platform_process is not None:
                try:
                    gevent.sleep(0.2)
                    self._platform_process.terminate()
                    gevent.sleep(0.2)
                except OSError:
                    self.logit('Platform process was terminated.')
                pid_file = f"{self._server_options.volttron_home.as_posix()}/VOLTTRON_PID"
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
            self._instance_shutdown = True

    def __repr__(self):
        return str(self)

    def __str__(self):
        data = []
        data.append('volttron_home: {}'.format(self.volttron_home))
        return '\n'.join(data)

    def restart_agent(self, agent_uuid: AgentUUID):
        cmd = f"vctl restart {agent_uuid}"
        self._virtual_env.run(cmd, capture=True, env=self._platform_environment)

def mergetree(src, dst, symlinks=False, ignore=None):
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            mergetree(s, d, symlinks, ignore)
        else:
            if not os.path.exists(d) or os.stat(src).st_mtime - os.stat(
                    dst).st_mtime > 1:
                shutil.copy2(s, d)
