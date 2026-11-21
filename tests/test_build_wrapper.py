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
"""build_wrapper fails with the platform's own error when the platform does not start, after cleaning up."""
import pytest

from volttrontesting.fixtures import volttron_platform_fixtures as fixtures


def test_a_platform_that_does_not_start_fails_the_fixture(monkeypatch):
    cleaned = []

    def fail(self, address, **kwargs):
        raise RuntimeError("platform did not start")

    monkeypatch.setattr(fixtures.PlatformWrapper, "startup_platform", fail)
    monkeypatch.setattr(fixtures, "cleanup_wrapper", lambda wrapper: cleaned.append(wrapper))
    with pytest.raises(RuntimeError, match="platform did not start"):
        fixtures.build_wrapper("tcp://127.0.0.1:1", messagebus="zmq", ssl_auth=True)
    assert len(cleaned) == 1 and cleaned[0].messagebus == "zmq"
