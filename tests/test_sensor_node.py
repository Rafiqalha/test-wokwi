import json
import random
import unittest
from unittest.mock import Mock

from scripts.sensor_node import SensorNode, AckTracker, arguments, run


class SensorNodeTests(unittest.TestCase):
    def test_random_walk_stays_in_valid_sensor_range(self):
        node = SensorNode(random.Random(7))
        values = [node.reading(index, index * 2000)
                  for index in range(1, 1001)]
        self.assertTrue(all(24 <= item["suhu"] <= 34 and
                            48 <= item["kelembapan"] <= 84
                            for item in values))
        self.assertGreater(len({item["suhu"] for item in values}), 10)
        self.assertEqual(values[0]["device_id"], "demo-node-01")
        self.assertEqual(values[0]["source_mode"], "demo")
        self.assertEqual(len({value["session_id"] for value in values}), 1)
        self.assertNotEqual(node.session_id, SensorNode().session_id)

    def test_both_mode_sends_without_browser_requests(self):
        args = arguments(["--protocol", "both", "--count", "4",
                          "--interval", "0.1"])
        session = Mock()
        client = Mock()
        client.is_connected.return_value = True
        client.publish.return_value.rc = 0
        node = SensorNode()
        tracker = AckTracker(node.session_id)
        def publish(topic, payload, qos):
            if topic == "sensor/dht22":
                data = json.loads(payload)
                tracker.receive({"session_id": data["session_id"], "seq": data["seq"]})
            return Mock(rc=0)
        def post(url, json, timeout):
            reply = Mock()
            reply.json.return_value = {"status": "ok", "session_id": json["session_id"], "seq": json["seq"]}
            return reply
        client.publish.side_effect = publish
        session.post.side_effect = post
        messages = []
        run(args, session=session, mqtt_client=client, output=messages.append, node=node, tracker=tracker)
        self.assertEqual(client.publish.call_count, 4)
        self.assertEqual(session.post.call_count, 4)
        mqtt_sequences = [json.loads(call.args[1])["seq"]
                          for call in client.publish.call_args_list if call.args[0] == "sensor/dht22"]
        http_sequences = [call.kwargs["json"]["seq"]
                          for call in session.post.call_args_list if call.args[0].endswith("/update")]
        self.assertEqual(mqtt_sequences, [1, 3])
        self.assertEqual(http_sequences, [2, 4])
        self.assertEqual(len(messages), 5)
        metrics = [json.loads(call.args[1]) for call in client.publish.call_args_list
                   if call.args[0] == "sensor/metrics"]
        self.assertTrue(all(item["status"] == "ok" and item["rtt_ms"] >= 0 for item in metrics))

    def test_ack_from_another_session_or_sequence_is_ignored(self):
        tracker = AckTracker("current-boot")
        tracker.expect(2)
        for value in ([], {"session_id": "old-boot", "seq": 2},
                      {"session_id": "current-boot", "seq": 1}):
            tracker.receive(value)
            self.assertFalse(tracker.wait(0))
        tracker.receive({"session_id": "current-boot", "seq": 2})
        self.assertTrue(tracker.wait(0))

    def test_missing_mqtt_ack_reports_timeout_with_no_rtt(self):
        args = arguments(["--protocol", "mqtt", "--count", "1"])
        client = Mock()
        client.is_connected.return_value = True
        client.publish.return_value.rc = 0
        tracker = Mock()
        tracker.wait.return_value = False
        run(args, mqtt_client=client, tracker=tracker, output=lambda _value: None)
        metric = json.loads(client.publish.call_args_list[-1].args[1])
        self.assertEqual(metric["status"], "timeout")
        self.assertIsNone(metric["rtt_ms"])

    def test_http_response_must_acknowledge_the_current_session(self):
        args = arguments(["--protocol", "http", "--count", "1"])
        session = Mock()
        session.post.return_value.json.return_value = {"status": "ok", "session_id": "old", "seq": 1}
        run(args, session=session, output=lambda _value: None)
        metric = session.post.call_args_list[-1].kwargs["json"]
        self.assertEqual(metric["status"], "error")
        self.assertIsNone(metric["rtt_ms"])


if __name__ == "__main__":
    unittest.main()
