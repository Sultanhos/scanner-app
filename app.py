"""
Scanner — a small web app that turns photos of paper into clean,
flattened, professional-looking scans.

Run with:  python app.py
Then open: http://localhost:5000
"""

import io
import os
import uuid

import cv2
import img2pdf
import numpy as np
from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE_DIR, "processed")
os.makedirs(PROCESSED_DIR, exist_ok=True)

MAX_DIMENSION = 1600  # working resolution for edge detection (speed)


# --------------------------------------------------------------------------
# Image processing
# --------------------------------------------------------------------------

def order_points(pts):
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def _largest_quad_from_mask(mask, ratio, min_area_frac=0.15, max_area_frac=0.98):
    """Given a binary mask, find the largest external contour, take its
    convex hull, and fit a minimum-area rectangle. Using minAreaRect
    (instead of a noisy 4-point polygon approximation) is what keeps the
    resulting crop a clean, symmetric rectangle instead of a ragged
    quadrilateral that follows every dent in the detected edge."""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    area_frac = cv2.contourArea(c) / (mask.shape[0] * mask.shape[1])
    if not (min_area_frac <= area_frac <= max_area_frac):
        return None
    hull = cv2.convexHull(c)
    rect = cv2.minAreaRect(hull)
    box = cv2.boxPoints(rect)
    return (box / ratio).astype("float32")


def find_document_contour(image):
    """Find a clean, symmetric rectangle around the document.

    Tries two strategies and uses whichever gives a plausible result:
    1. Edge-based: a Canny edge map. This is the original, well-tested
       method — reliable across normal lighting and most backgrounds.
    2. Brightness-based: a plain threshold. Only used as a fallback, for
       the specific case of a page on a strongly darker surface where
       edges alone are faint (e.g. a phone photo on a dark table).
    """
    h, w = image.shape[:2]
    ratio = MAX_DIMENSION / max(h, w) if max(h, w) > MAX_DIMENSION else 1.0
    small = cv2.resize(image, (int(w * ratio), int(h * ratio)))
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Strategy 1: edges (primary — most reliable across lighting conditions)
    edged = cv2.Canny(blurred, 50, 150)
    edged = cv2.dilate(edged, np.ones((3, 3), np.uint8), iterations=2)
    edged = cv2.erode(edged, np.ones((3, 3), np.uint8), iterations=1)
    box = _largest_quad_from_mask(edged, ratio, min_area_frac=0.2)
    if box is not None:
        return box

    # Strategy 2: brightness threshold (fallback only, for a dark
    # background where edges are too faint to trace reliably). Stricter
    # area bound since a bad fallback match is worse than no crop.
    _, thresh = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _largest_quad_from_mask(thresh, ratio, min_area_frac=0.25, max_area_frac=0.9)


def four_point_transform(image, pts):
    rect = order_points(pts)
    (tl, tr, br, bl) = rect

    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    max_width = max(int(width_a), int(width_b))

    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_height = max(int(height_a), int(height_b))

    dst = np.array(
        [[0, 0], [max_width - 1, 0], [max_width - 1, max_height - 1], [0, max_height - 1]],
        dtype="float32",
    )

    m = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(image, m, (max_width, max_height))


