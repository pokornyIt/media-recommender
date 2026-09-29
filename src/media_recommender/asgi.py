"""Production ASGI entry point for the Media Recommender web application.

The module exposes a single importable application object so an ASGI server can
serve the same FastAPI application built by the Web interface factory. It adds no
business logic and does not construct application services; feature routes keep
resolving their own dependencies.
"""

from media_recommender.web import create_app

app = create_app()
