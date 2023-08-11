from datetime import datetime

import gevent
import pytest
from mock import MagicMock, patch
from pathlib import Path

from volttron.client.messaging import headers as headers_mod
from volttron.client.vip.agent import Agent
from volttron.client.vip.agent import PubSub
from volttron.utils import format_timestamp
from volttrontesting.utils import (poll_gevent_sleep,
                                   messages_contains_prefix)


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

class PublishFromHandlerTestAgent(Agent):
    def __init__(self, **kwargs):
        super(PublishFromHandlerTestAgent, self).__init__(**kwargs)
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
                                               agent_class=PublishFromHandlerTestAgent)

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



@pytest.fixture(scope="module")
def tagging_agent(volttron_instance):
    query_agent = volttron_instance.build_agent()
    config = {
        "connection": {
            "type": "sqlite",
            "params": {
                "database": volttron_instance.volttron_home + '/test.sqlite'
            }
        }
    }
    historian_vip = 'test.historian'
    historian_uuid = volttron_instance.install_agent(
        vip_identity=historian_vip,
        agent_dir="volttron-sqlite-historian",
        config_file=config,
        start=True)
    gevent.sleep(1)
    assert volttron_instance.is_agent_running(historian_uuid)

    config = {
        "connection": {
            "type": "sqlite",
            "params": {
                "database": volttron_instance.volttron_home + '/test_tagging.sqlite'
            }
        },
        "historian_vip_identity": "test.historian"
    }
    tagging_agent_vip = 'test.tagging'
    tagging_agent_id = volttron_instance.install_agent(vip_identity=tagging_agent_vip,
                                                       agent_dir="volttron-sqlite-tagging",
                                                       config_file=config)
    volttron_instance.start_agent(tagging_agent_id)
    gevent.sleep(1)
    assert volttron_instance.is_agent_running(tagging_agent_id)


    now = format_timestamp(datetime.utcnow())
    headers = {headers_mod.DATE: now,
               headers_mod.TIMESTAMP: now}
    to_send = [{'topic': 'devices/campus1/d2/all', 'headers': headers,
                'message': [
                    {'p1': 2, 'p2': 2, 'p3': 1, 'p4': 2, 'p5': 2}]}]
    query_agent.vip.rpc.call(historian_vip, 'insert', to_send).get(
        timeout=10)
    to_send = [{'topic': 'devices/campus1/d1/all', 'headers': headers,
                'message': [
                    {'p1': 2, 'p2': 2, 'p3': 1, 'p4': 2, 'p5': 2}]}]
    query_agent.vip.rpc.call(historian_vip, 'insert', to_send).get(
        timeout=10)

    to_send = [{'topic': 'devices/campus2/d1/all', 'headers': headers,
                'message': [
                    {'p1': 2, 'p2': 2, 'p3': 1, 'p4': 2, 'p5': 2}]}]
    query_agent.vip.rpc.call(historian_vip, 'insert', to_send).get(
        timeout=10)
    gevent.sleep(2)

    # 2. Add tags to topics and topic_prefix that can be used for queries
    query_agent.vip.rpc.call(
        tagging_agent_vip, 'add_topic_tags', topic_prefix='campus1',
        tags={'campus': True, 'dis': "Test description",
              "geoCountry": "US"}).get(timeout=10)

    query_agent.vip.rpc.call(
        tagging_agent_vip, 'add_topic_tags', topic_prefix='campus2',
        tags={'campus': True, "geoCountry": "UK"}).get(timeout=10)

    query_agent.vip.rpc.call(
        tagging_agent_vip, 'add_tags',
        tags={
            'campus.*/d.*/p1': {'point': True, 'maxVal': 15, 'minVal': -1},
            'campus.*/d.*/p2': {'point': True, 'maxVal': 10, 'minVal': 0,
                                'dis': "Test description"},
            'campus.*/d.*/p3': {'point': True, 'maxVal': 5, 'minVal': 1,
                                'dis': "Test description"},
            'campus.*/d1': {'equip': True, 'elec': True, 'phase': 'p1_1',
                            'dis': "Test description"},
            'campus.*/d2': {'equip': True, 'elec': True,
                            'phase': 'p2'},
            'campus1/d.*': {'campusRef': 'campus1'},
            'campus2/d.*': {'campusRef': 'campus2'}}).get(timeout=10)

    query_agent.vip.rpc.call(tagging_agent_vip, 'add_topic_tags',
                             topic_prefix='campus2/d1',
                             tags={'phase': "p1_2"}).get(timeout=10)
    gevent.sleep(2)

    yield tagging_agent_vip, query_agent

    volttron_instance.stop_agent(tagging_agent_id)
    volttron_instance.remove_agent(tagging_agent_id)


class TestAgentPubsubByTags2(Agent):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.subscription_results = dict()
        self.instance_subscription_results = dict()

    def callback_method(self, peer, sender, bus, topic, headers, message):
        self.instance_subscription_results[topic] = {'headers': headers,
                                                     'message': message}

    def reset_results(self):
        self.subscription_results = dict()
        self.instance_subscription_results = dict()


def test_subscribe_by_tags_with_sqlite_tagging_agent(volttron_instance, tagging_agent):
    tagging_vip, pub_agent = tagging_agent
    gevent.sleep(2)
    agent = volttron_instance.build_agent(identity="test-agent-2", agent_class=TestAgentPubsubByTags2,
                                          tag_vip_id=tagging_vip)

    try:
        result1 = pub_agent.vip.rpc.call(
            tagging_vip, 'get_topics_by_tags',
            condition='equip AND NOT (phase LIKE "p1.*")').get(timeout=10)
        print("Results of AND and OR query with parenthesis: {} ".format(
            result1))
        assert len(result1) == 1
        assert result1 == ['campus1/d2']

        #  Subscribe to subscribe_by_tags instance method and check result
        result = agent.vip.pubsub.subscribe_by_tags('pubsub', 'equip AND NOT (phase LIKE "p1.*")',
                                                    agent.callback_method).get(timeout=20)

        print(f"RESULT of subscribe by tags {result}")
        gevent.sleep(1)
        # Publish messages
        pub_agent.vip.pubsub.publish('pubsub', "devices/campus1/d1/all", headers,
                                     all_message).get(timeout=10)
        pub_agent.vip.pubsub.publish('pubsub', "devices/campus1/d2/all", headers,
                                     all_message).get(timeout=10)
        gevent.sleep(2)
        print(agent.instance_subscription_results)
        assert agent.instance_subscription_results["devices/campus1/d2/all"]["headers"] == headers
        assert agent.instance_subscription_results["devices/campus1/d2/all"]["message"] == all_message
        assert agent.instance_subscription_results.get("devices/campus1/d1/all") is None
    finally:
        agent.core.stop()
        pub_agent.core.stop()
