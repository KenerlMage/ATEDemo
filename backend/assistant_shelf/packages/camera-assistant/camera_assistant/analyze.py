"""光斑检测算法：纯 numpy 实现，不依赖 OpenCV / SciPy，便于随工程打包。

处理顺序：ROI 裁切 → 背景估计 → 阈值（Otsu / 绝对 / 相对）→ 连通域 → 形态量测。
输出的坐标一律换算回整幅图像坐标系，便于与界面上的 ROI 框对照。
"""

from __future__ import annotations

import time
from typing import Optional, Tuple

import numpy as np

from .models import AnalyzeParams, AnalyzeResult, Blob

# 前景占比超过该比例视为阈值失准：既无物理意义，也会让连通域遍历变慢
FOREGROUND_GUARD = 0.60
MAX_FOREGROUND_PIXELS = 150_000


def otsu_threshold(values: np.ndarray) -> float:
    """一维 Otsu：在 0..255 直方图上最大化类间方差，返回分割阈值。"""
    hist = np.bincount(values.astype(np.uint8).ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 0.0
    levels = np.arange(256, dtype=np.float64)
    sum_all = float((levels * hist).sum())
    weight_bg = 0.0
    sum_bg = 0.0
    best_var = -1.0
    best_t = 0.0
    for t in range(256):
        weight_bg += hist[t]
        if weight_bg <= 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg <= 0:
            break
        sum_bg += levels[t] * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_all - sum_bg) / weight_fg
        var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var > best_var:
            best_var = var
            best_t = float(t)
    return best_t


def _label_foreground(mask: np.ndarray) -> Tuple[np.ndarray, int]:
    """四邻域连通域标记。

    只在前景像素上建索引，避免逐像素扫描整幅图像；返回 (labels, 连通域个数)。
    """
    labels = np.zeros(mask.shape, dtype=np.int32)
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return labels, 0

    position = {(int(y), int(x)): i for i, (y, x) in enumerate(zip(ys, xs))}
    visited = bytearray(ys.size)
    count = 0
    for start in range(ys.size):
        if visited[start]:
            continue
        count += 1
        visited[start] = 1
        stack = [start]
        while stack:
            j = stack.pop()
            y, x = int(ys[j]), int(xs[j])
            labels[y, x] = count
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                k = position.get((ny, nx))
                if k is not None and not visited[k]:
                    visited[k] = 1
                    stack.append(k)
    return labels, count


def _threshold(crop: np.ndarray, params: AnalyzeParams) -> Tuple[float, str]:
    """返回 (阈值, 告警)。Otsu 在低对比场景可能给出接近背景的值，此时补一条提示。"""
    bg = float(np.percentile(crop, params.bg_percentile))
    peak = float(crop.max())
    mode = (params.threshold_mode or "otsu").lower()

    if mode == "absolute":
        return float(params.threshold_value), ""
    if mode == "relative":
        span = max(peak - bg, 1e-6)
        return bg + span * float(np.clip(params.threshold_value, 0.0, 100.0)) / 100.0, ""
    thr = otsu_threshold(crop)
    if thr <= bg + 1.0:
        return float(min(bg + 3.0, peak)), "Otsu 阈值贴近背景，已自动上抬 3 灰阶"
    return thr, ""


