"""Session-owned background tokenizer migration with pollable progress."""

import asyncio
import json

from tokenizers import Tokenizer

from .compiler import Compiler
from .storage import StorageConflict, TokenizerMigrationError


class TokenizerMigration:
    def __init__(self, store, runner, activate):
        self.store, self.runner, self.activate = store, runner, activate
        self.task = None
        self.status = {"status": "idle", "converted": 0, "total": 0}
        runner.tokenizer_migration = self

    @property
    def running(self):
        return self.status["status"] == "running"

    def start(self, data, current):
        if self.running:
            raise ValueError("Tokenizer migration is already running")
        if self.runner.status["running"] or self.runner.scenario_runner.status["running"]:
            raise ValueError("Stop generation before migrating the tokenizer")
        revision = data.get("expected_revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        if revision != self.store.info()[0]:
            raise StorageConflict("Dataset changed; reload before migrating the tokenizer")
        special = data.get("special", {})
        if not isinstance(special, dict) or set(special) - {"bos", "eos", "noop"} or any(not isinstance(v, str) or not v for v in special.values()):
            raise ValueError("special may contain only nonempty bos, eos and noop token spellings")
        raw = data.get("tokenizer")
        if not isinstance(raw, (str, dict)):
            raise ValueError("tokenizer must be tokenizer.json text or an object")
        try:
            tokenizer = Tokenizer.from_str(raw if isinstance(raw, str) else json.dumps(raw, allow_nan=False))
        except Exception as error:
            raise ValueError(f"Invalid tokenizer configuration: {error}") from error
        candidate = Compiler(tokenizer, **{**current.special, **special},
                             max_window_slots=current.max_window_slots,
                             slot_policy=current.slot_policy, tools=current.tools)
        self.status = {"status": "running", "converted": 0, "total": len(self.store.samples)}
        self.task = asyncio.create_task(self._run(candidate, revision))
        return dict(self.status)

    async def _run(self, candidate, revision):
        def progress(converted, total):
            self.status.update(converted=converted, total=total)

        try:
            revision = await asyncio.to_thread(self.store.migrate_tokenizer, candidate, revision, progress)
            self.activate(candidate, revision)
            self.status.update(status="completed", revision=revision, sha256=candidate.fingerprint)
        except Exception as error:
            details = {}
            if isinstance(error, TokenizerMigrationError):
                details = {"sample_id": error.sample_id, "diagnostics": [error.diagnostic]}
            self.status.update(status="failed", error=str(error), **details)
