"""The safety layer: path policy, deterministic scorer, taint, budget, audit.

These are the tests the project's central claim rests on. If any of them can be
made to pass by accident, the claim is weaker than it looks — so several assert
the *negative* case explicitly (a lying planner cannot lower risk; an unknown
check fails rather than being skipped; editing the log is detectable).
"""

from __future__ import annotations

import os
import sqlite3

import pytest

import maestro.executor  # noqa: F401
from maestro import registry
from maestro.ir import Action, Budget, Plan, Risk, Trust
from maestro.safety import PathPolicy, PathVerdict, score_action, score_plan
from maestro.safety.audit import AuditLog
from maestro.safety.budget import BudgetExceeded, BudgetGuard
from maestro.safety.consent import Approval, ConsentGate, ConsentRequest, token_matches
from maestro.safety.taint import analyze


def plan_of(*actions, instruction="test") -> Plan:
    return Plan(plan_id="p_t", instruction=instruction, actions=list(actions))


def act(aid, verb, args=None, **kw) -> Action:
    return Action(action_id=aid, verb=verb, args=args or {}, **kw)


# =========================================================================== #
# path policy
# =========================================================================== #


def test_allowlisted_path_is_allowed(policy, tmp_path):
    assert policy.check(tmp_path / "a.txt") is PathVerdict.ALLOWED


def test_path_outside_allowlist_is_outside(policy):
    assert policy.check("/somewhere/else/file.txt") is PathVerdict.OUTSIDE


def test_denylisted_directory_is_denied(policy):
    assert policy.check("~/.ssh/id_rsa") is PathVerdict.DENIED
    assert policy.check("~/.aws/credentials") is PathVerdict.DENIED


def test_traversal_does_not_escape_the_allowlist(policy, tmp_path):
    """`workspace/../../.ssh/x` must be resolved BEFORE matching.

    This is the bug the whole canonicalise-first ordering exists to prevent: a
    naive prefix check on the raw string says "starts with the allowed root",
    and the agent then reads the user's private key.
    """
    escape = tmp_path / ".." / ".." / ".ssh" / "id_rsa"
    assert policy.check(escape) is PathVerdict.DENIED


def test_traversal_out_of_the_sandbox_is_not_allowed(policy, tmp_path):
    outside = tmp_path / "sub" / ".." / ".." / "elsewhere" / "x.txt"
    assert policy.check(outside) is not PathVerdict.ALLOWED


@pytest.mark.skipif(os.name == "nt", reason="symlinks need admin rights on Windows")
def test_symlink_into_a_denied_dir_is_denied(policy, tmp_path):
    """A symlink inside an allowed directory pointing at a denied one is denied.

    docs/06 §2 names this as the case to write a test for in week 5, "because it
    is the bug you would otherwise ship".
    """
    target = tmp_path / "pretend_ssh"
    target.mkdir()
    link = tmp_path / "innocent"
    link.symlink_to(target)
    policy.deny_dirs.append(str(target))
    policy.__post_init__()
    assert policy.check(link / "id_rsa") is PathVerdict.DENIED


def test_denied_filename_pattern_anywhere(policy, tmp_path):
    for name in ("secret.pem", "id_rsa", "vault.kdbx", "my_wallet.dat", ".env"):
        assert policy.check(tmp_path / name) is PathVerdict.DENIED, name


def test_dot_ssh_as_any_component_is_denied(policy, tmp_path):
    assert policy.check(tmp_path / "Documents" / ".ssh" / "backup") is PathVerdict.DENIED


def test_windows_system_paths_denied_on_every_platform():
    """One list, both platforms — so the cross-platform differential test in
    docs/02 §7 compares identical policy rather than two policies."""
    p = PathPolicy()
    assert p.check("C:/Windows/System32/config") is PathVerdict.DENIED
    assert p.check("C:/Program Files/App") is PathVerdict.DENIED


def test_unparseable_path_fails_closed(policy):
    assert policy.check("bad\x00path") is PathVerdict.DENIED


def test_policy_explains_itself(policy):
    assert "denylist" in policy.explain("~/.ssh/id_rsa")


# =========================================================================== #
# deterministic risk scorer
# =========================================================================== #


def test_read_only_verb_in_workspace_is_r0(policy, tmp_path):
    v = score_action(act("a1", "fs.glob", {"root": str(tmp_path)}), policy)
    assert v.risk is Risk.R0 and not v.blocked


def test_move_is_r2_and_requires_consent(policy, tmp_path):
    p = plan_of(act("a1", "fs.move_batch",
                    {"sources": [str(tmp_path / "a.pdf")], "dest_dir": str(tmp_path)}))
    verdict = score_plan(p, policy)
    assert verdict.risk is Risk.R2
    assert verdict.gate == "confirm"


