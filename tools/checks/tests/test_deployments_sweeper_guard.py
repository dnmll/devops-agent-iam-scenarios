"""Guard: long-lived deployments must be invisible to the iamscn-* sweeper.

`tools/probes/sweeper.py` deletes IAM roles *and* Agent Spaces whose name starts
with `iamscn-` once they are older than six hours, and `.github/workflows/sweeper.yml`
runs it on a schedule. Everything under `deployments/` is long-lived and applied by
hand, so a name (or tag) the sweeper can select would silently destroy a real
deployment overnight.

These are drift guards, not style checks: they pin the properties
`deployments/live-agentspace/README.md` calls out as "will break a live deployment
if missed" — no `iamscn-` naming, no `iamscn:` tags, no permissions boundary, own
state key — so a future edit that reintroduces one fails in CI instead of in
production.

Pure static analysis of the HCL: no AWS calls, no terraform binary.
"""

from __future__ import annotations

import hcl2
import pytest

from tools.checks import REPO_ROOT

DEPLOYMENTS_DIR = REPO_ROOT / "deployments"
SWEEPER = REPO_ROOT / "tools" / "probes" / "sweeper.py"
SWEPT_PREFIX = "iamscn-"
SWEPT_TAG_NAMESPACE = "iamscn:"

# Arguments whose value ends up as an AWS resource *name* (what the sweeper
# matches on). `name_prefix` is terraform-level, the rest are provider arguments.
NAME_ARGUMENTS = frozenset(
    {"name", "name_prefix", "policy_name", "role_name", "alias", "target_key_id", "log_group_name"}
)

# Variables the sweeper's two selectors make dangerous: role naming and the
# Agent Space name.
MUST_REJECT_SWEPT_PREFIX = frozenset({"name_prefix", "agentspace_name"})


def _unquote(value):
    """python-hcl2 >= 4 keeps the surrounding quotes on string literals."""
    if isinstance(value, list):
        return [_unquote(v) for v in value]
    if isinstance(value, str) and len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _one(value):
    """hcl2 wraps some attribute values in a single-element list."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _load(tf):
    with tf.open() as fh:
        return hcl2.load(fh)


def _tf_files() -> list:
    return sorted(DEPLOYMENTS_DIR.rglob("*.tf"))


def _deployment_dirs() -> list:
    return sorted(p for p in DEPLOYMENTS_DIR.iterdir() if p.is_dir())


def _walk(node):
    """Yield every (key, value) pair in a parsed-HCL tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield _unquote(key), value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _variables(d) -> dict:
    out = {}
    for tf in sorted(d.glob("*.tf")):
        for block in _load(tf).get("variable", []):
            for name, spec in block.items():
                out[_unquote(name)] = spec
    return out


def test_deployments_dir_has_terraform():
    assert _tf_files(), "expected at least one terraform file under deployments/"


def test_sweeper_still_selects_on_the_iamscn_prefix():
    """If the sweeper's selector changes, the guards below need rewriting."""
    src = SWEEPER.read_text()
    assert src.count(f'startswith("{SWEPT_PREFIX}")') == 2, (
        "sweeper.py no longer selects IAM roles and Agent Spaces by the literal "
        f"{SWEPT_PREFIX!r} prefix; the deployment naming guards in this file must be "
        "updated to match the new selector."
    )


@pytest.mark.parametrize("tf", _tf_files(), ids=lambda p: p.name)
def test_no_name_argument_can_produce_a_swept_name(tf):
    """No resource in a deployment may be named into the swept namespace.

    Checked on the parsed tree, so prose in comments/descriptions that *mentions*
    the prefix (the warnings themselves do) is not a false positive.
    """
    for key, value in _walk(_load(tf)):
        if key not in NAME_ARGUMENTS:
            continue
        value = _unquote(_one(value))
        if not isinstance(value, str):
            # A block body, not an argument — e.g. `variable "name_prefix" { … }`,
            # whose description deliberately explains the prefix it must avoid.
            continue
        rendered = value
        assert SWEPT_PREFIX not in rendered, (
            f"{tf.relative_to(REPO_ROOT)}: {key} = {rendered!r} lands in the swept "
            f"{SWEPT_PREFIX}* namespace; sweeper.py would delete it after six hours"
        )


