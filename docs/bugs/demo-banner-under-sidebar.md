# The demo banner rendered under the sidebar

**Seen:** 18 Nov 2026, on `https://attendance.hamzaaziz.dev/demo/`, by the owner.

## Symptom

The demo's banner — the strip that says the page is running a recorded day and that the faces are drawn — was cut off
on its left. Its first words sat behind the navigation sidebar, so the sentence started mid-word, and the line ran on
past the right edge instead of wrapping into the content column.

## Cause

`tools/demo/build.py` inserted the banner before the skip link:

```python
page = page.replace('<a class="skip" href="#main">', banner + '<a class="skip" href="#main">', 1)
```

That is outside `<div class="shell">`. The website's layout (`web/app.css`) places `.side` as a fixed column and
gives `.shell` the matching left margin, so anything added outside the shell starts at x=0, underneath the sidebar,
with no margin to clear it. The banner is the only element the demo adds to the layout, so nothing else showed it.

## Fix

Insert it just inside the shell, where the page's own content lives:

```python
assert page.count('<div class="shell">') == 1
page = page.replace('<div class="shell">', '<div class="shell">' + banner, 1)
```

The banner then spans the content column and wraps like the rest of the page.

## Lesson

The demo is the real pages with things added to them (D-050), so an addition has to respect the real layout's
assumptions. The capture step cannot catch this: every page it saved was correct, and the fault was introduced after
it, by the stitching. The check for it is a look at the published page, which is what found it.
