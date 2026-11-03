// Presentation only: never changes backend submission eligibility or stored consent.
export function nodeCompatibilityView(node, workflow = {}) {
  const compatibility = node.compatibility;
  if (!compatibility) return null;
  const issues = compatibility.issues || [];
  const own = issues.filter(issue => Boolean(issue.activity_id) && issue.activity_id === node.activity_id);
  const global = issues.filter(issue => !issue.activity_id);
  const unknownFailure = compatibility.status === 'incompatible' && !issues.length;
  if (own.length || unknownFailure) return {status:'incompatible',label:'不兼容',issues:own,hint:'此节点存在需要处理的问题，整条流程不能正式提交。'};
  if (compatibility.execution_owner === 'dingtalk' && compatibility.preserves_template && node.actor_type === 'audit' && node.kind !== 'manual') {
    return {status:'delegated',label:'钉钉后续办理',issues:[],hint:'保留原模板办理节点；平台只负责发起，不替办理人操作。整条流程能否提交仍取决于整体校验。'};
  }
  const blocked = workflow.status === 'incompatible' || compatibility.workflow_blocked || global.length || compatibility.status === 'verified';
  return {status:blocked?'verified':'submittable',label:blocked?'节点校验通过':'可提交',issues:[],hint:blocked?'本节点未发现独立校验错误；整条流程仍有阻止项，不代表可以提交。':'整条流程校验通过，正式提交仍需预览和确认。'};
}
