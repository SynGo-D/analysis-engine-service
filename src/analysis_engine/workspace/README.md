# workspace

Isolated temporary workspace management — one workspace per analysis job.

- `WorkspaceManager.prepare(job)` — async context manager. Creates a
  fresh `tempfile.mkdtemp` directory (0o700 permissions by default),
  clones the target commit into it, yields a `Workspace`, and guarantees
  cleanup (`shutil.rmtree`, off the event loop) even if anything inside
  the `async with` block raises. Verified against a real leftover-`/tmp`
  check, not just an in-process assertion.
- `git_client.py` — fetches `branch` (shallow, depth 50 by default) and
  checks out the specific `commit_sha` from what was fetched, executed
  via argument-list subprocess calls (never `shell=True`) with a
  per-command timeout. **Not** a straight fetch-by-SHA — that was the
  original design, and testing it against a real public GitHub repo
  showed it fails outright ("couldn't find remote ref"): GitHub doesn't
  universally support fetching by raw SHA
  (`uploadpack.allowReachableSHA1InWant` is often disabled). Fetching the
  branch by name and checking out the exact commit from that is what's
  actually reliable.
- Validates `clone_url` (https + allowlisted host only — closes
  SSRF-style abuse via `file://`/arbitrary hosts), `commit_sha` (hex-SHA
  pattern), and `branch` *before* any of it reaches a subprocess.
  `validate_branch` is a **blocklist** matching git's own actual
  ref-name rules (`git-check-ref-format`), not an arbitrary allowlist —
  found via a real live webhook delivery for a branch literally named
  `branch#1`, which the original allowlist (`^[A-Za-z0-9._/-]+$`)
  rejected outright. `#` is a perfectly valid character in real git
  branch names; that allowlist was simply wrong, not conservative. Since
  every git subprocess call here uses argument-list execution
  (`shell=False`), shell metacharacters like `#` were never actually an
  injection risk to begin with — the real, still-enforced risk is a
  branch name starting with `-` (git's own flag-injection vector, since
  it's git itself parsing argv, independent of shell=False).

Repository source code is treated as untrusted input throughout. Hard
CPU/memory/network isolation beyond the clone timeout is explicitly out
of scope here — that's a containerization concern (Phase 12), not
something a bare process can genuinely enforce.

## Private repositories

Clones of private repositories are authenticated with the OAuth token of
a user who connected the repository. integration-service stores it
encrypted and hands out a currently valid one (refreshed when needed) on
`GET /internal/repository-token`, behind the shared `INTERNAL_SERVICE_TOKEN`
(`credentials.py`).

The token reaches git through environment variables
(`GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0`, git ≥ 2.31)
as an `http.<host>.extraheader`. The alternatives each leak it:

| Approach | Leak |
|---|---|
| Token in the clone URL | Saved in `.git/config`, on disk in the workspace |
| `git -c http.extraheader=…` | On the command line, visible to every user through `ps` |
| **Environment, per git command** | Readable only by the same user; used here |

Also:
- **Scoped to the provider's host,** so a redirect elsewhere doesn't carry it.
- **Only git network commands get it:** the clone, the target-branch fetch
  and the Rule Miner's `ls-remote`. It's never put in `os.environ`, so
  linters and agent tools never inherit it. Agents can't read `.git/`
  either.
- **Never logged.** `Workspace.__repr__` hides it, and errors include git's
  stderr but not the environment.
- **Fails safe.** No `INTERNAL_SERVICE_TOKEN`, a repository nobody
  connected, or integration-service unreachable all mean an anonymous
  clone: public repositories still work, and private ones fail at clone
  time with git's own error. `GIT_TERMINAL_PROMPT=0` means git never waits
  for a password.

Tested against a local fake git server (`tests/test_private_repositories.py`):
the header is sent to the right host, not sent to another, and the token
appears nowhere under `.git/` or in errors.
