import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
# Compatibility entry point only.  The stable/safe logical mode is selected
# per request by the single API on port 8765; this script must never create a
# second HTTP listener or force the stable model for every request.
os.environ.setdefault("IRODORI_API_PORT", "8765")
os.environ.setdefault("IRODORI_API_PID", str(ROOT / "logs" / "kurisu_voice_api" / "server.pid"))

from kurisu_voice_api import main


if __name__ == "__main__":
    main()
