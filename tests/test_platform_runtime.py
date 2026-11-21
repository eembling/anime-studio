import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import platform_runtime as platform


class PlatformRuntimeTests(unittest.TestCase):
    def test_windows_project_data_is_outside_install_directory(self):
        with patch.object(platform.sys,'platform','win32'),patch.dict(os.environ,{'LOCALAPPDATA':'C:/Users/Test/AppData/Local'}):
            self.assertEqual(platform.config_directory(),Path('C:/Users/Test/AppData/Local/AnimeStudio'))

    def test_storage_lock_releases_after_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            lock=Path(directory)/'storage.lock'
            with self.assertRaises(ValueError):
                with platform.storage_lock(lock):raise ValueError('stop')
            with platform.storage_lock(lock):self.assertTrue(lock.exists())

    def test_windows_cancellation_targets_only_owned_live_process(self):
        process=Mock(pid=12345);process.poll.return_value=None
        with patch.object(platform.sys,'platform','win32'),patch.object(platform.subprocess,'run') as run:
            platform.terminate_process(process)
            self.assertEqual(run.call_args.args[0],['taskkill','/PID','12345','/T','/F'])
            process.poll.return_value=0;platform.terminate_process(process)
            self.assertEqual(run.call_count,1)

if __name__=='__main__':unittest.main()
