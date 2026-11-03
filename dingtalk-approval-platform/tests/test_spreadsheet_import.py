from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from numbers_parser import Document
from odf.opendocument import OpenDocumentSpreadsheet
from odf.table import Table, TableCell, TableRow
from odf.text import P

from app.splitter import HEADERS, analyze_payment_allowances
from app.spreadsheet_import import SpreadsheetImportError, normalize_workbook, workbook_extension


DATA_ROW = [1, "示例公司", "测试员工", "供应链中心", "采购部", "采购组", 100, 20, 10, None, ""]


def write_ods(path: Path) -> None:
    document = OpenDocumentSpreadsheet()
    table = Table(name="补贴总表")
    for values in (["2026年8月补贴明细"], HEADERS, DATA_ROW):
        row = TableRow()
        for value in values:
            if isinstance(value, (int, float)):
                cell = TableCell(valuetype="float", value=value)
            else:
                cell = TableCell(valuetype="string")
                if value not in (None, ""):
                    cell.addElement(P(text=str(value)))
            row.addElement(cell)
        table.addElement(row)
    document.spreadsheet.addElement(table)
    document.save(str(path))


def write_numbers(path: Path) -> None:
    document = Document(None, sheet_name="封面", table_name="空表", num_rows=2, num_cols=2, num_header_rows=0, num_header_cols=0)
    document.add_sheet("补贴数据")
    sheet = document.sheets[-1]
    table = sheet.add_table("补贴总表", num_rows=2, num_cols=len(HEADERS), num_header_rows=0, num_header_cols=0)
    for row_index, values in enumerate((HEADERS, DATA_ROW)):
        for col_index, value in enumerate(values):
            if value is not None:
                table.write(row_index, col_index, value)
    document.save(path)


class SpreadsheetImportTest(unittest.TestCase):
    def test_ods_is_normalized_and_analyzed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "8月测试总表.ods"
            target = Path(temp) / "normalized.xlsx"
            write_ods(source)
            normalize_workbook(source, target)
            analysis = analyze_payment_allowances(target)
            self.assertEqual(analysis["source_rows"], 1)
            self.assertEqual(analysis["payment_total"], 130)
            self.assertEqual(analysis["month"], "8月")

    def test_numbers_selects_the_table_with_required_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "8月测试总表.numbers"
            target = Path(temp) / "normalized.xlsx"
            write_numbers(source)
            normalize_workbook(source, target)
            analysis = analyze_payment_allowances(target)
            self.assertEqual(analysis["source_rows"], 1)
            self.assertEqual(analysis["payment_total"], 130)
            self.assertEqual(analysis["actual_groups"], 1)
            self.assertEqual(analysis["month"], "8月")

    def test_ods_content_with_numbers_extension_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "8月测试总表.numbers"
            target = Path(temp) / "normalized.xlsx"
            write_ods(source)
            normalize_workbook(source, target)
            analysis = analyze_payment_allowances(target)
            self.assertEqual(analysis["source_rows"], 1)
            self.assertEqual(analysis["payment_total"], 130)
            self.assertEqual(analysis["month"], "8月")

    def test_extension_validation_is_case_insensitive(self) -> None:
        self.assertEqual(workbook_extension("测试总表.NUMBERS"), ".numbers")
        with self.assertRaises(SpreadsheetImportError):
            workbook_extension("测试总表.xls")


if __name__ == "__main__":
    unittest.main()
