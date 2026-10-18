import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ritm.store import Store, ValidationError
from ritm.mobile_sync import SyncEngine


class GraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 10, 16, 21, tzinfo=timezone(timedelta(hours=3)))
        self.path = Path(self.temp.name) / 'tasks.sqlite3'
        self.store = Store(self.path, clock=lambda: self.now)
        self.task = self.store.create_task({'title':'Лекция по физике','time':'21:00','reminder':True})
        self.row = self.store.state('2026-10-16')['tasks'][0]

    def at(self, hour, minute=0, second=0):
        self.now = self.now.replace(day=17, hour=hour, minute=minute, second=second)
        return self.store.due_notifications()

    def test_two_hour_extension_survives_restart_and_delivers_two_signals(self):
        self.assertEqual(len(self.store.due_notifications()), 1)
        self.assertEqual(self.at(0), [])
        self.assertTrue(self.store.state('2026-10-17')['tasks'][0]['in_grace'])
        self.assertEqual(self.at(0,59), [])
        self.assertEqual(len(self.at(1)), 1)
        self.assertEqual(self.at(1,30), [])
        self.store = Store(self.path, clock=lambda: self.now)
        self.assertEqual(self.at(1,59), [])
        self.assertEqual(len(self.at(2,0,3)), 1)
        self.assertEqual(self.at(2,1), [])
        self.assertEqual(self.at(3), [])
        self.assertTrue(self.store.state('2026-10-17')['unfinished'][0]['can_dismiss'])

    def test_completion_stops_extra_signals_and_counts_once(self):
        self.at(0,30)
        self.store.complete(self.row['id'])
        self.assertEqual(self.at(1), [])
        self.assertEqual(self.at(2), [])
        self.assertEqual(self.store.state('2026-10-17')['summary']['xp'],10)
        self.assertEqual(self.store.day_details('2026-10-16')['unfinished'],[])

    def test_dismissal_is_eligible_only_after_grace_and_can_be_undone(self):
        self.at(1,59)
        with self.assertRaises(ValidationError): self.store.dismiss(self.row['id'])
        self.at(2,1)
        self.store.dismiss(self.row['id'])
        state = self.store.state('2026-10-17')
        self.assertIsNotNone(state['tasks'][0]['dismissed_at'])
        self.assertEqual(state['unfinished'],[])
        self.assertEqual(state['summary']['xp'],-5)
        self.assertEqual(self.at(2,2), [])
        with self.assertRaises(ValidationError): self.store.complete(self.row['id'])
        self.store.dismiss(self.row['id'], False)
        self.store.complete(self.row['id'])
        self.assertEqual(self.store.state('2026-10-17')['summary']['xp'],10)

    def test_no_old_signals_after_sleep_and_global_notification_switch(self):
        self.assertEqual(self.at(3), [])
        self.store.update_settings({'notifications':False})
        self.assertEqual(self.at(1), [])

    def test_plain_task_also_receives_deadline_signals(self):
        self.store.archive_task(self.task['id'])
        self.store.create_task({'title':'Конспект'})
        self.assertEqual(len(self.at(1)),1)
        self.assertEqual(len(self.at(2)),1)

    def test_explicit_final_day_is_used_and_daily_tasks_have_separate_grace(self):
        self.store.update_task(self.task['id'],{'reminder_end_date':'2026-10-18'})
        self.store.due_notifications()
        self.assertEqual(self.at(1), [])
        self.now = self.now.replace(day=19,hour=1)
        self.assertEqual(len(self.store.due_notifications()),1)
        self.store.create_task({'title':'Спорт','recurrence':'daily','start_date':'2026-10-19'})
        self.now = self.now.replace(day=20,hour=1)
        self.assertEqual(len(self.store.due_notifications()),1)

    def test_offline_dismiss_and_undo_sync_without_xp(self):
        self.at(3)
        engine=SyncEngine(self.store,'laptop')
        op={'id':str(uuid.uuid4()),'task_id':self.task['id'],'kind':'dismiss','date':self.row['date'],
            'dismissed':True,'dismissed_at':self.now.isoformat(),'before':{'status':'pending','dismissed_at':None}}
        response=engine.exchange({'protocol':2,'operations':[op]})
        self.assertEqual(response['accepted'],[op['id']])
        self.assertEqual(response['occurrences'][0]['dismissed_at'],self.now.isoformat())
        self.assertEqual(engine.exchange({'protocol':2,'operations':[op]})['conflicts'],[])
        self.assertEqual(self.store.state('2026-10-17')['summary']['xp'],-5)
        undo=dict(op,id=str(uuid.uuid4()),dismissed=False,dismissed_at=None,
            before={'status':'pending','dismissed_at':self.now.isoformat()})
        self.assertEqual(engine.exchange({'protocol':2,'operations':[undo]})['accepted'],[undo['id']])
        self.assertEqual(self.store.state('2026-10-17')['summary']['xp'],0)

    def test_penalty_is_per_occurrence_reversible_and_survives_archive(self):
        self.at(3)
        self.store.dismiss(self.row['id'])
        self.store.dismiss(self.row['id'])
        state=Store(self.path,clock=lambda:self.now).state('2026-10-17')
        self.assertEqual(state['summary']['xp'],-5)
        self.assertEqual(state['summary']['level'],1)
        self.assertEqual(state['summary']['dismissed'],1)
        self.assertEqual(len(state['tasks']),1)
        self.assertEqual(state['unfinished'],[])
        self.store.dismiss(self.row['id'],False)
        self.assertEqual(self.store.state('2026-10-17')['summary']['xp'],0)
        self.store.dismiss(self.row['id'])
        self.store.archive_task(self.task['id'])
        snapshot=SyncEngine(self.store,'laptop').exchange(dict(protocol=2,operations=[]))
        self.assertIsNotNone(snapshot['occurrences'][0]['dismissed_at'])
        self.assertEqual(self.store.state('2026-10-17')['summary']['xp'],-5)
