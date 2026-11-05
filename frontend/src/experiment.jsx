import React, { useState } from "react";
import "./experiment.css";

export function DiagnosticReport({ report }) {
  if (!report) return null;
  const summary = report.diagnostic_summary || {};
  const failure = report.failure;
  return <section className="diagnostic-report" aria-label="审批诊断报告">
    <div className="diagnostic-heading"><strong>{report.sample || "审批诊断报告"}</strong>
      <span>{summary.status} · 本地问题 {summary.local_errors || 0} · 待验证 {summary.unverified || 0}</span></div>
    <p className="diagnostic-note">只读检查不代表钉钉能够创建审批。{report.offline ? "当前为历史文件回放，未联网。" : "本轮未上传附件、未创建审批。"}</p>
    {failure && <div className="diagnostic-failure">
      <strong>{failure.origin} · {failure.stage}</strong>
      <dl><dt>HTTP 状态</dt><dd>{failure.http_status ?? "未记录"}</dd>
        <dt>错误码</dt><dd>{failure.error_code || "未记录"}</dd>
        <dt>追踪号</dt><dd className="diagnostic-trace">{failure.trace_id || "未记录"}</dd>
        <dt>实例 ID</dt><dd>{failure.has_instance_id ? failure.instance_id : "未返回"}</dd></dl>
      <p>{failure.message}。{failure.cause}</p>
    </div>}
    {!!report.personnel?.length && <div className="diagnostic-table-wrap"><table className="diagnostic-table">
      <thead><tr><th>模板顺序 / 节点</th><th>类型 / 来源</th><th>必选</th><th>当前人员</th><th>检查结果</th></tr></thead>
      <tbody>{report.personnel.map((node, i) => <tr key={`${node.actor_key || node.name}-${i}`}>
        <td>{node.index}. {node.name}{node.prev_activity_id && <small>前序：{node.prev_activity_id}</small>}</td>
        <td>{node.role}<small>{node.source}</small></td><td>{node.required ? "是" : "按模板规则"}</td>
        <td>{node.users?.length ? node.users.map((u, j) => <div key={j}>{u.name}<small>{u.source === "local_default" ? "本地候选默认值" : u.source === "template" ? "模板人员" : ""}</small></div>) : "未选择 / 未返回"}</td>
        <td>{node.status}</td></tr>)}</tbody>
    </table></div>}
    <details open><summary>本地检查与待验证项（{report.findings?.length || 0}）</summary>
      <ul className="diagnostic-findings">{report.findings?.map((f, i) => <li key={i} data-level={f.level}>
        <span>{f.level === "error" ? "本地问题" : "待验证"}</span><div><strong>{f.field}</strong> {f.message}<small>{f.code}</small></div>
      </li>)}</ul></details>
  </section>;
}

export function ExperimentPanel({ task }) {
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [source, setSource] = useState("");
  const reports = result?.items ? result.items.map(i => i.diagnostics) : result ? [result] : [];
  async function run(mode) {
    setBusy(true); setError("");
    try {
      const response = await fetch(mode === "replay" ? "/api/experiment/replay" : `/api/tasks/${encodeURIComponent(task.id)}/approvals/diagnostics`, { method: mode === "replay" ? "GET" : "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "诊断请求失败");
      setResult(data); setSource(mode);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  function download() {
    // API results are already redacted. Never export task/profile objects from the browser.
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = url; a.download = source === "replay" ? "历史400诊断_脱敏.json" : "审批诊断_脱敏.json"; a.click(); URL.revokeObjectURL(url);
  }
  return <section className="experiment-panel" aria-label="诊断实验区">
    <div className="diagnostic-heading"><strong>审批诊断实验版</strong><span className="readonly-badge">钉钉只读 · 正式提交已阻止</span></div>
    <p>核对人员、流程和表单，独立保存实验资料。历史 1200 元样例仅供回放，不是待提交任务。</p>
    <div className="diagnostic-actions">
      <button type="button" className="button secondary" disabled={busy} onClick={() => run("replay")}>回放历史 400 响应</button>
      <button type="button" className="button secondary" disabled={busy || !task?.result} onClick={() => run("task")}>查询当前任务诊断</button>
      <button type="button" className="button secondary" disabled={busy || !result} onClick={download}>导出脱敏诊断</button>
      {busy && <span role="status">正在只读诊断…</span>}
    </div>
    {error && <p className="diagnostic-error" role="alert">{error}</p>}
    {result && <details open className="diagnostic-results"><summary>诊断结果 · {source === "replay" ? "离线历史回放" : "当前任务只读查询"}</summary>
      {reports.map((report, i) => <DiagnosticReport key={i} report={report} />)}
    </details>}
  </section>;
}
