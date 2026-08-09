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
  pattern), and `branch` (rejects a leading `-`, git's own
  flag-injection vector) *before* any of it reaches a subprocess.

Repository source code is treated as untrusted input throughout. Hard
CPU/memory/network isolation beyond the clone timeout is explicitly out
of scope here — that's a containerization concern (Phase 12), not
something a bare process can genuinely enforce.
