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

    async def test_task_finalization_is_exactly_once(self):
        ok, info = await self.q.reserve(1, 100)
        self.assertTrue(ok)
        await self.db.execute(
            "INSERT INTO tasks(id,user_id,status,endpoint,model,request_id,reserved_tokens,quota_state,created_at) VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
            ("task-once", 1, "running", "responses", "luna", "request-once", info["reserved"], "reserved"),
        )
        first = await self.q.finalize_task(
            "task-once", status="success", error_text=None, input_tokens=40,
            output_tokens=20, duration_ms=25, request_time="2026-08-26T00:00:00+00:00",
            model="luna", request_id="request-once", error_type=None, charge=True,
        )
        second = await self.q.finalize_task(
            "task-once", status="failed", error_text="duplicate", input_tokens=0,
            output_tokens=0, duration_ms=30, request_time="2026-08-26T00:00:00+00:00",
            model="luna", request_id="request-once", error_type="duplicate", charge=True,
        )
        self.assertTrue(first)
        self.assertFalse(second)
        user = await self.db.fetchone("SELECT weekly_used,weekly_reserved FROM users WHERE id=1")
        self.assertEqual(user["weekly_used"], 60)
        self.assertEqual(user["weekly_reserved"], 0)
        task = await self.db.fetchone("SELECT status,quota_state,actual_tokens FROM tasks WHERE id='task-once'")
        self.assertEqual(dict(task), {"status": "success", "quota_state": "settled", "actual_tokens": 60})
        logs = await self.db.fetchone("SELECT COUNT(*) AS count FROM usage_logs WHERE request_id='request-once'")
        self.assertEqual(logs["count"], 1)
        weekly = await self.db.fetchone("SELECT SUM(total_tokens) AS total,COUNT(*) AS requests FROM weekly_usage WHERE user_id=1")
        self.assertEqual(weekly["total"], 60)
        self.assertEqual(weekly["requests"], 1)

    async def test_queue_timeout_releases_only_its_reservation(self):
        ok, first = await self.q.reserve(1, 100)
        self.assertTrue(ok)
        ok, second = await self.q.reserve(1, 100)
        self.assertTrue(ok)
        await self.db.execute(
            "INSERT INTO tasks(id,user_id,status,endpoint,request_id,reserved_tokens,quota_state,created_at) VALUES(?,?,?,?,?,?,?,datetime('now'))",
            ("task-release", 1, "pending", "responses", "request-release", second["reserved"], "reserved"),
        )
        released = await self.q.finalize_task(
            "task-release", status="timed_out", error_text="queue timeout", input_tokens=0,
            output_tokens=0, duration_ms=1, request_time="2026-08-26T00:00:00+00:00",
            model="luna", request_id="request-release", error_type="queue_timeout", charge=False,
        )
        self.assertTrue(released)
        user = await self.db.fetchone("SELECT weekly_used,weekly_reserved FROM users WHERE id=1")
        self.assertEqual(user["weekly_used"], 0)
        self.assertEqual(user["weekly_reserved"], first["reserved"])

    async def test_restart_recovery_releases_task_owned_reservations(self):
        ok, first = await self.q.reserve(1, 100)
        self.assertTrue(ok)
        await self.db.execute(
            "INSERT INTO tasks(id,user_id,status,endpoint,request_id,reserved_tokens,quota_state,created_at) VALUES(?,?,?,?,?,?,?,datetime('now'))",
            ("task-recover", 1, "running", "responses", "request-recover", first["reserved"], "reserved"),
        )
        recovered = await self.q.recover_inflight("2026-08-26T00:00:00+00:00")
        self.assertEqual(recovered, 1)
        task = await self.db.fetchone("SELECT status,phase,quota_state,finished_at FROM tasks WHERE id='task-recover'")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["phase"], "failed")
        self.assertEqual(task["quota_state"], "released")
        self.assertIsNotNone(task["finished_at"])
        user = await self.db.fetchone("SELECT weekly_used,weekly_reserved FROM users WHERE id=1")
        self.assertEqual(user["weekly_used"], 0)
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
