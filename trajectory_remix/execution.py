"""Opt-in Docker execution over immutable virtual files; replay never runs code."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from .tools import ToolError

OUTPUT_LIMIT = 32_768


def receipt(result):
    data = {k: v for k, v in result.items() if k != "receipt_sha256"}
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_execution(result, sandbox):
    if not isinstance(result, dict) or result.get("backend") != "docker":
        raise ToolError("Execution evidence requires the Docker backend")
    if not isinstance(result.get("executionId"), str) or not result["executionId"]:
        raise ToolError("Execution evidence requires executionId")
    if result.get("status") not in ("passed", "failed", "timed_out"):
        raise ToolError("Invalid execution status")
    if type(result.get("elapsed_ms")) is not int or result["elapsed_ms"] < 0:
        raise ToolError("Invalid execution duration")
    if not isinstance(result.get("image"), str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", result["image"]
    ):
        raise ToolError("Execution image must be pinned by content ID")
    if result.get("receipt_sha256") != receipt(result):
        raise ToolError("Execution receipt checksum mismatch")
    if not isinstance(result.get("inputs"), list) or not result["inputs"]:
        raise ToolError("Execution evidence requires immutable input references")
    seen = set()
    for ref in result["inputs"]:
        if not isinstance(ref, dict) or set(ref) != {"path", "version"}:
            raise ToolError("Invalid execution input reference")
        path = sandbox.path(ref["path"])
        if path != ref["path"] or path in seen:
            raise ToolError("Execution input paths must be canonical and unique")
        seen.add(path)
        if sandbox.artifact_key(path, ref["version"]) not in sandbox.artifacts:
            raise ToolError("Execution input artifact not found")
    exit_code = result.get("exit_code")
    if (
        "exit_code" not in result
        or type(result.get("timed_out")) is not bool
        or (exit_code is not None and type(exit_code) is not int)
    ):
        raise ToolError("Invalid execution exit status")
    expected = (
        "timed_out" if result["timed_out"] else "passed" if exit_code == 0 else "failed"
    )
    if result["status"] != expected or (exit_code is None) != result["timed_out"]:
        raise ToolError("Execution status does not match exit code and timeout")
    argv = result.get("argv")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 32
        or any(
            not isinstance(a, str) or not a or len(a) > 16_000 or "\x00" in a
            for a in argv
        )
    ):
        raise ToolError("Invalid recorded Python argv")
    if type(result.get("truncated")) is not bool:
        raise ToolError("Invalid execution output truncation flag")
    for stream in ("stdout", "stderr"):
        if (
            not isinstance(result.get(stream), str)
            or len(result[stream].encode()) > OUTPUT_LIMIT * 3
        ):
            raise ToolError("Invalid bounded execution output")
    return result


class DockerExecutor:
    """Fixed Python image, no network, read-only inputs/root, bounded resources."""

    def __init__(self, image="python:3.12-slim", docker="docker"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,199}", image):
            raise ValueError("Invalid execution image")
        self.image, self.docker = image, docker

    def __deepcopy__(self, memo):
        return self  # Stateless capability, not part of virtual snapshots.

    async def command(self, *args):
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_TLS",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            }
        }
        try:
            return await asyncio.create_subprocess_exec(
                self.docker,
                "--host=unix:///var/run/docker.sock",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as error:
            raise ToolError("Docker execution backend is unavailable") from error

    async def image_id(self):
        proc = await self.command("image", "inspect", "--format", "{{.Id}}", self.image)
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 10)
        except BaseException:
            proc.kill()
            await proc.wait()
            raise
        value = stdout.decode().strip()
        if proc.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ToolError(
                "Execution image is not installed; pull the configured image before running tests"
            )
        return value

    async def run(self, files, argv, timeout_seconds=10):
        if (
            not files
            or len(files) > 128
            or sum(len(f["content"].encode()) for f in files) > 2_000_000
        ):
            raise ToolError("Execution requires 1..128 files totaling at most 2 MB")
        if (
            not isinstance(argv, list)
            or not argv
            or len(argv) > 32
            or any(
                not isinstance(a, str) or not a or len(a) > 16_000 or "\x00" in a
                for a in argv
            )
        ):
            raise ToolError("Python argv requires 1..32 bounded nonempty strings")
        image = await self.image_id()
        container = "remix-exec-" + uuid.uuid4().hex
        started = time.monotonic()
        timed_out = False

        async def read(stream):
            retained, size, truncated = [], 0, False
            while block := await stream.read(8192):
                remaining = max(0, OUTPUT_LIMIT - size)
                if remaining:
                    retained.append(block[:remaining])
                size += min(len(block), remaining)
                truncated |= len(block) > remaining
            return b"".join(retained).decode("utf-8", errors="replace"), truncated

        with tempfile.TemporaryDirectory(prefix="remix-exec-") as temporary:
            root = Path(temporary)
            for file in files:
                relative = PurePosixPath(file["path"].lstrip("/"))
                if not relative.parts or ".." in relative.parts or "\\" in file["path"]:
                    raise ToolError("Invalid execution file path")
                target = root.joinpath(*relative.parts)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(file["content"], encoding="utf-8")
                except (OSError, ValueError) as error:
                    raise ToolError("Conflicting execution file paths") from error
                target.chmod(0o444)
            for directory in [root, *(p for p in root.rglob("*") if p.is_dir())]:
                directory.chmod(0o555)
            args = [
                "run",
                "--rm",
                "--name",
                container,
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "64",
                "--cpus",
                "1",
                "--memory",
                "128m",
                "--memory-swap",
                "128m",
                "--ulimit",
                "cpu=20:20",
                "--ulimit",
                "nofile=128:128",
                "--user",
                "65534:65534",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16777216,mode=1777",
                "--mount",
                f"type=bind,src={root},dst=/workspace,readonly",
                "--workdir",
                "/workspace",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
            ]
            for proxy in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
            ):
                args.extend(["--env", proxy + "="])
            args.extend([image, "python", "-B", "-s", *argv])
            proc = await self.command(*args)
            stdout_task = asyncio.create_task(read(proc.stdout))
            stderr_task = asyncio.create_task(read(proc.stderr))
            try:
                try:
                    await asyncio.wait_for(proc.wait(), timeout_seconds)
                except asyncio.TimeoutError:
                    timed_out = True
                finally:
                    if proc.returncode is None:
                        proc.kill()
                        await proc.wait()
                    cleanup = await self.command("rm", "--force", container)
                    try:
                        await asyncio.wait_for(cleanup.communicate(), 10)
                    except BaseException:
                        if cleanup.returncode is None:
                            cleanup.kill()
                        await cleanup.wait()
                        raise
                stdout, truncated_out = await stdout_task
                stderr, truncated_err = await stderr_task
            except BaseException:
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                raise
            finally:
                # TemporaryDirectory must be able to clean nested read-only dirs.
                for directory in [root, *(p for p in root.rglob("*") if p.is_dir())]:
                    directory.chmod(0o755)
        result = {
            "executionId": "execution-" + uuid.uuid4().hex,
            "backend": "docker",
            "image": image,
            "container_id": container,
            "argv": argv,
            "inputs": [{k: f[k] for k in ("path", "version")} for f in files],
            "exit_code": None if timed_out else proc.returncode,
            "timed_out": timed_out,
            "status": (
                "timed_out"
                if timed_out
                else "passed" if proc.returncode == 0 else "failed"
            ),
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated_out or truncated_err,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }
        result["receipt_sha256"] = receipt(result)
        return result