def clean_border_artifacts(image):
    """Inpaint any leftover dark background that still touches the edge
    after cropping. A rectangular crop can't perfectly follow a page
    whose corner is lifted or rounded — this mops up what's left by
    treating any border-touching patch that's much darker than the page
    itself as background, and reconstructing it from nearby page pixels,
    the same way remove_fingers() handles a stray thumb.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    # sample the middle of the image to estimate how bright real page
    # content is here, since edges are exactly what we don't trust yet
    core = gray[h // 4: 3 * h // 4, w // 4: 3 * w // 4]
    page_level = np.median(core)

    dark_mask = (gray.astype(np.int16) < (page_level - 70)).astype(np.uint8) * 255
    dark_mask = cv2.morphologyEx(dark_mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))

    margin = max(6, int(0.02 * min(h, w)))
    border_mask = np.zeros_like(dark_mask)
    border_mask[:margin, :] = 255
    border_mask[-margin:, :] = 255
    border_mask[:, :margin] = 255
    border_mask[:, -margin:] = 255

    num, labels = cv2.connectedComponents(dark_mask)
    final_mask = np.zeros_like(dark_mask)
    for i in range(1, num):
        comp = (labels == i).astype(np.uint8) * 255
        area = comp.sum() / 255
        if area < 0.0005 * h * w:
            continue
        if np.any(comp & border_mask):
            final_mask |= comp

    if final_mask.sum() == 0:
        return image

    final_mask = cv2.dilate(final_mask, np.ones((7, 7), np.uint8), iterations=2)
    return cv2.inpaint(image, final_mask, 9, cv2.INPAINT_TELEA)


def auto_trim_background(image, max_trim_frac=0.06, bright_ratio=0.7, min_brightness=130):
    """Trim each edge independently until it reaches real page content.

    A rectangle fit around a real (slightly bent/rounded) sheet of paper
    can't always hug every side perfectly, which leaves thin slivers of
    background on one or more edges. Rather than trim a fixed amount off
    all four sides (which would either under-trim the bad side or eat
    into content on the good sides), this scans inward from each edge on
    its own and stops as soon as that row/column looks like paper —
    producing a clean, even border on all sides.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    max_trim_h = int(h * max_trim_frac)
    max_trim_w = int(w * max_trim_frac)

    def scan(get_line, limit):
        for i in range(limit):
            line = get_line(i)
            if (line > min_brightness).mean() > bright_ratio:
                return i
        return 0  # nothing looked bright enough — don't guess, don't trim

    top = scan(lambda i: gray[i, :], max_trim_h)
    bottom = scan(lambda i: gray[h - 1 - i, :], max_trim_h)
    left = scan(lambda i: gray[:, i], max_trim_w)
    right = scan(lambda i: gray[:, w - 1 - i], max_trim_w)

    if h - top - bottom < 10 or w - left - right < 10:
        return image  # safety net against a bad fit collapsing the image

    return image[top: h - bottom, left: w - right]