def test_unknown_verb_is_blocked_not_warned(policy):
    v = score_action(act("a1", "sys.exec_shell", {"cmd": "rm -rf /"}), policy)
    assert v.blocked
    assert "closed registry" in " ".join(v.reasons)


def test_hard_blocked_verb_has_no_override(policy, tmp_path):
    for verb, args in [("fs.delete_permanent", {"paths": [str(tmp_path / "x")]}),
                       ("email.send", {"to": ["a@b.c"], "subject": "s", "body": "b"})]:
        v = score_action(act("a1", verb, args), policy)
        assert v.blocked, verb
        assert "no override" in " ".join(v.reasons)


def test_denylisted_path_blocks_even_a_read(policy):
    v = score_action(act("a1", "fs.read_text", {"path": "~/.ssh/id_rsa"}), policy)
    assert v.blocked


def test_path_outside_workspace_escalates_to_r2(policy):
    v = score_action(act("a1", "fs.mkdir", {"path": "/elsewhere/newdir"}), policy)
    assert v.risk is Risk.R2
    assert any("outside" in r for r in v.reasons)


def test_bulk_operation_escalates(policy, tmp_path):
    action = act("a1", "fs.copy_batch",
                 {"sources": [str(tmp_path / f"{i}.txt") for i in range(3)],
                  "dest_dir": str(tmp_path / "out")})
    low = score_action(action, policy, estimated_files=2)
    high = score_action(action, policy, estimated_files=500)
    assert low.risk is Risk.R1
    assert high.risk is Risk.R2
    assert any("500 files" in r for r in high.reasons)


def test_irreversible_without_undo_is_r3(policy):
    v = score_action(act("a1", "browser.click",
                         {"url": "https://example.com", "selector": "#go"}), policy)
    assert v.risk is Risk.R3
    assert any("irreversible" in r for r in v.reasons)


def test_invalid_args_are_blocked_not_coerced(policy):
    v = score_action(act("a1", "sys.set_volume", {"level": 900}), policy)
    assert v.blocked


# --- the three invariants -------------------------------------------------- #


def test_scoring_is_deterministic(policy, tmp_path):
    """NFR-07: the same plan yields the same verdict, every time."""
    p = plan_of(act("a1", "fs.move_batch",
                    {"sources": [str(tmp_path / "a")], "dest_dir": str(tmp_path)}))
    verdicts = {(score_plan(p, policy).risk, score_plan(p, policy).gate)
                for _ in range(50)}
    assert len(verdicts) == 1


def test_risk_hint_is_recorded_but_ignored(policy, tmp_path):
    """A lying planner cannot downgrade risk.

    The planner claims R0 on a move. The scorer must still say R2, and the lie
    must be recorded so the hint-agreement metric can count it.
    """
    action = act("a1", "fs.move_batch",
                 {"sources": [str(tmp_path / "a.pdf")], "dest_dir": str(tmp_path)},
                 risk_hint=Risk.R0)
    v = score_action(action, policy)
    assert v.risk is Risk.R2          # the scorer decides
    assert v.hint is Risk.R0          # the claim is kept
    assert v.hint_agrees is False     # and counted as disagreement


def test_a_lying_hint_cannot_unblock(policy):
    action = act("a1", "fs.delete_permanent", {"paths": ["~/Documents/x"]},
                 risk_hint=Risk.R0)
    assert score_action(action, policy).blocked


def test_escalation_is_monotonic(policy):
    """Every rule may only raise risk. An action that trips several rules ends
    at the maximum, never at the last one evaluated."""
    action = act("a1", "fs.write_text", {"path": "/outside/x.txt", "content": "hi"})
    v = score_action(action, policy, estimated_files=1000)
    assert v.risk >= Risk.R2


def test_plan_risk_is_the_max_over_actions(policy, tmp_path):
    p = plan_of(
        act("a1", "fs.glob", {"root": str(tmp_path)}),
        act("a2", "fs.trash", {"paths": [str(tmp_path / "a")]}),
    )
    assert score_plan(p, policy).risk is Risk.R2


def test_gate_mapping(policy, tmp_path):
    assert score_plan(plan_of(act("a1", "fs.glob", {"root": str(tmp_path)})),
                      policy).gate == "auto"
    assert score_plan(plan_of(act("a1", "fs.mkdir", {"path": str(tmp_path / "d")})),
                      policy).gate == "auto"
    assert score_plan(plan_of(act("a1", "fs.trash", {"paths": [str(tmp_path / "a")]})),
                      policy).gate == "confirm"
    assert score_plan(plan_of(act("a1", "browser.click",
                                  {"url": "https://x.com", "selector": "#a"})),
                      policy).gate == "typed_confirm"


