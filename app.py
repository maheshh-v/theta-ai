"""
Entry point for Theta.

Builds the FastAPI app (chat API + OAuth + static SPA) and serves it with
uvicorn. ASGI servers can also import the module-level `app` object directly.

Run locally:
    python app.py
"""

from __future__ import annotations

from server.app_factory import create_app

app = create_app()


def main() -> None:
    import uvicorn

    from config import settings

    uvicorn.run(app, host=settings.server_host, port=settings.server_port)


if __name__ == "__main__":
    main()
