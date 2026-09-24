import jpg_lossless_web as module


def test_compress_to_target_chooses_highest_quality_near_target(monkeypatch):
    calls = []

    def fake_compress(src, dst, fmt, eng, quality, level):
        calls.append(quality)
        return quality * 100

    monkeypatch.setattr(module, "compress_one", fake_compress)
    q, size, in_range, near = module._compress_to_target(
        "src", "dst", "JPG", None, 80, -5, 8000, tolerance=1000)

    assert q == 90
    assert size == 9000
    assert in_range and near
    assert calls[-1] == q
