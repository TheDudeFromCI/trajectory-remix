# Synthetic tools and chat

Tools are project input JSON schemas without executable implementations or output
schemas. Authors/models supply observations explicitly; schema mismatches in
submitted arguments warn and free-form outputs are never schema-validated.
See [source fields](GRAPH-SOURCE.md), [tool/chat lesson](../trajectory_remix/guides/graph-tools-chat.md)
and [generation recipes](GRAPH-GENERATION.md) for fragmented requests, submission,
abandonment, independent result framing/delivery, and optional chat echoes.

The former executable sandbox, invisible mutations and tick anchors are removed.
