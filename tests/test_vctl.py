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

import subprocess

import pytest
from volttron.client.known_identities import CONTROL

import volttrontesting.platformwrapper as pw


@pytest.mark.skip(msg="Need to fix in core so that when shutdown happens and we are in a vip message it handles the errors.")
def test_vctl_shutdown(volttron_instance: pw.PlatformWrapper):

    assert volttron_instance.is_running()

    with pw.with_os_environ(volttron_instance.env):
        proc = subprocess.Popen(["vctl", "-vv", "status"], stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE)
        proc.wait()
        out, err = proc.communicate()
        print(f"out: {out}")
        print(f"err: {err}")

        proc = subprocess.Popen(["vctl", "-vv", "shutdown", "--platform"], stderr=subprocess.PIPE,
                                stdout=subprocess.PIPE)
        proc.wait()
        out, err = proc.communicate()
        print(f"out: {out}")
        print(f"err: {err}")

    # A None value means that the process is still running.
    # A negative means that the process exited with an error.
    assert volttron_instance.p_process.poll() is not None

#    response = volttron_instance.dynamic_agent.vip.rpc.call(CONTROL, "stop_platform").get(timeout=1)

#    print(f"Response: {response}")
