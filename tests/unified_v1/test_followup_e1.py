import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unified_v1.contracts import PROFILES, ContractError
from unified_v1.adapters import PersistentPredictor, AxAdapter, VeilAdapter, DetectionEngine
from unified_v1.replay import SnapshotGateAdapter
from unified_v1.runtime import profile_status, build_engine

class RuntimeTests(unittest.TestCase):
    def test_profiles_honest_readiness(self):
        self.assertEqual(PROFILES['quality3'],('ax','veil','klue'))
        self.assertEqual(profile_status('quality3',dict(ax='LOCAL_MODEL_PASS',veil='LOCAL_MODEL_PASS',klue='LOCAL_MODEL_PASS')),'REAL_READY')
        self.assertEqual(profile_status('quality4',{}),'MISSING')
        self.assertEqual(profile_status('own2',{}),'BLOCKED')

    def test_no_gate_reinference_and_team_does_not_call_own(self):
        calls=[]
        def raw(t):calls.append(t);return []
        engine=DetectionEngine('own2',{'ax':AxAdapter('mock',raw),'veil':VeilAdapter('mock',raw)})
        ev=engine.detect('안전교육 안내',input_kind='document')
        gate=SnapshotGateAdapter().check('안전교육 안내',ev)
        self.assertEqual(gate['decision'],'READY_FOR_LOCAL_PSEUDONYMIZATION');self.assertEqual(len(calls),2)
        def own(t):self.fail('unselected own model called')
        ev=build_engine('team2',own_worker=own).detect('합성',input_kind='query')
        self.assertFalse(ev.ready)
        self.assertEqual(SnapshotGateAdapter().check('합성',ev)['decision'],'BLOCKED')

    def test_resident_worker_reused_concurrency_and_fault(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'worker.py'
            p.write_text('import sys,json,os\nfor line in sys.stdin:\n r=json.loads(line)\n r.pop("text")\n r["candidates"]=[]\n r["metrics"]={"pid":os.getpid()}\n print(json.dumps(r),flush=True)\n')
            worker=PersistentPredictor([str(Path(sys.executable).absolute()),str(p)],'cfg',timeout=2)
            with worker:
                with concurrent.futures.ThreadPoolExecutor(2) as pool:
                    self.assertEqual(list(pool.map(worker,['합성A','합성B'])),[[],[]])
                pid=worker.last_metrics['pid'];worker('합성C');self.assertEqual(worker.last_metrics['pid'],pid)
            with self.assertRaisesRegex(ContractError,'WORKER_FAULTED'):worker('합성D')
            p.write_text('import time\ntime.sleep(1)')
            w=PersistentPredictor([str(Path(sys.executable).absolute()),str(p)],'cfg',timeout=.02)
            with self.assertRaisesRegex(ContractError,'MODEL_TIMEOUT'):w('합성')
            with self.assertRaisesRegex(ContractError,'WORKER_FAULTED'):w('합성')

    def test_ablation_worker_receives_only_selected_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'worker.py'
            p.write_text('import sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n r.pop("text")\n r["candidates"]={s:[] for s in r["sources"]}\n r["metrics"]={"selected_sources":r["sources"]}\n print(json.dumps(r),flush=True)\n')
            with PersistentPredictor([str(Path(sys.executable).absolute()),str(p)],'cfg',timeout=2) as worker:
                for profile,selected in [('ax_klue','ax'),('veil_klue','veil')]:
                    engine=build_engine(profile,own_worker=worker,klue_worker=lambda t:[])
                    self.assertTrue(engine.detect('합성',input_kind='document').ready)
                    self.assertEqual(worker.last_metrics['selected_sources'],[selected])
