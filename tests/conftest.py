import pytest

from askphilo import create_app


@pytest.fixture()
def app():
    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret-key"})
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()
