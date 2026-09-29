"""Keep tests independent of this Mac: what Basic Correction learned from the user's grades lives in data/."""
import tempfile
from pathlib import Path

try:
    from davigen.basic import settings as _basic_settings
    _basic_settings.LEARNED = Path(tempfile.mkdtemp(prefix="davigen_test_")) / "basic_learned.json"
except ImportError:          # numpy / colour-science missing: those tests are skipped anyway
    pass
