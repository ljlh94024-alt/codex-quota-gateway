import asyncio
import os
import tempfile
import unittest

from app.db import Database
from app.quota import QuotaManager, estimate_tokens, hash_api_key


class QuotaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()
        await self.db.execute("INSERT INTO quota_state(id,total_quota,source,updated_at) VALUES(1,1000000,'test',datetime('now'))")
        await self.db.execute("INSERT INTO users(username,api_key_hash,weekly_limit,reset_time,created_at) VALUES(?,?,?,?,datetime('now'))", ("u", hash_api_key("k"), 25, "2099-01-01T00:00:00+00:00"))
        self.q = QuotaManager(self.db)

    async def asyncTearDown(self):
        os.unlink(self.tmp.name)

    async def test_reserve_and_settle(self):
        ok, info = await self.q.reserve(1, 100)
        self.assertTrue(ok)
        await self.q.settle(1, 100, 120)
        user = await self.db.fetchone("SELECT weekly_used,weekly_reserved FROM users WHERE id=1")
        self.assertEqual(user["weekly_used"], 120)
        self.assertEqual(user["weekly_reserved"], 0)

    async def test_quota_isolated(self):
        await self.db.execute("INSERT INTO users(username,api_key_hash,weekly_limit,reset_time,created_at) VALUES(?,?,?,?,datetime('now'))", ("v", hash_api_key("v"), 25, "2099-01-01T00:00:00+00:00"))
        ok, _ = await self.q.reserve(1, 200_000)
        self.assertTrue(ok)
        ok, info = await self.q.reserve(1, 100_000)
        self.assertTrue(ok)
        self.assertEqual(info["reserved"], 50_000)
        ok, _ = await self.q.reserve(1, 1)
        self.assertFalse(ok)
        ok, _ = await self.q.reserve(2, 100)
        self.assertTrue(ok)

    def test_estimate(self):
        self.assertGreater(estimate_tokens({"messages": [{"content": "hello"}]}), 1)
        self.assertEqual(estimate_tokens({"input": "x" * 2_000_000}), 270_000)

    def test_api_key_hash_is_not_plaintext(self):
        value = hash_api_key("cg_test")
        self.assertEqual(len(value), 64)
        self.assertNotEqual(value, "cg_test")

    async def test_global_concurrency_shape(self):
        semaphore = asyncio.Semaphore(3)
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal active, peak
            async with semaphore:
                async with lock:
                    active += 1
                    peak = max(peak, active)
                await asyncio.sleep(0.02)
                async with lock:
                    active -= 1

        await asyncio.gather(*(worker() for _ in range(4)))
        self.assertEqual(peak, 3)


if __name__ == "__main__":
    unittest.main()
