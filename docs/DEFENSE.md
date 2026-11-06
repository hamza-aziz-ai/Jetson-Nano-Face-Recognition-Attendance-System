# DEFENSE: Jetson Nano Face Recognition Attendance System

## What it does

A small NVIDIA Jetson Nano board watches one camera, recognizes the people it has been taught, and keeps an attendance record: who arrived, when they were first and last seen, with a photo of each sighting. Everything happens on the board. Nothing is sent anywhere.

## How it works

The camera's video is decoded by the board's video hardware. A face detector finds faces and five landmarks (eyes, nose, mouth corners). A tracker follows each face across frames so a person is recognized once per visit, not 15 times a second. The face is straightened using the landmarks and turned into 512 numbers by a recognition model; those numbers are compared with the numbers of enrolled photos. A close enough match records attendance; otherwise the system tries a few more times and then records an unknown face.

A web page shows today's present and absent people and unknown faces, and can export a CSV. A live view and an RTSP stream show the camera with names drawn on it. Each attendance event also keeps a full-size picture and a short video clip as evidence.

## Why these choices

- **On the device, not a server:** no network dependency, no data leaving the building, one low-power box per entrance.
- **Track first, recognize once:** recognition is the expensive step; tracking keeps the board real-time.
- **Hardware encoders for video:** the GPU is fully used by detection, so video output uses the separate JPEG and H.264 blocks.
- **Files instead of a database:** a few hundred events a day need no database server on a 4 GB board.

## Where it fails

- Faces smaller than 40 px, strongly turned, or upside down are not recognized.
- More than about 19 frames per second of detection is beyond the GPU.
- The match threshold is not tuned on a labelled set; a lookalike could match.
- It is plain HTTP on the local network: anyone on that network who learns the password can see faces.

## Security in one line

Logins on the page and the stream, strict browser security headers, no secrets in the repository, and biometric data that never leaves the device.
