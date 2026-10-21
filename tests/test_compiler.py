import copy
import json
from pathlib import Path

import pytest
from tokenizers import Tokenizer, models
from trajectory_remix.compiler import Compiler, CompileError, chunks, visible_to


def test_reactive_fixture(compiler):
    sample = json.loads((Path(__file__).parents[1] / 'examples/reactive-sample.json').read_text())
    p = compiler.compile(sample)
    warning, done = p['anchors']['4']['tick'], p['anchors']['5']['tick']
    reason = next(e for e in p['events'] if e['op']=='generate' and e['window']=='reasoning')
    assert reason['start'] == warning+1 < done
    assert all(not r['loss_mask'] for r in p['rows'] if r['op']=='append')
    assert p['windows'][2]['close'] is not None


def test_bos_noop_eos_masks(compiler, sample):
    sample['replay']['2']=[{'op':'generate','window':'a','content':'x<|eos|>'}]
    p=compiler.compile(sample)
    assert [r['token_id'] for r in p['rows'] if r['op']=='open']==[compiler.ids['bos']]*2
    assert all(r['loss_weight']==.1 and not r['durable'] for r in p['rows'] if r['op']=='noop')
    eos=next(r for r in p['rows'] if r['token_id']==compiler.ids['eos'])
    assert eos['loss_mask']==1 and eos['tick']==3
    assert next(w for w in p['windows'] if w['id']=='a')['close']==3
    assert compiler.frame(p,2)['windows']['a']['text']=='x'


def test_same_tick_parallel_causality(compiler, sample):
    sample['replay']['1']=[{'op':'generate','window':'a','content':'xy'}, {'op':'generate','window':'b','content':'zw'}]
    p=compiler.compile(sample)
    a=next(r for r in p['rows'] if r['window']=='a' and r['tick']==1 and r['phase']=='D')
    b=next(r for r in p['rows'] if r['window']=='b' and r['tick']==1 and r['phase']=='D')
    later=next(r for r in p['rows'] if r['window']=='b' and r['tick']==2)
    assert not visible_to(a,b,p['windows'])
    assert not visible_to(b,a,p['windows'])
    assert visible_to(later,a,p['windows'])
    assert not visible_to(a,a,p['windows'])


def test_external_same_tick_and_closed_keys(compiler, sample):
    sample['replay']['1']=[{'op':'append','window':'a','source':'tool','content':'x'}, {'op':'generate','window':'b','content':'yz'}]
    sample['replay']['2']=[{'op':'generate','window':'a','content':'<|eos|>'}]
    sample['replay']['3']=[{'op':'generate','window':'b','content':'!'}]
    p=compiler.compile(sample)
    external=next(r for r in p['rows'] if r['op']=='append')
    q1=next(r for r in p['rows'] if r['tick']==1 and r['phase']=='D')
    q3=next(r for r in p['rows'] if r['tick']==3 and r['phase']=='D')
    assert visible_to(q1,external,p['windows'])
    assert not visible_to(q3,external,p['windows'])
    assert not visible_to(q3,next(r for r in p['rows'] if r['op']=='noop'),p['windows'])


def test_chunks_do_not_split_ticks(compiler,sample):
    sample['replay']['1']=[{'op':'generate','window':'a','content':'abc'}, {'op':'generate','window':'b','content':'def'}]
    p=compiler.compile(sample); cs=list(chunks(p,2))
    assert [r for c in cs for r in c['rows']]==p['rows']
    assert all(c['token_ids']==[r['token_id'] for r in c['rows']] for c in cs)
    for t in range(p['max_tick']+1):
        assert sum(any(r['tick']==t for r in c['rows']) for c in cs)==1
    assert cs[1]['requires_prefix_through_row']==cs[0]['rows'][-1]['index']


def test_mask_overrides(compiler,sample):
    sample['replay']['1']=[{'op':'append','window':'a','source':'tool','content':'abc','loss':{'mask':'include','weight':.5}}, {'op':'generate','window':'b','content':'xyz','loss':{'mask':'exclude'}}]
    p=compiler.compile(sample)
    assert all(r['loss_weight']==.5 for r in p['rows'] if r['op']=='append')
    assert all(r['loss_weight']==0 for r in p['rows'] if r['op']=='generate')


def test_segment_masks_and_escaped_anchor(compiler,sample):
    sample['replay']['1']=[{'op':'generate','window':'a','segments':[{'text':'a','loss':{'mask':'exclude'}},{'text':'b','loss':{'mask':'include'}}]}, {'op':'append','window':'b','source':'tool','content':r'hello\{N_TICK:7}'}]
    p=compiler.compile(sample)
    assert not p['anchors']
    assert [r['loss_weight'] for r in p['rows'] if r['op']=='generate']==[0,1]
    assert '{N_TICK:7}' in compiler.frame(p,2)['windows']['b']['text']


