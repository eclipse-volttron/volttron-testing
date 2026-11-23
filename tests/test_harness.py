"""volttrontesting.harness: the helpers, the checklist format and the subprocess runner."""
import io
import socket
import subprocess
import sys
import textwrap

import pytest

from volttrontesting.harness import Checklist, compare, dig, free_port, near, port_open, run_harness, wait_for


def test_free_port_is_bindable():
    port = free_port()
    with socket.socket() as s:
        s.bind(('127.0.0.1', port))


def test_port_open_sees_a_listener():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        s.listen()
        port = s.getsockname()[1]
        assert port_open('127.0.0.1', port)
    assert not port_open('127.0.0.1', port)


def test_wait_for_returns_the_truthy_value():
    calls = iter([None, 0, {'x': 1}])
    assert wait_for(lambda: next(calls), timeout=2, interval=0.01) == {'x': 1}


def test_wait_for_treats_exceptions_as_not_yet():
    state = {'n': 0}

    def flaky():
        state['n'] += 1
        if state['n'] < 3:
            raise ConnectionError('not up')
        return 'up'

    assert wait_for(flaky, timeout=2, interval=0.01) == 'up'


def test_wait_for_times_out_with_the_last_value():
    assert wait_for(lambda: [], timeout=0.05, interval=0.01) == []
    assert wait_for(lambda: 1 / 0, timeout=0.05, interval=0.01) is None


def test_wait_for_can_let_exceptions_through():
    with pytest.raises(ZeroDivisionError):
        wait_for(lambda: 1 / 0, timeout=0.05, interval=0.01, ignore=())


def test_near_compare_and_dig():
    assert near('1.0000001', 1) and not near(1.1, 1) and near('on', 'on') and not near(None, 0)
    assert compare({'W': 100.0, 'Hz': 60}, {'W': 100, 'Hz': 60.0}) == (True, '')
    ok, detail = compare({'W': 90}, {'W': 100}, who='DNP3 master')
    assert not ok and detail == "DNP3 master sees {'W': 90}, expected {'W': 100}"
    tree = {'a': [{'b': 2}], 'c': None}
    assert dig(tree, 'a', 0, 'b') == 2 and dig(tree, 'a', 5) is None and dig(tree, 'c', 'd') is None


def test_checklist_lines_and_summary():
    out = io.StringIO()
    checks = Checklist(out=out)
    assert checks.check('proxy launched', True, '1.2s')
    assert not checks.check('value read', False)
    checks.record('telemetry', 'sunspec', 'dnp3', True, 'resource_bridge/x', 'W=100')
    lines = out.getvalue().splitlines()
    assert lines[0] == 'PASS proxy launched  1.2s'
    assert lines[1] == 'FAIL value read'
    assert lines[2] == 'PASS telemetry sunspec -> dnp3    via resource_bridge/x  W=100'
    assert checks.summary() == "2/3 passed; FAILED: ['value read']"
    assert checks.exit_code == 1


def test_checklist_finish_exits_with_the_status():
    checks = Checklist(out=io.StringIO())
    checks.check('only', True)
    with pytest.raises(SystemExit) as exc:
        checks.finish()
    assert exc.value.code == 0 and checks.summary() == '1/1 passed'


def _script(tmp_path, body):
    path = tmp_path / 'harness.py'
    path.write_text(textwrap.dedent(body))
    return path


def test_run_harness_passes_arguments_environment_and_log(tmp_path):
    script = _script(tmp_path, """
        import os, sys
        from volttrontesting.harness import Checklist
        open(os.environ['E2E_LOG'], 'w').write('log line\\n')
        checks = Checklist()
        checks.check('argument', sys.argv[1] == '42')
        checks.check('environment', os.environ.get('MODE') == 'bus')
        checks.finish()
    """)
    result = run_harness(script, 42, env={'MODE': 'bus'}, log_path=tmp_path / 'e2e.log', timeout=60)
    result.assert_ok('2/2 passed')
    assert result.count('PASS ') == 2 and 'log line' not in result.report    # the log tail is only shown on failure


def test_run_harness_report_includes_the_log_on_failure(tmp_path):
    script = _script(tmp_path, """
        import os
        from volttrontesting.harness import Checklist
        open(os.environ['MODBUS_E2E_LOG'], 'w').write('the interesting detail\\n')
        checks = Checklist()
        checks.check('device answered', False)
        checks.finish()
    """)
    result = run_harness(script, log_path=tmp_path / 'e2e.log', log_env='MODBUS_E2E_LOG', timeout=60)
    assert result.returncode == 1 and 'the interesting detail' in result.report
    with pytest.raises(AssertionError, match='the interesting detail'):
        result.assert_ok()


def test_harness_module_needs_only_the_standard_library():
    """A harness imports this right after gevent's monkey patching, so it must not drag in the platform."""
    code = ("import sys; import volttrontesting.harness; "
            "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('volttron', 'gevent', 'grequests', 'zmq')); "
            "print(bad)")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60).stdout.strip()
    assert out == '[]', out
