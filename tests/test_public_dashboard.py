import os
import tempfile
import unittest

from app.db import Database
from app.usage import public_dashboard_usage, update_weekly_usage


class PublicDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()
        await self.db.execute("INSERT INTO quota_state(id,total_quota,source,updated_at) VALUES(1,1000,'test','sync')")
        await self.db.execute("INSERT INTO users(username,api_key_hash,weekly_limit,reset_time,created_at) VALUES('private-user','secret-hash',25,'2099-01-01','now')")
        await update_weekly_usage(self.db, 1, 100)

    async def asyncTearDown(self):
        os.unlink(self.tmp.name)

    async def test_public_data_is_anonymous_and_estimated(self):
        data = await public_dashboard_usage(self.db)
        self.assertEqual(data["quota_basis"], "estimated")
        self.assertEqual(data["users"][0]["name"], "private-user")
        self.assertNotIn("api_key_hash", data["users"][0])
        self.assertNotIn("api_key", str(data))
        self.assertNotIn("secret-hash", str(data))


if __name__ == "__main__":
    unittest.main()
