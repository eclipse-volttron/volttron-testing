from datetime import datetime
from unittest import mock

import gevent
import pytest
from mock import MagicMock, patch

from volttron.client.messaging import headers as headers_mod
from volttron.client.vip.agent import Agent
from volttron.client.vip.agent import PubSub
from volttron.utils import format_timestamp
from volttrontesting.utils import (poll_gevent_sleep,
                                   messages_contains_prefix)


class _publish_from_handler_test_agent(Agent):
    def __init__(self, **kwargs):
        super(_publish_from_handler_test_agent, self).__init__(**kwargs)
        self.subscription_results = {}
        PubSub.subscribe('pubsub', '')

    @PubSub.subscribe('pubsub', '')
    def onmessage(self, peer, sender, bus, topic, headers, message):
        self.subscription_results[topic] = {'headers': headers,
                                            'message': message}
        if not topic.startswith("testtopic2/test"):
            self.vip.pubsub.publish("pubsub", "testtopic2/test",
                                    headers={"foo": "bar"},
                                    message="Test message").get(timeout=2.0)

    def setup_callback(self, topic):
        self.vip.pubsub.subscribe(peer="pubsub", prefix=topic,
                                  callback=self.onmessage).get(timeout=2.0)

    def reset_results(self):
        self.subscription_results = {}



@pytest.mark.pubsub
def test_publish_from_message_handler(volttron_instance):
    """ Tests the ability to change a status by sending a different status
    code.

    This test also tests that the heartbeat is received.

    :param volttron_instance:
    :return:
    """
    test_topic = "testtopic1/test"
    new_agent1 = volttron_instance.build_agent(identity='test_publish1',
                                               agent_class=_publish_from_handler_test_agent)

    new_agent2 = volttron_instance.build_agent(identity='test_publish2')

    # new_agent1.setup_callback("")

    new_agent2.vip.pubsub.publish("pubsub", test_topic, headers={},
                                  message="Test message").get()

    poll_gevent_sleep(2, lambda: messages_contains_prefix(test_topic,
                                                          new_agent1.subscription_results))

    assert new_agent1.subscription_results[test_topic][
               "message"] == "Test message"


@pytest.mark.pubsub
def test_multi_unsubscribe(volttron_instance):
    subscriber_agent = volttron_instance.build_agent()
    subscriber_agent.subscription_callback = MagicMock(
        callback='subscription_callback')
    subscriber_agent.subscription_callback.reset_mock()

    # test unsubscribe all when there are no subscriptions
    subscriber_agent.vip.pubsub.unsubscribe("pubsub", prefix=None,
                                            callback=None)

    publisher_agent = volttron_instance.build_agent()

    topic_to_check = "testtopic1/test/foo/bar/one"
    test_topic1 = "testtopic1/test/foo/bar"
    test_topic2 = "testtopic1/test/foo"
    test_topic3 = "testtopic1"

    subscriber_agent.vip.pubsub.subscribe(
        peer='pubsub', prefix=test_topic1,
        callback=subscriber_agent.subscription_callback)
    subscriber_agent.vip.pubsub.subscribe(
        peer='pubsub', prefix=test_topic2,
        callback=subscriber_agent.subscription_callback)
    subscriber_agent.vip.pubsub.subscribe(
        peer='pubsub', prefix=test_topic3,
        callback=subscriber_agent.subscription_callback)
    gevent.sleep(1)

    publisher_agent.vip.pubsub.publish(peer="pubsub", topic=topic_to_check,
                                       message="test message 1")
    gevent.sleep(1)

    assert subscriber_agent.subscription_callback.call_count == 3
    subscriber_agent.subscription_callback.reset_mock()

    subscriber_agent.vip.pubsub.unsubscribe(peer='pubsub',
                                            prefix="testtopic1/test/foo/bar",
                                            callback=None)
    gevent.sleep(1)

    publisher_agent.vip.pubsub.publish(peer="pubsub", topic=topic_to_check,
                                       message="test message 2")
    gevent.sleep(1)

    assert subscriber_agent.subscription_callback.call_count == 2
    subscriber_agent.subscription_callback.reset_mock()

    subscriber_agent.vip.pubsub.unsubscribe("pubsub", prefix=None,
                                            callback=None)
    gevent.sleep(1)

    publisher_agent.vip.pubsub.publish(peer="pubsub", topic=topic_to_check,
                                       message="test message 3")
    gevent.sleep(1)

    assert subscriber_agent.subscription_callback.call_count == 0


