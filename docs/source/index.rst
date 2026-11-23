.. _VOLTTRON-Testing:

================
VOLTTRON Testing
================

`volttron-testing <https://pypi.org/project/volttron-testing/>`__ provides classes and pytest fixtures for testing
VOLTTRON agents and libraries at three levels:

* **In process.** ``TestServer`` connects an agent instance to an in-memory server, so a test can trigger lifecycle
  events, publish, and inspect what the agent published, without a platform.
* **Black box.** ``PlatformCLI`` starts a real platform as a subprocess in a temporary ``VOLTTRON_HOME`` and drives it
  with ``vctl``, the way an operator does.
* **White box.** ``PlatformWrapper`` starts a real platform through ``PlatformCLI`` and also builds agents in the
  test process, for assertions on the message bus itself.

Its pytest plugin provides fixtures for the real-platform levels (:ref:`Testing-Fixtures`), and a ``platform`` fixture
that runs one test body on any of the three (:ref:`Testing-Platform`). They run on any message bus and auth module
that has an adapter (:ref:`Testing-Adapters`).

.. toctree::
   :maxdepth: 2

   fixtures
   platform
   adapters
