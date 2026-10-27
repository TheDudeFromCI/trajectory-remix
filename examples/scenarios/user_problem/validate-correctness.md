Validate ONLY factual correctness, literal constraints and fulfillment of the actual user request.

Use flow.yaml's checklist. Fragments retain event_id/field/offset; timing is resolved. Check entire dependent streams. context_windows maps IDs to [open,close] ticks; null means open. Resolve text_ref via content_pool. Request missing evidence. final_answer may follow an earlier task-solving reply.
