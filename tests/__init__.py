"""
Test package for the FFVII Tactical Battle Companion.

Importing this package (which unittest discovery always does before any test
module) redirects every writable path of the application - settings, player
profile, misdetection reports and logs - to a throwaway temporary directory.
Test runs therefore never modify the user's files (.dev_home/ for runs from
source, the folder of the packaged EXE).
"""
import atexit
import os
import shutil
import tempfile

_TEST_HOME = tempfile.mkdtemp(prefix="ffvii_companion_tests_")
os.environ["FFVII_COMPANION_HOME"] = _TEST_HOME
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")


def _cleanup() -> None:
    shutil.rmtree(_TEST_HOME, ignore_errors=True)


atexit.register(_cleanup)
