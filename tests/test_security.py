import os
import tempfile
import unittest

from app.db import Database
from app.security import SecurityGuard
from app.config import settings


class SecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()
        await self.db.execute(
            "INSERT INTO users(username,api_key_hash,weekly_limit,reset_time,created_at) VALUES(?,?,?,?,datetime('now'))",
            ("u", "fingerprint", 25, "2099-01-01T00:00:00+00:00"),
        )
        self.guard = SecurityGuard(self.db)

    async def asyncTearDown(self):
        os.unlink(self.tmp.name)

    def test_secret_scan_does_not_flag_normal_code(self):
        self.assertIsNone(self.guard.scan_payload({"messages": [{"content": "def add(a, b): return a + b"}]}))

    def test_secret_scan_blocks_high_confidence_token(self):
        match = self.guard.scan_payload({"input": "sk-" + "abcdefghijklmnopqrstuvwxyz1234567890"})
        self.assertIsNotNone(match)
        self.assertEqual(match.match_type, "openai_api_key")

    async def test_rate_limit_and_audit(self):
        for _ in range(settings.rate_limit_per_minute):
            allowed, status, _, _ = await self.guard.check_rate(1, "fingerprint", "127.0.0.1")
            self.assertTrue(allowed)
            self.assertEqual(status, 200)
        allowed, status, code, retry_after = await self.guard.check_rate(1, "fingerprint", "127.0.0.1")
        self.assertFalse(allowed)
        self.assertEqual(status, 429)
        self.assertEqual(code, "rate_limit_exceeded")
        self.assertGreater(retry_after, 0)
        await self.guard.audit(1, "rate_limit", "warning", "127.0.0.1", "rate_limit", {"reason": code})
        row = await self.db.fetchone("SELECT event_type,action FROM security_events")
        self.assertEqual((row["event_type"], row["action"]), ("rate_limit", "rate_limit"))


if __name__ == "__main__":
    unittest.main()
