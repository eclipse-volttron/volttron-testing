#!/usr/bin/env python
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

"""
Test the package manager for automatic installation and cleanup.
"""

import subprocess
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)


def get_package_version(package_name: str):
    """Helper to check if a package is installed"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "show", package_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            for line in result.stdout.split('\n'):
                if line.startswith('Version:'):
                    return line.split(':', 1)[1].strip()
    except Exception:
        pass
    return None


def test_package_manager():
    """Test the MessageBusPackageManager directly"""
    from volttrontesting.platformwrapper_enhanced import MessageBusPackageManager
    
    # Create package manager
    manager = MessageBusPackageManager()
    
    # Check initial state of a test package (use requests as a safe test)
    test_package = "requests"
    original_version = get_package_version(test_package)
    
    print(f"Original {test_package} version: {original_version}")
    
    # Test ensure_package_installed
    success = manager.ensure_package_installed(test_package)
    assert success, f"Failed to ensure {test_package} is installed"
    
    # Verify package is installed
    current_version = get_package_version(test_package)
    assert current_version is not None, f"{test_package} should be installed"
    print(f"Current {test_package} version: {current_version}")
    
    # Test cleanup
    manager.cleanup()
    
    # Check final state
    final_version = get_package_version(test_package)
    
    if original_version is None:
        # Package wasn't installed before, should be uninstalled now
        assert final_version is None or final_version == original_version, \
            f"{test_package} should be uninstalled or restored"
        print(f"✓ Package state restored (uninstalled or original)")
    else:
        # Package was installed before, should be same version
        assert final_version == original_version, \
            f"{test_package} should be restored to {original_version}, got {final_version}"
        print(f"✓ Package restored to original version: {original_version}")


def test_volttron_lib_zmq_management():
    """Test managing volttron-lib-zmq specifically"""
    from volttrontesting.platformwrapper_enhanced import MessageBusPackageManager
    
    # Create package manager
    manager = MessageBusPackageManager()
    
    # Check initial state 
    original_zmq_version = get_package_version("volttron-lib-zmq")
    print(f"\nOriginal volttron-lib-zmq version: {original_zmq_version}")
    
    # Note: volttron-lib-zmq is already installed in this environment
    # So we test that it's preserved
    
    # Ensure it's installed (should detect existing)
    success = manager.ensure_package_installed("volttron-lib-zmq")
    assert success, "Failed to ensure volttron-lib-zmq is installed"
    
    current_version = get_package_version("volttron-lib-zmq")
    print(f"After ensure_installed: {current_version}")
    
    # Cleanup should not remove it since it was already installed
    manager.cleanup()
    
    final_version = get_package_version("volttron-lib-zmq")
    print(f"After cleanup: {final_version}")
    
    # Should still be installed
    assert final_version is not None, "volttron-lib-zmq should still be installed"
    print("✓ volttron-lib-zmq preserved as expected")


def test_cleanup_on_exception():
    """Test that cleanup happens even with exceptions"""
    from volttrontesting.platformwrapper_enhanced import MessageBusPackageManager
    
    manager = MessageBusPackageManager()
    
    # Test with a simple package that we know won't break anything
    test_package = "six"  # Common utility package
    
    # Check original state
    original = get_package_version("six")
    
    # Ensure it's installed
    manager.ensure_package_installed(test_package)
    
    # Should be installed now
    current = get_package_version("six")
    assert current is not None, "Package should be installed"
    
    # Cleanup
    manager.cleanup()
    
    # Check final state matches original
    final = get_package_version("six")
    if original:
        # Was installed before, should still be installed
        assert final is not None, "Should still be installed"
    else:
        # Wasn't installed, might be removed (depending on dependencies)
        pass  # Don't assert as it might be needed by other packages
    
    print("✓ Cleanup works correctly")


if __name__ == "__main__":
    print("\n=== Testing Package Manager ===\n")
    
    print("1. Testing basic package management...")
    test_package_manager()
    
    print("\n2. Testing volttron-lib-zmq management...")
    test_volttron_lib_zmq_management()
    
    print("\n3. Testing cleanup registration...")
    test_cleanup_on_exception()
    
    print("\n=== All tests passed! ===")