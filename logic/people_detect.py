"""Mensen tellen in een camerabeeld — geen gezichtsherkenning, alleen "hoeveel".

Gebruikt OpenCV's ingebouwde HOG-mensdetector (zit al in ``opencv-python``,
dus geen extra model-download of dependency nodig — belangrijk op een Pi 4B).
Minder nauwkeurig dan een DNN-model, maar ruim genoeg om 0 / 1 / 2 / 3+ te
onderscheiden voor aanwezigheids-automatisering. Er wordt nooit een beeld
opgeslagen: het frame wordt alleen in het geheugen verwerkt en meteen
weggegooid (privacy — zie Dashboard.backend.presence).
"""
from __future__ import annotations

_hog = None


def _get_detector():
    """Lazy singleton — de detector maak je maar één keer aan."""
    global _hog
    if _hog is None:
        import cv2

        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        _hog = hog
    return _hog


def _to_xyxy(rects):
    return [(float(x), float(y), float(x + w), float(y + h)) for (x, y, w, h) in rects]


def non_max_suppression(boxes, scores, overlap_thresh: float = 0.45):
    """Kleine, dependency-vrije NMS: filtert overlappende detecties van
    dezelfde persoon eruit. ``boxes`` = lijst van (x1,y1,x2,y2). Geeft de
    indices terug die overblijven."""
    if not boxes:
        return []
    import numpy as np

    b = np.array(boxes, dtype=float)
    s = np.array(scores, dtype=float) if len(scores) == len(boxes) else np.zeros(len(boxes))
    x1, y1, x2, y2 = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = s.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        overlap = np.where(areas[rest] > 0, (w * h) / areas[rest], 0.0)
        order = rest[overlap <= overlap_thresh]
    return keep


def count_people(frame_bgr, *, scale: float = 1.0, hit_threshold: float = 0.0) -> int:
    """Tel het aantal mensen in een BGR-frame (numpy array, zoals opencv ze
    levert). Geeft 0 bij een leeg/ongeldig frame of een detectiefout — telt
    NOOIT als een crash, dit draait onbeheerd op een achtergrond-thread."""
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return 0
    try:
        import cv2

        hog = _get_detector()
        img = frame_bgr
        if scale != 1.0:
            img = cv2.resize(img, None, fx=scale, fy=scale)
        rects, weights = hog.detectMultiScale(
            img, winStride=(8, 8), padding=(8, 8), scale=1.05, hitThreshold=hit_threshold,
        )
        if len(rects) == 0:
            return 0
        keep = non_max_suppression(_to_xyxy(rects), list(weights), overlap_thresh=0.45)
        return len(keep)
    except Exception as exc:  # noqa: BLE001 - detectie mag nooit de worker slopen
        print(f"[PEOPLE] detectiefout: {exc}")
        return 0
