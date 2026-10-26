from pathlib import Path
import copy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vision import learn_ranges
from core import load_config


def sample(parts):
    hues = np.concatenate([np.full(count, hue, np.uint8) for hue, count in parts])
    hsv = np.zeros((1, len(hues), 3), np.uint8)
    hsv[0, :, 0] = hues
    hsv[0, :, 1:] = 210
    return hsv, np.full(hsv.shape[:2], 255, np.uint8)


class CalibrationTests(unittest.TestCase):
    def test_cli_does_not_save_a_color_conflicting_with_another(self):
        from app import photograph
        cfg = load_config(Path(__file__).resolve().parent / 'fixture.json')
        camera = Mock(kind='csi')
        camera.read.return_value = np.zeros((240,320,3),np.uint8), 0, {'ColourGains':[1,1]}
        detector = Mock()
        detector.inspect.return_value = 'verde', {'verde':.8,'azul':.8}, None, None
        with patch('app.Camera',return_value=camera), patch('vision.Vision',return_value=detector), \
                patch('vision.learn_ranges',return_value=[[[50,50,50],[70,255,255]]]), patch('app.save_config') as save:
            with self.assertRaisesRegex(ValueError,'configuração preservada'):
                photograph(copy.deepcopy(cfg),SimpleNamespace(command='learn',color='azul',config='unused',output='unused'))
            save.assert_not_called()

    def test_dominant_color_survives_minority_distant_tones(self):
        hsv, mask = sample([(110, 760), (20, 240)])
        ranges = learn_ranges(hsv, mask, 100)
        self.assertTrue(any(lo[0] <= 110 <= hi[0] for lo,hi in ranges))
        self.assertFalse(any(lo[0] <= 20 <= hi[0] for lo,hi in ranges))

    def test_balanced_two_color_sample_is_rejected(self):
        hsv, mask = sample([(110, 500), (20, 500)])
        with self.assertRaises(ValueError):
            learn_ranges(hsv, mask, 100)

    def test_insufficient_dominance_is_rejected(self):
        hsv, mask = sample([(110, 650), (20, 350)])
        with self.assertRaises(ValueError):
            learn_ranges(hsv, mask, 100)

    def test_dominant_hue_wrap_remains_circular(self):
        hsv, mask = sample([(178, 425), (1, 425), (60, 150)])
        ranges = learn_ranges(hsv, mask, 100)
        self.assertEqual(len(ranges), 2)
        for hue in (178, 1):
            self.assertTrue(any(lo[0] <= hue <= hi[0] for lo,hi in ranges))
        self.assertFalse(any(lo[0] <= 60 <= hi[0] for lo,hi in ranges))

    def test_too_few_pixels_after_filtering_is_rejected(self):
        hsv, mask = sample([(110, 76), (20, 24)])
        with self.assertRaises(ValueError):
            learn_ranges(hsv, mask, 100)

    def test_broad_uniform_hues_are_rejected(self):
        hsv, mask = sample([(hue, 10) for hue in range(180)])
        with self.assertRaises(ValueError):
            learn_ranges(hsv, mask, 100)
