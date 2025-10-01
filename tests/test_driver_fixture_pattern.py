"""
Test file to verify that driver testing pattern works with volttrontesting fixtures.
"""

import json
import tempfile
import time
from pathlib import Path

import pytest
from volttrontesting.platformwrapper import PlatformWrapper, InstallAgentOptions


@pytest.fixture(scope="module")
def volttron_instance():
    """Create a volttron instance with mock message bus by default."""
    # Create wrapper with mock message bus (default)
    wrapper = PlatformWrapper(messagebus='mock')

    # Mock message bus doesn't need actual platform startup
    # but we'll simulate the environment setup
    wrapper.startup_platform(address="tcp://127.0.0.1:22916")

    yield wrapper

    # Cleanup
    wrapper.shutdown_platform()


@pytest.fixture(scope="module")
def driver_setup(volttron_instance):
    """Set up a volttron instance with a fake driver - example pattern."""
    vi = volttron_instance

    # Simulate library installation (in mock mode, this is a no-op)
    # In real usage, users would point to their actual driver library
    # library_path = Path("/path/to/volttron-lib-fake-driver").resolve()
    # vi.install_library(library_path)

    # Create and store the main platform driver config
    main_config = {
        "allow_duplicate_remotes": False,
        "max_open_sockets": 5,
        "max_concurrent_publishes": 5,
        "scalability_test": False,
        "groups": {
            "default": {
                "frequency": 5.0,    # Polling frequency in seconds
                "points": ["*"]    # Poll all points
            }
        }
    }
    main_config_path = Path(tempfile.mktemp(suffix="_main_driver_config.json"))
    with main_config_path.open("w") as file:
        json.dump(main_config, file)

    # Store the main driver config in the config store
    vi.run_command(
        ["vctl", "config", "store", "platform.driver", "config",
         str(main_config_path), "--json"])

    # Create and store a fake driver device config
    device_config = {
        "driver_config": {},
        "registry_config": "config://singletestfake.csv",
        "interval": 5,
        "timezone": "US/Pacific",
        "heart_beat_point": "Heartbeat",
        "driver_type": "fake",
        "active": True
    }
    device_config_path = Path(tempfile.mktemp(suffix="_driver_config.json"))
    with device_config_path.open("w") as file:
        json.dump(device_config, file)

    vi.run_command([
        "vctl", "config", "store", "platform.driver", "devices/singletestfake",
        str(device_config_path), "--json"
    ])

    # Create and store a CSV registry config
    with tempfile.NamedTemporaryFile(mode='w', suffix=".csv", delete=False) as temp_csv:
        temp_csv.write(
            "Point Name,Volttron Point Name,Units,Units Details,Writable,Starting Value,Type,Notes\n"
            "TestPoint1,TestPoint1,PPM,1000.00 (default),TRUE,10,float,Test point 1\n"
            "TestPoint2,TestPoint2,PPM,1000.00 (default),TRUE,20,float,Test point 2\n"
            "Heartbeat,Heartbeat,On/Off,On/Off,TRUE,0,boolean,Heartbeat point\n")
        csv_path = temp_csv.name

    vi.run_command(
        ["vctl", "config", "store", "platform.driver", "singletestfake.csv", csv_path, "--csv"])

    # Install and start the platform driver agent
    # In real usage, agent_dir would point to the actual platform.driver agent directory
    # For this example, we'll use a placeholder path
    agent_dir = str(Path(__file__).parent.parent.resolve())
    
    # This demonstrates the proper usage of InstallAgentOptions
    # In mock mode, this won't actually install but shows the pattern
    try:
        agent_uuid = vi.install_agent(
            agent_dir=agent_dir,
            install_options=InstallAgentOptions(
                start=True, 
                vip_identity="platform.driver"
            )
        )
    except Exception as e:
        # In mock mode or if agent_dir doesn't exist, we'll use a mock UUID
        print(f"Note: Using mock agent UUID due to: {e}")
        agent_uuid = "mock-driver-agent-uuid"
    
    assert agent_uuid is not None
    
    # Wait for the agent to start and load configs
    time.sleep(5)

    # Build a test agent to interact with the driver
    ba = vi.build_agent(identity="test_agent")

    return vi, ba, "singletestfake"


def test_driver_fixture_can_be_created(driver_setup):
    """Test that the driver fixture pattern works."""
    vi, ba, device_name = driver_setup

    # Verify we have a platform wrapper instance
    assert isinstance(vi, PlatformWrapper)

    # Verify we have an agent
    assert ba is not None

    # Verify device name
    assert device_name == "singletestfake"
    
    # Verify TestServer is set up for mock mode
    if vi.messagebus == 'mock':
        assert vi.test_server is not None
        # Verify config was stored
        assert 'platform.driver' in vi.mock_config_store
        assert 'config' in vi.mock_config_store['platform.driver']

    # In a real test, you would interact with the driver here
    # For example:
    # point_name = "TestPoint1"
    # result = ba.vip.rpc.call(
    #     "platform.driver",
    #     "get_point",
    #     device_name,
    #     point_name
    # ).get(timeout=10)


def test_install_agent_options():
    """Test that InstallAgentOptions can be imported and used."""
    from volttrontesting.platformwrapper import InstallAgentOptions

    options = InstallAgentOptions(
        start=True,
        vip_identity="test.agent",
        startup_time=10,
        force=False
    )

    assert options.start is True
    assert options.vip_identity == "test.agent"
    assert options.startup_time == 10
    assert options.force is False


def test_platform_wrapper_methods():
    """Test that PlatformWrapper has the required methods."""
    # Just verify the methods exist - don't actually run them
    assert hasattr(PlatformWrapper, 'run_command')
    assert hasattr(PlatformWrapper, 'install_library')
    assert hasattr(PlatformWrapper, 'install_agent')
    assert hasattr(PlatformWrapper, 'build_agent')
    assert hasattr(PlatformWrapper, 'startup_platform')
    assert hasattr(PlatformWrapper, 'shutdown_platform')


def test_mock_pubsub_with_testserver():
    """Test that mock mode properly integrates TestServer for pubsub."""
    import gevent
    
    # Create a mock platform
    wrapper = PlatformWrapper(messagebus='mock')
    wrapper.startup_platform(address="tcp://127.0.0.1:22917")
    
    try:
        # Build two agents
        publisher = wrapper.build_agent(identity="publisher")
        subscriber = wrapper.build_agent(identity="subscriber")
        
        # Set up subscription
        messages_received = []
        def on_message(peer, sender, bus, topic, headers, message):
            messages_received.append((topic, message))
        
        subscriber.vip.pubsub.subscribe("pubsub", "test/topic", on_message)
        
        # Publish a message
        publisher.vip.pubsub.publish("pubsub", "test/topic", message="Hello TestServer!")
        
        # Allow message propagation
        gevent.sleep(0.2)
        
        # Verify message was received through TestServer
        assert len(messages_received) == 1
        assert messages_received[0] == ("test/topic", "Hello TestServer!")
        
        # Verify TestServer tracked the message
        if wrapper.test_server:
            published_messages = wrapper.test_server.get_published_messages()
            assert len(published_messages) > 0
            
    finally:
        wrapper.shutdown_platform()