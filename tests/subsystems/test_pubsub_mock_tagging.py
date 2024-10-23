from datetime import datetime
from unittest import mock

import gevent
import pytest

pytest.importorskip(__name__)

from volttron.client.messaging import headers as headers_mod
from volttron.client.vip.agent import Agent
from volttron.client.vip.agent import PubSub
from volttron.utils import format_timestamp


class TestAgentPubsubByTags(Agent):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.subscription_results = dict()
        self.instance_subscription_results = dict()

    # set topic_source parameter explicitly to empty string as agent is instantiated with
    # mock get_topics_by_tag that returns results with the "devices" included in topic str
    @PubSub.subscribe_by_tags('pubsub', 'condition_test_class_method', topic_source="")
    def on_match(self, peer, sender, bus, topic, headers, message):
        self.subscription_results[topic] = {'headers': headers,
                                            'message': message}

    def callback_method(self, peer, sender, bus, topic, headers, message):
        self.instance_subscription_results[topic] = {'headers': headers,
                                                     'message': message}

    def reset_results(self):
        self.subscription_results = dict()
        self.instance_subscription_results = dict()

@pytest.mark.skip(reason="Need to update")
@pytest.fixture(scope="module")
def test_agents(volttron_instance):
    with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
        mock_tag_method.return_value = ["devices/campus/b1/device_test_class_method"]
        pub_agent = volttron_instance.build_agent()
        agent = volttron_instance.build_agent(identity="test-agent", agent_class=TestAgentPubsubByTags)
        gevent.sleep(0.5)
        yield pub_agent, agent
    pub_agent.core.stop()
    agent.core.stop()

@pytest.mark.skip(reason="Need to update")
@pytest.fixture(scope="module")
def test_agents_tagging(volttron_instance):
    with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
        mock_tag_method.return_value = ["devices/campus/b1/device_test_class_method"]
        pub_agent = volttron_instance.build_agent()
        agent = volttron_instance.build_agent(identity="test-agent", agent_class=TestAgentPubsubByTags)
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

@pytest.mark.skip(reason="Need to update")
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

@pytest.mark.skip(reason="Need to update")
def test_subscribe_by_tags_instance_method(volttron_instance, test_agents):
    pub_agent, agent = test_agents
    try:
        with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
            mock_tag_method.return_value = ["campus/b2/d2"]

            # Subscribe to subscribe_by_tags instance method and check result
            # test with default topic_source.
            agent.vip.pubsub.subscribe_by_tags('pubsub', "condition_devices/campus/b2/d2",
                                               agent.callback_method).get(timeout=10)

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

@pytest.mark.skip(reason="Need to update")
def test_subscribe_by_tags_refresh_tags(volttron_instance, test_agents):
    pub_agent, test_agent = test_agents
    agent = volttron_instance.build_agent(identity="test-agent-2", agent_class=TestAgentPubsubByTags,
                                          tag_refresh_interval=5)
    gevent.sleep(0.5)
    try:
        with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
            mock_tag_method.return_value = ["devices/campus/b2/d2"]

            # Subscribe to subscribe_by_tags instance method and check result
            # test with topic_source="" as mock return value has "devices" prefix
            agent.vip.pubsub.subscribe_by_tags('pubsub', "condition_devices/campus/b2",
                                               agent.callback_method, topic_source="").get(timeout=5)
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

@pytest.mark.skip(reason="Need to update")
def test_unsubscribe_by_tags(volttron_instance, test_agents):
    pub_agent, agent = test_agents
    try:
        with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
            mock_tag_method.return_value = ["campus/b2/d3"]

            # Subscribe to subscribe_by_tags instance method and check result
            agent.vip.pubsub.subscribe_by_tags('pubsub', "condition_devices/campus/b2/d3",
                                               agent.callback_method).get(timeout=5)
            # Publish messages
            pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d3/all", headers, all_message).get(timeout=10)

            assert agent.instance_subscription_results["devices/campus/b2/d3/all"]["headers"] == headers
            assert agent.instance_subscription_results["devices/campus/b2/d3/all"]["message"] == all_message
            agent.reset_results()

            # Unsubscribe and check result
            agent.vip.pubsub.unsubscribe_by_tags('pubsub', "condition_devices/campus/b2/d3",
                                                 agent.callback_method).get(timeout=5)
            pub_agent.vip.pubsub.publish('pubsub', "devices/campus/b2/d3/all", headers, all_message).get(timeout=10)
            gevent.sleep(2)
            assert agent.instance_subscription_results.get("devices/campus/b2/d3/all") is None
    finally:
        agent.reset_results()

@pytest.mark.skip(reason="Need to update")
def test_publish_by_tags(volttron_instance, test_agents):
    pub_agent, agent = test_agents
    try:
        with mock.patch.object(PubSub, "get_topics_by_tag") as mock_tag_method:
            mock_tag_method.return_value = ["campus/b2/d4/p1"]

            # Subscribe by prefix
            agent.vip.pubsub.subscribe('pubsub', 'devices/campus/b2/d4/p1', agent.callback_method).get(timeout=5)

            # publish by tags. should publish to two topics returned by mock method
            # mock method is returning WITHOUT "devices" prefix similar to tagging service so use default topic_source
            agent.vip.pubsub.publish_by_tags('pubsub', "tag_condition_device_d4_points",
                                             headers=headers, message=[75.2, {"units": "F"}])
            gevent.sleep(0.5)
            assert agent.instance_subscription_results["devices/campus/b2/d4/p1"]["headers"] == headers
            assert agent.instance_subscription_results["devices/campus/b2/d4/p1"]["message"] == [75.2, {"units": "F"}]
            agent.reset_results()

            mock_tag_method.return_value = ["devices/campus/b2/d4/p1", "devices/campus/b2/d4/p2"]

            # Unsubscribe and check result
            # explicitly sending topic_source as empty as mock get_topics_by_tag is returning with "devices" prefix
            agent.vip.pubsub.subscribe_by_tags('pubsub', "tag_condition_device_d4_points", agent.callback_method,
                                               topic_source="").get(timeout=5)
            try:
                # explicitly sending topic_source as empty as mock get_topics_by_tag is returning with "devices" prefix
                agent.vip.pubsub.publish_by_tags('pubsub', "tag_condition_device_d4_points",
                                                 headers=headers, message=[75.2, {"units": "F"}], topic_source="")
            except ValueError as v:

                assert v.args[0] == 'tag condition tag_condition_device_d4_points matched 2 topics but ' \
                                    'max_publish_count is set to 1'
            # explicitly sending topic_source as empty as mock get_topics_by_tag is returning with "devices" prefix
            agent.vip.pubsub.publish_by_tags('pubsub', "tag_condition_device_d4_points",
                                             headers=headers, message=[75.2, {"units": "F"}], max_publish_count=2,
                                             topic_source="")
            gevent.sleep(1)
            assert agent.instance_subscription_results["devices/campus/b2/d4/p1"]["headers"] == headers
            assert agent.instance_subscription_results["devices/campus/b2/d4/p1"]["message"] == [75.2, {"units": "F"}]
            assert agent.instance_subscription_results["devices/campus/b2/d4/p2"]["headers"] == headers
            assert agent.instance_subscription_results["devices/campus/b2/d4/p2"]["message"] == [75.2, {"units": "F"}]
    finally:
        agent.reset_results()
