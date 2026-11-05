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

const API = "/api";
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
  }, []);
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
  }, [task?.id, task?.status, history]);
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
            <strong>企业业务平台</strong>
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
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("groups");
  const [resultOpen, setResultOpen] = useState(false);
  const [submitOpen, setSubmitOpen] = useState(false);
  const [progressOpen, setProgressOpen] = useState(false);
  const [users, setUsers] = useState([]);
  const [loadingUsers, setLoadingUsers] = useState(false);
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
  const savedChoices = task?.choices || [];
  const groupsDirty =
    Boolean(result) &&
    (choices.length !== savedChoices.length ||
      choices.some(
        (item, index) =>
          item.group_id !== savedChoices[index]?.group_id ||
          item.selected !== savedChoices[index]?.selected ||
          item.payment_department !== savedChoices[index]?.payment_department,
      ));
  useEffect(() => {
    setChoices(choicesFor(task));
    setActioners(task?.workflow_actioners || {});
  }, [task?.id, task?.updated_at]);
  useEffect(() => {
    setTab(
      ["previewed", "submitted", "partial_failed"].includes(task?.status)
        ? "preview"
        : "groups",
    );
  }, [task?.id]);
  const updateChoice = (id, patch) =>
    setChoices((items) =>
      items.map((item) =>
        item.group_id === id ? { ...item, ...patch } : item,
      ),
    );
  async function loadUsers(allowedUserIds = []) {
    const missing = allowedUserIds.filter(
      (userId) => !users.some((user) => user.user_id === userId),
    );
    if (
      loadingUsers ||
      (allowedUserIds.length ? !missing.length : users.length)
    )
      return;
    setLoadingUsers(true);
    try {
      const path = missing.length
        ? `/dingtalk/users?user_ids=${encodeURIComponent(missing.join(","))}`
        : "/dingtalk/users";
      const data = await api(path);
      setUsers((current) => {
        const merged = new Map(current.map((user) => [user.user_id, user]));
        (data.items || []).forEach((user) => merged.set(user.user_id, user));
        return [...merged.values()];
      });
      setNotice({
        type: "success",
        text: `已加载 ${data.items?.length || 0} 位钉钉可选人员。`,
      });
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setLoadingUsers(false);
    }
  }
  async function forecast(currentTask) {
    setForecasting(true);
    try {
      const data = await api(`/tasks/${currentTask.id}/approvals/forecast`, {
        method: "POST",
        body: "{}",
      });
      const forecasts = Object.fromEntries(
        (data.groups || []).map((group) => [group.group_id, group]),
      );
      setTask({
        ...currentTask,
        workflow_forecasts: forecasts,
        workflow_actioners: {},
        previews: [],
        status: "prepared",
        updated_at: new Date().toISOString(),
      });
      setActioners({});
      const failures = data.groups?.filter((group) => group.error).length || 0;
      setNotice({
        type: failures ? "error" : "success",
        text: failures
          ? `${failures} 个付款分组未能完成流程检测。`
          : "已完成钉钉审批路径检测。",
      });
    } catch (error) {
      setNotice({ type: "error", text: `流程检测失败：${error.message}` });
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
  async function submit(retry = false) {
    setSubmitOpen(false);
    setProgressOpen(true);
    setBusy(true);
    try {
      const data = await api(
        `/tasks/${task.id}/approvals/${retry ? "retry" : "submit"}`,
        { method: "POST", body: JSON.stringify({ confirm: true }) },
      );
      setTask(await api(`/tasks/${task.id}`));
      await refresh();
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
    } catch (error) {
      setNotice({ type: "error", text: error.message });
    } finally {
      setBusy(false);
    }
  }
  function setNode(groupId, actorKey, userIds) {
    const picked = userIds.map(
      (userId) =>
        users.find((user) => user.user_id === userId) || {
          user_id: userId,
          name: userId,
        },
    );
    setActioners((current) => ({
      ...current,
      [groupId]: { ...(current[groupId] || {}), [actorKey]: picked },
    }));
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
              付款分组与审批人
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
              {result && !groupsDirty && (
                <Forecasts
                  forecasts={task.workflow_forecasts || {}}
                  actioners={actioners}
                  users={users}
                  expanded={expanded}
                  setExpanded={setExpanded}
                  loadingUsers={loadingUsers}
                  loadUsers={loadUsers}
                  setNode={setNode}
                  forecasting={forecasting}
                />
              )}
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
                    disabled={!result || groupsDirty}
                    title={
                      groupsDirty ? "付款分组已变更，请先重新生成附件" : ""
                    }
                    onClick={() => setResultOpen(true)}
                  >
                    <Icon name="eye" />
                    查看结果文件
                  </button>
                  <button
                    className="secondary"
                    onClick={() => {
                      setTask(null);
                      setChoices([]);
                      setActioners({});
                      setResultOpen(false);
                    }}
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
            </div>
          ) : (
            <PreviewPanel
              previews={previews}
              attempts={task.approval_attempts || []}
              progress={task.submission_progress}
              busy={busy}
              liveEnabled={connection?.live_submit_enabled}
              preview={preview}
              openProgress={() => setProgressOpen(true)}
              requestSubmit={() => setSubmitOpen(true)}
              retry={() => submit(true)}
            />
          )}
        </>
      )}
      <ResultModal
        open={resultOpen}
        task={task}
        close={() => setResultOpen(false)}
      />
      <SubmissionProgressModal
        open={progressOpen}
        task={task}
        close={() => setProgressOpen(false)}
      />
      {submitOpen && (
        <ConfirmModal
          count={previews.length}
          close={() => setSubmitOpen(false)}
          confirm={() => submit(false)}
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
function Forecasts({
  forecasts,
  actioners,
  users,
  expanded,
  setExpanded,
  loadingUsers,
  loadUsers,
  setNode,
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
          <p>固定节点只读；手选节点仅允许选择钉钉模板设定范围内的人员。</p>
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
                  {forecast.error ? (
                    <div className="inline-error">{forecast.error}</div>
                  ) : (
                    (forecast.nodes || []).map((node) => (
                      <Node
                        key={node.activity_id}
                        groupId={groupId}
                        node={node}
                        actioners={actioners[groupId] || {}}
                        users={users}
                        loadingUsers={loadingUsers}
                        loadUsers={loadUsers}
                        setNode={setNode}
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
function Node({
  groupId,
  node,
  actioners,
  users,
  loadingUsers,
  loadUsers,
  setNode,
}) {
  const manual = node.kind === "manual";
  const chosen = (actioners[node.actor_key] || []).map((user) => user.user_id);
  const available = node.allowed_user_ids?.length
    ? users.filter((user) => node.allowed_user_ids.includes(user.user_id))
    : users;
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
            {manual ? "手选节点" : "固定节点"}
          </span>
          {manual && (
            <span className={node.required ? "red" : ""}>
              {node.required ? "必选" : "可选"}
            </span>
          )}
          {manual && <span>{node.allowed_multi ? "可多选" : "单选"}</span>}
          {manual && (
            <span className={rangeReady ? "blue" : "red"}>{rangeText}</span>
          )}
        </div>
      </div>
      {manual ? (
        <div className="node-control">
          {loadingUsers ? (
            <span className="muted-line">
              <LoadingIcon size={13} />
              正在加载通讯录…
            </span>
          ) : (
            <div
              className={`select-shell ${node.allowed_multi ? "multiple" : ""}`}
            >
              <select
                multiple={node.allowed_multi}
                value={node.allowed_multi ? chosen : chosen[0] || ""}
                disabled={!rangeReady}
                onFocus={() => loadUsers(node.allowed_user_ids || [])}
                onChange={(event) => {
                  const selected = Array.from(event.target.selectedOptions).map(
                    (option) => option.value,
                  );
                  setNode(groupId, node.actor_key, selected);
                }}
              >
                <option value="">
                  {node.required ? "请选择审批人" : "可不选择"}
                </option>
                {available.map((user) => (
                  <option key={user.user_id} value={user.user_id}>
                    {user.name} ({user.user_id})
                  </option>
                ))}
              </select>
              {!node.allowed_multi && (
                <ChevronDown
                  className="select-chevron"
                  size={16}
                  strokeWidth={1.9}
                />
              )}
            </div>
          )}
          {manual && !node.selection_range_restricted && (
            <div className="inline-error">
              该手选节点尚未设置可选人员范围，请先在钉钉审批模板中配置。
            </div>
          )}
          {node.selection_range_restricted &&
            !node.selection_range_resolvable && (
              <div className="inline-error">
                钉钉返回了无法识别的人员范围，请改为明确指定成员。
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
            <small>由审批模板自动处理</small>
          )}
        </div>
      )}
    </div>
  );
}
function PreviewPanel({
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
  const valid = previews.length && previews.every((item) => item.valid);
  const submitting = attempts.some((item) => item.status === "submitting");
  const canShowProgress = attempts.length > 0;
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
            disabled={busy || !valid || !liveEnabled}
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
        <button className="retry-button" onClick={retry} disabled={busy}>
          {busy && <LoadingIcon />}
          <Icon name="retry" />
          只重试失败项
        </button>
      )}
    </section>
  );
}
function PreviewDetails({ item, attempt }) {
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
          <strong>{item.attachment_name}</strong>
        </div>
        {fields.map((field) => (
          <div
            className={
              typeof field.value === "object" || field.name.includes("明细")
                ? "field-wide"
                : ""
            }
            key={field.name}
          >
            <small>{field.name}</small>
            <strong>{displayFieldValue(field.value)}</strong>
          </div>
        ))}
      </div>
      <div className="node-summary">
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
      </div>
      {attempt && (
        <div className={`attempt ${attempt.status}`}>
          {attempt.status === "submitted" ? (
            `已提交，实例 ID：${attempt.instance_id}`
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
  if (attempt.status === "submitting" && index === current) return "active";
  return index >= 0 && current > index ? "done" : "pending";
}
function SubmissionProgressModal({ open, task, close }) {
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
                <article
                  className={`submission-group ${attempt?.status || "queued"}`}
                  key={group.id}
                >
                  <header>
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
                        "已完成"
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
                  </header>
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
                      {attempt.error && <strong>{attempt.error}</strong>}
                    </footer>
                  )}
                </article>
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
        onClick={(event) => event.stopPropagation()}
      >
        <header className="results-modal-head">
          <div>
            <span className="section-kicker">RESULT CENTER</span>
            <h2>结果文件已生成</h2>
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
              <Icon name="download" />
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
function ConfirmModal({ count, close, confirm }) {
  return (
    <div className="modal-backdrop" onClick={close}>
      <section
        className="confirm-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <span className="modal-icon">
          <Icon name="alert" />
        </span>
        <h2>确认批量发起审批</h2>
        <p>
          将发起 {count}{" "}
          条独立付款审批。提交后会上传对应附件，并再次核对流程节点。
        </p>
        <div className="modal-actions">
          <button className="secondary" onClick={close}>
            返回核对
          </button>
          <button className="primary" onClick={confirm}>
            确认发起
          </button>
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
  const [saving, setSaving] = useState(false);
  const companies = [
    ...new Set([
      ...profiles.map((item) => item.company),
      "深圳再造",
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
  const saveLabel = tab === "companies" ? "保存资料" : "保存人员范围";
  return (
    <section className="page simple-page profiles-page">
      <div className="page-heading">
        <div>
          <span className="section-kicker">PAYMENT PROFILES</span>
          <h1>付款资料</h1>
          <p>
            {tab === "companies"
              ? "维护公司付款主体、供应商和银行资料。"
              : "限制手选审批节点能够选择的钉钉人员。"}
          </p>
        </div>
        <button
          className="primary profile-save"
          disabled={saving || (tab === "scopes" && loadingScopes)}
          onClick={saveAll}
        >
          <Icon name="save" />
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
      ) : loadingScopes ? (
        <div className="section-card scope-loading">
          <span className="spinner" />
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
        <span>{searching ? "搜索中..." : ""}</span>
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
              <RefreshCw size={15} strokeWidth={1.9} />
              重新读取配置
            </button>
            <button
              className="primary"
              disabled={checking || !connection?.configured}
              onClick={() => check(true)}
            >
              <ShieldCheck size={16} strokeWidth={1.9} />
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
