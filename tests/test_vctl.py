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
import copy
import subprocess
import tempfile
from pathlib import Path
from typing import List

import gevent
import pytest
import yaml
from volttron.utils import execute_command
from volttron.utils import jsonapi

from volttrontesting.platformwrapper import PlatformWrapper, with_os_environ

test_agent_dir = None


@pytest.fixture(scope="module", autouse=True)
def create_test_agent(request, volttron_instance):
    global test_agent_dir
    clone_dir = Path(tempfile.mkdtemp(prefix="vtest_tmp_"))
    test_agent_src_dir = clone_dir.joinpath("testagent/src/testagent")
    test_agent_src_dir.mkdir(parents=True)
    test_agent_dir = test_agent_src_dir.parent.parent
    test_agent_src = test_agent_src_dir.joinpath("agent.py")
    test_agent_src.write_text("""
from volttron.utils.commands import vip_main
from volttron.client.vip.agent import Agent, Core
from volttron.client.messaging.health import STATUS_GOOD

class TestAgent(Agent):
    def __init__(self, config_path, **kwargs):
        super().__init__(**kwargs)
        self.config_path = config_path

    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        self.vip.health.set_status(STATUS_GOOD, "status good")

def main():
    try:
        vip_main(TestAgent, version=0.1)
    except Exception as e:
        print(f"unhandled exception {e}")
    """)
    test_agent_toml = test_agent_dir.joinpath("pyproject.toml")
    test_agent_toml.write_text("""
[tool.poetry]
name = "testagent"
version = "0.1.0"
description = "test agent to test vctl commands"
authors = ["VOLTTRON Team"]

[tool.poetry.dependencies]
python = ">=3.8,<4.0"
volttron = ">=10.0.2rc0,<11.0"

[tool.poetry.scripts]
test-agent = "testagent.agent:main"
""")
    print(f"Created test agent dir at {test_agent_dir}")
    execute_command(["poetry", "build"], cwd=test_agent_dir, env=volttron_instance.env)
    yield test_agent_dir
    import shutil
    print(f"removing test agent at {test_agent_dir}")
    shutil.rmtree(test_agent_dir.parent)


# @pytest.mark.skip(msg="Need to fix in core so that when shutdown happens and we are in a vip message it handles the errors.")
def test_vctl_shutdown(volttron_instance: PlatformWrapper):
    assert volttron_instance.is_running()

    with with_os_environ(volttron_instance.env):
        proc = subprocess.Popen(["vctl", "status"], stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE)
        out, err = proc.communicate()
        assert err.strip() == b"No installed Agents found"
        result = execute_command(["vctl", "status"])

        volttron_instance.stop_platform()
        with pytest.raises(RuntimeError,
                           match='VOLTTRON is not running. This command requires VOLTTRON platform to be running'):
            execute_command(["vctl", "status"])
        gevent.sleep(1)
        volttron_instance.restart_platform()


@pytest.mark.control
def test_peerlist_no_connection(volttron_instance: PlatformWrapper):
    # Test command that needs instance running
    with with_os_environ(volttron_instance.env):
        volttron_instance.stop_platform()
        with pytest.raises(RuntimeError,
                           match='VOLTTRON is not running. This command requires VOLTTRON platform to be running'):
            execute_command(["volttron-ctl", "peerlist"], env=volttron_instance.env)
        volttron_instance.restart_platform()


@pytest.mark.control
def test_peerlist_with_connection(volttron_instance: PlatformWrapper):
    # Verify peerlist command works when instance is running
    with with_os_environ(volttron_instance.env):
        assert volttron_instance.is_running()
        response = execute_command(["volttron-ctl", "peerlist"], volttron_instance.env)
        peers = set(response.split())
        expected_peers = {'control.connection', 'dynamic_agent', 'platform.auth', 'platform.config_store',
                          'platform.control', 'platform.health'}
        assert peers == expected_peers


