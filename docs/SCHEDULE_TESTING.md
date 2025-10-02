# Testing Scheduled Events with TestServer

This guide explains how to test agents that use VOLTTRON's scheduling features (`core.schedule`) with the TestServer mock infrastructure.

## Overview

The TestServer now supports testing agents that use scheduled events, including:
- **Periodic events** - callbacks that run at regular intervals
- **Cron events** - callbacks that run on a cron schedule
- **Time-based events** - callbacks that run at a specific time

## Basic Usage

### 1. Scheduling Events in Your Agent

Your agent can schedule events as normal:

```python
from volttron.client import Agent
from volttron.client.vip.agent import Core

class MyAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.periodic_count = 0
    
    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        # Schedule a periodic callback every 5 seconds
        self.core.schedule.periodic(self.periodic_task, 5.0)
        
        # Schedule a cron job
        self.core.schedule.cron(self.hourly_task, "0 * * * *")
        
        # Schedule a one-time event
        from datetime import datetime, timedelta
        future_time = datetime.now() + timedelta(minutes=30)
        self.core.schedule.schedule(self.future_task, future_time)
    
    def periodic_task(self):
        """Called every 5 seconds"""
        self.periodic_count += 1
        print(f"Periodic task executed {self.periodic_count} times")
    
    def hourly_task(self):
        """Called every hour on the hour"""
        print("Hourly task executed")
    
    def future_task(self):
        """Called at the scheduled time"""
        print("Future task executed")
```

### 2. Testing Scheduled Events

```python
from volttrontesting.server_mock import TestServer
from volttron.types.auth.auth_credentials import Credentials

def test_agent_scheduling():
    # Create test server and agent
    server = TestServer()
    agent = MyAgent(credentials=Credentials(identity="test_agent"), name="mock")
    server.connect_agent(agent)
    
    # Trigger the agent's onstart to set up schedules
    server.trigger_start_event(agent, sender="test")
    
    # Verify events were scheduled
    periodic_events = server.get_periodic_events(agent)
    assert len(periodic_events) == 1
    assert periodic_events[0].period == 5.0
    
    cron_events = server.get_cron_events(agent)
    assert len(cron_events) == 1
    assert cron_events[0].cron_schedule == "0 * * * *"
    
    time_events = server.get_time_events(agent)
    assert len(time_events) == 1
```

## TestServer API for Scheduled Events

### Getting Scheduled Events

```python
# Get all scheduled events for an agent
all_events = server.get_scheduled_events(agent)

# Filter by event type
periodic_events = server.get_periodic_events(agent)
cron_events = server.get_cron_events(agent)
time_events = server.get_time_events(agent)
```

### Verifying Events are Scheduled

```python
# Wait up to 5 seconds for an event to be scheduled
assert server.verify_event_scheduled(
    agent,
    event_type='periodic',
    period=5.0,
    timeout=5.0
)

# Verify a cron schedule
assert server.verify_event_scheduled(
    agent,
    event_type='cron',
    cron_schedule="0 * * * *"
)
```

### Manually Triggering Events

```python
# Get the scheduled event
events = server.get_periodic_events(agent)
event = events[0]

# Manually trigger the callback
result = server.trigger_scheduled_event(event)

# Verify the event was executed
assert event.run_count == 1
assert event.last_run is not None
```

### Running Events with Greenlets

For testing asynchronous behavior:

```python
import gevent

# Run an event in a greenlet with a delay
event = server.get_periodic_events(agent)[0]
greenlet = server.run_scheduled_event_with_greenlet(event, delay=0.5)

# Wait for completion
greenlet.join(timeout=2.0)

# Verify execution
assert event.run_count == 1
```

## Complete Example

