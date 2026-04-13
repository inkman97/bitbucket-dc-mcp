# Security Considerations

This document describes the threat model, security controls, and
deployment requirements for `bitbucket-dc-mcp`. It is written to
support a security review by the adopting organization.

## 1. Scope

The subject of this document is the Python MCP server shipped by this
project. Out of scope: the MCP client itself, the AI model, and the
Bitbucket Data Center server.

## 2. Why a custom server

For Jira and Confluence, the upstream project
[`mcp-atlassian`](https://github.com/sooperset/mcp-atlassian) is mature,
widely used, and supports OAuth 2.0 for Atlassian Data Center. Use it
as-is.

For Bitbucket Data Center, no equivalent upstream server exists. This
project provides a minimal, auditable alternative.

## 3. Tool surface

The server exposes fourteen tools, eight read-only and six write. Write
tools are tagged `[WRITE]` in their description so the MCP client's
human approval UI can clearly distinguish them.

**Read tools:**

- `bitbucket_get_repo_info`
- `bitbucket_list_branches`
- `bitbucket_list_files`
- `bitbucket_get_file_content`
- `bitbucket_list_pull_requests`
- `bitbucket_get_pull_request`
- `bitbucket_get_pull_request_diff`
- `bitbucket_get_pull_request_comments`

**Write tools:**

- `bitbucket_clone_repo`
- `bitbucket_create_branch`
- `bitbucket_commit_changes`
- `bitbucket_push_branch`
- `bitbucket_create_pull_request`
- `bitbucket_add_pull_request_comment`

### 3.1 Deliberately omitted: merge_pull_request

The server does **not** expose a tool to merge a pull request, even
though the Bitbucket Data Center REST API supports this operation.

**Rationale.** Merging a pull request is the one operation in the
entire workflow that is *irreversible* in the practical sense: once a
branch is merged into the main line of development, the code is
immediately visible to other developers and part of the project
history. Undoing a merge requires a revert commit that leaves a trace
and may itself need review. Every other write operation in this server
is recoverable: a branch can be deleted, a commit can be amended or
reset before pushing, a push can be force-pushed to a previous state,
a pull request can be declined, a comment can be deleted.

The review of a pull request by a human is the primary security gate
of the entire AI-assisted workflow. If an AI coding assistant could
complete the cycle `clone → branch → commit → push → create PR →
merge` without any human ever reading the code, the integrity of the
codebase would depend entirely on the quality of the AI's output and
the reviewer's diligence at each per-tool approval popup. The
per-tool approvals are a useful gate for reversible operations, but
they are not equivalent to a considered code review.

By design, this server leaves merging to a human acting in the
Bitbucket UI, after reviewing the PR. This is the single most
important defense-in-depth control of the project. Removing it would
undermine most of the other controls.

**If you decide to add a merge tool later,** you accept that the
per-tool approval dialog is now the last checkpoint before
irreversible changes enter your main branch. Weigh this carefully
against your risk tolerance, and document the decision in your own
threat model.

## 4. Authentication model and OAuth limitation

Some enterprise security policies require that AI agent tools use OAuth
2.0 for authentication, with an enterprise identity provider (Entra ID,
Okta, etc.) as the authorization server.

**Technical constraint.** Bitbucket Data Center does not support OAuth
2.0 as a client authentication mechanism for its REST API. The
available authentication methods are:

- HTTP Access Tokens (Bearer)
- Basic Auth (username + password)
- OAuth 1.0a via Application Links, intended for Atlassian-to-Atlassian
  integration only

This server uses HTTP Access Tokens. The token must be sourced from a
secret store at deployment time, never committed to configuration files.

**Compensating controls** for the absence of OAuth:

- The server runs as a local subprocess of the MCP client (stdio
  transport). Authentication between client and server is implicit
  through process launch with inherited environment variables; there
  is no network attack surface between them.
- The HTTP Access Token must be stored in a secret store (OS keyring,
  Credential Manager, Key Vault, HashiCorp Vault, Kubernetes Secret,
  etc.). Storing it in plaintext configuration files or source code is
  a policy violation.
- The token should be scoped to the minimum permissions needed
  (read/write on specific projects, never admin).
- The token must be rotated regularly (suggested: every 90 days, or
  per your organization's policy for service credentials).
- All operations are audit-logged for after-the-fact review.

If your organization's policy strictly requires OAuth with no
exceptions, this server may not meet the requirement literally. Discuss
the compensating controls with your security team before adopting.

## 5. Assets

| Asset | Sensitivity | Notes |
|---|---|---|
| Bitbucket HTTP Access Token | High | Grants write access to source repositories |
| Cloned source code | High | May contain proprietary or regulated code |
| Git credentials cache | High | Must never be persisted |
| Audit log records | Medium | Contain metadata about every tool invocation |
| MCP session identifiers | Low | Used for log correlation |

## 6. Trust boundaries

```
    +-----------------+      stdio      +----------------------+
    |   MCP Client    |<--------------->|  bitbucket-dc-mcp    |
    | (Claude, etc.)  |  JSON messages  |  (this server)       |
    +-----------------+                 +----------------------+
                                                  |
                                                  | HTTPS + Bearer
                                                  v
                                        +----------------------+
                                        | Bitbucket Data       |
                                        | Center               |
                                        +----------------------+
```

Three boundaries:

1. MCP client <-> MCP server (local subprocess, inherited env)
2. MCP server <-> Bitbucket (HTTPS, Bearer token authentication)
3. MCP server <-> local filesystem (workspace directory only)

## 7. Threats and mitigations

### T1 — Prompt injection

**Description.** A crafted instruction in an AI prompt could cause the
model to call a tool with arguments that exfiltrate data or perform
unintended operations.

**Mitigations.**

- Every tool argument passes through an allowlist validator
  (`src/bitbucket_dc_mcp/validation.py`). Invalid inputs are rejected
  with a logged audit event.
- `repo_slug`, `branch_name`, `project_key`, `file_path`, commit
  messages, titles, descriptions, pull request IDs, comment text,
  and PR states each have their own validator.
- Paths are resolved and confined to the workspace via
  `Path.relative_to(workspace_dir)`, so a crafted slug cannot escape.
- `file_path` rejects `..` segments, absolute paths, and control
  characters. Control characters are checked before any stripping so
  a trailing newline cannot bypass the check.
- Branch names starting with `-` are rejected, preventing git option
  injection like `--upload-pack=...`.
- Pull request states are restricted to the set `OPEN, DECLINED,
  MERGED, ALL` before being interpolated into a URL.
- The MCP client preserves per-tool human approval by default. Each
  write tool is explicitly tagged `[WRITE]` in its description.
- The server does not expose a merge tool (see section 3.1), so even
  a successful prompt injection cannot bypass the human review on a
  pull request.

**Residual risk: medium.** The human reviewer is the last line of
defense. A reviewer who blindly approves all tool calls can still
authorize harmful operations. This is an operational control, not a
technical one. The absence of a merge tool caps the worst-case
outcome at "a malicious PR is created and pushed, awaiting review",
which is recoverable.

### T2 — Credential theft

**Description.** The HTTP Access Token could be exposed if written to
disk, log files, temp files, or error messages.

**Mitigations.**

- Token is read only from an environment variable at startup.
- Token is passed to git via `-c http.extraheader=...` on each command;
  this is ephemeral and never persisted to `.git/config`.
- A global logging filter (`SecretRedactingFilter`) replaces any
  occurrence of the token in log records with `***REDACTED***`.
- Tool arguments with sensitive key names (`token`, `password`,
  `secret`, `authorization`) are redacted before being logged.
- Git stderr is passed through the redactor before being included in
  error messages returned to the client.

**Residual risk: low.** The token never leaves process memory during
normal operation. A process memory dump on the host would reveal it,
which is why the host itself must be protected.

### T3 — Supply chain attack via dependencies

**Description.** A malicious update to a dependency (`mcp`, `httpx`)
could introduce a backdoor.

**Mitigations.**

- Dependencies are minimal: only `mcp` and `httpx`.
- For production deployments, pin exact versions with hashes in a
  locked requirements file, and review updates before applying.
- Both packages are actively maintained and widely used.

**Residual risk: low.** Even pinned versions can contain vulnerabilities
discovered after deployment. Routine dependency review is recommended.

### T4 — Command injection via git arguments

**Description.** A crafted slug, branch, or commit message could be
interpreted as a shell metacharacter or a git option.

**Mitigations.**

- All `subprocess.run` calls pass arguments as a list, never as a
  shell string. No shell is invoked.
- Input validation rejects characters commonly used for argument
  injection: whitespace, quotes, shell metacharacters.
- Branch and slug names must start with an alphanumeric character,
  preventing them from being interpreted as git flags.

**Residual risk: very low.**

### T5 — Denial of service

**Description.** A crafted prompt could trigger a clone of a very
large repository, a slow network fetch, or a huge file read that
blocks the server.

**Mitigations.**

- Git subprocess calls have a configurable timeout (default 300 s).
- HTTP calls have a configurable timeout (default 30 s).
- `get_file_content` enforces a max byte limit (default 1 MiB) and
  truncates, with the size check performed while streaming paginated
  results.
- `list_files` caps results at 5000 entries to protect against
  repositories with very large trees.
- `list_branches` and paginated PR endpoints limit results per page.
- `get_pull_request_comments` caps the number of activity log pages
  fetched to 10, protecting against pathologically long PR histories.

**Residual risk: low.**

### T6 — SSRF (Server-Side Request Forgery)

**Description.** An attacker could supply a URL or construct a path
that causes the server to make requests to internal services other
than Bitbucket.

**Mitigations.**

- `BITBUCKET_ALLOWED_HOSTS` is enforced at startup and before every
  HTTP request. Any URL whose host is not in the allowlist is rejected.
- When `BITBUCKET_ALLOWED_HOSTS` is set explicitly, the server does
  **not** auto-add the `BITBUCKET_BASE_URL` host. An explicit
  allowlist is respected exactly, so a misconfigured `base_url`
  pointing outside the allowlist causes startup to fail rather than
  silently succeed.
- `BITBUCKET_BASE_URL` must be HTTPS; HTTP is refused at startup.
- `follow_redirects=False` is set on the HTTP client, preventing
  redirect-based bypass of the host check.

**Residual risk: very low.**

### T7 — Token leakage via audit log

**Description.** If the audit log is forwarded to a SIEM as raw text,
tokens could leak into SIEM storage.

**Mitigations.**

- Audit log writer uses the same secret redaction filter as the
  operational log.
- Argument sanitization removes known-sensitive keys before records
  are written.
- Comment text passed to `bitbucket_add_pull_request_comment` is
  subject to the same secret redaction as all other log output, so
  even if an AI accidentally includes a token in a comment body, it
  will not appear in the audit log as plaintext.
- In deployment, the SIEM forwarder should additionally apply its own
  redaction rules as defense in depth.

**Residual risk: low.**

### T8 — Unauthorized code in main branch via automated merge

**Description.** An AI coding assistant, either through prompt
injection, hallucination, or misconfiguration of the client's
per-tool approval policy, could attempt to merge a pull request
before any human has reviewed the code it contains.

**Mitigations.**

- The server does not expose a `merge_pull_request` tool. See
  section 3.1 for the full rationale. This is a structural mitigation,
  not a configuration option: the capability is simply not part of
  the tool surface the AI assistant can see.
- Merging must be performed manually by a human in the Bitbucket UI
  after reviewing the pull request.

**Residual risk: very low**, assuming the deployment operator does
not add a merge tool in a fork or subclass of the server.

## 8. Deployment requirements

For the server to be considered compliant in a given deployment, the
operator must:

1. Source `BITBUCKET_TOKEN` from a secret store, not from a plain-text
   configuration file or environment file under source control
2. Set `BITBUCKET_ALLOWED_HOSTS` explicitly to the exact hosts of your
   Bitbucket DC instance
3. Configure the MCP client to require explicit human approval for
   every tool invocation. Do not disable approvals.
4. Require a human code review on every pull request before merging.
   Do not configure auto-merge on branches where AI-created PRs can
   land.
5. Forward the file at `BITBUCKET_AUDIT_LOG_PATH` to your SIEM
6. Apply filesystem permissions so the workspace and audit log are
   readable only by the running user
7. Rotate the Bitbucket token per your organization's policy
8. Pin exact versions of `mcp` and `httpx`; review updates monthly

## 9. Open items for each deployment

Each adopting organization should answer these before going to
production:

1. Which secret backend will store the Bitbucket token?
2. What is the SIEM endpoint and protocol for audit log forwarding?
3. What is the minimum token scope required for your workflows?
4. What is the token rotation cadence and procedure?
5. Which users and which repositories are in scope for the initial
   rollout?
6. What is the policy for merging pull requests created by AI
   assistants? (At minimum, manual review by a human other than the
   person who drove the AI session is recommended.)

## 10. Reporting security issues

If you discover a security vulnerability in this project, please
report it privately to the maintainers before disclosing publicly.
Create a GitHub security advisory or email the maintainer listed in
the project metadata.
