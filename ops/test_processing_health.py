import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from autonomy import assess_processing,health_exit_code,cycle,TARGETS

def snapshot(**overrides):
    value=dict(messages=0,last_created_at=None,pending=0,processing=0,failed=0,
        ready_pending_over_30m=0,deferred=0,expired_processing_over_5m=0,
        deep_pending=0,deep_done_today=0,expired_deep_over_5m=0,budget_used_micro_usd=0)
    value.update(overrides)
    return value

class ProcessingTests(unittest.TestCase):
    def test_idle_ingestion_is_not_evidence_of_collector_failure(self):
        result=assess_processing(snapshot(last_created_at="2000-01-01T00:00:00Z"),5,3)
        self.assertFalse(result['requires_attention'])
    def test_deep_daily_cap_is_not_a_stall(self):
        result=assess_processing(snapshot(deep_pending=100,deep_done_today=3),5,3)
        self.assertEqual(result['deep_state'],'daily_limited')
        self.assertFalse(result['requires_attention'])
    def test_disabled_deep_is_not_a_stall(self):
        result=assess_processing(snapshot(deep_pending=100),5,0)
        self.assertEqual(result['deep_state'],'disabled')
        self.assertFalse(result['requires_attention'])
    def test_scheduled_retry_is_not_ready_backlog(self):
        result=assess_processing(snapshot(pending=1,deferred=1),5,3)
        self.assertFalse(result['requires_attention'])
    def test_budget_limit_does_not_restart_worker(self):
        result=assess_processing(snapshot(ready_pending_over_30m=5,budget_used_micro_usd=5000000),5,3)
        self.assertEqual(result['budget_state'],'exhausted')
        self.assertFalse(result['requires_attention'])
        self.assertFalse(result['restart_requested'])
    def test_due_backlog_requires_diagnosis_without_claiming_hang(self):
        result=assess_processing(snapshot(ready_pending_over_30m=1),5,3)
        self.assertEqual(result['codes'],['classification_due_backlog'])
        self.assertFalse(result['restart_requested'])
    def test_expired_claim_is_visible_even_when_budget_is_exhausted(self):
        result=assess_processing(snapshot(expired_processing_over_5m=1,budget_used_micro_usd=5000000),5,3)
        self.assertEqual(result['codes'],['expired_classification_claim'])
    def test_deep_expired_claim_requires_attention(self):
        result=assess_processing(snapshot(expired_deep_over_5m=1),5,3)
        self.assertEqual(result['codes'],['expired_deep_claim'])
    def test_unknown_budget_is_not_assumed_exhausted(self):
        result=assess_processing(snapshot(ready_pending_over_30m=1),None,None)
        self.assertEqual(result['budget_state'],'unknown')
        self.assertTrue(result['requires_attention'])
    def test_attention_does_not_restart_or_increment_container_failures(self):
        now=time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'state.json').write_text(json.dumps({'last_backup':{'verified_at':now}}))
            report=assess_processing(snapshot(ready_pending_over_30m=1),5,3)
            with patch('autonomy.inspect',return_value={'State':{'Running':True}}),\
                 patch('autonomy.check_container',side_effect=lambda name,item:{'name':name,'ok':True}),\
                 patch('autonomy.run',return_value=b'100') as command,\
                 patch('autonomy.processing_snapshot',return_value=report):
                result=cycle(True,root)
            self.assertFalse(result['ok'])
            self.assertEqual(health_exit_code(result),2)
            self.assertFalse(any(call.args[0][1]=='restart' for call in command.call_args_list))
            state=json.loads((root/'state.json').read_text())
            self.assertTrue(all(state[n]['failures']==0 for n in TARGETS))
    def test_service_or_backup_failure_stays_critical(self):
        result={'ok':False,'database':{'ok':True},'processing':{'requires_attention':True},
                'containers':[{'ok':True} for _ in TARGETS]}
        self.assertEqual(health_exit_code(result),2)
        result['backup_error']='safe_error'
        self.assertEqual(health_exit_code(result),1)
        del result['backup_error']
        result['containers'][0]['ok']=False
        self.assertEqual(health_exit_code(result),1)
