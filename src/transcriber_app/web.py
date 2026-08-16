"""Compatibility module for WSGI servers and container entrypoints."""

from .app import create_app

__all__ = ["create_app"]
