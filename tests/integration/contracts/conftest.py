"""
Configuración de tests para contratos
"""
import sys
from pathlib import Path

# Agregar fixtures al path
fixtures_path = Path(__file__).parent.parent / "fixtures"
if str(fixtures_path) not in sys.path:
    sys.path.insert(0, str(fixtures_path))

# Importar configuración principal
from conftest import *
