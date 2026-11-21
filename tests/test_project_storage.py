from contextlib import closing
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import project_storage as storage


class ProjectStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.config = self.root / 'Application Support' / 'AnimeStudio'
        self.source = self.root / 'Documents' / 'AnimeStudio Data'

    def legacy(self, wal=False):
        self.source.mkdir(parents=True)
        db = sqlite3.connect(self.source / 'studio.db')
        if wal:
            db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE settings (key TEXT, value TEXT)')
        db.execute("INSERT INTO settings VALUES ('server', 'http://comfy:8188')")
        db.commit()
        (self.source / 'assets').mkdir()
        (self.source / 'assets' / 'frame.png').write_bytes(b'frame')
        self.config.mkdir(parents=True)
        (self.config / 'data-folder').write_text(str(self.source))
        return db

    def test_fresh_install_uses_application_support(self):
        result = storage.resolve_storage(self.config, home=self.root)
        self.assertEqual(result, self.config / 'Project Data')
        self.assertTrue(result.is_dir())
        self.assertFalse((self.root / 'Documents').exists())

    def test_legacy_copy_preserves_settings_assets_and_original(self):
        self.legacy().close()
        original = (self.source / 'studio.db').read_bytes()
        result = storage.resolve_storage(self.config)
        with closing(sqlite3.connect(result / 'studio.db')) as db:
            self.assertEqual(db.execute('SELECT value FROM settings').fetchone()[0], 'http://comfy:8188')
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
        self.assertEqual((result / 'assets/frame.png').read_bytes(), b'frame')
        self.assertEqual((self.source / 'studio.db').read_bytes(), original)
        self.assertEqual((self.config / 'data-folder.before-managed-storage').read_text(), str(self.source))
        self.source.rename(self.root / 'original-backup')
        with patch.object(storage, 'migrate_project', side_effect=AssertionError('must not access legacy project')):
            self.assertEqual(storage.resolve_storage(self.config), result)

    def test_wal_committed_settings_are_included(self):
        db = self.legacy(wal=True)
        self.addCleanup(db.close)
        result = storage.resolve_storage(self.config)
        with closing(sqlite3.connect(result / 'studio.db')) as copied:
            self.assertEqual(copied.execute('SELECT COUNT(*) FROM settings').fetchone()[0], 1)

    def test_failed_copy_does_not_change_preference_or_original(self):
        self.legacy().close()
        with patch.object(storage.shutil, 'copytree', side_effect=PermissionError('blocked')):
            with self.assertRaises(PermissionError):
                storage.resolve_storage(self.config)
        self.assertEqual((self.config / 'data-folder').read_text(), str(self.source))
        self.assertTrue((self.source / 'studio.db').is_file())

    def test_source_change_does_not_activate_inconsistent_copy(self):
        self.legacy().close()
        with patch.object(storage, 'fingerprint', side_effect=[{'old': 1}, {'new': 2}]):
            with self.assertRaisesRegex(ValueError, 'changed during migration'):
                storage.resolve_storage(self.config)
        self.assertEqual((self.config / 'data-folder').read_text(), str(self.source))
        self.assertFalse(list((self.config / 'Projects').glob('.migrating-*')))

    def test_missing_saved_project_is_never_replaced_with_blank_project(self):
        self.config.mkdir(parents=True)
        (self.config / 'data-folder').write_text(str(self.source))
        with self.assertRaisesRegex(ValueError, 'no studio.db'):
            storage.resolve_storage(self.config)
        self.assertFalse((self.config / 'Project Data').exists())

    def test_explicit_override_does_not_rewrite_saved_preference(self):
        self.legacy().close()
        override = self.root / 'custom'
        self.assertEqual(storage.resolve_storage(self.config, override=override), override)
        self.assertEqual((self.config / 'data-folder').read_text(), str(self.source))


if __name__ == '__main__':
    unittest.main()