@pytest.mark.parametrize("tf", _tf_files(), ids=lambda p: p.name)
def test_no_iamscn_tag_namespace(tf):
    """live-validate's destroy sweep is tag-scoped on iamscn:* — stay out of it."""
    for key, _ in _walk(_load(tf)):
        assert not str(key).startswith(SWEPT_TAG_NAMESPACE), (
            f"{tf.relative_to(REPO_ROOT)}: tag {key!r} is in the {SWEPT_TAG_NAMESPACE}* "
            f"namespace that tag-scoped cleanup selects on"
        )


@pytest.mark.parametrize("tf", _tf_files(), ids=lambda p: p.name)
def test_no_permissions_boundary(tf):
    """iamscn-boundary caps policies *under test*: it allows aidevops:* plus narrow
    IAM reads. A real Agent Space role capped by it applies cleanly and then fails
    every investigation, because AIDevOpsAgentAccessPolicy needs broad describe/read
    access across many services."""
    for key, value in _walk(_load(tf)):
        assert key != "permissions_boundary", (
            f"{tf.relative_to(REPO_ROOT)}: permissions_boundary = {_one(value)!r}; deployments "
            f"must attach no boundary (see deployments/live-agentspace/README.md constraint 2)"
        )


@pytest.mark.parametrize("d", _deployment_dirs(), ids=lambda p: p.name)
def test_name_prefix_default_is_not_swept(d):
    """`terraform apply` with no -var must still produce sweeper-safe names."""
    spec = _variables(d).get("name_prefix")
    assert spec is not None, f"{d.name}: expected a name_prefix variable guarding resource naming"
    default = _unquote(_one(spec.get("default")))
    assert default, "name_prefix must have a default so an apply cannot omit it"
    assert not str(default).startswith(SWEPT_PREFIX), (
        f"{d.name}: name_prefix default {default!r} is in the swept namespace"
    )


@pytest.mark.parametrize("d", _deployment_dirs(), ids=lambda p: p.name)
def test_swept_prefix_is_rejected_by_variable_validation(d):
    """A default is not enough: an operator passing -var name_prefix=iamscn- (or an
    Agent Space name in that namespace) must be refused at plan time."""
    guarded = set()
    for name, spec in _variables(d).items():
        validations = spec.get("validation", [])
        if not isinstance(validations, list):
            validations = [validations]
        for v in validations:
            condition = str(v.get("condition", ""))
            if SWEPT_PREFIX in condition and "startswith" in condition:
                guarded.add(name)
    missing = sorted(MUST_REJECT_SWEPT_PREFIX - guarded)
    assert not missing, (
        f"{d.name}: no validation rejecting {SWEPT_PREFIX!r} on {missing}; sweeper.py "
        f"deletes aged {SWEPT_PREFIX}* IAM roles AND Agent Spaces"
    )


@pytest.mark.parametrize("d", _deployment_dirs(), ids=lambda p: p.name)
def test_deployment_has_its_own_state_key(d):
    """Shared state is how a `terraform destroy` elsewhere reaches a long-lived
    deployment. Each deployment owns deployments/<name>/terraform.tfstate."""
    keys = []
    for tf in sorted(d.glob("*.tf")):
        for block in _load(tf).get("terraform", []):
            backends = block.get("backend", [])
            if not isinstance(backends, list):
                backends = [backends]
            for backend in backends:
                for cfg in backend.values():
                    if "key" in cfg:
                        keys.append(_unquote(_one(cfg["key"])))
    assert keys, f"{d.name}: no S3 backend key declared"
    expected = f"deployments/{d.name}/terraform.tfstate"
    for key in keys:
        assert key == expected, (
            f"{d.name}: backend key {key!r} must be {expected!r} — separate from "
            f"terraform/bootstrap/ and from every scenario"
        )
