import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import Studio


class RenderImportRecoveryTests(unittest.TestCase):
    def test_restart_reconciles_rolled_back_and_committed_output(self):
        with TemporaryDirectory() as folder:
            first = Studio(folder)
            job_dir = first.data / 'jobs' / '1'
            job_dir.mkdir()
            partial = first.data / 'shots' / 'uncommitted.mp4'
            committed = first.data / 'shots' / 'committed.mp4'
            for state, path in [('uncommitted', partial), ('committed', committed)]:
                path.touch()
                (job_dir / f'{state}.render-import').write_text(json.dumps(str(path)) + '\n')
            with self.assertRaises(RuntimeError):
                with first.db.transaction():
                    first.db.x("INSERT INTO takes(path) VALUES(?)", (first.rel(partial),))
                    first.db.x("INSERT INTO render_imports VALUES('uncommitted')")
                    raise RuntimeError('Interrupted after insert')
            with first.db.transaction():
                first.db.x("INSERT INTO takes(path) VALUES(?)", (first.rel(committed),))
                first.db.x("INSERT INTO render_imports VALUES('committed')")
            first.db.conn.close()

            reopened = Studio(folder)
            self.assertFalse(partial.exists())
            self.assertTrue(committed.exists())
            self.assertEqual(reopened.db.q('SELECT path FROM takes'), [{'path': reopened.rel(committed)}])
            self.assertEqual(list(job_dir.glob('*.render-import')), [])
            reopened.db.conn.close()
