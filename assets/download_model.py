#!/usr/bin/env python3
"""Download the YuNet face-detection model used by the facecam layout.
clip_watcher.py calls this automatically on first use, but you can also
run it manually:  python assets/download_model.py
"""
import os
import urllib.request

URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
DEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "face_detection_yunet_2023mar.onnx")

if os.path.exists(DEST):
    print(f"Already present: {DEST}")
else:
    print(f"Downloading {URL} ...")
    urllib.request.urlretrieve(URL, DEST)
    print(f"Saved to {DEST}")
