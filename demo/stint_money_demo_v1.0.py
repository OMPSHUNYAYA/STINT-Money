import json
import hashlib
from copy import deepcopy


def normalize(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(normalize(obj).encode("utf-8")).hexdigest()


def structural_time(visible_state):
    return len(sorted(visible_state.keys()))


def merge_fragments(*partials):
    merged = {}
    conflicts = []

    for part in partials:
        for k, v in part.items():
            if k.startswith("_"):
                continue

            if k not in merged:
                merged[k] = v
            elif merged[k] != v:
                entry = {
                    "field": k,
                    "left": merged[k],
                    "right": v,
                }
                if entry not in conflicts:
                    conflicts.append(entry)

    if conflicts:
        merged["_conflicts"] = conflicts

    return merged


def policy_action(resolution_state, admissibility_tag):
    if resolution_state == "CONFLICT":
        return "ESCALATE"

    if resolution_state == "ABSTAIN":
        if admissibility_tag == "recovery_only":
            return "REVIEW"
        return "HOLD"

    if resolution_state == "RESOLVED":
        if admissibility_tag == "retail_safe":
            return "COMMIT"
        if admissibility_tag == "treasury_safe":
            return "DUAL_APPROVAL"
        if admissibility_tag == "recovery_only":
            return "REVIEW"
        return "COMMIT"

    return "HOLD"


def release_policy_action(activation_state):
    if activation_state == "BLOCKED":
        return "WAIT"
    if activation_state == "READY":
        return "QUEUE"
    if activation_state == "RELEASED":
        return "RELEASE"
    if activation_state == "FROZEN":
        return "FREEZE"
    return "WAIT"


def supervisory_policy_action(batch_capsules):
    if any(c["resolution_state"] == "CONFLICT" for c in batch_capsules):
        return "ESCALATE"

    if any(c["activation_state"] == "FROZEN" for c in batch_capsules):
        return "FREEZE"

    if any(c["activation_state"] == "BLOCKED" for c in batch_capsules):
        return "WAIT"

    if all(c["activation_state"] == "RELEASED" for c in batch_capsules):
        return "RELEASE"

    if any(c["activation_state"] == "READY" for c in batch_capsules):
        return "QUEUE"

    if any(c["resolution_state"] == "ABSTAIN" for c in batch_capsules):
        return "HOLD"

    return "WAIT"


def derive_authoritative_nets_from_complete_edges(s):
    required_edges = ["A_to_B", "B_to_C", "C_to_A"]
    if not all(k in s for k in required_edges):
        return None

    return {
        "A_net": s["C_to_A"] - s["A_to_B"],
        "B_net": s["A_to_B"] - s["B_to_C"],
        "C_net": s["B_to_C"] - s["C_to_A"],
    }


def add_conflict_once(s, field, left, right):
    entry = {
        "field": field,
        "left": left,
        "right": right,
    }

    if "_conflicts" not in s:
        s["_conflicts"] = [entry]
        return True

    if entry not in s["_conflicts"]:
        s["_conflicts"].append(entry)
        return True

    return False


def resolve(local_state):
    s = deepcopy(local_state)
    accounts = ["A", "B", "C"]

    changed = True
    while changed:
        changed = False

        required_edges = ["A_to_B", "B_to_C", "C_to_A"]
        has_required_edges = all(k in s for k in required_edges)

        required_initials = [f"{acc}_initial" for acc in accounts]
        has_all_initials = all(k in s for k in required_initials)

        authoritative_nets = derive_authoritative_nets_from_complete_edges(s)

        if authoritative_nets is not None:
            for net_key, net_value in authoritative_nets.items():
                if net_key not in s:
                    s[net_key] = net_value
                    changed = True
                elif s[net_key] != net_value:
                    if add_conflict_once(s, net_key, s[net_key], net_value):
                        changed = True

        required_nets = [f"{acc}_net" for acc in accounts]
        has_all_nets = all(k in s for k in required_nets)

        for acc in accounts:
            initial_key = f"{acc}_initial"
            net_key = f"{acc}_net"
            final_key = f"{acc}_final"

            if (
                s.get(initial_key) is not None
                and s.get(net_key) is not None
                and final_key not in s
            ):
                s[final_key] = s[initial_key] + s[net_key]
                changed = True

        required_finals = [f"{acc}_final" for acc in accounts]
        has_all_finals = all(k in s for k in required_finals)

        threshold_met = has_all_initials and has_required_edges and has_all_nets

        if threshold_met and "threshold_met" not in s:
            s["threshold_met"] = True
            changed = True

        if has_required_edges and "edge_total" not in s:
            s["edge_total"] = s["A_to_B"] + s["B_to_C"] + s["C_to_A"]
            changed = True

        if "expected_edge_total" in s and "edge_total" in s and "edge_total_ok" not in s:
            s["edge_total_ok"] = (s["edge_total"] == s["expected_edge_total"])
            changed = True

        if has_all_nets and "net_sum_ok" not in s:
            total_net = sum(s[f"{acc}_net"] for acc in accounts)
            s["net_sum_ok"] = (total_net == 0)
            changed = True

        if threshold_met and has_all_finals and "conservation_ok" not in s:
            initial_total = sum(s[f"{acc}_initial"] for acc in accounts)
            final_total = sum(s[f"{acc}_final"] for acc in accounts)
            s["conservation_ok"] = (initial_total == final_total)
            changed = True

        if s.get("_conflicts"):
            new_resolution_state = "CONFLICT"
        elif threshold_met and has_all_finals and (
            s.get("net_sum_ok") is False
            or s.get("conservation_ok") is False
            or s.get("edge_total_ok") is False
        ):
            new_resolution_state = "CONFLICT"
        elif threshold_met and has_all_finals and (
            s.get("net_sum_ok") is True
            and s.get("conservation_ok") is True
            and (s.get("edge_total_ok") is True or "edge_total_ok" not in s)
        ):
            new_resolution_state = "RESOLVED"
        else:
            new_resolution_state = "ABSTAIN"

        if s.get("resolution_state") != new_resolution_state:
            s["resolution_state"] = new_resolution_state
            changed = True

    visible = {}

    ordered_keys = [
        "batch_id",
        "expected_edge_total",
        "A_initial", "B_initial", "C_initial",
        "A_to_B", "B_to_C", "C_to_A",
        "A_net", "B_net", "C_net",
        "A_final", "B_final", "C_final",
        "threshold_met",
        "edge_total",
        "edge_total_ok",
        "net_sum_ok",
        "conservation_ok",
        "resolution_state",
    ]

    for k in ordered_keys:
        if k in s:
            visible[k] = s[k]

    if "_conflicts" in s:
        visible["conflict_count"] = len(s["_conflicts"])

    s["_visible"] = visible
    s["_mature"] = (s.get("resolution_state") == "RESOLVED")
    s["_structural_time"] = structural_time(visible)
    s["_certificate"] = sha(visible)

    return s


def finality_class(resolution_state, activation_state, depends_on):
    if resolution_state == "ABSTAIN":
        return "LOCAL"

    if resolution_state == "CONFLICT":
        return "BATCH"

    if resolution_state == "RESOLVED" and not depends_on:
        if activation_state in ("READY", "RELEASED", "FROZEN"):
            return "BATCH"
        return "LOCAL"

    if resolution_state == "RESOLVED" and depends_on:
        if activation_state in ("READY", "RELEASED", "FROZEN"):
            return "CHAIN"
        return "BATCH"

    return "LOCAL"


def make_capsule(node_id, resolved_state, admissibility_tag="retail_safe"):
    visible_state = resolved_state["_visible"]

    capsule_basis = {
        "node_id": node_id,
        "visible_state": visible_state,
        "structural_time": resolved_state["_structural_time"],
    }

    capsule = {
        "capsule_id": sha(capsule_basis)[:16],
        "origin_node": node_id,
        "admissibility_tag": admissibility_tag,
        "payload_hash": sha(visible_state),
        "resolution_state": visible_state.get("resolution_state", "UNKNOWN"),
        "structural_time": resolved_state["_structural_time"],
        "certificate": resolved_state["_certificate"],
        "policy_action": policy_action(
            visible_state.get("resolution_state", "UNKNOWN"),
            admissibility_tag,
        ),
        "visible_state": visible_state,
    }

    if resolved_state.get("_conflicts"):
        capsule["conflicts"] = resolved_state["_conflicts"]

    return capsule


def make_canonical_batch_capsule(
    resolved_state,
    batch_id,
    admissibility_tag="retail_safe",
    depends_on=None,
    release_mode="manual",
    release_if=None
):
    if depends_on is None:
        depends_on = []

    if release_if is None:
        release_if = []

    visible_state = resolved_state["_visible"]

    canonical_basis = {
        "visible_state": visible_state,
        "structural_time": resolved_state["_structural_time"],
        "admissibility_tag": admissibility_tag,
        "batch_id": batch_id,
        "depends_on": sorted(depends_on),
        "release_mode": release_mode,
        "release_if": sorted(release_if),
    }

    canonical_capsule = {
        "capsule_id": sha(canonical_basis)[:16],
        "origin_node": "CANONICAL",
        "batch_id": batch_id,
        "depends_on": sorted(depends_on),
        "release_mode": release_mode,
        "release_if": sorted(release_if),
        "admissibility_tag": admissibility_tag,
        "payload_hash": sha(visible_state),
        "resolution_state": visible_state.get("resolution_state", "UNKNOWN"),
        "structural_time": resolved_state["_structural_time"],
        "certificate": resolved_state["_certificate"],
        "policy_action": policy_action(
            visible_state.get("resolution_state", "UNKNOWN"),
            admissibility_tag,
        ),
        "visible_state": visible_state,
    }

    if resolved_state.get("_conflicts"):
        canonical_capsule["conflicts"] = resolved_state["_conflicts"]

    return canonical_capsule


def validate_capsule(capsule):
    errors = []

    required_fields = [
        "capsule_id",
        "origin_node",
        "admissibility_tag",
        "payload_hash",
        "resolution_state",
        "structural_time",
        "certificate",
        "policy_action",
        "visible_state",
    ]

    for field in required_fields:
        if field not in capsule:
            errors.append(f"missing field: {field}")

    if errors:
        return {
            "valid": False,
            "errors": errors,
        }

    expected_payload_hash = sha(capsule["visible_state"])
    if capsule["payload_hash"] != expected_payload_hash:
        errors.append("payload_hash mismatch")

    expected_certificate = sha(capsule["visible_state"])
    if capsule["certificate"] != expected_certificate:
        errors.append("certificate mismatch")

    expected_policy = policy_action(
        capsule["resolution_state"],
        capsule["admissibility_tag"],
    )
    if capsule["policy_action"] != expected_policy:
        errors.append("policy_action mismatch")

    if capsule["origin_node"] == "CANONICAL":
        expected_capsule_id = sha({
            "visible_state": capsule["visible_state"],
            "structural_time": capsule["structural_time"],
            "admissibility_tag": capsule["admissibility_tag"],
            "batch_id": capsule.get("batch_id"),
            "depends_on": sorted(capsule.get("depends_on", [])),
            "release_mode": capsule.get("release_mode", "manual"),
            "release_if": sorted(capsule.get("release_if", [])),
        })[:16]
    else:
        expected_capsule_id = sha({
            "node_id": capsule["origin_node"],
            "visible_state": capsule["visible_state"],
            "structural_time": capsule["structural_time"],
        })[:16]

    if capsule["capsule_id"] != expected_capsule_id:
        errors.append("capsule_id mismatch")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


def reconstruct_from_capsules(*capsules):
    valid_payloads = []
    validation_results = []

    for capsule in capsules:
        result = validate_capsule(capsule)
        validation_results.append({
            "origin_node": capsule.get("origin_node", "UNKNOWN"),
            "valid": result["valid"],
            "errors": result["errors"],
        })

        if result["valid"]:
            payload = {
                k: v
                for k, v in capsule["visible_state"].items()
                if k not in ("resolution_state", "conflict_count")
            }
            valid_payloads.append(payload)

    merged = resolve(merge_fragments(*valid_payloads))
    return merged, validation_results


def dependency_satisfied(batch_capsule, batch_index):
    for dep in batch_capsule.get("depends_on", []):
        if dep not in batch_index:
            return False

        dep_capsule = batch_index[dep]

        if dep_capsule.get("resolution_state") != "RESOLVED":
            return False

        if dep_capsule.get("activation_state") not in ("READY", "RELEASED", "FROZEN"):
            return False

    return True


def release_condition_satisfied(batch_capsule, batch_index):
    release_if = batch_capsule.get("release_if", [])
    if not release_if:
        return True

    for dep in release_if:
        if dep not in batch_index:
            return False
        if batch_index[dep].get("activation_state") != "RELEASED":
            return False

    return True


def derive_activation_state(canonical_capsule, batch_index, freeze_batches=None):
    if freeze_batches is None:
        freeze_batches = set()

    batch_id = canonical_capsule["batch_id"]
    resolution_state = canonical_capsule["resolution_state"]
    release_mode = canonical_capsule.get("release_mode", "manual")

    if resolution_state != "RESOLVED":
        return "BLOCKED"

    if not dependency_satisfied(canonical_capsule, batch_index):
        return "BLOCKED"

    if batch_id in freeze_batches:
        return "FROZEN"

    if release_mode == "auto" and release_condition_satisfied(canonical_capsule, batch_index):
        return "RELEASED"

    return "READY"


def promote_ready_to_released(batch_index, explicit_release_batches=None):
    if explicit_release_batches is None:
        explicit_release_batches = set()

    changed = True
    while changed:
        changed = False

        for batch_id in sorted(batch_index.keys()):
            capsule = batch_index[batch_id]

            if capsule["activation_state"] != "READY":
                continue

            if batch_id not in explicit_release_batches:
                continue

            if not release_condition_satisfied(capsule, batch_index):
                continue

            capsule["activation_state"] = "RELEASED"
            capsule["release_action"] = release_policy_action("RELEASED")
            capsule["finality_class"] = finality_class(
                capsule["resolution_state"],
                capsule["activation_state"],
                capsule.get("depends_on", []),
            )
            changed = True


def attach_activation_and_finality(
    batch_capsules,
    freeze_batches=None,
    explicit_release_batches=None
):
    if freeze_batches is None:
        freeze_batches = set()

    if explicit_release_batches is None:
        explicit_release_batches = set()

    batch_index = {
        capsule["batch_id"]: deepcopy(capsule)
        for capsule in batch_capsules
    }

    first_pass_ids = sorted(batch_index.keys())
    for batch_id in first_pass_ids:
        capsule = batch_index[batch_id]
        capsule["activation_state"] = derive_activation_state(
            capsule,
            batch_index,
            freeze_batches=freeze_batches,
        )
        capsule["release_action"] = release_policy_action(capsule["activation_state"])
        capsule["finality_class"] = finality_class(
            capsule["resolution_state"],
            capsule["activation_state"],
            capsule.get("depends_on", []),
        )

    promote_ready_to_released(
        batch_index,
        explicit_release_batches=explicit_release_batches,
    )

    return [batch_index[k] for k in sorted(batch_index.keys())]


def make_activation_capsule(batch_capsule):
    visible_state = {
        "batch_id": batch_capsule["batch_id"],
        "depends_on": sorted(batch_capsule.get("depends_on", [])),
        "release_mode": batch_capsule.get("release_mode", "manual"),
        "release_if": sorted(batch_capsule.get("release_if", [])),
        "resolution_state": batch_capsule["resolution_state"],
        "activation_state": batch_capsule["activation_state"],
        "release_action": batch_capsule["release_action"],
        "finality_class": batch_capsule["finality_class"],
        "certificate": batch_capsule["certificate"],
    }

    structural_t = structural_time(visible_state)

    capsule_basis = {
        "visible_state": visible_state,
        "structural_time": structural_t,
        "batch_id": batch_capsule["batch_id"],
    }

    return {
        "capsule_id": sha(capsule_basis)[:16],
        "origin_node": "ACTIVATION",
        "batch_id": batch_capsule["batch_id"],
        "payload_hash": sha(visible_state),
        "structural_time": structural_t,
        "certificate": sha(visible_state),
        "visible_state": visible_state,
    }


def make_supervisory_capsule(batch_capsules):
    summaries = []

    for capsule in sorted(batch_capsules, key=lambda x: x["batch_id"]):
        summaries.append({
            "batch_id": capsule["batch_id"],
            "resolution_state": capsule["resolution_state"],
            "activation_state": capsule["activation_state"],
            "finality_class": capsule["finality_class"],
            "certificate": capsule["certificate"],
        })

    if any(c["resolution_state"] == "CONFLICT" for c in batch_capsules):
        super_resolution_state = "CONFLICT"
    elif any(c["resolution_state"] == "ABSTAIN" for c in batch_capsules):
        super_resolution_state = "ABSTAIN"
    else:
        super_resolution_state = "RESOLVED"

    if any(c["activation_state"] == "FROZEN" for c in batch_capsules):
        super_activation_state = "FROZEN"
    elif any(c["activation_state"] == "BLOCKED" for c in batch_capsules):
        super_activation_state = "BLOCKED"
    elif all(c["activation_state"] == "RELEASED" for c in batch_capsules):
        super_activation_state = "RELEASED"
    elif any(c["activation_state"] == "READY" for c in batch_capsules):
        super_activation_state = "READY"
    else:
        super_activation_state = "BLOCKED"

    visible_state = {
        "batch_count": len(batch_capsules),
        "batches": summaries,
        "super_resolution_state": super_resolution_state,
        "super_activation_state": super_activation_state,
        "super_finality_class": "SUPERVISORY",
    }

    structural_t = structural_time(visible_state)

    super_capsule = {
        "capsule_id": sha({
            "visible_state": visible_state,
            "structural_time": structural_t,
            "admissibility_tag": "system_supervisor",
        })[:16],
        "origin_node": "SUPER-CANONICAL",
        "admissibility_tag": "system_supervisor",
        "payload_hash": sha(visible_state),
        "resolution_state": super_resolution_state,
        "activation_state": super_activation_state,
        "finality_class": "SUPERVISORY",
        "structural_time": structural_t,
        "certificate": sha(visible_state),
        "policy_action": supervisory_policy_action(batch_capsules),
        "visible_state": visible_state,
    }

    return super_capsule


def print_result(label, result):
    print("=" * 88)
    print(label)
    print("- visible:", json.dumps(result["_visible"], indent=2, sort_keys=True))
    if result.get("_conflicts"):
        print("- conflicts:", json.dumps(result["_conflicts"], indent=2, sort_keys=True))
    print("- mature:", result["_mature"])
    print("- structural_time:", result["_structural_time"])
    print("- certificate:", result["_certificate"])


def print_capsule(label, capsule):
    print("=" * 88)
    print(label)
    print(json.dumps(capsule, indent=2, sort_keys=True))


def print_validation(label, validation_results):
    print("=" * 88)
    print(label)
    print(json.dumps(validation_results, indent=2, sort_keys=True))


def main():
    print("\nSTINT-Money Demo v1.0")
    print("Money Without Network Dependency")
    print("Dependency-aware structural settlement with separated truth, readiness, release, freeze, and supervision.\n")

    b1_manifest = {"batch_id": "BATCH-1", "expected_edge_total": 60}
    b1_f1 = {"batch_id": "BATCH-1", "A_initial": 100, "B_initial": 200}
    b1_f2 = {"batch_id": "BATCH-1", "C_initial": 300, "A_to_B": 30}
    b1_f3 = {"batch_id": "BATCH-1", "B_to_C": 20}
    b1_f4 = {"batch_id": "BATCH-1", "C_to_A": 10}

    b2_manifest = {"batch_id": "BATCH-2", "expected_edge_total": 60}
    b2_f1 = {"batch_id": "BATCH-2", "A_initial": 500, "B_initial": 600}
    b2_f2 = {"batch_id": "BATCH-2", "C_initial": 700, "A_to_B": 25}
    b2_f3 = {"batch_id": "BATCH-2", "B_to_C": 15}
    b2_f4 = {"batch_id": "BATCH-2", "C_to_A": 20}

    b3_manifest = {"batch_id": "BATCH-3", "expected_edge_total": 60}
    b3_f1 = {"batch_id": "BATCH-3", "A_initial": 1000, "B_initial": 1100}
    b3_f2 = {"batch_id": "BATCH-3", "C_initial": 1200, "A_to_B": 20}
    b3_f3 = {"batch_id": "BATCH-3", "B_to_C": 30}
    b3_f4 = {"batch_id": "BATCH-3", "C_to_A": 10}

    b4_manifest = {"batch_id": "BATCH-4", "expected_edge_total": 60}
    b4_f1 = {"batch_id": "BATCH-4", "A_initial": 200, "B_initial": 300}
    b4_f2 = {"batch_id": "BATCH-4", "C_initial": 400, "A_to_B": 30}
    b4_f3 = {"batch_id": "BATCH-4", "B_to_C": 20}
    b4_f4 = {"batch_id": "BATCH-4", "C_to_A": 50}

    print("SCENARIO 1 - BUILD LOCAL CAPSULES\n")

    b1_capsules = [
        make_capsule("B1-MANIFEST", resolve(b1_manifest)),
        make_capsule("B1-F1", resolve(b1_f1)),
        make_capsule("B1-F2", resolve(b1_f2)),
        make_capsule("B1-F3", resolve(b1_f3)),
        make_capsule("B1-F4", resolve(b1_f4)),
    ]

    b2_capsules = [
        make_capsule("B2-MANIFEST", resolve(b2_manifest)),
        make_capsule("B2-F1", resolve(b2_f1)),
        make_capsule("B2-F2", resolve(b2_f2)),
        make_capsule("B2-F3", resolve(b2_f3)),
        make_capsule("B2-F4", resolve(b2_f4)),
    ]

    b3_capsules = [
        make_capsule("B3-MANIFEST", resolve(b3_manifest)),
        make_capsule("B3-F1", resolve(b3_f1)),
        make_capsule("B3-F2", resolve(b3_f2)),
        make_capsule("B3-F3", resolve(b3_f3)),
        make_capsule("B3-F4", resolve(b3_f4)),
    ]

    b4_capsules = [
        make_capsule("B4-MANIFEST", resolve(b4_manifest)),
        make_capsule("B4-F1", resolve(b4_f1)),
        make_capsule("B4-F2", resolve(b4_f2)),
        make_capsule("B4-F3", resolve(b4_f3)),
        make_capsule("B4-F4", resolve(b4_f4)),
    ]

    print("\nSCENARIO 2 - RECONSTRUCT EACH BATCH\n")

    b1_r, b1_v = reconstruct_from_capsules(*b1_capsules)
    b2_r, b2_v = reconstruct_from_capsules(*b2_capsules)
    b3_r, b3_v = reconstruct_from_capsules(*b3_capsules)
    b4_r, b4_v = reconstruct_from_capsules(*b4_capsules)

    print_result("Batch 1 Reconstruction", b1_r)
    print_validation("Batch 1 Validation", b1_v)

    print_result("Batch 2 Reconstruction", b2_r)
    print_validation("Batch 2 Validation", b2_v)

    print_result("Batch 3 Reconstruction", b3_r)
    print_validation("Batch 3 Validation", b3_v)

    print_result("Batch 4 Reconstruction", b4_r)
    print_validation("Batch 4 Validation", b4_v)

    assert b1_r["_visible"]["resolution_state"] == "RESOLVED"
    assert b2_r["_visible"]["resolution_state"] == "RESOLVED"
    assert b3_r["_visible"]["resolution_state"] == "RESOLVED"
    assert b4_r["_visible"]["resolution_state"] == "CONFLICT"

    print("\nSCENARIO 3 - CREATE CANONICAL BATCH CAPSULES\n")

    b1_canonical = make_canonical_batch_capsule(
        b1_r,
        batch_id="BATCH-1",
        admissibility_tag="retail_safe",
        depends_on=[],
        release_mode="auto",
        release_if=[],
    )

    b2_canonical = make_canonical_batch_capsule(
        b2_r,
        batch_id="BATCH-2",
        admissibility_tag="treasury_safe",
        depends_on=["BATCH-1"],
        release_mode="manual",
        release_if=["BATCH-1"],
    )

    b3_canonical = make_canonical_batch_capsule(
        b3_r,
        batch_id="BATCH-3",
        admissibility_tag="retail_safe",
        depends_on=["BATCH-4"],
        release_mode="manual",
        release_if=["BATCH-4"],
    )

    b4_canonical = make_canonical_batch_capsule(
        b4_r,
        batch_id="BATCH-4",
        admissibility_tag="retail_safe",
        depends_on=[],
        release_mode="auto",
        release_if=[],
    )

    print_capsule("Batch 1 Canonical Capsule", b1_canonical)
    print_capsule("Batch 2 Canonical Capsule", b2_canonical)
    print_capsule("Batch 3 Canonical Capsule", b3_canonical)
    print_capsule("Batch 4 Canonical Capsule", b4_canonical)

    print("\nSCENARIO 4 - ATTACH ACTIVATION AND FINALITY WITHOUT EXPLICIT RELEASE\n")

    batch_capsules_phase_1 = attach_activation_and_finality(
        [b1_canonical, b2_canonical, b3_canonical, b4_canonical],
        freeze_batches=set(),
        explicit_release_batches=set(),
    )

    for capsule in batch_capsules_phase_1:
        print_capsule(f"Phase 1 {capsule['batch_id']}", capsule)

    phase_1_index = {c["batch_id"]: c for c in batch_capsules_phase_1}

    assert phase_1_index["BATCH-1"]["activation_state"] == "RELEASED"
    assert phase_1_index["BATCH-2"]["activation_state"] == "READY"
    assert phase_1_index["BATCH-3"]["activation_state"] == "BLOCKED"
    assert phase_1_index["BATCH-4"]["activation_state"] == "BLOCKED"

    print("\nSCENARIO 5 - EXPLICIT RELEASE OF A READY BATCH\n")

    batch_capsules_phase_2 = attach_activation_and_finality(
        [b1_canonical, b2_canonical, b3_canonical, b4_canonical],
        freeze_batches=set(),
        explicit_release_batches={"BATCH-2"},
    )

    for capsule in batch_capsules_phase_2:
        print_capsule(f"Phase 2 {capsule['batch_id']}", capsule)

    phase_2_index = {c["batch_id"]: c for c in batch_capsules_phase_2}

    assert phase_2_index["BATCH-1"]["activation_state"] == "RELEASED"
    assert phase_2_index["BATCH-2"]["activation_state"] == "RELEASED"
    assert phase_2_index["BATCH-2"]["finality_class"] == "CHAIN"

    print("\nSCENARIO 6 - STRUCTURAL TRUTH PRESERVED DESPITE DEPENDENCY BLOCK\n")

    assert phase_2_index["BATCH-3"]["resolution_state"] == "RESOLVED"
    assert phase_2_index["BATCH-3"]["activation_state"] == "BLOCKED"

    print("PASS: BATCH-3 is structurally correct")
    print("PASS: BATCH-3 remains operationally blocked")
    print("PASS: dependency failure does not erase truth")

    print("\nSCENARIO 7 - FROZEN BATCH WITHOUT TRUTH LOSS\n")

    frozen_batch_capsules = attach_activation_and_finality(
        [b1_canonical, b2_canonical, b3_canonical, b4_canonical],
        freeze_batches={"BATCH-2"},
        explicit_release_batches=set(),
    )

    for capsule in frozen_batch_capsules:
        print_capsule(f"Frozen View {capsule['batch_id']}", capsule)

    frozen_index = {c["batch_id"]: c for c in frozen_batch_capsules}

    assert frozen_index["BATCH-2"]["resolution_state"] == "RESOLVED"
    assert frozen_index["BATCH-2"]["activation_state"] == "FROZEN"

    print("\nSCENARIO 8 - CREATE ACTIVATION CAPSULES\n")

    activation_capsules = [make_activation_capsule(c) for c in batch_capsules_phase_2]
    for capsule in activation_capsules:
        print_capsule(f"Activation Capsule {capsule['batch_id']}", capsule)

    print("\nSCENARIO 9 - SUPERVISORY CAPSULE ACROSS ALL BATCHES\n")

    supervisory_capsule = make_supervisory_capsule(batch_capsules_phase_2)
    print_capsule("Supervisory Capsule", supervisory_capsule)

    assert supervisory_capsule["resolution_state"] == "CONFLICT"
    assert supervisory_capsule["activation_state"] == "BLOCKED"
    assert supervisory_capsule["finality_class"] == "SUPERVISORY"

    print("\nSCENARIO 10 - TAMPER VALIDATION RETAINED\n")

    tampered_b2 = deepcopy(b2_capsules[3])
    tampered_b2["visible_state"]["B_to_C"] = 999

    tampered_validation = validate_capsule(tampered_b2)
    print_capsule("Tampered Batch 2 Capsule", tampered_b2)
    print_validation("Tampered Batch 2 Validation", [{
        "origin_node": tampered_b2.get("origin_node", "UNKNOWN"),
        "valid": tampered_validation["valid"],
        "errors": tampered_validation["errors"],
    }])

    assert tampered_validation["valid"] is False

    print("\n" + "=" * 88)
    print("FINAL RESULT")
    print("PASS: batch truth is derived independently from structure")
    print("PASS: activation is separated into BLOCKED, READY, RELEASED, and FROZEN")
    print("PASS: a batch can be RESOLVED and READY without being RELEASED")
    print("PASS: a batch can be RESOLVED but BLOCKED by dependency failure")
    print("PASS: explicit release is separated from structural readiness")
    print("PASS: chain finality is distinct from batch finality")
    print("PASS: supervisory rollup preserves worst-case state visibility")
    print("PASS: tampering remains detectable and rejectable")
    print("=" * 88)


if __name__ == "__main__":
    main()