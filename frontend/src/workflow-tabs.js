export function restoredWorkflowTab(task) {
  if (task?.previews?.length || task?.approval_attempts?.length || ["previewed", "submitting", "submitted", "partial_failed"].includes(task?.status)) return "preview";
  return task?.result ? "nodes" : "groups";
}
export function forecastActioners(forecasts) {
  return Object.fromEntries(Object.entries(forecasts).map(([id, group]) => [id, group.node_actioners || {}]));
}
