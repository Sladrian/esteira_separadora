import copy
import http.client
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import load_config
from motor import Motor, validate_motor
from panel import Panel

CFG = load_config(Path(__file__).with_name("fixture.json"))


class MotorTests(unittest.TestCase):
    def setUp(self):
        self.motors = []

    def tearDown(self):
        for motor in self.motors:
            motor.close()

    def make_motor(self, settings=None, timeout=.8):
        pi = Mock(connected=True)
        for name in ("write", "set_PWM_dutycycle", "set_PWM_range", "set_PWM_frequency"):
            getattr(pi, name).return_value = 0
        with patch.dict(sys.modules, {"pigpio": SimpleNamespace(pi=Mock(return_value=pi))}):
            motor = Motor(settings or {"enabled": True}, CFG["servos"], dry_run=False, timeout=timeout)
        self.motors.append(motor)
        return motor, pi

    def test_startup_only_zeros_outputs_and_does_not_run(self):
        motor, pi = self.make_motor()
        self.assertFalse(motor.status()["running"])
        pi.set_PWM_dutycycle.assert_called_once_with(17, 0)
        self.assertEqual(pi.write.call_args_list[0].args, (27, 0))
        self.assertEqual(pi.write.call_args_list[1].args, (22, 0))
        self.assertTrue(motor.status()["available"])

    def test_start_requires_fresh_camera_but_allows_occupied_start(self):
        motor, pi = self.make_motor()
        with self.assertRaises(ValueError):
            motor.command("start", 30)
        motor.beat(False)
        motor.command("start", 30)
        pi.set_PWM_dutycycle.assert_called_with(17, 300)
        self.assertTrue(motor.status()["running"])

    def test_motor_commands_cancel_previous_schedules_without_empty_gate(self):
        import threading
        from app import command_motor
        from continuous import ContinuousController
        motor, pi = self.make_motor({"enabled": True, "direction_control": "fixed"})
        motor.beat(False)
        control = ContinuousController(CFG, Mock())
        control.submit("rosa", time.monotonic())
        self.assertTrue(control.queue)
        reset = threading.Event()
        self.assertTrue(command_motor(control, motor, reset, "start", 30)["running"])
        self.assertTrue(reset.is_set())
        self.assertEqual(control.queue, [])
        self.assertEqual(control.passages, [])
        self.assertLessEqual(control.inhibit_until, time.monotonic())
        control.submit("azul", time.monotonic())
        command_motor(control, motor, reset, "speed", 40)
        self.assertEqual(control.queue, [])
        pi.set_PWM_dutycycle.assert_called_with(17, 400)

    def test_tracker_accepts_present_cap_after_explicit_start(self):
        from continuous import Tracker
        tracker = Tracker(CFG["vision"], require_empty=False)
        detection = {"label": "azul", "center": (100, 100)}
        events = []
        for index in range(CFG["vision"]["stable_frames"]+2):
            events.extend(tracker.update([detection], index/15))
        self.assertEqual(events, [("azul", 0)])

    def test_speed_change_stop_and_no_automatic_restart(self):
        motor, pi = self.make_motor()
        motor.beat(True)
        motor.command("start", 30)
        motor.command("speed", 42)
        pi.set_PWM_dutycycle.assert_called_with(17, 420)
        motor.command("stop")
        pi.set_PWM_dutycycle.assert_called_with(17, 0)
        motor.beat(True)
        self.assertFalse(motor.status()["running"])
        with self.assertRaises(ValueError):
            motor.command("speed", 50)

    def test_watchdog_stops_without_capture_or_browser_polling(self):
        motor, pi = self.make_motor(timeout=.06)
        motor.beat(True)
        motor.command("start", 30)
        until = time.monotonic()+2
        while motor.status()["running"] and time.monotonic() < until:
            time.sleep(.01)
        self.assertFalse(motor.status()["running"])
        pi.set_PWM_dutycycle.assert_called_with(17, 0)
        motor.beat(True)
        self.assertFalse(motor.status()["running"])

    def test_disabled_motor_never_opens_gpio(self):
        factory = Mock()
        with patch.dict(sys.modules, {"pigpio": SimpleNamespace(pi=factory)}):
            motor = Motor({}, CFG["servos"], dry_run=False)
            motor.close()
        factory.assert_not_called()
        self.assertFalse(motor.status()["configured"])

    def test_fixed_direction_only_touches_enable_and_releases_direction_gpios(self):
        motor, pi = self.make_motor({"enabled": True, "direction_control": "fixed"})
        motor.beat(True)
        motor.command("start", 30)
        motor.command("stop")
        pi.write.assert_not_called()
        pi.set_PWM_dutycycle.assert_called_with(17, 0)
        servos = copy.deepcopy(CFG["servos"])
        servos["direita"]["gpio"] = 27
        validate_motor({"enabled": True, "direction_control": "fixed"}, servos)

    def test_missing_daemon_keeps_motor_unavailable_and_stopped(self):
        pi = Mock(connected=False)
        with patch.dict(sys.modules, {"pigpio": SimpleNamespace(pi=Mock(return_value=pi))}):
            motor = Motor({"enabled": True}, CFG["servos"], dry_run=False)
        self.motors.append(motor)
        self.assertFalse(motor.status()["available"])
        self.assertFalse(motor.status()["running"])

    def test_gpio_failure_zeros_outputs_and_latches_fault(self):
        motor, pi = self.make_motor()
        motor.beat(True)
        pi.set_PWM_dutycycle.side_effect = lambda pin, duty: -1 if duty else 0
        with self.assertRaises(ValueError):
            motor.command("start", 30)
        self.assertFalse(motor.status()["available"])
        self.assertFalse(motor.status()["running"])
        pi.set_PWM_dutycycle.assert_called_with(17, 0)

    def test_bad_speed_values_do_not_send_pwm(self):
        motor, pi = self.make_motor()
        motor.beat(True)
        for speed in (-1, 101, float("nan"), True, "50"):
            with self.assertRaises(ValueError):
                motor.command("start", speed)
        pi.set_PWM_dutycycle.assert_called_once_with(17, 0)

    def test_zero_percent_stops_and_reverse_only_changes_direction(self):
        motor, pi = self.make_motor({"enabled": True, "reverse": True, "channel": "B"})
        motor.beat(True)
        motor.command("start", 25)
        self.assertEqual(pi.write.call_args_list[-2].args, (27, 0))
        self.assertEqual(pi.write.call_args_list[-1].args, (22, 1))
        motor.command("speed", 0)
        self.assertFalse(motor.status()["running"])

    def test_servo_pin_conflicts_and_duplicate_motor_pins_rejected(self):
        for settings in ({"enabled": True, "pwm_gpio": 18}, {"enabled": True, "in1_gpio": 17},
                         {"enabled": True, "frequency_hz": 0}, {"enabled": True, "channel": "C"}):
            with self.assertRaises(ValueError):
                validate_motor(settings, CFG["servos"])

    def test_simulation_can_change_without_importing_pigpio(self):
        with patch.dict(sys.modules, {"pigpio": None}):
            motor = Motor({"enabled": True}, CFG["servos"], dry_run=True)
        self.motors.append(motor)
        motor.beat(True)
        self.assertTrue(motor.command("start", 30)["simulated"])


