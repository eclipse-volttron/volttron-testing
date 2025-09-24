#!/usr/bin/env python
"""
Simple test for package manager without circular imports.
"""

import subprocess
import sys
import logging

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)


class SimplePackageManager:
    """Simplified version for testing"""
    
    def __init__(self):
        self._installed = []
        self._original = {}
    
    def get_version(self, package):
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", package],
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
    
    def install(self, package):
        # Save original state
        original = self.get_version(package.split('[')[0].split('==')[0])
        if original:
            self._original[package] = original
            _log.info(f"{package} already installed: {original}")
            return True
            
        # Install
        _log.info(f"Installing {package}")
        try:
            cmd = [sys.executable, "-m", "pip", "install", "--pre", package]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            self._installed.append(package)
            _log.info(f"Installed {package}")
            return True
        except Exception as e:
            _log.error(f"Failed to install {package}: {e}")
            return False
    
    def cleanup(self):
        _log.info("Cleaning up packages")
        for package in self._installed:
            pkg_name = package.split('[')[0].split('==')[0]
            if pkg_name not in self._original:
                # Uninstall
                _log.info(f"Uninstalling {pkg_name}")
                try:
                    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", pkg_name]
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                except Exception as e:
                    _log.warning(f"Failed to uninstall {pkg_name}: {e}")


def test_manager():
    """Test the package manager"""
    manager = SimplePackageManager()
    
    # Check volttron-lib-zmq 
    zmq_version = manager.get_version("volttron-lib-zmq")
    print(f"volttron-lib-zmq version: {zmq_version}")
    
    # Ensure it's installed
    success = manager.install("volttron-lib-zmq")
    assert success, "Failed to ensure volttron-lib-zmq"
    
    # Check it's still there
    new_version = manager.get_version("volttron-lib-zmq")
    assert new_version is not None, "volttron-lib-zmq should be installed"
    print(f"After install: {new_version}")
    
    # Clean up (should not remove since it was already installed)
    manager.cleanup()
    
    final_version = manager.get_version("volttron-lib-zmq")
    print(f"After cleanup: {final_version}")
    
    # Should still be installed
    assert final_version == zmq_version, "Version should be preserved"
    print("✓ Test passed!")


if __name__ == "__main__":
    print("\n=== Testing Simple Package Manager ===\n")
    test_manager()
    print("\n=== Success! ===")