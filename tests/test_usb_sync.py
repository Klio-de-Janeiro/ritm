import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ritm.usb_sync import devices, reconnect


class UsbTests(unittest.TestCase):
    def test_several_devices_get_independent_tunnels_and_failure_does_not_stop_others(self):
        tunnels = []
        def command(adb, *args):
            if args == ("devices",):
                return "first\tdevice\nbroken\tdevice\nsecond\tdevice\nlocked\tunauthorized"
            if args[2:5] == ("shell", "pm", "path"):
                if args[1] == "broken":
                    raise OSError("disconnected")
                return "package:/data/app/base.apk"
            if args[2:] == ("reverse", "--list"):
                return ""
            tunnels.append(args)
            return ""
        with patch("ritm.usb_sync.run", side_effect=command):
            reconnect("adb", 8769)
        self.assertEqual(tunnels, [
            ("-s", "first", "reverse", "--no-rebind", "tcp:8769", "tcp:8769"),
            ("-s", "second", "reverse", "--no-rebind", "tcp:8769", "tcp:8769"),
        ])

    def test_only_authorized_physical_devices_are_used(self):
        self.assertEqual(devices("List of devices attached\nphone\tdevice\nlocked\tunauthorized\nold\toffline\nemulator-5554\tdevice\n1.2.3.4:5555\tdevice\n"), ["phone"])

    def test_reconnect_is_idempotent_and_does_not_replace_other_tunnels(self):
        outputs=["phone\tdevice", "package:/data/app/base.apk", ""]
        with patch("ritm.usb_sync.run",side_effect=outputs+[""]) as run:
            reconnect("adb",8769)
            self.assertEqual(run.call_args.args,("adb","-s","phone","reverse","--no-rebind","tcp:8769","tcp:8769"))
        for mapping in ("phone tcp:8769 tcp:8769", "phone tcp:8769 tcp:9999"):
            with patch("ritm.usb_sync.run",side_effect=["phone\tdevice","package:/data/app/base.apk",mapping]) as run:
                reconnect("adb",8769)
                self.assertEqual(run.call_count,3)
