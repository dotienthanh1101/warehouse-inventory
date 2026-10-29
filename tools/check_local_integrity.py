"""Check real inventory services using temporary SQLite warehouses only.

No PostgreSQL, camera, GPU, application file edits or clock changes.
Source modules are imported in this separate process with a temporary storage root.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

MODULES = [
    'database', 'warehouse_scope', 'box_inventory_service', 'duplicate_check',
    'duplicate_repository', 'daily_inventory_service', 'daily_session_service',
    'daily_reconciliation_service', 'session_finalize_service', 'day_closing_service', 'visual_similarity',
]
UTC = timezone.utc


class Clock(datetime):
    instant = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.wid = uuid4().hex
        self.scope = scopes.warehouse_scope(self.wid)
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__, None, None, None)
        Clock.instant = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for module in (days, sessions, closing, finalizing, recording):
            self.patches.enter_context(patch.object(module, 'datetime', Clock))
        # Refuse before initialize_database if this project's storage is not scoped.
        self.assertTrue(Path(os.fspath(database.DATABASE_PATH)).resolve().is_relative_to(TEMP_ROOT),
                        'Unsafe storage configuration: test DB would be outside temporary area')
        database.initialize_database()
        self.root = Path(scopes.STORAGE_ROOT) / self.wid
        (self.root / 'captures').mkdir(exist_ok=True)
        (self.root / 'data/reviews').mkdir(parents=True, exist_ok=True)
        self.day = days.create_day({i: 2 if i == 23 else 0 for i in range(1, 31)})['day_id']
        self.sid = sessions.create_daily_session(self.day, 'Isolated integrity fixture')['session_id']

    def sql(self, query, parameters=()):
        with database.get_connection() as c:
            return [dict(r) for r in c.execute(query, parameters).fetchall()]

    def scalar(self, query, parameters=()):
        with database.get_connection() as c:
            return c.execute(query, parameters).fetchone()[0]

    def prepare(self, quantity=2):
        rid = uuid4().hex
        boxes = [{'box_id': f'box-{i+1}', 'bounds': [20+i*100, 20, 90+i*100, 150],
                  'marker_id': 23, 'reviewed': True, 'note': ''} for i in range(quantity)]
        doc = {'review_id': rid, 'revision': 2, 'session_id': self.sid,
               'created_at': Clock.instant.isoformat(), 'image_url': f'/api/captures/{rid}.jpg',
               'width': 640, 'height': 480, 'analysis': {'markers': [], 'boxes': []},
               'human_review': {'revision': 2, 'boxes': boxes, 'assignments': {}, 'excluded_regions': []}}
        report = checks.create_check(self.sid, doc)
        decisions = [{'box_id': b['box_id'], 'decision': 'new'} for b in boxes]
        return doc, report['check_id'], decisions

    def record(self, prepared):
        doc, check_id, decisions = prepared
        return recording.record_review(self.sid, doc, 2, check_id, decisions)

    def finalize(self, count, empty=False):
        return finalizing.finalize_session(self.sid, confirm_full_coverage=True,
                                           expected_receipt_count=count, confirm_empty=empty)

    def close(self, count=1):
        return closing.finalize_day(self.day, confirm_close=True, expected_session_count=count)

    def parallel(self, calls):
        barrier = threading.Barrier(len(calls))
        values = [None] * len(calls)
        def run(index, fn):
            with scopes.warehouse_scope(self.wid):
                try:
                    barrier.wait(timeout=5)
                    values[index] = ('ok', fn())
                except Exception as exc:
                    values[index] = ('error', exc)
        threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=15)
        self.assertTrue(all(not t.is_alive() for t in threads), 'Concurrent call did not finish')
        return values

    def ordered(self, first, first_module, second, second_module):
        """Force both writer orderings while retaining actual SQLite locks."""
        acquired, attempted, release = threading.Event(), threading.Event(), threading.Event()
        values = [None, None]
        class Connection:
            def __init__(self, connection): self.connection = connection
            def __getattr__(self, name): return getattr(self.connection, name)
            def execute(self, query, *args):
                if query.strip().upper() == 'BEGIN IMMEDIATE':
                    name = threading.current_thread().name
                    if name == 'integrity-second': attempted.set()
                    result = self.connection.execute(query, *args)
                    if name == 'integrity-first':
                        acquired.set()
                        if not release.wait(10): raise TimeoutError('Fixture lock release timeout')
                    return result
                return self.connection.execute(query, *args)
        def run(i, fn):
            with scopes.warehouse_scope(self.wid):
                try: values[i] = ('ok', fn())
                except Exception as exc: values[i] = ('error', exc)
        with ExitStack() as stack:
            for module in {first_module, second_module}:
                original = module.get_connection
                @contextmanager
                def wrapped(original=original):
                    with original() as c: yield Connection(c)
                stack.enter_context(patch.object(module, 'get_connection', wrapped))
            a = threading.Thread(name='integrity-first', target=run, args=(0, first))
            b = threading.Thread(name='integrity-second', target=run, args=(1, second))
            a.start()
            try:
                self.assertTrue(acquired.wait(5), 'First writer did not acquire lock')
                b.start()
                self.assertTrue(attempted.wait(5), 'Second writer did not attempt lock')
            finally:
                release.set()
                a.join(timeout=15)
                if b.ident is not None: b.join(timeout=15)
            self.assertFalse(a.is_alive() or b.is_alive())
        return values

    def assert_error(self, outcome, text=None):
        self.assertEqual(outcome[0], 'error')
        self.assertIsInstance(outcome[1], ValueError)
        if text: self.assertIn(text, str(outcome[1]))

    def tearDown(self):
        # Independent stored-row invariants after every scenario, including failures.
        self.assertEqual(self.scalar('PRAGMA integrity_check'), 'ok')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM pragma_foreign_key_check'), 0)
        rows = self.sql('''SELECT r.box_count, COUNT(b.instance_id) AS actual
            FROM inventory_review_receipts r LEFT JOIN inventory_box_instances b ON b.review_id=r.review_id
            GROUP BY r.review_id''')
        self.assertTrue(all(r['box_count'] == r['actual'] for r in rows))

    def test_01_simultaneous_confirmation_adds_once(self):
        p = self.prepare()
        outcomes = self.parallel([lambda: self.record(p), lambda: self.record(p)])
        self.assertTrue(all(x[0] == 'ok' for x in outcomes))
        self.assertEqual(sorted(x[1]['new_box_count'] for x in outcomes), [0, 2])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_review_receipts'), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_box_instances'), 2)

    def test_02_record_first_rejects_stale_session_finalization(self):
        p = self.prepare()
        a, b = self.ordered(lambda: self.record(p), recording, lambda: self.finalize(0, True), finalizing)
        self.assertEqual(a[0], 'ok')
        self.assert_error(b, 'thay đổi')
        self.assertEqual(self.sql('SELECT status FROM inventory_sessions')[0]['status'], 'open')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_session_stock'), 0)

    def test_03_session_finalizes_first_rejects_late_record(self):
        p = self.prepare()
        a, b = self.ordered(lambda: self.finalize(0, True), finalizing, lambda: self.record(p), recording)
        self.assertEqual(a[0], 'ok')
        self.assert_error(b, 'kết thúc')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_review_receipts'), 0)
        self.assertEqual(self.scalar('SELECT SUM(quantity) FROM inventory_session_stock'), 0)

    def test_04_day_close_waits_for_session_finalization(self):
        self.record(self.prepare())
        a, b = self.ordered(lambda: self.finalize(1), finalizing, self.close, closing)
        self.assertEqual((a[0], b[0]), ('ok', 'ok'))
        self.assertEqual(b[1]['closing_total_boxes'], 2)

    def test_05_day_close_first_cannot_ignore_open_session(self):
        self.record(self.prepare())
        a, b = self.ordered(self.close, closing, lambda: self.finalize(1), finalizing)
        self.assert_error(a, 'chưa chốt')
        self.assertEqual(b[0], 'ok')
        self.assertEqual(days.get_day(self.day)['status'], 'open')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_day_closing_items'), 0)

    def test_06_new_session_first_blocks_day_close(self):
        self.record(self.prepare()); self.finalize(1)
        a, b = self.ordered(lambda: sessions.create_daily_session(self.day, 'New fixture session'), sessions,
                            self.close, closing)
        self.assertEqual(a[0], 'ok')
        self.assert_error(b, 'chưa chốt')
        self.assertEqual(days.get_day(self.day)['status'], 'open')

    def test_07_day_close_first_blocks_new_session(self):
        self.record(self.prepare()); self.finalize(1)
        a, b = self.ordered(self.close, closing,
                            lambda: sessions.create_daily_session(self.day, 'Must reject'), sessions)
        self.assertEqual(a[0], 'ok')
        self.assert_error(b, 'đã chốt')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_sessions'), 1)

    def test_08_parallel_session_finalize_is_idempotent(self):
        self.record(self.prepare())
        outcomes = self.parallel([lambda: self.finalize(1), lambda: self.finalize(1)])
        self.assertTrue(all(x[0] == 'ok' for x in outcomes))
        self.assertEqual(sorted(x[1]['already_finalized'] for x in outcomes), [False, True])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_session_stock'), 30)
        self.assertEqual(self.scalar('SELECT SUM(quantity) FROM inventory_session_stock'), 2)

    def test_09_parallel_day_finalize_is_idempotent(self):
        self.record(self.prepare()); self.finalize(1)
        outcomes = self.parallel([self.close, self.close])
        self.assertTrue(all(x[0] == 'ok' for x in outcomes))
        self.assertEqual(sorted(x[1]['already_closed'] for x in outcomes), [False, True])
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_day_closing_items'), 30)

    def test_10_receipt_and_instances_rollback_together(self):
        p = self.prepare()
        self.sql('''CREATE TRIGGER fixture_fail_instance BEFORE INSERT ON inventory_box_instances
            WHEN NEW.source_box_id='box-2' BEGIN SELECT RAISE(ABORT,'fixture insert failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError): self.record(p)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_review_receipts'), 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_box_instances'), 0)
        self.sql('DROP TRIGGER fixture_fail_instance')
        self.assertEqual(self.record(p)['new_box_count'], 2)

    def test_11_session_stock_and_closed_status_rollback_together(self):
        self.record(self.prepare())
        self.sql('''CREATE TRIGGER fixture_fail_session BEFORE UPDATE OF finalized_at ON inventory_day_sessions
            WHEN NEW.finalized_at IS NOT NULL BEGIN SELECT RAISE(ABORT,'fixture finalize failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError): self.finalize(1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_session_stock'), 0)
        self.assertEqual(self.sql('SELECT status,ended_at FROM inventory_sessions')[0], {'status':'open','ended_at':None})
        self.assertIsNone(self.sql('SELECT finalized_at FROM inventory_day_sessions')[0]['finalized_at'])
        self.sql('DROP TRIGGER fixture_fail_session')
        self.assertEqual(self.finalize(1)['recorded_total_boxes'], 2)

    def test_12_day_stock_and_closed_status_rollback_together(self):
        self.record(self.prepare()); self.finalize(1)
        self.sql('''CREATE TRIGGER fixture_fail_day BEFORE UPDATE OF status ON inventory_days
            WHEN NEW.status='closed' BEGIN SELECT RAISE(ABORT,'fixture closing failure'); END''')
        with self.assertRaises(sqlite3.IntegrityError): self.close()
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_day_closing_items'), 0)
        self.assertEqual(days.get_day(self.day)['status'], 'open')
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_session_stock'), 30)
        self.sql('DROP TRIGGER fixture_fail_day')
        self.assertEqual(self.close()['closing_total_boxes'], 2)

    def test_13_receipt_change_invalidates_previous_comparison(self):
        old = self.prepare(); other = self.prepare(1)
        self.record(other)
        with self.assertRaisesRegex(ValueError, 'ghi thêm'): self.record(old)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_review_receipts'), 1)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_box_instances'), 1)

    def test_14_draft_history_change_invalidates_previous_comparison(self):
        p = self.prepare(); other = self.prepare(1)
        path = self.root/'data/reviews'/f"{other[0]['review_id']}.json"
        path.write_text(json.dumps(other[0]), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Lịch sử'): self.record(p)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_review_receipts'), 0)

    def test_15_document_change_invalidates_previous_comparison(self):
        p = self.prepare(); p[0]['human_review']['boxes'][0]['note'] = 'Changed after comparison'
        with self.assertRaisesRegex(ValueError, 'đổi sau'): self.record(p)

    def test_16_stale_revision_cannot_record(self):
        doc, cid, decisions = self.prepare()
        with self.assertRaisesRegex(ValueError, 'thay đổi'):
            recording.record_review(self.sid, doc, 1, cid, decisions)

    def test_17_receipt_count_mismatch_cannot_finalize(self):
        self.record(self.prepare())
        with self.assertRaisesRegex(ValueError, 'thay đổi'): self.finalize(0)

    def test_18_corrupt_declared_count_cannot_finalize(self):
        self.record(self.prepare())
        self.sql('UPDATE inventory_review_receipts SET box_count=99')
        try:
            with self.assertRaisesRegex(ValueError, 'không khớp'): self.finalize(1)
            self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_session_stock'), 0)
        finally:
            self.sql('UPDATE inventory_review_receipts SET box_count=2')

    def test_19_old_receipt_readback_does_not_write_after_day_close(self):
        p = self.prepare(); self.record(p); self.finalize(1); self.close()
        result = self.record(p)
        self.assertTrue(result['already_recorded'])
        self.assertEqual(result['new_box_count'], 0)
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_box_instances'), 2)

    def test_20_day_count_change_requires_reload(self):
        self.record(self.prepare()); self.finalize(1)
        self.sid = sessions.create_daily_session(self.day, 'Second fixture')['session_id']
        self.finalize(0, True)
        with self.assertRaisesRegex(ValueError, 'thay đổi'): self.close(count=1)
        self.assertEqual(days.get_day(self.day)['status'], 'open')
        self.assertEqual(self.close(count=2)['closing_total_boxes'], 0)

    def test_21_vietnam_midnight_inherits_previous_closed_stock(self):
        self.record(self.prepare()); self.finalize(1)
        Clock.instant = datetime(2026, 10, 27, 16, 59, 59, tzinfo=UTC)
        old = self.close()
        self.assertEqual(old['business_date'], '2026-10-27')
        Clock.instant = datetime(2026, 10, 27, 17, 0, 0, tzinfo=UTC)
        new = days.create_day()
        self.assertEqual(new['business_date'], '2026-10-28')
        self.assertEqual(new['source_day_id'], self.day)
        self.assertEqual(new['opening_total_boxes'], 2)
        self.assertEqual(next(r for r in new['opening_items'] if r['marker_id']==23)['quantity'], 2)
        opened = sessions.create_daily_session(new['day_id'], 'Next-day fixture')
        self.assertEqual(opened['reference_total_boxes'], 2)
        self.assertEqual(opened['recorded_total_boxes'], 0)

    def test_22_unclosed_previous_day_blocks_new_day(self):
        Clock.instant = datetime(2026, 10, 27, 17, 0, 0, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, 'chưa chốt'): days.create_day()
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_days'), 1)

    def test_23_previous_date_cannot_open_new_session(self):
        self.finalize(0, True)
        Clock.instant = datetime(2026, 10, 27, 17, 0, 0, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, 'hôm nay'):
            sessions.create_daily_session(self.day, 'Must reject old date')
        # An open old day can still be closed to allow opening the new day.
        self.assertEqual(self.close()['status'], 'closed')
        self.assertEqual(days.create_day()['business_date'], '2026-10-28')

    def test_24_second_day_cannot_override_inherited_opening(self):
        self.record(self.prepare()); self.finalize(1); self.close()
        Clock.instant = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, 'không nhập lại'):
            days.create_day({i: 99 for i in range(1,31)})
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM inventory_days'), 1)

    def test_25_new_product_has_zero_next_day_opening(self):
        self.record(self.prepare()); self.finalize(1); self.close()
        self.sql("INSERT INTO packages(marker_id,package_code,product_code,product_name) VALUES (31,'KIEN-031','SP-031','Fixture new type')")
        Clock.instant = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)
        new = days.create_day()
        self.assertEqual(next(r for r in new['opening_items'] if r['marker_id']==31)['quantity'], 0)
        self.assertEqual(new['opening_total_boxes'], 2)

    def test_26_closed_same_date_cannot_be_reopened(self):
        self.finalize(0, True); self.close()
        with self.assertRaisesRegex(ValueError, 'không được mở lại'): days.create_day()


def main():
    parser = argparse.ArgumentParser(description='Kiem tra giao dich local trong kho tam; khong sua kho dang dung.')
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report-dir', type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    paths = {name: project/'backend/app'/f'{name}.py' for name in MODULES}
    if any(not p.is_file() for p in paths.values()):
        raise SystemExit('Khong tim thay du ma nguon. Chay voi --project DUONG_DAN_DU_AN.')
    baseline = {name: hashlib.sha256(p.read_bytes()).hexdigest() for name,p in paths.items()}
    print('Chi thu dich vu local voi SQLite va kho TAM. Khong doc kho that, PostgreSQL, camera hoac GPU.', flush=True)
    with tempfile.TemporaryDirectory(prefix='warehouse-integrity-') as temporary:
        os.environ['WAREHOUSE_STORAGE_ROOT'] = str(Path(temporary)/'warehouses')
        sys.path.insert(0, str(project))
        global database, scopes, days, sessions, closing, finalizing, recording, checks, TEMP_ROOT
        TEMP_ROOT = Path(temporary).resolve()
        from backend.app import database, warehouse_scope as scopes, daily_inventory_service as days
        from backend.app import daily_session_service as sessions, day_closing_service as closing
        from backend.app import session_finalize_service as finalizing, box_inventory_service as recording, duplicate_check as checks
        for name in MODULES:
            imported = sys.modules.get('backend.app.'+name)
            if imported is not None and Path(imported.__file__).resolve() != paths[name].resolve():
                raise SystemExit('Dung lai: module da nap khong thuoc ma nguon du an duoc chon.')
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntegrityTests)
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        print(stream.getvalue(), end='')
        unchanged = baseline == {name: hashlib.sha256(p.read_bytes()).hexdigest() for name,p in paths.items()}
        successful = result.wasSuccessful() and unchanged
        failed = [{'test':test.id().split('.')[-1], 'trace':trace} for test,trace in result.failures+result.errors]
        report = {'status':'passed' if successful else 'failed','scenario_count':result.testsRun,
                  'failures':failed,'source_sha256':baseline,'source_files_unchanged':unchanged,
                  'runtime':{'python':sys.version.split()[0],'sqlite':sqlite3.sqlite_version,
                             'opencv':sys.modules['cv2'].__version__},
                  'scope':['TC-12/20: ordered SQLite writer contention, duplicate submissions, stale revisions/history',
                           'NFR-04: SQLite ABORT fault injection and all-or-nothing inventory transactions',
                           'NFR-15: simulated Vietnam midnight, day inheritance and old-date session guards'],
                  'method':'Actual application service functions and real SQLite, isolated temporary warehouse per scenario. No fake transaction layer.',
                  'limitations':['Does not test HTTP/authentication middleware, browser UI, physical camera, AI accuracy or native PostgreSQL',
                                 'Clock patched only inside this test process; fault triggers only on temporary SQLite databases',
                                 'Only selected concurrency orderings and failure points are covered; not proof against all crashes/power loss']}
    report['temporary_data_cleaned'] = not Path(temporary).exists()
    output = (args.report_dir or project/'docs').resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output/('local_integrity_'+datetime.now(UTC).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]+'.json')
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('BAO CAO:',destination)
    print('DAT' if successful else 'CHUA DAT',f'{result.testsRun} tinh huong. Ma nguon giu nguyen:',unchanged)
    return 0 if successful else 1


if __name__ == '__main__':
    raise SystemExit(main())
