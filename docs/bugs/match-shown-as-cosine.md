# Match shown as cosine while L2 decided

Date: 2026-11-07. Status: fixed in v1.11.2.

**Symptom:** With face matching set to L2 (L2 <= 0.90, System page), the Overview showed matches such as `-0.04`, `0.52` and `0.62`. The owner: "only COSINE has range [-1, 1] but L2 has range [0, 2]".

**Cause:** Each event stores both numbers (`score` = best cosine, `l2` = the matching L2 distance, `metric` = the rule that decided, FLOW 2 step 13), but the website always printed `score`. Recognition itself used L2 (log: `match L2 <= 0.90`; events `"metric":"l2"`), so names were right; only the number shown was the other measure.

**Fix:** `match_text()` shows the deciding rule's number: `L2 1.44` when `metric` is `l2`, the cosine otherwise (events before the `metric` field are cosine). Used on the Overview, the Attendance table (the closest sighting's event) and the unknown faces; the CSV gains `match_rule` before `best_match`.

**Test:** web self-test (`L2 1.44` for the owner's event `score -0.035, l2 1.439`; cosine events unchanged; the closest sighting kept).
