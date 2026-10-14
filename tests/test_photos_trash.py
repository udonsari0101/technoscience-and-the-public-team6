"""Photo attachments (staged uploads, vision reading, per-photo quota) and the trash."""

import io
from datetime import timedelta

from PIL import Image

from askphilo.ai import set_provider
from askphilo.ai.base import ImageReading
from askphilo.data.base import utcnow
from askphilo.services import images as image_files

from .conftest import api_headers, signup
from .helpers import CountingProvider


def png(w=400, h=300, mode="RGB", color=(200, 80, 80)):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color if mode != "1" else 1).save(buf, "PNG")
    buf.seek(0)
    return buf


def upload(client, data=None, name="a.png"):
    return client.post("/api/uploads", data={"image": (data or png(), name)},
                       headers=api_headers(client), content_type="multipart/form-data")


def staged(client, **kw) -> str:
    res = upload(client, **kw)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["upload"]["id"]


def uid(client):
    with client.session_transaction() as s:
        return s["uid"]


class ReadingProvider(CountingProvider):
    """Counting provider that also 'reads' photos, like the vision model."""

    def __init__(self):
        super().__init__()
        self.read_calls = []

    def read_images(self, **kwargs):
        self.read_calls.append(kwargs)
        return ImageReading(text="[사진 1 · 대화 캡처]\n상대: 오늘은 좀 바빠\n나: 알겠어",
                            provider="mock", model="mock-v1")


# ---------------------------------------------------------------- processing
def test_small_photo_becomes_one_jpeg_part():
    parts = image_files.process_image(png(400, 300))
    assert len(parts) == 1 and parts[0]["width"] == 400
    assert Image.open(io.BytesIO(parts[0]["data"])).format == "JPEG"


def test_wide_photo_is_scaled_to_900px():
    parts = image_files.process_image(png(2000, 1000))
    assert parts[0]["width"] == 900 and parts[0]["height"] == 450


def test_tall_capture_is_cut_into_readable_parts():
    parts = image_files.process_image(png(900, 4000))
    assert len(parts) == 4 and all(p["width"] == 900 and p["height"] <= 1300 for p in parts)
    assert sum(p["height"] for p in parts) == 4000
    huge = image_files.process_image(png(900, 12000))
    assert len(huge) == image_files.MAX_PARTS and all(p["height"] <= 1300 for p in huge)


def test_every_part_fits_one_firestore_document():
    noisy = Image.effect_noise((900, 1300), 120).convert("RGB")
    buf = io.BytesIO()
    noisy.save(buf, "PNG")
    buf.seek(0)
    parts = image_files.process_image(buf)
    assert all(len(p["data"]) <= image_files.MAX_PART_BYTES for p in parts)
    assert all(len(p["data"]) * 4 / 3 < 1_000_000 for p in parts)  # base64 < 1 MB


def test_transparency_is_flattened_and_metadata_dropped():
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x0110] = "PhoneModel"  # camera model tag
    Image.new("RGB", (300, 200), (10, 20, 30)).save(buf, "JPEG", exif=exif.tobytes())
    buf.seek(0)
    out = Image.open(io.BytesIO(image_files.process_image(buf)[0]["data"]))
    assert not out.getexif()
    assert image_files.process_image(png(50, 50, mode="RGBA", color=(0, 0, 0, 0)))


def test_non_images_and_huge_resolutions_are_refused():
    for data in (io.BytesIO(b"not an image at all"), io.BytesIO(b"%PDF-1.4 ...")):
        try:
            image_files.process_image(data)
            raise AssertionError("expected ImageError")
        except image_files.ImageError as exc:
            assert exc.code == "not_image"
    try:
        image_files.process_image(png(8000, 8000, mode="1"))  # 64 MP
        raise AssertionError("expected ImageError")
    except image_files.ImageError as exc:
        assert exc.code == "too_many_pixels"


# ---------------------------------------------------------------- upload / serve
def test_upload_and_serve_only_to_owner(app, logged_in):
    res = upload(logged_in)
    assert res.status_code == 201
    info = res.get_json()["upload"]
    image = logged_in.get(info["url"])
    assert image.status_code == 200 and image.mimetype == "image/jpeg"
    assert "private" in image.headers["Cache-Control"]
    other = app.test_client()
    signup(other, username="someone_else")
    assert other.get(info["url"]).status_code == 404
    assert app.test_client().get(info["url"]).status_code == 302  # login first


def test_upload_rejects_non_images_and_oversize(app, logged_in, monkeypatch):
    res = upload(logged_in, data=io.BytesIO(b"hello"), name="note.txt")
    assert res.status_code == 400 and res.get_json()["error"] == "not_image"
    monkeypatch.setattr(image_files, "MAX_UPLOAD_BYTES", 100)
    big = io.BytesIO()
    Image.effect_noise((600, 600), 120).convert("RGB").save(big, "PNG")  # ~1 MB
    big.seek(0)
    res = upload(logged_in, data=big)
    assert res.status_code == 413 and "100MB" in res.get_json()["message"]


