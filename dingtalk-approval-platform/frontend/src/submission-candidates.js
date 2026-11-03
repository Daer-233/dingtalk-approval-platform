// Match the backend eligibility rules; confirmed/uncertain approvals are never resubmitted.
export function eligibleApprovalPreviews(previews = [], attempts = [], failedOnly = false) {
  const statusByGroup = new Map(attempts.map((item) => [item.group_id, item.status]));
  return previews.filter((item) => {
    const status = statusByGroup.get(item.group_id);
    if (status === "submitted" || status === "uncertain") return false;
    return !failedOnly || status === "failed";
  });
}
