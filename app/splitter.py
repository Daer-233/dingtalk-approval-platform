from __future__ import annotations

import argparse
import hashlib
import re
import zipfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

HEADERS = ["序号", "公司主体", "姓名", "一级部门", "二级部门", "三级部门", "日常餐补", "宵夜餐补", "出差餐补", "合计/元", "备注"]
TITLE_FILL = PatternFill("solid", fgColor="5368D8")
HEADER_FILL = PatternFill("solid", fgColor="E7EBFF")
TOTAL_FILL = PatternFill("solid", fgColor="FFF2CC")
THIN_SIDE = Side(style="thin", color="C7CEE6")
ALL_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
FONT_NAME = "Microsoft YaHei"


def clean_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def to_number(value: object) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(clean_text(value).replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"餐补金额不是有效数字：{value!r}") from exc


def display_number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else round(value, 2)


def extract_month(title: str, file_name: str) -> tuple[str, str]:
    match = re.search(r"(1[0-2]|[1-9])\s*月", title) or re.search(r"(1[0-2]|[1-9])\s*月", file_name)
    if match:
        return f"{int(match.group(1))}月", f"{int(match.group(1))}月份"
    return "当月", "当月"


def find_header_row(ws: Worksheet) -> tuple[int, Dict[str, int]]:
    aliases = {name: name for name in HEADERS}
    aliases["夜宵餐补"] = "宵夜餐补"
    required = {"公司主体", "姓名", "一级部门", "二级部门", "三级部门", "日常餐补", "宵夜餐补", "出差餐补"}
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 30)):
        mapping: Dict[str, int] = {}
        for cell in row:
            key = aliases.get(clean_text(cell.value))
            if key:
                mapping[key] = cell.column
        if required.issubset(mapping):
            return row[0].row, mapping
    raise ValueError("未找到表头。请确保包含公司主体、姓名、三级部门和三类餐补列。")


def read_rows(input_path: Path) -> tuple[List[Dict[str, object]], str]:
    try:
        wb = load_workbook(input_path, data_only=False)
    except Exception as exc:
        raise RuntimeError(f"无法读取 {input_path.name}。请使用可编辑、未加密的 .xlsx 副本作为输入文件。") from exc
    ws = wb.active
    header_row, col = find_header_row(ws)
    title = clean_text(ws.cell(1, 1).value) or input_path.stem
    rows: List[Dict[str, object]] = []
    for excel_row in range(header_row + 1, ws.max_row + 1):
        company = clean_text(ws.cell(excel_row, col["公司主体"]).value)
        name = clean_text(ws.cell(excel_row, col["姓名"]).value)
        if not company or company in {"合计", "总计"} or not name:
            continue
        item = {
            "公司主体": company, "姓名": name,
            "一级部门": clean_text(ws.cell(excel_row, col["一级部门"]).value),
            "二级部门": clean_text(ws.cell(excel_row, col["二级部门"]).value),
            "三级部门": clean_text(ws.cell(excel_row, col["三级部门"]).value),
            "日常餐补": to_number(ws.cell(excel_row, col["日常餐补"]).value),
            "宵夜餐补": to_number(ws.cell(excel_row, col["宵夜餐补"]).value),
            "出差餐补": to_number(ws.cell(excel_row, col["出差餐补"]).value),
            "备注": clean_text(ws.cell(excel_row, col.get("备注", 0)).value) if col.get("备注") else "",
        }
        # A zero-fee row cannot produce a payment and is omitted from dynamic groups.
        if not any(to_number(item[key]) for key in ("日常餐补", "宵夜餐补", "出差餐补")):
            continue
        rows.append(item)
    if not rows:
        raise ValueError("未读取到有效费用明细。")
    return rows, title


@dataclass(frozen=True)
class PaymentRule:
    filename_stem: str
    payment_department: str
    company: str
    scope: str
    predicate: Callable[[Mapping[str, object]], bool]


