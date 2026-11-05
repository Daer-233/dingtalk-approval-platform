from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock, patch

from app.dingtalk import DingTalkClient, DingTalkConfig, DingTalkError


class EnterpriseDirectoryTest(unittest.TestCase):
    """All directory responses are mocked; no tokens or production data are used."""
    def setUp(self):
        self.config = DingTalkConfig(originator_user_id="applicant", originator_dept_id="10")
        with patch("app.dingtalk.httpx.Client"):
            self.client = DingTalkClient(self.config)
        self.addCleanup(self.client.close)
        self.client.access_token = Mock(return_value="mock-token")
        self.children = {1: [10, 20], 10: [11], 11: [10], 20: []}
        self.pages = {
            (1, 0): {"list": [], "has_more": False},
            (10, 0): {"list": [{"userid": "a", "name": "甲"}], "has_more": False},
            (11, 0): {"list": [{"userid": "b", "name": "乙"}], "has_more": False},
            (20, 0): {"list": [{"userid": "a", "name": "甲"}, {"userid": "outside-C", "name": "外部丙"}], "has_more": False},
        }
        self.requests = []
        self.client._request = Mock(side_effect=self.respond)

    def respond(self, method, url, **kwargs):
        self.assertEqual(method, "POST")
        self.assertFalse(kwargs["token"])
        self.assertEqual(kwargs["retries"], 2)
        body = kwargs["json"]
        dept = body["dept_id"]
        self.requests.append((url.rsplit("/", 1)[-1], dept, body.get("cursor")))
        if url.endswith("/department/listsubid"):
            return {"result": {"dept_id_list": self.children[dept]}}
        if url.endswith("/user/list"):
            self.assertEqual(body["size"], 100)
            return {"result": self.pages[(dept, body["cursor"])]}
        self.fail("Unexpected endpoint: directory tests must not create approvals")

    def paginated_directory(self):
        self.children = {1: []}
        users = [{"userid": f"User{i:03d}", "name": f"人员{i}"} for i in range(117)]
        self.pages = {
            (1, 0): {"list": users[:100], "has_more": True, "next_cursor": 100},
            (1, 100): {"list": users[100:], "has_more": False},
        }

    def test_root_traversal_includes_siblings_nested_depts_and_deduplicates(self):
        users = self.client.list_users()
        self.assertEqual(self.requests[0], ("listsubid", 1, None))
        self.assertEqual({u["user_id"] for u in users}, {"a", "b", "outside-C"})
        self.assertEqual(len(users), 3)
        self.assertEqual(sum(endpoint == "listsubid" for endpoint, _, _ in self.requests), 4)
        self.assertEqual(next(u for u in users if u["user_id"] == "outside-C")["dept_id"], "20")
        self.assertEqual(self.client.config, self.config)
        self.assertEqual(self.client.public_config()["originator_dept_id"], "10")

    def test_default_returns_all_117_people_and_reads_all_pages(self):
        self.paginated_directory()
        users = self.client.list_users()
        self.assertEqual(len(users), 117)
        self.assertEqual(len({u["user_id"] for u in users}), 117)
        self.assertIn(("list", 1, 100), self.requests)

    def test_search_finds_late_page_and_sibling_department_by_name_or_id(self):
        self.assertEqual(self.client.list_users("外部丙")[0]["user_id"], "outside-C")
        self.assertEqual(self.client.list_users("  OUTSIDE-c  ")[0]["user_id"], "outside-C")
        self.client._directory_cache = None
        self.paginated_directory()
        self.assertEqual(self.client.list_users("User116")[0]["name"], "人员116")

    def test_optional_limit_does_not_truncate_cached_directory(self):
        self.paginated_directory()
        self.assertEqual(len(self.client.list_users(limit=2)), 2)
        requests = len(self.requests)
        self.assertEqual(self.client.list_users("User116")[0]["user_id"], "User116")
        self.assertEqual(len(self.client.list_users()), 117)
        self.assertEqual(len(self.requests), requests)
        self.assertEqual(self.client.list_users(limit=0), [])

    def test_cache_reuse_expiry_and_returned_rows_cannot_mutate_cache(self):
        with patch("app.dingtalk.time.monotonic", return_value=100):
            first = self.client.list_users()
        first[0]["name"] = "changed"
        initial = len(self.requests)
        with patch("app.dingtalk.time.monotonic", return_value=101):
            self.assertEqual(self.client.list_users("甲")[0]["name"], "甲")
            self.assertEqual(len(self.requests), initial)
        with patch("app.dingtalk.time.monotonic", return_value=220):
            self.client.list_users()
        self.assertEqual(len(self.requests), initial * 2)

    def test_reload_config_clears_cache_without_changing_directory_root(self):
        self.client.list_users()
        new_config = replace(self.config, originator_dept_id="20")
        with patch("app.dingtalk.load_dingtalk_config", return_value=new_config):
            self.client.reload_config()
        self.assertIsNone(self.client._directory_cache)
        self.requests.clear()
        self.assertEqual(len(self.client.list_users()), 3)
        self.assertEqual(self.requests[0], ("listsubid", 1, None))
        self.assertEqual(self.client.config.originator_dept_id, "20")

    def test_failed_read_never_caches_or_returns_partial_directory(self):
        def fail_late(method, url, **kwargs):
            if url.endswith("/user/list") and kwargs["json"]["dept_id"] == 20:
                raise DingTalkError("部门不在授权范围内")
            return self.respond(method, url, **kwargs)
        self.client._request.side_effect = fail_late
        with self.assertRaisesRegex(DingTalkError, "授权范围"):
            self.client.list_users()
        self.assertIsNone(self.client._directory_cache)
        self.client._request.side_effect = self.respond
        self.assertEqual(len(self.client.list_users()), 3)

    def test_invalid_pagination_rejected_without_cache(self):
        self.paginated_directory()
        for cursor in (None, 0, "invalid"):
            self.pages[(1, 0)]["next_cursor"] = cursor
            with self.subTest(cursor=cursor), self.assertRaises(DingTalkError):
                self.client.list_users()
            self.assertIsNone(self.client._directory_cache)

    def test_concurrent_searches_share_one_complete_directory_read(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(self.client.list_users, ["甲", "乙", "外部", "outside"]))
        self.assertEqual([len(value) for value in values], [1, 1, 1, 1])
        self.assertEqual(len(self.requests), 8)


if __name__ == "__main__":
    unittest.main()