class TestAgent(Agent):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.subscription_results = dict()
        self.instance_subscription_results = dict()

    @PubSub.subscribe_by_tags('pubsub', 'condition_devices', all_platforms=True)
    def on_match(self, peer, sender, bus, topic, headers, message):
        print("on_match")
        self.subscription_results[topic] = {'headers': headers,
                                            'message': message}

    def callback_method(self, peer, sender, bus, topic, headers, message):
        self.instance_subscription_results[topic] = {'headers': headers,
                                                     'message': message}

    def reset_results(self):
        self.subscription_results = dict()
        self.instance_subscription_results = dict()


@pytest.fixture(scope="module")
def test_agents(volttron_instance):
    with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
        mock_tag_method.return_value = ["devices/campus/b1/device_test_class_method"]
        pub_agent = volttron_instance.build_agent()
        agent = volttron_instance.build_agent(identity="test-agent", agent_class=TestAgent)
        gevent.sleep(0.5)
        yield pub_agent, agent
    pub_agent.core.stop()
    agent.core.stop()


all_message = [{'OutsideAirTemperature': 0.5,
                'MixedAirTemperature': 0.2},
               {'OutsideAirTemperature': {'units': 'F', 'tz': 'UTC', 'type': 'float'},
                'MixedAirTemperature': {'units': 'F', 'tz': 'UTC', 'type': 'float'}
                }]
now = format_timestamp(datetime.utcnow())
headers = {
            headers_mod.DATE: now,
            headers_mod.TIMESTAMP: now
        }


def test_subscribe_by_tags_class_method(volttron_instance, test_agents, mocker):
    pub_agent, agent = test_agents
    try:
        # TestAgent subscribes to "devices/campus/b1/device_test_class_method" tag condition using the
        # @Pusbub.subscribe_by_tags decorator

        # publish to devices and check agent.subscription_results
        # Publish messages
        pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b1/device_test_class_method/all",
                                     headers, all_message).get(timeout=10)
        pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b1/d2/all", headers, all_message).get(timeout=10)
        gevent.sleep(0.5)

        assert agent.subscription_results["devices/campus/b1/device_test_class_method/all"]["headers"] == headers
        assert agent.subscription_results["devices/campus/b1/device_test_class_method/all"]["message"] == all_message
        assert agent.subscription_results.get("devices/campus/b1/d2/all") is None
    finally:
        agent.reset_results()


def test_subscribe_by_tags_instance_method(volttron_instance, test_agents):
    pub_agent, agent = test_agents
    try:
        with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
            mock_tag_method.return_value = ["devices/campus/b2/d2"]

            # Subscribe to subscribe_by_tags instance method and check result
            agent.vip.pubsub.subscribe_by_tags('pubsub', "condition_devices/campus/b2/d2", agent.callback_method)

            # Publish messages
            pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b1/device_test_class_method/all", headers,
                                         all_message).get(timeout=10)
            pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d2/all", headers, all_message).get(timeout=10)
            gevent.sleep(0.5)

            assert agent.subscription_results["devices/campus/b1/device_test_class_method/all"]["headers"] == headers
            assert agent.subscription_results["devices/campus/b1/device_test_class_method/all"][
                       "message"] == all_message

            assert agent.instance_subscription_results["devices/campus/b2/d2/all"]["headers"] == headers
            assert agent.instance_subscription_results["devices/campus/b2/d2/all"]["message"] == all_message
    finally:
        agent.reset_results()


