const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const platform = process.env.PAYMENT_PLATFORM_ROOT || 'I:/AI_Develop/py_AI/补贴付款拆分平台_局域网版';
const main = path.join(platform, 'frontend/src/main.jsx');
const helper = path.join(platform, 'frontend/src/task-link.js');
const record = path.join(root, 'data/payments/runtime/frontend-patch.json');
const sha = text => crypto.createHash('sha256').update(text).digest('hex');
const helperSource = `// Explicit task links take precedence over remembered workspace state.
export function linkedTaskId(search) {
  const params = new URLSearchParams(search || "");
  if (!params.has("task_id")) return undefined;
  const value = params.get("task_id");
  return /^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.test(value || "") ? value : null;
}
`;
const old = `    let savedId = "";
    try { savedId = localStorage.getItem("business-platform-active-task-v1") || ""; } catch { /* Storage may be disabled. */ }
    if (savedId) api("/tasks/" + encodeURIComponent(savedId)).then((saved) => {
      if (active) setTask((current) => current || saved);
    }).catch(() => { try { localStorage.removeItem("business-platform-active-task-v1"); } catch { /* No persistence. */ } });`;
const replacement = `    const requestedId = linkedTaskId(window.location.search);
    let savedId = requestedId || "";
    if (requestedId === null) {
      setNotice({ type: "error", text: "任务链接无效，请从助手重新打开。" });
      return () => { active = false; };
    }
    if (requestedId === undefined) {
      try { savedId = localStorage.getItem("business-platform-active-task-v1") || ""; } catch { /* Storage may be disabled. */ }
    }
    if (savedId) api("/tasks/" + encodeURIComponent(savedId)).then((saved) => {
      if (!active) return;
      setTask((current) => requestedId ? saved : current || saved);
      if (requestedId) setPage("workspace");
    }).catch(() => {
      if (!active) return;
      if (requestedId) setNotice({ type: "error", text: "无法打开指定任务，请核对链接或稍后重试。" });
      else { try { localStorage.removeItem("business-platform-active-task-v1"); } catch { /* No persistence. */ } }
    });`;
function apply() {
  let source = fs.readFileSync(main, 'utf8').replace(/\r\n/g, '\n');
  if (source.includes('const requestedId = linkedTaskId(window.location.search)')) {
    console.log('Payment task links already installed.'); return;
  }
  if (source.split(old).length !== 2 || !source.startsWith('import React')) throw new Error('Frontend source changed; refusing broad replacement.');
  if (fs.existsSync(helper)) throw new Error('task-link.js already exists; inspect before replacing.');
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const backup = path.join(root, 'backups/payment-platform', stamp);
  fs.mkdirSync(backup, {recursive:true}); fs.mkdirSync(path.dirname(record), {recursive:true});
  fs.writeFileSync(path.join(backup,'main.before.jsx'),source,'utf8');
  let changed = source.replace(old,replacement);
  changed='import { linkedTaskId } from "./task-link.js";\n'+changed;
  fs.writeFileSync(helper,helperSource,'utf8'); fs.writeFileSync(main,changed,'utf8');
  fs.writeFileSync(record,JSON.stringify({main,helper,backup,before:sha(source),after:sha(changed),helper_sha:sha(helperSource)},null,2));
  console.log('Installed task-specific navigation; backup recorded.');
}
function rollback() {
  const data=JSON.parse(fs.readFileSync(record,'utf8'));
  if(sha(fs.readFileSync(main,'utf8').replace(/\r\n/g,'\n'))!==data.after || sha(fs.readFileSync(helper,'utf8'))!==data.helper_sha)
    throw new Error('Frontend changed after installation; restore manually from the recorded backup.');
  fs.writeFileSync(main,fs.readFileSync(path.join(data.backup,'main.before.jsx'),'utf8'),'utf8');
  fs.unlinkSync(helper); console.log('Restored prior task navigation; rebuild the frontend.');
}
if(require.main===module) {
  if(process.argv.includes('--apply')) apply();
  else if(process.argv.includes('--rollback')) rollback();
  else {
    const source=fs.readFileSync(main,'utf8').replace(/\r\n/g,'\n');
    console.log(JSON.stringify({patchable:source.includes(old),installed:source.includes('const requestedId = linkedTaskId(window.location.search)')}));
  }
}
module.exports={helperSource};
