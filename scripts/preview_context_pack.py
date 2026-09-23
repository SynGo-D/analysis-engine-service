"""
Shows exactly what the Reviewer agent would read for a real pull request,
and roughly what that costs — without calling any model.

    .venv/bin/python scripts/preview_context_pack.py \\
        https://github.com/SynGo-D/test-project.git "branch#1" main

The pack goes to stdout, the size and cost summary to stderr, so
`> pack.txt` keeps only the pack.
"""

import argparse
import asyncio
import subprocess
import sys

from analysis_engine.agents.runtime.pricing import OPENAI_PRICES
from analysis_engine.application.finding_normalizer import FindingNormalizer
from analysis_engine.context import ContextPackBuilder
from analysis_engine.diffing import DiffExtractor, changed_symbols, mark_findings
from analysis_engine.domain import AnalysisJob
from analysis_engine.factories import AnalyzerFactory, detect_languages
from analysis_engine.indexing import CodeIndexer
from analysis_engine.workspace import WorkspaceManager


def _head_of(clone_url: str, branch: str) -> str:
    output = subprocess.run(
        ["git", "ls-remote", clone_url, f"refs/heads/{branch}"],
        check=True, capture_output=True, text=True,
    ).stdout
    if not output:
        sys.exit(f"Branch '{branch}' not found at {clone_url}")
    return output.split()[0]


async def main(clone_url: str, branch: str, target: str, title: str | None) -> None:
    repository = clone_url.removeprefix("https://").split("/", 1)[1].removesuffix(".git")
    job = AnalysisJob(
        provider="github", repository=repository, clone_url=clone_url,
        commit_sha=_head_of(clone_url, branch), branch=branch, pull_request_number=0,
        queued_at="preview", target_branch=target, title=title,
    )

    async with WorkspaceManager().prepare(job) as workspace:
        changes = await DiffExtractor().extract(workspace.path, job)
        if changes.change_set is None or changes.status != "available":
            sys.exit(f"No reviewable changes: {changes.unavailable_reason}")

        index = CodeIndexer().build(workspace.path)
        changes.changed_symbols = changed_symbols(index, changes.change_set)

        analyzers = AnalyzerFactory().create_for_languages(detect_languages(workspace.path))
        results = await asyncio.gather(*(a.analyze(workspace, job) for a in analyzers), return_exceptions=True)
        findings = FindingNormalizer().normalize([f for r in results if isinstance(r, list) for f in r])
        findings, on_changed = mark_findings(findings, changes.change_set)

        pack = await ContextPackBuilder().build(workspace.path, job, changes, index, findings)

    print(pack.render())

    tokens = pack.estimated_tokens
    print(f"\n--- {pack.char_count:,} characters ≈ {tokens:,} tokens "
          f"({changes.files_changed} files, {len(changes.changed_symbols)} changed symbols, "
          f"{on_changed} of {len(findings)} findings on changed lines)", file=sys.stderr)
    print("Cost to send this pack once, as fresh / cached input:", file=sys.stderr)
    for model, price in OPENAI_PRICES.items():
        fresh = tokens * price.input / 1_000_000
        cached = tokens * price.cached_input / 1_000_000
        print(f"  {model:<14} ${fresh:.5f} / ${cached:.5f}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clone_url")
    parser.add_argument("branch")
    parser.add_argument("target_branch")
    parser.add_argument("--title")
    args = parser.parse_args()
    asyncio.run(main(args.clone_url, args.branch, args.target_branch, args.title))
