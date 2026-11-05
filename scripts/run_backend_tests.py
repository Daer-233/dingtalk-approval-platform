"""Run all tests with temporary SQLite and a fail-closed real network boundary."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from app import main
from app.db import Database

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    import os
    os.chdir(root)
    (root/'output/test-temp').mkdir(parents=True,exist_ok=True)
    tempfile.tempdir=str(root/'output/test-temp')
    with tempfile.TemporaryDirectory(dir=root/'output') as temp, patch.object(main,'db',Database(Path(temp)/'suite.db')), patch.object(httpx.HTTPTransport,'handle_request',side_effect=AssertionError('Real network disabled during regression')):
        suite=unittest.defaultTestLoader.discover('tests')
        result=unittest.TextTestRunner(verbosity=1).run(suite)
        summary={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'network':'mock only'}
        (root/'output/backend-tests.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
        print(summary)
        sys.exit(not result.wasSuccessful())
