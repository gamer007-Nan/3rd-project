#!/usr/bin/env python3
"""Computer-vision pipeline (report sections 3.6 and 4.4.3).

OpenCV reads frames, YOLOv8 detects and tracks products, and ExitTracker raises a
shelf-exit event when a tracked product that was inside the shelf zone leaves it
(or vanishes from inside it, which is how concealment looks to a camera).
The event is POSTed to the Flask app, whose correlation engine decides if a
matching sale exists. The camera alone never accuses anyone.

  python detector.py --source 0 --api http://localhost:5000 --key CAMERA_KEY \
      --zone 0.05,0.10,0.45,0.90 --show
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


class ExitTracker:
    """Pure logic, no camera or model needed, so it can be unit-tested."""

    def __init__(self, zone, min_inside=5, min_outside=5, lost_frames=15):
        self.zone = zone  # (x1, y1, x2, y2) in pixels
        self.min_inside, self.min_outside, self.lost_frames = min_inside, min_outside, lost_frames
        self.tracks = {}

    def _inside(self, cx, cy):
        x1, y1, x2, y2 = self.zone
        return x1 <= cx <= x2 and y1 <= cy <= y2

    def update(self, track_id, label, cx, cy):
        s = self.tracks.setdefault(track_id, {"label": label, "inside": 0, "outside": 0,
                                              "confirmed": False, "fired": False, "missing": 0})
        s["missing"] = 0
        if self._inside(cx, cy):
            s["inside"] += 1
            s["outside"] = 0
            if s["inside"] >= self.min_inside:
                s["confirmed"], s["fired"] = True, False  # (re)entered: ready to fire again
            return None
        s["outside"] += 1
        s["inside"] = 0
        if s["confirmed"] and not s["fired"] and s["outside"] >= self.min_outside:
            s["fired"] = True
            return {"label": s["label"], "track_id": track_id, "reason": "left_zone"}
        return None

    def end_frame(self, seen_ids):
        events = []
        for tid in list(self.tracks):
            s = self.tracks[tid]
            if tid in seen_ids:
                continue
            s["missing"] += 1
            if s["confirmed"] and not s["fired"] and s["missing"] >= self.lost_frames:
                s["fired"] = True
                events.append({"label": s["label"], "track_id": tid, "reason": "lost_in_zone"})
            if s["missing"] > self.lost_frames * 3:
                del self.tracks[tid]
        return events


def load_class_map(path):
    with open(path, encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def post_event(api, key, product):
    field = "product_id" if isinstance(product, int) else "sku"
    req = urllib.request.Request(api.rstrip("/") + "/camera/events",
                                 data=json.dumps({field: product}).encode(),
                                 headers={"Content-Type": "application/json", "X-API-Key": key},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as exc:
        print("Could not reach the server:", exc)
        return None


def run(args):
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError:
        sys.exit("Install the vision packages first:  pip install opencv-python ultralytics")
    class_map = load_class_map(args.class_map)
    model = YOLO(args.weights)  # yolov8n.pt downloads on first run (needs internet)
    cap = cv2.VideoCapture(int(args.source) if args.source.isdigit() else args.source)
    if not cap.isOpened():
        sys.exit(f"Cannot open video source: {args.source}")
    fx1, fy1, fx2, fy2 = [float(v) for v in args.zone.split(",")]
    tracker, frame_no = None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame_no += 1
        if frame_no % args.stride:
            continue  # CPU-only: process every Nth frame
        h, w = frame.shape[:2]
        if tracker is None:
            tracker = ExitTracker((fx1 * w, fy1 * h, fx2 * w, fy2 * h), args.min_inside, args.min_outside, args.lost)
        result = model.track(frame, persist=True, conf=args.conf, verbose=False)[0]
        seen, events = set(), []
        if result.boxes is not None and result.boxes.id is not None:
            for (x1, y1, x2, y2), tid, cls in zip(result.boxes.xyxy.tolist(),
                                                  result.boxes.id.int().tolist(),
                                                  result.boxes.cls.int().tolist()):
                label = result.names[cls]
                if label not in class_map:
                    continue
                seen.add(tid)
                ev = tracker.update(tid, label, (x1 + x2) / 2, (y1 + y2) / 2)
                if ev:
                    events.append(ev)
        events += tracker.end_frame(seen)
        for ev in events:
            reply = post_event(args.api, args.key, class_map[ev["label"]])
            print(f"Exit: {ev['label']} ({ev['reason']}) -> {reply}")
        if args.show:
            zx1, zy1, zx2, zy2 = [int(v) for v in tracker.zone]
            cv2.rectangle(frame, (zx1, zy1), (zx2, zy2), (0, 200, 255), 2)
            cv2.imshow("Shelf monitor (q to quit)", result.plot() if len(seen) else frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    cap.release()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default="0", help="camera index or video file")
    p.add_argument("--api", default="http://localhost:5000")
    p.add_argument("--key", default=os.environ.get("CAMERA_API_KEY", ""))
    p.add_argument("--weights", default="yolov8n.pt", help="use your fine-tuned best.pt here")
    p.add_argument("--zone", default="0.05,0.10,0.45,0.90", help="shelf zone as fractions x1,y1,x2,y2")
    p.add_argument("--class-map", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "class_map.json"))
    p.add_argument("--conf", type=float, default=0.35)
    p.add_argument("--stride", type=int, default=3)
    p.add_argument("--min-inside", type=int, default=5)
    p.add_argument("--min-outside", type=int, default=5)
    p.add_argument("--lost", type=int, default=15)
    p.add_argument("--show", action="store_true", help="show a live window (needs a display)")
    args = p.parse_args()
    if not args.key:
        sys.exit("Set --key or CAMERA_API_KEY (must match the server).")
    run(args)
