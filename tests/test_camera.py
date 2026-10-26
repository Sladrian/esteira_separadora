import copy
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import load_config, SorterFault
from hardware import Camera, USBCamera

CFG = load_config(Path(__file__).resolve().parent / "fixture.json")["camera"]


class SelectionTests(unittest.TestCase):
    def test_auto_prefers_usb(self):
        usb = SimpleNamespace(kind="usb")
        with patch("hardware.usb_cameras", return_value=["/dev/video2"]), \
                patch("hardware.USBCamera", return_value=usb) as factory, \
                patch("hardware.CSICamera") as csi:
            self.assertIs(Camera(CFG).camera, usb)
            factory.assert_called_once_with(CFG, "/dev/video2")
            csi.assert_not_called()

    def test_no_usb_uses_csi(self):
        with patch("hardware.usb_cameras", return_value=[]), \
                patch("hardware.CSICamera", return_value=SimpleNamespace(kind="csi")):
            self.assertEqual(Camera(CFG).kind, "csi")

    def test_bad_usb_falls_back_only_at_startup(self):
        with patch("hardware.usb_cameras", return_value=["/dev/video0"]), \
                patch("hardware.USBCamera", side_effect=SorterFault("sem imagem")), \
                patch("hardware.CSICamera", return_value=SimpleNamespace(kind="csi")):
            self.assertEqual(Camera(CFG).kind, "csi")

    def test_runtime_failure_does_not_switch_cameras(self):
        usb = Mock(kind="usb")
        usb.read.side_effect = SorterFault("desconectada")
        with patch("hardware.usb_cameras", return_value=["/dev/video0"]), \
                patch("hardware.USBCamera", return_value=usb), patch("hardware.CSICamera") as csi:
            camera = Camera(CFG)
            with self.assertRaises(SorterFault):
                camera.read()
            csi.assert_not_called()

    def test_explicit_usb_never_falls_back(self):
        with patch("hardware.usb_cameras", return_value=[]), patch("hardware.CSICamera") as csi:
            with self.assertRaises(SorterFault):
                Camera(dict(CFG, backend="usb"))
            csi.assert_not_called()

    def test_multiple_usb_requires_choice(self):
        with patch("hardware.usb_cameras", return_value=["/dev/video0", "/dev/video2"]):
            with self.assertRaisesRegex(SorterFault, "Mais de uma"):
                Camera(CFG)

    def test_explicit_csi_does_not_probe_usb(self):
        with patch("hardware.usb_cameras") as usb, \
                patch("hardware.CSICamera", return_value=SimpleNamespace(kind="csi")):
            self.assertEqual(Camera(dict(CFG, backend="csi")).kind, "csi")
            usb.assert_not_called()


class FakeCapture:
    def __init__(self):
        self.released = False
        self.unplugged = False
        self.failed = threading.Event()

    def isOpened(self):
        return True

    def set(self, *_):
        return True

    def read(self):
        time.sleep(.01)
        if self.unplugged:
            self.failed.set()
            return False, None
        return True, np.zeros((480, 640, 3), np.uint8)

    def release(self):
        self.released = True


class USBTests(unittest.TestCase):
    def test_frames_have_target_size_and_increasing_sequence(self):
        capture = FakeCapture()
        with patch("cv2.VideoCapture", return_value=capture):
            camera = USBCamera(dict(CFG, warmup_s=0), "/dev/video0")
            try:
                frame, timestamp, metadata = camera.read()
                self.assertEqual(frame.shape, (240, 320, 3))
                self.assertLess(abs(time.monotonic()-timestamp), .5)
                _, _, next_metadata = camera.read()
                self.assertGreater(next_metadata["FrameSequence"], metadata["FrameSequence"])
                self.assertNotIn("SensorTimestamp", metadata)
            finally:
                camera.close()
        self.assertTrue(capture.released)
        self.assertFalse(camera.thread.is_alive())

    def test_disconnect_surfaces_as_fault(self):
        capture = FakeCapture()
        with patch("cv2.VideoCapture", return_value=capture):
            camera = USBCamera(dict(CFG, warmup_s=0), "/dev/video0")
            try:
                capture.unplugged = True
                self.assertTrue(capture.failed.wait(1))
                # Espera a publicação do erro pela thread, sem depender do escalonador.
                with camera.condition:
                    self.assertTrue(camera.condition.wait_for(lambda: camera.error, timeout=1))
                with self.assertRaises(SorterFault):
                    camera.read()
            finally:
                camera.close()

    def test_usb_balance_has_actionable_message(self):
        from app import photograph
        camera = Mock(kind="usb")
        camera.read.return_value = np.zeros((240, 320, 3), np.uint8), time.monotonic(), {}
        with patch("app.Camera", return_value=camera):
            with self.assertRaisesRegex(SorterFault, "controles UVC"):
                photograph({"camera": CFG}, SimpleNamespace(command="balance"))
        camera.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
