# Check: cosine of facepipe embeddings (JSONL, tracks in photo order) against CCTV InsightFace-REST on the same photos.
# Usage: python -I check_vs_rest.py <photo_dir> <dir_with_faces.jsonl>   (REST on localhost:6385; expect diagonal >= 0.96)
import base64, json, sys, urllib.request, numpy as np
d = sys.argv[1]; S = sys.argv[2]
names = ["front", "left_30", "right_30", "front_glasses"]
ref = []
for n in names:
    body = json.dumps({"images": {"data": [base64.b64encode(open(f"{d}/{n}.jpg", "rb").read()).decode()]}, "extract_embedding": True}).encode()
    im = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:6385/extract", body, {"Content-Type": "application/json", "Accept": "application/json"}), timeout=60))["data"][0]
    f = max(im["faces"], key=lambda f: f["prob"])
    e = np.array(f["vec"], dtype=np.float32); ref.append(e / np.linalg.norm(e))
nano = [np.array(json.loads(l)["emb"], dtype=np.float32) for l in open(f"{S}/faces.jsonl")]
print("cosine nano track vs REST (rows: nano tracks 0-3, cols: REST", names, ")")
for i, n in enumerate(nano):
    print(names[i].ljust(14), " ".join(f"{float(n @ e):.4f}" for e in ref))
