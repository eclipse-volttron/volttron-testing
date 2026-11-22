# -*- coding: utf-8 -*- {{{
# ===----------------------------------------------------------------------===
#
#                 Installable Component of Eclipse VOLTTRON
#
# ===----------------------------------------------------------------------===
#
# Copyright 2026 Battelle Memorial Institute
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
"""The pytest plugin: loading, markers, skip switch, scope option, options override, message-bus selection.

Inner pytest sessions run in a subprocess with a fake PlatformCLI, so these tests start no platform; one test at the
end starts a real one through the fixture.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = str(Path(__file__).resolve().parents[1] / "src")

FAKE_PLATFORM = '''
import os
from pathlib import Path
from volttrontesting import platform_cli

STARTS = Path(os.environ["FAKE_PLATFORM_LOG"])

def _start(self, timeout=480.0):
    with STARTS.open("a") as f:
        f.write(f"{self.instance_name} {self.message_bus} web={self.web is not None}\\n")
    return self

platform_cli.PlatformCLI.start = _start
platform_cli.PlatformCLI.shutdown = lambda self: None
platform_cli.PlatformCLI.missing = classmethod(lambda cls, **kw: [])
platform_cli.PlatformCLI.add_web_user = lambda self, *a, **kw: None
'''


@pytest.fixture
def inner(pytester, monkeypatch, tmp_path):
    """Run a pytest session in a subprocess with the plugin and a fake PlatformCLI; return (result, starts)."""
    log = tmp_path / "starts.log"
    monkeypatch.setenv("FAKE_PLATFORM_LOG", str(log))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([SRC, os.environ.get("PYTHONPATH", "")]))
    monkeypatch.delenv("VOLTTRON_TESTING_SKIP_PLATFORM", raising=False)
    monkeypatch.delenv("VOLTTRON_TEST_MESSAGEBUS", raising=False)

    def run(files: dict, *args, conftest: str = ""):
        pytester.makeconftest(FAKE_PLATFORM + textwrap.dedent(conftest))
        pytester.makepyfile(**{name: textwrap.dedent(body) for name, body in files.items()})
        result = pytester.runpytest_subprocess("-p", "volttrontesting.plugin", "-p", "no:cacheprovider", "-rs", *args)
        starts = log.read_text().splitlines() if log.exists() else []
        return result, starts
    return run


TWO_MODULES = {
    "test_a": "def test_a(platform_cli):\n    assert platform_cli.instance_name\n",
    "test_b": "def test_b(platform_cli):\n    assert platform_cli.instance_name\n",
}


def test_loading_the_plugin_imports_nothing_heavy():
    code = ("import sys, volttrontesting.plugin, volttrontesting.fixtures.params; "
            "print([m for m in ('volttrontesting.platformwrapper', 'gevent', 'volttron.server') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": SRC})
    assert out.stdout.strip() == "[]", out.stderr


def test_markers_are_registered(pytestconfig):
    markers = " ".join(pytestconfig.getini("markers"))
    assert "platform:" in markers and "docker:" in markers and "slow:" in markers


def test_platform_fixtures_are_module_scoped_by_default(inner):
    result, starts = inner(TWO_MODULES)
    result.assert_outcomes(passed=2)
    assert len(starts) == 2


@pytest.mark.parametrize("how", ["ini", "option"])
def test_the_scope_can_be_widened_to_the_session(inner, pytester, how):
    if how == "ini":
        pytester.makeini("[pytest]\nvolttron_platform_scope = session\n")
        result, starts = inner(TWO_MODULES)
    else:
        result, starts = inner(TWO_MODULES, "--volttron-platform-scope=session")
    result.assert_outcomes(passed=2)
    assert len(starts) == 1


def test_a_bad_scope_is_a_usage_error(inner, pytester):
    pytester.makeini("[pytest]\nvolttron_platform_scope = forever\n")
    result, _ = inner(TWO_MODULES)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*volttron_platform_scope must be one of*"])


def test_the_skip_switch_skips_platform_tests(inner, monkeypatch):
    monkeypatch.setenv("VOLTTRON_TESTING_SKIP_PLATFORM", "1")
    result, starts = inner(TWO_MODULES)
    result.assert_outcomes(skipped=2)
    result.stdout.fnmatch_lines(["*VOLTTRON_TESTING_SKIP_PLATFORM is set*"])
    assert starts == []


def test_options_fixtures_configure_the_platforms(inner):
    conftest = '''
    import pytest
    @pytest.fixture(scope="session")
    def platform_cli_options():
        return dict(instance_name="from-options")
    @pytest.fixture(scope="session")
    def web_platform_options():
        return dict(instance_name="web-from-options")
    '''
    files = {"test_opts": "def test_both(platform_cli, web_platform):\n    assert web_platform.web_user.username\n"}
    result, starts = inner(files, conftest=conftest)
    result.assert_outcomes(passed=1)
    assert sorted(starts) == ["from-options zmq web=False", "web-from-options zmq web=True"]


def test_platform_tests_are_marked_and_can_be_deselected(inner):
    files = {"test_mixed": "def test_plain():\n    pass\n\ndef test_platform(platform_cli):\n    pass\n"}
    result, starts = inner(files, "-m", "not platform")
    result.assert_outcomes(passed=1, deselected=1)
    assert starts == []


def test_each_selected_bus_gets_a_parameter(inner):
    files = {"test_ids": "def test_bus(platform_cli, request):\n    print('ID', request.node.callspec.id)\n"}
    result, starts = inner(files, "-s")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(["*ID zmq-*"])


def test_an_unavailable_bus_skips_with_the_reason(inner):
    result, starts = inner({"test_x": "def test_x(platform_cli):\n    pass\n"}, "--messagebus=carrier-pigeon")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*carrier-pigeon*"])
    assert starts == []


def test_platform_cli_fixture_starts_a_real_platform(platform_cli):
    assert platform_cli.process is not None
    platform_cli.vctl("status")


# ------------------------------------------------------------------------------------- the platform fixture (step 7)
BACKEND_TESTS = {
    "test_backends": '''
        import pytest

        def test_unmarked(platform):
            assert platform.backend == "testserver"

        @pytest.mark.platform_backend("cli", "testserver")
        def test_both(platform):
            assert platform.backend in ("cli", "testserver")

        @pytest.mark.platform_backend("cli")
        def test_cli_only(platform):
            assert platform.backend == "cli"

        @pytest.mark.platform_backend("cli")
        def test_cli_cannot_subscribe(platform):
            platform.subscribe("x")
        ''',
}


def _ids(result) -> set:
    return {line.split("::")[1].split(" ")[0] for line in result.outlines if "::" in line
            and (" PASSED" in line or " SKIPPED" in line or " FAILED" in line)}


def test_each_test_runs_on_its_own_backends(inner):
    result, starts = inner(BACKEND_TESTS, "-v")
    assert _ids(result) == {"test_unmarked[testserver]", "test_both[cli-zmq-libauth]", "test_both[testserver]",
                            "test_cli_only[cli-zmq-libauth]", "test_cli_cannot_subscribe[cli-zmq-libauth]"}
    result.assert_outcomes(passed=4, skipped=1)               # NotSupported becomes a skip
    result.stdout.fnmatch_lines(["*subscribe is not supported on the cli backend*"])
    assert len(starts) == 1                                     # one module-scoped real platform


def test_the_option_narrows_but_never_moves_a_test(inner):
    result, starts = inner(BACKEND_TESTS, "-v", "--platform-backend=testserver")
    assert _ids(result) == {"test_unmarked[testserver]", "test_both[testserver]", "test_cli_only[no-backend]",
                            "test_cli_cannot_subscribe[no-backend]"}
    result.assert_outcomes(passed=2, skipped=2)
    result.stdout.fnmatch_lines(["*marked for cli; --platform-backend selects testserver*"])
    assert starts == []


def test_the_option_sets_the_backends_of_unmarked_tests(inner):
    result, _starts = inner({"test_u": "def test_u(platform):\n    assert platform.backend == 'cli'\n"},
                            "-v", "--platform-backend=cli")
    result.assert_outcomes(passed=1)


def test_an_unknown_backend_is_a_usage_error(inner):
    result, _starts = inner({"test_u": "def test_u(platform):\n    pass\n"}, "--platform-backend=docker")
    result.stderr.fnmatch_lines(["*unknown backend(s) docker*"])


def test_a_repositorys_own_platform_fixture_is_left_alone(inner):
    result, _starts = inner({"test_own": '''
        import pytest

        @pytest.fixture
        def platform():
            return object()

        def test_one(platform):
            pass

        def test_two(platform):
            pass
        '''}, "-v")
    result.assert_outcomes(passed=2)
    result.stdout.fnmatch_lines(["*test_one PASSED*", "*test_two PASSED*"])      # no [testserver] id
