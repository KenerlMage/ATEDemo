"""光斑检测算法测试：用已知参数的高斯斑验证数量、中心、直径与边界处理。"""

from __future__ import annotations

import numpy as np

from camera_assistant.analyze import analyze, otsu_threshold
from camera_assistant.models import AnalyzeParams


def make_frame(spots=((80, 60, 180, 7, 7), (200, 120, 220, 9, 9), (120, 180, 160, 5, 5)),
               h: int = 240, w: int = 320, bg: float = 12.0, noise: float = 1.5) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.full((h, w), bg, dtype=np.float64)
    for cx, cy, amp, sx, sy in spots:
        img += amp * np.exp(-(((xx - cx) ** 2) / (2.0 * sx * sx) + ((yy - cy) ** 2) / (2.0 * sy * sy)))
    rng = np.random.default_rng(3)
    img += rng.normal(0.0, noise, size=(h, w))
    return np.clip(img, 0, 255).astype(np.uint8)


def test_detects_expected_blob_count():
    result = analyze(make_frame(), AnalyzeParams(threshold_mode="otsu", min_area=8))
    assert result.count == 3, result.summary()
    assert all(b.area >= 8 for b in result.blobs)


def test_centers_match_ground_truth():
    spots = ((80, 60, 180, 7, 7), (200, 120, 220, 9, 9), (120, 180, 160, 5, 5))
    result = analyze(make_frame(spots=spots), AnalyzeParams(threshold_mode="otsu", min_area=8))
    found = sorted(((b.cx, b.cy) for b in result.blobs))
    truth = sorted(((float(x), float(y)) for x, y, *_ in spots))
    for (fx, fy), (tx, ty) in zip(found, truth):
        assert abs(fx - tx) <= 2.0 and abs(fy - ty) <= 2.0, f"中心偏差过大: {(fx, fy)} vs {(tx, ty)}"


def test_equivalent_diameter_is_consistent_with_area():
    result = analyze(make_frame(), AnalyzeParams(threshold_mode="otsu", min_area=8))
    for blob in result.blobs:
        expected = 2.0 * np.sqrt(blob.area / np.pi)
        assert abs(blob.eq_diameter - expected) < 1e-6


def test_roi_offsets_are_absolute_coordinates():
    frame = make_frame()
    result = analyze(frame, AnalyzeParams(threshold_mode="otsu", min_area=8, roi=(60, 40, 180, 160)))
    assert result.roi == (60, 40, 180, 160)
    # ROI 内包含 (80,60) 与 (120,180) 之外的两个斑，(200,120) 恰在右边界外
    assert result.count >= 1
    for blob in result.blobs:
        assert 60 <= blob.cx <= 240 and 40 <= blob.cy <= 200


def test_empty_and_dark_frames_return_empty_result():
    blank = np.full((120, 160), 8, dtype=np.uint8)
    result = analyze(blank, AnalyzeParams(threshold_mode="absolute", threshold_value=60))
    assert result.count == 0
    assert result.warn

    nothing = analyze(np.zeros((0, 0), dtype=np.uint8), AnalyzeParams())
    assert nothing.count == 0 and nothing.warn == "空帧"


def test_min_area_filters_small_specks():
    frame = np.full((120, 160), 10, dtype=np.uint8)
    frame[20:22, 20:22] = 200        # 4 像素
    frame[60:72, 80:92] = 200        # 144 像素
    result = analyze(frame, AnalyzeParams(threshold_mode="absolute", threshold_value=100, min_area=20))
    assert result.count == 1
    assert result.blobs[0].area >= 100


def test_invert_mode_detects_dark_spots():
    frame = np.full((160, 200), 210, dtype=np.uint8)
    yy, xx = np.mgrid[0:160, 0:200]
    frame = np.clip(frame - 170.0 * np.exp(-(((xx - 100) ** 2) + ((yy - 80) ** 2)) / (2 * 8.0 ** 2)), 0, 255)
    result = analyze(frame.astype(np.uint8),
                     AnalyzeParams(threshold_mode="absolute", threshold_value=80, invert=True, min_area=10))
    assert result.count == 1
    assert abs(result.blobs[0].cx - 100) <= 2 and abs(result.blobs[0].cy - 80) <= 2


def test_foreground_guard_warns_on_bad_threshold():
    frame = np.full((120, 160), 200, dtype=np.uint8)
    result = analyze(frame, AnalyzeParams(threshold_mode="absolute", threshold_value=5))
    assert result.count == 0
    assert "阈值可能失准" in result.warn


def test_otsu_threshold_sits_between_classes():
    data = np.concatenate([np.full(900, 20, dtype=np.uint8), np.full(100, 210, dtype=np.uint8)])
    thr = otsu_threshold(data)
    assert 20 <= thr < 210


def test_saturated_flag_marks_clipped_blobs():
    frame = np.full((80, 80), 10, dtype=np.uint8)
    frame[30:50, 30:50] = 255
    result = analyze(frame, AnalyzeParams(threshold_mode="absolute", threshold_value=100,
                                         min_area=20, saturate_level=250))
    assert result.count == 1
    assert result.blobs[0].saturated is True
    assert result.blobs[0].peak == 255

def test_relative_threshold_mode():
    frame = make_frame(bg=10.0, noise=0.5)
    result = analyze(frame, AnalyzeParams(threshold_mode="relative", threshold_value=50, min_area=8))
    assert result.count == 3
    assert result.threshold > 10
