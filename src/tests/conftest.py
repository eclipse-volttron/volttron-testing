import sys
from pathlib import Path

p = Path(__file__)
# Adds src dir to the path
if p.parent.parent.resolve().as_posix() not in sys.path:
    sys.path.insert(0, p.parent.parent.resolve().as_posix())

if p.parent.resolve().as_posix() not in sys.path:
    sys.path.insert(0, p.parent.resolve().as_posix())

from fixtures.volttron_platform_fixtures import *
