import sys
from pathlib import Path

path_src = Path(__file__).parent.parent.joinpath('src')
# Adds src dir to the path
if str(path_src.resolve()) not in sys.path:
    sys.path.insert(0, str(path_src.resolve()))

from volttrontesting.fixtures.volttron_platform_fixtures import *
