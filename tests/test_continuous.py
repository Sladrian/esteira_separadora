"""Ensaios de múltiplas peças, conflitos, falhas de GPIO e execução integrada."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from continuous import ContinuousController, Tracker
from core import load_config
from hardware import Servos
from settings import changed_config
from vision import Vision

CFG = load_config(Path(__file__).with_name("fixture.json"))


def scene(items=()):
    hsv = np.full((240, 320, 3), (0, 0, 45), np.uint8)
    for x, hue in items:
        cv2.circle(hsv, (x, 120), 12, (hue, 210, 210), -1)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.vision = Vision(CFG["vision"])
        self.tracker = Tracker(CFG["vision"])
        for i in range(4):
            self.tracker.update([], i/15)

    def test_two_colors_simultaneously_once_each(self):
        events = []
        for i in range(15):
            detections = self.vision.detect(scene([(100+i*2, 160), (190+i*2, 110)]))
            self.assertEqual(len(detections), 2)
            events += self.tracker.update(detections, 1+i/15)
        self.assertEqual(sorted(events), [("azul", 1), ("rosa", 1)])

    def test_new_cap_while_other_still_visible(self):
        events = []
        for i in range(12):
            items = [(110, 160)] + ([(210, 60)] if i >= 5 else [])
            events += self.tracker.update(self.vision.detect(scene(items)), 1+i/15)
        self.assertEqual([event[0] for event in events], ["rosa", "verde"])

    def test_unknown_entry_can_become_blue(self):
        events = []
        for i, hue in enumerate([25]*4+[110]*8):
            events += self.tracker.update(self.vision.detect(scene([(150, hue)])), 1+i/15)
        self.assertEqual(events, [("azul", 1)])

    def test_unknown_leaves_without_stopping_next_color(self):
        events = []
        for i, image in enumerate([scene([(150, 25)])]*5+[scene()]*5+[scene([(150, 160)])]*6):
            events += self.tracker.update(self.vision.detect(image), 1+i/15)
        self.assertEqual([event[0] for event in events], ["desconhecida", "rosa"])

    def test_dropout_does_not_duplicate_and_tracks_are_bounded(self):
        events = []
        images = [scene([(140, 110)])]*4+[scene()]*2+[scene([(145, 110)])]*7
        for i, image in enumerate(images):
            events += self.tracker.update(self.vision.detect(image), 1+i/15)
        self.assertEqual([e[0] for e in events], ["azul"])
        for i in range(10):
            self.tracker.update([], 3+i/15)
        self.assertEqual(self.tracker.tracks, {})

    def test_occupied_startup_ignored_until_empty(self):
        tracker = Tracker(CFG["vision"])
        for i in range(20):
            self.assertEqual(tracker.update(self.vision.detect(scene([(140, 110)])), i), [])


class Driver:
    def __init__(self):
        self.events = []

    def set_pulse(self, side, pulse):
        self.events.append((side, pulse))
        return True


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.driver = Driver()
        self.control = ContinuousController(CFG, self.driver)
        self.now = 100

    def submit(self, color, offset):
        with patch("continuous.time.monotonic", return_value=self.now+offset+.15):
            return self.control.submit(color, self.now+offset)

    def test_many_caps_in_transit_not_global_gap(self):
        self.submit("rosa", 0)
        self.submit("azul", .8)  # Antigo limite global era 1,2 s.
        self.submit("verde", 1.6)
        self.assertEqual(len(self.control.queue), 4)
        self.assertEqual(self.control.skipped, {})

    def test_simultaneous_different_colors_cancel_conflicting_pushes(self):
        self.submit("rosa", 0)
        _, result = self.submit("azul", 0)
        self.assertEqual(self.control.queue, [])
        self.assertIn("sem acionamento", result)
        self.assertEqual(self.control.counts, {"rosa": 1, "azul": 1})
        self.assertIn("desvio em", self.submit("rosa", 2)[1])

    def test_green_cancels_earlier_pink_and_reverse(self):
        self.submit("rosa", 0)
        self.submit("verde", .2)
        self.assertEqual(self.control.queue, [])
        self.control = ContinuousController(CFG, self.driver)
        self.submit("verde", 0)
        self.assertIn("sem acionamento", self.submit("rosa", .2)[1])

    def test_conflict_retracts_an_already_active_arm(self):
        self.submit("rosa", 0)
        self.control.active["direita"] = 1
        self.submit("verde", .2)
        self.assertIn(("direita", 1500), self.driver.events)

    def test_late_detection_skipped_and_next_one_accepted(self):
        with patch("continuous.time.monotonic", return_value=105):
            self.assertIn("sem acionamento", self.control.submit("rosa", 100)[1])
        self.assertIn("desvio em", self.submit("rosa", 6)[1])

    def test_timing_update_keeps_existing_events(self):
        self.submit("azul", 0)
        before = list(self.control.queue)
        updated = changed_config(CFG, {"servos": {"esquerda": {"travel_s": 4}}})
        self.control.reconfigure(updated)
        self.assertEqual(self.control.queue, before)
        self.submit("azul", 2)
        self.assertTrue(any(abs(e[0]-105.8) < .001 for e in self.control.queue))

    def test_driver_failure_keeps_worker_alive(self):
        class Broken(Driver):
            def set_pulse(self, *_):
                raise OSError("sem GPIO")
        control = ContinuousController(CFG, Broken())
        now = time.monotonic()
        control.queue = [(now, 1, "direita", 1800, 1, True)]
        control.start()
        try:
            time.sleep(.07)
            control.beat()
            control.check()
            self.assertTrue(control.thread.is_alive())
            self.assertGreater(control.skipped["erro_servo"], 0)
        finally:
            control.close()

    def test_watchdog_cancels_then_recovers_without_replaying(self):
        cfg = copy.deepcopy(CFG)
        cfg["timing"]["camera_timeout_s"] = .06
        control = ContinuousController(cfg, self.driver)
        control.start()
        try:
            control.submit("rosa", time.monotonic())
            time.sleep(.15)
            self.assertTrue(control.suspended)
            self.assertEqual(control.queue, [])
            control.beat()
            self.assertTrue(control.suspended)  # Peças em trânsito precisam sair das estações.
            with control.condition:
                control.inhibit_until = 0
            control.beat()
            self.assertFalse(control.suspended)
            self.assertIn("desvio em", control.submit("azul", time.monotonic())[1])
        finally:
            control.close()

    def test_late_push_discarded_but_return_still_happens(self):
        control = ContinuousController(CFG, self.driver)
        now = time.monotonic()
        control.queue = [(now-1, 0, "direita", 1800, 1, True),
                         (now-.5, 1, "direita", 1500, 1, False)]
        control.start()
        try:
            time.sleep(.05)
            self.assertNotIn(("direita", 1800), self.driver.events)
            self.assertIn(("direita", 1500), self.driver.events)
        finally:
            control.close()


class HardwareAndSettingsTests(unittest.TestCase):
    def test_missing_daemon_does_not_stop_run(self):
        pi = Mock(connected=False)
        with patch.dict(sys.modules, {"pigpio": SimpleNamespace(pi=Mock(return_value=pi))}):
            servos = Servos(CFG["servos"], tolerate_errors=True)
            try:
                self.assertFalse(servos.set_pulse("direita", 1800))
                self.assertEqual(set(servos.status().values()), {"indisponível"})
            finally:
                servos.close()

    def test_one_failed_channel_does_not_disable_other(self):
        pi = Mock(connected=True)
        pi.set_servo_pulsewidth.return_value = 0
        with patch.dict(sys.modules, {"pigpio": SimpleNamespace(pi=Mock(return_value=pi))}):
            servos = Servos(CFG["servos"], tolerate_errors=True)
            pi.set_servo_pulsewidth.side_effect = lambda pin, pulse: -1 if pin == 18 else 0
            try:
                self.assertFalse(servos.set_pulse("direita", 1800))
                self.assertTrue(servos.set_pulse("esquerda", 1200))
            finally:
                servos.close()

    def test_changes_preserve_calibrations_and_validate_bounds(self):
        updated = changed_config(CFG, {"roi": [110, 105, 80, 25], "servos": {"direita": {"travel_s": 2.5}}})
        self.assertEqual(updated["vision"]["colors"], CFG["vision"]["colors"])
        self.assertEqual(CFG["servos"]["direita"]["travel_s"], 2)
        for changes in ({"roi": [300, 200, 80, 25]}, {"servos": {"direita": {"push_us": 3000}}},
                        {"servos": {"direita": {"gpio": 1}}}, {"roi": [0, 0, 2, 2]}):
            with self.assertRaises(ValueError):
                changed_config(CFG, changes)

    def test_continuous_run_recovers_camera_and_reads_multiple_colors(self):
        from app import run
        cfg = copy.deepcopy(CFG)
        for settings in cfg["servos"].values():
            settings["return_s"] = .01
        count = 0
        def read():
            nonlocal count
            time.sleep(.006)
            count += 1
            if count == 15:
                raise OSError("câmera desconectada")
            image = scene() if count < 6 or 16 <= count < 22 else scene([(120, 160), (200, 110)])
            return image, time.monotonic(), {"FrameSequence": count}
        camera = Mock(kind="csi")
        camera.read.side_effect = read
        args = SimpleNamespace(live=False, web=False, preview=False, seconds=3.3, config="unused")
        with patch("app.Camera", return_value=camera) as factory, patch("app.signal.signal"), \
                self.assertLogs("esteira", level="INFO") as logs:
            run(cfg, args)
        self.assertGreaterEqual(factory.call_count, 2)
        self.assertTrue(any("Tampinha rosa" in line for line in logs.output))
        self.assertTrue(any("Tampinha azul" in line for line in logs.output))
        self.assertGreaterEqual(camera.close.call_count, 2)


if __name__ == "__main__":
    unittest.main()
