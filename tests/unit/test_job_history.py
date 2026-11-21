import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backup_manager.job_history import admit, finish, phase, prune_history, request_cancel, run_rows
from backup_manager.launch_journal import register_owner
from backup_manager.process_identity import ProcessIdentity
from backup_manager.state import initialize, state_write


class JobHistoryTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.db, self.anchor = root / "state" / "db", root / "witness" / "identity"
        self.db.parent.mkdir(mode=0o700)
        self.anchor.parent.mkdir(mode=0o700)
        initialize(self.db, self.anchor)

    def test_same_job_and_global_admission_are_separate(self):
        with state_write(self.db, self.anchor) as db:
            first = register_owner(db, ProcessIdentity.capture(os.getpid()), "fixture")
            second = register_owner(db, ProcessIdentity.capture(os.getpid()), "fixture")
            run, busy = admit(db, first, "first", {}, 1)
            self.assertIsNone(busy)
            self.assertEqual(admit(db, second, "first", {}, 6), (None, "JOB_BUSY"))
            self.assertEqual(admit(db, second, "second", {}, 1), (None, "GLOBAL_BUSY"))
            self.assertTrue(request_cancel(db, run))
            finish(db, run, "cancelled", "not_run", None, None)
            self.assertFalse(request_cancel(db, run))
            self.assertIsNone(admit(db, second, "second", {}, 1)[1])

    def test_retention_age_or_count_is_per_job(self):
        with state_write(self.db, self.anchor) as db:
            owner = register_owner(db, ProcessIdentity.capture(os.getpid()), "fixture")
            runs = []
            for name, age in (("other", 30), ("job", 30), ("job", 2), ("job", 0)):
                run, _ = admit(db, owner, name, {}, 1)
                phase(db, run, "cleanup", "succeeded", data={"index": 1})
                finish(db, run, "full", "succeeded", "primary", "snapshot")
                ended = (datetime.now(timezone.utc) - timedelta(days=age)).isoformat()
                db.execute("UPDATE job_runs SET finished_at = ? WHERE run_id = ?", (ended, run))
                runs.append(run)
            prune_history(db, "job", {"history_days": 10, "history_count": 2})
            self.assertEqual({row["run_id"] for row in run_rows(db)}, {runs[0], runs[2], runs[3]})
            prune_history(db, "job", {"history_days": 10, "history_count": 1})
            self.assertEqual({row["run_id"] for row in run_rows(db)}, {runs[0], runs[3]})
