"""Native CI tests with bounded, named diagnostics for platform-specific stalls."""
import faulthandler
from pathlib import Path
import sys
import unittest


class Result(unittest.TextTestResult):
    def startTest(self,test):
        super().startTest(test)
        faulthandler.dump_traceback_later(120,exit=True)

    def stopTest(self,test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)


if __name__=='__main__':
    tests=Path(__file__).resolve().parents[1]/'tests'
    pattern=sys.argv[sys.argv.index('--module')+1] if '--module' in sys.argv else 'test_*.py'
    suite=unittest.defaultTestLoader.discover(str(tests),pattern=pattern)
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
