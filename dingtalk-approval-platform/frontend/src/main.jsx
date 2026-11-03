import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Building2,
  Cable,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleAlert,
  CircleHelp,
  ClipboardCheck,
  Download,
  Eye,
  FileSpreadsheet,
  GitBranch,
  History as HistoryIcon,
  LayoutDashboard,
  ListChecks,
  LoaderCircle,
  Menu,
  Moon,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  ShieldCheck,
  Sun,
  Upload,
  UserPlus,
  UsersRound,
  X,
} from "lucide-react";
import "./styles.css";
import "./legacy-chain.css";
import { eligibleApprovalPreviews } from "./submission-candidates.js";
import { templateCandidateIds, pickerSections, newScopeCandidates } from "./approver-picker.js";
import "./allocation.css";
import "./spreadsheet-preview.css";
import "./node-picker.css";
import "./workflow-tabs.css";
import { restoredWorkflowTab, forecastActioners } from "./workflow-tabs.js";
import { nodeCompatibilityView } from "./workflow-compatibility.js";

const API = "/api";
const normalizeNodeName = (name) => String(name || "").replace(/\s/g, "").replace(/／/g, "/");
const RECENT_APPROVERS_KEY = "business-platform-recent-approvers-v1";
function recentApprovers() {
  try {
    const values = JSON.parse(localStorage.getItem(RECENT_APPROVERS_KEY) || "[]");
    return Array.isArray(values) ? values.filter((user) => user?.user_id && user?.name).slice(0, 12) : [];
  } catch { return []; }
}
function rememberApprovers(users) {
  const merged = new Map();
  [...users, ...recentApprovers()].forEach((user) => {
    if (user?.user_id && !merged.has(user.user_id)) merged.set(user.user_id, { user_id: user.user_id, name: user.name });
  });
  try { localStorage.setItem(RECENT_APPROVERS_KEY, JSON.stringify([...merged.values()].slice(0, 12))); } catch { /* Storage may be disabled. */ }
}
const MODULE_NAME = "补贴付款拆分";
const NAV = [
  ["workspace", "工作台", "grid"],
  ["history", "任务记录", "clock"],
  ["companies", "付款资料", "building"],
  ["rules", "归集规则", "branch"],
  ["integration", "钉钉接入", "link"],
];
const ICONS = {
  grid: LayoutDashboard,
  clock: HistoryIcon,
  building: Building2,
  branch: GitBranch,
  link: Cable,
  upload: Upload,
  file: FileSpreadsheet,
  download: Download,
  people: UsersRound,
  check: Check,
  alert: CircleAlert,
  eye: Eye,
  retry: RotateCcw,
  save: Save,
  close: X,
  menu: Menu,
  arrow: ChevronRight,
  loading: LoaderCircle,
  progress: ListChecks,
};

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers:
      options.body instanceof FormData
        ? undefined
        : { "Content-Type": "application/json" },
    ...options,
  });
  const raw = await response.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(`服务返回了无法识别的内容（HTTP ${response.status}）`);
  }
  if (!response.ok) throw new Error(data.detail || "请求失败");
  return data;
}
const money = (value) =>
  Number(value || 0).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const date = (value) =>
  value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "-";
const statusText = (status) =>
  ({
    review: "等待处理",
    prepared: "附件已生成",
    previewed: "等待提交",
    submitting: "正在提交",
    submitted: "已完成",
    partial_failed: "部分失败",
    failed: "需要检查",
  })[status] || "等待上传";
const displayFieldValue = (value) => {
  if (value == null || value === "") return "-";
  if (Array.isArray(value))
    return value.map((item) => displayFieldValue(item)).join("；");
  if (typeof value === "object")
    return Object.entries(value)
      .map(([key, item]) => `${key}：${displayFieldValue(item)}`)
      .join("；");
  return String(value);
};

