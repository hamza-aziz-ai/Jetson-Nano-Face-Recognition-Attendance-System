# Enrollment: file left open, crash on an unreadable folder

Date: 2026-11-06. Status: fixed. Found by: CI cppcheck (`[facepipe.cpp:843]: (error) Resource leak: out`) on the first full CI run, plus review of the same function.

**Bugs:**

1. If the gallery folder could not be opened, enrollment returned without closing the temporary gallery file it had already opened.
2. Each person's folder was opened without checking the result; an unreadable folder would pass a null pointer to `readdir` and crash.

**Fix:** List the gallery folder before creating the temporary file; skip a person's folder with a message if it cannot be opened.

**Proof:** CI compile and cppcheck green on the fix commit.
