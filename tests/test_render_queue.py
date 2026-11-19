#!/usr/bin/env python3
"""Queue estimates must be useful without inventing measured completion or wait times."""
import sys
import unittest
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from render_queue import queue_snapshot


def job(id,status='pending',**values):
    return {'id':id,'status':status,'stage':'animate','draft':0,'priority':0,'instance_id':None,'started':None,'finished':None,**values}


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.server={'id':1,'name':'GPU','roles':'animate,keyframe','enabled':1}
        self.history=[job(1,'done',instance_id=1,started=900,finished=1000)]

    def snap(self,jobs,**kw):
        return queue_snapshot(jobs,kw.pop('history',self.history),kw.pop('instances',[self.server]),now=2000,**kw)

    def test_active_and_queued_are_separate_with_ordered_finish_estimates(self):
        q=self.snap([job(2,'running',instance_id=1,started=1950),job(3),job(4,priority=10)])
        a=q['active'][0]
        self.assertEqual(a['estimated_progress'],50)
        self.assertEqual(a['remaining_seconds'],50)
        self.assertEqual([j['id'] for j in q['queued']],[4,3])
        self.assertEqual([j['estimated_finish_seconds'] for j in q['queued']],[150,250])

    def test_overdue_work_never_claims_completion_or_invents_downstream_eta(self):
        q=self.snap([job(2,'running',instance_id=1,started=1800),job(3)])
        self.assertEqual(q['active'][0]['estimated_progress'],95)
        self.assertTrue(q['active'][0]['overdue'])
        self.assertIsNone(q['active'][0]['remaining_seconds'])
        self.assertIsNone(q['queued'][0]['estimated_finish_seconds'])

    def test_no_history_means_indeterminate_not_a_fake_percentage(self):
        q=self.snap([job(2,'running',instance_id=1,started=1950),job(3)],history=[])
        self.assertIsNone(q['active'][0]['estimated_progress'])
        self.assertIsNone(q['queued'][0]['estimated_finish_seconds'])

    def test_free_parallel_gpu_can_finish_first(self):
        q=self.snap([job(2,'running',instance_id=1,started=1950),job(3)],instances=[self.server,dict(self.server,id=2)])
        self.assertEqual(q['queued'][0]['estimated_server_id'],2)
        self.assertEqual(q['queued'][0]['estimated_start_seconds'],0)
        self.assertEqual(q['queued'][0]['estimated_finish_seconds'],100)

    def test_offline_disabled_or_incompatible_servers_get_no_eta(self):
        for args in ({'health':{1:{'ok':False}}},{'instances':[dict(self.server,enabled=0)]},
                     {'readiness':{1:{'animate':{'ready':False}}}}):
            with self.subTest(args=args):
                j=self.snap([job(3)],**args)['queued'][0]
                self.assertIsNone(j['estimated_finish_seconds'])
                self.assertIn('No enabled',j['wait_reason'])

    def test_failed_runs_and_drafts_do_not_pollute_full_quality_history(self):
        history=[job(1,'error',instance_id=1,started=100,finished=110),job(2,'done',draft=1,instance_id=1,started=100,finished=120)]
        q=self.snap([job(3,'running',instance_id=1,started=1950)],history=history)
        self.assertIsNone(q['active'][0]['estimated_progress'])

    def test_completed_is_100_but_failed_and_cancelled_stay_separate(self):
        q=self.snap([job(1,'done',instance_id=1,started=900,finished=1000),job(2,'error'),job(3,'cancelled')])
        self.assertEqual(q['completed'][0]['estimated_progress'],100)
        self.assertEqual(len(q['attention']),2)
        self.assertTrue(all(j['estimated_progress'] is None for j in q['attention']))

    def test_median_limits_outlier_effect(self):
        history=[job(i,'done',instance_id=1,started=100,finished=100+d) for i,d in enumerate([100,100,10000],1)]
        self.assertEqual(self.snap([job(4)],history=history)['queued'][0]['typical_seconds'],100)

    def test_timing_history_survives_clearing_jobs_and_retry_resets_old_timestamps(self):
        import anime_studio as app
        with tempfile.TemporaryDirectory() as folder:
            st=app.Studio(folder)
            st.worker=app.Worker(st)
            api=app.API(st)
            try:
                st.db.x("INSERT INTO jobs(stage,status,instance_id,started,finished) VALUES('animate','done',1,100,200)")
                api.job_action({'action':'clear'})
                self.assertEqual(st.db.one('SELECT COUNT(*) n FROM jobs')['n'],0)
                self.assertEqual(st.db.one('SELECT COUNT(*) n FROM render_timing_history')['n'],1)
                jid=st.db.x("INSERT INTO jobs(stage,status,instance_id,started) VALUES('animate','running',1,?)", (time.time()-50,))
                active=api.state()['render_queue']['active'][0]
                self.assertEqual(active['typical_seconds'],100)
                self.assertEqual(active['estimated_progress'],50)
                st.db.x("UPDATE jobs SET status='error',finished=? WHERE id=?", (time.time(),jid))
                api.job_action({'action':'retry','ids':[jid]})
                retried=st.db.one('SELECT * FROM jobs WHERE id=?',(jid,))
                self.assertIsNone(retried['started']);self.assertIsNone(retried['finished'])
            finally:
                st.db.conn.close()


if __name__=='__main__': unittest.main()
