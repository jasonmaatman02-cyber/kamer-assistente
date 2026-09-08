from picamera2 import Picamera2
import cv2
import time

# Camera initialiseren
picam2 = Picamera2()
picam2.start()
time.sleep(2)  # even wachten tot de camera klaar is

try:
    while True:
        frame = picam2.capture_array()  # capture als numpy array
        cv2.imshow("Live Feed", frame)  # toon het frame in een venster

        # Stop met 'q'
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
finally:
    picam2.stop()