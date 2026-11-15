"""USER-MACHINE ONLY: small endpoint-configurable local-LLM smoke cases.

No benchmarks, CI hook, hidden hosted endpoint, tool execution or compilation.
Invoke explicitly; merely importing/listing cases never calls a model.
"""
import argparse
import asyncio
import json
import platform
import subprocess
from pathlib import Path

from trajectory_remix.graph.user_validation import require_user_machine
from trajectory_remix.graph.bulk import BulkService
from trajectory_remix.graph.generation import GenerationService
from trajectory_remix.graph.jobs import JobService
from trajectory_remix.graph.models import ProjectSettings
from trajectory_remix.graph.provider import OpenAITransport
from trajectory_remix.graph.sampling import SamplerService
from trajectory_remix.graph.storage import GraphStore


def smoke_plan(case):
    windows = {
        'room': dict(purpose='chatroom', agent_writable=False),
        'reason': dict(purpose='reasoning', agent_writable=True),
        'call': dict(purpose='tool', agent_writable=True),
        'read': dict(purpose='tool', agent_writable=True),
        'document': dict(purpose='document', agent_writable=False)}
    def task(ident, kind, window, after=(), instruction='', **envelope):
        return dict(id=ident, envelope=dict(kind=kind, window=window, after=list(after), **envelope), instruction=instruction)
    tasks = [task('start', 'chat_message', 'room', chat={'speaker': 'Alice'}, instruction='Ask the agent to coordinate a small release. Include a concrete deadline and an unresolved acceptance requirement.')]
    if case == 'multi-user-wakeup':
        tasks.extend([
            task('bob', 'chat_message', 'room', ['start'], chat={'speaker': 'Bob'}, instruction='As Bob, clarify your ownership of acceptance testing, without claiming tests already ran.'),
            task('consider', 'agent_text', 'reason', ['bob'], instruction='Consider the currently observed release constraints.'),
            task('alarm', 'external_text', 'document', ['consider'], instruction='Author an external acceptance-watcher alarm with concrete failure evidence. This is a startup/watcher observation, not a tool response. No in-sample request is needed.'),
            task('return', 'chat_message', 'room', ['alarm'], chat={'speaker': 'Alice'}, instruction='Return after the autonomous update with a relevant question, grounded in current observations.')])
    previous = tasks[-1]['id']
    if case in {'partial-instant', 'partial-streamed', 'document-replacement'}:
        tasks.extend([
            task('prefix', 'tool_call', 'call', [previous], tool='chat', call_id='message', insert_tool_end=False,
                 instruction='Begin a chat-tool JSON request with an open content string. Leave it intentionally incomplete; a lookup will arrive before finishing.'),
            task('lookup', 'tool_call', 'read', ['prefix'], tool='read', call_id='lookup', close_window=True,
                 instruction='Author JSON arguments requesting the release acceptance document.'),
            task('observation', 'tool_response', 'document', ['lookup'], call_id='lookup', insert_response_end=False,
                 delivery={'mode': 'streamed' if case == 'partial-streamed' else 'instant'},
                 instruction='Author the first fragment of a concrete free-form acceptance document; include a failed check and its evidence. The response is authored, not executed.'),
            task('result-end', 'tool_response', 'document', ['observation'], call_id='lookup', insert_response_begin=False,
                 delivery={'mode': 'streamed' if case == 'partial-streamed' else 'instant'},
                 instruction='Finish the same observation with nested JSON-looking diagnostic text and an unresolved TODO. Keep identifiers consistent.'),
            task('suffix', 'tool_call', 'call', ['result-end'], tool='chat', call_id='message', insert_tool_begin=False, close_window=True,
                 instruction='Finish the earlier incomplete chat JSON content string and object using the retrieved evidence. Do not duplicate the prefix.')])
        if case == 'document-replacement':
            tasks.append(task('replacement', 'external_text', 'document', ['suffix'], replace_body=True,
                instruction='Replace the visible document body with a short current TODO snapshot retaining necessary facts. Do not claim completed work without evidence.'))
    else:
        tasks.append(task('think', 'agent_text', 'reason', [previous], instruction='Decide a supported next coordination step and state uncertainty. Never claim tool execution without an observation.'))
        tasks.append(dict(task('send', 'tool_call', 'call', ['think'], tool='chat', call_id='message', close_window=True,
            echo_chat=case not in {'filtered-chat', 'rejected-chat'}, instruction='Write a truthful progress message using the chat input schema.'), arguments='json'))
        if case == 'filtered-chat':
            tasks.append(task('filtered', 'chat_message', 'room', ['send'], chat={'speaker': 'agent'}, instruction='Author delivery of the submitted chat message with one sensitive term replaced by [redacted]. Automatic echo was disabled; this is the custom authored outcome.'))
        if case == 'rejected-chat':
            tasks.append(task('rejection', 'tool_response', 'document', ['send'], call_id='message', instruction='Author a rate-limit failure result. No message reached the chatroom. Do not execute a rate limiter.'))
    tasks.append(task('continue', 'agent_text', 'reason', [tasks[-1]['id']], close_window=True,
                      instruction='Respond to currently visible evidence and report truthful episode status. Do not resurrect closed/replaced or parallel text.'))
    return dict(windows=windows, tasks=tasks)


