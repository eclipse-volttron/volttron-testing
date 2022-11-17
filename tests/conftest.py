import sys
from pathlib import Path

src_path = Path(__file__).parent.parent.joinpath('src')
# Adds src dir to the path
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))
    
from volttrontesting.fixtures.volttron_platform_fixtures import *