# WHY: cc-sidecar uses src-layout (cc-sidecar/src/cc_sidecar/) but isn't
# installed into the root project's venv. This lets tests run from either
# the project root (poetry run pytest) or cc-sidecar/ directly.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
