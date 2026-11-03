from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from app.dingtalk import DingTalkClient, DingTalkConfig
from app.submission import run_limited, submission_progress


class SubmissionProgressTest(unittest.TestCase):
    def test_progress_contract_matches_api_example(self) -> None:
        attempts = (
            [{"status": "submitted"}] * 5
            + [{"status": "queued"}] * 4
            + [{"status": "submitting"}] * 2
            + [{"status": "failed"}]
        )
        self.assertEqual(
            submission_progress(12, attempts),
            {
                "total": 12,
                "completed": 5,
                "queued": 4,
                "processing": 2,
                "failed": 1,
                "uncertain": 0,
                "percent": 42,
            },
        )

    def test_batch_concurrency_is_clamped_to_safe_range(self) -> None:
        client = DingTalkClient(DingTalkConfig())
        self.addCleanup(client.close)
        for value, expected in (("0", 1), ("3", 3), ("8", 5), ("invalid", 3)):
            with self.subTest(value=value), patch.dict(
                os.environ, {"DINGTALK_BATCH_CONCURRENCY": value}
            ):
                self.assertEqual(client.batch_concurrency, expected)


class SubmissionConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_12_19_and_25_groups_never_exceed_three_workers(self) -> None:
        for count in (12, 19, 25):
            active = 0
            peak = 0

            async def worker(_: int) -> None:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.002)
                active -= 1

            await run_limited(range(count), worker, 3)
            with self.subTest(count=count):
                self.assertEqual(peak, 3)
                self.assertEqual(active, 0)


if __name__ == "__main__":
    unittest.main()