def test_discard_removes_an_unsent_photo(logged_in):
    upload_id = staged(logged_in)
    res = logged_in.post(f"/api/uploads/{upload_id}/discard", headers=api_headers(logged_in))
    assert res.status_code == 200
    assert logged_in.get(f"/images/{upload_id}-0").status_code == 404


def test_stale_unsent_photos_are_cleaned_up(logged_in, repo):
    old = staged(logged_in)
    repo.user_images[uid(logged_in)][f"{old}-0"]["created_at"] = utcnow() - timedelta(hours=25)
    staged(logged_in)
    assert logged_in.get(f"/images/{old}-0").status_code == 404


def test_csp_allows_blob_previews(client):
    assert "blob:" in client.get("/").headers["Content-Security-Policy"]


# ---------------------------------------------------------------- sending
def test_photos_are_read_once_and_the_reply_model_gets_text(app, logged_in, repo):
    provider = ReadingProvider()
    set_provider(app, provider)
    ids = [staged(logged_in), staged(logged_in, data=png(900, 3000))]
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "이거 봐줘",
                                                     "image_ids": ids}, headers=api_headers(logged_in))
    assert res.status_code == 201, res.get_json()
    data = res.get_json()
    assert len(provider.read_calls) == 1 and len(provider.read_calls[0]["images"]) == 2
    sent = provider.reply_calls[0]["messages"][-1]["content"]
    assert isinstance(sent, str) and "[보낸 사진 내용]" in sent and "상대: 오늘은 좀 바빠" in sent
    photos = data["user_message"]["images"]
    assert len({p["g"] for p in photos}) == 2 and len(photos) == 1 + 3  # tall one: 3 parts
    assert data["daily"]["used"] == 3  # 1 message + 2 photos
    stored = repo.get_messages(uid(logged_in), data["conversation_id"])[0]
    assert stored["image_text"].startswith("[사진 1")
    # sent photos can't be sent again or discarded
    again = logged_in.post(f"/api/conversations/{data['conversation_id']}/messages",
                           json={"content": "또", "image_ids": ids[:1]}, headers=api_headers(logged_in))
    assert again.status_code == 400 and again.get_json()["error"] == "bad_image"
    logged_in.post(f"/api/uploads/{ids[0]}/discard", headers=api_headers(logged_in))
    assert logged_in.get(photos[0]["url"]).status_code == 200


def test_photo_only_message_is_allowed(app, logged_in):
    set_provider(app, ReadingProvider())
    res = logged_in.post("/api/conversations", json={"philosopher_id": "nietzsche", "content": "",
                                                     "image_ids": [staged(logged_in)]},
                         headers=api_headers(logged_in))
    assert res.status_code == 201
    home = logged_in.get("/home").get_data(as_text=True)
    assert "사진" in home  # preview of a photo-only message


def test_unreadable_photos_go_to_the_reply_model_directly(app, logged_in):
    provider = CountingProvider()  # cannot read photos
    set_provider(app, provider)
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "봐줘",
                                                     "image_ids": [staged(logged_in)]},
                         headers=api_headers(logged_in))
    assert res.status_code == 201
    content = provider.reply_calls[0]["messages"][-1]["content"]
    assert isinstance(content, list) and content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg" and content[-1]["text"] == "봐줘"


def test_old_unread_photos_become_a_text_note(app, logged_in):
    provider = CountingProvider()
    set_provider(app, provider)
    cid = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "사진",
                                                     "image_ids": [staged(logged_in)]},
                         headers=api_headers(logged_in)).get_json()["conversation_id"]
    for text in ("그다음 얘기", "또 다른 얘기"):
        logged_in.post(f"/api/conversations/{cid}/messages", json={"content": text},
                       headers=api_headers(logged_in))
    first = provider.reply_calls[-1]["messages"][0]["content"]
    assert first == "사진 [사진 1장을 보냄]"


def test_photo_limits_and_ids(app, logged_in):
    set_provider(app, ReadingProvider())
    six = [staged(logged_in) for _ in range(6)]
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "x",
                                                     "image_ids": six}, headers=api_headers(logged_in))
    assert res.status_code == 400 and res.get_json()["error"] == "too_many_images"
    for bad in (["../etc"], ["0" * 32], "notalist"):
        res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "x",
                                                         "image_ids": bad}, headers=api_headers(logged_in))
        assert res.status_code == 400 and res.get_json()["error"] == "bad_image", bad


def test_someone_elses_photo_cannot_be_sent(app, logged_in):
    other = app.test_client()
    signup(other, username="other_user")
    theirs = staged(other)
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "x",
                                                     "image_ids": [theirs]}, headers=api_headers(logged_in))
    assert res.status_code == 400 and res.get_json()["error"] == "bad_image"


