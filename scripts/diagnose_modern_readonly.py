"""Default offline; --remote-readonly permits only token/schema/forecast, never creation."""
from __future__ import annotations
import argparse
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.dingtalk import DingTalkClient, load_dingtalk_config, normalize_forecast
from app.modern_diagnostics import ReadOnlyTransport, audit_request
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
PROTECTED = ("modern_create_once_guard.json", "modern_create_request.json", "modern_live_report.json", "modern_schema.json", "modern_test_fixture.json")

def digest():
    return {n: hashlib.sha256((OUT / n).read_bytes()).hexdigest() for n in PROTECTED}

def run(remote=False):
    before = digest()
    schema = json.loads((OUT / "modern_schema.json").read_text(encoding="utf-8"))
    payload = json.loads((OUT / "modern_create_request.json").read_text(encoding="utf-8"))
    history = json.loads((OUT / "modern_live_report.json").read_text(encoding="utf-8"))
    cfg = load_dingtalk_config()
    forecast = None
    guard = None
    if remote:
        if cfg.process_code != payload.get("processCode"):
            raise RuntimeError("当前测试模板不同于历史失败模板，未进行在线查询。")
        guard = ReadOnlyTransport()
        client = DingTalkClient(replace(cfg, live_submit_enabled=False, attachment_enabled=False))
        client.close()
        client._http = httpx.Client(transport=guard, timeout=30, follow_redirects=False)
        try:
            schema = client.get_schema(force_refresh=True)
            # No old fileId is sent: no attachment creation, read, or validation.
            types = {DingTalkClient._label(i): i.get("componentName") for i in DingTalkClient._schema_items(schema)}
            values = [f for f in payload["formComponentValues"] if types.get(f.get("name")) != "DDAttachment"]
            forecast = normalize_forecast(client.forecast(values))
        finally:
            client.close()
    report = audit_request(schema, payload, forecast, history.get("nodes"))
    report.update({"checked_at": datetime.now().astimezone().isoformat(),
                   "source": "fresh_schema_and_forecast_without_attachment" if remote else "archived_failed_request",
                   "historical_error_code": "processInstanceStartFailed",
                   "historical_create_calls": history.get("create_calls"),
                   "historical_template_name": history.get("template_name"),
                   "configuration_matches_archive": {
                     "process": cfg.process_code == payload.get("processCode"),
                     "originator": cfg.originator_user_id == payload.get("originatorUserId"),
                     "dept": str(cfg.originator_dept_id) == str(payload.get("deptId")),
                     "agent": str(cfg.agent_id) == str(payload.get("microappAgentId"))},
                   "current_live_submit_enabled": cfg.live_submit_enabled,
                   "network_calls": guard.calls if guard else [], "blocked_network_calls": guard.blocked if guard else 0,
                   "original_evidence_unchanged": before == digest()})
    path = OUT / ("modern_readonly_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
    with path.open("x", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"report": str(path), "structural_errors": report["structural_errors"],
                      "unverified_count": report["unverified_count"], "real_create_calls": 0,
                      "real_upload_calls": 0, "original_evidence_unchanged": report["original_evidence_unchanged"]}, ensure_ascii=False))
    return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="新接口只读排查，不提供创建/上传选项")
    parser.add_argument("--remote-readonly", action="store_true", help="只读取当前测试模板与无附件流程预测")
    run(parser.parse_args().remote_readonly)
