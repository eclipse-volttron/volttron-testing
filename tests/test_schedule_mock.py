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

"""Tests for schedule functionality in TestServer and MockCore"""

import gevent
from datetime import datetime, timedelta

from volttrontesting.server_mock import TestServer
from volttrontesting.mock_agent import MockAgent


def test_mock_core_has_schedule_attribute():
    """Test that MockCore has a schedule attribute after connecting to TestServer"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    
    server.connect_agent(agent)
    
    # Verify that agent.core.schedule exists
    assert hasattr(agent.core, 'schedule')
    assert agent.core.schedule is not None


def test_schedule_periodic_event():
    """Test scheduling a periodic event"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    # Track callback executions
    callback_count = []
    
    def periodic_callback():
        callback_count.append(1)
    
    # Schedule periodic event
    event = agent.core.schedule.periodic(periodic_callback, 5.0)
    
    # Verify event was registered
    assert event is not None
    assert event.event_type == 'periodic'
    assert event.period == 5.0
    assert event.callback == periodic_callback
    
    # Verify event is tracked by server
    events = server.get_scheduled_events(agent)
    assert len(events) == 1
    assert events[0] == event


def test_schedule_cron_event():
    """Test scheduling a cron event"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    def cron_callback():
        pass
    
    # Schedule cron event
    event = agent.core.schedule.cron(cron_callback, "0 * * * *")
    
    # Verify event was registered
    assert event is not None
    assert event.event_type == 'cron'
    assert event.cron_schedule == "0 * * * *"
    assert event.callback == cron_callback
    
    # Verify event is tracked by server
    events = server.get_scheduled_events(agent)
    assert len(events) == 1
    assert events[0] == event


def test_schedule_time_event():
    """Test scheduling a time-based event"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    future_time = datetime.now() + timedelta(hours=1)
    
    def time_callback():
        pass
    
    # Schedule time-based event
    event = agent.core.schedule.schedule(time_callback, future_time)
    
    # Verify event was registered
    assert event is not None
    assert event.event_type == 'time'
    assert event.scheduled_time == future_time
    assert event.callback == time_callback
    
    # Verify event is tracked by server
    events = server.get_scheduled_events(agent)
    assert len(events) == 1
    assert events[0] == event


def test_get_periodic_events():
    """Test filtering periodic events"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    # Schedule different types of events
    agent.core.schedule.periodic(lambda: None, 5.0)
    agent.core.schedule.cron(lambda: None, "0 * * * *")
    agent.core.schedule.periodic(lambda: None, 10.0)
    
    # Get only periodic events
    periodic_events = server.get_periodic_events(agent)
    assert len(periodic_events) == 2
    assert all(e.event_type == 'periodic' for e in periodic_events)


def test_get_cron_events():
    """Test filtering cron events"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    # Schedule different types of events
    agent.core.schedule.periodic(lambda: None, 5.0)
    agent.core.schedule.cron(lambda: None, "0 * * * *")
    agent.core.schedule.cron(lambda: None, "*/15 * * * *")
    
    # Get only cron events
    cron_events = server.get_cron_events(agent)
    assert len(cron_events) == 2
    assert all(e.event_type == 'cron' for e in cron_events)


def test_get_time_events():
    """Test filtering time-based events"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    future_time1 = datetime.now() + timedelta(hours=1)
    future_time2 = datetime.now() + timedelta(hours=2)
    
    # Schedule different types of events
    agent.core.schedule.periodic(lambda: None, 5.0)
    agent.core.schedule.schedule(lambda: None, future_time1)
    agent.core.schedule.schedule(lambda: None, future_time2)
    
    # Get only time-based events
    time_events = server.get_time_events(agent)
    assert len(time_events) == 2
    assert all(e.event_type == 'time' for e in time_events)


def test_trigger_scheduled_event():
    """Test manually triggering a scheduled event"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    # Track callback execution
    callback_data = []
    
    def callback_with_args(value, multiplier=2):
        result = value * multiplier
        callback_data.append(result)
        return result
    
    # Schedule event with args and kwargs
    event = agent.core.schedule.periodic(callback_with_args, 5.0, 10, multiplier=3)
    
    # Manually trigger the event
    result = server.trigger_scheduled_event(event)
    
    # Verify callback was executed with correct args
    assert result == 30
    assert callback_data == [30]
    assert event.run_count == 1
    assert event.last_run is not None


def test_trigger_multiple_events():
    """Test triggering multiple scheduled events"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    execution_order = []
    
    def callback1():
        execution_order.append(1)
    
    def callback2():
        execution_order.append(2)
    
    def callback3():
        execution_order.append(3)
    
    # Schedule multiple events
    event1 = agent.core.schedule.periodic(callback1, 5.0)
    event2 = agent.core.schedule.periodic(callback2, 10.0)
    event3 = agent.core.schedule.cron(callback3, "0 * * * *")
    
    # Trigger events in specific order
    server.trigger_scheduled_event(event2)
    server.trigger_scheduled_event(event1)
    server.trigger_scheduled_event(event3)
    
    # Verify execution order
    assert execution_order == [2, 1, 3]
    assert event1.run_count == 1
    assert event2.run_count == 1
    assert event3.run_count == 1


def test_verify_event_scheduled():
    """Test verifying that an event has been scheduled"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    # Schedule a periodic event
    agent.core.schedule.periodic(lambda: None, 5.0)
    
    # Verify event was scheduled
    assert server.verify_event_scheduled(agent, event_type='periodic', period=5.0)
    
    # Verify non-existent event returns False
    assert not server.verify_event_scheduled(agent, event_type='periodic', period=10.0)


