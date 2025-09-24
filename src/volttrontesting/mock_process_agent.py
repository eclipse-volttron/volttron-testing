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
Process-based mock agent testing for more realistic isolation.
"""

from __future__ import annotations

import multiprocessing as mp
from multiprocessing import Process, Queue, Pipe
import pickle
import logging
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
import time

_log = logging.getLogger(__name__)


@dataclass
class AgentMessage:
    """Message passed between test process and agent process"""
    msg_type: str  # 'publish', 'subscribe', 'rpc', 'control'
    data: Dict[str, Any]


class MockAgentProcess:
    """
    Runs a mock agent in a separate process for realistic testing.
    
    This provides:
    - Process isolation
    - Separate memory space
    - More realistic IPC
    - Better crash isolation
    """
    
    def __init__(self, identity: str, agent_class: type = None):
        self.identity = identity
        self.agent_class = agent_class
        
        # IPC mechanisms
        self.parent_conn, self.child_conn = Pipe()
        self.message_queue = Queue()
        self.result_queue = Queue()
        
        # Process management
        self.process: Optional[Process] = None
        self.running = False
        
    def start(self):
        """Start the agent in a separate process"""
        if self.running:
            return
            
        self.process = Process(
            target=self._agent_process_main,
            args=(self.identity, self.child_conn, self.message_queue, self.result_queue)
        )
        self.process.start()
        self.running = True
        _log.info(f"Started agent process {self.identity} with PID {self.process.pid}")
        
    def stop(self):
        """Stop the agent process"""
        if not self.running:
            return
            
        # Send shutdown message
        self.send_control_message("shutdown")
        
        # Wait for graceful shutdown
        self.process.join(timeout=5.0)
        
        # Force terminate if still running
        if self.process.is_alive():
            _log.warning(f"Force terminating agent {self.identity}")
            self.process.terminate()
            self.process.join(timeout=2.0)
            
            if self.process.is_alive():
                self.process.kill()
                
        self.running = False
        _log.info(f"Stopped agent process {self.identity}")
        
    def publish(self, topic: str, headers: Optional[Dict] = None, message: Any = None):
        """Publish a message from the agent"""
        msg = AgentMessage(
            msg_type="publish",
            data={"topic": topic, "headers": headers, "message": message}
        )
        self.message_queue.put(msg)
        
    def subscribe(self, pattern: str, callback_name: str):
        """Subscribe to a topic pattern"""
        msg = AgentMessage(
            msg_type="subscribe",
            data={"pattern": pattern, "callback": callback_name}
        )
        self.message_queue.put(msg)
        
    def call_rpc(self, method: str, *args, **kwargs):
        """Call an RPC method on the agent"""
        msg = AgentMessage(
            msg_type="rpc",
            data={"method": method, "args": args, "kwargs": kwargs}
        )
        self.message_queue.put(msg)
        
        # Wait for result
        try:
            result = self.result_queue.get(timeout=5.0)
            return result
        except:
            return None
            
    def send_control_message(self, command: str, data: Any = None):
        """Send a control message to the agent process"""
        msg = AgentMessage(
            msg_type="control",
            data={"command": command, "data": data}
        )
        self.message_queue.put(msg)
        
    def get_published_messages(self) -> list:
        """Get messages published by the agent"""
        messages = []
        while not self.result_queue.empty():
            try:
                msg = self.result_queue.get_nowait()
                if isinstance(msg, dict) and msg.get("type") == "published":
                    messages.append(msg["data"])
            except:
                break
        return messages
        
    @staticmethod
    def _agent_process_main(identity: str, conn, message_queue: Queue, result_queue: Queue):
        """Main loop running in the agent process"""
        _log.info(f"Agent process {identity} starting")
        
        # Create the agent in this process
        from volttrontesting.mock_agent import MockAgent
        agent = MockAgent(identity=identity)
        
        # Track published messages
        published_messages = []
        
        # Override publish to track messages
        original_publish = agent.vip.pubsub.publish
        def tracking_publish(peer, topic, headers=None, message=None, bus=''):
            result = original_publish(peer, topic, headers, message, bus)
            published_messages.append({
                "topic": topic,
                "headers": headers,
                "message": message
            })
            # Send back to parent process
            result_queue.put({
                "type": "published",
                "data": {"topic": topic, "headers": headers, "message": message}
            })
            return result
        agent.vip.pubsub.publish = tracking_publish
        
        # Main message processing loop
        running = True
        while running:
            try:
                # Check for messages (non-blocking)
                if not message_queue.empty():
                    msg = message_queue.get_nowait()
                    
                    if msg.msg_type == "control":
                        if msg.data["command"] == "shutdown":
                            running = False
                            break
                            
                    elif msg.msg_type == "publish":
                        data = msg.data
                        agent.vip.pubsub.publish(
                            "pubsub",
                            data["topic"],
                            headers=data.get("headers"),
                            message=data.get("message")
                        )
                        
                    elif msg.msg_type == "subscribe":
                        data = msg.data
                        # Would implement subscription logic
                        pass
                        
                    elif msg.msg_type == "rpc":
                        data = msg.data
                        # Would implement RPC call logic
                        result = f"RPC {data['method']} called"
                        result_queue.put(result)
                        
                time.sleep(0.01)  # Small delay to prevent CPU spinning
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                _log.error(f"Error in agent process: {e}")
                
        _log.info(f"Agent process {identity} shutting down")


class ProcessModeTestingContext:
    """
    Testing context that runs agents in separate processes.
    
    Provides more realistic testing with process isolation.
    """
    
    def __init__(self, use_processes: bool = True):
        self.use_processes = use_processes
        self.agents: Dict[str, MockAgentProcess] = {}
        
    def create_agent(self, identity: str, agent_class: type = None) -> MockAgentProcess:
        """Create and start an agent in a separate process"""
        if self.use_processes:
            agent = MockAgentProcess(identity, agent_class)
            agent.start()
            self.agents[identity] = agent
            return agent
        else:
            # Fall back to in-process mock
            from volttrontesting.mock_agent import MockAgent
            agent = MockAgent(identity)
            self.agents[identity] = agent
            return agent
            
    def cleanup(self):
        """Stop all agent processes"""
        for agent in self.agents.values():
            if isinstance(agent, MockAgentProcess):
                agent.stop()
        self.agents.clear()
        

# Example usage
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # Create context with process isolation
    context = ProcessModeTestingContext(use_processes=True)
    
    try:
        # Create agents in separate processes
        agent1 = context.create_agent("agent1")
        agent2 = context.create_agent("agent2")
        
        # Test publishing
        agent1.publish("test/topic", message="Hello from process")
        
        # Give time for message to process
        time.sleep(0.5)
        
        # Get published messages
        messages = agent1.get_published_messages()
        print(f"Published messages: {messages}")
        
        # Test RPC
        result = agent1.call_rpc("test_method", "arg1", key="value")
        print(f"RPC result: {result}")
        
    finally:
        context.cleanup()
        print("Test completed")