def analyze(array: np.ndarray, params: AnalyzeParams, frame_index: int = -1) -> AnalyzeResult:
    """对一帧灰度图执行检测，返回结构化结果。异常输入返回空结果而不抛错。"""
    t0 = time.perf_counter()
    if array is None or array.size == 0:
        return AnalyzeResult(frame_index=frame_index, warn="空帧")

    gray = array.astype(np.float32) if array.ndim == 2 else array.astype(np.float32).mean(axis=2)
    height, width = gray.shape[:2]
    x0, y0, w, h = params.roi_or_full(width, height)
    crop = gray[y0:y0 + h, x0:x0 + w]

    thr, warn = _threshold(crop, params)
    mask = crop > thr if not params.invert else crop < thr
    if not mask.any():
        return AnalyzeResult(threshold=thr, mask=mask, roi=(x0, y0, w, h),
                             elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                             warn=(warn + "；未检出前景" if warn else "未检出前景"),
                             frame_index=frame_index)

    foreground = int(mask.sum())
    ratio = foreground / float(mask.size)
    if ratio > FOREGROUND_GUARD or foreground > MAX_FOREGROUND_PIXELS:
        return AnalyzeResult(threshold=thr, mask=mask, roi=(x0, y0, w, h),
                             elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                             warn=f"前景占比 {ratio * 100:.0f}%，阈值可能失准（建议提高阈值或检查反相）",
                             frame_index=frame_index)

    labels, count = _label_foreground(mask)
    if count == 0:
        return AnalyzeResult(threshold=thr, mask=mask, roi=(x0, y0, w, h),
                             elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                             warn=warn, frame_index=frame_index)

    flat = labels.ravel()
    area = np.bincount(flat, minlength=count + 1)[1:].astype(np.float64)
    ys, xs = np.nonzero(mask)
    lb = labels[ys, xs].astype(np.int64)
    vals = crop[ys, xs].astype(np.float64)

    sum_w = np.bincount(lb, weights=vals, minlength=count + 1)[1:]
    sum_wx = np.bincount(lb, weights=vals * xs, minlength=count + 1)[1:]
    sum_wy = np.bincount(lb, weights=vals * ys, minlength=count + 1)[1:]
    sum_x = np.bincount(lb, weights=xs.astype(np.float64), minlength=count + 1)[1:]
    sum_y = np.bincount(lb, weights=ys.astype(np.float64), minlength=count + 1)[1:]

    min_x = np.full(count + 1, 10 ** 9)
    min_y = np.full(count + 1, 10 ** 9)
    max_x = np.full(count + 1, -1)
    max_y = np.full(count + 1, -1)
    peak = np.zeros(count + 1)
    sat = np.zeros(count + 1)
    np.minimum.at(min_x, lb, xs)
    np.minimum.at(min_y, lb, ys)
    np.maximum.at(max_x, lb, xs)
    np.maximum.at(max_y, lb, ys)
    np.maximum.at(peak, lb, vals)
    np.add.at(sat, lb, (vals >= params.saturate_level).astype(np.float64))

    candidates = []
    for i in range(1, count + 1):
        a = float(area[i - 1])
        if a < params.min_area or a <= 0:
            continue
        cx = float(sum_wx[i - 1] / max(sum_w[i - 1], 1e-9))
        cy = float(sum_wy[i - 1] / max(sum_w[i - 1], 1e-9))
        gx = float(sum_x[i - 1] / a)
        gy = float(sum_y[i - 1] / a)
        bw = int(max_x[i] - min_x[i] + 1)
        bh = int(max_y[i] - min_y[i] + 1)
        candidates.append(Blob(
            index=0,
            cx=cx + x0, cy=cy + y0,
            gx=gx + x0, gy=gy + y0,
            area=int(a),
            eq_diameter=float(2.0 * np.sqrt(a / np.pi)),
            bbox=(int(min_x[i]) + x0, int(min_y[i]) + y0, bw, bh),
            peak=int(round(float(peak[i]))),
            mean=float(sum_w[i - 1] / a),
            saturated=bool(sat[i] > 0),
        ))

    candidates.sort(key=lambda b: (-b.area, b.gx, b.gy))
    candidates = candidates[: max(1, int(params.max_blobs))]
    for n, blob in enumerate(candidates, start=1):
        blob.index = n

    elapsed = (time.perf_counter() - t0) * 1000.0
    if not candidates:
        warn = (warn + "；" if warn else "") + f"共 {count} 个连通域，均小于最小面积 {params.min_area}"
    return AnalyzeResult(blobs=candidates, threshold=thr, mask=mask, roi=(x0, y0, w, h),
                         elapsed_ms=elapsed, warn=warn, frame_index=frame_index)


def crop_with_mask(array: np.ndarray, result: AnalyzeResult) -> Optional[np.ndarray]:
    """按结果里的 ROI 裁出原图，供界面显示。"""
    if array is None or array.size == 0:
        return None
    x, y, w, h = result.roi
    return array[y:y + h, x:x + w]