def dewarp_curl(image):
    """Detect and correct page curl / creases by measuring how the page's
    own text lines bow left-to-right, then flattening that curve.

    This is separate from the four-point perspective correction: that step
    fixes camera *angle* (a tilted photo of a flat sheet). This step fixes
    physical *warping* of the paper itself (curled edges, a bend down the
    middle, a photo taken with the page not held flat) by using the text
    baselines as a reference for what "straight" should look like.

    Returns the corrected image, or the original image unchanged if not
    enough text lines were found to estimate a reliable curve.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    bw = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 35, 15
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 25), 3))
    merged = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel, iterations=1)
    merged = cv2.dilate(merged, np.ones((3, max(3, w // 20)), np.uint8), iterations=1)

    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # only reasonably long lines — short label fragments ("Für:",
    # "Datum:") give noisy, unreliable curve estimates
    lines = []
    for c in contours:
        x, y, bw_, bh_ = cv2.boundingRect(c)
        if bw_ > w * 0.4 and bh_ < h * 0.08:
            lines.append(c)

    if len(lines) < 5:
        return image  # not enough long, reliable lines to trust a curve

    # fit each line's own curve independently first, so we can check
    # whether the lines actually agree with each other before trusting
    # any correction — genuine page curl bends every line the same way;
    # noise from font spacing/short runs does not.
    per_line_curves = []
    common_lo, common_hi = 0, w
    for c in lines:
        mask = np.zeros_like(bw)
        cv2.drawContours(mask, [c], -1, 255, -1)
        roi = bw & mask
        ys, xs = np.where(roi > 0)
        if len(xs) < 80:
            continue
        col_y = {}
        for xi, yi in zip(xs, ys):
            col_y.setdefault(int(xi), []).append(int(yi))
        cols = np.array(sorted(col_y.keys()))
        if cols.max() - cols.min() < w * 0.35:
            continue
        centroids = np.array([np.mean(col_y[cx]) for cx in cols])
        try:
            coeffs = np.polyfit(cols, centroids, 2)
        except (np.linalg.LinAlgError, ValueError):
            continue
        fn = np.poly1d(coeffs)
        common_lo = max(common_lo, cols.min())
        common_hi = min(common_hi if common_hi != w else cols.max(), cols.max())
        per_line_curves.append(fn)

    if len(per_line_curves) < 5 or common_hi - common_lo < w * 0.3:
        return image

    sample_x = np.linspace(common_lo, common_hi, 60)
    stack = np.array([fn(sample_x) - np.mean(fn(sample_x)) for fn in per_line_curves])

    # agreement check: if lines disagree a lot about the curve's shape,
    # what we detected is noise, not real physical curl — bail out
    signal_amplitude = np.mean(np.ptp(stack, axis=1))
    disagreement = np.mean(np.std(stack, axis=0))
    if signal_amplitude < h * 0.006 or disagreement > signal_amplitude * 0.6:
        return image  # too small or too inconsistent to trust

    avg_curve_samples = stack.mean(axis=0)
    try:
        coeffs = np.polyfit(sample_x, avg_curve_samples, 2)
    except (np.linalg.LinAlgError, ValueError):
        return image

    curve_fn = np.poly1d(coeffs)
    curve = curve_fn(np.arange(w))
    curve -= curve.mean()

    # sanity cap: never shift more than 2.5% of the page height — a
    # borderline-plausible fit still should not visibly distort the page
    max_shift = h * 0.025
    curve = np.clip(curve, -max_shift, max_shift)

    map_x, map_y = np.meshgrid(
        np.arange(w).astype(np.float32), np.arange(h).astype(np.float32)
    )
    map_y = map_y + curve.astype(np.float32)[np.newaxis, :]

    return cv2.remap(
        image, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def remove_fingers(image):
    """Detect skin-toned blobs that intrude from the edge of the frame
    (a finger or thumb holding the page) and inpaint over them.

    This is a heuristic, not a trained model: it looks for skin-colored
    regions touching the image border and reconstructs that area from
    surrounding pixels. It works well when the finger overlaps blank
    paper or the background; it cannot recover text that the finger was
    actually covering, since that content was never captured.
    """
    ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
    lower = np.array([0, 133, 77], dtype=np.uint8)
    upper = np.array([255, 180, 130], dtype=np.uint8)
    skin_mask = cv2.inRange(ycrcb, lower, upper)

    kernel = np.ones((7, 7), np.uint8)
    skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_OPEN, kernel)
    skin_mask = cv2.morphologyEx(skin_mask, cv2.MORPH_CLOSE, kernel)

    h, w = skin_mask.shape
    margin = max(10, int(0.03 * min(h, w)))
    border_mask = np.zeros_like(skin_mask)
    border_mask[:margin, :] = 255
    border_mask[-margin:, :] = 255
    border_mask[:, :margin] = 255
    border_mask[:, -margin:] = 255

    num, labels = cv2.connectedComponents(skin_mask)
    final_mask = np.zeros_like(skin_mask)
    for i in range(1, num):
        comp = (labels == i).astype(np.uint8) * 255
        area = comp.sum() / 255
        if area < 0.0008 * h * w:
            continue  # ignore tiny skin-toned specks (not a finger)
        if np.any(comp & border_mask):
            final_mask |= comp

    if final_mask.sum() == 0:
        return image, False

    final_mask = cv2.dilate(final_mask, np.ones((15, 15), np.uint8), iterations=2)
    inpainted = cv2.inpaint(image, final_mask, 9, cv2.INPAINT_TELEA)
    return inpainted, True


def apply_style(image, style):
    """Apply the chosen 'scanned paper' finish."""
    if style == "color":
        lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge((l, a, b))
        result = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        result = cv2.convertScaleAbs(result, alpha=1.08, beta=6)
        return result

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    if style == "grayscale":
        clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
        result = clahe.apply(gray)
        return cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)

    # "bw" — crisp black-and-white document look
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    thresh = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 15
    )
    return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)


def scan_image(image_bytes, style="bw", auto_crop=True, dewarp=True, remove_finger=True):
    file_bytes = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not read image")

    # Downscale once, up front, to a working resolution. Modern phone
    # photos are often 3000-4000px on the long side; running every later
    # step (dewarp, inpainting, thresholding) at that size is what was
    # timing out on a constrained server. This resolution is still
    # plenty sharp for a document scan.
    h0, w0 = image.shape[:2]
    if max(h0, w0) > MAX_DIMENSION:
        scale = MAX_DIMENSION / max(h0, w0)
        image = cv2.resize(image, (int(w0 * scale), int(h0 * scale)), interpolation=cv2.INTER_AREA)

    warped = image
    cropped = False
    if auto_crop:
        contour = find_document_contour(image)
        if contour is not None:
            warped = four_point_transform(image, contour)
            warped = auto_trim_background(warped)
            warped = clean_border_artifacts(warped)
            cropped = True

    if dewarp:
        warped = dewarp_curl(warped)

    finger_removed = False
    if remove_finger:
        warped, finger_removed = remove_fingers(warped)

    result = apply_style(warped, style)
    return result, cropped, finger_removed


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scan", methods=["POST"])
def api_scan():
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    file = request.files["image"]
    style = request.form.get("style", "bw")
    auto_crop = request.form.get("auto_crop", "true") == "true"
    dewarp = request.form.get("dewarp", "true") == "true"
    remove_finger = request.form.get("remove_finger", "true") == "true"

    if style not in ("bw", "grayscale", "color"):
        style = "bw"

    try:
        image_bytes = file.read()
        result, cropped, finger_removed = scan_image(
            image_bytes, style=style, auto_crop=auto_crop, dewarp=dewarp, remove_finger=remove_finger
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 400

    ok, buf = cv2.imencode(".jpg", result, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return jsonify({"error": "Could not encode result"}), 500

    token = uuid.uuid4().hex
    out_path = os.path.join(PROCESSED_DIR, f"{token}.jpg")
    with open(out_path, "wb") as f:
        f.write(buf.tobytes())

    return jsonify({
        "cropped": cropped,
        "finger_removed": finger_removed,
        "token": token,
        "download_url": f"/api/download/{token}",
        "preview_url": f"/api/download/{token}",
    })


@app.route("/api/download/<token>")
def api_download(token):
    safe_token = "".join(c for c in token if c.isalnum())
    path = os.path.join(PROCESSED_DIR, f"{safe_token}.jpg")
    if not os.path.exists(path):
        return jsonify({"error": "Not found"}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route("/api/combine-pdf", methods=["POST"])
def api_combine_pdf():
    """Combine several already-scanned pages (by their tokens) into one
    PDF, in the order given."""
    data = request.get_json(silent=True) or {}
    tokens = data.get("tokens", [])
    if not tokens or not isinstance(tokens, list):
        return jsonify({"error": "No pages given"}), 400

    paths = []
    for token in tokens:
        safe_token = "".join(c for c in str(token) if c.isalnum())
        path = os.path.join(PROCESSED_DIR, f"{safe_token}.jpg")
        if not os.path.exists(path):
            return jsonify({"error": f"Page not found: {token}"}), 404
        paths.append(path)

    try:
        pdf_bytes = img2pdf.convert(paths)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    pdf_token = uuid.uuid4().hex
    pdf_path = os.path.join(PROCESSED_DIR, f"{pdf_token}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    return jsonify({"download_url": f"/api/download-pdf/{pdf_token}"})


@app.route("/api/download-pdf/<token>")
def api_download_pdf(token):
    safe_token = "".join(c for c in token if c.isalnum())
    path = os.path.join(PROCESSED_DIR, f"{safe_token}.pdf")
    if not os.path.exists(path):
        return jsonify({"error": "Not found"}), 404
    return send_file(path, mimetype="application/pdf", as_attachment=True, download_name="scan.pdf")


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
