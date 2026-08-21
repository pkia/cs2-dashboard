import app

client = app.app.test_client()


def test_index_serves_page():
    r = client.get("/")
    assert r.status_code == 200


def test_api_status():
    assert client.get("/api/status").get_json()["ok"] is True