CASES = ['ordinary', 'multi-user-wakeup', 'partial-instant', 'partial-streamed',
         'document-replacement', 'filtered-chat', 'rejected-chat']


async def run(args):
    require_user_machine(args.user_machine)
    settings = ProjectSettings.model_validate_json(args.config.read_text())
    if not args.output.is_dir():
        args.output.mkdir(parents=True)
    path = args.output / 'project.sqlite'
    GraphStore.initialize(path, settings)  # Refuse overwriting a previous run.
    store = GraphStore(path)
    jobs = JobService(store, active_limit=2)
    gen = GenerationService(jobs, SamplerService(jobs), BulkService(jobs), OpenAITransport())
    results = []
    report = dict(acceptance_passed=False, quality='requires human inspection', performance='not measured',
        python=platform.python_version(), seed=args.seed, cases=args.cases,
        models={key: dict(model=e.model, concurrency=e.concurrency, retries=e.retries) for key,e in settings.endpoints.items()},
        endpoint_roles=settings.endpoint_roles, generation=settings.generation.model_dump(), results=results)
    try:
        report['commit'] = subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        report['commit'] = 'unknown (installed package)'
    for case in args.cases:
        job = gen.start([{'seed': args.seed}], {'plan': smoke_plan(case)})
        task = asyncio.create_task(gen.run(jobs, job))
        while not task.done():
            await asyncio.sleep(.2)
            status = jobs.inspect(job)
            if status['paused_endpoints'] or status['state'] == 'paused':
                if status['state'] == 'running':
                    jobs.control(job, 'pause')
                print(f'{case}: paused; inspect project.sqlite in graph-studio and explicitly Resume. No quality failure inferred.')
                break
        await task
        attempts = jobs.attempts(job)
        for attempt in attempts:
            try:
                source = store.read(attempt['trajectory_id']).source
            except KeyError:
                source = None
            results.append(dict(case=case, job=job, attempt=attempt['id'], trajectory=attempt['trajectory_id'],
                state=jobs.inspect(job)['state'], outcome=attempt['outcome'],
                stage=source.metadata.get('stage') if source else None,
                evaluations=source.metadata.get('evaluations', []) if source else []))
        (args.output / 'report.json').write_text(json.dumps(report, indent=2))
        if jobs.inspect(job)['state'] != 'completed':
            break
    print(f'Inspect {path} and {args.output / "report.json"}. Quality needs human sample inspection; no benchmark was performed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user-machine', action='store_true')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=['ordinary'])
    parser.add_argument('--seed', type=int, default=137)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except ValueError as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
