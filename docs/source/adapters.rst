.. _Testing-Adapters:

=====================================
Message Bus and Auth Module Adapters
=====================================

VOLTTRON's message bus and its auth module are both pluggable. volttron-testing supports this through *adapters*:
small classes that perform the few steps a test needs from a bus or an auth module that ``vctl`` cannot provide.

``PlatformCLI`` drives a platform only through its config file and ``vctl``, so it needs almost nothing from either.
The exception is ``grant``, which goes through the auth adapter. ``PlatformWrapper`` also builds agents inside the test
process. For that it must create agent credentials, register their authorization before the platform starts, and
construct the bus's agent core. Those are the adapter's steps.


Installing a bus and an auth module
===================================

volttron-testing does not depend on any message bus or auth module. Install the ones your tests run with, either as
extras or as their own distributions:

.. code-block:: bash

   pip install "volttron-testing[zmq,libauth]"

============= ============================ =======================================
Adapter       Kind                         Provided by
============= ============================ =======================================
``zmq``       message bus                  volttron-lib-zmq (extra ``zmq``)
``libauth``   auth module                  volttron-lib-auth (extra ``libauth``)
``none``      auth module, auth disabled   built in
============= ============================ =======================================


How a platform's bus and auth module are chosen
===============================================

The two are chosen differently, because the platform chooses them differently.

* **The message bus is chosen per platform.** The platform config's ``messagebus`` key names it, so one test run can
  start platforms on several buses. ``PlatformWrapper(messagebus=...)`` and ``PlatformCLI(message_bus=...)`` take the
  name.
* **The auth module is chosen per environment.** The platform has no setting for it and uses whichever auth module is
  installed. Two cannot be installed together, because both register the ``platform.auth`` service. A platform with
  auth disabled uses the built-in ``none`` adapter.

The fixtures that start real platforms (``volttron_instance``, ``platforms``) run once per selected bus, with the
environment's auth module. Test ids name the pair, for example ``zmq-libauth``. Two environment variables control
the selection:

``VOLTTRON_TEST_MESSAGEBUS``
    A comma-separated list of buses to run on. Unset, every bus whose adapter is available is used. A listed bus
    that is not available is an error, so a CI job that asks for a bus cannot pass by testing nothing.

``VOLTTRON_TEST_AUTH``
    The auth module the environment is expected to have. Unset, it is the one installed. None installed, more than
    one, or a different one from the name given, is an error.

A pair the auth module does not support (see ``buses`` below) is skipped. Testing a second auth module therefore
means a second environment, for instance a second CI job.


Writing an adapter
==================

An adapter is a plain class. The interfaces, ``MessageBusAdapter`` and ``AuthAdapter`` in
``volttrontesting.adapters``, are structural ``typing.Protocol`` classes: an adapter needs the attributes and methods
below, not a base class, so its distribution does not have to import or depend on volttron-testing. Import the bus or
auth library inside the methods rather than at module level. Listing the available adapters then works even when
that library is not installed.

Both kinds have these attributes:

``api_version``
    The adapter interface version the class implements. The current value is ``volttrontesting.adapters.API_VERSION``,
    which is ``1``. An adapter that declares a different version is reported as incompatible and is not used.

``name``
    The adapter's name. For a bus it is the platform config's ``messagebus`` value. It is also the entry point name.

``distributions``
    The distributions that provide the bus or auth module. The adapter is available when all of them are installed.

Message bus adapter
-------------------

One instance is created per platform. ``home`` is the platform's ``VOLTTRON_HOME``.

``write_credentials(home, identity)``
    Create the credentials for ``identity`` in the platform's credential store, or return them if they already
    exist. It is used before the platform starts, for the wrapper's own agent, and for every agent when auth is
    disabled. Return the credentials object the bus's agent core accepts.

``read_credentials(home, identity)``
    Return the stored credentials for ``identity``. It is used after the platform's auth service has created them,
    in answer to the core ``create_credentials`` call.

``server_credentials(home)``
    Return the platform's own credentials, read after it started. Agents need its public key to connect.

``core_builder()``
    Return a context manager. Inside it, ``Agent(...)`` must construct this bus's agent core for this platform.
    Core keeps the core builder in a process-wide cache, so install the builder for the duration of the block and
    restore the previous state afterwards. Keep the builder on the instance, so that all of a platform's agents share
    it.

Auth adapter
------------

``buses``
    The bus names the auth module works with, or ``None`` for every bus.

``enabled``
    ``True`` for a real auth module. Platforms then run with ``auth-enabled = True``.

``write_authz(home, identity, admin=False)``
    Register the authorization for ``identity`` on disk before the platform starts, so that the platform has it when
    the agent connects. ``admin`` grants every RPC and pubsub capability.

``grant(platform, identity, rpc_capabilities)``
    Add RPC capabilities to an agent on a running platform. ``platform`` is a ``PlatformCLI``; use its ``vctl``.


Registering an adapter
======================

Register the class under an entry point in the distribution that provides the bus or auth module, or in a small
companion distribution. The groups are ``volttrontesting.message_buses`` and ``volttrontesting.auth_modules``. With
poetry:

.. code-block:: toml

   [tool.poetry.plugins."volttrontesting.message_buses"]
   mybus = "volttron.messagebus.mybus.testing:MyBusAdapter"

With standard project metadata:

.. code-block:: toml

   [project.entry-points."volttrontesting.message_buses"]
   mybus = "volttron.messagebus.mybus.testing:MyBusAdapter"

An entry point with the same name as a built-in adapter replaces it. That way a bus or auth module can take over
its own adapter later without a change to volttron-testing.

A complete example that does nothing, which is useful as a starting point and is what volttron-testing's own tests
register:

.. code-block:: python

   from contextlib import nullcontext

   class StubBus:
       api_version = 1
       name = "stub"
       distributions = ("stub-adapters",)

       def write_credentials(self, home, identity):
           return identity

       def read_credentials(self, home, identity):
           return identity

       def server_credentials(self, home):
           return "server"

       def core_builder(self):
           return nullcontext()


Where an adapter should live, and why zmq and lib-auth are different
====================================================================

Most adapters belong with the bus or auth module they adapt: in its own distribution, or in a companion one. The
adapter changes when that library changes, and the release order stays one way. The library releases, then its
adapter is updated.

The ``zmq`` and ``libauth`` adapters are the exception. They live in volttron-testing, built in rather than
registered, because volttron-testing's own CI tests the wrapper with them. If lib-zmq owned the zmq adapter, the two
repositories' test suites would depend on each other:

* a change to the adapter interface would break volttron-testing's tests until lib-zmq released a matching adapter;
* that lib-zmq release could not be tested until volttron-testing released the new interface.

Keeping these adapters in volttron-testing removes the cycle. The rule is that **every bus and auth module in
volttron-testing's own CI has its adapter in volttron-testing; any other may ship its own**. Built-in adapters are
listed in code (``volttrontesting.adapters.BUILT_IN``) rather than as entry points. They therefore also work from a
source checkout or an editable install whose metadata is out of date.


Interface versions
==================

``API_VERSION`` changes when an adapter method or attribute is added or removed, or changes meaning. It covers both
kinds of adapter. Adding an optional helper that volttron-testing checks for with ``hasattr`` does not change it.
When it changes, the volttron-testing release notes describe the change. Adapters declaring the old version are
reported as incompatible, with the version they declare, until they are updated.
