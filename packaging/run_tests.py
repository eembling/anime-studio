"""Native CI tests with bounded, named diagnostics for platform-specific stalls."""
import faulthandler
import concurrent.futures
import subprocess
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


def run_module(path):
    result=subprocess.run([sys.executable,'-u',__file__,'--module',path.name],capture_output=True,text=True,encoding='utf-8',timeout=600)
    return path.name,result.returncode,result.stdout+result.stderr


if __name__=='__main__':
    tests=Path(__file__).resolve().parents[1]/'tests'
    if sys.platform=='win32' and '--module' not in sys.argv:
        failed=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(run_module,path) for path in sorted(tests.glob('test_*.py'))]
            for future in concurrent.futures.as_completed(futures):
                name,code,output=future.result();print('=== '+name+' ===',flush=True);print(output,flush=True)
                if code:failed.append(name)
        if failed:print('Failed modules: '+', '.join(failed),flush=True)
        else:print('All native test modules passed.',flush=True)
        sys.exit(1 if failed else 0)
    pattern=sys.argv[sys.argv.index('--module')+1] if '--module' in sys.argv else 'test_*.py'
    suite=unittest.defaultTestLoader.discover(str(tests),pattern=pattern)
    result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
