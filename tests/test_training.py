import json
import pytest
from trajectory_remix.compiler import chunks
from trajectory_remix.training import query_batch, read_plans


def test_adapter_never_exposes_own_or_same_tick_target(compiler,sample):
    sample['replay']['1']=[{'op':'generate','window':'a','content':'xyz'}, {'op':'generate','window':'b','content':'abc'}]
    plan=compiler.compile(sample);batch=query_batch(plan,plan['rows'])
    targets=[r for r in plan['rows'] if r['loss_weight']>0]
    memory=[r for r in plan['rows'] if r['durable']]
    for q,mask in zip(targets,batch['attention_allowed']):
        for k,allowed in zip(memory,mask):
            assert not (allowed and k['tick']==q['tick'] and k['phase']=='D')
            assert not (allowed and k['tick']>q['tick'])
    assert all(p!=l for p,l in zip(batch['query_previous_token_ids'][:2],batch['labels'][:2]))


def test_reassemble_export_and_reject_missing_chunk(compiler,sample,tmp_path):
    sample['replay']['1']=[{'op':'generate','window':'a','content':'long content'}]
    p=compiler.compile(sample);cs=list(chunks(p,3));file=tmp_path/'export.jsonl'
    file.write_text('\n'.join(json.dumps(c) for c in cs))
    assert list(read_plans(file))[0]['rows']==p['rows']
    file.write_text('\n'.join(json.dumps(c) for c in cs[1:]))
    with pytest.raises(ValueError,match='prefix'):list(read_plans(file))


def test_missing_suffix_chunk_is_detected(compiler,sample,tmp_path):
    sample['replay']['1']=[{'op':'generate','window':'a','content':'long content'}]
    cs=list(chunks(compiler.compile(sample),3));file=tmp_path/'truncated.jsonl'
    file.write_text('\n'.join(json.dumps(c) for c in cs[:-1]))
    with pytest.raises(ValueError,match='missing suffix'):list(read_plans(file))