def build_rules(month: str) -> List[PaymentRule]:
    return [
        PaymentRule(f"财务管理中心{month}补贴明细", "财务管理中心", "深圳再造", "财务管理中心", lambda r: r["公司主体"] == "深圳再造" and r["一级部门"] == "财务管理中心"),
        PaymentRule(f"发展中心{month}补贴明细", "发展中心", "新进化", "发展中心", lambda r: r["公司主体"] == "新进化" and r["一级部门"] == "发展中心"),
        PaymentRule(f"供应链中心{month}补贴明细", "供应链中心", "深圳再造", "供应链中心全部层级", lambda r: r["公司主体"] == "深圳再造" and r["一级部门"] == "供应链中心"),
        PaymentRule(f"人力资源组{month}补贴明细", "人力资源组", "深圳再造", "人力资源组、薪酬绩效组", lambda r: r["公司主体"] == "深圳再造" and r["一级部门"] == "人力行政中心" and r["二级部门"] in {"人力资源组", "薪酬绩效组"}),
        PaymentRule(f"软装A、B组{month}补贴明细", "软装A、B组", "新进化", "软装深圳A组、软装深圳B组", lambda r: r["公司主体"] == "新进化" and r["三级部门"] in {"软装深圳A组", "软装深圳B组"}),
        PaymentRule(f"软装C组{month}补贴明细", "软装C组", "新进化", "软装深圳C组", lambda r: r["公司主体"] == "新进化" and r["三级部门"] == "软装深圳C组"),
        PaymentRule(f"软装D组{month}补贴明细", "软装D组", "新进化", "软装深圳D组", lambda r: r["公司主体"] == "新进化" and r["三级部门"] == "软装深圳D组"),
        PaymentRule(f"软装上海组{month}补贴明细-上海再造", "软装上海组", "上海再造", "软装上海组", lambda r: r["公司主体"] == "上海再造" and r["三级部门"] == "软装上海组"),
        PaymentRule(f"软装上海组{month}补贴明细-新进化", "软装上海组", "新进化", "软装上海组", lambda r: r["公司主体"] == "新进化" and r["三级部门"] == "软装上海组"),
        PaymentRule(f"上海人力行政中心{month}补贴明细", "上海人力行政中心", "上海再造", "上海人力行政中心", lambda r: r["公司主体"] == "上海再造" and r["一级部门"] == "人力行政中心"),
        PaymentRule(f"行政管理组{month}补贴明细-老进化", "行政管理组", "老进化", "行政管理组", lambda r: r["公司主体"] == "老进化" and r["三级部门"] == "行政管理组"),
        PaymentRule(f"行政管理组{month}补贴明细-深圳再造", "行政管理组", "深圳再造", "行政管理组", lambda r: r["公司主体"] == "深圳再造" and r["三级部门"] == "行政管理组"),
        PaymentRule(f"营销及品牌部{month}补贴明细", "营销及品牌部", "深圳再造", "营销及品牌部", lambda r: r["公司主体"] == "深圳再造" and r["一级部门"] == "营销及品牌部"),
        PaymentRule(f"硬装地产组{month}补贴明细-上海再造", "硬装地产组", "上海再造", "地产组", lambda r: r["公司主体"] == "上海再造" and r["一级部门"] == "硬装事业部" and r["二级部门"] == "地产组"),
        PaymentRule(f"硬装地产组{month}补贴明细-新进化", "硬装地产组", "新进化", "地产组", lambda r: r["公司主体"] == "新进化" and r["一级部门"] == "硬装事业部" and r["二级部门"] == "地产组"),
        PaymentRule(f"硬装技术组{month}补贴明细", "硬装技术组", "新进化", "硬装技术组全部层级", lambda r: r["公司主体"] == "新进化" and r["一级部门"] == "硬装事业部" and r["二级部门"] == "技术组"),
        PaymentRule(f"硬装商业组{month}补贴明细", "硬装商业组", "新进化", "商业组", lambda r: r["公司主体"] == "新进化" and r["一级部门"] == "硬装事业部" and r["二级部门"] == "商业组"),
        PaymentRule(f"总经办{month}补贴明细-老进化", "总经办", "老进化", "总经办", lambda r: r["公司主体"] == "老进化" and r["一级部门"] == "总经办"),
        PaymentRule(f"总经办{month}补贴明细-再造", "总经办", "深圳再造", "总经办", lambda r: r["公司主体"] == "深圳再造" and r["一级部门"] == "总经办"),
    ]


