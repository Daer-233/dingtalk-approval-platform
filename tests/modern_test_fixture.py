from app.platform_paths import CHAIN_VERSION

def node(key, prev, manual=False, users=None, role="approver", **changes):
    value = {
        "activity_id": key, "prev_activity_id": prev, "metadata_version": CHAIN_VERSION,
        "name": key, "kind": "manual" if manual else "fixed",
        "activity_type": "target_select" if manual else "target_approval",
        "actor_type": role, "approval_type": "MANUAL", "actioners_complete": True,
        "actor_key": key if manual else "", "required": manual,
        "allowed_multi": False, "allowed_user_ids": ["u1", "u2", "u3"],
        "selection_range_restricted": True, "selection_range_resolvable": True,
        "approval_method": "ONE_BY_ONE", "activation_type": "ONE_BY_ONE",
        "fixed_actioners": [{"user_id": uid, "name": uid} for uid in (users or ["fixed-" + key])],
    }
    value.update(changes)
    return value


def fixture():
    nodes = [node("cc-start", "cc-start", users=["cc"], role="notifier"),
             node("fixed", "cc-start"), node("director", "fixed", True),
             node("manager", "director", True), node("department", "manager", True),
             node("accountant", "department"), node("finance", "accountant"),
             node("cashier", "finance"), node("cc-end", "cashier", users=["cc"], role="notifier")]
    selections = {n["actor_key"]: [{"user_id": "u" + str(i), "name": "user" + str(i)}]
                  for i, n in enumerate([n for n in nodes if n["kind"] == "manual"], 1)}
    return nodes, selections


