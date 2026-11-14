"""Code lives in the project; canonical evidence is opened in a working copy."""
import os
from pathlib import Path
from compute.src.archive import materialize

CODE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ['THESIS_EVIDENCE_ROOT']).resolve() if os.environ.get('THESIS_EVIDENCE_ROOT') else materialize()
