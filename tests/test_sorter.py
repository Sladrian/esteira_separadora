"""Testes de visão e temporização sem câmera/servos. python3 -m unittest discover -s tests -v"""
import copy
from pathlib import Path
import sys
import threading
import time
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Controller, Debouncer, Planner, SorterFault, load_config
from vision import Vision, learn_ranges

CFG = load_config(Path(__file__).resolve().parent / "fixture.json")


def frame(hue=None, second=False):
    hsv = np.zeros((240, 320, 3), np.uint8)
    hsv[:] = [0, 0, 45]
    if hue is not None:
        cv2.circle(hsv, (140, 120), 15, (hue, 210, 210), -1)
    if second:
        cv2.circle(hsv, (195, 120), 15, (110, 210, 210), -1)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.vision = Vision(CFG["vision"])

    def test_three_colors_and_empty(self):
        for hue, expected in ((160, "rosa"), (110, "azul"), (60, "verde"), (None, None)):
            with self.subTest(color=expected):
                self.assertEqual(self.vision.inspect(frame(hue))[0], expected)

    def test_unknown_is_occupied(self):
        label, _, _, mask = self.vision.inspect(frame(25))
        self.assertEqual(label, "desconhecida")
        self.assertGreater(cv2.countNonZero(mask), 100)

    def test_outside_roi_ignored(self):
        image = frame()
        cv2.circle(image, (20, 20), 15, (255, 0, 0), -1)
        self.assertIsNone(self.vision.inspect(image)[0])

    def test_multiple_caps_fail(self):
        with self.assertRaises(SorterFault):
            self.vision.inspect(frame(160, second=True))

    def test_mixed_color_not_accepted(self):
        image = frame(160)
        image[90:151, 140:156] = frame(110)[90:151, 140:156]
        self.assertEqual(self.vision.inspect(image)[0], "desconhecida")

    def test_learn_and_reload(self):
        _, _, hsv, mask = self.vision.inspect(frame(135))
        cfg = copy.deepcopy(CFG["vision"])
        cfg["colors"]["rosa"]["ranges"] = learn_ranges(hsv, mask, 100)
        self.assertEqual(Vision(cfg).inspect(frame(135))[0], "rosa")

    def test_hue_wrap(self):
        hsv = np.zeros((20, 20, 3), np.uint8)
        hsv[:] = [178, 200, 200]
        hsv[10:] = [1, 200, 200]
        ranges = learn_ranges(hsv, np.full((20, 20), 255, np.uint8), 100)
        self.assertEqual(len(ranges), 2)
        for hue in (178, 1):
            self.assertTrue(any(lo[0] <= hue <= hi[0] for lo, hi in ranges))


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.d = Debouncer(CFG["vision"])

    def arm(self, start=0):
        for i in range(4):
            self.d.update(None, start+i/15)

    def test_startup_must_be_empty(self):
        for i in range(20):
            self.assertIsNone(self.d.update("rosa", i/15))

    def test_once_per_cap_and_first_frame_time(self):
        self.arm()
        self.assertIsNone(self.d.update("rosa", 1))
        self.assertIsNone(self.d.update("rosa", 1.07))
        self.assertEqual(self.d.update("rosa", 1.14), ("rosa", 1))
        for i in range(10):
            self.assertIsNone(self.d.update("azul", 1.2+i/15))

    def test_short_dropout_does_not_rearm(self):
        self.arm()
        for i in range(3):
            self.d.update("rosa", 1+i/15)
        self.d.update(None, 1.3)
        for i in range(5):
            self.assertIsNone(self.d.update("rosa", 1.4+i/15))

    def test_rearm_after_full_empty(self):
        self.arm()
        for i in range(3):
            self.d.update("rosa", 1+i/15)
        self.arm(2)
        results = [self.d.update("verde", 3+i/15) for i in range(3)]
        self.assertEqual(results[-1], ("verde", 3))

    def test_stuck_cap_fault(self):
        self.arm()
        self.d.update("rosa", 1)
        with self.assertRaises(SorterFault):
            self.d.update("rosa", 7)