def test_anchor_schedule_stream_rate_and_empty(compiler,sample):
    sample['replay']['1']=[{'op':'append','window':'a','source':'tool','delivery':'stream','tokens-per-tick':2,'content':'ab{N_TICK:1}cd{N_TICK:2}'}]
    sample['replay']['{N_TICK:1}+1']=[{'op':'generate','window':'b','content':'x{N_TICK:3}'}]
    sample['replay']['{N_TICK:3}+1']=[{'op':'generate','window':'b','content':'<|eos|>'}]
    p=compiler.compile(sample)
    assert {k:v['tick'] for k,v in p['anchors'].items()}=={'1':1,'2':2,'3':2}


def test_instant_and_start_anchors(compiler,sample):
    sample['replay']['3']=[{'op':'append','window':'a','source':'user','content':'{N_TICK:1}hello{N_TICK:2}'}]
    p=compiler.compile(sample)
    assert p['anchors']['1']['tick']==p['anchors']['2']['tick']==3


def test_key_order_is_irrelevant(compiler,sample):
    sample['replay']['2']=[{'op':'generate','window':'a','content':'x'}]
    other=copy.deepcopy(sample);other['replay']=dict(reversed(list(other['replay'].items())))
    assert compiler.compile(sample)==compiler.compile(other)


@pytest.mark.parametrize('content,message', [('x{N_TICKS:1}','Invalid marker'),('x{N_TICK:-1}','Invalid marker'),('x{N_TICK:a}','Invalid marker'),('x{N_TICK:1}{N_TICK:1}','Duplicate anchor'),('<|bos|>','BOS is automatic'),('<|eos|>x','after EOS')])
def test_content_errors(compiler,sample,content,message):
    sample['replay']['1']=[{'op':'generate','window':'a','content':content}]
    with pytest.raises(CompileError,match=message):compiler.compile(sample)


@pytest.mark.parametrize('replay,message', [
 ({'{N_TICK:99}+1':[{'op':'open','window':'c'}]},'Undefined anchor'),
 ({'{N_TICK:1}+1':[{'op':'generate','window':'a','content':'x{N_TICK:1}'}]},'Cyclic'),
 ({'{N_TICK:1}-1':[{'op':'generate','window':'a','content':'x'}]},'Invalid tick'),
 ({'1':[{'op':'close','window':'a'}]},'Allowed operations'),
 ({'1':[{'op':'generate','window':'never','content':'x'}]},'unopened/closed'),
 ({'1':[{'op':'open','window':'a'}]},'already opened'),
 ({'1':[{'op':'generate','window':'a','content':'xxx'}],'2':[{'op':'generate','window':'a','content':'x'}]},'Overlapping'),
 ({'1':[{'op':'append','window':'a','source':'tool','content':'x'},{'op':'generate','window':'a','content':'y'}]},'Overlapping'),
 ({'1':[{'op':'generate','window':'a','content':'<|eos|>'}],'2':[{'op':'append','window':'a','source':'tool','content':'x'}]},'unopened/closed'),
 ({'1':[{'op':'generate','window':'a','content':'x{N_TICK:1}'}],'{N_TICK:1}':[{'op':'generate','window':'b','content':'x'}]},'phase-D'),
 ({'1':[{'op':'append','window':'a','source':'tool','content':'x{N_TICK:1}'}],'{N_TICK:1}':[{'op':'open','window':'c'}]},'phase B'),
 ({'1':[{'op':'generate','window':'a','content':'x','loss':{'weight':-1}}]},'nonnegative'),
 ({'1':[{'op':'generate','window':'a','content':'x','loss':None}]},'loss must be'),
])
def test_schedule_errors(compiler,sample,replay,message):
    sample['replay'].update(replay)
    with pytest.raises(CompileError,match=message):compiler.compile(sample)


def test_mid_token_marker_rejected(sample):
    tok=Tokenizer(models.WordLevel({'<|bos|>':0,'<|eos|>':1,'<|noop|>':2,'<|unk|>':3,'transform':4},unk_token='<|unk|>'))
    tok.add_special_tokens(['<|bos|>','<|eos|>','<|noop|>'])
    c=Compiler(tok)
    sample['replay']['1']=[{'op':'generate','window':'a','content':'trans{N_TICK:9}form'}]
    with pytest.raises(CompileError,match='splits a tokenizer token'):c.compile(sample)


def test_multiple_instant_appends_order(compiler,sample):
    sample['replay']['1']=[{'op':'append','window':'a','source':'user','content':'ab'},{'op':'append','window':'a','source':'user','content':'cd'}]
    p=compiler.compile(sample)
    assert compiler.frame(p,1)['windows']['a']['text']=='abcd'
    keys=[r for r in p['rows'] if r['op']=='append']
    assert visible_to(keys[1],keys[0],p['windows'])
    assert not visible_to(keys[0],keys[1],p['windows'])