def test_subscribe_by_tags_refresh_tags(volttron_instance, test_agents):
    pub_agent, test_agent = test_agents
    agent = volttron_instance.build_agent(identity="test-agent-2", agent_class=TestAgent, tag_refresh_interval=5)
    gevent.sleep(0.5)
    try:
        with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
            mock_tag_method.return_value = ["devices/campus/b2/d2"]

            # Subscribe to subscribe_by_tags instance method and check result
            agent.vip.pubsub.subscribe_by_tags('pubsub', "condition_devices/campus/b2", agent.callback_method)
            gevent.sleep(0.5)
            # Publish messages
            pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d2/all", headers, all_message).get(timeout=10)
            gevent.sleep(0.5)

            assert agent.instance_subscription_results["devices/campus/b2/d2/all"]["headers"] == headers
            assert agent.instance_subscription_results["devices/campus/b2/d2/all"]["message"] == all_message
            agent.reset_results()

            # Update tag query result
            mock_tag_method.return_value = ["devices/campus/b2/d2", "devices/campus/b2/d3"]
            pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d3/all", headers, all_message).get(timeout=10)
            gevent.sleep(0.5)
            # refresh shouldn't have happened
            assert agent.instance_subscription_results.get("devices/campus/b2/d3/all") is None

            gevent.sleep(5)
            # now refresh should have happened
            pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d3/all", headers, all_message).get(timeout=10)
            assert agent.instance_subscription_results["devices/campus/b2/d3/all"]["headers"] == headers
            assert agent.instance_subscription_results["devices/campus/b2/d3/all"]["message"] == all_message
    finally:
        test_agent.reset_results()
        agent.core.stop()

#
# def test_unsubscribe_by_tags(volttron_instance, test_agents):
#     pub_agent, agent = test_agents
#     try:
#         with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
#             mock_tag_method.return_value = ["devices/campus/b2/d3"]
#
#             # Subscribe to subscribe_by_tags instance method and check result
#             agent.vip.pubsub.subscribe_by_tags('pubsub', "condition_devices/campus/b2/d3",
#                                                agent.callback_method).get(timeout=5)
#             # Publish messages
#             pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d3/all", headers, all_message).get(timeout=10)
#
#             assert agent.instance_subscription_results["devices/campus/b2/d3/all"]["headers"] == headers
#             assert agent.instance_subscription_results["devices/campus/b2/d3/all"]["message"] == all_message
#             agent.reset_results()
#
#             # Unsubscribe and check result
#             agent.vip.pubsub.unsubscribe_by_tags('pubsub', "condition_devices/campus/b2/d3",
#                                                  agent.callback_method).get(timeout=5)
#             pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d3/all", headers, all_message).get(timeout=10)
#             assert agent.instance_subscription_results.get("devices/campus/b2/d3/all") is None
#     finally:
#         agent.reset_results()
#
#
# def test_publish_by_tags(volttron_instance, test_agents):
#     pub_agent, agent = test_agents
#     try:
#         with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
#             mock_tag_method.return_value = ["devices/campus/b2/d4/p1", "devices/campus/b2/d4/p2"]
#
#             # Subscribe by prefix
#             agent.vip.pubsub.subscribe('pubsub', 'devices/campus/b2/d4/p1', agent.callback_method).get(timeout=5)
#
#             agent.vip.pubsub.publish_by_tags('pubsub', "tag_condition_device_d4_points",
#                                              headers, [75.2, {"units": "F"}]).get(timeout=5)
#
#
#             assert agent.instance_subscription_results["devices/campus/b2/d2/all"]["headers"] == headers
#             assert agent.instance_subscription_results["devices/campus/b2/d2/all"]["message"] == all_message
#             agent.reset_results()
#
#             # Unsubscribe and check result
#             agent.vip.pubsub.unsubscribe_by_tags('pubsub', "condition_devices/campus/b2/d2", agent.callback_method)
#             gevent.sleep(0.5)
#             pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d2/all", headers, all_message).get(timeout=10)
#             gevent.sleep(2)  # just to make sure we waited long enough
#             assert agent.instance_subscription_results.get("devices/campus/b2/d2/all") is None
#     finally:
#         agent.reset_results()