def deepest_department(row: Mapping[str, object]) -> str:
    return clean_text(row.get("三级部门")) or clean_text(row.get("二级部门")) or clean_text(row.get("一级部门")) or "未命名部门"


def group_id(company: str, key: str) -> str:
    return hashlib.sha1(f"{company}\0{key}".encode("utf-8")).hexdigest()[:14]


def _amounts(rows: Sequence[Mapping[str, object]]) -> dict[str, int | float]:
    daily = sum(to_number(row["日常餐补"]) for row in rows)
    night = sum(to_number(row["宵夜餐补"]) for row in rows)
    travel = sum(to_number(row["出差餐补"]) for row in rows)
    return {"count": len(rows), "daily": display_number(daily), "night": display_number(night), "travel": display_number(travel), "amount": display_number(daily + night + travel)}


def department_hierarchy(row: Mapping[str, object]) -> list[str]:
    return [
        clean_text(row.get(name))
        for name in ("一级部门", "二级部门", "三级部门")
        if clean_text(row.get(name))
    ]


def dingtalk_department_candidates(
    hierarchy: Sequence[str], payment_department: str
) -> list[str]:
    distinct = list(dict.fromkeys(clean_text(value) for value in hierarchy if clean_text(value)))
    candidates: list[str] = []
    if len(distinct) >= 3:
        candidates.append(f"{distinct[-2]}-{distinct[-1]}")
    if len(distinct) >= 2:
        candidates.append(f"{distinct[0]}-{distinct[1]}")
    candidates.extend(reversed(distinct))
    candidates.append(clean_text(payment_department))
    return list(dict.fromkeys(value for value in candidates if value))


def department_allocations(
    rows: Sequence[Mapping[str, object]], payment_department: str
) -> list[dict[str, object]]:
    grouped: "OrderedDict[tuple[str, ...], list[Mapping[str, object]]]" = OrderedDict()
    for row in rows:
        hierarchy = tuple(department_hierarchy(row))
        grouped.setdefault(hierarchy, []).append(row)
    allocations: list[dict[str, object]] = []
    for hierarchy, department_rows in grouped.items():
        candidates = dingtalk_department_candidates(hierarchy, payment_department)
        allocations.append({
            "费用科目": "福利费",
            "原表部门": " / ".join(hierarchy) or payment_department,
            "发生费用部门": candidates[0] if candidates else payment_department,
            "金额（元）": _amounts(department_rows)["amount"],
            "_department_candidates": candidates,
        })
    return allocations


def analyze_payment_allowances(input_path: Path, mappings: Mapping[tuple[str, str], str] | None = None) -> Dict[str, object]:
    rows, title = read_rows(input_path)
    short_month, full_month = extract_month(title, input_path.stem)
    mappings = mappings or {}
    rules = build_rules(short_month)
    grouped: "OrderedDict[tuple[str, str, str], dict[str, object]]" = OrderedDict()
    for index, row in enumerate(rows):
        matched = [rule for rule in rules if rule.predicate(row)]
        if len(matched) > 1:
            raise ValueError(f"第 {index + 1} 条明细同时命中多个付款规则，请检查规则。")
        company = str(row["公司主体"])
        source = deepest_department(row)
        if matched:
            rule = matched[0]
            key = (company, "known", rule.payment_department)
            payment_department, needs_mapping, scope, known = rule.payment_department, False, rule.scope, True
        else:
            key = (company, "source", source)
            payment_department = mappings.get((company, source), "")
            needs_mapping, scope, known = not bool(payment_department), source, False
        bucket = grouped.setdefault(key, {"company": company, "payment_department": payment_department, "needs_mapping": needs_mapping, "known_rule": known, "scope": scope, "source_departments": [], "row_indices": []})
        bucket["row_indices"].append(index)
        if source not in bucket["source_departments"]:
            bucket["source_departments"].append(source)

    groups = []
    for (_, kind, raw_key), bucket in grouped.items():
        selected_rows = [rows[i] for i in bucket["row_indices"]]
        groups.append({"id": group_id(str(bucket["company"]), f"{kind}:{raw_key}"), "company": bucket["company"],
                       "payment_department": bucket["payment_department"], "suggested_department": raw_key,
                       "needs_mapping": bucket["needs_mapping"], "known_rule": bucket["known_rule"], "scope": bucket["scope"],
                       "source_departments": bucket["source_departments"], "selected": True, **_amounts(selected_rows)})
    total = _amounts(rows)
    return {"source_rows": len(rows), "actual_groups": len(groups), "unknown_groups": sum(1 for group in groups if group["needs_mapping"]),
            "daily_total": total["daily"], "night_total": total["night"], "travel_total": total["travel"], "payment_total": total["amount"],
            "month": short_month, "full_month": full_month, "title": title, "groups": groups}


