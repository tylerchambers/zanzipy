"""LocalProxy for accessing the configured Zanzibar extension within Flask.

Usage:
    from zanzipy.integration.flask import Zanzibar
    from zanzipy.integration.flask.proxy import current_zanzibar

    zanzibar = Zanzibar()
    def create_app():
        app = Flask(__name__)
        zanzibar.init_app(app)
        return app

    @app.route("/ping")
    def ping():
        return {"ok": current_zanzibar.check(
            "document:readme",
            "can_view",
            "user:alice",
        )}
"""

_FLASK_IMPORT_ERROR: Exception | None = None


class _LocalProxy:
    def __init__(self, resolver):
        self._resolver = resolver

    def __getattr__(self, name):
        obj = self._resolver()
        return getattr(obj, name)

    def __call__(self, *args, **kwargs):
        obj = self._resolver()
        return obj(*args, **kwargs)


def _find_extension():
    try:
        # Import lazily to avoid hard dependency and type complaints when Flask
        # is not installed.
        from flask import current_app, has_app_context
    except Exception as exc:
        raise RuntimeError("Flask is not installed") from exc

    if not has_app_context():
        raise RuntimeError("No Flask application context is active")
    ext = current_app.extensions.get("zanzibar")
    if ext is None:
        raise RuntimeError("Zanzibar extension not initialized on this app")
    return ext


current_zanzibar = _LocalProxy(_find_extension)
