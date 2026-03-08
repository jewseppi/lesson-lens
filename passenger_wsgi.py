import os
import sys

ROOT = os.path.dirname(__file__)
API_DIR = os.path.join(ROOT, "api")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from app import app as application