const allocationCellStyle = {
  minWidth: 0,
  padding: "10px 12px",
  border: "1px solid var(--line)",
  borderRadius: 6,
  background: "var(--surface)",
  overflowWrap: "anywhere",
  wordBreak: "break-word",
};
function AllocationDetails({ field }) {
  const department = field.name === "部门分摊明细";
  const rows = Array.isArray(field.value) && field.value.length ? field.value : [{}];
  const columns = department
    ? ["费用科目", "发生费用部门", "金额（元）"]
    : ["项目费用科目", "项目信息", "付款金额（元）"];
  return (
    <div className="field-wide allocation-details">
      <small>{field.name}</small>
      {rows.map((row, rowIndex) => (
        <div className="allocation-row" key={`${field.name}-${rowIndex}`}>
          {columns.map((column) => {
            const value = row?.[column];
            const sourceDepartment = department && column === "发生费用部门"
              ? row?.["原表部门"]
              : "";
            return (
              <div
                style={allocationCellStyle}
                key={column}
              >
                <small
                  style={{
                    display: "block",
                    marginBottom: 5,
                    whiteSpace: "normal",
                    overflowWrap: "anywhere",
                    lineHeight: 1.4,
                  }}
                >
                  {column}
                </small>
                {sourceDepartment ? (
                  <>
                    <strong style={{ display: "block", minWidth: 0 }}>
                      {sourceDepartment}
                    </strong>
                    <span
                      style={{
                        display: "block",
                        marginTop: 5,
                        color: "var(--accent)",
                        fontSize: 11,
                        lineHeight: 1.5,
                        overflowWrap: "anywhere",
                      }}
                    >
                      钉钉填写：{value || "待匹配"}
                    </span>
                  </>
                ) : (
                  <strong
                    style={{
                      display: "block",
                      minWidth: 0,
                      whiteSpace: column.includes("金额") ? "nowrap" : "normal",
                    }}
                  >
                    {value == null || value === ""
                      ? column === "项目信息" ? "本次不填写" : "-"
                      : column.includes("金额") ? `¥ ${money(value)}` : String(value)}
                  </strong>
                )}
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
function choicesFor(task) {
  return task?.choices?.length
    ? task.choices
    : (task?.analysis?.groups || []).map((group) => ({
        group_id: group.id,
        selected: true,
        payment_department: group.payment_department || "",
        remember_mapping: true,
      }));
}

function Icon({ name, className = "", size = 16, strokeWidth = 1.8 }) {
  const Glyph = ICONS[name] || ICONS.grid;
  return (
    <span className={`icon ${className}`} aria-hidden="true">
      <Glyph size={size} strokeWidth={strokeWidth} />
    </span>
  );
}
function LoadingIcon({ size = 15, className = "" }) {
  return (
    <LoaderCircle
      className={`loading-icon ${className}`}
      size={size}
      strokeWidth={1.8}
      aria-hidden="true"
    />
  );
}
function ThemeToggle({ theme, setTheme }) {
  const dark = theme === "dark";
  return (
    <button
      className="theme-toggle"
      title={dark ? "切换到浅色主题" : "切换到深色主题"}
      aria-label={dark ? "切换到浅色主题" : "切换到深色主题"}
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      {dark ? (
        <Moon size={18} strokeWidth={1.8} />
      ) : (
        <Sun size={18} strokeWidth={1.8} />
      )}
    </button>
  );
}

function App() {
  const [page, setPage] = useState("workspace");
  const [sidebar, setSidebar] = useState(false);
  const [theme, setTheme] = useState(() => {
    const saved = localStorage.getItem("business-platform-theme-v4");
    return saved === "light" || saved === "dark"
      ? saved
      : window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
  });
  const [task, setTask] = useState(null);
  const [history, setHistory] = useState([]);
  const [rules, setRules] = useState([]);
  const [profiles, setProfiles] = useState([]);
  const [connection, setConnection] = useState(null);
  const [notice, setNotice] = useState(null);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    localStorage.setItem("business-platform-theme-v4", theme);
  }, [theme]);
  const refresh = async () => {
    const [tasks, ruleData, profileData, config] = await Promise.all([
      api("/tasks").catch(() => ({ items: [] })),
      api("/rules").catch(() => ({ items: [] })),
      api("/company-profiles").catch(() => ({ items: [] })),
      api("/dingtalk/preflight").catch(() => null),
    ]);
    setHistory(tasks.items || []);
    setRules(ruleData.items || []);
    setProfiles(profileData.items || []);
    setConnection(config);
  };
  useEffect(() => {
    refresh();
    let active = true;
    let savedId = "";
    try { savedId = localStorage.getItem("business-platform-active-task-v1") || ""; } catch { /* Storage may be disabled. */ }
    if (savedId) api("/tasks/" + encodeURIComponent(savedId)).then((saved) => {
      if (active) setTask((current) => current || saved);
    }).catch(() => { try { localStorage.removeItem("business-platform-active-task-v1"); } catch { /* No persistence. */ } });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (task?.id) { try { localStorage.setItem("business-platform-active-task-v1", task.id); } catch { /* No persistence. */ } }
  }, [task?.id]);
  const activeTaskKey = useMemo(
    () =>
      history
        .filter((item) => item.status === "submitting")
        .map((item) => item.id)
        .join(","),
    [history],
  );
  useEffect(() => {
    const shouldPoll =
      task?.status === "submitting" ||
      history.some((item) => item.status === "submitting");
    if (!shouldPoll) return undefined;
    let active = true;
    const poll = async () => {
      try {
        const tasks = await api("/tasks");
        if (!active) return;
        const items = tasks.items || [];
        setHistory(items);
        if (task?.id) {
          const latest = items.find((item) => item.id === task.id);
          if (latest) setTask(latest);
        }
      } catch {
        /* Keep the current progress view visible during a transient refresh error. */
      }
    };
    const interval = window.setInterval(poll, 1500);
    poll();
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [task?.id, task?.status, activeTaskKey]);
  const title = {
    workspace: MODULE_NAME,
    history: "任务记录",
    companies: "付款资料",
    rules: "部门归集规则",
    integration: "钉钉审批接入",
  }[page];
  function navigate(key) {
    setPage(key);
    setSidebar(false);
  }
  function openTask(item) {
    setTask(item);
    navigate("workspace");
  }
  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebar ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark">汇</span>
          <div>
            <strong>业务平台 · 新接口测试版</strong>
            <small>财务流程服务中心</small>
          </div>
          <button
            className="mobile-close"
            onClick={() => setSidebar(false)}
            aria-label="关闭菜单"
          >
            <Icon name="close" />
          </button>
        </div>
        <div className="space-chip">
          <i />
          综合业务空间
        </div>
        <nav className="side-nav">
          {NAV.map(([key, label, icon]) => (
            <button
              key={key}
              className={page === key ? "active" : ""}
              onClick={() => navigate(key)}
            >
              <Icon name={icon} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-info">
          <small>当前能力</small>
          <strong>补贴付款准备与审批</strong>
          <p>动态拆分、流程预测、批量提交和失败重试。</p>
        </div>
        <div className="account">
          <span>管</span>
          <div>
            <strong>系统管理员</strong>
            <small>本地工作区</small>
          </div>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <button
            className="menu-button"
            onClick={() => setSidebar(true)}
            aria-label="打开菜单"
          >
            <Icon name="menu" size={20} />
          </button>
          <div className="breadcrumb">
            <span>业务中心</span>
            <b>/</b>
            <strong>{title}</strong>
          </div>
          <div className="topbar-actions">
            <div className="service-state">
              <i />
              服务正常
            </div>
            <ThemeToggle theme={theme} setTheme={setTheme} />
          </div>
        </header>
        <div className="content-scroll">
          {notice && <Notice value={notice} close={() => setNotice(null)} />}{" "}
          {page === "workspace" && (
            <Workspace {...{ task, setTask, connection, refresh, setNotice }} />
          )}{" "}
          {page === "history" && (
            <History items={history} openTask={openTask} />
          )}{" "}
          {page === "companies" && (
            <Profiles {...{ profiles, refresh, setNotice }} />
          )}{" "}
          {page === "rules" && <Rules rules={rules} />}{" "}
          {page === "integration" && (
            <Integration {...{ connection, setConnection, setNotice }} />
          )}
        </div>
      </main>
      {sidebar && (
        <button className="scrim" onClick={() => setSidebar(false)} />
      )}
    </div>
  );
}

function Notice({ value, close }) {
  return (
    <div className={`notice ${value.type || "info"}`}>
      <Icon name={value.type === "error" ? "alert" : "check"} />
      <span>{value.text}</span>
      <button onClick={close}>
        <Icon name="close" />
      </button>
    </div>
  );
}
function Steps({ task }) {
  const current =
    task?.status === "submitted"
      ? 5
      : task?.status === "previewed"
        ? 4
        : task?.result
          ? 3
          : task?.analysis
            ? 1
            : 0;
  return (
    <div className="step-rail">
      {[
        "上传总表",
        "处理部门",
        "生成附件",
        "检测流程",
        "审批预览",
        "批量发起",
      ].map((label, index) => (
        <div
          key={label}
          className={`step-item ${index < current ? "complete" : index === current ? "active" : ""}`}
        >
          <i>
            {index < current ? (
              <Check size={13} strokeWidth={2.4} />
            ) : (
              index + 1
            )}
          </i>
          <small>{label}</small>
        </div>
      ))}
    </div>
  );
}

function Workspace({ task, setTask, connection, refresh, setNotice }) {
  const [choices, setChoices] = useState([]);
  const [choicesTaskId, setChoicesTaskId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("groups");
  const [resultOpen, setResultOpen] = useState(false);
  const [submitOpen, setSubmitOpen] = useState(false);
  const [progressOpen, setProgressOpen] = useState(false);
  const [attachmentPreview, setAttachmentPreview] = useState(null);
  const [nodePicker, setNodePicker] = useState(null);
  const [pickerSaving, setPickerSaving] = useState(false);
  const [roleScopes, setRoleScopes] = useState([]);
  const [actioners, setActioners] = useState({});
  const [forecasting, setForecasting] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const analysis = task?.analysis;
  const result = task?.result;
  const selected = choices.filter((item) => item.selected).length;
  const unresolved = choices.filter(
    (item) => item.selected && !item.payment_department.trim(),
  ).length;
  const previews = task?.previews || [];
  const confirmationPreviews = eligibleApprovalPreviews(previews, task?.approval_attempts || [], submitOpen === "retry");
  const savedChoices = task?.choices || [];
  const groupsDirty =
    Boolean(result) && choicesTaskId === task?.id &&
    (choices.length !== savedChoices.length ||
      choices.some(
        (item, index) =>
          item.group_id !== savedChoices[index]?.group_id ||
          item.selected !== savedChoices[index]?.selected ||
          item.payment_department !== savedChoices[index]?.payment_department,
      ));
  useEffect(() => {
    setChoices(choicesFor(task));
    setChoicesTaskId(task?.id || null);
    setActioners(task?.workflow_actioners || {});
  }, [task?.id, task?.updated_at]);
  useEffect(() => {
    setAttachmentPreview(null);
    setNodePicker(null);
  }, [task?.id]);
  useEffect(() => {
    let active = true;
    api("/approval-role-scopes")
      .then((data) => { if (active) setRoleScopes(data.items || []); })
      .catch((error) => { if (active) setNotice({ type: "error", text: `读取审批节点人员范围失败：${error.message}` }); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    setTab(
      restoredWorkflowTab(task),
    );
  }, [task?.id]);
  useEffect(() => { if (groupsDirty) setTab("groups"); }, [groupsDirty]);
  const updateChoice = (id, patch) =>
    setChoices((items) =>
      items.map((item) =>
        item.group_id === id ? { ...item, ...patch } : item,
      ),
    );
  async function forecast(currentTask, preservedActioners = null) {
    setForecasting(true);
    try {
      const data = await api(`/tasks/${currentTask.id}/approvals/forecast`, {
        method: "POST",
        body: JSON.stringify({ groups: preservedActioners || {} }),
      });
      const forecasts = Object.fromEntries(
        (data.groups || []).map((group) => [group.group_id, group]),
      );
      const nextActioners = forecastActioners(forecasts);
      setTask({
        ...currentTask,
        workflow_forecasts: forecasts,
        workflow_actioners: nextActioners,
        previews: [],
        status: "prepared",
        updated_at: new Date().toISOString(),
      });
      setActioners(nextActioners);
      const failures = data.groups?.filter((group) => group.error).length || 0;
      setNotice({
        type: failures ? "error" : "success",
        text: failures
          ? `${failures} 个付款分组未能完成流程检测。`
          : "已完成钉钉审批路径检测。",
      });
      return forecasts;
    } catch (error) {
      setNotice({ type: "error", text: `流程检测失败：${error.message}` });
      return null;
    } finally {
      setForecasting(false);
    }
  }
  async function prepare() {
    if (!selected || unresolved)
      return setNotice({
        type: "error",
        text: "请先完成所有选中部门的付款归属。",
      });
    setBusy(true);
    try {
      await api(`/tasks/${task.id}/groups`, {
        method: "PUT",
        body: JSON.stringify({ groups: choices }),
      });
      const prepared = await api(`/tasks/${task.id}/prepare`, {
        method: "POST",
        body: "{}",
      });
      setTask(prepared);
      setActioners({});
      setResultOpen(false);
      setAttachmentPreview(null);
      setExpanded(null);
      setTab("nodes");
      await refresh();
      setNotice({
        type: "success",
        text: `已生成 ${prepared.result.payment_departments} 份部门附件。`,
      });
      await forecast(prepared);
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  }
  async function saveActioners() {
    const groups = Object.fromEntries(
      Object.keys(task?.workflow_forecasts || {}).map((groupId) => [
        groupId,
        actioners[groupId] || {},
      ]),
    );
    await api(`/tasks/${task.id}/approvals/actioners`, {
      method: "PUT",
      body: JSON.stringify({ groups }),
    });
    setTask((current) => ({
      ...current,
      workflow_actioners: groups,
      previews: [],
    }));
  }
  async function preview() {
    setBusy(true);
    try {
      await saveActioners();
      const data = await api(`/tasks/${task.id}/approvals/preview`, {
        method: "POST",
        body: "{}",
      });
      setTask((current) => ({
        ...current,
        previews: data.items,
        status: "previewed",
      }));
      setTab("preview");
      setNotice({
        type: data.valid ? "success" : "error",
        text: data.valid
          ? "审批预览已生成，请逐项核对后确认提交。"
          : "预览有待完成配置，请查看红色提示。",
      });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  }
  async function submit(retry = false, legacyConfirmed = false) {
    setSubmitOpen(false);
    setProgressOpen(true);
    setBusy(true);
    try {
      const data = await api(
        `/tasks/${task.id}/approvals/${retry ? "retry" : "submit"}`,
        { method: "POST", body: JSON.stringify({
          confirm: true,
          confirm_legacy_chain: legacyConfirmed,
          preview_fingerprints: Object.fromEntries(eligibleApprovalPreviews(previews, task?.approval_attempts || [], retry).map((item) => [item.group_id, item.confirmation_fingerprint || ""])),
        }) },
      );
      setTask(data.task || (await api(`/tasks/${task.id}`)));
      await refresh();
      if (data.status === "submitting") {
        setNotice({
          type: "success",
          text: "批量审批已开始，可在进度窗口查看各部门处理状态。",
        });
      } else {
        const issueText = [
          data.failed ? `失败 ${data.failed} 条` : "",
          data.uncertain ? `待确认 ${data.uncertain} 条` : "",
        ]
          .filter(Boolean)
          .join("，");
        setNotice({
          type: data.failed || data.uncertain ? "error" : "success",
          text: `已成功发起 ${data.submitted} 条${issueText ? `，${issueText}` : "。"}`,
        });
      }
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  }
  async function applyNodePicker(picked) {
    if (!nodePicker || pickerSaving) return;
    const { groupId, node } = nodePicker;
    const usersToSave = picked.map((user) => ({ user_id: user.user_id, name: user.name }));
    const templateIds = templateCandidateIds(node);
    if (templateIds.length && usersToSave.some((user) => !templateIds.includes(user.user_id))) {
      throw new Error("所选人员不在钉钉模板允许范围内。");
    }
    if (node.selection_range_kind === "unsupported") {
      throw new Error("钉钉返回的人员范围无法识别，请先在钉钉模板中明确指定成员。");
    }
    const nextActioners = {
      ...actioners,
      [groupId]: { ...(actioners[groupId] || {}), [node.actor_key]: usersToSave },
    };
    const configuredScope = roleScopes.find((scope) =>
      normalizeNodeName(scope.role_name) === normalizeNodeName(node.name));
    const locked = new Set(node.template_default_user_ids || []);
    const additions = newScopeCandidates(usersToSave.filter((user) => !locked.has(user.user_id)), configuredScope?.users || []);
    const needsScope = usersToSave.some((user) => !node.allowed_user_ids?.includes(user.user_id) && !locked.has(user.user_id));
    setPickerSaving(true);
    try {
      if (additions.length) {
        const savedScope = await api("/approval-role-scopes/candidates", {
          method: "POST", body: JSON.stringify({ role_name: node.name, users: additions }),
        });
        setRoleScopes((current) => [
          ...current.filter((scope) => scope.role_key !== savedScope.role_key), savedScope,
        ]);
      }
      if (additions.length || needsScope) {
        const updated = await forecast(task, nextActioners);
        if (!updated) throw new Error("人员范围已保存，但审批路径更新失败，请重新检测流程后选择审批人。");
        const refreshed = updated[groupId]?.nodes?.find((item) => item.actor_key === node.actor_key);
        if (!refreshed || usersToSave.some((user) => !refreshed.allowed_user_ids?.includes(user.user_id) && !refreshed.template_default_user_ids?.includes(user.user_id))) {
          throw new Error("人员范围已保存，但钉钉审批路径未允许所选人员，请检查模板配置。");
        }
      } else {
        setActioners(nextActioners);
        setTask((current) => ({ ...current, workflow_actioners: nextActioners, previews: [], status: "prepared" }));
      }
      rememberApprovers(usersToSave);
      setNodePicker(null);
      setNotice({ type: "success", text: additions.length ? "人员已加入该节点的本地可选范围，并选为本次审批人。" : "已选择本次审批人。" });
    } finally {
      setPickerSaving(false);
    }
  }
  return (
    <section className="page workspace-page">
      <PageHeading connection={connection} />
      <Steps task={task} />
      {!task && (
        <UploadPanel
          done={(next) => {
            setTask(next);
            setChoices(choicesFor(next));
            refresh();
          }}
          setNotice={setNotice}
        />
      )}{" "}
      {analysis && (
        <>
          <Stats
            analysis={analysis}
            selected={selected}
            unresolved={unresolved}
          />
          <div className="tabs">
            <button
              className={tab === "groups" ? "active" : ""}
              onClick={() => setTab("groups")}
            >
              <UsersRound size={16} strokeWidth={1.8} />
              付款分组与部门归属
            </button>
            <button className={tab === "nodes" ? "active" : ""} disabled={!result || groupsDirty} onClick={() => setTab("nodes")}>
              {forecasting ? <LoadingIcon size={16} /> : <GitBranch size={16} strokeWidth={1.8} />}动态审批节点
            </button>
            <button
              className={tab === "preview" ? "active" : ""}
              disabled={!result || groupsDirty}
              title={groupsDirty ? "付款分组已变更，请先重新生成附件" : ""}
              onClick={() => setTab("preview")}
            >
              <ClipboardCheck size={16} strokeWidth={1.8} />
              审批预览与提交
            </button>
          </div>
          {tab === "groups" ? (
            <div className="tab-panel">
              <Groups
                analysis={analysis}
                choices={choices}
                updateChoice={updateChoice}
                setAllChoices={(selected) =>
                  setChoices((items) =>
                    items.map((item) => ({ ...item, selected })),
                  )
                }
              />
              <AttachmentActionBar selected={selected} unresolved={unresolved} groupsDirty={groupsDirty} result={result} busy={busy || forecasting || task?.status === "submitting"} prepare={prepare} viewResults={() => setResultOpen(true)} reupload={() => { try { localStorage.removeItem("business-platform-active-task-v1"); } catch {} setTask(null); setChoices([]); setActioners({}); setResultOpen(false); }} />
            </div>
          ) : tab === "nodes" ? (
            <div className="tab-panel">
              {result && !groupsDirty && (
                <Forecasts
                  forecasts={task.workflow_forecasts || {}}
                  actioners={actioners}
                  roleScopes={roleScopes}
                  expanded={expanded}
                  setExpanded={setExpanded}
                  openNodePicker={(groupId, node) => setNodePicker({ groupId, node })}
                  forecasting={forecasting}
                />
              )}
              <div className="node-tab-actions">
                <button className="secondary" disabled={busy || forecasting || task?.status === "submitting"} onClick={() => forecast(task, actioners)}>{forecasting ? <LoadingIcon /> : <RefreshCw size={16} strokeWidth={1.8} />}重新检测审批路径</button>
                <button className="primary" disabled={busy || forecasting || !Object.keys(task.workflow_forecasts || {}).length || task?.status === "submitting"} onClick={preview}>{busy ? <LoadingIcon /> : <ClipboardCheck size={16} strokeWidth={1.8} />}保存节点选择并生成预览</button>
              </div>
              <AttachmentActionBar selected={selected} unresolved={unresolved} groupsDirty={groupsDirty} result={result} busy={busy || forecasting || task?.status === "submitting"} prepare={prepare} viewResults={() => setResultOpen(true)} reupload={() => { try { localStorage.removeItem("business-platform-active-task-v1"); } catch {} setTask(null); setChoices([]); setActioners({}); setResultOpen(false); }} />
            </div>
          ) : (
            <PreviewPanel
              task={task}
              openAttachment={(item) => {
                const index = task.result?.departments?.findIndex((group) => group.id === item.group_id) ?? -1;
                if (index >= 0) setAttachmentPreview({ index, name: item.attachment_name, groupId: item.group_id });
              }}
              previews={previews}
              attempts={task.approval_attempts || []}
              progress={task.submission_progress}
              busy={busy}
              liveEnabled={connection?.live_submit_enabled}
              preview={preview}
              openProgress={() => setProgressOpen(true)}
              requestSubmit={() => setSubmitOpen(true)}
              retry={() => setSubmitOpen("retry")}
            />
          )}
        </>
      )}
      {nodePicker && (
        <NodePersonPicker
          key={`${nodePicker.groupId}:${nodePicker.node.actor_key}`}
          node={nodePicker.node}
          configuredUsers={roleScopes.find((scope) => normalizeNodeName(scope.role_name) === normalizeNodeName(nodePicker.node.name))?.users || []}
          groupName={task?.workflow_forecasts?.[nodePicker.groupId]?.name || ""}
          selected={actioners[nodePicker.groupId]?.[nodePicker.node.actor_key] || []}
          saving={pickerSaving}
          onSave={applyNodePicker}
          close={() => !pickerSaving && setNodePicker(null)}
        />
      )}
      <ResultModal
        open={resultOpen}
        task={task}
        close={() => setResultOpen(false)}
      />
      {attachmentPreview && (
        <SpreadsheetPreviewModal
          taskId={task?.id}
          attachment={attachmentPreview}
          close={() => setAttachmentPreview(null)}
        />
      )}
      <SubmissionProgressModal
        open={progressOpen}
        task={task}
        close={() => setProgressOpen(false)}
      />
      {submitOpen && (
        <ConfirmModal
          count={confirmationPreviews.length}
          retry={submitOpen === "retry"}
          legacyRequired={confirmationPreviews.some((item) => item.legacy_chain?.mode === "explicit_chain")}
          close={() => setSubmitOpen(false)}
          confirm={(consent) => submit(submitOpen === "retry", consent)}
        />
      )}
    </section>
  );
}

function PageHeading({ connection }) {
  return (
    <div className="page-heading">
      <div>
        <span className="section-kicker">PAYMENT OPERATIONS</span>
        <h1>{MODULE_NAME}</h1>
        <p>
          按实际数据形成付款分组，生成部门附件，并在提交前校验钉钉审批路径。
        </p>
      </div>
      <span
        className={`connection-pill ${connection?.configured ? "ready" : ""}`}
      >
        <i />
        {connection?.configured ? "钉钉已配置" : "钉钉待配置"}
      </span>
    </div>
  );
}
function UploadPanel({ done, setNotice }) {
  const [file, setFile] = useState(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const input = useRef(null);
  function choose(item) {
    if (!item) return;
    if (!/\.(xlsx|ods|numbers)$/i.test(item.name))
      return setNotice({
        type: "error",
        text: "请上传可编辑、未加密的 .xlsx、.ods 或 .numbers 文件。",
      });
    setFile(item);
  }
  async function upload() {
    if (!file) return;
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      done(await api("/tasks", { method: "POST", body }));
      setNotice({
        type: "success",
        text: "总表解析完成，请核对实际付款分组。",
      });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="upload-panel">
      <div className="upload-copy">
        <span className="section-kicker">SOURCE WORKBOOK</span>
        <h2>上传月度补贴总表</h2>
        <p>
          系统会按实际费用识别分组。没有费用的部门自动跳过，未配置的新部门等待人工归属。
        </p>
        <ul>
          <li>
            <Icon name="check" />
            三类餐补重新计算
          </li>
          <li>
            <Icon name="check" />
            同名部门按公司主体分开
          </li>
          <li>
            <Icon name="check" />
            原特殊合并规则继续生效
          </li>
        </ul>
      </div>
      <div
        className={`drop-zone ${drag ? "dragging" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDrag(false);
          choose(event.dataTransfer.files?.[0]);
        }}
      >
        <input
          ref={input}
          type="file"
          accept=".xlsx,.ods,.numbers"
          hidden
          onChange={(event) => choose(event.target.files?.[0])}
        />
        <span className="upload-icon">
          <Icon name="upload" size={29} strokeWidth={1.6} />
        </span>
        <strong>{file ? file.name : "拖拽总表到这里"}</strong>
        <small>
          {file
            ? `${(file.size / 1024).toFixed(1)} KB · 已准备解析`
            : "支持 .xlsx、.ods 和 .numbers"}
        </small>
        <button
          className="secondary"
          onClick={(event) => {
            event.stopPropagation();
            input.current?.click();
          }}
        >
          选择文件
        </button>
        <button
          className="primary upload-submit"
          disabled={!file || busy}
          onClick={(event) => {
            event.stopPropagation();
            upload();
          }}
        >
          {busy && <LoadingIcon />}
          {busy ? "正在解析…" : "解析并进入分组"}
        </button>
      </div>
    </div>
  );
}
function Stats({ analysis, selected, unresolved }) {
  return (
    <div className="stats">
      <Stat
        label="识别付款分组"
        value={analysis.actual_groups || 0}
        suffix="个"
        tone="purple"
      />
      <Stat label="本次选中分组" value={selected} suffix="个" tone="blue" />
      <Stat
        label="待归属部门"
        value={unresolved}
        suffix="个"
        tone={unresolved ? "orange" : "green"}
      />
      <Stat
        label="餐补合计"
        value={`¥ ${money(analysis.payment_total || 0)}`}
        suffix=""
        tone="total"
      />
    </div>
  );
}
function Stat({ label, value, suffix, tone }) {
  return (
    <div className={`stat ${tone}`}>
      <small>{label}</small>
      <strong>
        {value}
        <em>{suffix}</em>
      </strong>
    </div>
  );
}
function Groups({ analysis, choices, updateChoice, setAllChoices }) {
  const mapped = Object.fromEntries(
    choices.map((item) => [item.group_id, item]),
  );
  const selectedCount = choices.filter((item) => item.selected).length;
  const allSelected = choices.length > 0 && selectedCount === choices.length;
  const mark = allSelected ? <Check size={11} strokeWidth={2.5} /> : null;
  return (
    <section className="section-card">
      <div className="section-head group-section-head">
        <div>
          <span className="section-kicker">PAYMENT GROUPS</span>
          <h2>付款分组与部门归属</h2>
          <p>只为勾选的分组生成附件并发起审批。未知部门必须先完成归属。</p>
        </div>
        <div className="group-selection">
          <span>
            已选 {selectedCount} / {choices.length}
          </span>
          <button
            className="select-all"
            onClick={() => setAllChoices(!allSelected)}
          >
            <span className={`check-box ${allSelected ? "checked" : ""}`}>
              {mark}
            </span>
            {allSelected ? "取消全选" : "全选"}
          </button>
        </div>
      </div>
      <div className="table-wrap">
        <table className="group-table">
          <thead>
            <tr>
              <th>
                <button
                  className="table-check"
                  onClick={() => setAllChoices(!allSelected)}
                  title={allSelected ? "取消全选" : "全选"}
                  aria-label={allSelected ? "取消全选付款分组" : "全选付款分组"}
                >
                  <span className={`check-box ${allSelected ? "checked" : ""}`}>
                    {mark}
                  </span>
                </button>
              </th>
              <th>公司主体 / 来源部门</th>
              <th>付款部门</th>
              <th>人数</th>
              <th>金额</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {(analysis.groups || []).map((group) => {
              const choice = mapped[group.id] || {};
              const sourceDepartments = Array.isArray(group.source_departments)
                ? group.source_departments.join("、")
                : group.source_departments || "";
              return (
                <tr key={group.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`选择 ${group.company} ${choice.payment_department || sourceDepartments}`}
                      checked={choice.selected ?? true}
                      onChange={(event) =>
                        updateChoice(group.id, {
                          selected: event.target.checked,
                        })
                      }
                    />
                  </td>
                  <td>
                    <strong>{group.company}</strong>
                    <small>{sourceDepartments}</small>
                  </td>
                  <td>
                    <input
                      className={
                        !choice.payment_department && choice.selected
                          ? "invalid"
                          : ""
                      }
                      value={choice.payment_department || ""}
                      placeholder="指定付款部门"
                      onChange={(event) =>
                        updateChoice(group.id, {
                          payment_department: event.target.value,
                        })
                      }
                    />
                  </td>
                  <td>{group.count}</td>
                  <td className="amount">¥ {money(group.amount)}</td>
                  <td>
                    <span
                      className={`tiny-tag ${group.known_rule ? "ok" : "warn"}`}
                    >
                      {group.known_rule ? "已匹配" : "待确认"}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
function AttachmentActionBar({ selected, unresolved, groupsDirty, result, busy, prepare, viewResults, reupload }) {
  return (
              <div className="action-bar">
                <div>
                  <strong>本次选择 {selected} 个来源分组</strong>
                  <small>
                    {unresolved
                      ? `还有 ${unresolved} 个部门待归属`
                      : groupsDirty
                        ? "付款选择已变更，请重新生成附件后再预览审批"
                        : result
                          ? "附件已生成，可核对审批节点并生成预览"
                          : "只为已勾选分组生成附件，并发起对应审批"}
                  </small>
                </div>
                <div className="action-buttons">
                  <button
                    className="secondary"
                    disabled={busy || !result || groupsDirty}
                    title={
                      groupsDirty ? "付款分组已变更，请先重新生成附件" : ""
                    }
                    onClick={viewResults}
                  >
                    <Icon name="eye" />
                    查看结果文件
                  </button>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={reupload}
                  >
                    <Icon name="retry" />
                    重新上传
                  </button>
                  <button
                    className="primary"
                    disabled={busy || !selected || unresolved > 0}
                    onClick={prepare}
                  >
                    {busy && <LoadingIcon />}
                    {busy
                      ? "处理中…"
                      : result
                        ? "重新生成附件"
                        : "确认分组并生成附件"}
                  </button>
                </div>
              </div>
  );
}

function NodePersonPicker({ node, groupName, configuredUsers, selected, saving, onSave, close, configuration = false }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [recent] = useState(recentApprovers);
  const [picked, setPicked] = useState(selected);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef(null);
  const requestRef = useRef(0);
  const unsupported = node.selection_range_kind === "unsupported";
  const templateIds = templateCandidateIds(node);
  const templateSet = useMemo(() => new Set(templateIds), [templateIds.join("\u0000")]);
  const inRange = (user) => !templateSet.size || templateSet.has(user.user_id);
  const configured = configuredUsers.length > 0;
  const { visibleConfigured, visibleRecent, visibleResults, matchingResults } = pickerSections({
    node, configuredUsers, recent, results, query,
  });
  const additions = newScopeCandidates(picked, configuredUsers);
  // Typing filters template candidates locally without repeating their directory lookups.
  const searchKeyword = templateSet.size ? "" : query.trim();
  const selectedIds = new Set(picked.map((user) => user.user_id));
  const lockedIds = new Set(node.template_default_user_ids || []);
  const toggle = (user) => {
    if (lockedIds.has(user.user_id)) return;
    setError("");
    setPicked((current) => current.some((item) => item.user_id === user.user_id)
      ? current.filter((item) => item.user_id !== user.user_id)
      : node.allowed_multi ? [...current, user] : [user]);
  };
  useEffect(() => { inputRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (event) => { if (event.key === "Escape" && !saving) close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close, saving]);
  useEffect(() => {
    if (unsupported) return undefined;
    const currentRequest = ++requestRef.current;
    setError("");
    if (!searchKeyword && !templateSet.size) {
      setResults([]);
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    setResults([]);
    setLoading(true);
    const timer = window.setTimeout(async () => {
      setError("");
      try {
        let found;
        if (templateSet.size) {
          const ids = [...templateSet];
          const chunks = [];
          for (let i = 0; i < ids.length; i += 40) chunks.push(ids.slice(i, i + 40));
          const pages = await Promise.all(chunks.map((chunk) => api(`/dingtalk/users?user_ids=${encodeURIComponent(chunk.join(","))}`, { signal: controller.signal })));
          found = pages.flatMap((page) => page.items || []);
        } else {
          const data = await api(`/dingtalk/users?keyword=${encodeURIComponent(searchKeyword)}`, { signal: controller.signal });
          found = data.items || [];
        }
        if (requestRef.current === currentRequest) setResults(found);
      } catch (failure) {
        if (!controller.signal.aborted && requestRef.current === currentRequest) setError(failure.message);
      } finally {
        if (requestRef.current === currentRequest) setLoading(false);
      }
    }, templateSet.size ? 0 : 350);
    return () => { ++requestRef.current; window.clearTimeout(timer); controller.abort(); };
  }, [searchKeyword, unsupported, templateSet]);
  async function submit() {
    if (saving || unsupported) return;
    if (node.required && !picked.length) return setError("该节点为必选，请至少添加一位审批人。");
    if (!node.allowed_multi && picked.length > 1) return setError("该节点只能选择一位审批人。");
    if (picked.some((user) => !inRange(user))) return setError("所选人员不在钉钉模板允许范围内。");
    setError("");
    try { await onSave(picked); }
    catch (failure) { setError(failure.message); }
  }
  const row = (user) => (
    <button type="button" className={`node-picker-person ${selectedIds.has(user.user_id) ? "is-picked" : ""}`}
      key={user.user_id} onClick={() => toggle(user)} disabled={saving || lockedIds.has(user.user_id)} aria-pressed={selectedIds.has(user.user_id)}>
      <span className="node-picker-avatar" aria-hidden="true">{(user.name || user.user_id).slice(0, 1)}</span>
      <span className="node-picker-person-text"><strong>{user.name || user.user_id}</strong><small>{user.user_id}{lockedIds.has(user.user_id) ? " · 模板默认，不可删除" : user.source === "local_default" ? " · 固定抄送预填" : ""}</small></span>
      {selectedIds.has(user.user_id) ? <Check size={17} strokeWidth={2} /> : <UserPlus size={17} strokeWidth={1.8} />}
    </button>
  );
  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !saving) close(); }}>
      <section className="node-picker-modal" role="dialog" aria-modal="true" aria-labelledby="node-picker-title">
        <header className="node-picker-header">
          <div><span className="section-kicker">APPROVER SEARCH</span><h2 id="node-picker-title">{node.actor_type === "notifier" || configuration ? "选择抄送人" : configured ? "选择审批人" : "添加审批人"}</h2>
            <p>{groupName ? `${groupName} · ` : ""}{node.name} · {node.allowed_multi ? "可多选" : "单选"}{node.required ? " · 必选" : " · 可留空"}</p></div>
          <button type="button" className="modal-close" onClick={close} disabled={saving} aria-label="关闭"><X size={18} strokeWidth={1.8} /></button>
        </header>
        <div className="node-picker-content">
          {unsupported ? <div className="inline-error">钉钉返回的可选人员范围无法识别，请先在审批模板中明确指定成员，当前不能保存。</div> : <>
            <label className="node-picker-search"><Search size={18} strokeWidth={1.8} />
              <input ref={inputRef} value={query} onChange={(event) => setQuery(event.target.value)}
                placeholder={templateSet.size ? "搜索模板允许人员的姓名或 UserId" : "搜索通讯录姓名或 UserId"} aria-label="搜索审批人" maxLength={60} disabled={saving} />
              {loading && <LoadingIcon size={17} />}</label>
            <p className="node-picker-explain">
              {configured ? `已配置 ${configuredUsers.length} 位人员，也可搜索通讯录添加候选人。` : "输入姓名或 UserId 搜索通讯录，保留最近添加的人员。"}
              {templateSet.size ? ` 当前模板限定 ${templateSet.size} 位人员，只显示范围内成员。` : " 搜索范围为应用获授权的通讯录。"}
              {configuration ? "保存后应用于后续重新生成附件的任务，不覆盖已有任务。" : "新选人员将在保存时追加到付款资料的本地候选范围，不修改钉钉模板。"}
            </p>
            <div className="node-picker-section"><h3>本次已选 <span>{picked.length}</span></h3>
              {picked.length ? <div className="node-picker-list">{picked.map(row)}</div> : <p className="node-picker-empty">还没有选择审批人</p>}</div>
            {configured && <div className="node-picker-section"><h3>已配置人员 <span>{visibleConfigured.length}</span></h3>
              {visibleConfigured.length ? <div className="node-picker-list">{visibleConfigured.map(row)}</div>
                : <p className="node-picker-empty">{configuredUsers.some(inRange)
                  ? "没有匹配的已配置人员" : "已配置人员不在当前审批路径可选范围，请核对钉钉模板并重新检测"}</p>}</div>}
            {visibleRecent.length > 0 && <div className="node-picker-section"><h3>最近添加</h3><div className="node-picker-list">{visibleRecent.map(row)}</div></div>}
            <div className="node-picker-section"><h3>通讯录搜索结果 <span>{loading ? "" : visibleResults.length}</span></h3>
              {!query.trim() && !templateSet.size ? <p className="node-picker-empty">输入姓名或 UserId 开始搜索</p>
                : loading ? <p className="node-picker-empty"><LoadingIcon size={16} /> 正在搜索…</p>
                : visibleResults.length ? <div className="node-picker-list">{visibleResults.map(row)}</div>
                : <p className="node-picker-empty">{matchingResults ? "匹配人员已显示在上方列表中" : "没有找到可选人员，请检查关键词或通讯录授权范围"}</p>}</div>
          </>}
          {error && <div className="inline-error" role="alert">{error}</div>}
        </div>
        <footer className="node-picker-footer"><span>{configuration ? "默认配置保存在服务器；最近使用记录保存在当前浏览器" : additions.length ? `保存后将追加 ${additions.length} 位本地候选人，保留原有人员` : "最近添加记录仅保存在当前浏览器"}</span>
          <div><button type="button" className="secondary" onClick={close} disabled={saving}>取消</button>
            <button type="button" className="primary" onClick={submit} disabled={saving || unsupported}>
              {saving ? <LoadingIcon size={16} /> : <Check size={16} strokeWidth={1.8} />}{configuration ? "确定选择" : additions.length ? "保存并加入候选范围" : "保存本次选择"}</button></div></footer>
      </section>
    </div>
  );
}

function Forecasts({
  forecasts,
  actioners,
  roleScopes,
  expanded,
  setExpanded,
  openNodePicker,
  forecasting,
}) {
  const items = Object.entries(forecasts);
  if (forecasting)
    return (
      <section className="section-card forecast-loading">
        <LoadingIcon />
        <strong>正在检测钉钉审批路径</strong>
        <small>根据各付款分组的表单值识别实际激活节点。</small>
      </section>
    );
  if (!items.length)
    return (
      <section className="section-card">
        <div className="empty-inline">附件已生成，正在等待钉钉流程检测。</div>
      </section>
    );
  return (
    <section className="section-card">
      <div className="section-head">
        <div>
          <span className="section-kicker">DINGTALK FORECAST</span>
          <h2>动态审批节点</h2>
          <p>固定节点只读；手选节点点击打开人员窗口。已配置人员优先显示，也可搜索通讯录添加候选人；钉钉模板范围始终优先。</p>
        </div>
      </div>
      <div className="forecast-list">
        {items.map(([groupId, forecast]) => {
          const open = expanded === groupId;
          return (
            <div className="forecast-group" key={groupId}>
              <button
                className="forecast-toggle"
                aria-expanded={open}
                onClick={() => setExpanded(open ? null : groupId)}
              >
                <div>
                  <strong>{forecast.name || groupId}</strong>
                  <small>
                    {forecast.error
                      ? forecast.error
                      : "当前表单实际激活的审批路径"}
                  </small>
                </div>
                <span className={`tiny-tag ${forecast.error ? "bad" : "ok"}`}>
                  {forecast.error ? "检测失败" : "已检测"}
                </span>
                <span className={`forecast-chevron ${open ? "open" : ""}`}>
                  <ChevronDown size={17} strokeWidth={1.9} />
                </span>
              </button>
              {open && (
                <div className="forecast-body">
                  {!forecast.error && (
                    <div className="branch-note">
                      仅显示本次表单实际激活的路径，条件未命中的审批分支不会出现。
                    </div>
                  )}
                  <TemplateExecutionNote plan={forecast.compatibility?.chain} />
                  {forecast.compatibility?.issues?.length > 0 && (
                    <div className="compatibility-notice" role="status">
                      <strong><CircleAlert size={16} strokeWidth={1.8} />当前路径不能提交</strong>
                      <p className="compatibility-explainer">流程路径问题与各节点的人员校验分开显示；未报独立错误的抄送人无需因此更换或清空。</p>
                      <ul>{forecast.compatibility.issues.map((issue, index) => <li key={[issue.code,issue.activity_id,index].join(':')}><span className="issue-scope">{issue.activity_id ? "节点问题" : "流程整体"}</span>{issue.message}</li>)}</ul>
                      {(forecast.path_diagnostics || forecast.compatibility.path_diagnostics)?.unresolved_connections?.length > 0 && (
                        <details className="path-diagnostics"><summary>查看待确认的路由连接</summary><p>以下只展示接口返回的标识，不将出边 ID 当成目标节点，也不按返回数组猜测顺序。</p>
                          <ul>{(forecast.path_diagnostics || forecast.compatibility.path_diagnostics).unresolved_connections.map((edge, index) => <li key={index}><span>来源 <code>{edge.source_activity_id}</code></span><span>出边 <code>{displayFieldValue(edge.out_id)}</code></span><small>目标标识待核对，实际流转由钉钉模板处理</small></li>)}</ul>
                        </details>
                      )}
                    </div>
                  )}
              {forecast.error ? (
                    <div className="inline-error">{forecast.error}</div>
                  ) : (
                    (forecast.nodes || []).map((node) => (
                      <Node
                        key={node.activity_id}
                        groupId={groupId}
                        node={node}
                        workflowCompatibility={forecast.compatibility}
                        actioners={actioners[groupId] || {}}
                        roleScopes={roleScopes}
                        openNodePicker={() => openNodePicker(groupId, node)}
                      />
                    ))
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
function TemplateExecutionNote({ plan }) {
  if (!plan?.preserves_template || !plan.handlers?.length) return null;
  return <div className="template-execution-note" role="note">
    <strong><Icon name="people" />保留原模板，由钉钉流转办理</strong>
    <p>平台只负责发起申请，不删除出纳节点，不把办理人改成审批人，也不会代为办理。</p>
    {plan.manual_binding_required && <p>当前旧接口的原模板手选人员绑定尚未验证，所选人员已保留；在解决绑定问题前，不会正式提交。</p>}
  </div>;
}
function Node({ node, actioners, roleScopes, openNodePicker, workflowCompatibility }) {
  const checked = nodeCompatibilityView(node, workflowCompatibility);
  const manual = node.kind === "manual";
  const configuredScope = roleScopes.find((scope) =>
    normalizeNodeName(scope.role_name) === normalizeNodeName(node.name) && (scope.users || []).length > 0);
  const rangeReady =
    node.selection_range_restricted && node.selection_range_resolvable;
  const rangeText = node.allowed_user_ids?.length
    ? `可选范围 ${node.allowed_user_ids.length} 人`
    : node.selection_range_restricted
      ? "范围待修正"
      : "未设置范围";
  return (
    <div className="workflow-node">
      <div>
        <strong>{node.name}</strong>
        <div className="node-tags">
          <span className={manual ? "blue" : ""}>
            {node.actor_type === "audit" ? "办理人" : node.actor_type === "notifier" ? manual ? "手选抄送" : "固定抄送" : manual ? "手选节点" : "固定节点"}
          </span>
          {manual && (
            <span className={node.required ? "red" : ""}>
              {node.required ? "必选" : "可选"}
            </span>
          )}
          {checked && <span className={checked.status === "incompatible" ? "red" : "blue"} title={checked.hint}>{checked.label}</span>}
          {manual && <span>{node.allowed_multi ? "可多选" : "单选"}</span>}
          {manual && (
            <span className={rangeReady ? "blue" : "red"}>{rangeText}</span>
          )}
        </div>
      </div>
      {manual ? (
        <div className="node-control">
          <button type="button" className="node-picker-trigger" onClick={openNodePicker}
            aria-label={`${node.name}审批人，打开人员选择窗口`}>
            <span>{(actioners[node.actor_key] || []).length
              ? (actioners[node.actor_key] || []).map((user) => user.name || user.user_id).join("、")
              : configuredScope ? `已配置 ${configuredScope.users.length} 人，可搜索添加` : "搜索并添加审批人"}</span>
            <Search size={16} strokeWidth={1.8} />
          </button>
          {!node.selection_range_restricted && (
            <div className="node-picker-hint">该节点尚无可选范围，点击搜索并添加人员；钉钉模板限制仍需遵守。</div>
          )}
          {node.selection_range_restricted &&
            !node.selection_range_resolvable && (
              <div className="inline-error">
                {node.selection_range_kind === "unsupported"
                  ? "钉钉返回了无法识别的人员范围，请改为明确指定成员。"
                  : "已配置人员与钉钉模板范围无交集，请核对付款资料或模板设置。"}
              </div>
            )}
        </div>
      ) : (
        <div className="fixed-users">
          {node.fixed_actioners?.length ? (
            node.fixed_actioners.map((user) => (
              <span key={user.user_id}>
                <Icon name="people" />
                {user.name}
              </span>
            ))
          ) : (
            <small>人员或自动行为尚未由接口确认</small>
          )}
        </div>
      )}
      {checked?.status === "delegated" && <div className="node-delegated-note">{checked.hint}</div>}
      {checked?.issues.length > 0 && <div className="node-own-issues" role="status">{checked.issues.map((issue,index) => <p key={index}>{issue.message}</p>)}</div>}
    </div>
  );
}
function PreviewPanel({
  task,
  openAttachment,
  previews,
  attempts,
  progress,
  busy,
  liveEnabled,
  preview,
  openProgress,
  requestSubmit,
  retry,
}) {
  const candidates = eligibleApprovalPreviews(previews, attempts);
  const retryCandidates = eligibleApprovalPreviews(previews, attempts, true);
  const valid = candidates.length > 0 && candidates.every((item) => item.valid);
  const retryValid = retryCandidates.length > 0 && retryCandidates.every((item) => item.valid);
  const submitting = Boolean(progress?.processing || progress?.queued);
  const canShowProgress = attempts.length > 0 || Boolean(progress?.total);
  return (
    <section className="section-card preview-card">
      <div className="section-head preview-heading">
        <div>
          <span className="section-kicker">DINGTALK APPROVAL</span>
          <h2>审批预览与提交</h2>
          <p>正式发起前会再次检测节点签名，防止审批模板变更后误提交。</p>
        </div>
        <div className="action-buttons">
          <button
            className="secondary"
            onClick={openProgress}
            disabled={!canShowProgress}
            title={
              canShowProgress
                ? "查看各部门的提交处理节点"
                : "提交开始后可查看进度"
            }
          >
            {submitting ? (
              <LoadingIcon />
            ) : (
              <ListChecks size={16} strokeWidth={1.8} />
            )}
            {submitting
              ? `提交中 ${progress?.completed || 0}/${progress?.total || 0}`
              : "查看提交进度"}
          </button>
          <button className="secondary" onClick={preview} disabled={busy}>
            {busy && <LoadingIcon />}生成审批预览
          </button>
          <button
            className="primary"
            disabled={busy || submitting || !valid || !liveEnabled}
            onClick={requestSubmit}
          >
            {busy && <LoadingIcon />}确认并批量发起
          </button>
        </div>
      </div>
      {!liveEnabled && (
        <div className="warning-bar">
          正式提交开关未开启。当前可以完成流程检测和审批预览。
        </div>
      )}
      {attempts.some((item) => item.status === "uncertain") && (
        <div className="warning-bar">
          存在结果待确认的审批，请先到钉钉核对后再处理，避免重复发起。
        </div>
      )}
      {previews.length ? (
        <div className="preview-list">
          {previews.map((item) => (
            <details key={item.group_id}>
              <summary>
                <div>
                  <span className="dept-avatar">
                    {item.payment_department.slice(0, 1)}
                  </span>
                  <strong>
                    {item.company} · {item.payment_department}
                  </strong>
                  <small>
                    {item.count} 人 · ¥ {money(item.amount)} ·{" "}
                    {item.attachment_name}
                  </small>
                </div>
                <span className={`tiny-tag ${item.valid ? "ok" : "bad"}`}>
                  {item.valid ? "校验通过" : "待处理"}
                </span>
              </summary>
              <PreviewDetails
                onPreviewAttachment={() => openAttachment(item)}
                canPreviewAttachment={Boolean(task?.result?.departments?.some((group) => group.id === item.group_id))}
                item={item}
                attempt={attempts.find(
                  (attempt) => attempt.group_id === item.group_id,
                )}
              />
            </details>
          ))}
        </div>
      ) : (
        <div className="empty-inline">
          生成附件并完成动态节点选择后，可在此生成审批预览。
        </div>
      )}
      {attempts.some((item) => item.status === "failed") && (
        <button
          className="retry-button"
          onClick={retry}
          disabled={busy || submitting || !retryValid || !liveEnabled}
        >
          {busy && <LoadingIcon />}
          <Icon name="retry" />
          只重试失败项
        </button>
      )}
    </section>
  );
}
function PreviewDetails({ item, attempt, onPreviewAttachment, canPreviewAttachment }) {
  const fields = (item.fields || []).filter((field) => field.name !== "附件");
  const stage =
    {
      queued: "等待处理",
      uploading: "上传审批附件",
      forecasting: "校验审批路径",
      creating: "创建钉钉审批",
      done: "已完成",
    }[attempt?.payload?.stage] || "";
  return (
    <div className="preview-details">
      {item.errors?.map((error) => (
        <div className="inline-error" key={error}>
          {error}
        </div>
      ))}
      <div className="field-grid">
        <div>
          <small>人数</small>
          <strong>{item.count}</strong>
        </div>
        <div>
          <small>付款金额</small>
          <strong>¥ {money(item.amount)}</strong>
        </div>
        <div className="field-wide">
          <small>附件</small>
          <div className="attachment-preview-line">
            <strong>{item.attachment_name}</strong>
            <button type="button" className="attachment-preview-button" onClick={onPreviewAttachment} disabled={!canPreviewAttachment} title="只读预览对应部门明细表">
              <Eye size={16} strokeWidth={1.8} />预览表格
            </button>
          </div>
        </div>
        {fields.map((field) =>
          ["项目分摊明细", "部门分摊明细"].includes(field.name) ? (
            <AllocationDetails field={field} key={field.name} />
          ) : (
            <div
              className={typeof field.value === "object" ? "field-wide" : ""}
              key={field.name}
            >
              <small>{field.name}</small>
              <strong>{displayFieldValue(field.value)}</strong>
            </div>
          ),
        )}
      </div>
      <TemplateExecutionNote plan={item.legacy_chain} />
      {["explicit_chain", "modern_template"].includes(item.legacy_chain?.mode) ? <LegacyChainPreview chain={item.legacy_chain} /> : <div className="node-summary">
        {(item.nodes || []).map((node) => (
          <span key={node.activity_id}>
            {node.name}：
            {node.kind === "manual"
              ? (item.node_actioners[node.actor_key] || [])
                  .map((user) => user.name || user.user_id)
                  .join("、") || (node.required ? "未选择" : "未选择（可选）")
              : (node.fixed_actioners || [])
                  .map((user) => user.name)
                  .join("、") || "模板处理"}
          </span>
        ))}
      </div>}
      {attempt && (
        <div className={`attempt ${attempt.status}`}>
          {attempt.status === "submitted" ? (
            `已提交，提交时间：${date(attempt.updated_at)}，实例 ID：${attempt.instance_id}`
          ) : (
            <>
              {stage && `${stage}：`}
              {attempt.error || "处理中"}
              {attempt.request_id ? `（请求编号：${attempt.request_id}）` : ""}
            </>
          )}
        </div>
      )}
    </div>
  );
}

const SUBMISSION_STAGES = [
  ["queued", "等待处理"],
  ["uploading", "上传附件"],
  ["forecasting", "校验路径"],
  ["creating", "创建审批"],
  ["done", "完成"],
];
function StageIcon({ state }) {
  if (state === "done") return <CheckCircle2 size={16} strokeWidth={2} />;
  if (state === "active") return <LoadingIcon size={16} />;
  if (state === "failed") return <CircleAlert size={16} strokeWidth={1.9} />;
  if (state === "uncertain") return <CircleHelp size={16} strokeWidth={1.9} />;
  return <Circle size={16} strokeWidth={1.8} />;
}
function stageState(stage, attempt) {
  if (!attempt) return "pending";
  if (attempt.status === "submitted") return "done";
  const current = SUBMISSION_STAGES.findIndex(
    ([key]) => key === attempt.payload?.stage,
  );
  const index = SUBMISSION_STAGES.findIndex(([key]) => key === stage);
  if (attempt.status === "failed" && index === current) return "failed";
  if (attempt.status === "uncertain" && index === current) return "uncertain";
  if (["queued", "submitting"].includes(attempt.status) && index === current)
    return "active";
  return index >= 0 && current > index ? "done" : "pending";
}
function SubmissionProgressModal({ open, task, close }) {
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, close]);
  if (!open) return null;
  const attempts = task?.approval_attempts || [];
  const progress = task?.submission_progress || {};
  const previewById = new Map(
    (task?.previews || []).map((item) => [item.group_id, item]),
  );
  const groups = (task?.result?.departments || []).map((group) => ({
    ...group,
    ...(previewById.get(group.id) || {}),
  }));
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        className="submission-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="submission-modal-head">
          <div>
            <span className="section-kicker">SUBMISSION PROGRESS</span>
            <h2>批量审批提交进度</h2>
            <p>
              {task?.status === "submitting"
                ? "正在按安全并发数处理付款分组，关闭窗口不会中断提交。"
                : "查看本次各付款分组的提交结果和处理节点。"}
            </p>
          </div>
          <button
            className="modal-close"
            onClick={close}
            title="关闭"
            aria-label="关闭提交进度"
          >
            <Icon name="close" />
          </button>
        </header>
        <div className="submission-summary">
          <div className="progress-summary-copy">
            <strong>
              {progress.completed || 0}
              <small> / {progress.total || groups.length} 已完成</small>
            </strong>
            <span>
              {progress.processing ? (
                <>
                  <LoadingIcon size={14} />
                  正在处理 {progress.processing} 条
                </>
              ) : progress.uncertain ? (
                "存在待确认结果"
              ) : progress.failed ? (
                "存在失败项"
              ) : (
                "当前没有处理中的审批"
              )}
            </span>
          </div>
          <div
            className="progress-bar"
            aria-label={`整体完成 ${progress.percent || 0}%`}
          >
            <i style={{ width: `${progress.percent || 0}%` }} />
          </div>
          <div className="progress-totals">
            <span className="ok">成功 {progress.completed || 0}</span>
            <span>排队 {progress.queued || 0}</span>
            <span className="bad">失败 {progress.failed || 0}</span>
            {Boolean(progress.uncertain) && (
              <span className="warn">待确认 {progress.uncertain}</span>
            )}
          </div>
        </div>
        <div className="submission-list">
          {groups.length ? (
            groups.map((group) => {
              const attempt = attempts.find(
                (item) => item.group_id === group.id,
              );
              return (
                <details
                  className={`submission-group ${attempt?.status || "queued"}`}
                  key={group.id}
                >
                  <summary>
                    <div>
                      <strong>
                        {group.company} · {group.payment_department}
                      </strong>
                      <small>
                        {group.count || 0} 人 · ¥ {money(group.amount || 0)} ·{" "}
                        {group.attachment_name || group.file_name || "部门明细"}
                      </small>
                    </div>
                    <span
                      className={`tiny-tag ${attempt?.status === "submitted" ? "ok" : attempt?.status === "failed" ? "bad" : attempt?.status === "uncertain" ? "warn" : ""}`}
                    >
                      {attempt?.status === "submitted" ? (
                        <>
                          已提交 · {date(attempt.updated_at)}
                        </>
                      ) : attempt?.status === "failed" ? (
                        "提交失败"
                      ) : attempt?.status === "uncertain" ? (
                        "结果待确认"
                      ) : attempt?.status === "submitting" ? (
                        <>
                          <LoadingIcon size={12} />
                          处理中
                        </>
                      ) : (
                        "等待处理"
                      )}
                    </span>
                  </summary>
                  <div className="submission-stages">
                    {SUBMISSION_STAGES.map(([stage, label]) => {
                      const state = stageState(stage, attempt);
                      return (
                        <div
                          className={`submission-stage ${state}`}
                          key={stage}
                        >
                          <StageIcon state={state} />
                          <span>{label}</span>
                        </div>
                      );
                    })}
                  </div>
                  {attempt && (
                    <footer>
                      <span>请求编号：{attempt.request_id}</span>
                      {attempt.instance_id && (
                        <span>审批实例：{attempt.instance_id}</span>
                      )}
                      {attempt.status === "submitted" && attempt.updated_at && (
                        <span>提交时间：{date(attempt.updated_at)}</span>
                      )}
                      {attempt.error && <strong>{attempt.error}</strong>}
                    </footer>
                  )}
                </details>
              );
            })
          ) : (
            <div className="empty-inline">尚未开始审批提交。</div>
          )}
        </div>
      </section>
    </div>
  );
}
function SpreadsheetPreviewModal({ taskId, attachment, close }) {
  const [page, setPage] = useState(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retryCount, setRetryCount] = useState(0);
  const pageSize = 50;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setData(null);
    setError("");
    const identity = `group_id=${encodeURIComponent(attachment.groupId)}&file_name=${encodeURIComponent(attachment.name)}`;
    api(`/tasks/${taskId}/preview/department/${attachment.index}?offset=${page * pageSize}&limit=${pageSize}&${identity}`, {
      signal: controller.signal,
    }).then((result) => {
      setData(result);
      setLoading(false);
    }).catch((failure) => {
      if (controller.signal.aborted) return;
      setError(failure.message);
      setLoading(false);
    });
    return () => controller.abort();
  }, [taskId, attachment.index, attachment.groupId, attachment.name, page, retryCount]);
  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [close]);
  const pages = Math.max(1, Math.ceil((data?.total || 0) / pageSize));
  const displayCell = (value, index) =>
    value == null ? "" : typeof value === "number" && index >= 6 && index <= 9
      ? value.toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : String(value);
  return (
    <div className="modal-backdrop" onClick={close}>
      <section className="spreadsheet-preview-modal" role="dialog" aria-modal="true" aria-labelledby="spreadsheet-preview-title" onClick={(event) => event.stopPropagation()}>
        <header className="spreadsheet-preview-header">
          <div>
            <span className="section-kicker">ATTACHMENT PREVIEW</span>
            <h2 id="spreadsheet-preview-title">预览部门明细表</h2>
            <p title={attachment.name}>{attachment.name}</p>
          </div>
          <div className="spreadsheet-preview-actions">
            {data && !error && <a className="secondary" href={`${API}/tasks/${taskId}/download/department/${attachment.index}?group_id=${encodeURIComponent(attachment.groupId)}&file_name=${encodeURIComponent(attachment.name)}`} download>
              <Download size={16} strokeWidth={1.8} />下载原表
            </a>}
            <button type="button" className="modal-close" onClick={close} title="关闭预览" aria-label="关闭预览"><X size={18} strokeWidth={1.8} /></button>
          </div>
        </header>
        <div className="spreadsheet-preview-scroll">
          {loading && <div className="spreadsheet-preview-state"><LoadingIcon />正在读取表格…</div>}
          {!loading && error && <div className="spreadsheet-preview-state inline-error">{error}<button type="button" className="secondary" onClick={() => setRetryCount((value) => value + 1)}>重试</button></div>}
          {!loading && !error && data && (
            <>
              <div className="spreadsheet-preview-title">{data.title || data.sheet_name}</div>
              <table className="spreadsheet-preview-table">
                <thead><tr><th scope="col">行号</th>{data.headers.map((header, index) => <th scope="col" key={index}>{header}</th>)}</tr></thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.number} className={row.cells[0] === "合计" ? "spreadsheet-preview-total" : ""}>
                      <th scope="row">{row.number}</th>
                      {row.cells.map((cell, index) => <td key={index} className={index >= 6 && index <= 9 ? "spreadsheet-preview-number" : ""}>{displayCell(cell, index)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
        <footer className="spreadsheet-preview-footer">
          <span>只读预览 · 共 {data?.total || 0} 行（含合计）</span>
          <div className="spreadsheet-preview-pages">
            <button type="button" className="secondary" disabled={loading || page === 0} onClick={() => setPage((value) => value - 1)}>上一页</button>
            <span>第 {page + 1} / {pages} 页</span>
            <button type="button" className="secondary" disabled={loading || page + 1 >= pages} onClick={() => setPage((value) => value + 1)}>下一页</button>
          </div>
        </footer>
      </section>
    </div>
  );
}
function ResultModal({ open, task, close }) {
  const result = task?.result;
  const departments = result?.departments || [];
  const [selected, setSelected] = useState([]);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  useEffect(() => {
    if (open) {
      setSelected([]);
      setDownloadError("");
    }
  }, [open, task?.id]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);
  if (!open) return null;
  const allSelected =
    departments.length > 0 && selected.length === departments.length;
  function toggle(index) {
    setSelected((values) =>
      values.includes(index)
        ? values.filter((value) => value !== index)
        : [...values, index],
    );
  }
  function toggleAll() {
    setSelected(allSelected ? [] : departments.map((_, index) => index));
  }
  async function downloadSelected() {
    if (!selected.length || downloading) return;
    setDownloading(true);
    setDownloadError("");
    try {
      const response = await fetch(
        `${API}/tasks/${task.id}/download/selected`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ indices: selected }),
        },
      );
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "下载已选文件失败");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${result?.month || "当月"}已选部门补贴明细.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      setDownloadError(error.message);
    } finally {
      setDownloading(false);
    }
  }
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        className="results-modal"
        role="dialog" aria-modal="true" aria-labelledby="result-files-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="results-modal-head">
          <div>
            <span className="section-kicker">RESULT CENTER</span>
            <h2 id="result-files-title">结果文件已生成</h2>
            <p>优先下载完整结果，或在下方勾选本次需要的部门明细。</p>
          </div>
          <button className="modal-close" onClick={close} title="关闭">
            <Icon name="close" />
          </button>
        </header>
        <div className="primary-downloads">
          <FileLink
            primary
            href={`${API}/tasks/${task.id}/download/summary`}
            name="新汇总表"
            meta="含本月总计行，可用于归档核对"
          />
          <FileLink
            primary
            href={`${API}/tasks/${task.id}/download/zip`}
            name="全部结果 ZIP"
            meta="汇总表及全部部门明细，一次下载"
          />
        </div>
        <div className="department-files-head">
          <div>
            <strong>部门明细</strong>
            <small>
              共 {departments.length} 份，已选择 {selected.length} 份
            </small>
          </div>
          <div>
            <button className="select-all" onClick={toggleAll}>
              <span className={`check-box ${allSelected ? "checked" : ""}`}>
                {allSelected ? <Check size={11} strokeWidth={2.5} /> : null}
              </span>
              {allSelected ? "取消全选" : "全选"}
            </button>
            <button
              className="primary selected-download"
              disabled={!selected.length || downloading}
              onClick={downloadSelected}
            >
              {downloading ? <LoadingIcon /> : <Icon name="download" />}
              {downloading
                ? "正在打包…"
                : `下载已选${selected.length ? ` (${selected.length})` : ""}`}
            </button>
          </div>
        </div>
        {downloadError && (
          <div className="inline-error result-error">{downloadError}</div>
        )}
        <div className="department-file-list">
          {departments.map((item, index) => (
            <DepartmentFile
              key={item.id}
              item={item}
              index={index}
              taskId={task.id}
              checked={selected.includes(index)}
              toggle={() => toggle(index)}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
function FileLink({ href, name, meta, primary = false }) {
  return (
    <a className={`file-link ${primary ? "primary-file" : ""}`} href={href}>
      <span>
        <Icon name={primary ? "download" : "file"} />
      </span>
      <div>
        <strong>{name}</strong>
        <small>{meta}</small>
      </div>
      <i>{primary ? "立即下载" : <Icon name="download" />}</i>
    </a>
  );
}
function DepartmentFile({ item, index, taskId, checked, toggle }) {
  return (
    <div className={`department-file ${checked ? "selected" : ""}`}>
      <button
        className="file-check"
        onClick={toggle}
        aria-label={`${checked ? "取消选择" : "选择"} ${item.file_name}`}
      >
        <span className={`check-box ${checked ? "checked" : ""}`}>
          {checked ? <Check size={11} strokeWidth={2.5} /> : null}
        </span>
      </button>
      <button className="file-copy" onClick={toggle}>
        <span>
          <Icon name="file" />
        </span>
        <div>
          <strong>{item.file_name}</strong>
          <small>
            {item.company} · {item.payment_department}
          </small>
        </div>
      </button>
      <a
        href={`${API}/tasks/${taskId}/download/department/${index}`}
        title="单独下载"
      >
        <Icon name="download" />
      </a>
    </div>
  );
}
function LegacyChainPreview({ chain }) {
  const methodLabels = { ONE_BY_ONE: "依次审批", AND: "会签", OR: "或签" };
  const ccLabels = { START: "开始抄送", FINISH: "结束抄送", START_FINISH: "开始和结束抄送" };
  return (
    <section className="legacy-chain" aria-label="模板审批路径（新接口）">
      <div className="legacy-chain-heading"><GitBranch size={18} strokeWidth={1.8} aria-hidden="true" /><strong>模板审批路径（新接口）</strong><span>{chain.nodes?.length || 0} 个审批步骤</span></div>
      {chain.warnings?.map((warning) => <p className="legacy-chain-warning" key={warning}><CircleAlert size={17} strokeWidth={1.8} aria-hidden="true" /><span>{warning}</span></p>)}
      <ol className="legacy-chain-list">
        {(chain.nodes || []).map((node, index) => (
          <li key={node.activity_id || index} className={node.skipped ? "legacy-chain-skipped" : ""}>
            <span className="legacy-chain-number">{index + 1}</span>
            <div className="legacy-chain-content">
              <div className="legacy-chain-title"><strong>{node.name}</strong><span className="tiny-tag">{node.role === "notifier" ? "抄送" : node.source === "manual" ? "手选" : "固定"}</span><small>{node.role === "notifier" ? ccLabels[node.cc_position] || "抄送位置待核对" : methodLabels[node.method] || "方式待核对"}</small></div>
              <p>{node.skipped ? "非必选，未选人员；由模板处理" : node.users?.map((user) => user.name || user.user_id).join("、") || "由钉钉模板解析"}</p>
              {node.legacy_steps?.length > 1 && <small>按上方人员顺序执行 {node.legacy_steps.length} 个单人审批步骤</small>}
            </div>
          </li>
        ))}
      </ol>
      <p className="legacy-chain-cc">{chain.cc_list ? "抄送方式：" + (ccLabels[chain.cc_position] || "待核对") : "固定节点、审批方式与抄送沿用钉钉模板，不覆盖完整审批链。"}</p>
    </section>
  );
}
function ConfirmModal({ count, close, confirm, legacyRequired = false, retry = false }) {
  const [consent, setConsent] = useState(false);
  useEffect(() => {
    const onKey = (event) => { if (event.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [close]);
  return (
    <div className="modal-backdrop" onClick={close}>
      <section className="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="batch-confirm-title" onClick={(event) => event.stopPropagation()}>
        <span className="modal-icon"><Icon name="alert" /></span>
        <h2 id="batch-confirm-title">{retry ? "确认重试失败审批" : "确认批量发起审批"}</h2>
        <p>{retry ? "仅重试明确失败的" : "将发起"} {count} 条独立付款审批。成功项和结果待确认项不会重复创建。提交时将上传附件并再次核对完整审批链。</p>
        {legacyRequired && <div className="legacy-confirm-notice">
          <p>本次使用旧接口指定完整审批链，不复用模板流程；手写签名、表单操作权限等高级设置不支持。</p>
          <label className="legacy-confirm-check"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>我已逐条核对固定审批人、手选人员、审批顺序和抄送配置，确认采用此模式。</span></label>
        </div>}
        <div className="modal-actions">
          <button className="secondary" onClick={close}>返回核对</button>
          <button className="primary" disabled={count < 1 || (legacyRequired && !consent)} onClick={() => confirm(consent)}>确认发起</button>
        </div>
      </section>
    </div>
  );
}
function History({ items, openTask }) {
  return (
    <section className="page simple-page">
      <div className="page-heading">
        <div>
          <span className="section-kicker">TASK ARCHIVE</span>
          <h1>任务记录</h1>
          <p>查看近期的拆分、审批预览和批量发起结果。</p>
        </div>
      </div>
      <div className="history-card">
        {items.length ? (
          items.map((item) => (
            <button key={item.id} onClick={() => openTask(item)}>
              <span className="history-icon">
                <Icon name="file" />
              </span>
              <div>
                <strong>{item.file_name}</strong>
                <small>{date(item.created_at)}</small>
              </div>
              <em className={item.status}>{statusText(item.status)}</em>
              {item.status === "submitting" && (
                <span className="history-progress" aria-label="审批提交进度">
                  <LoadingIcon size={13} />
                  <i>
                    {item.submission_progress?.completed || 0}/
                    {item.submission_progress?.total || 0}
                  </i>
                  <span className="history-mini-bar" aria-hidden="true">
                    <i
                      style={{
                        width: `${item.submission_progress?.percent || 0}%`,
                      }}
                    />
                  </span>
                </span>
              )}
              <Icon name="arrow" />
            </button>
          ))
        ) : (
          <div className="empty-inline">还没有任务记录。</div>
        )}
      </div>
    </section>
  );
}
function Profiles({ profiles, refresh, setNotice }) {
  const [tab, setTab] = useState("companies");
  const [drafts, setDrafts] = useState({});
  const [scopes, setScopes] = useState([]);
  const [loadingScopes, setLoadingScopes] = useState(true);
  const [cc, setCc] = useState(null);
  const [ccError, setCcError] = useState("");
  const [ccOpen, setCcOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const companies = [
    ...new Set([
      ...profiles.map((item) => item.company),
      "示例公司",
      "新进化",
      "上海再造",
      "老进化",
    ]),
  ];
  const profile = (company) =>
    drafts[company] ||
    profiles.find((item) => item.company === company) || {
      company,
      payment_entity: "",
      supplier_name: "",
      bank_account: "",
      bank_name: "",
    };
  useEffect(() => {
    let active = true;
    api("/approval-cc-defaults").then((data) => { if (active) setCc(data); }).catch((error) => { if (active) setCcError(error.message); });
    api("/approval-role-scopes")
      .then((data) => {
        if (active) setScopes(data.items || []);
      })
      .catch((error) => setNotice({ type: "error", text: error.message }))
      .finally(() => {
        if (active) setLoadingScopes(false);
      });
    return () => {
      active = false;
    };
  }, []);
  function change(company, key, value) {
    setDrafts((current) => ({
      ...current,
      [company]: { ...profile(company), [key]: value },
    }));
  }
  function updateScope(roleKey, patch) {
    setScopes((items) =>
      items.map((item) =>
        item.role_key === roleKey ? { ...item, ...patch } : item,
      ),
    );
  }
  async function saveAll() {
    if (saving) return;
    setSaving(true);
    try {
      if (tab === "companies") {
        await Promise.all(
          companies.map((company) =>
            api(`/company-profiles/${encodeURIComponent(company)}`, {
              method: "PUT",
              body: JSON.stringify(profile(company)),
            }),
          ),
        );
        setDrafts({});
        await refresh();
        setNotice({
          type: "success",
          text: `已保存 ${companies.length} 个公司主体的付款资料。`,
        });
      } else if (tab === "cc") {
        const data = await api("/approval-cc-defaults", { method: "PUT", body: JSON.stringify(cc) });
        setCc(data); setNotice({ type: "success", text: "固定抄送人已保存，后续生成附件时自动带入；已有任务不受影响。" });
      } else {
        const data = await api("/approval-role-scopes", {
          method: "PUT",
          body: JSON.stringify({ scopes }),
        });
        setScopes(data.items || []);
        setNotice({
          type: "success",
          text: "手选审批节点的可选人员范围已保存。",
        });
      }
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setSaving(false);
    }
  }
  const saveLabel = tab === "companies" ? "保存资料" : tab === "cc" ? "保存固定抄送人" : "保存人员范围";
  return (
    <section className="page simple-page profiles-page">
      <div className="page-heading">
        <div>
          <span className="section-kicker">PAYMENT PROFILES</span>
          <h1>付款资料</h1>
          <p>
            {tab === "companies"
              ? "维护公司付款主体、供应商和银行资料。"
              : tab === "cc" ? "为当前企业和审批模板设置默认抄送人，生成附件后自动带入。" : "限制手选审批节点能够选择的钉钉人员。"}
          </p>
        </div>
        <button
          className="primary profile-save"
          disabled={saving || (tab === "scopes" && loadingScopes) || (tab === "cc" && (!cc || !cc.corp_id || !cc.process_code))}
          onClick={saveAll}
        >
          {saving ? <LoadingIcon /> : <Icon name="save" />}
          {saving ? "正在保存..." : saveLabel}
        </button>
      </div>
      <div className="tabs profile-tabs">
        <button
          className={tab === "companies" ? "active" : ""}
          onClick={() => setTab("companies")}
        >
          <Building2 size={16} strokeWidth={1.8} />
          公司付款资料
        </button>
        <button
          className={tab === "scopes" ? "active" : ""}
          onClick={() => setTab("scopes")}
        >
          <UsersRound size={16} strokeWidth={1.8} />
          手选节点人员范围
        </button>
        <button className={tab === "cc" ? "active" : ""} onClick={() => setTab("cc")}><UsersRound size={16} strokeWidth={1.8} />固定抄送人</button>
      </div>
      {tab === "companies" ? (
        <div className="profile-grid">
          {companies.map((company) => (
            <div className="profile-card" key={company}>
              <h2>{company}</h2>
              <input
                placeholder="付款主体"
                value={profile(company).payment_entity}
                onChange={(event) =>
                  change(company, "payment_entity", event.target.value)
                }
              />
              <input
                placeholder="供应商名称"
                value={profile(company).supplier_name}
                onChange={(event) =>
                  change(company, "supplier_name", event.target.value)
                }
              />
              <input
                placeholder="银行账号"
                value={profile(company).bank_account}
                onChange={(event) =>
                  change(company, "bank_account", event.target.value)
                }
              />
              <input
                placeholder="开户银行"
                value={profile(company).bank_name}
                onChange={(event) =>
                  change(company, "bank_name", event.target.value)
                }
              />
            </div>
          ))}
        </div>
      ) : tab === "cc" ? (
        <section className="section-card cc-default-card">
          <div className="section-heading"><div><h2>固定抄送人</h2><p>模板默认人员保留，追加固定抄送人并去重。仅作用于手选抄送节点。</p></div>
          <button className="secondary" disabled={!cc || saving} onClick={() => setCcOpen(true)}><Search size={16} strokeWidth={1.8} />搜索并选择人员</button></div>
          {ccError ? <div className="inline-error">{ccError}</div> : !cc ? <p><LoadingIcon />正在读取固定抄送配置…</p> : <>
            <dl className="cc-template-info"><div><dt>企业</dt><dd>{cc.corp_id || "未配置"}</dd></div><div><dt>审批模板</dt><dd>{cc.process_code || "未配置"}</dd></div><div><dt>上次保存</dt><dd>{date(cc.updated_at)}</dd></div></dl>
            <div className="cc-selected-users">{cc.users.length ? cc.users.map((user) => <span key={user.user_id}><UsersRound size={15} strokeWidth={1.8} /><span>{user.name}<small>{user.user_id}</small></span><button type="button" disabled={saving} aria-label={`删除${user.name}`} onClick={() => setCc((current) => ({ ...current, users: current.users.filter((item) => item.user_id !== user.user_id) }))}><X size={15} strokeWidth={1.8} /></button></span>) : <p>还没有配置固定抄送人，点击搜索添加。</p>}</div>
            <p className="node-picker-explain">修改后点击右上方“保存固定抄送人”。已有任务保留当时快照，重新生成附件才使用新配置；抄送时机及人数限制仍需满足。</p>
          </>}
          {ccOpen && cc && <NodePersonPicker configuration node={{ name: "固定抄送人", actor_type: "notifier", allowed_multi: true, required: false }} groupName="当前审批模板" configuredUsers={cc.users} selected={cc.users} saving={false} close={() => setCcOpen(false)} onSave={async (users) => { if (users.length > 20) throw new Error("最多选择20位默认抄送人"); setCc((current) => ({ ...current, users })); rememberApprovers(users); setCcOpen(false); }} />}
        </section>
      ) : loadingScopes ? (
        <div className="section-card scope-loading">
          <LoadingIcon />
          <span>正在读取人员范围...</span>
        </div>
      ) : (
        <div className="scope-grid">
          {scopes.map((scope) => (
            <RoleScopeCard
              key={scope.role_key}
              scope={scope}
              update={(patch) => updateScope(scope.role_key, patch)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function RoleScopeCard({ scope, update }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const selectedIds = new Set((scope.users || []).map((user) => user.user_id));
  useEffect(() => {
    const keyword = query.trim();
    const currentRequest = ++requestId.current;
    if (!keyword) {
      setResults([]);
      setSearched(false);
      setError("");
      setSearching(false);
      return;
    }
    const timer = setTimeout(async () => {
      setSearching(true);
      setError("");
      try {
        const data = await api(
          `/dingtalk/users?keyword=${encodeURIComponent(keyword)}`,
        );
        if (requestId.current === currentRequest) {
          setResults(data.items || []);
          setSearched(true);
        }
      } catch (searchError) {
        if (requestId.current === currentRequest) {
          setError(searchError.message);
          setResults([]);
          setSearched(true);
        }
      } finally {
        if (requestId.current === currentRequest) setSearching(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [query]);
  function add(user) {
    if (!selectedIds.has(user.user_id))
      update({
        users: [
          ...(scope.users || []),
          { user_id: user.user_id, name: user.name },
        ],
      });
  }
  function remove(userId) {
    update({
      users: (scope.users || []).filter((user) => user.user_id !== userId),
    });
  }
  return (
    <section className="scope-card">
      <header>
        <div>
          <span className="section-kicker">TARGET SELECT</span>
          <h2>{scope.role_name}</h2>
        </div>
        <span className={`tiny-tag ${scope.users?.length ? "ok" : "warn"}`}>
          {scope.users?.length || 0} 人
        </span>
      </header>
      <label className="scope-name">
        <span>钉钉节点名称</span>
        <input
          value={scope.role_name}
          onChange={(event) => update({ role_name: event.target.value })}
        />
      </label>
      <div className="scope-selected">
        {scope.users?.length ? (
          scope.users.map((user) => (
            <span className="scope-chip" key={user.user_id}>
              <span>
                <strong>{user.name}</strong>
                <small>{user.user_id}</small>
              </span>
              <button
                onClick={() => remove(user.user_id)}
                title={`移除 ${user.name}`}
                aria-label={`移除 ${user.name}`}
              >
                <X size={14} strokeWidth={1.9} />
              </button>
            </span>
          ))
        ) : (
          <div className="scope-empty">尚未选择人员</div>
        )}
      </div>
      <div className="scope-search">
        <Search size={16} strokeWidth={1.8} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索姓名或 UserId"
        />
        <span>
          {searching && (
            <>
              <LoadingIcon size={13} />
              搜索中...
            </>
          )}
        </span>
      </div>
      {error && <div className="inline-error">{error}</div>}
      <div className="scope-results">
        {results.map((user) => {
          const selected = selectedIds.has(user.user_id);
          return (
            <button
              key={user.user_id}
              disabled={selected}
              onClick={() => add(user)}
            >
              <span className="scope-person">
                <strong>{user.name}</strong>
                <small>
                  {user.user_id}
                  {user.dept_id ? ` · 部门 ${user.dept_id}` : ""}
                </small>
              </span>
              <span className={selected ? "added" : ""}>
                {selected ? (
                  <>
                    <Check size={14} strokeWidth={2} />
                    已添加
                  </>
                ) : (
                  <>
                    <UserPlus size={15} strokeWidth={1.8} />
                    添加
                  </>
                )}
              </span>
            </button>
          );
        })}
        {searched && !searching && !results.length && !error && (
          <div className="scope-empty">
            没有找到匹配人员，请检查通讯录授权范围。
          </div>
        )}
      </div>
    </section>
  );
}
function Rules({ rules }) {
  return (
    <section className="page simple-page">
      <div className="page-heading">
        <div>
          <span className="section-kicker">RULE LIBRARY</span>
          <h1>部门归集规则</h1>
          <p>系统按实际数据动态分组，以下规则仅用于特殊部门合并。</p>
        </div>
      </div>
      <div className="rules-grid">
        {rules.map((rule, index) => (
          <div className="rule-card" key={`${rule.company}-${index}`}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <h3>{rule.payment_department}</h3>
            <p>
              {rule.company} · {rule.scope}
            </p>
            <small>{rule.filename_stem}</small>
          </div>
        ))}
      </div>
    </section>
  );
}
function Integration({ connection, setConnection, setNotice }) {
  const [checking, setChecking] = useState(false);
  async function check(remote = true) {
    setChecking(true);
    try {
      const data = await api(`/dingtalk/preflight?remote=${remote}`);
      setConnection(data);
      setNotice({
        type: data.ok ? "success" : remote ? "error" : "success",
        text: data.ok
          ? "钉钉在线预检通过。"
          : (data.messages || []).join("；") || "已重新读取本机配置。",
      });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setChecking(false);
    }
  }
  const statuses = [
    [
      "本机应用配置",
      connection?.configured,
      connection?.configured ? "Client ID 等资料已读取" : "资料未配置完整",
    ],
    [
      "审批表单 Schema",
      connection?.schema_ok,
      connection?.schema_ok ? "付款申请单字段已识别" : "执行在线预检后确认",
    ],
    [
      "动态审批节点",
      connection?.dynamic_workflow_nodes,
      connection?.dynamic_workflow_nodes
        ? "按实际表单预测激活路径"
        : "尚未启用",
    ],
    [
      "审批附件上传",
      connection?.attachment_ok,
      connection?.attachment_ok
        ? "钉盘空间与上传权限可用"
        : connection?.attachment_enabled
          ? "等待在线预检"
          : "附件开关未开启",
    ],
    [
      "正式批量提交",
      connection?.live_submit_enabled,
      connection?.live_submit_enabled ? "已开启" : "安全关闭",
    ],
  ];
  return (
    <section className="page simple-page integration-page">
      <div className="page-heading">
        <div>
          <span className="section-kicker">DINGTALK CONNECTION</span>
          <h1>钉钉审批接入</h1>
          <p>
            查看本机配置、审批模板和附件权限状态，正式提交前先完成在线预检。
          </p>
        </div>
        <span
          className={`connection-pill ${connection?.configured ? "ready" : ""}`}
        >
          <i />
          {connection?.configured ? "本机配置已读取" : "配置待完善"}
        </span>
      </div>
      <div className="integration-grid restored">
        <section className="integration-card config-card">
          <div className="integration-title">
            <div>
              <span className="section-kicker">LOCAL CONFIGURATION</span>
              <h2>连接配置</h2>
            </div>
            <span
              className={`tiny-tag ${connection?.configured ? "ok" : "warn"}`}
            >
              {connection?.configured ? "已配置" : "待配置"}
            </span>
          </div>
          <div className="config-list">
            <div>
              <span>AgentId</span>
              <strong>{connection?.agent_id || "未配置"}</strong>
            </div>
            <div>
              <span>CorpId</span>
              <strong>{connection?.corp_id || "未配置"}</strong>
            </div>
            <div className="wide">
              <span>processCode</span>
              <strong>{connection?.process_code || "未配置"}</strong>
            </div>
            <div>
              <span>固定发起人 UserId</span>
              <strong>{connection?.originator_user_id || "未配置"}</strong>
            </div>
            <div>
              <span>流程发起 DeptId</span>
              <strong>{connection?.originator_dept_id || "未配置"}</strong>
            </div>
            <div>
              <span>申请人部门 DeptId</span>
              <strong>{connection?.applicant_dept_id || "未配置"}</strong>
            </div>
          </div>
          <div className="integration-actions">
            <button
              className="secondary"
              disabled={checking}
              onClick={() => check(false)}
            >
              {checking ? (
                <LoadingIcon />
              ) : (
                <RefreshCw size={15} strokeWidth={1.9} />
              )}
              重新读取配置
            </button>
            <button
              className="primary"
              disabled={checking || !connection?.configured}
              onClick={() => check(true)}
            >
              {checking ? (
                <LoadingIcon />
              ) : (
                <ShieldCheck size={16} strokeWidth={1.9} />
              )}
              {checking ? "正在预检…" : "执行在线预检"}
            </button>
          </div>
        </section>
        <section className="integration-card safety-card">
          <div className="integration-title">
            <div>
              <span className="section-kicker">READINESS</span>
              <h2>接入状态</h2>
            </div>
          </div>
          <div className="status-list">
            {statuses.map(([label, ok, detail]) => (
              <div className={`status-line ${ok ? "ok" : ""}`} key={label}>
                <span>
                  <Icon
                    name={ok ? "check" : "alert"}
                    size={13}
                    strokeWidth={2}
                  />
                </span>
                <div>
                  <strong>{label}</strong>
                  <small>{detail}</small>
                </div>
                <em>{ok ? "正常" : "待确认"}</em>
              </div>
            ))}
          </div>
        </section>
      </div>
      <section className="env-guide">
        <div>
          <span className="section-kicker">CONFIGURATION GUIDE</span>
          <h2>本机配置指引</h2>
          <p>
            Client Secret 只保存在项目根目录的
            .env，不会显示在页面或返回给浏览器。
          </p>
        </div>
        <code>
          DINGTALK_CLIENT_ID=应用 Client ID
          <br />
          DINGTALK_CLIENT_SECRET=仅填写在本机
          <br />
          DINGTALK_PROCESS_CODE=付款申请单 processCode
          <br />
          DINGTALK_LIVE_SUBMIT_ENABLED=
          {connection?.live_submit_enabled ? "true" : "false"}
        </code>
      </section>
    </section>
  );
}
createRoot(document.getElementById("root")).render(<App />);
