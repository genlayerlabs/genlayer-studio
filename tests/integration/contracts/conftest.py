"""
Configuration for contract integration tests
"""
import sys
from pathlib import Path

# Add fixtures to path
fixtures_path = Path(__file__).parent.parent / "fixtures"
if str(fixtures_path) not in sys.path:
    sys.path.insert(0, str(fixtures_path))

# Import main configuration
from conftest import *