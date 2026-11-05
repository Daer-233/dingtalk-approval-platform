from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.dingtalk import chinese_upper_money
from app.splitter import HEADERS, analyze_payment_allowances, generate_selected_payment_files


def make_book(path: Path, rows: list[list[object]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.cell(1, 1, "2026年8月补贴明细")
    ws.append(HEADERS)
    for index, values in enumerate(rows, 1):
        ws.append([index, *values, None, ""])
    wb.save(path)


class WorkflowTest(unittest.TestCase):
    def test_dynamic_unknown_cross_company_and_selected_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "8月.xlsx"
            rows = [
                ["深圳再造", "甲", "供应链中心", "采购部", "采购组", 100, 20, 10],
                ["深圳再造", "乙", "供应链中心", "仓储物流部", "仓储组", 80, 0, 0],
                ["新进化", "丙", "创新中心", "产品部", "试验组", 90, 10, 0],
                ["上海再造", "丁", "创新中心", "产品部", "试验组", 70, 0, 5],
            ]
            make_book(source, rows)
            analysis = analyze_payment_allowances(source)
            self.assertEqual(analysis["source_rows"], 4)
            self.assertEqual(analysis["actual_groups"], 3)
            self.assertEqual(analysis["unknown_groups"], 2)
            self.assertEqual(analysis["payment_total"], 385)
            choices = []
            for group in analysis["groups"]:
                choices.append({"group_id": group["id"], "selected": True, "payment_department": group["payment_department"] or "创新产品组"})
            result = generate_selected_payment_files(source, root / "out", choices)
            self.assertEqual(result["payment_departments"], 3)
            self.assertEqual(result["payment_total"], 385)
            self.assertTrue(Path(result["zip"]).is_file())
            # The same payment department in two companies must stay separate.
            self.assertEqual(sum(1 for item in result["departments"] if item["payment_department"] == "创新产品组"), 2)
            supply = next(item for item in result["departments"] if item["payment_department"] == "供应链中心")
            self.assertEqual(len(supply["department_allocations"]), 2)
            self.assertEqual(
                sum(item["金额（元）"] for item in supply["department_allocations"]),
                supply["amount"],
            )
            self.assertIn("供应链中心 / 采购部 / 采购组", supply["department_allocations"][0]["原表部门"])
            summary = load_workbook(result["summary"], data_only=False)["付款汇总"]
            self.assertEqual(summary.cell(6, 5).value, "=SUM(E3:E5)")

    def test_unselected_unknown_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "8月.xlsx"
            make_book(source, [["深圳再造", "甲", "供应链中心", "采购部", "采购组", 100, 0, 0], ["深圳再造", "乙", "新部门", "", "", 20, 0, 0]])
            analysis = analyze_payment_allowances(source)
            choices = [{"group_id": group["id"], "selected": bool(group["known_rule"]), "payment_department": group["payment_department"]} for group in analysis["groups"]]
            result = generate_selected_payment_files(source, root / "out", choices)
            self.assertEqual(result["payment_departments"], 1)
            self.assertEqual(result["payment_total"], 100)

    def test_uppercase_money(self) -> None:
        self.assertEqual(chinese_upper_money(0), "零元整")
        self.assertEqual(chinese_upper_money(1234.56), "壹仟贰佰叁拾肆元伍角陆分")

    def test_twelve_and_twenty_five_groups_have_no_fixed_limit(self) -> None:
        for count in (12, 19, 25):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "8月.xlsx"
                rows = [["测试公司", f"员工{i}", f"中心{i}", f"部门{i}", f"小组{i}", 10, 0, 0] for i in range(count)]
                make_book(source, rows)
                mappings = {("测试公司", f"小组{i}"): f"付款组{i}" for i in range(count)}
                analysis = analyze_payment_allowances(source, mappings)
                self.assertEqual(analysis["actual_groups"], count)
                choices = [{"group_id": group["id"], "selected": True, "payment_department": group["payment_department"]} for group in analysis["groups"]]
                result = generate_selected_payment_files(source, root / "out", choices, mappings)
                self.assertEqual(result["payment_departments"], count)

    def test_zero_fee_rows_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "8月.xlsx"
            make_book(source, [["深圳再造", "零费用", "供应链中心", "采购部", "采购组", 0, 0, 0], ["深圳再造", "有费用", "供应链中心", "采购部", "采购组", 1, 0, 0]])
            analysis = analyze_payment_allowances(source)
            self.assertEqual(analysis["source_rows"], 1)


if __name__ == "__main__":
    unittest.main()
