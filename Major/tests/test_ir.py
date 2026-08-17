"""Action IR structural validation — a malformed plan must never construct."""

import pytest
from pydantic import ValidationError

from maestro.ir import Action, Plan


def _plan(actions):
    return Plan.model_validate(
        {"plan_id": "t", "instruction": "test", "actions": actions}
    )


def test_valid_dag_topo_order():
    p = _plan([
        {"action_id": "a1", "verb": "fs.glob", "args": {"root": "~/x"}, "produces": "files"},
        {"action_id": "a2", "verb": "fs.move_batch",
         "args": {"sources": "$files", "dest_dir": "~/y"}, "depends_on": ["a1"]},
    ])
    assert p.topo_order == ["a1", "a2"]


def test_duplicate_action_id_rejected():
    with pytest.raises(ValidationError, match="duplicate action_id"):
        _plan([
            {"action_id": "a1", "verb": "fs.stat", "args": {}},
            {"action_id": "a1", "verb": "fs.stat", "args": {}},
        ])


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError, match="unknown action"):
        _plan([{"action_id": "a1", "verb": "fs.stat", "args": {}, "depends_on": ["a9"]}])


def test_cycle_rejected():
    with pytest.raises(ValidationError, match="cycle"):
        _plan([
            {"action_id": "a1", "verb": "fs.stat", "args": {}, "depends_on": ["a2"]},
            {"action_id": "a2", "verb": "fs.stat", "args": {}, "depends_on": ["a1"]},
        ])


def test_undefined_variable_rejected():
    with pytest.raises(ValidationError, match=r"references undefined \$ghost"):
        _plan([{"action_id": "a1", "verb": "fs.move_batch",
                "args": {"sources": "$ghost", "dest_dir": "~/y"}}])


def test_var_without_dependency_rejected():
    # a2 uses $files but does not depend on its producer -> dataflow bug.
    with pytest.raises(ValidationError, match="does not depend on its producer"):
        _plan([
            {"action_id": "a1", "verb": "fs.glob", "args": {"root": "~/x"}, "produces": "files"},
            {"action_id": "a2", "verb": "fs.move_batch",
             "args": {"sources": "$files", "dest_dir": "~/y"}},
        ])


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Action.model_validate(
            {"action_id": "a1", "verb": "fs.stat", "args": {}, "surprise": True}
        )


def test_var_refs_found_recursively():
    a = Action.model_validate({
        "action_id": "a1", "verb": "fs.move_batch",
        "args": {"sources": ["$one", {"nested": "$two"}], "dest_dir": "~/y"},
    })
    assert a.var_refs() == {"one", "two"}
