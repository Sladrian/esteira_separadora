"""Contagens de produção e rotas do painel."""
from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from continuous import ContinuousController
from core import load_config
from panel import Panel


CFG = load_config(Path(__file__).with_name("fixture.json"))


class DashboardTests(unittest.TestCase):
    def test_straight_route_counts_but_unknown_does_not(self):
        control = ContinuousController(CFG, Mock())
        now = time.monotonic()
        control.submit("verde", now)
        control.submit("desconhecida", now)
        data = control.production_snapshot()
        self.assertEqual(data["counts"], {"rosa": 0, "azul": 0, "verde": 1})
        self.assertEqual(data["total"], 1)
        self.assertEqual(sum(data["bars"]), 1)
        self.assertIsNone(data["rate"])

    def test_rate_uses_recent_window_and_history_expires(self):
        with patch("continuous.time.time", return_value=3600), patch("continuous.time.monotonic", return_value=0):
            control = ContinuousController(CFG, Mock())
        for when in (0, 1500, 1600, 1799, 1860):
            control._record_sorted("azul", when)
        with patch("continuous.time.monotonic", return_value=1861):
            data = control.production_snapshot()
        self.assertEqual(data["total"], 5)
        self.assertEqual(data["rate"], 36)
        self.assertEqual(sum(data["bars"]), 4)
        self.assertEqual(data["bars"][-1], 1)
        self.assertEqual(len(data["bars"]), 30)

    def test_only_completed_servo_cycles_count(self):
        for case, expected in (("completed", 1), ("conflict", 0), ("driver failure", 0)):
            with self.subTest(case=case):
                cfg = deepcopy(CFG)
                cfg["timing"]["camera_timeout_s"] = 3
                for servo in cfg["servos"].values():
                    servo.update(travel_s=.08, advance_s=.02, hold_after_arrival_s=.04, return_s=.02)
                driver = Mock()
                driver.set_pulse.return_value = case != "driver failure"
                control = ContinuousController(cfg, driver)
                control.start()
                try:
                    with control.condition:
                        now = time.monotonic()
                        control.submit("rosa", now)
                        self.assertEqual(control.production_snapshot()["total"], 0)
                        if case == "conflict":
                            control.submit("desconhecida", now)
                    deadline = now + 1
                    while control.snapshot()["pending"] and time.monotonic() < deadline:
                        time.sleep(.01)
                    self.assertEqual(control.snapshot()["pending"], 0)
                    self.assertEqual(control.production_snapshot()["counts"]["rosa"], expected)
                finally:
                    control.close()

    def test_dashboard_uses_existing_token_and_hides_rate_when_stopped(self):
        control = ContinuousController(CFG, Mock())
        motor = {"configured": True, "running": False}
        panel = Panel(0, CFG, lambda _: CFG, motor_status=lambda: motor, production=control.production_snapshot)
        self.addCleanup(panel.close)
        conn = HTTPConnection("127.0.0.1", panel.server.server_port, timeout=3)
        self.addCleanup(conn.close)
        prefix = "/" + panel.token + "/"

        def get(path):
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, response.read()

        self.assertEqual(get("/production")[0], 404)
        status, page = get(prefix + "dashboard")
        self.assertEqual(status, 200)
        self.assertIn(b"Painel da esteira", page)
        self.assertEqual(json.loads(get(prefix + "production")[1])["state"], "stopped")
        panel.info["production_ready"] = True
        motor["running"] = True
        with patch("continuous.time.monotonic", return_value=control.session_clock + 120):
            data = json.loads(get(prefix + "production")[1])
            self.assertEqual(data["state"], "running")
            self.assertEqual(data["rate"], 0)
            motor["running"] = False
            self.assertIsNone(json.loads(get(prefix + "production")[1])["rate"])
