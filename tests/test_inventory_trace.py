"""Real SQLite, immutable source receipts and per-warehouse isolation; no AI/PG."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
from PIL import Image

from backend.app import warehouse_scope as scopes, database, daily_inventory_service as days
from backend.app import daily_session_service as sessions, session_finalize_service as finalizing
from backend.app import day_closing_service as closing, box_inventory_service as recording, duplicate_check as checks
from backend.app.inventory_trace_service import get_day_trace, image
from backend.app.inventory_provenance import actor_snapshot


class TraceFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.patch=patch.object(scopes,'STORAGE_ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
        self.wid=uuid4().hex;self.scope=scopes.warehouse_scope(self.wid);self.scope.__enter__();self.addCleanup(self.scope.__exit__,None,None,None)
        database.initialize_database();self.folder=self.root/self.wid
        (self.folder/'captures').mkdir(exist_ok=True);(self.folder/'data/reviews').mkdir(parents=True,exist_ok=True)
        self.identity={'user_id':uuid4().hex,'username':'trace-fixture-user','display_name':'Người thử',
                       'role':'user','warehouse_id':self.wid,'token_hash':'not-for-provenance','password_hash':'not-for-provenance'}
        self.day=days.create_day({i:2 if i==23 else 0 for i in range(1,31)})['day_id']
        self.sid=sessions.create_daily_session(self.day,'Phiên thử truy vết')['session_id']

    def prepare(self,quantity=2):
        rid=uuid4().hex;name=rid+'.jpg';Image.new('RGB',(640,480),'white').save(self.folder/'captures'/name)
        boxes=[{'box_id':f'box-{i+1}','bounds':[20+i*100,20,90+i*100,150],'marker_id':23,'reviewed':True,'note':''} for i in range(quantity)]
        human={'revision':2,'saved_at':'2026-10-29T00:00:00+00:00','reviewed_by':actor_snapshot(self.identity),
               'boxes':boxes,'assignments':{},'excluded_regions':[]}
        doc={'review_id':rid,'revision':2,'session_id':self.sid,'created_at':'2026-10-29T00:00:00+00:00',
             'image_url':'/api/captures/'+name,'width':640,'height':480,'analysis':{'markers':[],'boxes':[]},
             'human_review':human,'correction_history':[copy.deepcopy(human)]}
        report=checks.create_check(self.sid,doc)
        decisions=[{'box_id':b['box_id'],'decision':'new'} for b in boxes]
        return doc,report['check_id'],decisions

    def record(self,prepared,actor=True):
        doc,check_id,decisions=prepared
        return recording.record_review(self.sid,doc,2,check_id,decisions,
            confirmed_by=actor_snapshot(self.identity) if actor else None)

    def finalize(self,count=1):
        return finalizing.finalize_session(self.sid,confirm_full_coverage=True,expected_receipt_count=count,confirm_empty=True)

    def finish(self):self.finalize();closing.finalize_day(self.day,confirm_close=True,expected_session_count=1)

    def sql(self,query,args=()):
        with database.get_connection() as c:return c.execute(query,args).fetchall()


class InventoryTraceTests(TraceFixture):
    def test_trace_reads_actor_revision_history_and_image_from_receipt(self):
        doc,check_id,decisions=self.prepare();self.record((doc,check_id,decisions));self.finish()
        data=get_day_trace(self.day,23);r=data['receipts'][0]
        self.assertEqual(r['revision'],2);self.assertEqual(r['confirmed_by']['user_id'],self.identity['user_id'])
        self.assertTrue(r['image_available']);self.assertEqual(len(r['correction_history']),1)
        self.assertEqual(r['correction_history'][0]['reviewed_by']['user_id'],self.identity['user_id'])
        self.assertEqual(r['new_count'],2);self.assertEqual([x['decision'] for x in r['decisions']],['new','new'])
        self.assertNotIn('token_hash',r['confirmed_by']);self.assertNotIn('password_hash',r['confirmed_by'])

    def test_changed_or_missing_json_draft_cannot_rewrite_trace(self):
        doc,check_id,decisions=self.prepare();self.record((doc,check_id,decisions));self.finish()
        before=get_day_trace(self.day,23)
        (self.folder/'data/reviews'/f"{doc['review_id']}.json").write_text('{"revision":999}')
        self.assertEqual(get_day_trace(self.day,23),before)

    def test_legacy_actor_is_explicitly_missing(self):
        prepared=self.prepare();prepared[0]['human_review'].pop('reviewed_by');prepared[0]['correction_history'][0].pop('reviewed_by')
        prepared=(prepared[0],checks.create_check(self.sid,prepared[0])['check_id'],prepared[2])
        self.record(prepared,actor=False);self.finish();r=get_day_trace(self.day,23)['receipts'][0]
        self.assertIsNone(r['confirmed_by']);self.assertIsNone(r['correction_history'][0]['reviewed_by'])

    def test_read_only_does_not_change_database_bytes(self):
        self.record(self.prepare());self.finish()
        path=Path(os.fspath(database.DATABASE_PATH))
        # Ensure WAL is checkpointed only for the fixture baseline.
        self.sql('PRAGMA wal_checkpoint(TRUNCATE)')
        before=hashlib.sha256(path.read_bytes()).hexdigest();get_day_trace(self.day,23)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)

    def test_cross_warehouse_day_is_unavailable(self):
        self.record(self.prepare());self.finish()
        with scopes.warehouse_scope(uuid4().hex):
            database.initialize_database()
            with self.assertRaises(LookupError):get_day_trace(self.day,23)

    def test_missing_evidence_is_reported(self):
        prepared=self.prepare();self.record(prepared);self.finish()
        (self.folder/'captures'/Path(prepared[0]['image_url']).name).unlink()
        self.assertFalse(get_day_trace(self.day,23)['receipts'][0]['image_available'])

    def test_bad_snapshot_fails_closed(self):
        self.record(self.prepare());self.finish();self.sql('UPDATE inventory_review_receipts SET reviewed_payload_json=?',('{broken',))
        with self.assertRaises(ValueError):get_day_trace(self.day,23)

    def test_count_mismatch_is_not_reported_as_zero(self):
        self.record(self.prepare());self.finish();self.sql('UPDATE inventory_session_stock SET quantity=1 WHERE session_id=? AND marker_id=23',(self.sid,))
        with self.assertRaises(ValueError):get_day_trace(self.day,23)

    def test_missing_stock_row_is_not_fabricated_as_zero(self):
        self.record(self.prepare());self.finish()
        self.sql('DELETE FROM inventory_session_stock WHERE session_id=? AND marker_id=23',(self.sid,))
        with self.assertRaises(ValueError):get_day_trace(self.day,23)

    def test_snapshot_missing_recorded_box_fails_closed(self):
        self.record(self.prepare());self.finish()
        row=self.sql('SELECT review_id,reviewed_payload_json FROM inventory_review_receipts')[0]
        doc=json.loads(row['reviewed_payload_json']);doc['human_review']['boxes']=[]
        self.sql('UPDATE inventory_review_receipts SET reviewed_payload_json=? WHERE review_id=?',(json.dumps(doc),row['review_id']))
        with self.assertRaises(ValueError):get_day_trace(self.day,23)

    def test_sessions_are_separate_snapshots_not_added(self):
        self.record(self.prepare(2));self.finalize();first=self.sid
        self.sid=sessions.create_daily_session(self.day,'Phiên sau')['session_id']
        self.record(self.prepare(1));self.finalize();closing.finalize_day(self.day,confirm_close=True,expected_session_count=2)
        data=get_day_trace(self.day,23);self.assertEqual([x['quantity'] for x in data['sessions']],[2,1])
        self.assertEqual(data['report_row']['closing_quantity'],1)
        self.assertEqual(data['receipts'][0]['new_count'],1)
        self.assertEqual(get_day_trace(self.day,23,first)['receipts'][0]['new_count'],2)

    def test_mixed_duplicate_and_new_keep_reference_and_paginate(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        self.record(self.prepare(1))
        old_id=self.sql('SELECT instance_id FROM inventory_box_instances')[0][0]
        engine=SimpleNamespace(embed=lambda scene,boxes:{b['box_id']:None for b in boxes},
                               match_scenes=lambda a,b:None,devices={'test-fixture-no-model'})
        measured={'outcome':'suspected_duplicate','note':'Test fixture comparison','inliers':20,'appearance':{'crop':.9}}
        with patch.object(checks,'comparison_engine',lambda:nullcontext(engine)),patch.object(checks,'compare_regions',return_value=measured):
            doc,check_id,_=self.prepare(2)
        decisions=[{'box_id':'box-1','decision':'duplicate','reference_instance_id':old_id,'note':'Test fixture same object'},
                   {'box_id':'box-2','decision':'new','note':'Test fixture distinct object'}]
        self.record((doc,check_id,decisions));self.finalize(2);closing.finalize_day(self.day,confirm_close=True,expected_session_count=1)
        result=get_day_trace(self.day,23,limit=1,offset=1)
        self.assertEqual(result['total'],2);self.assertEqual(len(result['receipts']),1)
        receipt=result['receipts'][0];self.assertEqual(receipt['new_count'],1)
        d=next(d for d in receipt['decisions'] if d['decision']=='duplicate')
        self.assertEqual(d['reference']['instance_id'],old_id);self.assertTrue(d['reference']['image_available'])
        self.assertEqual(result['report_row']['closing_quantity'],2)

    def test_actor_rejects_wrong_warehouse_and_admin(self):
        for identity in [{**self.identity,'warehouse_id':uuid4().hex},{**self.identity,'role':'admin'},{}]:
            with self.subTest(identity=identity.get('role')),self.assertRaises(ValueError):actor_snapshot(identity)

    def test_bad_image_url_is_never_returned_as_external_link(self):
        for url in ['https://example.com/a.jpg','/api/captures/../secret.jpg','/api/captures/a.jpg?token=bad']:
            with self.subTest(url=url),self.assertRaises(ValueError):image(url)

    def test_foreign_session_and_invalid_pages_rejected(self):
        self.record(self.prepare());self.finish()
        with self.assertRaises(LookupError):get_day_trace(self.day,23,uuid4().hex)
        with self.assertRaises(ValueError):get_day_trace(self.day,23,limit=0)
        with self.assertRaises(ValueError):get_day_trace(self.day,23,offset=-1)
