"""Run tests that need neither personal media nor downloaded models or APIs."""
from pathlib import Path
import sys
import unittest

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
loader=unittest.TestLoader()
suite=unittest.TestSuite()
for pattern in ['test_state.py','test_planning_sources.py','test_pronunciation.py',
                'test_voice_phrases.py','test_public_checkout.py','test_recovery.py']:
    suite.addTests(loader.discover(str(root/'tests'),pattern=pattern))
sys.path.insert(0,str(root/'tests'))
suite.addTests(loader.loadTestsFromName('test_openings.PureOpeningTests'))
suite.addTests(loader.loadTestsFromName('test_openings.WorkspaceOpeningTests'))
suite.addTests(loader.loadTestsFromName('test_editorial.EditorialTests'))
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
