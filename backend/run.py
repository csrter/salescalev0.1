"""Entrypoint for the packaged backend.

Using a top-level launcher (rather than pointing PyInstaller at app/main.py)
gives the app a real package context so its relative imports resolve, and
actually starts the uvicorn server.
"""
import uvicorn

from app.main import app


def main() -> None:
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
