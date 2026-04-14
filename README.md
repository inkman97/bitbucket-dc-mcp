# bitbucket-dc-mcp

A hardened [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes Bitbucket Data Center operations to AI coding
assistants like Claude Code, Claude Desktop, and GitHub Copilot.

> **On-premise only.** This server targets Bitbucket **Data Center**
> (self-hosted), not Bitbucket Cloud. If you run `bitbucket.org`, use a
> Bitbucket Cloud MCP server instead.

## Why this exists

Most AI coding assistant integrations assume SaaS deployments: Jira
Cloud, Bitbucket Cloud, GitHub.com. But a large portion of enterprise
development happens behind corporate firewalls, on self-hosted Atlassian
Data Center, where cloud connectors cannot reach.

This project exists to fill that gap. It is a small, auditable, locally
runnable MCP server that an AI assistant can call to interact with an
on-premise Bitbucket Data Center instance, with a security posture that
is suitable for enterprise environments.

## What it does

Exposes seventeen tools via the MCP protocol, split into read and write
operations. Write tools are tagged `[WRITE]` in their description so
the MCP client's human approval UI clearly distinguishes them from
read-only tools.

### Read tools

| Tool | Description |
|---|---|
| `bitbucket_get_repo_info` | Fetch repository metadata (name, default branch, description) |
| `bitbucket_list_branches` | List branches of a repository |
| `bitbucket_list_files` | List files in a repository or subdirectory without cloning |
| `bitbucket_get_file_content` | Read a file from the remote without cloning |
| `bitbucket_list_pull_requests` | List pull requests by state (OPEN, DECLINED, MERGED, ALL) |
| `bitbucket_get_pull_request` | Get full details of a pull request by ID |
| `bitbucket_get_pull_request_diff` | Get the git-style unified diff of a pull request |
| `bitbucket_get_pull_request_comments` | Get all comments on a pull request |

### Write tools

| Tool | Description |
|---|---|
| `bitbucket_clone_repo` | Clone a repository into the local workspace |
| `bitbucket_create_branch` | Create a branch in a locally cloned repo |
| `bitbucket_write_file` | Write or overwrite a file in a locally cloned repo |
| `bitbucket_edit_file` | Replace a unique occurrence of a string in a file |
| `bitbucket_apply_patch` | Apply a unified diff to a locally cloned repo |
| `bitbucket_commit_changes` | Stage all changes and commit |
| `bitbucket_push_branch` | Push a branch to origin |
| `bitbucket_create_pull_request` | Open a pull request via REST API |
| `bitbucket_add_pull_request_comment` | Add a top-level comment to a pull request |

The typical end-to-end workflow uses the write tools in this order:
clone → branch → write/edit/apply_patch → commit → push → PR.

### Local file editing

The `bitbucket_write_file`, `bitbucket_edit_file`, and
`bitbucket_apply_patch` tools operate on the **locally cloned** working
tree in the configured workspace, not on the remote. This keeps the
server's write surface contained to a sandboxed directory that the user
can inspect at any time with `git diff` before committing.

Choose the tool that best fits the change:

- **`bitbucket_write_file`** creates a new file or replaces a file
  entirely. Best for generated files or rewriting small files from
  scratch. Creates parent directories as needed.
- **`bitbucket_edit_file`** replaces a single **unique** occurrence of
  an exact string with another. Fails loudly if the old string is not
  found or is found more than once. Best for surgical edits where the
  surrounding context is known and unambiguous.
- **`bitbucket_apply_patch`** applies a unified diff via `git apply`.
  Best for multi-file coherent changes or when a diff is more compact
  than a sequence of individual edits.

All three tools validate paths against directory traversal, enforce the
server's `BITBUCKET_MAX_FILE_BYTES` limit, and require the repo to
already be cloned via `bitbucket_clone_repo`.

### Deliberately omitted

`merge_pull_request` is **not** exposed by this server, even though the
underlying REST endpoint exists. Merging a PR is the irreversible
operation that moves AI-generated code into the main branch, and the
human review on the PR is the primary checkpoint of the entire
workflow. Exposing a merge tool would let an AI assistant complete the
full clone → branch → commit → push → PR → merge cycle without any
human ever reading the code. See [SECURITY.md](SECURITY.md) for the
full rationale.

## Security posture

This server is designed to run with enterprise security requirements in
mind. See [SECURITY.md](SECURITY.md) for the full threat model.
Highlights:

- **No hardcoded credentials.** All secrets are read from environment
  variables, which in production should be sourced from a secret store
  (Credential Manager, Azure Key Vault, HashiCorp Vault, etc.).
- **Strict input validation.** Every tool argument is checked against
  an allowlist regex before being used in git, filesystem, or HTTP
  operations. Path traversal, shell metacharacters, and git option
  injection are all blocked. Write tools validate that target file
  paths stay inside the cloned repo directory.
- **SSRF protection.** Only HTTPS URLs to hosts in a configurable
  allowlist are allowed. Redirects are disabled. When the allowlist is
  set explicitly, the base URL host is not silently added to it — an
  explicit allowlist is respected exactly.
- **Workspace confinement.** All repo paths and file-edit paths are
  resolved and checked to stay within the configured workspace
  directory.
- **Timeout enforcement.** Every subprocess and HTTP call has a
  configurable timeout; no operation can block indefinitely.
- **Token never persisted.** The Bitbucket token is passed to git only
  via ephemeral `http.extraheader` arguments, never written to
  `.git/config` or URLs.
- **Credential helper bypass.** Every git invocation passes
  `-c credential.helper=` to prevent the subprocess from consulting
  the user's credential manager, which can hang indefinitely in
  non-interactive environments.
- **LFS filter bypass by default.** See the LFS section below for
  the full rationale and how to opt in.
- **Structured audit log.** Every tool invocation emits a JSON record
  with `timestamp`, `agent_id`, `session_id`, `tool_invoked`,
  `parameters_used`, `response_summary`, `user_id`, `outcome`, and
  `error_type`, suitable for forwarding to a SIEM.
- **Automatic secret redaction** in both operational and audit logs.
- **No merge tool** to preserve the human review gate on pull requests.

## Installation

```
pip install bitbucket-dc-mcp
```

Requires Python 3.10 or newer and `git` available on PATH.

## Configuration

Configuration is entirely via environment variables. None of them are
committed anywhere by the server itself.

| Variable | Required | Description |
|---|---|---|
| `BITBUCKET_BASE_URL` | yes | Base URL of your Bitbucket DC instance (must be `https`) |
| `BITBUCKET_TOKEN` | yes | HTTP Access Token (Bearer) |
| `BITBUCKET_USERNAME` | yes | Username associated with the token |
| `BITBUCKET_DEFAULT_PROJECT` | no | Default project key for tool calls |
| `BITBUCKET_WORKSPACE` | no | Local directory for cloned repos. Default `~/mcp-workspace` |
| `BITBUCKET_ALLOWED_HOSTS` | no | Comma-separated host allowlist. Defaults to host of `BITBUCKET_BASE_URL` |
| `BITBUCKET_GIT_TIMEOUT` | no | Seconds. Default 300 |
| `BITBUCKET_HTTP_TIMEOUT` | no | Seconds. Default 30 |
| `BITBUCKET_MAX_FILE_BYTES` | no | Max bytes returned by `get_file_content`, written by `write_file`, or applied by `apply_patch`. Default 1 MiB |
| `BITBUCKET_LFS_MODE` | no | LFS handling: `disabled` (default), `enabled`, or `auto`. See below |
| `BITBUCKET_AUDIT_LOG_PATH` | no | Path to audit log. Default `{workspace}/audit.log` |
| `BITBUCKET_SESSION_ID` | no | Session identifier for log correlation. Auto-generated if unset |

### LFS handling

Git LFS support is controlled by `BITBUCKET_LFS_MODE`:

- **`disabled`** (default): every git invocation bypasses LFS filters
  via `-c filter.lfs.required=false -c filter.lfs.smudge=cat
  -c filter.lfs.clean=cat -c filter.lfs.process=`. Repositories that
  use git-lfs will have their LFS-tracked files materialized as
  **pointer files** rather than real content. This is safe across any
  environment because it does not depend on `git-lfs` being installed.
- **`enabled`**: LFS filters are left alone. Requires `git-lfs` to be
  reachable from the subprocess PATH. If `git-lfs` is missing and the
  user's `.gitconfig` declares `filter.lfs.required = true`, git will
  hang indefinitely during checkout. Use only when you control the
  subprocess environment.
- **`auto`**: at startup, the server checks for `git-lfs` in the
  subprocess PATH. If found, LFS is enabled; otherwise it is disabled.
  Good middle ground for mixed environments where LFS may or may not
  be installed.

The default is `disabled` because silent deadlocks are much worse than
pointer files in a working tree: pointer files are immediately visible
on inspection, deadlocks look like the server is hung.

## Client configuration

Add the server to your MCP client configuration. For Claude Desktop,
edit `claude_desktop_config.json` (path varies by OS). Example:

```json
{
  "mcpServers": {
    "bitbucket-dc": {
      "command": "bitbucket-dc-mcp",
      "env": {
        "BITBUCKET_BASE_URL": "https://bitbucket.example.com",
        "BITBUCKET_TOKEN": "${SECRET:bitbucket_token}",
        "BITBUCKET_USERNAME": "your.username",
        "BITBUCKET_DEFAULT_PROJECT": "MYPROJ",
        "BITBUCKET_WORKSPACE": "/path/to/workspace",
        "BITBUCKET_ALLOWED_HOSTS": "bitbucket.example.com",
        "BITBUCKET_LFS_MODE": "disabled"
      }
    }
  }
}
```

> **Never commit real tokens to this file.** In production, use your
> operating system's secret store and reference the secret via a
> placeholder that your client resolves at launch. See [SECURITY.md](SECURITY.md).

The `${SECRET:...}` syntax shown above is illustrative; the actual
mechanism depends on your MCP client. Some clients read env vars from
the process environment (set them before launching the client); others
support explicit secret-store integration. Check your client's docs.

See `examples/mcp_config_generic.json` for a complete template.

### Windows notes

On Windows, some MCP clients (notably Claude Desktop from the Microsoft
Store) spawn the server subprocess with a **sanitized environment**
that does not inherit the user's full `PATH`. If `git.exe` is installed
in a non-standard location, for example inside a portable
[cmder](https://cmder.app/) distribution at
`C:\cmder\vendor\git-for-windows\cmd\`, the subprocess will not find it
and all git operations will fail.

To fix this, add an explicit `PATH` entry to the server's `env` block
in your client configuration. Example for Git for Windows installed at
its default location:

```json
"env": {
  "PATH": "C:\\Program Files\\Git\\cmd;C:\\Program Files\\Git\\bin;C:\\Windows\\System32;C:\\Windows",
  "BITBUCKET_BASE_URL": "..."
}
```

For a cmder-bundled Git for Windows, the PATH typically needs to
include `cmd`, `bin`, `usr\bin`, and (if you want LFS)
`mingw64\bin` subdirectories of the cmder git installation.

This server handles the Windows-specific subprocess quirks of
Git for Windows internally: stdout and stderr are routed through
temporary files rather than anonymous pipes, working around a hang
that can occur during git's early process initialization when its
standard handles are Python-created pipes. No user configuration is
required for this workaround.

## Usage

Once configured, restart your MCP client and ask the assistant to
perform Bitbucket operations:

> "Clone the repository `my-service` and show me the contents of
> `src/main.py`."

> "List the files in the `src/` directory of `my-service` at branch
> `develop`."

> "Show me the open pull requests on `my-service`, then get the diff
> of PR #123 and summarize what it changes."

> "Create a branch `feature/fix-login` off master, apply these code
> changes, commit them, push the branch, and open a pull request
> titled 'Fix login bug'."

> "Reply to the comments on PR #456 with clarifications on the
> performance concerns raised."

The assistant will invoke the appropriate tools. Your MCP client will
show a per-tool approval dialog before each invocation; **do not
disable these approvals** in production.

## Development

```
git clone https://github.com/inkman97/bitbucket-dc-mcp
cd bitbucket-dc-mcp
pip install -e .[dev]
pytest
```

The test suite has over 100 tests covering configuration loading,
input validation, logging and secret redaction, git runner subprocess
handling, HTTP client SSRF protection, the write-side tools
(`write_file`, `edit_file`, `apply_patch`), and the read-side tools
via HTTP client mocks. All tests pass without network access or a real
Bitbucket instance.

## Limitations and design decisions

**No OAuth.** Bitbucket Data Center does not support OAuth 2.0 for
REST API client authentication. This server uses an HTTP Access Token
(Bearer), which must be sourced from a secret store in production. If
your security policy mandates OAuth, this server may not meet your
requirements literally, but see [SECURITY.md](SECURITY.md) for the
compensating controls.

**Stdio transport only.** The server runs as a local subprocess of the
MCP client via stdio. It does not expose an HTTP endpoint, which means
there is no network attack surface to worry about, but also means it
cannot be shared between multiple users. Each developer runs their own
instance.

**Bitbucket Data Center only.** The REST API calls use `/rest/api/1.0/`
and the clone URL pattern `/scm/{project_lower}/{repo}.git` specific to
Data Center. For Bitbucket Cloud, use a different server.

**Single token, read and write.** The server currently uses one
`BITBUCKET_TOKEN` for all operations. There is no separation between
a read-only token for exploration and a read-write token for
committing and pushing. If you need to restrict what AI assistants can
do, scope the single token at the Bitbucket side accordingly; a future
version may add separate tokens for read and write operations.

**No merge operation.** Pull requests are created via this server but
must be merged manually through the Bitbucket UI after human review.
This is a deliberate design choice to preserve the review gate. See the
"Deliberately omitted" section above.

**`bitbucket_create_branch` requires a local clone.** This tool creates
a branch in the locally cloned working tree (`git checkout -b`), not
directly on the remote. This matches the typical workflow where a
branch is immediately used for local modifications. To make the branch
visible on the remote, follow up with `bitbucket_push_branch`.

**LFS disabled by default.** Repositories that use git-lfs will see
their LFS-tracked files as pointer files rather than real content
unless `BITBUCKET_LFS_MODE=enabled` is set and `git-lfs` is reachable
in the subprocess PATH. See the LFS section under Configuration.

**Client-side timeouts on long operations.** Some MCP clients
(including early versions of Claude Desktop) enforce a client-side
timeout on tool invocations. Cloning or pulling very large repositories
may hit this timeout. The server mitigates this by sending
`notifications/progress` messages every 10 seconds during long git
operations, which resets the client's inactivity counter. If your
client does not honor progress notifications and you hit timeouts,
pre-clone the repository once outside the server so that subsequent
calls go through the faster `fetch + pull` path.

**Alpha status.** APIs, tool names, and argument shapes may change
before the first stable release. Do not depend on them from production
code.

## License

MIT. See [LICENSE](LICENSE).

## Related projects

- [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) — MCP
  server for Jira and Confluence, supports both Cloud and Data Center,
  and includes OAuth 2.0 support for Data Center. Pairs well with this
  server if you want both Jira and Bitbucket tools.
- [Model Context Protocol](https://modelcontextprotocol.io) — the
  protocol this server implements.
