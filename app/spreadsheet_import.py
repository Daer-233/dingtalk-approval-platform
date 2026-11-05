from __future__ import annotations

import shutil
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from openpyxl import Workbook

SUPPORTED_EXTENSIONS = {".xlsx", ".ods", ".numbers"}
SUPPORTED_FORMAT_LABEL = ".xlsx、.ods 和 .numbers"

REQUIRED_HEADERS = {
    "公司主体",
    "姓名",
    "一级部门",
    "二级部门",
    "三级部门",
    "日常餐补",
    "宵夜餐补",
    "出差餐补",
}
HEADER_ALIASES = {"夜宵餐补": "宵夜餐补"}
MAX_IMPORT_ROWS = 100_000
MAX_IMPORT_COLUMNS = 256


class SpreadsheetImportError(ValueError):
    pass


def workbook_extension(file_name: str) -> str:
    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise SpreadsheetImportError(f"文件格式不支持。请上传可编辑、未加密的 {SUPPORTED_FORMAT_LABEL} 文件。")
    return suffix


def normalize_workbook(source_path: Path, target_path: Path) -> Path:
    """Convert a supported workbook to the canonical .xlsx used by the splitter."""
    suffix = workbook_extension(source_path.name)
    try:
        actual_format = _detect_workbook_format(source_path, suffix)
        if actual_format == ".xlsx":
            if source_path.resolve() != target_path.resolve():
                shutil.copy2(source_path, target_path)
            return target_path
        if actual_format == ".ods":
            candidates = _read_ods(source_path)
        else:
            candidates = _read_numbers(source_path)
        _, rows = _select_data_table(candidates)
        _write_xlsx(rows, target_path, source_path.stem)
        return target_path
    except SpreadsheetImportError:
        raise
    except Exception as exc:
        raise SpreadsheetImportError(
            f"无法读取 {source_path.name}。请确认文件可编辑、未加密，且不是损坏或仅在线占位的文件。"
        ) from exc


def _detect_workbook_format(source_path: Path, claimed_format: str) -> str:
    """Identify ZIP-based spreadsheet formats by their internal structure."""
    try:
        with zipfile.ZipFile(source_path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile):
        return claimed_format
    if "content.xml" in names and "META-INF/manifest.xml" in names:
        return ".ods"
    if "[Content_Types].xml" in names and "xl/workbook.xml" in names:
        return ".xlsx"
    if any(name.endswith("Metadata/Properties.plist") for name in names) and any(name.endswith(".iwa") for name in names):
        return ".numbers"
    return claimed_format


def _clean_header(value: object) -> str:
    text = "" if value is None else str(value).strip()
    return HEADER_ALIASES.get(text, text)


def _contains_required_headers(rows: Sequence[Sequence[object]]) -> bool:
    for row in rows[:30]:
        headers = {_clean_header(value) for value in row}
        if REQUIRED_HEADERS.issubset(headers):
            return True
    return False


def _select_data_table(candidates: Iterable[tuple[str, list[list[object]]]]) -> tuple[str, list[list[object]]]:
    materialized = [(name, _trim_matrix(rows)) for name, rows in candidates]
    for name, rows in materialized:
        if rows and _contains_required_headers(rows):
            return name, rows
    raise SpreadsheetImportError("未找到符合补贴总表格式的数据表。请确认表头包含公司主体、姓名、三级部门和三类餐补列。")


def _trim_matrix(rows: Sequence[Sequence[object]]) -> list[list[object]]:
    result: list[list[object]] = []
    for source_row in rows[:MAX_IMPORT_ROWS]:
        row = list(source_row[:MAX_IMPORT_COLUMNS])
        while row and row[-1] in (None, ""):
            row.pop()
        result.append(row)
    while result and not result[-1]:
        result.pop()
    return result


def _excel_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool, date, datetime, time, timedelta)):
        return value
    return str(value)


def _write_xlsx(rows: Sequence[Sequence[object]], target_path: Path, source_name: str) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "导入数据"
    first_nonempty = next((row for row in rows if any(value not in (None, "") for value in row)), [])
    if first_nonempty and REQUIRED_HEADERS.issubset({_clean_header(value) for value in first_nonempty}):
        worksheet.append([source_name])
    for row in rows:
        worksheet.append([_excel_value(value) for value in row])
    worksheet.sheet_properties.tabColor = "5368D8"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target_path)


def _read_numbers(source_path: Path) -> list[tuple[str, list[list[object]]]]:
    try:
        from numbers_parser import Document
    except ImportError as exc:
        raise SpreadsheetImportError("当前服务缺少 Apple Numbers 导入组件，请重新启动平台以安装依赖。") from exc

    document = Document(source_path)
    try:
        candidates: list[tuple[str, list[list[object]]]] = []
        for sheet in document.sheets:
            for table in sheet.tables:
                rows = table.rows(values_only=True)
                candidates.append((f"{sheet.name} / {table.name}", rows))
        return candidates
    finally:
        # numbers-parser 4.x keeps its source ZipFile open; explicitly release it
        # so Windows can remove the uploaded temporary .numbers file.
        iwork = getattr(getattr(document._model, "objects", None), "_iwork", None)
        zip_file = getattr(iwork, "_zipf", None)
        if zip_file is not None:
            zip_file.close()


def _read_ods(source_path: Path) -> list[tuple[str, list[list[object]]]]:
    try:
        from odf import teletype
        from odf.namespaces import TABLENS
        from odf.opendocument import load
        from odf.table import Table, TableRow
    except ImportError as exc:
        raise SpreadsheetImportError("当前服务缺少 OpenDocument 导入组件，请重新启动平台以安装依赖。") from exc

    document = load(str(source_path))
    candidates: list[tuple[str, list[list[object]]]] = []
    for table in document.spreadsheet.getElementsByType(Table):
        rows: list[list[object]] = []
        for row_node in table.getElementsByType(TableRow):
            values: list[object] = []
            for cell in row_node.childNodes:
                if getattr(cell, "qname", None) not in {
                    (TABLENS, "table-cell"),
                    (TABLENS, "covered-table-cell"),
                }:
                    continue
                repeated = min(_positive_int(cell.getAttribute("numbercolumnsrepeated")), MAX_IMPORT_COLUMNS - len(values))
                if repeated <= 0:
                    break
                value = _ods_cell_value(cell, teletype)
                values.extend([value] * repeated)
            values = _trim_matrix([values])[0] if any(value not in (None, "") for value in values) else []
            row_repeated = _positive_int(row_node.getAttribute("numberrowsrepeated"))
            if values:
                rows.extend([list(values) for _ in range(min(row_repeated, MAX_IMPORT_ROWS - len(rows)))])
            elif row_repeated == 1:
                rows.append([])
            if len(rows) >= MAX_IMPORT_ROWS:
                break
        candidates.append((table.getAttribute("name") or "ODS 工作表", rows))
    return candidates


def _positive_int(value: object) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def _ods_cell_value(cell: object, teletype: object) -> object:
    value_type = cell.getAttribute("valuetype")
    if value_type in {"float", "currency", "percentage"}:
        raw = cell.getAttribute("value")
        return float(raw) if raw not in (None, "") else None
    if value_type == "boolean":
        return str(cell.getAttribute("booleanvalue")).lower() == "true"
    if value_type == "date":
        raw = cell.getAttribute("datevalue")
        if raw:
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return raw
    text = teletype.extractText(cell)
    return text if text != "" else None
