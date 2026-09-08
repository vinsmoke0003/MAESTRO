"""The Action IR: construction IS validation (docs/02 §3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from maestro.ir import Action, Budget, Plan, Risk, Trust, hash_instruction
from maestro.ir.model import normalize_path_str


def plan_of(*actions: dict, **kw) -> Plan:
    return Plan(plan_id="p_test", instruction="test", actions=list(actions), **kw)


def a(aid: str, verb: str = "fs.glob", **kw) -> dict:
    return {"action_id": aid, "verb": verb, **kw}


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #


def test_minimal_plan_is_valid():
    p = plan_of(a("a1"))
    assert p.topo_order == ["a1"]
    assert p.instruction_hash.startswith("sha256:")
    assert p.created_at  # stamped automatically


def test_action_id_must_match_pattern():
    with pytest.raises(ValidationError):
        Action(action_id="first", verb="fs.glob")


def test_duplicate_action_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate action_id"):
        plan_of(a("a1"), a("a1"))


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError, match="unknown action"):
        plan_of(a("a1", depends_on=["a9"]))


def test_self_dependency_rejected():
    with pytest.raises(ValidationError, match="depends on itself"):
        plan_of(a("a1", depends_on=["a1"]))


def test_cycle_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        plan_of(a("a1", depends_on=["a2"]), a("a2", depends_on=["a1"]))


def test_empty_plan_rejected():
    with pytest.raises(ValidationError):
        Plan(plan_id="p", instruction="x", actions=[])


def test_plan_over_step_budget_rejected():
    actions = [a(f"a{i}") for i in range(1, 8)]
    with pytest.raises(ValidationError, match="budget allows"):
        plan_of(*actions, budget=Budget(max_steps=5))


# --------------------------------------------------------------------------- #
# dataflow — the part that makes `$var` inspectable
# --------------------------------------------------------------------------- #


def test_var_must_be_produced_somewhere():
    with pytest.raises(ValidationError, match=r"undefined \$missing"):
        plan_of(a("a1", verb="fs.trash", args={"paths": "$missing"}))


def test_var_requires_a_dependency_on_its_producer():
    """Producing a variable is not enough — you must depend on the producer.

    Without this the DAG order does not guarantee the value exists at runtime,
    and dataflow stops being statically checkable.
    """
    with pytest.raises(ValidationError, match="does not depend on its producer"):
        plan_of(
            a("a1", produces="files"),
            a("a2", verb="fs.trash", args={"paths": "$files"}),  # no depends_on
        )


def test_var_via_transitive_dependency_is_allowed():
    p = plan_of(
        a("a1", produces="files"),
        a("a2", depends_on=["a1"]),
        a("a3", verb="fs.trash", args={"paths": "$files"}, depends_on=["a2"]),
    )
    assert p.topo_order.index("a1") < p.topo_order.index("a3")


def test_variable_produced_twice_rejected():
    with pytest.raises(ValidationError, match="produced twice"):
        plan_of(a("a1", produces="x"), a("a2", produces="x"))


def test_produces_must_be_an_identifier():
    with pytest.raises(ValidationError, match="bare identifier"):
        plan_of(a("a1", produces="not a name"))


def test_var_refs_finds_nested_references():
    action = Action(action_id="a1", verb="fs.move_batch",
                    args={"sources": ["$one", "literal"], "nested": {"k": "$two"}})
    assert action.var_refs() == {"one", "two"}


def test_extra_fields_are_forbidden():
    """`extra="forbid"` is what stops a planner smuggling an unvalidated field
    past the schema."""
    with pytest.raises(ValidationError):
        Action(action_id="a1", verb="fs.glob", sneaky=True)


# --------------------------------------------------------------------------- #
# canonicalisation — the basis of Plan-Exact-Match
# --------------------------------------------------------------------------- #


def test_canonical_ignores_rationale_and_hints():
    p1 = plan_of(a("a1", args={"root": "~/Downloads"}, rationale="one",
                   risk_hint=Risk.R0))
    p2 = plan_of(a("a1", args={"root": "~/Downloads"}, rationale="totally different",
                   risk_hint=Risk.R3))
    assert p1.canonical() == p2.canonical()
    assert p1.fingerprint() == p2.fingerprint()


def test_canonical_normalises_path_separators():
    p1 = plan_of(a("a1", args={"root": "~/Downloads/"}))
    p2 = plan_of(a("a1", args={"root": "~\\Downloads"}))
    assert p1.canonical() == p2.canonical()


def test_canonical_is_order_independent():
    p1 = plan_of(a("a1", args={"root": "~/A"}), a("a2", args={"root": "~/B"}))
    p2 = plan_of(a("a1", args={"root": "~/B"}), a("a2", args={"root": "~/A"}))
    assert p1.canonical() == p2.canonical()


def test_canonical_distinguishes_different_args():
    p1 = plan_of(a("a1", args={"root": "~/Downloads"}))
    p2 = plan_of(a("a1", args={"root": "~/Documents"}))
    assert p1.canonical() != p2.canonical()


def test_normalize_path_str_collapses_home():
    from pathlib import Path

    home = str(Path.home()).replace("\\", "/")
    assert normalize_path_str(f"{home}/Downloads") == "~/Downloads"


# --------------------------------------------------------------------------- #
# provenance and trust
# --------------------------------------------------------------------------- #


def test_instruction_hash_binds_the_exact_input():
    assert hash_instruction("move files") != hash_instruction("move  files")
    assert hash_instruction(" move files ") == hash_instruction("move files")


def test_plan_hash_is_derived_from_the_instruction_we_set():
    p = plan_of(a("a1"))
    assert p.instruction_hash == hash_instruction(p.instruction)


def test_risk_ordering_is_meaningful():
    assert Risk.R0 < Risk.R1 < Risk.R2 < Risk.R3 < Risk.BLOCKED
    assert max(Risk.R1, Risk.R3) is Risk.R3


def test_blocked_sits_above_r3():
    """Monotonic escalation depends on nothing being able to outrank BLOCKED."""
    assert Risk.BLOCKED > Risk.R3


def test_trust_defaults_to_derived():
    assert Action(action_id="a1", verb="fs.glob").trust is Trust.T1
    assert Trust.T2 > Trust.T1 > Trust.T0


def test_verb_sequence_follows_topological_order():
    p = plan_of(
        a("a2", verb="fs.mkdir", depends_on=["a1"]),
        a("a1", verb="fs.glob"),
    )
    assert p.verb_sequence() == ["fs.glob", "fs.mkdir"]
