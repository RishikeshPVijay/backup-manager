from dataclasses import replace
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from backup_manager.process_identity import Presence, ProcessIdentity


class ProcessIdentityTests(unittest.TestCase):
    def test_current_process_and_displaced_identity(self):
        identity = ProcessIdentity.capture(os.getpid())
        self.assertEqual(identity.presence(), Presence.SAME)
        self.assertEqual(replace(identity, start_ticks=identity.start_ticks + 1).presence(), Presence.GONE)

    def test_reaped_process_is_gone(self):
        with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)"]) as child:
            identity = ProcessIdentity.capture(child.pid)
            child.terminate()
            child.wait(timeout=5)
        self.assertEqual(identity.presence(), Presence.GONE)

    def test_access_error_does_not_establish_termination(self):
        identity = ProcessIdentity.capture(os.getpid())
        with patch.object(ProcessIdentity, "capture", side_effect=PermissionError):
            self.assertEqual(identity.presence(), Presence.UNKNOWN)
