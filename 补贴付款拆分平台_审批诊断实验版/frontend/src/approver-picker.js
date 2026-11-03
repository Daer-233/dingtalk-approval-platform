// Editable local candidates never override the immutable DingTalk template range.
export function templateCandidateIds(node) {
  const ids = Array.isArray(node.template_allowed_user_ids) ? node.template_allowed_user_ids
    : node.selection_range_source === "local" ? [] : node.allowed_user_ids || [];
  return ids.length ? [...new Set([...ids, ...node.template_default_user_ids || []])] : [];
}
export function newScopeCandidates(picked, configuredUsers) {
  const known = new Set(configuredUsers.map((user) => user.user_id));
  const additions = new Map();
  for (const user of picked) {
    if (user?.user_id && !known.has(user.user_id)) additions.set(user.user_id, user);
  }
  return [...additions.values()];
}
export function pickerSections({ node, configuredUsers, recent, results, query }) {
  const template = new Set(templateCandidateIds(node));
  const keyword = query.trim().toLowerCase();
  const eligible = (user) => node.selection_range_kind !== "unsupported" && user?.user_id &&
    (!template.size || template.has(user.user_id)) &&
    (!keyword || `${user.name || ""} ${user.user_id}`.toLowerCase().includes(keyword));
  const unique = (users) => [...new Map(users.filter(eligible).map((user) => [user.user_id, user])).values()];
  const visibleConfigured = unique(configuredUsers);
  const displayed = new Set(visibleConfigured.map((user) => user.user_id));
  const visibleRecent = unique(recent).filter((user) => !displayed.has(user.user_id));
  visibleRecent.forEach((user) => displayed.add(user.user_id));
  const matching = unique(results);
  return { visibleConfigured, visibleRecent,
    visibleResults: matching.filter((user) => !displayed.has(user.user_id)), matchingResults: matching.length };
}
