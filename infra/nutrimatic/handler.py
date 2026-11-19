"""AWS Lambda entry point for Nutrimatic searches.

Each invocation runs the same ``cgi-search.py`` the web instances used to run,
so a heavy ``find-expr`` exhausts only this Lambda sandbox's memory, never
Daphne's. Django (``microsites.views.nutrimatic_search``) invokes the function
synchronously and renders ``body`` itself.

Event:    {"query_string": "q=...&start=0"} or {"warmup": true}
Response: {"status": "ok" | "timeout" | "output_limit" | "error",
           "exit_code": int | None, "duration": float,
           "body_b64": str (only for "ok")}
"""

import base64
import os
import signal
import subprocess
import sys
import time

TASK_ROOT = os.environ.get("LAMBDA_TASK_ROOT", os.path.dirname(os.path.abspath(__file__)))
CGI_SCRIPT = os.path.join(TASK_ROOT, "cgi-search.py")
FIND_EXPR = os.path.join(TASK_ROOT, "find-expr")
INDEX = os.path.join(TASK_ROOT, "wiki-merged.packed.index")

# Django gives up after its own deadline; finish earlier so it gets a reply.
DEADLINE_SECONDS = float(os.environ.get("NUTRIMATIC_DEADLINE_SECONDS", "18"))
MAX_OUTPUT_BYTES = 4 * 1024 * 1024  # Lambda's synchronous response cap is 6 MB.
MAX_QUERY_STRING = 4096


def _kill_group(proc):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            continue


def handler(event, context):
    started = time.monotonic()
    if event.get("warmup"):
        return {"status": "warm", "exit_code": None, "duration": 0.0}

    query_string = str(event.get("query_string") or "")[:MAX_QUERY_STRING]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "REQUEST_METHOD": "GET",
        "QUERY_STRING": query_string,
        "NUTRIMATIC_FIND_EXPR": FIND_EXPR,
        "NUTRIMATIC_INDEX": INDEX,
    }
    proc = subprocess.Popen(
        [sys.executable, CGI_SCRIPT],
        env=env,
        cwd="/tmp",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    chunks, size, status = [], 0, "ok"
    try:
        # cgi-search.py streams results; read until EOF or a limit.
        os.set_blocking(proc.stdout.fileno(), True)
        while True:
            remaining = DEADLINE_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                status = "timeout"
                break
            chunk = _read_with_timeout(proc.stdout, remaining)
            if chunk is None:
                status = "timeout"
                break
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_OUTPUT_BYTES:
                status = "output_limit"
                break
            chunks.append(chunk)
    finally:
        if status != "ok":
            _kill_group(proc)
        else:
            try:
                proc.wait(timeout=max(0.1, DEADLINE_SECONDS - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                status = "timeout"
                _kill_group(proc)
        proc.stdout.close()

    duration = round(time.monotonic() - started, 3)
    exit_code = proc.returncode
    if status == "ok" and exit_code != 0:
        status = "error"
    result = {"status": status, "exit_code": exit_code, "duration": duration}
    if status == "ok":
        result["body_b64"] = base64.b64encode(b"".join(chunks)).decode("ascii")
    print(f"nutrimatic status={status} exit_code={exit_code} duration={duration:.3f} bytes={size} query={query_string[:300]!r}")
    return result


def _read_with_timeout(stream, timeout):
    import select

    ready, _, _ = select.select([stream], [], [], timeout)
    if not ready:
        return None
    return os.read(stream.fileno(), 65536)
