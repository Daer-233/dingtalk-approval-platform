from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any


class AuthorizedOfficeImportError(RuntimeError):
    """Raised when the installed spreadsheet application cannot export the file."""


def find_wps_spreadsheet() -> Path | None:
    """Return a locally installed WPS Spreadsheet executable when available."""
    local_app_data = Path.home() / "AppData" / "Local" / "Kingsoft" / "WPS Office"
    if not local_app_data.is_dir():
        return None
    matches = sorted(local_app_data.glob("*/office6/et.exe"), key=lambda item: item.parent.parent.name, reverse=True)
    return matches[0] if matches else None


def _open_workbook(workbooks: Any, source_path: Path, password: str) -> Any:
    # Excel and WPS both expose a Workbooks.Open-compatible method. Positional
    # arguments keep this compatible with the two COM implementations.
    if password:
        return workbooks.Open(str(source_path), 0, True, None, password)
    return workbooks.Open(str(source_path), 0, True)


def _save_plain_xlsx(workbook: Any, output_path: Path) -> None:
    """Save an .xlsx copy and explicitly remove normal workbook passwords.

    Passing empty Password / WriteResPassword is important: omitting these
    fields can cause WPS to retain the source workbook's password protection
    in the newly saved file.
    """
    # WPS and Microsoft Excel both accept this shared positional SaveAs shape:
    # Filename, FileFormat, Password, WriteResPassword, ReadOnlyRecommended,
    # CreateBackup. FileFormat 51 = standard .xlsx.
    workbook.SaveAs(str(output_path), 51, "", "", False, False)


def _assert_plain_xlsx(output_path: Path) -> None:
    # A password-protected XLSX is commonly stored as an OLE/CFB container,
    # not as the standard ZIP package expected by openpyxl.
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise AuthorizedOfficeImportError("本机表格软件未生成可用的导出副本。")
    if not zipfile.is_zipfile(output_path):
        raise AuthorizedOfficeImportError(
            "WPS / Excel 已打开文件，但导出的副本仍是加密或受保护格式，无法交给系统解析。"
            "请确认当前账号对该文件拥有“另存为/导出”权限；如公司策略禁止导出，系统无法绕过该限制。"
        )


def export_authorized_workbook(source_path: Path, output_path: Path, password: str = "") -> str:
    """Open through a local authorised WPS/Excel instance and export a plain xlsx.

    This does not decrypt or bypass permissions. It only uses the local Office
    application in the same way as an authorised user opening and saving a copy.
    WPS is attempted first, followed by Microsoft Excel.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:  # pragma: no cover - depends on the Windows host
        raise AuthorizedOfficeImportError(
            "本机导入功能需要 pywin32。请重新运行启动脚本安装依赖。"
        ) from exc

    pythoncom.CoInitialize()
    app = None
    workbook = None
    opened_by = ""
    candidates = [
        ("WPS 表格", "ket.Application"),
        ("WPS 表格", "ET.Application"),
        ("Microsoft Excel", "Excel.Application"),
    ]
    errors: list[str] = []
    try:
        for display_name, prog_id in candidates:
            try:
                app = win32com.client.DispatchEx(prog_id)
                app.Visible = True
                app.DisplayAlerts = False
                workbook = _open_workbook(app.Workbooks, source_path, password)
                opened_by = display_name
                break
            except Exception as exc:  # noqa: BLE001 - COM error details vary by host
                errors.append(f"{display_name}：{exc}")
                if workbook is not None:
                    try:
                        workbook.Close(False)
                    except Exception:
                        pass
                    workbook = None
                if app is not None:
                    try:
                        app.Quit()
                    except Exception:
                        pass
                    app = None

        if workbook is None or app is None:
            hint = "；".join(errors[-2:])
            wps_path = find_wps_spreadsheet()
            registration_hint = (
                "检测到本机 WPS，但它的表格自动化组件尚未注册。"
                "请先关闭 WPS，再双击项目目录中的“注册WPS表格自动化支持.bat”，然后重试。"
                if wps_path else "请确认已安装 WPS 表格或 Microsoft Excel。"
            )
            raise AuthorizedOfficeImportError(
                "WPS 和 Microsoft Excel 都无法打开该文件。"
                + registration_hint
                + "同时请确认当前账号拥有另存为/导出权限。"
                + (f"（{hint}）" if hint else "")
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        _save_plain_xlsx(workbook, output_path)
        _assert_plain_xlsx(output_path)
        return opened_by
    except AuthorizedOfficeImportError:
        raise
    except Exception as exc:  # noqa: BLE001 - COM error details vary by host
        raise AuthorizedOfficeImportError(
            f"已通过{opened_by or '本机表格软件'}打开文件，但无法导出未加密的 .xlsx 副本。"
            "请检查是否被策略禁止另存为。"
        ) from exc
    finally:
        if workbook is not None:
            try:
                workbook.Close(False)
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()