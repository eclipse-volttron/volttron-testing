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
"""Helpers for end-to-end harnesses and the pytest tests that run them.

A *harness* is a script that exercises real components (interfaces, proxies, simulators, a platform) and reports one
``PASS``/``FAIL`` line per check, then a ``N/M passed`` summary, and exits non-zero if anything failed. Harnesses run
in their own process because gevent's monkey patching has to happen before anything else is imported, which a pytest
process cannot guarantee; :func:`run_harness` is the runner the tests use.

This module imports nothing beyond the standard library, so a harness can import it right after
``monkey.patch_all()``. Its sleeps go through ``time.sleep``, which is cooperative once gevent has patched it.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = ["Checklist", "HarnessResult", "run_harness", "free_port", "port_open", "wait_for", "near", "compare", "dig"]


# ---------------------------------------------------------------------------- small helpers
def free_port(host: str = "127.0.0.1") -> int:
    """A TCP port that was free on ``host`` a moment ago (the usual bind-to-zero race applies)."""
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    """True if something accepts TCP connections on ``host:port``."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def wait_for(predicate: Callable[[], Any], timeout: float = 5.0, interval: float = 0.05, *,
             ignore: tuple[type[BaseException], ...] = (Exception,)) -> Any:
    """Poll ``predicate`` until it returns something truthy or ``timeout`` seconds pass.

    Returns the truthy value, or the result of one last call after the deadline (falsy, or ``None`` if that call
    raised). Exceptions listed in ``ignore`` count as "not yet", so a predicate may touch a service that is still
    starting; pass ``ignore=()`` to let them propagate.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = predicate()
        except ignore:
            value = None
        if value:
            return value
        time.sleep(interval)
    try:
        return predicate()
    except ignore:
        return None


def near(a: Any, b: Any, tol: float = 1e-6) -> bool:
    """Numeric equality within ``tol`` when both sides convert to float, plain equality otherwise."""
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


def compare(view: dict, expected: dict, tol: float = 1e-6, who: str = "external party") -> tuple[bool, str]:
    """Check every expected key against ``view`` with :func:`near`. Returns ``(ok, detail)`` for a check line."""
    bad = {k: view.get(k) for k, v in expected.items() if not near(view.get(k), v, tol)}
    return not bad, ("" if not bad else f"{who} sees {bad}, expected {expected}")


def dig(d: Any, *path: Any) -> Any:
    """Follow dict keys and list indices; ``None`` as soon as a step does not exist."""
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and p < len(d):
            d = d[p]
        else:
            return None
    return d


# ---------------------------------------------------------------------------- checklist
@dataclass
class Checklist:
    """The PASS/FAIL list a harness prints, in the format the runner tests parse.

    ``check(name, cond, detail)`` prints ``PASS name  detail`` or ``FAIL name  detail``. ``record(kind, src, dst, ok,
    path, detail)`` is the matrix form, ``PASS telemetry  sunspec -> dnp3    via ...``, where ``kind src>dst`` names
    the entry in the summary. ``finish()`` prints ``N/M passed`` (and the failed names) and exits with 1 if anything
    failed.
    """
    out: Any = None
    entries: list[tuple[str, bool]] = field(default_factory=list)

    def _print(self, line: str) -> None:
        print(line, file=self.out or sys.stdout, flush=True)

    def check(self, name: str, cond: Any, detail: str = "") -> bool:
        ok = bool(cond)
        self.entries.append((name, ok))
        self._print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""))
        return ok

    def record(self, kind: str, src: str, dst: str, ok: Any, path: str, detail: str = "") -> bool:
        ok = bool(ok)
        self.entries.append((f"{kind} {src}>{dst}", ok))
        self._print(f'{"PASS" if ok else "FAIL"} {kind:9} {src:>7} -> {dst:<7} via {path}  {detail}')
        return ok

    @property
    def failed(self) -> list[str]:
        return [name for name, ok in self.entries if not ok]

    @property
    def passed(self) -> int:
        return len(self.entries) - len(self.failed)

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0

    def summary(self) -> str:
        failed = self.failed
        return f"{self.passed}/{len(self.entries)} passed" + (f"; FAILED: {failed}" if failed else "")

    def finish(self) -> None:
        """Print the summary and exit the harness process with :attr:`exit_code`."""
        self._print("\n" + self.summary())
        sys.exit(self.exit_code)


# ---------------------------------------------------------------------------- runner
@dataclass
class HarnessResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    log_path: Path | None = None

    def log_tail(self, chars: int = 6000) -> str:
        if self.log_path is None or not self.log_path.exists():
            return ""
        return self.log_path.read_text(errors="replace")[-chars:]

    @property
    def report(self) -> str:
        """stdout and stderr, plus the end of the harness log when the run failed: what an assertion should show."""
        text = self.stdout + self.stderr
        tail = self.log_tail() if self.returncode != 0 else ""
        return text + (f"\n--- log ---\n{tail}" if tail else "")

    def assert_ok(self, *expected: str) -> "HarnessResult":
        """Assert a zero exit status and that each ``expected`` string (for example ``"23/23 passed"``) is in stdout."""
        assert self.returncode == 0, self.report
        for text in expected:
            assert text in self.stdout, f"{text!r} not in the harness output\n{self.report}"
        return self

    def count(self, text: str) -> int:
        return self.stdout.count(text)


def run_harness(script: str | Path, *args: Any, env: dict[str, str] | None = None, timeout: float = 600,
                log_path: str | Path | None = None, log_env: str = "E2E_LOG", python: str | None = None,
                cwd: str | Path | None = None) -> HarnessResult:
    """Run a harness script in a fresh interpreter and capture its output.

    ``env`` is added to the current environment. When ``log_path`` is given it is passed to the harness in the
    ``log_env`` variable (``E2E_LOG`` by default; the Modbus harnesses read ``MODBUS_E2E_LOG``) and its tail is
    included in :attr:`HarnessResult.report` on failure.
    """
    full_env = {**os.environ, **(env or {})}
    log = Path(log_path) if log_path is not None else None
    if log is not None:
        full_env[log_env] = str(log)
    cmd = [python or sys.executable, str(script), *map(str, args)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=full_env,
                          cwd=str(cwd) if cwd is not None else None)
    return HarnessResult(cmd, proc.returncode, proc.stdout, proc.stderr, log)
