"""Explicit boundary for user-owned model/performance harnesses."""

import os


def require_user_machine(confirmed):
    if any(
        os.environ.get(key, "").lower() not in ("", "0", "false", "no")
        for key in ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD")
    ):
        raise ValueError("USER MACHINE ONLY: refusing CI invocation")
    if not confirmed:
        raise ValueError("USER MACHINE ONLY: explicitly supply --user-machine")
