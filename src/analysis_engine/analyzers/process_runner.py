import asyncio
from pathlib import Path


class ToolExecutionError(Exception):
    """Raised when a tool subprocess times out, or an analyzer decides its exit code means a genuine failure."""


class ProcessResult:
    def __init__(self, stdout: str, stderr: str, returncode: int):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


async def run_process(
    args: list[str],
    cwd: Path,
    timeout: float,
    stdin_data: str | None = None,
) -> ProcessResult:
    """
    Runs a command via argument-list execution — never shell=True, same
    injection-safety principle as workspace/git_client.py.

    Deliberately does not decide success/failure by return code itself:
    tools vary in what a nonzero exit means (confirmed by testing —
    pylint exits with a bitmask reflecting issue severities found,
    ESLint exits 1 when it finds any lint problems; neither of those is
    "the tool crashed"). That decision belongs to each analyzer, which
    knows its own tool's exit-code conventions, not to this shared runner.
    """
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=stdin_data.encode() if stdin_data is not None else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise ToolExecutionError(f"{args[0]} timed out after {timeout}s.")

    return ProcessResult(
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
        returncode=process.returncode,
    )
