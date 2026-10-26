import copy
import http.client
import json
from pathlib import Path
import sys
import threading
import time
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import load_config
from panel import Panel
from settings import changed_config

CFG = load_config(Path(__file__).with_name("fixture.json"))


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(CFG)
        def apply(changes):
            self.cfg = changed_config(self.cfg, changes)
            return self.cfg
        self.panel = Panel(0, self.cfg, apply)
        self.connection = http.client.HTTPConnection("127.0.0.1", self.panel.server.server_address[1], timeout=8)

    def tearDown(self):
        self.connection.close()
        self.panel.close()

    def get(self, path):
        self.connection.request("GET", path)
        reply = self.connection.getresponse()
        return reply.status, reply.read()

    def post(self, changes, origin=None):
        def process():
            until = time.monotonic()+2
            while self.panel.commands.empty() and time.monotonic() < until:
                time.sleep(.005)
            self.panel.process_commands()
        thread = threading.Thread(target=process)
        thread.start()
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        self.connection.request("POST", "/"+self.panel.token+"/settings", json.dumps(changes), headers)
        reply = self.connection.getresponse()
        result = reply.status, reply.read()
        thread.join(timeout=3)
        return result

    def test_page_and_snapshot_use_same_camera_frame(self):
        status, page = self.get("/"+self.panel.token+"/")
        self.assertEqual(status, 200)
        self.assertIn("Salvar e aplicar".encode(), page)
        self.panel.publish(np.zeros((240, 320, 3), np.uint8), {"message": "Leitura ativa"})
        status, jpeg = self.get("/"+self.panel.token+"/frame")
        self.assertEqual(status, 200)
        self.assertTrue(jpeg.startswith(b"\xff\xd8"))

    def test_save_updates_config_and_preserves_colors(self):
        status, _ = self.post({"roi": [110, 105, 80, 25], "servos": {"direita": {"travel_s": 2.5}}})
        self.assertEqual(status, 200)
        _, body = self.get("/"+self.panel.token+"/status")
        result = json.loads(body)["config"]
        self.assertEqual(result["vision"]["roi"], [110, 105, 80, 25])
        self.assertEqual(result["vision"]["colors"], CFG["vision"]["colors"])

    def test_invalid_roi_keeps_config(self):
        status, body = self.post({"roi": [400, 0, 80, 25]})
        self.assertEqual(status, 400)
        self.assertIn(b"error", body)
        self.assertEqual(self.cfg, CFG)

    def test_wrong_token_and_cross_origin_rejected(self):
        self.assertEqual(self.get("/wrong/status")[0], 404)
        self.assertEqual(self.post({"roi": [110, 105, 80, 25]}, "http://foreign.example")[0], 403)
        self.assertEqual(self.cfg, CFG)

    def test_cancelled_request_does_not_apply_later(self):
        command = {"changes": {"roi": [110, 105, 80, 25]}, "cancelled": True,
                   "done": threading.Event(), "lock": threading.Lock()}
        self.panel.commands.put(command)
        self.panel.process_commands()
        self.assertEqual(self.cfg, CFG)


if __name__ == "__main__":
    unittest.main()
