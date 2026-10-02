import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from demo.fake_business_system import Store, normalize_ticket, TRIM
from test_store import ticket

ROOT = Path(__file__).resolve().parents[1]


class RegressionTests(unittest.TestCase):
    def test_legacy_records_do_not_gain_invented_attempt_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            store = Store(path)
            store.register(ticket())
            store.triage(dict(ticket_id='T-1',attempt_id=0,route='AUTO',reason='AUTO_ALLOWED'))
            with closing(sqlite3.connect(path)) as db:
                db.execute('DROP TABLE crm_cases')
                db.execute('CREATE TABLE crm_cases(ticket_id TEXT PRIMARY KEY,case_id TEXT,draft_reply TEXT)')
                db.execute("INSERT INTO crm_cases VALUES('T-1','CASE-T-1','LEGACY DRAFT')")
                db.execute("UPDATE tickets SET status='AUTO_ROUTED',record_id='CASE-T-1' WHERE ticket_id='T-1'")
                db.commit()
            migrated = Store(path)
            self.assertIsNone(migrated.list_records('crm')[0]['attempt_id'])
            for action in [lambda:migrated.register(ticket()), lambda:migrated.write_failed('T-1',0),
                           lambda:migrated.write('crm',dict(ticket_id='T-1',attempt_id=0,draft_reply='NEW'))]:
                with self.assertRaisesRegex(ValueError,'INVALID_ATTEMPT'):
                    action()
            self.assertEqual(migrated.list_records('crm')[0]['draft_reply'],'LEGACY DRAFT')

    def test_normalization_parity(self):
        values = [ticket('PARITY')]
        for char in TRIM + '\u001c\u0085\u200b':
            for field in ['body', 'customer_email', 'subject', 'ticket_id', 'source']:
                values.append({**ticket('PARITY'), field: char + ticket('PARITY')[field] + char})
        values.extend([
            {**ticket(), 'body': '\U0001f600' * 2500},
            {**ticket(), 'body': '\U0001f600' * 2501},
            {**ticket(), 'subject': '\U0001f600' * 100},
            {**ticket(), 'subject': '\U0001f600' * 101},
            {**ticket(), 'customer_email': 'A\u0130@Example.Test'},
            {**ticket(), 'customer_email': ' ' * 255 + 'P@example.test'},
            {**ticket(), 'customer_email': 'a\u0085b@example.test'},
        ])
        js = """const fs=require('fs'); const fn=new Function('$input',fs.readFileSync('workflow_code/validate.js','utf8'));
        process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(body=>fn({first:()=>({json:{body}})})[0].json)));"""
        result = subprocess.run(['node', '-e', js], input=json.dumps(values), capture_output=True,
                                text=True, encoding='utf-8', cwd=ROOT, check=True)
        outputs = json.loads(result.stdout)
        for raw, got in zip(values, outputs):
            with self.subTest(raw=repr(raw)):
                try:
                    normalized = normalize_ticket(raw)
                except ValueError:
                    self.assertFalse(got['valid'])
                else:
                    self.assertTrue(got['valid'])
                    self.assertEqual(got['ticket'], normalized)
        self.assertEqual(outputs[0]['ticket']['customer_email'], 'person@example.test')
        self.assertEqual(normalize_ticket({**ticket(), 'body':'text\u001c'})['body'], 'text\u001c')
        self.assertEqual(normalize_ticket({**ticket(), 'body':'\ufefftext\ufeff'})['body'], 'text')

    def test_intermediate_duplicates_are_authoritative_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.db'
            store = Store(path)
            store.register(ticket())
            duplicate = Store(path).register(ticket())
            self.assertEqual(duplicate['status'], 'RECEIVED')
            self.assertIsNone(duplicate['record_id'])
            store.triage(dict(ticket_id='T-1', attempt_id=0, route='AUTO', reason='AUTO_ALLOWED'))
            duplicate = Store(path).register(ticket())
            self.assertEqual(duplicate['status'], 'AI_CLASSIFIED')
            self.assertEqual(duplicate['attempt_id'], 0)
            self.assertEqual(store.list_records('crm'), [])

    def test_old_attempt_cannot_mutate_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'state.db')
            old = dict(ticket_id='T-1', attempt_id=0, route='AUTO', reason='AUTO_ALLOWED',
                       category='order_status', confidence=.95, draft_reply='OLD DRAFT')
            new = dict(old, attempt_id=1, category='technical', confidence=.9, draft_reply='NEW DRAFT')
            self.assertEqual(store.register(ticket())['attempt_id'], 0)
            store.triage(old)
            store.write_failed('T-1', 0)
            with self.assertRaisesRegex(ValueError, 'INVALID_STATE'):
                store.write('crm', old)
            self.assertEqual(store.register(ticket())['attempt_id'], 1)
            before = store.get('T-1')
            self.assertIsNone(before['category'])
            for attempt in [None, True, -1, '1']:
                with self.assertRaisesRegex(ValueError, 'INVALID_ATTEMPT'):
                    store.triage(dict(new, attempt_id=attempt))
            with self.assertRaisesRegex(ValueError, 'INVALID_STATE'):
                store.triage(old)
            store.triage(new)
            expected = store.get('T-1')
            for action in [lambda: store.write('crm', old), lambda: store.write('review', old),
                           lambda: store.write_failed('T-1', 0), lambda: store.triage(old)]:
                with self.assertRaisesRegex(ValueError, 'INVALID_STATE'):
                    action()
                self.assertEqual(store.get('T-1'), expected)
            store.write('crm', new)
            store.write('crm', new)
            self.assertEqual(store.list_records('crm'), [dict(ticket_id='T-1', case_id='CASE-T-1',
                             draft_reply='NEW DRAFT', attempt_id=1)])
            self.assertEqual(store.get('T-1')['category'], 'technical')
            self.assertEqual(store.write_failed('T-1', 1)['status'], 'AUTO_ROUTED')


if __name__ == '__main__':
    unittest.main()
