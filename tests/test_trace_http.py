"""Real ASGI middleware/routes + SQLite. Authentication and AI are explicit fixtures."""
import copy
import json
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_inventory_trace import TraceFixture
from backend.app import warehouse_scope as scopes, database


class FixtureAuthStore:
    def __init__(self,identities):
        self.identities=identities
        self.settings=SimpleNamespace(origins=('http://127.0.0.1:5173',))
    def authenticate(self,token):
        if token not in self.identities:raise HTTPException(401,'Fixture session required')
        return self.identities[token]
    def csrf_token(self,token):return 'test-csrf-'+token


class TraceHTTPTests(TraceFixture):
    @classmethod
    def setUpClass(cls):
        stub=types.ModuleType('backend.app.box_analysis');stub.analyze_frame=lambda frame: {}
        previous=sys.modules.get(stub.__name__)
        sys.modules[stub.__name__]=stub
        try:
            from backend.app.daily_api import app
        finally:
            if previous is None:sys.modules.pop(stub.__name__,None)
            else:sys.modules[stub.__name__]=previous
        cls.app=app

    def setUp(self):
        super().setUp()
        other={**self.identity,'user_id':uuid4().hex,'warehouse_id':uuid4().hex,'username':'trace-fixture-b'}
        with scopes.warehouse_scope(other['warehouse_id']):database.initialize_database()
        self.app.state.auth_store=FixtureAuthStore({'test-user-a':self.identity,'test-user-b':other,
             'test-admin':{'role':'admin','warehouse_id':None,'user_id':uuid4().hex}})
        self.app.state.account_gates={}
        # No context manager: lifespan/PG/camera/GPU workers intentionally never start.
        self.client=TestClient(self.app);self.addCleanup(self.client.close)
        self.headers={'Origin':'http://127.0.0.1:5173','Cookie':'warehouse_session=test-user-a','X-CSRF-Token':'test-csrf-test-user-a'}

    def test_route_auth_tenant_and_cache(self):
        self.record(self.prepare());self.finish();url=f'/api/inventory/days/{self.day}/trace?marker_id=23'
        self.assertEqual(self.client.get(url).status_code,401)
        self.assertEqual(self.client.get(url,headers={'Cookie':'warehouse_session=test-user-b'}).status_code,404)
        self.assertEqual(self.client.get(url,headers={'Cookie':'warehouse_session=test-admin'}).status_code,403)
        r=self.client.get(url,headers=self.headers);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.headers['cache-control'],'no-store');self.assertEqual(r.json()['warehouse_id'],self.wid)

    def test_real_correction_and_confirmation_capture_authenticated_actor(self):
        prepared=self.prepare();doc=copy.deepcopy(prepared[0]);boxes=doc['human_review']['boxes']
        doc.update(revision=1,human_review=None,correction_history=[],review_status='draft',inventory_recorded=False)
        rid=doc['review_id'];path=self.folder/'data/reviews'/f'{rid}.json';path.write_text(json.dumps(doc))
        r=self.client.put(f'/api/reviews/{rid}/correction',headers=self.headers,json={'revision':1,'boxes':boxes,'assignments':{},'note':''})
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['human_review']['reviewed_by']['user_id'],self.identity['user_id'])
        r=self.client.post(f'/api/reviews/{rid}/duplicate-check',headers=self.headers,json={'session_id':self.sid,'revision':2})
        self.assertEqual(r.status_code,200,r.text)
        body={'session_id':self.sid,'revision':2,'check_id':r.json()['check_id'],'confirm_new_boxes':True,
              'decisions':[{'box_id':b['box_id'],'decision':'new'} for b in boxes]}
        forged={**body,'confirmed_by':{'user_id':'forged-user'}}
        self.assertEqual(self.client.post(f'/api/reviews/{rid}/confirm',headers=self.headers,json=forged).status_code,422)
        r=self.client.post(f'/api/reviews/{rid}/confirm',headers=self.headers,json=body)
        self.assertEqual(r.status_code,200,r.text)
        first=json.loads(self.sql('SELECT reviewed_payload_json FROM inventory_review_receipts')[0][0])
        self.assertEqual(first['confirmation']['confirmed_by']['user_id'],self.identity['user_id'])
        again=self.client.post(f'/api/reviews/{rid}/confirm',headers=self.headers,json=body)
        self.assertTrue(again.json()['receipt']['already_recorded'])
        self.assertEqual(json.loads(self.sql('SELECT reviewed_payload_json FROM inventory_review_receipts')[0][0]),first)
        self.finish();r=self.client.get(f'/api/inventory/days/{self.day}/trace?marker_id=23',headers=self.headers)
        self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['receipts'][0]['confirmed_by']['user_id'],self.identity['user_id'])

    def test_schema_validates_trace_query(self):
        r=self.client.get(f'/api/inventory/days/{self.day}/trace?marker_id=250',headers=self.headers)
        self.assertEqual(r.status_code,422)
        r=self.client.get(f'/api/inventory/days/{self.day}/trace?marker_id=23&limit=51',headers=self.headers)
        self.assertEqual(r.status_code,422)

    def test_origin_and_csrf_still_required_for_correction(self):
        doc,_,_=self.prepare();rid=doc['review_id']
        self.assertEqual(self.client.put(f'/api/reviews/{rid}/correction',headers={'Cookie':'warehouse_session=test-user-a'},json={}).status_code,403)
        self.assertEqual(self.client.put(f'/api/reviews/{rid}/correction',headers={**self.headers,'X-CSRF-Token':'invalid'},json={}).status_code,403)
