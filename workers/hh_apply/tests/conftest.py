"""Делает `workers.hh_apply` импортируемым при запуске pytest откуда угодно.

Ни один тест здесь не открывает браузер и не ходит в сеть: страницы hh —
на моках (tests/fakes.py), playwright для тестов не нужен вообще.
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