class MotorPanelTests(unittest.TestCase):
    def test_motor_commands_do_not_wait_for_camera_command_queue(self):
        motor = Motor({"enabled": True}, CFG["servos"], dry_run=True)
        motor.beat(True)
        panel = Panel(0, CFG, lambda changes: CFG, motor_command=motor.command, motor_status=motor.status)
        connection = http.client.HTTPConnection("127.0.0.1", panel.server.server_address[1], timeout=3)
        try:
            for action in ("start", "stop"):
                connection.request("POST", "/"+panel.token+"/motor", json.dumps({"action": action, "percent": 30}),
                                   {"Content-Type": "application/json"})
                reply = connection.getresponse()
                self.assertEqual(reply.status, 200)
                self.assertEqual(json.loads(reply.read())["motor"]["running"], action == "start")
            self.assertTrue(panel.commands.empty())
            connection.request("GET", "/"+panel.token+"/status")
            reply = connection.getresponse()
            self.assertFalse(json.loads(reply.read())["motor"]["running"])
        finally:
            connection.close()
            panel.close()
            motor.close()

    def test_motor_endpoint_rejects_cross_origin(self):
        callback = Mock()
        panel = Panel(0, CFG, lambda changes: CFG, motor_command=callback)
        connection = http.client.HTTPConnection("127.0.0.1", panel.server.server_address[1], timeout=3)
        try:
            connection.request("POST", "/"+panel.token+"/motor", '{"action":"start","percent":30}',
                               {"Origin": "http://foreign.example"})
            reply = connection.getresponse()
            self.assertEqual(reply.status, 403)
            reply.read()
            callback.assert_not_called()
        finally:
            connection.close()
            panel.close()


if __name__ == "__main__":
    unittest.main()
