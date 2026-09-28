import json
import random
import unittest
from unittest.mock import Mock

from scripts.sensor_node import SensorNode, arguments, run


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

    def test_both_mode_sends_without_browser_requests(self):
        args = arguments(["--protocol", "both", "--count", "4",
                          "--interval", "0.1"])
        session = Mock()
        client = Mock()
        client.is_connected.return_value = True
        client.publish.return_value.rc = 0
        messages = []
        run(args, session=session, mqtt_client=client, output=messages.append)
        self.assertEqual(client.publish.call_count, 2)
        self.assertEqual(session.post.call_count, 2)
        mqtt_sequences = [json.loads(call.args[1])["seq"]
                          for call in client.publish.call_args_list]
        http_sequences = [call.kwargs["json"]["seq"]
                          for call in session.post.call_args_list]
        self.assertEqual(mqtt_sequences, [1, 3])
        self.assertEqual(http_sequences, [2, 4])
        self.assertEqual(len(messages), 5)


if __name__ == "__main__":
    unittest.main()
