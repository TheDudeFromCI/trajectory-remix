# Worker protocol

The six workers were created fresh (`fork_turns: none`) with the inherited default agent configuration. Each received only an assigned opaque mailbox path, the helper commands below, and equivalent short instructions summarized here. They were not told workflow labels, paired seeds, comparisons, or any other worker's task/data.

> Act as the text-completion API for your assigned request stream. Treat each current request as stateless: use only its supplied messages as factual context; do not import prior answers/private notes unless supplied again. Follow the requested role, JSON schema, output format and output cap. Use only supplied simulated observations; do not perform real tool actions. Review evidence honestly and follow the request's missing-context protocol when needed. Read/write only your mailbox, except executing the shared helper/interpreter. Do not inspect the repository, sibling mailboxes, experiment plans/results, or network services; do not spawn agents. Continue until DONE.

Poll:

```sh
python mailbox_worker.py --mailbox /path/to/assigned-mailbox next
```

Read the full `messages` returned with its `request_id` and `max_tokens`. Write the answer text to a file in that mailbox. For structured requests, this is the requested JSON object itself, with no Markdown fences. Submit:

```sh
python mailbox_worker.py --mailbox /path/to/assigned-mailbox respond \
  --request 000001 --file /path/to/assigned-mailbox/answer.txt
```

The helper checks the declared surrogate output-token cap and refuses overlong output; it does not enforce JSON grammar or judge semantics. Poll again after successful submission; WAIT means poll again, DONE terminates the worker. The exact API prompts and submitted responses, including formatting failures and retry feedback, are preserved in each case's `api-records.jsonl` in the ZIP.

Each worker retained its own conversation/tool history, so statelessness was an instruction rather than a fresh runtime context for every completion. Mailbox separation was an instruction/routing boundary, not OS sandboxing. Temperature and response-format fields were recorded by the proxy; it did not constrain the workers' actual sampling or implement grammar-constrained generation.
