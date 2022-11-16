import subprocess

from volttron.client.known_identities import CONTROL

import volttrontesting.platformwrapper as pw


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
