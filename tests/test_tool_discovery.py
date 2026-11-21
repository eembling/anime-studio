"""Optional tool discovery must not request protected-folder access on startup."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app


class ToolDiscoveryTests(unittest.TestCase):
    def test_optional_rife_search_avoids_protected_folders(self):
        searched = []
        with patch.object(app.shutil, 'which', return_value=None), patch.object(app.glob, 'glob', side_effect=lambda path: searched.append(path) or []):
            self.assertIsNone(app.find_rife())
        self.assertTrue(searched)
        self.assertFalse(any('/Downloads/' in path or '/Documents/' in path for path in searched))
        self.assertTrue(any('/Library/Application Support/AnimeStudio/tools/' in path for path in searched))

    def test_path_install_does_not_enumerate_folders(self):
        with patch.object(app.shutil, 'which', return_value='/opt/homebrew/bin/rife-ncnn-vulkan'), patch.object(app.glob, 'glob') as glob:
            self.assertEqual(app.find_rife(), Path('/opt/homebrew/bin/rife-ncnn-vulkan'))
            glob.assert_not_called()


if __name__ == '__main__':
    unittest.main()
