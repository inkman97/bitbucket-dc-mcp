"""MCP server entry point with tool definitions and dispatcher."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import ConfigError, ServerConfig, load_config
from .git_runner import GitError, GitRunner
from .http_client import BitbucketHttpClient, HttpClientError
from .logging_setup import AuditLogger, build_operational_logger
from .validation import (
    ValidationError,
    resolve_repo_path,
    validate_branch_name,
    validate_comment_text,
    validate_commit_message,
    validate_description,
    validate_file_path,
    validate_project_key,
    validate_pull_request_id,
    validate_pull_request_state,
    validate_repo_slug,
    validate_title,
)


class Context:
    """Shared state passed to every tool implementation."""

    def __init__(
            self,
            config: ServerConfig,
            git: GitRunner,
            http: BitbucketHttpClient,
            audit: AuditLogger,
    ) -> None:
        self.config = config
        self.git = git
        self.http = http
        self.audit = audit


def build_tools(config: ServerConfig) -> list[Tool]:
    default_hint = (
        f" Default: {config.default_project}."
        if config.default_project
        else " Required."
    )
    return [
        Tool(
            name="bitbucket_clone_repo",
            description=(
                "[WRITE] Clone a Bitbucket Data Center repository into the "
                "local workspace. If already cloned, fetches and updates "
                "the default branch. Returns the local path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "project_key": {
                        "type": "string",
                        "description": f"Bitbucket project key.{default_hint}",
                    },
                },
                "required": ["repo_slug"],
            },
        ),
        Tool(
            name="bitbucket_create_branch",
            description="[WRITE] Create a new branch in a locally cloned repo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "branch_name": {"type": "string"},
                },
                "required": ["repo_slug", "branch_name"],
            },
        ),
        Tool(
            name="bitbucket_commit_changes",
            description="[WRITE] Stage all changes and create a commit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "commit_message": {"type": "string"},
                },
                "required": ["repo_slug", "commit_message"],
            },
        ),
        Tool(
            name="bitbucket_push_branch",
            description="[WRITE] Push the current branch to origin.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "branch_name": {"type": "string"},
                },
                "required": ["repo_slug", "branch_name"],
            },
        ),
        Tool(
            name="bitbucket_create_pull_request",
            description=(
                "[WRITE] Open a pull request via REST API. Returns the PR URL."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "source_branch": {"type": "string"},
                    "target_branch": {
                        "type": "string",
                        "description": "Target branch. Default: master.",
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug", "source_branch", "title"],
            },
        ),
        Tool(
            name="bitbucket_get_repo_info",
            description="[READ] Retrieve repository metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug"],
            },
        ),
        Tool(
            name="bitbucket_list_branches",
            description="[READ] List branches of a repository (first 100).",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug"],
            },
        ),
        Tool(
            name="bitbucket_get_file_content",
            description=(
                "[READ] Read the raw content of a file from a repository "
                "without cloning it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "file_path": {"type": "string"},
                    "branch": {"type": "string"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug", "file_path"],
            },
        ),
        Tool(
            name="bitbucket_list_files",
            description=(
                "[READ] List files in a repository (or in a subdirectory) "
                "at a given branch, without cloning. Useful to explore "
                "the structure of a repo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional subdirectory. Empty for repo root."
                        ),
                    },
                    "branch": {"type": "string"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug"],
            },
        ),
        Tool(
            name="bitbucket_list_pull_requests",
            description=(
                "[READ] List pull requests of a repository, optionally "
                "filtered by state (OPEN, DECLINED, MERGED, ALL). "
                "Default: OPEN."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "state": {
                        "type": "string",
                        "description": (
                            "OPEN, DECLINED, MERGED, or ALL. Default OPEN."
                        ),
                    },
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug"],
            },
        ),
        Tool(
            name="bitbucket_get_pull_request",
            description=(
                "[READ] Get full details of a pull request by ID, "
                "including title, state, author, branches, reviewers, "
                "approval count, and description."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "pull_request_id": {"type": "integer"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug", "pull_request_id"],
            },
        ),
        Tool(
            name="bitbucket_get_pull_request_diff",
            description=(
                "[READ] Get the diff of a pull request as raw text "
                "(git-style unified diff)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "pull_request_id": {"type": "integer"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug", "pull_request_id"],
            },
        ),
        Tool(
            name="bitbucket_get_pull_request_comments",
            description=(
                "[READ] Get all comments on a pull request, including "
                "the author and creation timestamp of each comment."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "pull_request_id": {"type": "integer"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug", "pull_request_id"],
            },
        ),
        Tool(
            name="bitbucket_add_pull_request_comment",
            description=(
                "[WRITE] Add a top-level comment to a pull request. "
                "Useful for replying to reviewers or adding notes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_slug": {"type": "string"},
                    "pull_request_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "project_key": {"type": "string"},
                },
                "required": ["repo_slug", "pull_request_id", "text"],
            },
        ),
    ]


# ============================================================
# TOOL IMPLEMENTATIONS
# ============================================================

async def tool_clone_repo(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    )
    repo_path = resolve_repo_path(ctx.config.workspace_dir, repo_slug)
    clone_url = f"{ctx.config.base_url}/scm/{project.lower()}/{repo_slug}.git"
    ctx.http._check_url(clone_url)
    auth = ctx.git.auth_header_args()

    if repo_path.exists():
        ctx.git.run(auth + ["fetch", "origin"], cwd=repo_path)
        try:
            head_ref = ctx.git.run(
                ["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_path
            ).strip()
            default_branch = head_ref.split("/")[-1]
        except GitError:
            default_branch = "master"
        ctx.git.run(["checkout", default_branch], cwd=repo_path)
        ctx.git.run(
            auth + ["pull", "origin", default_branch], cwd=repo_path
        )
        return f"Repository updated at {repo_path} (branch: {default_branch})"
    else:
        ctx.git.run(auth + ["clone", clone_url, str(repo_path)])
        return f"Repository cloned at {repo_path}"


async def tool_create_branch(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    branch_name = validate_branch_name(args["branch_name"])
    repo_path = resolve_repo_path(ctx.config.workspace_dir, repo_slug)
    if not repo_path.exists():
        raise ValidationError(
            f"repo '{repo_slug}' is not cloned. "
            f"Call bitbucket_clone_repo first."
        )
    ctx.git.run(["checkout", "-b", branch_name], cwd=repo_path)
    return f"Branch {branch_name} created and checked out at {repo_path}"


async def tool_commit_changes(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    message = validate_commit_message(args["commit_message"])
    repo_path = resolve_repo_path(ctx.config.workspace_dir, repo_slug)
    if not repo_path.exists():
        raise ValidationError(f"repo '{repo_slug}' is not cloned")
    ctx.git.run(["add", "-A"], cwd=repo_path)
    status = ctx.git.run(["status", "--porcelain"], cwd=repo_path)
    if not status.strip():
        return "No changes to commit."
    ctx.git.run(["commit", "-m", message], cwd=repo_path)
    short_sha = ctx.git.run(
        ["rev-parse", "--short", "HEAD"], cwd=repo_path
    ).strip()
    first_line = message.splitlines()[0][:80]
    return f"Commit {short_sha} created: {first_line}"


async def tool_push_branch(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    branch_name = validate_branch_name(args["branch_name"])
    repo_path = resolve_repo_path(ctx.config.workspace_dir, repo_slug)
    if not repo_path.exists():
        raise ValidationError(f"repo '{repo_slug}' is not cloned")
    auth = ctx.git.auth_header_args()
    ctx.git.run(
        auth + ["push", "-u", "origin", branch_name], cwd=repo_path
    )
    return f"Branch {branch_name} pushed to origin."


async def tool_create_pull_request(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    source = validate_branch_name(args["source_branch"])
    target = validate_branch_name(args.get("target_branch", "master"))
    title = validate_title(args["title"])
    description = validate_description(args.get("description"))
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    )
    project_upper = project.upper()

    payload = {
        "title": title,
        "description": description,
        "fromRef": {
            "id": f"refs/heads/{source}",
            "repository": {
                "slug": repo_slug,
                "project": {"key": project_upper},
            },
        },
        "toRef": {
            "id": f"refs/heads/{target}",
            "repository": {
                "slug": repo_slug,
                "project": {"key": project_upper},
            },
        },
    }
    path = (
        f"/rest/api/1.0/projects/{project_upper}/repos/"
        f"{repo_slug}/pull-requests"
    )
    result = await ctx.http.request("POST", path, payload)
    pr_id = result.get("id", "?")
    links = result.get("links", {}).get("self", [])
    pr_url = links[0].get("href", "") if links else ""
    return f"PR #{pr_id} created: {pr_url}"


async def tool_get_repo_info(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()
    path = f"/rest/api/1.0/projects/{project}/repos/{repo_slug}"
    result = await ctx.http.request("GET", path)

    # The base repo endpoint does NOT always include defaultBranch in the
    # response (confirmed against Bitbucket DC 8.x). Fetch it from the
    # dedicated /default-branch endpoint as a second call. If that also
    # fails, fall back to the value (if any) from the base response, then
    # to "N/A" as a last resort.
    default_branch = result.get("defaultBranch") or "N/A"
    try:
        branch_path = (
            f"/rest/api/1.0/projects/{project}/repos/{repo_slug}/default-branch"
        )
        branch_result = await ctx.http.request("GET", branch_path)
        # The endpoint returns a Ref object: {"id": "refs/heads/master",
        # "displayId": "master", "type": "BRANCH"}
        display_id = branch_result.get("displayId")
        if display_id:
            default_branch = display_id
    except Exception:
        # Keep whatever fallback we already have; do not fail the whole
        # tool call just because the secondary lookup failed.
        pass

    return (
        f"Repo: {result.get('name')}\n"
        f"Slug: {result.get('slug')}\n"
        f"Project: {result.get('project', {}).get('key')}\n"
        f"Default branch: {default_branch}\n"
        f"Description: {result.get('description', '')}"
    )


async def tool_list_branches(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()
    path = (
        f"/rest/api/1.0/projects/{project}/repos/"
        f"{repo_slug}/branches?limit=100"
    )
    result = await ctx.http.request("GET", path)
    branches = result.get("values", [])
    lines = [f"Branches of {repo_slug} ({len(branches)} total):"]
    for b in branches[:50]:
        marker = " (default)" if b.get("isDefault") else ""
        lines.append(f"  - {b.get('displayId')}{marker}")
    return "\n".join(lines)


async def tool_get_file_content(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    file_path = validate_file_path(args["file_path"])
    branch = validate_branch_name(args.get("branch", "master"))
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()

    # Bitbucket DC exposes file content via /browse/{path} which returns
    # a paginated JSON object with line-by-line content. We fetch all
    # pages until isLastPage is true, then reassemble the lines.
    lines: list[str] = []
    start = 0
    page_limit = 500
    total_bytes = 0

    while True:
        path = (
            f"/rest/api/1.0/projects/{project}/repos/{repo_slug}"
            f"/browse/{file_path}?at=refs/heads/{branch}"
            f"&start={start}&limit={page_limit}"
        )
        result = await ctx.http.request("GET", path)

        page_lines = result.get("lines", [])
        for line_obj in page_lines:
            text = line_obj.get("text", "")
            lines.append(text)
            total_bytes += len(text.encode("utf-8")) + 1
            if total_bytes > ctx.config.max_file_bytes:
                lines.append(
                    f"[...truncated at {ctx.config.max_file_bytes} bytes]"
                )
                return f"File {file_path}:\n\n" + "\n".join(lines)

        if result.get("isLastPage", True):
            break
        next_start = result.get("nextPageStart")
        if next_start is None or next_start == start:
            break
        start = next_start

    content = "\n".join(lines)
    return f"File {file_path}:\n\n{content}"


async def tool_list_files(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    branch = validate_branch_name(args.get("branch", "master"))
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()
    raw_path = args.get("path", "")
    if raw_path:
        sub_path = validate_file_path(raw_path)
    else:
        sub_path = ""

    files: list[str] = []
    start = 0
    page_limit = 1000
    max_results = 5000

    while True:
        path = (
            f"/rest/api/1.0/projects/{project}/repos/{repo_slug}/files"
        )
        if sub_path:
            path += f"/{sub_path}"
        path += (
            f"?at=refs/heads/{branch}&start={start}&limit={page_limit}"
        )
        result = await ctx.http.request("GET", path)

        page_values = result.get("values", [])
        files.extend(page_values)

        if len(files) >= max_results:
            files = files[:max_results]
            files.append(f"[...truncated at {max_results} entries]")
            break
        if result.get("isLastPage", True):
            break
        next_start = result.get("nextPageStart")
        if next_start is None or next_start == start:
            break
        start = next_start

    location = sub_path if sub_path else "(root)"
    header = (
        f"Files in {repo_slug} at {branch}, path={location} "
        f"({len(files)} entries):"
    )
    body = "\n".join(f"  {f}" for f in files) if files else "  (empty)"
    return f"{header}\n{body}"


async def tool_list_pull_requests(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()
    state = validate_pull_request_state(args.get("state"))

    path = (
        f"/rest/api/1.0/projects/{project}/repos/{repo_slug}/pull-requests"
        f"?state={state}&limit=50&order=NEWEST"
    )
    result = await ctx.http.request("GET", path)
    values = result.get("values", [])
    if not values:
        return f"No pull requests found in {repo_slug} (state={state})."

    lines = [f"Pull requests in {repo_slug} (state={state}):"]
    for pr in values[:50]:
        pr_id = pr.get("id", "?")
        title = pr.get("title", "")[:80]
        author = pr.get("author", {}).get("user", {}).get("name", "?")
        from_branch = (
                pr.get("fromRef", {}).get("displayId")
                or pr.get("fromRef", {}).get("id", "?")
        )
        to_branch = (
                pr.get("toRef", {}).get("displayId")
                or pr.get("toRef", {}).get("id", "?")
        )
        pr_state = pr.get("state", "?")
        lines.append(
            f"  #{pr_id} [{pr_state}] {title} "
            f"({from_branch} -> {to_branch}, by {author})"
        )
    return "\n".join(lines)


async def tool_get_pull_request(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    pr_id = validate_pull_request_id(args["pull_request_id"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()

    path = (
        f"/rest/api/1.0/projects/{project}/repos/{repo_slug}"
        f"/pull-requests/{pr_id}"
    )
    result = await ctx.http.request("GET", path)

    title = result.get("title", "")
    description = result.get("description", "") or "(no description)"
    state = result.get("state", "?")
    author = result.get("author", {}).get("user", {}).get("name", "?")
    from_branch = (
            result.get("fromRef", {}).get("displayId")
            or result.get("fromRef", {}).get("id", "?")
    )
    to_branch = (
            result.get("toRef", {}).get("displayId")
            or result.get("toRef", {}).get("id", "?")
    )
    reviewers = result.get("reviewers", [])
    reviewer_names = [
        r.get("user", {}).get("name", "?") for r in reviewers
    ]
    approvals = sum(1 for r in reviewers if r.get("approved"))
    links = result.get("links", {}).get("self", [])
    pr_url = links[0].get("href", "") if links else ""

    out = [
        f"PR #{pr_id}: {title}",
        f"State: {state}",
        f"Author: {author}",
        f"From: {from_branch} -> To: {to_branch}",
        f"Reviewers: {', '.join(reviewer_names) if reviewer_names else '(none)'}",
        f"Approvals: {approvals}/{len(reviewers)}",
        f"URL: {pr_url}",
        "",
        "Description:",
        description[:2000],
    ]
    return "\n".join(out)


async def tool_get_pull_request_diff(ctx: Context, args: dict) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    pr_id = validate_pull_request_id(args["pull_request_id"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()

    path = (
        f"/rest/api/1.0/projects/{project}/repos/{repo_slug}"
        f"/pull-requests/{pr_id}/diff"
    )
    diff_text = await ctx.http.get_raw_text(path, ctx.config.max_file_bytes)
    return f"Diff for PR #{pr_id}:\n\n{diff_text}"


async def tool_get_pull_request_comments(
        ctx: Context, args: dict
) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    pr_id = validate_pull_request_id(args["pull_request_id"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()

    comments: list[dict] = []
    start = 0
    page_limit = 100
    max_pages = 10
    pages = 0

    while pages < max_pages:
        path = (
            f"/rest/api/1.0/projects/{project}/repos/{repo_slug}"
            f"/pull-requests/{pr_id}/activities"
            f"?start={start}&limit={page_limit}"
        )
        result = await ctx.http.request("GET", path)
        for activity in result.get("values", []):
            if activity.get("action") == "COMMENTED":
                comment = activity.get("comment", {})
                comments.append({
                    "id": comment.get("id"),
                    "author": comment.get("author", {}).get("name", "?"),
                    "text": comment.get("text", ""),
                    "created": activity.get("createdDate"),
                })
        if result.get("isLastPage", True):
            break
        next_start = result.get("nextPageStart")
        if next_start is None or next_start == start:
            break
        start = next_start
        pages += 1

    if not comments:
        return f"No comments on PR #{pr_id}."

    lines = [f"Comments on PR #{pr_id} ({len(comments)} total):"]
    for c in comments:
        text_preview = c["text"][:300].replace("\n", " ")
        lines.append(
            f"  [{c['id']}] {c['author']}: {text_preview}"
        )
    return "\n".join(lines)


async def tool_add_pull_request_comment(
        ctx: Context, args: dict
) -> str:
    repo_slug = validate_repo_slug(args["repo_slug"])
    pr_id = validate_pull_request_id(args["pull_request_id"])
    text = validate_comment_text(args["text"])
    project = validate_project_key(
        args.get("project_key"), default=ctx.config.default_project
    ).upper()

    path = (
        f"/rest/api/1.0/projects/{project}/repos/{repo_slug}"
        f"/pull-requests/{pr_id}/comments"
    )
    payload = {"text": text}
    result = await ctx.http.request("POST", path, payload)
    comment_id = result.get("id", "?")
    return f"Comment #{comment_id} added to PR #{pr_id}."


# ============================================================
# DISPATCHER
# ============================================================

TOOL_IMPLEMENTATIONS = {
    "bitbucket_clone_repo": tool_clone_repo,
    "bitbucket_create_branch": tool_create_branch,
    "bitbucket_commit_changes": tool_commit_changes,
    "bitbucket_push_branch": tool_push_branch,
    "bitbucket_create_pull_request": tool_create_pull_request,
    "bitbucket_get_repo_info": tool_get_repo_info,
    "bitbucket_list_branches": tool_list_branches,
    "bitbucket_get_file_content": tool_get_file_content,
    "bitbucket_list_files": tool_list_files,
    "bitbucket_list_pull_requests": tool_list_pull_requests,
    "bitbucket_get_pull_request": tool_get_pull_request,
    "bitbucket_get_pull_request_diff": tool_get_pull_request_diff,
    "bitbucket_get_pull_request_comments": tool_get_pull_request_comments,
    "bitbucket_add_pull_request_comment": tool_add_pull_request_comment,
}


async def dispatch_tool(
        ctx: Context, name: str, arguments: dict[str, Any]
) -> str:
    """Route the call to the right tool and produce an audit trail."""
    impl = TOOL_IMPLEMENTATIONS.get(name)
    if impl is None:
        ctx.audit.emit(
            name, arguments, "unknown tool",
            outcome="error", error_type="UnknownTool",
        )
        return f"Unknown tool: {name}"
    try:
        result = await impl(ctx, arguments)
        ctx.audit.emit(
            name, arguments, result[:500], outcome="success",
        )
        return result
    except ValidationError as e:
        ctx.audit.emit(
            name, arguments, f"validation error: {e}",
            outcome="rejected", error_type="ValidationError",
        )
        return f"REJECTED: {e}"
    except (GitError, HttpClientError) as e:
        ctx.audit.emit(
            name, arguments, str(e)[:500],
            outcome="error", error_type=type(e).__name__,
        )
        return f"ERROR: {type(e).__name__}: {e}"
    except Exception as e:
        ctx.audit.emit(
            name, arguments, f"{type(e).__name__}: {str(e)[:300]}",
            outcome="error", error_type=type(e).__name__,
        )
        return f"UNEXPECTED ERROR: {type(e).__name__}: {e}"


# ============================================================
# SERVER SETUP
# ============================================================

def build_context(config: ServerConfig) -> Context:
    git = GitRunner(token=config.token, timeout=config.git_timeout)
    http = BitbucketHttpClient(
        base_url=config.base_url,
        token=config.token,
        allowed_hosts=config.allowed_hosts,
        timeout=config.http_timeout,
        agent_id=config.agent_id,
    )
    audit = AuditLogger(config)
    return Context(config=config, git=git, http=http, audit=audit)


async def serve(config: ServerConfig) -> None:
    """Run the MCP server until the client disconnects."""
    ctx = build_context(config)
    log = build_operational_logger(config)
    log.info(f"{config.agent_id} starting")
    log.info(f"  base URL: {config.base_url}")
    log.info(f"  user: {config.username}")
    log.info(
        f"  default project: {config.default_project or '(none)'}"
    )
    log.info(f"  workspace: {config.workspace_dir}")
    log.info(f"  allowed hosts: {sorted(config.allowed_hosts)}")
    log.info(f"  session id: {config.session_id}")
    log.info(f"  audit log: {config.audit_log_path}")
    log.info(
        f"  git timeout: {config.git_timeout}s, "
        f"http timeout: {config.http_timeout}s"
    )

    ctx.audit.emit(
        "_server_start",
        {"base_url": config.base_url},
        "server started",
        outcome="success",
    )

    mcp = Server(config.agent_id)

    @mcp.list_tools()
    async def list_tools() -> list[Tool]:
        return build_tools(config)

    @mcp.call_tool()
    async def call_tool(
            name: str, arguments: dict[str, Any]
    ) -> list[TextContent]:
        log.info(f"tool invocation: {name}")
        result = await dispatch_tool(ctx, name, arguments)
        return [TextContent(type="text", text=result)]

    async with stdio_server() as (read_stream, write_stream):
        await mcp.run(
            read_stream,
            write_stream,
            mcp.create_initialization_options(),
        )


def run() -> None:
    """Entry point for the console script."""
    try:
        config = load_config()
    except ConfigError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        print("server interrupted", file=sys.stderr)
