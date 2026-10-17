import ctypes
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ritm.windows_identity import WindowIdentity


@unittest.skipUnless(os.name == "nt", "Windows native input")
class FullscreenTests(unittest.TestCase):
    def setUp(self):
        self.window = WindowIdentity(Path("test-profile"), threading.Event())
        self.window.verified_windows = {(100, 42)}
        self.user = MagicMock()
        self.user.GetForegroundWindow.return_value = 100
        self.user.GetWindowThreadProcessId.side_effect = lambda hwnd, pointer: setattr(pointer._obj, "value", 42)
        self.user.GetWindowLongW.return_value = 0
        self.user.GetAsyncKeyState.return_value = 0
        self.user.SendInput.return_value = 2
        native = patch("ritm.windows_identity.ctypes.WinDLL", return_value=self.user)
        native.start()
        self.addCleanup(native.stop)
        geometry = patch.object(self.window, "_covers_monitor", return_value=True)
        geometry.start()
        self.addCleanup(geometry.stop)

    def test_waits_for_escape_release_before_sending_one_f11_pair(self):
        pressed = [True, True, False]

        def key_state(key):
            return 0x8000 if key == 0x1B and pressed.pop(0) else 0

        self.user.GetAsyncKeyState.side_effect = key_state
        self.assertTrue(self.window.exit_fullscreen()["exited"])
        self.user.SendInput.assert_called_once()
        count, inputs, size = self.user.SendInput.call_args.args
        self.assertEqual(count, 2)
        self.assertEqual(inputs[0].value.key.vk, 0x7A)
        self.assertEqual(inputs[1].value.key.flags, 2)
        self.assertEqual(size, ctypes.sizeof(inputs[0]))
        self.assertFalse(self.window.exit_fullscreen()["exited"])
        self.user.SendInput.assert_called_once()

    def test_foreground_change_while_escape_held_cancels_input(self):
        self.user.GetAsyncKeyState.return_value = 0x8000
        self.user.GetForegroundWindow.side_effect = [100, 200]
        self.assertFalse(self.window.exit_fullscreen()["exited"])
        self.user.SendInput.assert_not_called()

    def test_normal_window_never_enters_fullscreen(self):
        self.user.GetWindowLongW.return_value = 0x00C00000
        self.assertFalse(self.window.exit_fullscreen()["exited"])
        self.user.SendInput.assert_not_called()

    def test_rechecks_geometry_before_sending(self):
        self.window._covers_monitor.side_effect = [True, False]
        self.assertFalse(self.window.exit_fullscreen()["exited"])
        self.user.SendInput.assert_not_called()

    def test_fresh_window_is_verified_without_waiting_for_icon_monitor(self):
        self.window.verified_windows.clear()
        self.user.GetClassNameW.side_effect = lambda hwnd, value, size: setattr(value, "value", "Chrome_WidgetWin_1")
        self.user.GetWindowTextW.side_effect = lambda hwnd, value, size: setattr(value, "value", "Ритм · Твой день")
        with patch.object(self.window, "_profile_pids", return_value={42}):
            self.assertTrue(self.window.exit_fullscreen()["exited"])

    def test_other_browser_profile_is_rejected(self):
        self.window.verified_windows.clear()
        self.user.GetClassNameW.side_effect = lambda hwnd, value, size: setattr(value, "value", "Chrome_WidgetWin_1")
        self.user.GetWindowTextW.side_effect = lambda hwnd, value, size: setattr(value, "value", "Ритм · Твой день")
        with patch.object(self.window, "_profile_pids", return_value={777}):
            self.assertFalse(self.window.exit_fullscreen()["exited"])
        self.user.SendInput.assert_not_called()

    def test_concurrent_request_does_not_send_second_toggle(self):
        with self.window.exit_lock:
            self.assertFalse(self.window.exit_fullscreen()["exited"])
        self.user.SendInput.assert_not_called()

    def test_geometry_uses_same_dpi_context_on_second_monitor(self):
        self.user.SetThreadDpiAwarenessContext.return_value = -2

        def window_rect(hwnd, pointer):
            pointer._obj.left, pointer._obj.top = -1920, 0
            pointer._obj.right, pointer._obj.bottom = 0, 1080
            return True

        def monitor_info(monitor, pointer):
            pointer._obj.monitor.left, pointer._obj.monitor.top = -1920, 0
            pointer._obj.monitor.right, pointer._obj.monitor.bottom = 0, 1080
            return True

        self.user.GetWindowRect.side_effect = window_rect
        self.user.GetMonitorInfoW.side_effect = monitor_info
        self.assertTrue(WindowIdentity._covers_monitor(self.user, 100))
        self.user.SetThreadDpiAwarenessContext.assert_called_with(-2)
