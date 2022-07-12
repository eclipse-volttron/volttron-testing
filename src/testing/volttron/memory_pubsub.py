from __future__ import annotations

from dataclasses import dataclass
from queue import Queue
from typing import List, Optional, Dict, Any, Callable, Pattern, AnyStr
import re


@dataclass
class PublishedMessage:
    headers: Optional[Dict[str, str]]
    message: Optional[Any]
    topic: str
    bus: Optional[str]


class MemorySubscriber:
    def __init__(self, callback: Callable):
        self._received_messages: List[PublishedMessage] = []
        self._callback = callback

    def get(self, timeout=0) -> MemorySubscriber:
        """
        Noop for handling the Async results from the server.

        :param timeout:
        :return:
        """
        return self

    def received_messages(self) -> List[PublishedMessage]:
        return self._received_messages

    @property
    def callback(self):
        return self._callback

    def reset_received_messages(self):
        self._received_messages.clear()


class MemoryPubSub:
    def __init__(self):
        self._pubsub_subscribers: Dict[Pattern[AnyStr], List[MemorySubscriber]] = {}
        self._messages: List[PublishedMessage] = []

    @property
    def published_messages(self) -> List[PublishedMessage]:
        return self._messages

    def get(self, timeout=0) -> MemoryPubSub:
        """
        Noop for AsyncResults wrapper
        :param timeout:
        :return:
        """
        return self

    def publish(self, topic: str, headers: Optional[Dict[str, Any]] = None, message: Optional[Any] = None,
                bus: str = ''):
        self._messages.append(PublishedMessage(topic=topic, headers=headers, message=message, bus=bus))
        for pattern, subscribers in self._pubsub_subscribers.items():
            if re.match(pattern, topic):
                for sub in subscribers:
                    sub.received_messages().append(PublishedMessage(topic=topic,
                                                                    headers=headers, message=message, bus=bus))
                    if sub.callback:
                        sub.callback(topic, headers, message, bus)
        return self

    def subscribe(self, prefix: str, callback: Optional[Callable] = None) -> MemorySubscriber:
        prefix = re.compile(prefix)
        subscriber = MemorySubscriber(callback)
        if self._pubsub_subscribers.get(prefix):
            self._pubsub_subscribers[prefix].append(subscriber)
        else:
            self._pubsub_subscribers[prefix] = [subscriber]

        return subscriber
