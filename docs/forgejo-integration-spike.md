# Forgejo integration spike

Issue: [#111](https://github.com/m3rrym4n/variflex/issues/111)

Investigated: 2026-08-11

Live instance: Forgejo `16.0.2+gitea-1.22.0`

## Executive summary

A small shared git-host interface is realistic. The operations Variflex needs have direct Forgejo REST equivalents, and the issue, file-content, PR, comment, milestone, and workflow-dispatch payloads are close enough to normalize at the client boundary. The implementation should nevertheless use separate `GitHubClient` and `ForgejoClient` adapters rather than conditionals inside one HTTP client: authentication syntax, API roots, update verbs, status codes, response details, and webhook headers differ.

The main milestone finding is that the proposed `PUT` route does **not** exist on this live version. Forgejo exposes collection `GET`/`POST` at `/repos/{owner}/{repo}/milestones`, then item `GET`/`PATCH`/`DELETE` at `/repos/{owner}/{repo}/milestones/{id}`.

This was a read-only investigation. The live version endpoint and OpenAPI document were fetched successfully. The instance has no public repositories, and no Forgejo credential was provided, so authenticated operations and mutation behavior were not exercised. Request/response contracts below are confirmed from the live instance's OpenAPI schema; they should receive an authenticated integration test before implementation is considered complete.

## Sources and method

- Live `GET /api/v1/version`: `16.0.2+gitea-1.22.0`.
- Live OpenAPI 2.0 schema: [`/swagger.v1.json`](http://192.168.1.73:3001/swagger.v1.json), linked from [`/api/swagger`](http://192.168.1.73:3001/api/swagger).
- Forgejo's version-matched [access-token scope documentation](https://forgejo.org/docs/latest/user/token-scope/).
- Current [`GitHubClient`](../orchestrator/task_runner/github.py), which performs issue fetch, `AGENTS.md` fetch, and Actions workflow dispatch. It does not currently create PRs or post comments; those are mapped below because the issue explicitly identifies them as migration requirements.

Unauthenticated `GET /api/v1/repos/search` returned an empty list. A milestone request for a deliberately nonexistent repository returned `404`, confirming reachability and the normal error envelope, but not authorization or successful CRUD behavior.

## Authentication and least privilege

The live schema accepts `Authorization: token <PAT>` (not GitHub's current `Bearer <token>` header). It also advertises Basic auth, OTP-assisted Basic auth, and admin sudo mechanisms; none are appropriate for the service integration. Use a PAT in the authorization header, never in a query string, and make the Forgejo base URL configurable.

Forgejo PAT scopes are route categories. A practical least-privilege token for the complete requested surface is:

| Scope | Needed for |
| --- | --- |
| `read:repository` | Read `AGENTS.md` and repository metadata; read access to workflow routes |
| `read:issue` | Read issues, comments, and milestones |
| `write:repository` | Create PRs and dispatch workflows; write includes read for this category |
| `write:issue` | Post comments and create/update/delete milestones; write includes read |

If all listed operations are enabled, `write:repository` plus `write:issue` is sufficient; listing explicit `read:*` scopes as well is redundant. Restrict the PAT to the selected repositories using Forgejo's repository-restricted token support. If a deployment only builds prompts, use `read:repository` plus `read:issue`. No `admin`, `organization`, `user`, `misc`, or package scope is required.

The current `GITHUB_TOKEN` is a classic GitHub PAT with `repo` and `workflow` scopes (along with unrelated scopes). `repo` broadly covers private repository contents, issues, PRs, and comments; `workflow` covers dispatch. Forgejo's category and repository restrictions permit a substantially narrower credential. Configuration should model a host token as opaque and document host-specific permissions rather than trying to translate GitHub scope names.

## Endpoint mapping

All Forgejo paths below are relative to configurable base `/api/v1`. `owner/repo` must be split into two encoded path segments.

| Required operation | Current GitHub request | Forgejo 16 equivalent | Compatibility notes |
| --- | --- | --- | --- |
| Fetch issue for prompt | `GET /repos/{owner}/{repo}/issues/{number}` | `GET /repos/{owner}/{repo}/issues/{index}` | Clean equivalent. Both expose `title` and nullable/string `body`. Forgejo calls the path value `index` but returns it as `number`. As on GitHub, this route can represent a PR, so callers should not infer issue-only semantics from the route. |
| Fetch `AGENTS.md` | `GET /repos/{owner}/{repo}/contents/AGENTS.md` | `GET /repos/{owner}/{repo}/contents/AGENTS.md` | Clean equivalent. Both return base64 file content; Forgejo also exposes `encoding`. Preserve the current `404` fallback. An optional `ref` can make branch selection explicit. |
| Dispatch workflow | `POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches` | `POST /repos/{owner}/{repo}/actions/workflows/{workflowfilename}/dispatches` | Same `{ref, inputs}` body. Forgejo selects by workflow filename, while GitHub accepts a workflow ID or filename. Forgejo may return `201` with run data or `204`; GitHub normally returns `204`. Normalize to success/no result unless run metadata is needed. |
| Create PR | `POST /repos/{owner}/{repo}/pulls` | `POST /repos/{owner}/{repo}/pulls` | Clean route and core `{title, body, head, base}` mapping. Forgejo supports extra assignee, due date, label-ID, and milestone-ID fields. Normalize the returned number and `html_url`; do not expose the full host response. |
| Post issue or PR conversation comment | `POST /repos/{owner}/{repo}/issues/{number}/comments` | `POST /repos/{owner}/{repo}/issues/{index}/comments` | Clean equivalent with `{body}` and `201`. Forgejo PRs share the issue conversation endpoint, as GitHub PR issue comments do. Inline review comments are a separate API and are not needed here. |
| List milestones | `GET /repos/{owner}/{repo}/milestones` | `GET /repos/{owner}/{repo}/milestones` | Clean equivalent. Forgejo supports `state` (`open`, `closed`, `all`), name filtering, and 1-based `page`/`limit`; handle pagination explicitly. |
| Create milestone | `POST /repos/{owner}/{repo}/milestones` | Same | Clean equivalent; see contract below. |
| Get milestone | `GET /repos/{owner}/{repo}/milestones/{number}` | `GET /repos/{owner}/{repo}/milestones/{id}` | Forgejo identifies the item by numeric ID (the schema says name is also accepted as fallback), not a GitHub-style milestone number. Treat the identifier as host-owned/opaque. |
| Update milestone | `PATCH /repos/{owner}/{repo}/milestones/{number}` | `PATCH /repos/{owner}/{repo}/milestones/{id}` | Clean capability, but **not** the `PUT` collection route stated in the investigation question. |
| Delete milestone | `DELETE /repos/{owner}/{repo}/milestones/{number}` | `DELETE /repos/{owner}/{repo}/milestones/{id}` | Clean equivalent; success is `204`. |

There is no missing core capability. The most important divergences are Forgejo's item ID semantics, workflow filename/status behavior, and host-specific error bodies. The current client should also stop hard-coding GitHub media-type and API-version headers in any shared transport.

## Milestone CRUD contract on the live instance

### List

`GET /repos/{owner}/{repo}/milestones?state=open|closed|all&name=<text>&page=<n>&limit=<n>` returns `200` with an array of milestone objects, or `404`. The default state is `open`; callers needing a complete synchronization must request `state=all` and paginate.

### Create

`POST /repos/{owner}/{repo}/milestones` accepts JSON:

```json
{
  "title": "Migration",
  "description": "Forgejo migration work",
  "due_on": "2026-12-31T23:59:59Z",
  "state": "open"
}
```

All fields are marked optional by the live schema, although a useful client should require a non-empty title before sending. `state` is `open` or `closed`, and `due_on` is RFC 3339 date-time. Success is `201` with a milestone object; `404` is documented.

### Read and update

`GET /repos/{owner}/{repo}/milestones/{id}` returns `200` with one milestone or `404`.

`PATCH /repos/{owner}/{repo}/milestones/{id}` accepts any subset of:

```json
{
  "title": "Migration phase 1",
  "description": "Updated scope",
  "due_on": "2027-01-31T23:59:59Z",
  "state": "closed"
}
```

Success is `200` with the updated milestone; `404` is documented. The edit schema does not enumerate `state`, unlike create, so the adapter should still constrain it locally to `open` or `closed`. The schema does not document how to clear `due_on`; that needs an authenticated integration test before exposing a clear-deadline operation.

### Delete and response shape

`DELETE /repos/{owner}/{repo}/milestones/{id}` returns `204` or `404`.

A milestone response contains `id`, `title`, `description`, `state`, `due_on`, `created_at`, `updated_at`, `closed_at`, `open_issues`, and `closed_issues`. Dates are RFC 3339 date-times and may be absent/null in actual JSON where not applicable. Unlike GitHub's milestone representation, the live Forgejo schema does not expose `number`, `url`, or `html_url`; consumers must not build links or cross-host identity from those fields.

## Webhooks and CI-stage reporting

Forgejo supports repository webhook CRUD at `/repos/{owner}/{repo}/hooks` and hook testing at `/repos/{owner}/{repo}/hooks/{id}/tests`. Creation uses a `forgejo` hook type, a config map (including URL and content type), an `events` array, optional `branch_filter`, `active`, and optional outbound `authorization_header`.

For Part 10, account for these differences rather than feeding Forgejo payloads directly into a GitHub webhook parser:

- Event headers are `X-Forgejo-Event` and `X-Forgejo-Delivery`. The version-matched Forgejo documentation also shows GitHub-, Gitea-, and Gogs-compatible event/delivery headers, but ingestion should use the native Forgejo names rather than depend on compatibility aliases.
- Forgejo event names and payload types are similar, but not a contractual match to GitHub. Introduce a host-specific webhook verifier/parser that emits a small internal CI event (`repo`, ref/SHA, workflow/run identity, stage/status/conclusion, delivery ID).
- Confirm the exact Actions/workflow event available on this deployment with a credential before designing Part 10. The live OpenAPI schema accepts free-form event strings and therefore does not prove which UI-selectable events or Actions payloads this configured instance emits.
- Configure a webhook secret, verify the hex HMAC-SHA-256 value in `X-Forgejo-Signature` against the raw request body using constant-time comparison, and deduplicate on delivery ID. An outbound authorization header is also supported, but it should not replace payload-signature verification without an explicit threat-model decision.

## Recommended abstraction

Use a narrow protocol driven by configuration, with separate adapters:

```text
GitHostClient
  get_issue_context(repo, number) -> IssueContext
  get_text_file(repo, path, ref?) -> str | not-found
  dispatch_workflow(repo, workflow, ref, inputs) -> None
  create_pull_request(repo, title, body, head, base) -> PullRequestRef
  post_comment(repo, number, body) -> CommentRef
  list/create/get/update/delete_milestone(...) -> normalized milestone values
```

Select `{type: github|forgejo, base_url, token}` from configuration and inject the adapter. Keep authentication headers, paths, pagination, response decoding, and error translation inside each adapter. Normalize only fields the orchestrator uses; retaining raw provider models would leak divergence upward. Use an opaque string for workflow selectors and milestone identifiers even if one provider currently uses integers.

Do not subclass `ForgejoClient` from `GitHubClient` or add provider branches to every method. Their route similarity makes a shared interface valuable, while their transport details make shared implementation inheritance brittle. A small common HTTP/error utility is reasonable if duplication appears during implementation.

## Follow-up validation required before implementation

With a repository-restricted Forgejo PAT in a disposable test repository:

1. Verify the two-scope token (`write:repository`, `write:issue`) can perform every mapped operation and that read-only variants fail writes.
2. Exercise full milestone CRUD, pagination, duplicate titles, invalid state, empty title, deadline clearing, and deletion when issues reference a milestone.
3. Confirm PR head syntax for same-repository and fork branches, plus the workflow filename and `201`/`204` variants.
4. Capture representative `401`, `403`, `404`, `409`, `422`, and archived-repository `423` errors for stable error translation.
5. Create a test webhook and record the exact Actions-related event names, delivery/signature headers, retries, and payloads emitted by this Forgejo configuration.

These are implementation prerequisites, not blockers to the conclusion of this documentation spike.
