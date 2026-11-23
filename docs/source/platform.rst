.. _Testing-Platform:

==============================
One Test, Several Platforms
==============================

The ``platform`` fixture gives a test a *platform under test*: one small set of operations that work the same way
on three backends. A test written against it runs in process, where it is fast and needs no platform, and on a
real platform, without changing the test body.

``testserver``
    The agents run in the test process on a ``TestServer``. RPC calls between agents are routed, a configuration
    store delivers configs through core's own config subsystem, scheduled callbacks run on time, and the log holds
    every logger in the process. Protocol proxies are real processes. Creating this backend gevent-patches the test
    process, as importing an agent does.

``cli``
    A real platform driven with ``vctl`` (``PlatformCLI``).

``wrapper``
    A real platform through ``PlatformWrapper``, with an agent in the test process for RPC and pubsub.


Operations
==========

``install_agent(source, identity, tag=None, *, config=None)``
    On a real platform, ``source`` is anything ``vctl install`` takes. In process, it is an agent class,
    ``"module:Class"``, or a project directory or installed distribution: its console script is run with
    ``vip_main`` replaced, to find the class the platform would start. The package must be importable in the test
    environment.

``restart_agent(identity)``, ``store_config(identity, name, content_or_path, kind="json")``
    ``kind`` is ``json``, ``csv`` or ``raw``. Configs stored before an agent starts arrive in its first batch.

``rpc(identity, method, *args, **kwargs)``
    Calls an agent's exported method. The caller sees ``MethodNotFound``, ``RemoteError`` or ``Unreachable`` as on a
    platform.

``get(topic)``, ``get_point(topic)``, ``set_point(topic, value)``, ``revert(topic)``
    The Platform Driver's ``get``, ``set`` and ``revert``.

``subscribe(prefix)``, ``publish(topic, headers=None, message=None)``
    ``subscribe`` returns an observer: ``wait(count)`` blocks until that many messages have arrived.

``log_size()``, ``log_since(offset)``, ``wait_for_log(pattern, since=0)``
    The platform log.

An operation a backend cannot do raises ``NotSupported``, which is reported as a skip that names the backend. For
example, the ``cli`` backend calls RPC only when the installed core's ``vctl`` has ``rpc call``, and subscribes only
when its ``vctl subscribe`` has ``--json``.


Choosing backends
=================

Mark a test, class or module with the backends it is written for. An unmarked test runs on ``testserver``:

.. code-block:: python

   import pytest

   pytestmark = pytest.mark.platform_backend("testserver", "cli")

   def test_setpoint(platform_driver):
       platform = platform_driver.platform
       platform.store_config("platform.driver", "devices/campus/building/rtu", {...})
       assert platform.set_point("campus/building/rtu/SetPoint", 72.5) == 72.5

``--platform-backend`` narrows a run. Marked tests run only on the listed backends among their own, and unmarked
tests run on the listed backends. It never moves a test onto a backend it is not marked for: such a test is
skipped, with the reason. So one CI run can run each test on its own backends, and a quick local run can use
``--platform-backend=testserver``.

Real-platform backends run once per selected message bus, with ids such as ``cli-zmq-libauth``, and are marked
``platform``. The fixture's scope follows ``volttron_platform_scope``, as for the other platform fixtures.

``platform_driver`` installs the Platform Driver on ``platform``, so it runs on the same backends.

A repository that defines its own fixture named ``platform`` keeps it: the plugin parametrizes only its own.