# =========================================================================== #
# taint tracking
# =========================================================================== #


def test_file_contents_are_untrusted(policy, tmp_path):
    p = plan_of(
        act("a1", "fs.read_text", {"path": str(tmp_path / "doc.txt")},
            produces="content"),
        act("a2", "fs.write_text", {"path": "$content", "content": "x"},
            depends_on=["a1"]),
    )
    reports = analyze(p)
    assert reports["a2"].trust is Trust.T2
    assert "path" in reports["a2"].tainted_args


def test_globbed_paths_are_derived_not_untrusted(policy, tmp_path):
    p = plan_of(
        act("a1", "fs.glob", {"root": str(tmp_path)}, produces="files"),
        act("a2", "fs.move_batch", {"sources": "$files", "dest_dir": str(tmp_path)},
            depends_on=["a1"]),
    )
    assert analyze(p)["a2"].trust is Trust.T1


def test_untrusted_data_reaching_a_sensitive_arg_escalates(policy, tmp_path):
    """Control #7 in docs/06 §6: laundering document text into a destination."""
    p = plan_of(
        act("a1", "fs.read_text", {"path": str(tmp_path / "doc.txt")},
            produces="content"),
        act("a2", "fs.move_batch", {"sources": [str(tmp_path / "a")],
                                    "dest_dir": "$content"}, depends_on=["a1"]),
    )
    verdict = score_plan(p, policy)
    a2 = verdict.action_verdict("a2")
    assert a2.risk >= Risk.R2
    assert any("UNTRUSTED" in r for r in a2.reasons)


def test_taint_is_stamped_onto_the_plan(policy, tmp_path):
    p = plan_of(
        act("a1", "fs.read_text", {"path": str(tmp_path / "d.txt")},
            produces="content"),
        act("a2", "fs.write_text", {"path": "$content"}, depends_on=["a1"]),
    )
    score_plan(p, policy)
    assert p.action("a2").trust is Trust.T2   # survives into the audit log


def test_unknown_producer_verbs_are_assumed_untrusted():
    from maestro.safety.taint import producer_trust

    assert producer_trust("some.future.verb") is Trust.T2


# =========================================================================== #
# budget guard (threat T7)
# =========================================================================== #


def test_static_budget_check_catches_scale():
    guard = BudgetGuard(Budget(max_steps=5, max_files_touched=10))
    assert guard.check_static(3, 5) is None
    assert "steps" in guard.check_static(9, 5)
    assert "files" in guard.check_static(3, 99)


def test_dynamic_budget_raises():
    guard = BudgetGuard(Budget(max_steps=2, max_files_touched=100))
    guard.tick(1)
    guard.tick(1)
    with pytest.raises(BudgetExceeded, match="step budget"):
        guard.tick(1)


def test_file_budget_raises():
    guard = BudgetGuard(Budget(max_steps=10, max_files_touched=5))
    with pytest.raises(BudgetExceeded, match="file budget"):
        guard.tick(50)


# =========================================================================== #
# consent gate
# =========================================================================== #


def _request(gate_risk: Risk, files: int = 3) -> ConsentRequest:
    from maestro.executor.base import EffectManifest
    from maestro.safety.scorer import ActionVerdict, PlanVerdict

    plan = plan_of(act("a1", "fs.glob", {"root": "~/Downloads"}))
    verdict = PlanVerdict("p_t", [ActionVerdict("a1", "fs.glob", gate_risk)])
    return ConsentRequest(plan, verdict,
                          [EffectManifest(summary="x", files_touched=files)])


def test_low_risk_never_prompts():
    """Anti-habituation is a design requirement (docs/06 §4): a system that
    prompts constantly trains the user to click through."""
    asked = []
    gate = ConsentGate(ask=lambda r: asked.append(r) or Approval(True))
    assert gate.decide(_request(Risk.R0)).approved
    assert gate.decide(_request(Risk.R1)).approved
    assert asked == []


def test_medium_risk_prompts():
    asked = []
    gate = ConsentGate(ask=lambda r: asked.append(r) or Approval(True, "click"))
    assert gate.decide(_request(Risk.R2)).approved
    assert len(asked) == 1


def test_no_consent_channel_means_no_execution():
    assert not ConsentGate(ask=None).decide(_request(Risk.R2)).approved


