.. _Testing-Fixtures:

==========================
pytest Plugin and Fixtures
==========================

Installing volttron-testing installs a pytest plugin, ``volttrontesting.plugin``, which pytest loads automatically.
It provides fixtures that start real platforms, a fixture for test certificates, markers and command-line options.
Loading it imports nothing heavy: the platform fixtures import their code only when a test uses them.

The older fixtures built on ``PlatformWrapper`` (``volttron_instance``, ``platforms`` and others) are not part of the
plugin. A repository that uses them keeps importing them in its ``conftest.py``:

.. code-block:: python

   from volttrontesting.fixtures.volttron_platform_fixtures import *


Fixtures
========

``platform_cli``
    A started ``PlatformCLI``: a real platform in a temporary ``VOLTTRON_HOME``, driven with ``vctl``.

``web_platform``
    A separate started ``PlatformCLI`` with the web service and one web user, available as
    ``web_platform.web_user``.

``platform_driver``
    The Platform Driver installed on ``platform_cli``, as a ``DriverHandle``. It offers:

    - ``store_registry(name, rows_or_path)``;
    - ``store_device(topic, config)``;
    - ``get``, ``set`` and ``revert`` for points;
    - ``cli``, the platform.

    To put the driver on another platform, for example ``web_platform``, call
    ``volttrontesting.plugin.install_platform_driver(cli)``.

``certs``
    A ``CertificateSet`` in a temporary directory: a CA, server certificates (``certs.server(name, dns=..., ips=...)``)
    and client certificates (``certs.client(name)``), as PEM files.

The driver source is the Platform Driver checkout when one can be found, and its PyPI name otherwise (see
``AgentSources``).


Configuring the platforms
=========================

Override the options fixtures in your ``conftest.py``. Each returns ``PlatformCLI`` keyword arguments. Keep them
session-scoped:

.. code-block:: python

   import pytest
   from volttrontesting.plugin import WebUser

   @pytest.fixture(scope="session")
   def platform_cli_options():
       return dict(instance_name="my-tests")

   @pytest.fixture(scope="session")
   def web_platform_options():
       return dict(rpc_allow_list=["platform.control: peerlist"])

   @pytest.fixture(scope="session")
   def web_user():
       return WebUser("admin", "a fixed password")


Scope
=====

By default each test module gets its own platform. To share one platform across a package or the whole session,
set ``volttron_platform_scope`` in the pytest configuration, or pass ``--volttron-platform-scope`` on the command
line:

.. code-block:: toml

   [tool.pytest.ini_options]
   volttron_platform_scope = "session"

A wider scope is faster but shares state between modules: installed agents, configuration store entries and
authorization changes.


Selecting and skipping
======================

``VOLTTRON_TESTING_SKIP_PLATFORM=1``
    Skips every test that uses a platform fixture. Without it, such tests also skip, with the reason, when the
    environment cannot start a platform: ``volttron`` or ``vctl`` is missing, there is no message bus or auth module,
    or the Platform Driver is missing for ``platform_driver``.

``-m "not platform"``
    Deselects those tests. The plugin marks every test that uses a platform fixture with ``platform``. It also
    registers the ``docker`` and ``slow`` markers.

``--messagebus`` (or ``VOLTTRON_TEST_MESSAGEBUS``)
    The buses the platform fixtures run on, comma-separated. By default each runs once per available bus, with ids
    such as ``zmq-libauth``. See :ref:`Testing-Adapters`.
