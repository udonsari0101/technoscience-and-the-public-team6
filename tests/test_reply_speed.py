"""Settings → 답변 속도: how quickly a reply's bubbles follow each other."""

from askphilo.services.preferences import DEFAULT_REPLY_SPEED, reply_speed

from .conftest import api_headers, csrf_from


def uid(client):
    with client.session_transaction() as s:
        return s["uid"]


def test_default_is_normal():
    assert DEFAULT_REPLY_SPEED == "normal"
    assert reply_speed(None) == "normal"
    assert reply_speed({}) == "normal"
    assert reply_speed({"reply_speed": "warp"}) == "normal"
    assert reply_speed({"reply_speed": "slow"}) == "slow"


def test_settings_shows_three_speeds_with_normal_checked(logged_in):
    html = logged_in.get("/settings").get_data(as_text=True)
    assert "답변 속도" in html
    for label in ("빠름", "보통", "느림"):
        assert f"<span>{label}</span>" in html
    assert 'value="normal" checked' in html
    assert 'value="fast" checked' not in html and 'value="slow" checked' not in html


def test_save_with_fetch(logged_in, repo):
    res = logged_in.post("/settings/reply-speed", json={"speed": "slow"}, headers=api_headers(logged_in))
    assert res.status_code == 200 and res.get_json() == {"ok": True, "speed": "slow"}
    assert repo.get_user(uid(logged_in))["reply_speed"] == "slow"
    assert 'value="slow" checked' in logged_in.get("/settings").get_data(as_text=True)


def test_save_with_form_fallback(logged_in, repo):
    res = logged_in.post("/settings/reply-speed", data={"csrf_token": csrf_from(logged_in), "speed": "fast"})
    assert res.status_code == 302 and res.headers["Location"].endswith("/settings#speed")
    assert repo.get_user(uid(logged_in))["reply_speed"] == "fast"


def test_rejects_unknown_speed(logged_in, repo):
    res = logged_in.post("/settings/reply-speed", json={"speed": "warp"}, headers=api_headers(logged_in))
    assert res.status_code == 400 and res.get_json()["ok"] is False
    res = logged_in.post("/settings/reply-speed", json={"speed": ["slow"]}, headers=api_headers(logged_in))
    assert res.status_code == 400
    assert "reply_speed" not in repo.get_user(uid(logged_in))


def test_requires_csrf_and_login(client, logged_in):
    res = logged_in.post("/settings/reply-speed", json={"speed": "slow"})
    assert res.status_code in (400, 403)
    logged_in.post("/logout", data={"csrf_token": csrf_from(logged_in)})
    res = client.post("/settings/reply-speed", json={"speed": "slow"}, headers=api_headers(client))
    assert res.status_code in (302, 401)


def test_chat_pages_carry_the_speed(logged_in):
    assert 'data-reply-speed="normal"' in logged_in.get("/chat/plato").get_data(as_text=True)
    logged_in.post("/settings/reply-speed", json={"speed": "fast"}, headers=api_headers(logged_in))
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "고민이 있어"},
                         headers=api_headers(logged_in))
    cid = res.get_json()["conversation_id"]
    assert 'data-reply-speed="fast"' in logged_in.get(f"/c/{cid}").get_data(as_text=True)
    assert 'data-reply-speed="fast"' in logged_in.get("/chat/plato").get_data(as_text=True)


def test_rejects_non_object_json(logged_in):
    res = logged_in.post("/settings/reply-speed", json=["slow"], headers=api_headers(logged_in))
    assert res.status_code == 400