def test_a_click_cannot_satisfy_a_typed_confirmation():
    """The UI does not get to decide that an R3 gate was met by a click."""
    gate = ConsentGate(ask=lambda r: Approval(True, "click"))
    answer = gate.decide(_request(Risk.R3))
    assert not answer.approved
    assert "typed" in answer.note


def test_typed_confirmation_satisfies_r3():
    gate = ConsentGate(ask=lambda r: Approval(True, "typed"))
    assert gate.decide(_request(Risk.R3)).approved


def test_typed_token_mentions_the_real_file_count():
    assert _request(Risk.R3, files=47).token == "CONFIRM 47 FILES"


def test_token_matching_is_forgiving_about_whitespace_and_case():
    assert token_matches("  confirm 47 files ", "CONFIRM 47 FILES")
    assert not token_matches("confirm 46 files", "CONFIRM 47 FILES")


def test_remembering_applies_only_to_the_same_plan_shape():
    """"Approve & remember" must not silently approve a different plan."""
    calls = []
    gate = ConsentGate(ask=lambda r: calls.append(r) or
                       Approval(True, "click", remember=True))
    req = _request(Risk.R2)
    gate.decide(req)
    gate.decide(req)                      # remembered, no second prompt
    assert len(calls) == 1

    other = plan_of(act("a1", "fs.trash", {"paths": ["~/Downloads/x"]}))
    from maestro.executor.base import EffectManifest
    from maestro.safety.scorer import ActionVerdict, PlanVerdict

    different = ConsentRequest(
        other, PlanVerdict("p_o", [ActionVerdict("a1", "fs.trash", Risk.R2)]),
        [EffectManifest(summary="y")])
    gate.decide(different)
    assert len(calls) == 2                # a different shape prompts again


def test_blocked_plans_are_refused_without_asking():
    asked = []
    gate = ConsentGate(ask=lambda r: asked.append(r) or Approval(True))
    answer = gate.decide(_request(Risk.BLOCKED))
    assert not answer.approved and asked == []


# =========================================================================== #
# audit log
# =========================================================================== #


def test_audit_chain_verifies(audit):
    audit.append("PROPOSED", plan_id="p1", detail="move files")
    audit.append("APPROVED", plan_id="p1")
    audit.append("EXECUTED", plan_id="p1", action_id="a1", verb="fs.move_batch")
    assert audit.verify()
    assert audit.count() == 3


def test_audit_rejects_unknown_events(audit):
    with pytest.raises(ValueError, match="unknown audit event"):
        audit.append("MADE_UP")


def test_audit_tampering_is_detected(audit, tmp_path):
    """Editing a historical row breaks verification of every row after it."""
    audit.append("PROPOSED", plan_id="p1", detail="innocent")
    audit.append("EXECUTED", plan_id="p1", verb="fs.trash")
    audit.close()

    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.execute("UPDATE audit_log SET detail = 'rewritten' WHERE seq = 1")
    conn.commit()
    conn.close()

    reopened = AuditLog(tmp_path / "audit.db")
    ok, bad_seq, message = reopened.verify_detailed()
    assert not ok
    assert bad_seq == 1
    assert "edited" in message


def test_audit_deletion_is_detected(audit, tmp_path):
    for i in range(3):
        audit.append("EXECUTED", plan_id="p1", action_id=f"a{i}")
    audit.close()
    conn = sqlite3.connect(tmp_path / "audit.db")
    conn.execute("DELETE FROM audit_log WHERE seq = 2")
    conn.commit()
    conn.close()
    assert not AuditLog(tmp_path / "audit.db").verify()


def test_audit_records_args_and_risk(audit):
    audit.append("EXECUTED", plan_id="p1", action_id="a1", verb="fs.move_batch",
                 args={"dest_dir": "~/Documents"}, risk="R2")
    row = audit.rows()[0]
    assert row.risk == "R2" and "dest_dir" in row.args_json
    assert audit.verify()


# =========================================================================== #
# registry
# =========================================================================== #


def test_hard_blocked_verbs_are_not_advertised_to_the_planner():
    plannable = registry.plannable_verbs()
    for verb in registry.hard_blocked_verbs():
        assert verb not in plannable
    assert "fs.delete_permanent" in registry.known_verbs()


def test_every_registered_verb_has_an_executor_or_is_blocked():
    from maestro.executor.base import has_executor

    for verb in registry.known_verbs():
        spec = registry.get(verb)
        assert has_executor(verb) or spec.hard_blocked, verb


def test_unknown_verb_raises_registry_error():
    with pytest.raises(registry.RegistryError):
        registry.get("fs.nope")


def test_validate_args_rejects_wrong_types():
    with pytest.raises(registry.RegistryError):
        registry.validate_args("fs.glob", {"root": 42})
