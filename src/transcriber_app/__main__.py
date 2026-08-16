from __future__ import annotations

import os

from .app import create_app


def main() -> None:
    app = create_app()
    host = os.getenv("TRANSCRIBER_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    app.run(host=host, port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