def test_verify_cron_event_scheduled():
    """Test verifying a cron event is scheduled"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    # Schedule a cron event
    agent.core.schedule.cron(lambda: None, "0 * * * *")
    
    # Verify event was scheduled
    assert server.verify_event_scheduled(agent, event_type='cron', cron_schedule="0 * * * *")
    
    # Verify non-existent cron schedule returns False
    assert not server.verify_event_scheduled(agent, event_type='cron', cron_schedule="*/15 * * * *")


def test_event_with_exception():
    """Test that exceptions in callbacks are handled"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    def failing_callback():
        raise ValueError("Test error")
    
    event = agent.core.schedule.periodic(failing_callback, 5.0)
    
    # Trigger should raise the exception
    try:
        server.trigger_scheduled_event(event)
        assert False, "Expected exception was not raised"
    except ValueError as e:
        assert str(e) == "Test error"
        assert event.run_count == 1


def test_run_scheduled_event_with_greenlet():
    """Test running a scheduled event in a greenlet"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    callback_executed = []
    
    def callback():
        callback_executed.append(True)
    
    event = agent.core.schedule.periodic(callback, 5.0)
    
    # Run event with delay
    greenlet = server.run_scheduled_event_with_greenlet(event, delay=0.1)
    
    # Wait for greenlet to complete
    greenlet.join(timeout=1.0)
    
    # Verify callback was executed
    assert callback_executed == [True]
    assert event.run_count == 1


def test_cancel_scheduled_event():
    """Test cancelling a scheduled event"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    event = agent.core.schedule.periodic(lambda: None, 5.0)
    
    # Cancel the event
    event.cancelled = True
    
    # Verify cancelled events are filtered out
    periodic_events = server.get_periodic_events(agent)
    assert len(periodic_events) == 0
    
    # Verify triggering cancelled event raises error
    try:
        server.trigger_scheduled_event(event)
        assert False, "Expected ValueError was not raised"
    except ValueError as e:
        assert "cancelled" in str(e).lower()


def test_multiple_agents_with_schedules():
    """Test that multiple agents can have independent schedules"""
    server = TestServer()
    agent1 = MockAgent(identity="agent1")
    agent2 = MockAgent(identity="agent2")
    
    server.connect_agent(agent1)
    server.connect_agent(agent2)
    
    # Schedule events for each agent
    agent1.core.schedule.periodic(lambda: None, 5.0)
    agent1.core.schedule.periodic(lambda: None, 10.0)
    agent2.core.schedule.cron(lambda: None, "0 * * * *")
    
    # Verify each agent has their own events
    events1 = server.get_scheduled_events(agent1)
    events2 = server.get_scheduled_events(agent2)
    
    assert len(events1) == 2
    assert len(events2) == 1
    assert all(e.event_type == 'periodic' for e in events1)
    assert events2[0].event_type == 'cron'


def test_schedule_with_identity_string():
    """Test that methods work with both agent and identity string"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    agent.core.schedule.periodic(lambda: None, 5.0)
    
    # Test with agent object
    events_by_agent = server.get_scheduled_events(agent)
    assert len(events_by_agent) == 1
    
    # Test with identity string
    events_by_identity = server.get_scheduled_events("test_agent")
    assert len(events_by_identity) == 1
    assert events_by_agent == events_by_identity


def test_event_run_count_tracking():
    """Test that run count is properly tracked"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    counter = []
    
    def callback():
        counter.append(1)
    
    event = agent.core.schedule.periodic(callback, 5.0)
    
    assert event.run_count == 0
    
    # Trigger multiple times
    for i in range(5):
        server.trigger_scheduled_event(event)
    
    assert event.run_count == 5
    assert len(counter) == 5


def test_schedule_callable_direct():
    """Test that ScheduleWrapper is callable for direct scheduling"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    callback_executed = []
    
    def callback(value):
        callback_executed.append(value)
        return value * 2
    
    # Call schedule directly with delay
    event = agent.core.schedule(callback, 2.0, 10)
    
    # Verify event was created
    assert event is not None
    assert event.event_type == 'time'
    assert event.callback == callback
    assert event.args == (10,)
    
    # Verify event is tracked
    events = server.get_time_events(agent)
    assert len(events) == 1
    assert events[0] == event
    
    # Trigger the event
    result = server.trigger_scheduled_event(event)
    assert result == 20
    assert callback_executed == [10]


def test_schedule_callable_immediate():
    """Test scheduling with zero delay (immediate execution in tests)"""
    server = TestServer()
    agent = MockAgent(identity="test_agent")
    server.connect_agent(agent)
    
    executed = []
    
    def callback():
        executed.append(True)
    
    # Schedule with default delay (0)
    event = agent.core.schedule(callback)
    
    assert event is not None
    assert event.event_type == 'time'
    
    # Manually trigger
    server.trigger_scheduled_event(event)
    assert executed == [True]
