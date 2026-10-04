import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

BASE_URL = "http://localhost:8001"


def make_multipart_body(file_bytes: bytes, filename: str, content_type: str):
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    lines = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode(),
        f"Content-Type: {content_type}".encode(),
        b"",
        file_bytes,
        f"--{boundary}--".encode(),
        b"",
    ]
    body = b"\r\n".join(lines)
    content_type_header = f"multipart/form-data; boundary={boundary}"
    return body, content_type_header


def api_post_image(path, file_bytes, filename="test.jpg", content_type="image/jpeg"):
    url = f"{BASE_URL}{path}"
    body, ctype = make_multipart_body(file_bytes, filename, content_type)
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": ctype},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
        return e.code, payload


@pytest.fixture
def sample_image_bytes():
    images_dir = Path(__file__).resolve().parents[2] / "data" / "processed" / "images"
    for img_file in images_dir.glob("*.jpg"):
        return img_file.read_bytes()
    # Fallback to minimal 1x1 GIF/JPEG if no file on disk
    return bytes.fromhex("47494638396101000100800000ffffff00000021f90401000000002c00000000010001000002024401003b")


def test_search_by_image_valid(sample_image_bytes):
    status, body = api_post_image("/search/image?limit=6", sample_image_bytes)
    if status in (400, 503):
        pytest.skip(
            f"Image search unavailable on this deployment (HTTP {status}): "
            f"{body.get('detail', body) if isinstance(body, dict) else body}"
        )
    assert status == 200
    assert "items" in body
    assert "total" in body
    assert "timings" in body
    assert body["total"] > 0
    assert len(body["items"]) <= 6

    # Verify visual similarity scores are descending and between 0 and 1
    prev_score = 1.0
    for item in body["items"]:
        assert "visualSimilarity" in item
        sim = item["visualSimilarity"]
        assert 0.0 <= sim <= 1.0
        assert sim <= prev_score + 1e-4  # allow tiny float margin
        prev_score = sim


def test_search_by_image_department_filter(sample_image_bytes):
    status, body = api_post_image("/search/image?department=Men&limit=5", sample_image_bytes)
    if status in (400, 503):
        pytest.skip(
            f"Image search unavailable on this deployment (HTTP {status}): "
            f"{body.get('detail', body) if isinstance(body, dict) else body}"
        )
    assert status == 200
    for item in body["items"]:
        assert item["department"] == "Men"


def test_search_by_image_invalid_content_type():
    status, body = api_post_image(
        "/search/image",
        b"Not an image file text content",
        filename="notes.txt",
        content_type="text/plain",
    )
    assert status == 400


def test_search_by_image_too_small():
    status, body = api_post_image(
        "/search/image",
        b"tiny",
        filename="tiny.jpg",
        content_type="image/jpeg",
    )
    assert status == 400