@pytest.mark.skip(reason="fix vctl list")
@pytest.mark.control
def test_no_connection():
    # Test command that doesn't need instance running.
    wrapper = PlatformWrapper(ssl_auth=False)
    p = subprocess.Popen(
        ["volttron-ctl", "list"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=wrapper.env
    )
    stdout, stderr = p.communicate()

    try:
        assert "No installed Agents found" in stderr.decode("utf-8")
    except AssertionError:
        assert not stderr.decode("utf-8")


@pytest.mark.control
def test_install_same_identity(volttron_instance: PlatformWrapper):
    global test_agent_dir
    with with_os_environ(volttron_instance.env):
        expected_identity = "testagent"
        args = [
            "volttron-ctl",
            "--json",
            "install",
            test_agent_dir,
            "--vip-identity",
            expected_identity,
            "--start",
        ]
        response = execute_command(args, volttron_instance.env)
        json_response = jsonapi.loads(response)
        agent_uuid = json_response["agent_uuid"]
        response = execute_command(
            ["vctl", "--json", "status", agent_uuid], volttron_instance.env
        )
        json_response = jsonapi.loads(response)
        identity = list(json_response.keys())[0]
        agent_status_dict = json_response[identity]
        assert "running [" in agent_status_dict.get("status")

        expected_status = agent_status_dict.get("status")
        expected_auuid = agent_status_dict.get("agent_uuid")

        # Attempt to install without force.
        with pytest.raises(RuntimeError):
            execute_command(args, volttron_instance.env)

        # Nothing should have changed the pid should be the same
        response = execute_command(
            ["vctl", "--json", "status", agent_uuid], volttron_instance.env
        )
        json_response = jsonapi.loads(response)
        identity = list(json_response.keys())[0]
        agent_status_dict = json_response[identity]
        assert expected_status == agent_status_dict.get("status")
        assert expected_auuid == agent_status_dict.get("agent_uuid")

        args = [
            "volttron-ctl",
            "--json",
            "install",
            test_agent_dir,
            "--vip-identity",
            expected_identity,
            "--start",
            "--force",
        ]

        # Install with force.
        response = execute_command(args, volttron_instance.env)
        json_response = jsonapi.loads(response)
        agent_uuid = json_response["agent_uuid"]
        print(f"json response after force install with --start {json_response}")
        assert json_response["starting"]
        gevent.sleep(1)
        response = execute_command(
            ["vctl", "--json", "status", agent_uuid], volttron_instance.env
        )
        json_response = jsonapi.loads(response)
        identity = list(json_response.keys())[0]
        agent_status_dict = json_response[identity]
        print(f"agent status dict after reinstall is {agent_status_dict}")
        assert "running [" in agent_status_dict.get("status")
        assert expected_status != agent_status_dict.get("status")
        assert expected_auuid != agent_status_dict.get("agent_uuid")

        volttron_instance.remove_all_agents()


@pytest.mark.control
def test_install_with_wheel(volttron_instance: PlatformWrapper):
    global test_agent_dir
    with with_os_environ(volttron_instance.env):
        whl_file = test_agent_dir.joinpath("dist/testagent-0.1.0-py3-none-any.whl")
        args = ["volttron-ctl", "--json", "install", whl_file]
        response = execute_command(args, volttron_instance.env)
        response_dict = jsonapi.loads(response)
        assert response_dict.get("agent_uuid")
        volttron_instance.remove_all_agents()


@pytest.mark.control
def test_install_with_wheel_bad_path(volttron_instance: PlatformWrapper):
    with with_os_environ(volttron_instance.env):
        bad_wheel_path = "foo/wheel.whl"
        args = ["volttron-ctl", "--json", "install", bad_wheel_path]
        try:
            response = execute_command(args, volttron_instance.env)
        except RuntimeError as exc:
            assert f"Invalid wheel file {bad_wheel_path}" in exc.args[0]


@pytest.mark.control
@pytest.mark.parametrize(
    "args",
    (
        ["--tag", "brewster", "--priority", "1"],
        ["--tag", "brewster", "--start", "--priority", "1"],
        ["--tag", "enabled_agent", "--enable"],
        ["--tag", "autostart_agent", "--priority", "10"],
        [],
        ["--vip-identity", "ralph"]
    )
)
@pytest.mark.parametrize("use_config", [False, True])
def test_install_arg_matrix(
        volttron_instance: PlatformWrapper, args: List, use_config: bool
):
    global test_agent_dir
    with with_os_environ(volttron_instance.env):
        # Don't change the parametrized args that have mutable values. Make copy if changing within test.
        # parameterized args when used with more than 1 .parametrize() or with another parameterized fixture
        # fails to rest values correctly
        # @pytest.mark.parametrize("x,y", (([1, 2], 1), ([3, 4], 1))) - will work fine even if x is changed in test
        # But
        # @pytest.mark.parametrize("x,y", (([1,2],1), ([3,4],1)))
        # @pytest.mark.parametrize("z", [8, 9])
        # will fail to reset value of x correctly if x is changed within test

        vctl_args = copy.copy(args)
        # parameterization happens before calling any agent fixtures, so global variables if passed to pytest parameters
        # will only take any default value set at declaration. We set value of test_agent_dir in create_agent_dir
        # fixture which is a module level autouse fixture. so we use test_agent_dir inside the test function instead
        # of passing it as parameter value
        vctl_cmd = ["vctl", "install", test_agent_dir, "--json"]
        config_str = '{"message": "hello"}'
        if use_config:
            config_file = test_agent_dir.joinpath("config")
            config_file.write_text(config_str)
            vctl_args.extend(["--agent-config", config_file])
        vctl_cmd.extend(vctl_args)
        try:
            response = execute_command(vctl_cmd, volttron_instance.env)
        except BaseException as e:
            print(f"Base exception {e}")
            assert False

        json_response = jsonapi.loads(response)

        agent_uuid = json_response["agent_uuid"]
        gevent.sleep(1)

        response = execute_command(
            ["vctl", "--json", "status", agent_uuid], volttron_instance.env
        )
        json_response = jsonapi.loads(response)

        identity = list(json_response.keys())[0]
        agent_status_dict = json_response[identity]

        if "--start" in vctl_args:
            assert agent_status_dict["status"]
            assert "running [" in agent_status_dict["status"]
            assert agent_status_dict["health"]

        if "--tag" in vctl_args:
            assert agent_status_dict["agent_tag"]
            tag_name = vctl_args[vctl_args.index("--tag") + 1]
            assert tag_name == agent_status_dict["agent_tag"]

        assert agent_status_dict["identity"]
        if "--vip-identity" in vctl_args:
            expected_identity = vctl_args[vctl_args.index("--vip-identity") + 1]
            assert expected_identity == agent_status_dict["identity"]

        if use_config:
            expected_config = yaml.safe_load(config_str)
            config_path = Path(volttron_instance.volttron_home).joinpath(
                f"agents/{agent_status_dict['identity']}/config"
            )
            with open(config_path) as fp:
                config_data = yaml.safe_load(fp.read())
                assert expected_config == config_data

        if "--enable" in vctl_args or "--priority" in vctl_args:
            priority_path = Path(volttron_instance.volttron_home).joinpath(
                f"agents/{agent_status_dict['identity']}/AUTOSTART"
            )
            priority_value = 50
            if "--priority" in vctl_args:
                priority_value = vctl_args[vctl_args.index("--priority") + 1]
            assert priority_path.read_text().strip() == str(priority_value)
            volttron_instance.restart_platform()
            gevent.sleep(1)
            response = execute_command(["vctl", "--json", "status", agent_uuid], volttron_instance.env)
            json_response = jsonapi.loads(response)
            identity = list(json_response.keys())[0]
            agent_status_dict = json_response[identity]
            assert agent_status_dict["status"]
            assert "running [" in agent_status_dict["status"]
            assert agent_status_dict["health"]

        volttron_instance.remove_all_agents()


@pytest.mark.skip(message="issue #146. vctl list fails")
@pytest.mark.control
def test_agent_filters(volttron_instance):
    global test_agent_dir
    auuid = volttron_instance.install_agent(
        agent_dir=test_agent_dir, start=True
    )
    buuid = volttron_instance.install_agent(
        agent_dir=test_agent_dir, start=True
    )

    # Verify all installed agents show up in list
    with with_os_environ(volttron_instance.env):
        agent_list = execute_command(["volttron-ctl", "list"], env=volttron_instance.env)
        assert "testagent-0.1.0_1" in str(agent_list)
        assert "testagent-0.1.0_2" in str(agent_list)

    # Filter agent based on agent uuid
    with with_os_environ(volttron_instance.env):
        agent_list = execute_command(["volttron-ctl", "list", str(auuid)], env=volttron_instance.env)
        assert "testagent-0.1.0_1" in str(agent_list)
        assert "testagent-0.1.0_2" not in str(agent_list)

    # Filter agent based on agent name
    with with_os_environ(volttron_instance.env):
        agent_list = execute_command(["volttron-ctl", "list", "testagent-0.1.0_1"], env=volttron_instance.env)
        assert "testagent-0.1.0_1" in str(agent_list)
        assert "testagent-0.1.0_2" not in str(agent_list)

    volttron_instance.remove_all_agents()


@pytest.mark.control
def test_vctl_start_stop_restart_by_uuid_should_succeed(volttron_instance: PlatformWrapper):
    global test_agent_dir
    with with_os_environ(volttron_instance.env):
        identity = "test_agent"
        install_test_agent = [
            "volttron-ctl",
            "--json",
            "install",
            test_agent_dir,
            "--vip-identity",
            identity
        ]
        # install agent
        agent_uuid = jsonapi.loads(execute_command(install_test_agent, volttron_instance.env))['agent_uuid']

        # check that agent has not been started
        check_agent_status = ["vctl", "--json", "status", agent_uuid]
        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        assert not agent_status[identity]['health']
        assert not agent_status[identity]['status']

        # start agent
        start_agent_by_uuid = ["vctl", "start", agent_uuid]
        execute_command(start_agent_by_uuid, volttron_instance.env)

        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        assert agent_status[identity]['health']['message'] == 'GOOD'
        assert 'running' in agent_status[identity]['status']

        # stop agent
        stop_tagged_agent = ["vctl", "stop", agent_uuid]
        execute_command(stop_tagged_agent, volttron_instance.env)

        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        assert not agent_status[identity]['health']
        assert not int(agent_status[identity]['status'])  # status is a '0' when agent is stopped

        # restart agent
        # start the agent first so that restart agent will go through the entire flow of stopping and starting an agent
        execute_command(start_agent_by_uuid, volttron_instance.env)
        restart_tagged_agent = ["vctl", "restart", agent_uuid]
        execute_command(restart_tagged_agent, volttron_instance.env)

        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        assert agent_status[identity]['health']['message'] == 'GOOD'
        assert 'running' in agent_status[identity]['status']

        volttron_instance.remove_all_agents()


@pytest.mark.control
def test_vctl_start_stop_restart_by_tag_should_succeed(volttron_instance: PlatformWrapper):
    global test_agent_dir
    with with_os_environ(volttron_instance.env):
        identity = "testagent"
        tag_name = "testagent"
        install_testagent = [
            "volttron-ctl",
            "--json",
            "install",
            test_agent_dir,
            "--vip-identity",
            identity,
            "--tag",
            tag_name
        ]
        # install tagged agent
        agent_uuid = jsonapi.loads(execute_command(install_testagent, volttron_instance.env))['agent_uuid']
        # check that agent have not been started
        check_agent_status = ["vctl", "--json", "status", agent_uuid]
        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        print(agent_status)
        assert not agent_status[identity]['health']
        assert not agent_status[identity]['status']

        # start tagged agent
        start_tagged_agent = ["vctl", "start", "--tag", tag_name]
        execute_command(start_tagged_agent, volttron_instance.env)

        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        assert agent_status[identity]['health']['message'] == 'GOOD'
        assert 'running' in agent_status[identity]['status']

        # stop tagged agent
        stop_tagged_agent = ["vctl", "stop", "--tag", tag_name]
        execute_command(stop_tagged_agent, volttron_instance.env)

        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        assert not agent_status[identity]['health']
        assert not int(agent_status[identity]['status'])  # status is a '0' when agent is stopped

        # restart tagged agent
        # start the agent first so that restart agent will go through the entire flow of stopping and starting an agent
        execute_command(start_tagged_agent, volttron_instance.env)
        restart_tagged_agent = ["vctl", "restart", "--tag", tag_name]
        execute_command(restart_tagged_agent, volttron_instance.env)

        agent_status = jsonapi.loads(execute_command(check_agent_status, volttron_instance.env))
        assert agent_status[identity]['health']['message'] == 'GOOD'
        assert 'running' in agent_status[identity]['status']

        volttron_instance.remove_all_agents()


@pytest.mark.skip(message="issue #150")
@pytest.mark.control
def test_vctl_start_stop_restart_by_all_tagged_should_succeed(volttron_instance: PlatformWrapper):
    global test_agent_dir
    with with_os_environ(volttron_instance.env):
        identity_tag = "test_tag"
        identity_tag2 = "test_tag2"
        identity_no_tag = "test_no_tag"
        tag_name = "test_agent"
        install_tagged_agent = [
            "volttron-ctl",
            "--json",
            "install",
            test_agent_dir,
            "--vip-identity",
            identity_tag,
            "--tag",
            tag_name
        ]
        install_tagged_agent2 = [
            "volttron-ctl",
            "--json",
            "install",
            test_agent_dir,
            "--vip-identity",
            identity_tag2,
            "--tag",
            tag_name
        ]
        install_agent_no_tag = [
            "volttron-ctl",
            "--json",
            "install",
            test_agent_dir,
            "--vip-identity",
            identity_no_tag
        ]

        # install two tagged agents, one untagged agent
        jsonapi.loads(execute_command(install_tagged_agent, volttron_instance.env))
        jsonapi.loads(execute_command(install_tagged_agent2, volttron_instance.env))
        jsonapi.loads(execute_command(install_agent_no_tag, volttron_instance.env))

        check_all_status = ["vctl", "--json", "status"]

        # check that all three agents were installed and were not started
        status = jsonapi.loads(execute_command(check_all_status, volttron_instance.env))
        assert len(status) == 3
        for agent_info in status.values():
            assert not agent_info['health']
            assert not agent_info['status']

        # start all tagged
        start_all_tagged = ["vctl", "start", "--all-tagged"]
        execute_command(start_all_tagged, volttron_instance.env)

        # check that only tagged agents were started
        status = jsonapi.loads(execute_command(check_all_status, volttron_instance.env))

        assert status[identity_tag]['health']
        assert 'running' in status[identity_tag]['status']

        assert status[identity_tag2]['health']
        assert 'running' in status[identity_tag2]['status']

        assert not status[identity_no_tag]['health']
        assert not status[identity_no_tag]['status']

        # stop all tagged
        stop_all_tagged = ["vctl", "stop", "--all-tagged"]
        execute_command(stop_all_tagged, volttron_instance.env)

        # check that all agents were stopped
        status = jsonapi.loads(execute_command(check_all_status, volttron_instance.env))

        assert not status[identity_tag]['health']
        assert not int(status[identity_tag]['status'])  # status is a '0' when agent is started and then stopped

        assert not status[identity_tag2]['health']
        assert not int(status[identity_tag2]['status'])  # status is a '0' when agent is started and then stopped

        assert not status[identity_no_tag]['health']
        assert not status[identity_no_tag]['status']

        # restart all tagged
        # start all tagged agents first so that restart agent will go through the entire flow of stopping and
        # starting an agent
        execute_command(start_all_tagged, volttron_instance.env)
        restart_all_tagged = ["vctl", "restart", "--all-tagged"]
        execute_command(restart_all_tagged, volttron_instance.env)

        # check that only tagged agents were restarted
        status = jsonapi.loads(execute_command(check_all_status, volttron_instance.env))

        assert status[identity_tag]['health']
        assert 'running' in status[identity_tag]['status']

        assert status[identity_tag2]['health']
        assert 'running' in status[identity_tag2]['status']

        assert not status[identity_no_tag]['health']
        assert not status[identity_no_tag]['status']

        volttron_instance.remove_all_agents()


@pytest.mark.parametrize("subcommand, invalid_option", [
    #("start", "--all-taggeD"), ("stop", "--all-taggeD"), ("restart", "--all-taggeD"),
    ("start", "--all"), ("stop", "--all"), ("restart", "--all")
]
                         )
def test_vctl_start_stop_restart_should_raise_error_on_invalid_options(volttron_instance: PlatformWrapper, subcommand,
                                                                       invalid_option):
    with with_os_environ(volttron_instance.env):
        with pytest.raises(RuntimeError):
            execute_command(["vctl", subcommand, invalid_option], volttron_instance.env)


@pytest.mark.skip(message="issue #150")
@pytest.mark.parametrize("subcommand, valid_option",
                         [("start", "--all-tagged"), ("stop", "--all-tagged"), ("restart", "--all-tagged")])
def test_vctl_start_stop_restart_all_tagged_when_no_agents_are_installed(volttron_instance: PlatformWrapper,
                                                                                 subcommand, valid_option):
    with with_os_environ(volttron_instance.env):
        execute_command(["vctl", subcommand, valid_option], volttron_instance.env)
        assert not jsonapi.loads(execute_command(["vctl", "--json", "status"], volttron_instance.env))
