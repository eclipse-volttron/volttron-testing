#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Example test for a driver agent that uses scheduled periodic scraping.

This demonstrates how to test driver agents that previously failed with
"MockCore has no schedule attribute" errors.
"""

from volttrontesting.server_mock import TestServer
from volttron.client import Agent
from volttron.client.vip.agent import Core, PubSub
from volttron.types.auth.auth_credentials import Credentials


class MockDriver(Agent):
    """
    Example driver that periodically scrapes device data.
    This is a simplified version of what a real VOLTTRON driver might do.
    """
    
    def __init__(self, config, **kwargs):
        super().__init__(**kwargs)
        self.config = config
        self.scrape_interval = config.get('interval', 5.0)
        self.device_address = config.get('device_address', 'device1')
        self.scrape_count = 0
        self.last_reading = None
    
    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        """Start periodic scraping when agent starts"""
        # This is where the schedule feature is used
        self.core.schedule.periodic(
            self.scrape_all,
            self.scrape_interval
        )
    
    def scrape_all(self):
        """Scrape all points from the device"""
        self.scrape_count += 1
        
        # Simulate reading from device
        reading = {
            'temperature': 72.5 + (self.scrape_count * 0.1),
            'humidity': 45.0 + (self.scrape_count * 0.2),
            'timestamp': f'2024-01-01T00:{self.scrape_count:02d}:00'
        }
        
        self.last_reading = reading
        
        # Publish the reading
        topic = f'devices/{self.device_address}/all'
        self.vip.pubsub.publish(
            'pubsub',
            topic=topic,
            message=reading
        )
        
        return reading


def test_driver_schedules_periodic_scraping():
    """Test that driver schedules periodic scraping on start"""
    # Setup
    server = TestServer()
    config = {
        'interval': 5.0,
        'device_address': 'campus/building1/device1'
    }
    driver = MockDriver(
        config=config,
        credentials=Credentials(identity="test.driver"),
        name="mock"
    )
    
    # Connect agent to server
    server.connect_agent(driver)
    
    # Trigger onstart lifecycle event
    server.trigger_start_event(driver, sender="test")
    
    # Verify periodic scraping was scheduled
    periodic_events = server.get_periodic_events(driver)
    assert len(periodic_events) == 1, "Driver should schedule one periodic event"
    
    event = periodic_events[0]
    assert event.period == 5.0, "Scrape interval should be 5 seconds"
    assert event.callback == driver.scrape_all, "Should schedule scrape_all method"
    
    print("✓ Driver scheduled periodic scraping correctly")


def test_driver_scrapes_device_data():
    """Test that driver scrapes and publishes device data"""
    # Setup
    server = TestServer()
    config = {'interval': 5.0, 'device_address': 'campus/building1/device1'}
    driver = MockDriver(
        config=config,
        credentials=Credentials(identity="test.driver"),
        name="mock"
    )
    server.connect_agent(driver)
    server.trigger_start_event(driver, sender="test")
    
    # Get the scheduled scraping event
    event = server.get_periodic_events(driver)[0]
    
    # Manually trigger scraping (instead of waiting for timer)
    reading = server.trigger_scheduled_event(event)
    
    # Verify scraping occurred
    assert driver.scrape_count == 1, "Scrape count should increment"
    assert reading is not None, "Should return reading"
    assert 'temperature' in reading, "Reading should include temperature"
    assert 'humidity' in reading, "Reading should include humidity"
    assert driver.last_reading == reading, "Last reading should be stored"
    
    # Verify data was published
    published_messages = server.get_published_messages()
    assert len(published_messages) >= 1, "Should publish at least one message"
    
    last_msg = published_messages[-1]
    assert 'campus/building1/device1/all' in last_msg.topic
    assert last_msg.message == reading
    
    print("✓ Driver scrapes and publishes data correctly")


def test_driver_multiple_scrapes():
    """Test multiple scraping cycles"""
    # Setup
    server = TestServer()
    config = {'interval': 5.0, 'device_address': 'device1'}
    driver = MockDriver(
        config=config,
        credentials=Credentials(identity="test.driver"),
        name="mock"
    )
    server.connect_agent(driver)
    server.trigger_start_event(driver, sender="test")
    
    event = server.get_periodic_events(driver)[0]
    
    # Trigger multiple scraping cycles
    readings = []
    for _ in range(3):
        reading = server.trigger_scheduled_event(event)
        readings.append(reading)
    
    # Verify all scrapes occurred
    assert driver.scrape_count == 3, "Should have scraped 3 times"
    assert event.run_count == 3, "Event should have run 3 times"
    
    # Verify temperature values increase
    temps = [r['temperature'] for r in readings]
    assert temps[0] < temps[1] < temps[2], "Temperature should increase each reading"
    
    print("✓ Multiple scraping cycles work correctly")


def test_driver_with_different_intervals():
    """Test that different scrape intervals are respected"""
    server = TestServer()
    
    # Create drivers with different intervals
    driver1 = MockDriver(
        config={'interval': 5.0, 'device_address': 'device1'},
        credentials=Credentials(identity="driver1"),
        name="mock"
    )
    driver2 = MockDriver(
        config={'interval': 10.0, 'device_address': 'device2'},
        credentials=Credentials(identity="driver2"),
        name="mock"
    )
    
    server.connect_agent(driver1)
    server.connect_agent(driver2)
    
    server.trigger_start_event(driver1, sender="test")
    server.trigger_start_event(driver2, sender="test")
    
    # Verify different intervals
    event1 = server.get_periodic_events(driver1)[0]
    event2 = server.get_periodic_events(driver2)[0]
    
    assert event1.period == 5.0, "Driver1 should have 5 second interval"
    assert event2.period == 10.0, "Driver2 should have 10 second interval"
    
    print("✓ Different scrape intervals work correctly")


def test_verify_event_scheduled_with_timeout():
    """Test the verify_event_scheduled helper"""
    server = TestServer()
    config = {'interval': 5.0, 'device_address': 'device1'}
    driver = MockDriver(
        config=config,
        credentials=Credentials(identity="test.driver"),
        name="mock"
    )
    server.connect_agent(driver)
    server.trigger_start_event(driver, sender="test")
    
    # Verify event was scheduled (should find immediately)
    found = server.verify_event_scheduled(
        driver,
        event_type='periodic',
        period=5.0,
        timeout=1.0
    )
    assert found, "Should find scheduled event"
    
    # Verify non-existent event returns False
    not_found = server.verify_event_scheduled(
        driver,
        event_type='periodic',
        period=999.0,
        timeout=0.5
    )
    assert not not_found, "Should not find non-existent event"
    
    print("✓ Event verification works correctly")


if __name__ == '__main__':
    """Run all tests"""
    print("Running driver schedule tests...\n")
    
    test_driver_schedules_periodic_scraping()
    test_driver_scrapes_device_data()
    test_driver_multiple_scrapes()
    test_driver_with_different_intervals()
    test_verify_event_scheduled_with_timeout()
    
    print("\n" + "="*60)
    print("ALL DRIVER TESTS PASSED! ✓")
    print("="*60)
