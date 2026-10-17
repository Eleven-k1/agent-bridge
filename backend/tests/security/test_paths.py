"""M3 路径边界测试：项目内外判定、逃逸、Windows 特殊名称、拒写区。"""

import pytest

from agent_bridge.security.errors import PathDenied
from agent_bridge.security.paths import PathClass, PathPolicy


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".multi-agent").mkdir()
    (root / ".git").mkdir()
    (root / ".env").write_text("SECRET=x\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    return root


@pytest.fixture
def policy(workspace):
    return PathPolicy(workspace_root=workspace)


def test_file_inside_workspace_is_in_project(policy: PathPolicy, workspace) -> None:
    assert policy.classify(workspace / "src" / "main.py").path_class is PathClass.IN_PROJECT


def test_workspace_root_itself_is_in_project(policy: PathPolicy, workspace) -> None:
    assert policy.classify(workspace).path_class is PathClass.IN_PROJECT


def test_parent_traversal_is_outside_project(policy: PathPolicy, workspace) -> None:
    result = policy.classify(workspace / "src" / ".." / ".." / "outside.txt")
    assert result.path_class is PathClass.OUTSIDE_PROJECT


def test_sibling_prefix_directory_is_not_inside(policy: PathPolicy, workspace) -> None:
    sibling = workspace.parent / (workspace.name + "-notes")
    assert policy.classify(sibling / "a.txt").path_class is PathClass.OUTSIDE_PROJECT


def test_absolute_outside_path_is_outside(policy: PathPolicy, workspace) -> None:
    assert policy.classify(workspace.parent / "outside.txt").path_class is PathClass.OUTSIDE_PROJECT


def test_audit_area_is_deny_write_for_tools(policy: PathPolicy, workspace) -> None:
    result = policy.classify(workspace / ".multi-agent" / "audit.sqlite3")
    assert result.path_class is PathClass.INTERNAL_DENY_WRITE


def test_git_internals_are_deny_write_for_tools(policy: PathPolicy, workspace) -> None:
    result = policy.classify(workspace / ".git" / "hooks" / "pre-commit")
    assert result.path_class is PathClass.INTERNAL_DENY_WRITE


def test_sensitive_project_file_is_flagged(policy: PathPolicy, workspace) -> None:
    assert policy.classify(workspace / ".env").sensitive is True


def test_sensitive_variants_are_flagged(policy: PathPolicy, workspace) -> None:
    for name in (".env.local", ".env.production", "id_rsa", "server.key", "store.pfx"):
        assert policy.classify(workspace / name).sensitive is True, name


def test_ordinary_source_file_is_not_sensitive(policy: PathPolicy, workspace) -> None:
    assert policy.classify(workspace / "src" / "main.py").sensitive is False


def test_alternate_data_stream_is_rejected(policy: PathPolicy, workspace) -> None:
    with pytest.raises(PathDenied, match="stream"):
        policy.classify(str(workspace / "src" / "main.py") + ":hidden:$DATA")


def test_reserved_device_name_is_rejected(policy: PathPolicy, workspace) -> None:
    for name in ("CON", "nul", "COM1", "LPT9.txt"):
        with pytest.raises(PathDenied, match="reserved"):
            policy.classify(workspace / name)


def test_short_name_component_is_rejected(policy: PathPolicy, workspace) -> None:
    with pytest.raises(PathDenied, match="short name"):
        policy.classify(workspace / "PROGRA~1" / "a.txt")


def test_write_inside_project_is_allowed(policy: PathPolicy, workspace) -> None:
    assert policy.require_writable(workspace / "src" / "new.py") is not None


def test_write_to_audit_area_is_denied(policy: PathPolicy, workspace) -> None:
    with pytest.raises(PathDenied, match="internal"):
        policy.require_writable(workspace / ".multi-agent" / "x.json")


def test_write_outside_project_is_denied_without_approval(policy: PathPolicy, workspace) -> None:
    with pytest.raises(PathDenied, match="outside"):
        policy.require_writable(workspace.parent / "outside.txt")


def test_hard_denied_paths_have_no_approval_route(policy: PathPolicy) -> None:
    result = policy.classify("C:/Users/someone/.ssh/id_ed25519")
    assert result.path_class is PathClass.HARD_DENIED
    assert result.approvable is False


def test_outside_project_is_approvable(policy: PathPolicy, workspace) -> None:
    assert policy.classify(workspace.parent / "outside.txt").approvable is True


def test_classification_reports_the_full_resolved_path(policy: PathPolicy, workspace) -> None:
    result = policy.classify(workspace / "src" / ".." / "src" / "main.py")
    assert result.resolved_path == (workspace / "src" / "main.py").resolve()
