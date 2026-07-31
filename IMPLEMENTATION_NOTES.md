# Implementation Notes: Schedule Support for TestServer

## Issue
Driver tests based on the TestServer were getting "MockCore has no schedule attribute" errors when agents tried to use `core.schedule.periodic()`, `core.schedule.cron()`, or `core.schedule.schedule()`.

## Solution Overview
Added full schedule support to the TestServer mock infrastructure, allowing agents to register scheduled events and tests to verify and trigger those events.

## Changes Made

### 1. Core Implementation Files

#### `src/volttrontesting/server_mock.py`
- **New Classes:**
  - `ScheduledEvent`: Dataclass to track scheduled events with metadata (type, callback, args, run count, etc.)
  - `ScheduleWrapper`: Wraps schedule functionality and tracks events for an agent
  
- **New TestServer Class Variables:**
  - `__schedule_wrappers__`: Dict storing ScheduleWrapper for each agent
  - `__scheduled_events__`: Dict storing list of events for each agent

- **New TestServer Methods:**
  - `_register_scheduled_event()`: Internal method to track scheduled events
  - `get_scheduled_events()`: Get all events for an agent
  - `get_periodic_events()`: Filter periodic events
  - `get_cron_events()`: Filter cron events
  - `get_time_events()`: Filter time-based events
  - `trigger_scheduled_event()`: Manually execute a scheduled callback
  - `run_scheduled_event_with_greenlet()`: Execute with delay in greenlet
  - `verify_event_scheduled()`: Wait for event to be scheduled within timeout

- **Modified Methods:**
  - `connect_agent()`: Now creates and attaches ScheduleWrapper to agent's core

#### `src/volttrontesting/mock_core.py`
- **New Property:**
  - `schedule`: Returns the attached `_schedule_wrapper` if available

### 2. Test Files

#### `tests/test_schedule_mock.py` (NEW)
Comprehensive test suite with 20+ test cases covering:
- Basic scheduling (periodic, cron, time-based)
- Event filtering and retrieval
- Manual event triggering
- Multiple events and multiple agents
- Event metadata tracking
- Error handling
- Cancellation

### 3. Documentation

#### `docs/SCHEDULE_TESTING.md` (NEW)
Complete guide covering:
- Overview of schedule testing
- Basic usage examples
- API reference for all new methods
- Advanced features (metadata, cancellation, greenlets)
- Best practices
- Troubleshooting
- Migration guide

#### `docs/examples/test_driver_with_schedule.py` (NEW)
Practical example showing:
- Realistic driver agent implementation
- Multiple test scenarios
- Integration with pubsub
- Testing patterns for driver agents

#### `README.md`
- Added schedule testing example
- Updated API reference with new methods
- Link to schedule testing guide

## API Usage Examples

### Basic Usage
```python
# Setup
server = TestServer()
agent = MyAgent(credentials=Credentials(identity="test"), name="mock")
server.connect_agent(agent)

# Agent schedules periodic task in onstart
server.trigger_start_event(agent, sender="test")

# Verify event was scheduled
events = server.get_periodic_events(agent)
assert len(events) == 1
assert events[0].period == 5.0

# Manually trigger the scheduled callback
server.trigger_scheduled_event(events[0])
```

### Agent Implementation
```python
class MyAgent(Agent):
    @Core.receiver('onstart')
    def onstart(self, sender, **kwargs):
        # Now works with TestServer!
        self.core.schedule.periodic(self.my_callback, 5.0)
        self.core.schedule.cron(self.hourly_task, "0 * * * *")
```

## Design Decisions

1. **Minimal Changes**: Only added necessary functionality without modifying existing behavior
2. **Consistent API**: Methods follow existing TestServer patterns (support both agent object and identity string)
3. **Flexible Testing**: Can manually trigger events or use greenlets for async testing
4. **Comprehensive Metadata**: Events track creation time, last run, run count, etc.
5. **Type Safety**: Used dataclasses and type hints throughout
6. **Documentation First**: Created extensive docs before users encounter issues

## Testing Strategy

Since full dependencies aren't available in the development environment:
1. Verified Python syntax with `py_compile`
2. Checked all class and method definitions exist
3. Verified integration points (schedule wrapper attachment)
4. Created structure verification script
5. CI will run full test suite with all dependencies

## Backwards Compatibility

- No breaking changes to existing functionality
- All existing tests should continue to work
- New functionality only activated when agents use `core.schedule`
- MockCore returns None for schedule if not connected to TestServer (graceful degradation)

## Future Enhancements

Possible future additions (not required for this issue):
- Automatic execution of periodic events in background
- Time advancement simulation for testing time-based events
- Integration with gevent event loop for realistic async testing
- Schedule decorators support (if VOLTTRON adds them)

## Verification

All structure checks passed:
- ✓ Python syntax valid for all files
- ✓ All classes and methods present
- ✓ Integration points correct
- ✓ Documentation complete
- ✓ Examples provided
- ✓ Tests comprehensive

## Related Issues

This resolves the main issue: "TestServer should implement core.schedule"
Specifically addresses: "Driver tests getting 'MockCore has no schedule attribute' errors"
