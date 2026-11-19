#!/usr/bin/env python3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app


class DatabaseAccessTests(unittest.TestCase):
    def test_authorization_denial_has_project_recovery_instructions(self):
        error=app.sqlite3.DatabaseError('authorization denied')
        with patch.object(app.sqlite3,'connect',side_effect=error):
            with self.assertRaises(app.ProjectDatabaseAccessError) as caught:
                app.DB(Path('/project/AnimeStudio Data/studio.db'))
        message=str(caught.exception)
        self.assertIn('ANIME_STUDIO_DATA_ACCESS_DENIED',message)
        self.assertIn('Choose Project Folder',message)
        self.assertIn('Do not delete or replace',message)
        self.assertIs(caught.exception.__cause__,error)

    def test_unrelated_sqlite_error_is_not_presented_as_permission_denial(self):
        error=app.sqlite3.DatabaseError('database disk image is malformed')
        with patch.object(app.sqlite3,'connect',side_effect=error):
            with self.assertRaises(app.sqlite3.DatabaseError) as caught:
                app.DB(Path('/project/studio.db'))
        self.assertIs(caught.exception,error)

    def test_existing_project_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'studio.db'
            first=app.DB(path);first.x("INSERT INTO settings(key,value) VALUES('test','keep me')");first.conn.close()
            reopened=app.DB(path)
            self.assertEqual(reopened.one("SELECT value FROM settings WHERE key='test'")['value'],'keep me')
            reopened.conn.close()

if __name__=='__main__':unittest.main()