class PlannerTests(unittest.TestCase):
    def test_images_to_scheduled_commands(self):
        vision, debounce, planner = Vision(CFG["vision"]), Debouncer(CFG["vision"]), Planner(CFG)
        images = [frame()] * 5
        for hue in (160, 110, 60, 25):
            images += [frame(hue)] * 6 + [frame()] * 30
        detections, moves = [], []
        for i, image in enumerate(images):
            now = 10+i/15
            label = vision.inspect(image)[0]
            event = debounce.update(label, now)
            if event:
                color, timestamp = event
                destination, events = planner.plan(color, timestamp, now)
                detections.append((color, destination))
                moves.extend(events)
        self.assertEqual(detections, [("rosa", "direita"), ("azul", "esquerda"),
                                      ("verde", "reto"), ("desconhecida", "reto")])
        self.assertEqual([(side, pulse) for _, side, pulse in moves],
                         [("direita", 1800), ("direita", 1500),
                          ("esquerda", 1200), ("esquerda", 1500)])

    def test_destinations_and_offsets(self):
        for label, expected in (("rosa", "direita"), ("azul", "esquerda"),
                                ("verde", "reto"), ("desconhecida", "reto")):
            with self.subTest(label=label):
                dest, events = Planner(CFG).plan(label, 10, 10.15)
                self.assertEqual(dest, expected)
                self.assertEqual(len(events), 0 if expected == "reto" else 2)
                if events:
                    travel = CFG["servos"][expected]["travel_s"]
                    self.assertAlmostEqual(events[0][0], 10+travel-0.2)
                    self.assertAlmostEqual(events[1][0], 10+travel+0.25)

    def test_close_cap_even_green_fault(self):
        p = Planner(CFG)
        p.plan("rosa", 10, 10.15)
        with self.assertRaises(SorterFault):
            p.plan("verde", 10.5, 10.65)
        self.assertEqual(p.last_cap, 10)  # erro não altera reservas

    def test_green_reserves_station_before_next_pink(self):
        p = Planner(CFG)
        p.plan("verde", 10, 10.15)
        with self.assertRaises(SorterFault):
            p.plan("rosa", 10.3, 10.45)

    def test_late_detection_fault(self):
        with self.assertRaises(SorterFault):
            Planner(CFG).plan("rosa", 10, 11.9)

    def test_distinct_station_distance(self):
        cfg = copy.deepcopy(CFG)
        cfg["servos"]["esquerda"]["travel_s"] = 3.5
        _, events = Planner(cfg).plan("azul", 10, 10.15)
        self.assertAlmostEqual(events[0][0], 13.3)

    def test_valid_spaced_sequence(self):
        p = Planner(CFG)
        for i, label in enumerate(("rosa", "azul", "verde", "rosa")):
            p.plan(label, 10+i*1.5, 10.15+i*1.5)


class FakeServos:
    def __init__(self):
        self.events = []
        self.parked = threading.Event()

    def set_pulse(self, side, pulse):
        self.events.append((side, pulse, time.monotonic()))

    def park(self):
        self.parked.set()


class ControllerTests(unittest.TestCase):
    def test_actuates_while_main_thread_keeps_reading(self):
        cfg = copy.deepcopy(CFG)
        for s in cfg["servos"].values():
            s.update(travel_s=0.18, advance_s=0.08, hold_after_arrival_s=0.05)
        driver = FakeServos()
        c = Controller(cfg, driver)
        c.start()
        start = time.monotonic()
        try:
            c.submit("rosa", start)
            while time.monotonic()-start < 0.4:
                c.beat()
                time.sleep(0.01)
            c.check()
        finally:
            c.close()
        self.assertEqual([(side, pulse) for side, pulse, _ in driver.events],
                         [("direita", 1800), ("direita", 1500)])
        self.assertTrue(driver.parked.is_set())

    def test_camera_watchdog_parks_and_cancels(self):
        cfg = copy.deepcopy(CFG)
        cfg["timing"]["camera_timeout_s"] = 0.10
        driver = FakeServos()
        c = Controller(cfg, driver)
        c.start()
        try:
            c.submit("rosa", time.monotonic())
            self.assertTrue(driver.parked.wait(1))
            with self.assertRaises(SorterFault):
                c.check()
            self.assertEqual(driver.events, [])
        finally:
            c.close()

    def test_late_servo_event_not_executed(self):
        driver = FakeServos()
        c = Controller(CFG, driver)
        c.queue = [(time.monotonic()-1, "direita", 1800)]
        c.start()
        try:
            self.assertTrue(driver.parked.wait(1))
            with self.assertRaises(SorterFault):
                c.check()
            self.assertEqual(driver.events, [])
        finally:
            c.close()

    def test_servo_failure_propagates(self):
        class Broken(FakeServos):
            def set_pulse(self, *_):
                raise OSError("GPIO desconectado")
        driver = Broken()
        c = Controller(CFG, driver)
        c.queue = [(time.monotonic()+0.02, "direita", 1800)]
        c.start()
        try:
            self.assertTrue(driver.parked.wait(1))
            self.assertIn("GPIO desconectado", str(c.error))
        finally:
            c.close()


class ConfigurationTests(unittest.TestCase):
    def test_absent_camera_has_actionable_error(self):
        import types
        from unittest.mock import patch
        from hardware import Camera
        module = types.ModuleType("picamera2")
        module.Picamera2 = type("Picamera2", (), {"global_camera_info": staticmethod(lambda: [])})
        with patch.dict(sys.modules, {"picamera2": module}), patch("hardware.usb_cameras", return_value=[]):
            with self.assertRaisesRegex(SorterFault, "Nenhuma câmera reconhecida"):
                Camera(CFG["camera"])

    def test_delivered_config_is_valid(self):
        load_config(Path(__file__).resolve().parents[1] / "config.json")

    def test_duplicate_pins_rejected(self):
        import json
        import tempfile
        cfg = copy.deepcopy(CFG)
        cfg["servos"]["esquerda"]["gpio"] = cfg["servos"]["direita"]["gpio"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_unsafe_pulse_rejected(self):
        import json
        import tempfile
        cfg = copy.deepcopy(CFG)
        cfg["servos"]["direita"]["push_us"] = 2500
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(cfg), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
