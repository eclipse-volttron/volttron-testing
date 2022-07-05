"""Tests suite for `testing`."""

from pathlib import Path
import sys

if '../src' not in sys.path:
    sys.path.insert(0, '../src')

from testing.fixtures.volttron_platform_fixtures import *
