def test_healthz(app_factory):
    assert app_factory().get("/healthz").json() == {"status": "ok"}