def set_sheet_title(ws: Worksheet, title: str, last_col: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    cell = ws.cell(1, 1, title)
    cell.fill = TITLE_FILL
    cell.font = Font(name=FONT_NAME, size=14, bold=True, color="FFFFFF")
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28


def style_headers(ws: Worksheet, row: int, last_col: int) -> None:
    for col in range(1, last_col + 1):
        cell = ws.cell(row, col)
        cell.fill, cell.font, cell.border = HEADER_FILL, Font(name=FONT_NAME, bold=True), ALL_BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def apply_table_style(ws: Worksheet, start_row: int, end_row: int, last_col: int, numeric_cols: Iterable[int]) -> None:
    numeric = set(numeric_cols)
    for row in ws.iter_rows(min_row=start_row, max_row=end_row, min_col=1, max_col=last_col):
        for cell in row:
            cell.border, cell.font = ALL_BORDER, Font(name=FONT_NAME, size=10)
            cell.alignment = Alignment(horizontal="right" if cell.column in numeric else "center", vertical="center", wrap_text=cell.column == last_col)
            if cell.column in numeric:
                cell.number_format = '#,##0.00;[Red]-#,##0.00'


def set_detail_widths(ws: Worksheet) -> None:
    for col, width in enumerate([8, 14, 12, 15, 15, 16, 12, 12, 12, 12, 28], 1):
        ws.column_dimensions[get_column_letter(col)].width = width


def write_detail_workbook(rows: Sequence[Mapping[str, object]], path: Path, title: str) -> Dict[str, int | float]:
    wb = Workbook()
    ws = wb.active
    ws.title, ws.sheet_view.showGridLines = "补贴明细", False
    set_sheet_title(ws, title, 11)
    ws.append(HEADERS)
    style_headers(ws, 2, 11)
    for index, row in enumerate(rows, start=1):
        excel_row = index + 2
        ws.append([index, row["公司主体"], row["姓名"], row["一级部门"], row["二级部门"], row["三级部门"],
                   display_number(to_number(row["日常餐补"])), display_number(to_number(row["宵夜餐补"])), display_number(to_number(row["出差餐补"])),
                   f"=SUM(G{excel_row}:I{excel_row})", row.get("备注", "")])
    last_data_row, total_row = 2 + len(rows), 3 + len(rows)
    ws.cell(total_row, 1, "合计")
    for column in (7, 8, 9):
        letter = get_column_letter(column)
        ws.cell(total_row, column, f"=SUM({letter}3:{letter}{last_data_row})")
    ws.cell(total_row, 10, f"=SUM(G{total_row}:I{total_row})")
    ws.cell(total_row, 11, "付款金额＝日常餐补＋宵夜餐补＋出差餐补")
    apply_table_style(ws, 3, total_row, 11, {7, 8, 9, 10})
    for cell in ws[total_row]:
        cell.fill, cell.font = TOTAL_FILL, Font(name=FONT_NAME, bold=True)
    set_detail_widths(ws)
    ws.freeze_panes, ws.auto_filter.ref = "A3", f"A2:K{last_data_row}"
    wb.calculation.fullCalcOnLoad = wb.calculation.forceFullCalc = True
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return _amounts(rows)


def write_summary_workbook(all_rows: Sequence[Mapping[str, object]], results: Sequence[Dict[str, object]], path: Path, title: str, month: str) -> None:
    count = len(results)
    wb = Workbook()
    ws = wb.active
    ws.title, ws.sheet_view.showGridLines = "付款汇总", False
    set_sheet_title(ws, f"{title} - 付款部门拆分汇总（{count}份）", 10)
    ws.append(["序号", "付款部门", "公司主体", "归集口径", "人数", "日常餐补", "宵夜餐补", "出差餐补", "付款金额", "子表文件名"])
    style_headers(ws, 2, 10)
    for idx, item in enumerate(results, start=1):
        row_number = idx + 2
        ws.append([idx, item["payment_department"], item["company"], item["scope"], int(item["count"]), item["daily"], item["night"], item["travel"], f"=SUM(F{row_number}:H{row_number})", item["file_name"]])
    last_data_row, total_row = 2 + count, 3 + count
    ws.cell(total_row, 1, "合计")
    for column in range(5, 9):
        letter = get_column_letter(column)
        ws.cell(total_row, column, f"=SUM({letter}3:{letter}{last_data_row})")
    ws.cell(total_row, 9, f"=SUM(F{total_row}:H{total_row})")
    ws.cell(total_row, 10, f"共{count}个付款分组")
    apply_table_style(ws, 3, total_row, 10, {5, 6, 7, 8, 9})
    for row in range(3, total_row + 1):
        ws.cell(row, 5).number_format = "#,##0"
    for cell in ws[total_row]:
        cell.fill, cell.font = TOTAL_FILL, Font(name=FONT_NAME, bold=True)
    for col, width in enumerate([7, 18, 14, 40, 8, 12, 12, 12, 12, 40], 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes, ws.auto_filter.ref = "A3", f"A2:J{last_data_row}"

    raw = wb.create_sheet("原始明细（已重算）")
    raw.sheet_view.showGridLines = False
    set_sheet_title(raw, f"{title}（不含原总计行）", 11)
    raw.append(HEADERS)
    style_headers(raw, 2, 11)
    for index, row in enumerate(all_rows, start=1):
        excel_row = index + 2
        raw.append([index, row["公司主体"], row["姓名"], row["一级部门"], row["二级部门"], row["三级部门"], row["日常餐补"], row["宵夜餐补"], row["出差餐补"], f"=SUM(G{excel_row}:I{excel_row})", row.get("备注", "")])
    apply_table_style(raw, 3, 2 + len(all_rows), 11, {7, 8, 9, 10})
    set_detail_widths(raw)
    raw.freeze_panes = "A3"

    notes = wb.create_sheet("使用说明")
    set_sheet_title(notes, "使用说明", 2)
    notes_rows = [("动态分组", f"本次按实际数据生成 {count} 份付款子表，没有费用的部门不会生成。"),
                  ("金额规则", "付款金额按日常餐补、宵夜餐补、出差餐补三项重新计算。"),
                  ("公司维度", "同名付款部门属于不同公司主体时，分别生成文件和付款流程。"),
                  ("特殊规则", "原有供应链中心、人力资源组、硬装技术组等合并规则继续生效。"), ("账期", f"本次处理月份：{month}。")]
    for row_index, (label, text) in enumerate(notes_rows, start=3):
        notes.cell(row_index, 1, label)
        notes.cell(row_index, 2, text)
    notes.column_dimensions["A"].width, notes.column_dimensions["B"].width = 18, 78
    wb.calculation.fullCalcOnLoad = wb.calculation.forceFullCalc = True
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def safe_name(value: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "_", value).strip(" .") or "未命名"


def generate_selected_payment_files(input_path: Path, output_root: Path, selections: Sequence[Mapping[str, object]], mappings: Mapping[tuple[str, str], str] | None = None) -> Dict[str, object]:
    rows, title = read_rows(input_path)
    analysis = analyze_payment_allowances(input_path, mappings)
    selection_map = {str(item["group_id"]): item for item in selections}
    source_groups = {str(item["id"]): item for item in analysis["groups"]}
    chosen: "OrderedDict[tuple[str, str], dict[str, object]]" = OrderedDict()
    short_month, full_month = str(analysis["month"]), str(analysis["full_month"])
    rules = build_rules(short_month)
    row_buckets: dict[str, list[Mapping[str, object]]] = {key: [] for key in source_groups}
    for row in rows:
        match = next((rule for rule in rules if rule.predicate(row)), None)
        source = deepest_department(row)
        gid = group_id(str(row["公司主体"]), f"known:{match.payment_department}" if match else f"source:{source}")
        row_buckets.setdefault(gid, []).append(row)
    for gid, source in source_groups.items():
        choice = selection_map.get(gid)
        if not choice or not bool(choice.get("selected", False)):
            continue
        payment_department = clean_text(choice.get("payment_department"))
        if not payment_department:
            raise ValueError(f"{source['company']} / {source['suggested_department']} 尚未指定付款部门。")
        key = (str(source["company"]), payment_department)
        target = chosen.setdefault(key, {"company": source["company"], "payment_department": payment_department, "scope_items": [], "source_group_ids": [], "rows": []})
        target["rows"].extend(row_buckets.get(gid, []))
        target["source_group_ids"].append(gid)
        target["scope_items"].extend(item for item in source["source_departments"] if item not in target["scope_items"])
    if not chosen:
        raise ValueError("请至少勾选一个需要付款的部门。")

    count = len(chosen)
    run_dir = output_root / f"{full_month}补贴_付款准备"
    child_dir = run_dir / f"{full_month}补贴_付款部门文件_{count}份"
    child_dir.mkdir(parents=True, exist_ok=True)
    duplicate_names = {dept for _, dept in chosen if sum(1 for _, other in chosen if other == dept) > 1}
    results: list[dict[str, object]] = []
    for (company, department), bucket in chosen.items():
        suffix = f"-{company}" if department in duplicate_names else ""
        file_name = safe_name(f"{department}{short_month}补贴明细{suffix}") + ".xlsx"
        path = child_dir / file_name
        amounts = write_detail_workbook(bucket["rows"], path, title)
        results.append({"id": group_id(company, f"payment:{department}"), "source_group_ids": bucket["source_group_ids"],
                        "payment_department": department, "company": company, "scope": "、".join(bucket["scope_items"]),
                        "file_name": file_name, "file_path": str(path),
                        "department_allocations": department_allocations(bucket["rows"], department), **amounts})
    selected_rows = [row for item in chosen.values() for row in item["rows"]]
    raw_totals = _amounts(selected_rows)
    if round(float(raw_totals["amount"]), 2) != round(sum(float(item["amount"]) for item in results), 2):
        raise AssertionError("拆分前后餐补金额不一致。")
    summary_path = run_dir / f"{full_month}补贴_付款部门拆分汇总_{count}份.xlsx"
    write_summary_workbook(selected_rows, results, summary_path, title, short_month)
    zip_path = run_dir / f"{full_month}补贴_按付款部门拆分_{count}份.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in results:
            archive.write(item["file_path"], arcname=item["file_name"])
    return {"output_directory": str(run_dir), "summary": str(summary_path), "zip": str(zip_path), "detail_folder": str(child_dir),
            "source_rows": int(raw_totals["count"]), "payment_departments": count, "daily_total": raw_totals["daily"],
            "night_total": raw_totals["night"], "travel_total": raw_totals["travel"], "payment_total": raw_totals["amount"],
            "month": short_month, "full_month": full_month, "title": title, "departments": results}


def split_payment_allowances(input_path: Path, output_root: Path) -> Dict[str, object]:
    analysis = analyze_payment_allowances(input_path)
    selections = [{"group_id": item["id"], "selected": True, "payment_department": item["payment_department"] or item["suggested_department"]} for item in analysis["groups"]]
    return generate_selected_payment_files(input_path, output_root, selections)


def main() -> None:
    parser = argparse.ArgumentParser(description="按实际公司主体和付款部门动态拆分月度补贴总表。")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = split_payment_allowances(args.input.resolve(), args.output.resolve())
    print(f"拆分完成：{result['payment_departments']} 份，合计 {result['payment_total']} 元")


if __name__ == "__main__":
    main()
