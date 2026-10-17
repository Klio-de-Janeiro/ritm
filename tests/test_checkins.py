import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from ritm.store import Store, ValidationError
from ritm.mobile_sync import SyncEngine


class CheckinTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=datetime(2026,10,18,9,tzinfo=timezone(timedelta(hours=3)))
        self.path=Path(self.temp.name)/'tasks.sqlite3'
        self.store=Store(self.path,clock=lambda:self.now)
        self.task=self.store.create_task(dict(title='спорт',time='09:00',reminder=True,recurrence='every_3_hours'))
        self.row=self.store.state('2026-10-18')['tasks'][0]

    def test_missed_check_can_be_corrected_without_duplicating_xp(self):
        self.assertEqual(self.task['title'],'Спорт')
        self.assertEqual(self.row['slots'],['09:00','12:00','15:00','18:00','21:00'])
        self.now=self.now.replace(hour=12)
        row=self.store.state('2026-10-18')['tasks'][0]
        self.assertEqual([item['status'] for item in row['checkins']],['missed','pending','future','future','future'])
        self.store.check(row['id'],'09:00')
        self.store.check(row['id'],'09:00')
        self.assertEqual(self.store.state('2026-10-18')['summary']['xp'],10)
        with self.assertRaises(ValidationError):self.store.check(row['id'],'15:00')
        self.store.check(row['id'],'12:00')
        state=Store(self.path,clock=lambda:self.now).state('2026-10-18')
        self.assertEqual(state['summary']['xp'],20)
        self.assertEqual(len(state['history']),2)
        self.assertEqual(len(self.store.day_details('2026-10-18')['done']),2)
        self.store.check(row['id'],'09:00',False)
        self.assertEqual(self.store.state('2026-10-18')['summary']['xp'],10)

    def test_each_slot_reminds_even_after_an_earlier_slot_is_done(self):
        self.assertEqual(len(self.store.due_notifications()),1)
        self.store.check(self.row['id'],'09:00')
        self.assertEqual(self.store.due_notifications(),[])
        self.now=self.now.replace(hour=12)
        self.assertEqual(len(self.store.due_notifications()),1)
        self.assertEqual(self.store.due_notifications(),[])

    def test_day_change_archive_and_schedule_edits_preserve_recorded_units(self):
        self.store.check(self.row['id'],'09:00')
        self.store.update_task(self.task['id'],dict(time='10:00',repeat_until='22:00'))
        self.assertEqual(self.store.state('2026-10-18')['tasks'][0]['slots'][0],'09:00')
        self.now+=timedelta(days=1,hours=1)
        rows=self.store.state('2026-10-19')['tasks']
        self.assertEqual(rows[0]['slots'][0],'10:00')
        self.assertEqual(rows[0]['checks'],{})
        self.store.archive_task(self.task['id'])
        self.assertEqual(self.store.state('2026-10-19')['summary']['xp'],10)

    def test_different_slot_edits_merge_and_same_slot_conflicts(self):
        self.now=self.now.replace(hour=15)
        engine=SyncEngine(self.store,'test')
        def op(slot):return dict(id=str(uuid.uuid4()),kind='check',task_id=self.task['id'],date='2026-10-18',time=slot,completed=True,completed_at=self.now.isoformat(),before=None)
        first,second=op('09:00'),op('12:00')
        result=engine.exchange(dict(protocol=2,operations=[first,second]))
        self.assertEqual(result['accepted'],[first['id'],second['id']])
        self.assertEqual(len(result['occurrences'][0]['checks']),2)
        conflict=engine.exchange(dict(protocol=2,operations=[op('09:00')]))
        self.assertEqual(len(conflict['conflicts']),1)
        self.assertEqual(self.store.state('2026-10-18')['summary']['xp'],20)

    def test_legacy_completed_day_keeps_its_original_ten_xp(self):
        with self.store.connection(write=True) as db:
            db.execute("UPDATE occurrences SET slots=NULL,checks=NULL,status='done',completed_at=?",(self.now.isoformat(),))
        state=Store(self.path,clock=lambda:self.now).state('2026-10-18')
        self.assertEqual(state['summary']['xp'],10)
        self.assertEqual(state['tasks'][0]['checkins'],[])

    def test_custom_end_of_day_is_validated(self):
        with self.assertRaises(ValidationError):self.store.update_task(self.task['id'],{'repeat_until':'08:00'})
        self.assertEqual(self.store.update_task(self.task['id'],{'repeat_until':'18:00'})['repeat_until'],'18:00')

    def test_partial_day_archived_today_stays_in_progress_and_phone_snapshot(self):
        self.store.check(self.row['id'],'09:00')
        self.store.archive_task(self.task['id'])
        self.assertEqual(self.store.state('2026-10-18')['summary']['xp'],10)
        snapshot=SyncEngine(self.store,'laptop').exchange(dict(protocol=2,operations=[]))
        self.assertEqual(snapshot['occurrences'][0]['status'],'cancelled')
        self.assertIn('09:00',snapshot['occurrences'][0]['checks'])
