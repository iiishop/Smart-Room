import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parents[2]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from ha_client.main import main

if __name__ == "__main__":
    main()
