# 让同目录下的 kb_query / kb_rsi 可被导入（pytest 不自动把 pipeline/ 加到 sys.path）。
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))
