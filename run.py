"""Start the report add-on.

    python run.py          production mode
    python run.py --dev    development: restarts on code changes, listens on localhost only
"""
import sys

import uvicorn

from app.config import ROOT, settings

if __name__ == "__main__":
    dev = "--dev" in sys.argv[1:]
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1" if dev else settings.host,
        port=settings.port,
        reload=dev,
        reload_dirs=[str(ROOT / "app")] if dev else None,
    )
