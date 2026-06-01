def test_web_health_importable():
    try:
        from godotter_web.app import app  # noqa: F401
    except ModuleNotFoundError:
        # web extra not installed in this environment
        return

