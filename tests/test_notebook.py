import copy
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import load_config, SorterFault
from notebook import calibrate, save_calibration, open_camera, select_camera, set_exposure
from vision import Vision

CFG = load_config(Path(__file__).resolve().parent / "fixture.json")


class NotebookTests(unittest.TestCase):
    def test_exposure_requires_driver_confirmation(self):
        device = Mock()
        device.set.return_value = True
        device.get.side_effect = [-5, -5]
        with patch("notebook.sys.platform", "win32"):
            with self.assertRaises(SorterFault):
                set_exposure(device, -7)
        device.set.assert_any_call(cv2.CAP_PROP_EXPOSURE, -5)

    def test_exposure_accepted(self):
        device = Mock()
        device.set.return_value = True
        device.get.side_effect = [-5, -7]
        with patch("notebook.sys.platform", "win32"):
            self.assertEqual(set_exposure(device, -7), -7)

    def test_selects_brio_by_name_even_when_index_changes(self):
        cameras = [SimpleNamespace(index=0, name="Integrated Webcam"),
                   SimpleNamespace(index=2, name="Brio 300")]
        self.assertEqual(select_camera(cameras).index, 2)

    def test_missing_usb_does_not_open_integrated_camera(self):
        with self.assertRaises(SorterFault):
            select_camera([SimpleNamespace(index=0, name="Integrated Webcam")])

    def test_explicit_index_overrides_name(self):
        cameras = [SimpleNamespace(index=0, name="Other USB"),
                   SimpleNamespace(index=1, name="Brio 300")]
        self.assertEqual(select_camera(cameras, index=0).name, "Other USB")

    def sample(self, hue):
        hsv = np.zeros((240, 320, 3), np.uint8)
        cv2.circle(hsv, (140, 120), 15, (hue, 210, 210), -1)
        image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        return Vision(CFG["vision"]).inspect(image)[2:]

    def test_learns_each_color_without_mutating_original(self):
        before = copy.deepcopy(CFG)
        for color, hue in (("rosa", 160), ("azul", 110), ("verde", 60)):
            hsv, mask = self.sample(hue)
            learned = calibrate(CFG, hsv, mask, color)
            self.assertIn("ranges", learned["vision"]["colors"][color])
        self.assertEqual(CFG, before)

    def test_rejects_empty_sample(self):
        hsv, mask = self.sample(160)
        with self.assertRaises(ValueError):
            calibrate(CFG, hsv, np.zeros_like(mask), "rosa")

    def test_ambiguous_sample_keeps_original(self):
        hsv, mask = self.sample(110)
        before = copy.deepcopy(CFG)
        with self.assertRaises(ValueError):
            calibrate(CFG, hsv, mask, "rosa")
        self.assertEqual(CFG, before)

    def test_save_creates_valid_file_and_previous_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "notebook.json"
            save_calibration(path, CFG)
            original = path.read_bytes()
            updated = copy.deepcopy(CFG)
            updated["vision"]["min_object_pixels"] += 1
            save_calibration(path, updated)
            self.assertEqual(load_config(path), updated)
            self.assertEqual(path.with_name(path.name + ".bak").read_bytes(), original)
            invalid = copy.deepcopy(CFG)
            invalid["vision"]["roi"] = [-1, 0, 100, 100]
            with self.assertRaises(ValueError):
                save_calibration(path, invalid)
            self.assertEqual(load_config(path), updated)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_missing_camera_releases_device(self):
        device = Mock()
        device.isOpened.return_value = False
        with patch("cv2.VideoCapture", return_value=device):
            with self.assertRaises(SorterFault):
                open_camera(1, CFG["camera"])
        device.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