def test_each_photo_uses_one_more_question(app, logged_in, repo):
    app.config["CHAT_MESSAGES_PER_DAY"] = 3
    set_provider(app, ReadingProvider())
    ids = [staged(logged_in) for _ in range(3)]
    res = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "봐줘",
                                                     "image_ids": ids}, headers=api_headers(logged_in))
    assert res.status_code == 429
    body = res.get_json()
    assert body["error"] == "daily_limit" and body["needed"] == 4 and "남은 질문은 3개" in body["message"]
    assert logged_in.get("/home").status_code == 200
    ok = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "봐줘",
                                                    "image_ids": ids[:2]}, headers=api_headers(logged_in))
    assert ok.status_code == 201 and ok.get_json()["daily"]["remaining"] == 0


# ---------------------------------------------------------------- trash
def make_conv(client, content="연락 문제로 고민이야"):
    res = client.post("/api/conversations", json={"philosopher_id": "plato", "content": content},
                      headers=api_headers(client))
    return res.get_json()["conversation_id"]


def test_delete_moves_to_trash_and_restore_brings_it_back(logged_in, repo):
    cid = make_conv(logged_in)
    page = logged_in.get(f"/c/{cid}/delete").get_data(as_text=True)
    assert "휴지통으로 옮길까요?" in page
    res = logged_in.post(f"/c/{cid}/delete", data={"csrf_token": api_headers(logged_in)["X-CSRF-Token"]})
    assert res.status_code == 302 and res.headers["Location"].endswith("/history")
    assert repo.get_conversation(uid(logged_in), cid)["deleted_at"]
    assert logged_in.get(f"/c/{cid}").status_code == 404
    assert f"/c/{cid}" not in logged_in.get("/history").get_data(as_text=True)
    assert f"/c/{cid}" not in logged_in.get("/home").get_data(as_text=True)
    trash = logged_in.get("/trash").get_data(as_text=True)
    assert "연락 문제로" in trash and "30일이 지나면" in trash
    res = logged_in.post(f"/trash/{cid}/restore", data={"csrf_token": api_headers(logged_in)["X-CSRF-Token"]})
    assert res.status_code == 302 and res.headers["Location"].endswith(f"/c/{cid}")
    assert logged_in.get(f"/c/{cid}").status_code == 200
    assert "deleted_at" not in repo.get_conversation(uid(logged_in), cid)


def test_permanent_delete_removes_messages_and_photos(app, logged_in, repo):
    set_provider(app, ReadingProvider())
    photo = staged(logged_in)
    cid = logged_in.post("/api/conversations", json={"philosopher_id": "plato", "content": "봐줘",
                                                     "image_ids": [photo]},
                         headers=api_headers(logged_in)).get_json()["conversation_id"]
    token = api_headers(logged_in)["X-CSRF-Token"]
    assert logged_in.post(f"/trash/{cid}/delete", data={"csrf_token": token}).status_code == 404  # not trashed yet
    logged_in.post(f"/c/{cid}/delete", data={"csrf_token": token})
    res = logged_in.post(f"/trash/{cid}/delete", data={"csrf_token": token})
    assert res.status_code == 302 and res.headers["Location"].endswith("/trash")
    assert repo.get_conversation(uid(logged_in), cid) is None
    assert repo.get_messages(uid(logged_in), cid) == []
    assert logged_in.get(f"/images/{photo}-0").status_code == 404


def test_empty_trash_and_auto_purge(logged_in, repo):
    token = api_headers(logged_in)["X-CSRF-Token"]
    a, b = make_conv(logged_in, "첫 번째"), make_conv(logged_in, "두 번째")
    for cid in (a, b):
        logged_in.post(f"/c/{cid}/delete", data={"csrf_token": token})
    repo.conversations[uid(logged_in)][a]["deleted_at"] = utcnow() - timedelta(days=31)
    page = logged_in.get("/trash").get_data(as_text=True)
    assert "첫 번째" not in page and "두 번째" in page
    assert repo.get_conversation(uid(logged_in), a) is None  # purged on the way
    logged_in.post("/trash/empty", data={"csrf_token": token})
    assert repo.get_conversation(uid(logged_in), b) is None
    assert "휴지통이 비어 있어요" in logged_in.get("/trash").get_data(as_text=True)


def test_trash_actions_are_owner_only(app, logged_in):
    cid = make_conv(logged_in)
    token = api_headers(logged_in)["X-CSRF-Token"]
    logged_in.post(f"/c/{cid}/delete", data={"csrf_token": token})
    other = app.test_client()
    signup(other, username="intruder_1")
    t2 = api_headers(other)["X-CSRF-Token"]
    assert other.post(f"/trash/{cid}/restore", data={"csrf_token": t2}).status_code == 404
    assert other.post(f"/trash/{cid}/delete", data={"csrf_token": t2}).status_code == 404


def test_settings_and_history_link_to_trash(logged_in):
    assert 'href="/trash"' in logged_in.get("/settings").get_data(as_text=True)
    assert 'href="/trash"' in logged_in.get("/history").get_data(as_text=True)
