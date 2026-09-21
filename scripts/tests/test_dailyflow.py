import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from bridge import Vault, CATEGORIES
import os
ROOT = Path(os.environ['KB_TEST_WORK'])
import dailyflow as flow


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'work')
        self.v = Vault(self.temp.name)
        for name in CATEGORIES:
            (self.v.root/name).mkdir()
        self.moment = flow.stamp('2026-09-20T20:30:00+08:00')
        self.clock = patch.object(flow, 'current', return_value=self.moment)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.temp.cleanup()

    def create(self, kind='decision', **kw):
        q = dict(operation='create', authorization='记下来', user_instruction='把这个待定决定记下来',
                 category='项目', title='是否改变流程', body='## AI建议\n增加外部输入\n## 我的反驳\n先核对目标\n## 待决定\n是否需要调整', kind=kind)
        q.update(kw)
        return self.v.run(q)

    def change(self, note, **props):
        return self.v.run(dict(operation='update',authorization='update',user_instruction='更新这条事项',
                               path=note['path'],expected_revision=note['revision'],properties=props))

    def overdue(self):
        return self.create(due_at='2026-09-19T20:30:00+08:00')

    def snapshot(self):
        return {str(p.relative_to(self.v.root)):p.read_bytes() for p in self.v.root.rglob('*') if p.is_file()}

    def test_saved_item_appears_in_all_three_navigation_entries(self):
        a = self.create()
        self.assertFalse(a.get('views_pending'))
        today = self.v.root/'项目/每日流/今日.md'
        queue = self.v.root/'项目/每日流/待处理事项.md'
        for p in (today, queue, *list((self.v.root/'项目/每日流/日报').glob('*.md'))):
            if '是否改变流程' in p.read_text(encoding='utf-8'):
                self.assertIn('[[项目/是否改变流程|是否改变流程]]',p.read_text(encoding='utf-8'))
        self.assertIn('是否改变流程', today.read_text(encoding='utf-8'))
        self.assertIn('是否改变流程', queue.read_text(encoding='utf-8'))
        self.assertTrue(any('是否改变流程' in p.read_text(encoding='utf-8') for p in (self.v.root/'项目/每日流/日报').glob('*.md')))
        self.assertEqual(self.v.run({'operation':'list'})['total'],1)

    def test_read_workflows_do_not_save_chat_or_change_any_file(self):
        self.create()
        before = self.snapshot()
        for op in ('today','daily','queue','reminder_scan','reminder_settings'):
            self.v.run({'operation':op})
        self.assertEqual(before,self.snapshot())

    def test_external_inputs_are_not_personal_daily_material(self):
        self.create(kind='knowledge',category='资源',title='外部材料',body='外部唯一关键词')
        today=(self.v.root/'项目/每日流/今日.md').read_text(encoding='utf-8')
        self.assertNotIn('外部材料',today)
        self.assertNotIn('外部唯一关键词',today)
        self.assertEqual(self.v.run({'operation':'today'})['saved'],[])
        with self.assertRaises(ValueError):
            self.create(category='资源')

    def test_decision_can_be_adopted_completed_archived_and_restored(self):
        a=self.create()
        self.assertEqual(self.v.run({'operation':'queue'})['items'][0]['task_state'],'pending_decision')
        b=self.change(a,task_state='todo')
        c=self.change(b,task_state='done')
        self.assertEqual(self.v.run({'operation':'queue'})['items'],[])
        self.assertEqual(len(self.v.run({'operation':'queue','include_closed':True})['items']),1)
        d=self.v.run(dict(operation='archive',authorization='archive',user_instruction='归档',path=c['path'],expected_revision=c['revision']))
        self.assertEqual(self.v.run({'operation':'queue','include_closed':True})['items'],[])
        e=self.v.run(dict(operation='restore',authorization='restore',user_instruction='恢复',path=d['path'],expected_revision=d['revision']))
        f=self.change(e,task_state='in_progress')
        self.assertEqual(self.v.run({'operation':'queue'})['items'][0]['task_state'],'in_progress')
        self.assertIn('我的反驳',self.v.read(f['path'])['body'])

    def test_due_reminder_ack_is_deduplicated_but_changed_item_can_remind(self):
        a=self.overdue()
        alerts=self.v.run({'operation':'reminder_scan'})['alerts']
        self.assertEqual(len(alerts),1)
        self.v.run(dict(operation='reminder_ack',authorization='maintenance',user_instruction='已准备本轮提醒',fingerprints=[alerts[0]['fingerprint']]))
        self.assertEqual(self.v.run({'operation':'reminder_scan'})['alerts'],[])
        self.change(a,task_state='todo')
        self.assertEqual(len(self.v.run({'operation':'reminder_scan'})['alerts']),1)

    def test_completed_archived_cancelled_and_muted_items_never_remind(self):
        for prop in ({'task_state':'done'},{'task_state':'cancelled'},{'reminder_enabled':False}):
            a=self.overdue()
            b=self.change(a,**prop)
            self.assertEqual(self.v.run({'operation':'reminder_scan'})['alerts'],[])
            a=self.change(b,task_state='pending_decision',reminder_enabled=True)
            self.v.run(dict(operation='archive',authorization='archive',user_instruction='归档',path=a['path'],expected_revision=a['revision']))
            self.assertEqual(self.v.run({'operation':'reminder_scan'})['alerts'],[])
            restored=self.v.run(dict(operation='restore',authorization='restore',user_instruction='恢复',path=a['path'],expected_revision=self.v.read(a['path'])['revision']))
            self.change(restored,task_state='done')

    def test_future_review_suppresses_stale_nudge(self):
        a=self.create(review_at='2026-10-01T20:30:00+08:00')
        with patch.object(flow,'current',return_value=flow.stamp('2026-09-29T20:30:00+08:00')):
            self.assertEqual(self.v.run({'operation':'reminder_scan'})['alerts'],[])
        with patch.object(flow,'current',return_value=flow.stamp('2026-10-01T20:30:00+08:00')):
            self.assertEqual(self.v.run({'operation':'reminder_scan'})['alerts'][0]['reason'],'已到复查时间')

    def test_48_hours_nudge_is_not_a_fabricated_deadline(self):
        a=self.create()
        created=flow.stamp(a['metadata']['updated'])
        self.assertIsNone(a['metadata']['due_at'])
        with patch.object(flow,'current',return_value=created+timedelta(hours=47)):
            self.assertEqual(self.v.run({'operation':'reminder_scan'})['alerts'],[])
        with patch.object(flow,'current',return_value=created+timedelta(hours=48)):
            self.assertIn('48小时',self.v.run({'operation':'reminder_scan'})['alerts'][0]['reason'])

    def test_settings_can_be_read_changed_disabled_reenabled(self):
        self.overdue()
        self.v.run(dict(operation='reminder_configure',authorization='update',user_instruction='关闭提醒',settings={'enabled':False}))
        self.assertEqual(self.v.run({'operation':'reminder_scan'})['alerts'],[])
        self.v.run(dict(operation='reminder_configure',authorization='update',user_instruction='打开提醒',settings={'enabled':True,'stale_hours':72}))
        self.assertTrue(self.v.run({'operation':'reminder_settings'})['enabled'])
        self.assertEqual(len(self.v.run({'operation':'reminder_scan'})['alerts']),1)

    def test_navigation_preserves_manual_notes_and_conflict_is_reported(self):
        a=self.create()
        p=self.v.root/'项目/每日流/今日.md'
        p.write_text(p.read_text(encoding='utf-8')+'\n我的导航备注\n',encoding='utf-8')
        self.change(a,task_state='todo')
        self.assertIn('我的导航备注',p.read_text(encoding='utf-8'))
        p.write_text('完全手写的文件，不覆盖',encoding='utf-8')
        a=self.v.read(a['path'])
        b=self.change(a,task_state='in_progress')
        self.assertTrue(b['views_pending'])
        self.assertEqual(self.v.read(a['path'])['metadata']['task_state'],'in_progress')
        self.assertEqual(p.read_text(encoding='utf-8'),'完全手写的文件，不覆盖')

    def test_flow_changes_and_ack_require_authorization_and_fresh_data(self):
        a=self.overdue()
        token=self.v.run({'operation':'reminder_scan'})['alerts'][0]['fingerprint']
        for op in ('refresh_views','reminder_configure','reminder_ack'):
            with self.assertRaises(ValueError):
                self.v.run({'operation':op})
        self.change(a,task_state='done')
        with self.assertRaises(ValueError):
            self.v.run(dict(operation='reminder_ack',authorization='maintenance',user_instruction='本轮提醒',fingerprints=[token]))

    def test_invalid_state_date_and_settings_are_rejected(self):
        a=self.create()
        for props in ({'task_state':'fake'},{'due_at':'yesterday'},{'reminder_enabled':'false'}):
            with self.assertRaises(ValueError):
                self.change(a,**props)
        with self.assertRaises(ValueError):
            self.v.run(dict(operation='reminder_configure',authorization='update',user_instruction='修改提醒',settings={'stale_hours':-1}))

if __name__=='__main__':
    unittest.main(verbosity=2)
