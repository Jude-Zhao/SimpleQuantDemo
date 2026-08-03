"""SimpleQuant Web Dashboard startup script.

Run with:
    python run_webapp.py
"""

import uvicorn

from webapp.config import get_config


def main() -> None:
    config = get_config()
    uvicorn.run(
        "webapp.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )


if __name__ == "__main__":
    main()