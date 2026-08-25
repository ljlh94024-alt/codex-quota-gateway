import os
import tempfile
import unittest

from app.db import Database
from app.usage import update_weekly_usage, user_history, user_usage, week_bounds


class UsageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()
        await self.db.execute("INSERT INTO quota_state(id,total_quota,source,updated_at) VALUES(1,1000,'test','now')")
        for name in ("a", "b"):
            await self.db.execute(
                "INSERT INTO users(username,api_key_hash,weekly_limit,reset_time,created_at) VALUES(?,?,?,?,datetime('now'))",
                (name, name, 25, "2099-01-01T00:00:00+00:00"),
            )

    async def asyncTearDown(self):
        os.unlink(self.tmp.name)

    async def test_user_isolation_and_weekly_totals(self):
        await update_weekly_usage(self.db, 1, 120)
        await update_weekly_usage(self.db, 1, 30)
        await update_weekly_usage(self.db, 2, 999)
        a = await self.db.fetchone("SELECT * FROM users WHERE id=1")
        b = await self.db.fetchone("SELECT * FROM users WHERE id=2")
        a_usage = await user_usage(self.db, a)
        b_usage = await user_usage(self.db, b)
        self.assertEqual(a_usage["week_usage"]["tokens"], 150)
        self.assertEqual(a_usage["requests"], 2)
        self.assertEqual(b_usage["week_usage"]["tokens"], 999)
        self.assertNotEqual(a_usage["week_usage"]["tokens"], b_usage["week_usage"]["tokens"])
        self.assertEqual(a_usage["quota"]["basis"], "estimated")
        self.assertIn("last_sync_time", a_usage["quota"])

    async def test_week_bounds_are_monday_to_monday(self):
        start, end = week_bounds()
        self.assertEqual(start.weekday(), 0)
        self.assertEqual((end - start).days, 7)

    async def test_history_is_limited_and_has_no_prompt(self):
        for i in range(3):
            await self.db.execute(
                "INSERT INTO usage_logs(user_id,request_id,model,input_tokens,output_tokens,total_tokens,duration_ms,request_time,response_time,status,error_type,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                (1, str(i), "gpt-test", 1, 2, 3, 10, "request", "response", "success", None),
            )
        history = await user_history(self.db, 1, 2)
        self.assertEqual(len(history), 2)
        self.assertNotIn("prompt", history[0])


if __name__ == "__main__":
    unittest.main()