```python
from volttrontesting.server_mock import TestServer
from volttron.client import Agent
from volttron.client.vip.agent import Core
from volttron.types.auth.auth_credentials import Credentials
import gevent

class DataAggregator(Agent):
    """Agent that aggregates data periodically"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_buffer = []
        self.aggregation_results = []
    
    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        # Aggregate data every 10 seconds
        self.core.schedule.periodic(self.aggregate_data, 10.0)
    
    def add_data(self, value):
        """Add data to buffer"""
        self.data_buffer.append(value)
    
    def aggregate_data(self):
        """Aggregate buffered data"""
        if self.data_buffer:
            avg = sum(self.data_buffer) / len(self.data_buffer)
            self.aggregation_results.append(avg)
            self.data_buffer = []
            return avg
        return None


def test_data_aggregator():
    """Test the data aggregator agent"""
    # Setup
    server = TestServer()
    agent = DataAggregator(
        credentials=Credentials(identity="aggregator"),
        name="mock"
    )
    server.connect_agent(agent)
    
    # Start the agent (sets up schedules)
    server.trigger_start_event(agent, sender="test")
    
    # Verify periodic task was scheduled
    assert server.verify_event_scheduled(
        agent,
        event_type='periodic',
        period=10.0
    )
    
    # Simulate data collection
    agent.add_data(10)
    agent.add_data(20)
    agent.add_data(30)
    
    # Get the scheduled event
    events = server.get_periodic_events(agent)
    aggregation_event = events[0]
    
    # Manually trigger aggregation
    result = server.trigger_scheduled_event(aggregation_event)
    
    # Verify aggregation occurred
    assert result == 20.0  # Average of 10, 20, 30
    assert agent.aggregation_results == [20.0]
    assert len(agent.data_buffer) == 0  # Buffer cleared
    assert aggregation_event.run_count == 1


def test_multiple_aggregations():
    """Test multiple aggregation cycles"""
    server = TestServer()
    agent = DataAggregator(
        credentials=Credentials(identity="aggregator"),
        name="mock"
    )
    server.connect_agent(agent)
    server.trigger_start_event(agent, sender="test")
    
    event = server.get_periodic_events(agent)[0]
    
    # First cycle
    agent.add_data(5)
    agent.add_data(15)
    server.trigger_scheduled_event(event)
    
    # Second cycle
    agent.add_data(10)
    agent.add_data(20)
    server.trigger_scheduled_event(event)
    
    # Third cycle
    agent.add_data(100)
    server.trigger_scheduled_event(event)
    
    # Verify all aggregations
    assert agent.aggregation_results == [10.0, 15.0, 100.0]
    assert event.run_count == 3
```

## Advanced Features

### Event Metadata

Each `ScheduledEvent` tracks metadata:

```python
event = server.get_periodic_events(agent)[0]

# Check when event was created
print(f"Created at: {event.created_at}")

# Check last execution time
server.trigger_scheduled_event(event)
print(f"Last run: {event.last_run}")

# Check execution count
print(f"Run count: {event.run_count}")

# Check if cancelled
print(f"Cancelled: {event.cancelled}")
```

### Cancelling Events

```python
# Cancel a specific event
event.cancelled = True

# Verify cancelled events are filtered out
active_events = server.get_periodic_events(agent)
assert event not in active_events

# Cancel all events for an agent
schedule_wrapper = server._TestServer__schedule_wrappers__[agent.core.identity]
schedule_wrapper.cancel_all()
```

### Testing Event Arguments

```python
class Agent(Agent):
    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        # Schedule with arguments
        self.core.schedule.periodic(
            self.callback_with_args,
            5.0,
            "arg1", "arg2",
            kwarg1="value1"
        )
    
    def callback_with_args(self, arg1, arg2, kwarg1=None):
        return f"{arg1}-{arg2}-{kwarg1}"


def test_event_arguments():
    server = TestServer()
    agent = MyAgent(credentials=Credentials(identity="test"), name="mock")
    server.connect_agent(agent)
    server.trigger_start_event(agent, sender="test")
    
    event = server.get_periodic_events(agent)[0]
    
    # Verify arguments are stored
    assert event.args == ("arg1", "arg2")
    assert event.kwargs == {"kwarg1": "value1"}
    
    # Trigger and verify result
    result = server.trigger_scheduled_event(event)
    assert result == "arg1-arg2-value1"
```

## Best Practices

1. **Always trigger lifecycle events**: Call `server.trigger_start_event()` to ensure scheduled events are registered.

2. **Verify schedules before testing**: Use `verify_event_scheduled()` to confirm events are properly set up.

3. **Test event logic independently**: Manually trigger events to test callback logic without waiting for actual timers.

4. **Track execution counts**: Use `event.run_count` to verify callbacks are being executed.

5. **Handle exceptions**: Wrap callback execution in try/except to handle errors gracefully.

6. **Use greenlets for async testing**: Use `run_scheduled_event_with_greenlet()` when testing async behavior.

## Troubleshooting

### "MockCore has no schedule attribute"

Ensure you're connecting the agent to the TestServer:
```python
server.connect_agent(agent)
```

### Events not being scheduled

Make sure to trigger the appropriate lifecycle event:
```python
server.trigger_start_event(agent, sender="test")
```

### Events not executing

Remember to manually trigger events in tests:
```python
event = server.get_periodic_events(agent)[0]
server.trigger_scheduled_event(event)
```

## Migration Guide

If you're updating tests from previous versions:

**Before:**
```python
# Tests would fail with "MockCore has no schedule attribute"
agent.core.schedule.periodic(callback, 5.0)  # Error!
```

**After:**
```python
# Connect agent to server to enable schedule
server = TestServer()
agent = MyAgent(credentials=Credentials(identity="test"), name="mock")
server.connect_agent(agent)

# Now schedule works
agent.core.schedule.periodic(callback, 5.0)  # Success!
```
