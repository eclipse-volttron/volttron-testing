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
Enhancements to PlatformWrapper for automatic message bus package management.

This module monkey-patches the existing PlatformWrapper to add automatic
installation and cleanup of message bus packages (volttron-lib-zmq, volttron-lib-rmq).
"""

from __future__ import annotations

import atexit
import logging
import subprocess
import sys
from typing import Optional, Dict, List

_log = logging.getLogger(__name__)


class MessageBusPackageManager:
    """
    Manages message bus package installation and cleanup.
    
    This ensures that volttron-lib-zmq or volttron-lib-rmq are installed
    when needed and cleaned up after tests.
    """
    
    _instance = None
    
    def __new__(cls):
        """Singleton pattern to ensure only one manager"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._installed_packages: List[str] = []
        self._original_states: Dict[str, Optional[str]] = {}
        self._cleanup_registered = False
        self._initialized = True
        
    def _get_package_version(self, package_name: str) -> Optional[str]:
        """Get the currently installed version of a package"""
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
        except Exception as e:
            _log.debug(f"Could not get version for {package_name}: {e}")
        return None
    
    def ensure_package_installed(self, package_name: str) -> bool:
        """
        Ensure a message bus package is installed.
        
        :param package_name: Package name (e.g., 'volttron-lib-zmq')
        :return: True if package is available
        """
        # Check if already installed
        version = self._get_package_version(package_name)
        if version:
            _log.info(f"{package_name} already installed (version {version})")
            return True
            
        # Save original state (not installed)
        if package_name not in self._original_states:
            self._original_states[package_name] = None
            
        # Install the package with pre-release flag
        _log.info(f"Installing {package_name} (pre-release)")
        try:
            cmd = [
                sys.executable, "-m", "pip", "install", 
                "--pre",  # Allow pre-release versions
                package_name
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            self._installed_packages.append(package_name)
            _log.info(f"Successfully installed {package_name}")
            
            # Register cleanup handler on first install
            if not self._cleanup_registered:
                atexit.register(self.cleanup)
                self._cleanup_registered = True
                
            return True
            
        except subprocess.CalledProcessError as e:
            _log.error(f"Failed to install {package_name}: {e.stderr}")
            return False
    
    def cleanup(self):
        """Clean up installed packages, restoring original state"""
        if not self._installed_packages:
            return
            
        _log.info("Cleaning up message bus packages")
        
        # Make a copy to avoid modification during iteration
        packages_to_clean = self._installed_packages.copy()
        
        for package in packages_to_clean:
            original = self._original_states.get(package)
            
            if original is None:
                # Package wasn't installed before, remove it
                _log.info(f"Uninstalling {package}")
                try:
                    cmd = [sys.executable, "-m", "pip", "uninstall", "-y", package]
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    _log.info(f"Successfully uninstalled {package}")
                except subprocess.CalledProcessError as e:
                    _log.warning(f"Failed to uninstall {package}: {e.stderr}")
                except Exception as e:
                    _log.warning(f"Unexpected error uninstalling {package}: {e}")
            else:
                # Package was installed before, restore original version
                _log.info(f"Restoring {package} to version {original}")
                try:
                    cmd = [
                        sys.executable, "-m", "pip", "install",
                        f"{package}=={original}"
                    ]
                    subprocess.run(cmd, capture_output=True, text=True, check=True)
                    _log.info(f"Successfully restored {package} to version {original}")
                except subprocess.CalledProcessError as e:
                    _log.error(f"Failed to restore {package}: {e.stderr}")
                except Exception as e:
                    _log.error(f"Unexpected error restoring {package}: {e}")
        
        self._installed_packages.clear()
        self._original_states.clear()
        _log.info("Package cleanup complete")


# Global package manager instance
_package_manager = MessageBusPackageManager()


def enhance_platform_wrapper():
    """
    Enhance the existing PlatformWrapper class with automatic package management.
    
    This function should be called before using PlatformWrapper to add
    automatic message bus package installation.
    """
    from volttrontesting.platformwrapper import PlatformWrapper
    
    # Store original methods
    original_init = PlatformWrapper.__init__
    original_startup = PlatformWrapper.startup_platform
    original_shutdown = PlatformWrapper.shutdown_platform
    
    def enhanced_init(self, messagebus=None, *args, **kwargs):
        """Enhanced __init__ that tracks message bus type"""
        original_init(self, messagebus=messagebus, *args, **kwargs)
        self._messagebus_package_installed = False
        
    def enhanced_startup(self, *args, **kwargs):
        """Enhanced startup that installs message bus package if needed"""
        # Install appropriate message bus package
        if self.messagebus == "zmq" and not self._messagebus_package_installed:
            if _package_manager.ensure_package_installed("volttron-lib-zmq"):
                self._messagebus_package_installed = True
                _log.info("volttron-lib-zmq package ready for testing")
            else:
                raise RuntimeError("Failed to install volttron-lib-zmq")
                
        elif self.messagebus == "rmq" and not self._messagebus_package_installed:
            if _package_manager.ensure_package_installed("volttron-lib-rmq"):
                self._messagebus_package_installed = True
                _log.info("volttron-lib-rmq package ready for testing")
            else:
                raise RuntimeError("Failed to install volttron-lib-rmq")
        
        # Call original startup
        return original_startup(self, *args, **kwargs)
    
    def enhanced_shutdown(self):
        """Enhanced shutdown that includes cleanup"""
        try:
            original_shutdown(self)
        finally:
            # Note: Cleanup happens automatically via atexit, but we could
            # trigger it here if we want immediate cleanup
            pass
    
    # Apply enhancements
    PlatformWrapper.__init__ = enhanced_init
    PlatformWrapper.startup_platform = enhanced_startup
    PlatformWrapper.shutdown_platform = enhanced_shutdown
    
    # Add cleanup method to class
    PlatformWrapper.cleanup_packages = lambda self: _package_manager.cleanup()
    
    _log.info("PlatformWrapper enhanced with automatic package management")


# Auto-enhance when this module is imported
enhance_platform_wrapper()


# Context manager for explicit cleanup
from contextlib import contextmanager

@contextmanager
def managed_platform_wrapper(*args, **kwargs):
    """
    Context manager for PlatformWrapper with guaranteed cleanup.
    
    Usage:
        with managed_platform_wrapper(messagebus="zmq") as wrapper:
            wrapper.startup_platform(...)
            # Run tests
        # Cleanup happens automatically
    """
    from volttrontesting.platformwrapper import PlatformWrapper
    
    wrapper = PlatformWrapper(*args, **kwargs)
    try:
        yield wrapper
    finally:
        try:
            wrapper.shutdown_platform()
        except Exception as e:
            _log.error(f"Error during shutdown: {e}")
        finally:
            _package_manager.cleanup()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    print("\n=== Testing Enhanced PlatformWrapper ===")
    
    # The enhancement happens automatically on import
    from volttrontesting.platformwrapper import PlatformWrapper
    
    # Now PlatformWrapper automatically manages packages
    wrapper = PlatformWrapper(messagebus="zmq")
    
    print("\nWhen startup_platform is called, volttron-lib-zmq will be installed automatically")
    print("When the process exits, packages will be cleaned up automatically")
    
    # Or use context manager for explicit cleanup
    print("\n=== Using Context Manager ===")
    with managed_platform_wrapper(messagebus="zmq") as wrapper:
        print("Wrapper created with automatic package management")
        # wrapper.startup_platform(...)
    print("Packages cleaned up")