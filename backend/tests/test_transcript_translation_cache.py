from app.services.transcript_translation_cache import compute_hash


def test_compute_hash_deterministic():
    assert compute_hash("Hello world", "vi") == compute_hash("Hello world", "vi")


def test_compute_hash_distinguishes_target_lang():
    assert compute_hash("Hello world", "vi") != compute_hash("Hello world", "en")


def test_compute_hash_distinguishes_text():
    assert compute_hash("Hello", "vi") != compute_hash("Hello world", "vi")


def test_compute_hash_strips_whitespace():
    assert compute_hash("Hello world", "vi") == compute_hash("  Hello world  ", "vi")
