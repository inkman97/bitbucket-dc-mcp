"""Validation is the primary defense against prompt injection.

These tests must be comprehensive. If a test fails, assume the server
is NOT safe to run until the regression is fixed.
"""

from pathlib import Path

import pytest

from bitbucket_dc_mcp.validation import (
    ValidationError,
    resolve_repo_path,
    validate_branch_name,
    validate_commit_message,
    validate_description,
    validate_file_path,
    validate_project_key,
    validate_repo_slug,
    validate_title,
)


class TestRepoSlug:
    def test_accepts_valid_slug(self):
        assert validate_repo_slug("my-repo") == "my-repo"
        assert validate_repo_slug("repo_123") == "repo_123"
        assert validate_repo_slug("a.b.c") == "a.b.c"
        assert validate_repo_slug("nova-masterdata-editor-commons") == (
            "nova-masterdata-editor-commons"
        )

    def test_strips_whitespace(self):
        assert validate_repo_slug("  my-repo  ") == "my-repo"

    def test_rejects_non_string(self):
        with pytest.raises(ValidationError):
            validate_repo_slug(None)
        with pytest.raises(ValidationError):
            validate_repo_slug(42)
        with pytest.raises(ValidationError):
            validate_repo_slug(["my-repo"])

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_repo_slug("")
        with pytest.raises(ValidationError):
            validate_repo_slug("   ")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValidationError):
            validate_repo_slug("../etc/passwd")
        with pytest.raises(ValidationError):
            validate_repo_slug("..")

    def test_rejects_slashes(self):
        with pytest.raises(ValidationError):
            validate_repo_slug("foo/bar")
        with pytest.raises(ValidationError):
            validate_repo_slug("foo\\bar")

    def test_rejects_leading_special(self):
        with pytest.raises(ValidationError):
            validate_repo_slug("-rf")
        with pytest.raises(ValidationError):
            validate_repo_slug(".hidden")
        with pytest.raises(ValidationError):
            validate_repo_slug("_private")

    def test_rejects_control_characters(self):
        with pytest.raises(ValidationError):
            validate_repo_slug("my\x00repo")
        with pytest.raises(ValidationError):
            validate_repo_slug("my\nrepo")

    def test_rejects_shell_metacharacters(self):
        with pytest.raises(ValidationError):
            validate_repo_slug("repo;rm")
        with pytest.raises(ValidationError):
            validate_repo_slug("repo|pipe")
        with pytest.raises(ValidationError):
            validate_repo_slug("repo$var")
        with pytest.raises(ValidationError):
            validate_repo_slug("repo`cmd`")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            validate_repo_slug("a" * 201)


class TestBranchName:
    def test_accepts_valid_branch(self):
        assert validate_branch_name("master") == "master"
        assert validate_branch_name("feature/NOVA-123") == "feature/NOVA-123"
        assert validate_branch_name("bugfix/fix-123") == "bugfix/fix-123"
        assert validate_branch_name("release-v1.2.3") == "release-v1.2.3"

    def test_rejects_dotdot(self):
        with pytest.raises(ValidationError):
            validate_branch_name("feature/../evil")
        with pytest.raises(ValidationError):
            validate_branch_name("..")

    def test_rejects_leading_slash(self):
        with pytest.raises(ValidationError):
            validate_branch_name("/absolute")

    def test_rejects_trailing_slash(self):
        with pytest.raises(ValidationError):
            validate_branch_name("feature/")

    def test_rejects_leading_dash(self):
        # This is critical: prevents --upload-pack injection
        with pytest.raises(ValidationError):
            validate_branch_name("--upload-pack=evil")

    def test_rejects_spaces(self):
        with pytest.raises(ValidationError):
            validate_branch_name("feature branch")


class TestProjectKey:
    def test_accepts_valid_project_key(self):
        assert validate_project_key("NOVA") == "NOVA"
        assert validate_project_key("my_project") == "my_project"
        assert validate_project_key("PROJ-1") == "PROJ-1"

    def test_uses_default_when_empty(self):
        assert validate_project_key(None, default="DEFAULT") == "DEFAULT"
        assert validate_project_key("", default="DEFAULT") == "DEFAULT"

    def test_rejects_empty_without_default(self):
        with pytest.raises(ValidationError):
            validate_project_key(None, default="")
        with pytest.raises(ValidationError):
            validate_project_key("", default="")

    def test_rejects_special_chars(self):
        with pytest.raises(ValidationError):
            validate_project_key("PROJ/OTHER")
        with pytest.raises(ValidationError):
            validate_project_key("PROJ;rm")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            validate_project_key("A" * 65)


class TestFilePath:
    def test_accepts_relative_path(self):
        assert validate_file_path("src/main.py") == "src/main.py"
        assert validate_file_path("README.md") == "README.md"
        assert validate_file_path("a/b/c/d.txt") == "a/b/c/d.txt"

    def test_normalizes_backslashes(self):
        assert validate_file_path("src\\main.py") == "src/main.py"

    def test_rejects_absolute(self):
        with pytest.raises(ValidationError):
            validate_file_path("/etc/passwd")

    def test_rejects_dotdot(self):
        with pytest.raises(ValidationError):
            validate_file_path("../secrets.txt")
        with pytest.raises(ValidationError):
            validate_file_path("src/../../../etc/passwd")
        with pytest.raises(ValidationError):
            validate_file_path("a/../b")

    def test_accepts_dot_in_filename(self):
        # A literal dot in a filename is fine, only .. is dangerous
        assert validate_file_path("file.name.with.dots.txt") == (
            "file.name.with.dots.txt"
        )

    def test_rejects_control_characters(self):
        with pytest.raises(ValidationError):
            validate_file_path("src/\x00file")
        with pytest.raises(ValidationError):
            validate_file_path("src/file\n")

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_file_path("")


class TestCommitMessage:
    def test_accepts_valid(self):
        assert validate_commit_message("Fix bug") == "Fix bug"

    def test_accepts_multiline(self):
        msg = "Subject\n\nBody text"
        assert validate_commit_message(msg) == msg

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_commit_message("")
        with pytest.raises(ValidationError):
            validate_commit_message("   ")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            validate_commit_message("x" * 5001)

    def test_strips_control_chars(self):
        result = validate_commit_message("clean\x00dirty")
        assert "\x00" not in result


class TestTitle:
    def test_accepts_valid(self):
        assert validate_title("My PR") == "My PR"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            validate_title("")

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            validate_title("x" * 256)


class TestDescription:
    def test_none_becomes_empty(self):
        assert validate_description(None) == ""

    def test_empty_is_fine(self):
        assert validate_description("") == ""

    def test_accepts_long_but_bounded(self):
        assert validate_description("x" * 32768) == "x" * 32768

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            validate_description("x" * 32769)


class TestResolveRepoPath:
    def test_stays_within_workspace(self, tmp_path: Path):
        result = resolve_repo_path(tmp_path, "my-repo")
        assert result == (tmp_path / "my-repo").resolve()

    def test_rejects_escape_via_absolute(self, tmp_path: Path):
        # Even if the slug somehow bypasses validation, resolve_repo_path
        # must catch an escape attempt.
        with pytest.raises(ValidationError):
            # Simulate a post-validation slug that tries to escape
            resolve_repo_path(tmp_path, "../outside")
