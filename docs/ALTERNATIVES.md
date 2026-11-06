# Alternatives considered

Short list of what was not chosen and why. The full reasoning, trade-offs and revisit conditions are in DECISIONS.md.

| Area | Not chosen | Why not | Decision |
| --- | --- | --- | --- |
| Language | Python + pyds | Bindings not installed on JetPack 4.6 | D-001 |
| Detector output | Custom bbox parser library | DeepStream 6.0 parser API has no landmarks | D-002 |
| Landmark transport | NvDsUserMeta | More code for the same result | D-003 |
| Tracker | NvDCF | Uses the GPU the detector needs | D-004 |
| Recognition | Secondary nvinfer every frame | 41-48 ms per face, every frame | D-005 |
| Detector input | 640x640 | 92 ms vs 51-55 ms | D-006 |
| Frame rate control | Detector interval | More conversion and tracking work for the same detections | D-007 |
| Storage of events | SQLite | Library not installed, not needed at this volume | D-009 |
| Web framework | Flask / FastAPI | Dependencies on Python 3.6, larger surface | D-010 |
| Live view | HLS, WebRTC | Needs a player or signalling; MJPEG plays everywhere | D-012 |
| RTSP server | Installing -dev packages | Avoided device package changes | D-013 |
| Recording | Continuous | About 43 GB a day | D-014 |
| Recording space | Real partition | Risky offline root resize | D-015 |
| App location | External SSD | It is the owner's Windows backup disk | D-017 |
| CSI camera | Rewire the enable line | Undocumented, risk of damage; R8 removal or a Jetson-specific board instead | bugs/csi-hq-camera-not-detected.md |
