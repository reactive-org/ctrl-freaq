"""compare_page.py — the review walk's comparison page (#237): one local HTML page per design item, showing the
approved design's capture and the build's side by side, with a **Flip** view (the two in one frame, alternating on
each press and on the space key) and an **Overlay** view (the build over the record, an opacity slider 0–100%).

Import-only: ``prep_review.py`` calls :func:`write_page` for each design item whose two captures both staged, and
reports the page's path as the item's ``captures.compare_page``; the review walk opens it in the host's browser pane,
or prints its path and both links. The model never writes the page (CLAUDE.md "facts by script, meaning by model").

**What the page may do.** Its inputs come from a GitHub comment (the state id and the difference a design root
carries, the captures it links), so:

- every text value is written through ``html.escape``;
- the two images are copies in ``out_dir``, named ``record<suffix>`` / ``build<suffix>`` with the suffix taken from
  :data:`IMAGE_SUFFIXES` only, and shown with ``<img>`` alone — an SVG capture is never inlined or embedded, so a
  script inside one never runs;
- a ``Content-Security-Policy`` meta lets the page load nothing but its two sibling images and its own inline style
  and script, so nothing on it fetches from the network;
- a link becomes an anchor only when it is an ``https`` URL; anything else (a local path) is shown as text.

It writes only ``out_dir`` (created when missing): the two images and ``index.html``, after removing any earlier
``record.*`` / ``build.*`` there. Stdlib only (plus the in-repo `pipelib.limits`).
"""

import html
import shutil
from pathlib import Path

from pipelib.limits import ATTACH_IMAGE_TYPES

# The suffixes the page shows as images, derived from the one definition of the image types `gh` uploads
# (`pipelib.limits.ATTACH_IMAGE_TYPES`, which the upload pre-filter reads too), so everything uploaded is something the
# page shows. Any other staged file (a `.pdf`, a `.bin`) makes `write_page` raise `ValueError`.
IMAGE_SUFFIXES = tuple(sorted("." + t for t in ATTACH_IMAGE_TYPES))
PAGE_NAME = "index.html"

# img-src 'self' file: — a page opened as file:// has an opaque origin in some browsers, where 'self' matches
# nothing; `file:` admits its sibling images and still no network scheme.
CSP = ("default-src 'none'; img-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "base-uri 'none'; form-action 'none'")

_STYLE = """
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --line: #d1d9e0;
  --accent: #0969da; --panel: #f6f8fa; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #3d444d; --accent: #4493f8; --panel: #151b23; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
h1 { font-size: 18px; margin: 0 0 4px; }
h1 code { font-size: 17px; }
p.caption { margin: 0 0 12px; color: var(--muted); }
.controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 12px; }
.controls button { font: inherit; padding: 4px 12px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--panel); color: var(--fg); cursor: pointer; }
.controls button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
.controls label { color: var(--muted); }
.view[hidden] { display: none; }
.side { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
figure { margin: 0; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); padding: 8px; }
figcaption { font-weight: 600; margin-bottom: 6px; }
figcaption a, figcaption span.link { font-weight: 400; margin-left: 8px; color: var(--accent); word-break: break-all; }
figcaption span.link { color: var(--muted); }
img { display: block; max-width: 100%; height: auto; }
.stack { position: relative; display: inline-block; }
.stack img.top { position: absolute; top: 0; left: 0; }
@media (max-width: 700px) { .side { grid-template-columns: 1fr; } }
"""

_SCRIPT = """
(function () {
  var views = {side: document.getElementById('view-side'), flip: document.getElementById('view-flip'),
               overlay: document.getElementById('view-overlay')};
  var buttons = document.querySelectorAll('button[data-view]');
  var showing = 'record';
  function show(name) {
    Object.keys(views).forEach(function (key) { views[key].hidden = key !== name; });
    buttons.forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.view === name)); });
  }
  function flip() {
    showing = showing === 'record' ? 'build' : 'record';
    document.getElementById('flip-record').hidden = showing !== 'record';
    document.getElementById('flip-build').hidden = showing !== 'build';
    document.getElementById('flip-label').textContent = showing === 'record' ? 'Approved design' : 'Build';
  }
  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.dataset.view === 'flip' && !views.flip.hidden) { flip(); return; }
      show(b.dataset.view);
    });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === ' ' && !views.flip.hidden) { e.preventDefault(); flip(); }
  });
  var slider = document.getElementById('opacity');
  var top = document.getElementById('overlay-build');
  function opacity() {
    top.style.opacity = String(slider.value / 100);
    document.getElementById('opacity-value').textContent = slider.value + '%';
  }
  slider.addEventListener('input', opacity);
  opacity();
  show('side');
})();
"""


def _image_suffix(path):
    suffix = Path(path).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("%s is not an image the page can show (%s)" % (path, ", ".join(IMAGE_SUFFIXES)))
    return suffix


def _link(link):
    """An anchor for an ``https`` link, the text itself for anything else, nothing for ``None``."""
    if not link:
        return ""
    text = html.escape(str(link), quote=True)
    if str(link).startswith("https://"):
        return '<a href="%s" rel="noreferrer noopener" target="_blank">%s</a>' % (text, text)
    return '<span class="link">%s</span>' % text


def render(state, caption, record_name, build_name, record_link, build_link):
    """The page's HTML for two image files beside it (``record_name`` / ``build_name``, plain file names). Pure."""
    e = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    state_text = e(state)
    caption_html = '<p class="caption">%s</p>' % e(caption) if caption else ""
    record_img = '<img src="%s" alt="Approved design: %s">' % (e(record_name), state_text)
    build_img = '<img src="%s" alt="Build: %s">' % (e(build_name), state_text)
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="%(csp)s">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Design comparison: %(state)s</title>
<style>%(style)s</style>
</head>
<body>
<h1>Design state <code>%(state)s</code></h1>
%(caption)s
<div class="controls" role="toolbar" aria-label="View">
<button type="button" data-view="side" aria-pressed="true">Side by side</button>
<button type="button" data-view="flip" aria-pressed="false">Flip</button>
<button type="button" data-view="overlay" aria-pressed="false">Overlay</button>
</div>
<section id="view-side" class="view side">
<figure><figcaption>Approved design %(record_link)s</figcaption>%(record_img)s</figure>
<figure><figcaption>Build %(build_link)s</figcaption>%(build_img)s</figure>
</section>
<section id="view-flip" class="view" hidden>
<figure><figcaption><span id="flip-label">Approved design</span> — press Flip or the space key to switch</figcaption>
<div id="flip-record">%(record_img)s</div>
<div id="flip-build" hidden>%(build_img)s</div>
</figure>
</section>
<section id="view-overlay" class="view" hidden>
<div class="controls"><label for="opacity">Build opacity</label>
<input id="opacity" type="range" min="0" max="100" value="50"> <span id="opacity-value">50%%</span></div>
<figure><figcaption>Build over the approved design</figcaption>
<div class="stack">%(record_img)s<img id="overlay-build" class="top" src="%(build_name)s" alt="Build: %(state)s"></div>
</figure>
</section>
<script>%(script)s</script>
</body>
</html>
""" % {"csp": e(CSP), "state": state_text, "style": _STYLE, "caption": caption_html, "record_img": record_img,
       "build_img": build_img, "build_name": e(build_name), "record_link": _link(record_link),
       "build_link": _link(build_link), "script": _SCRIPT}


def write_page(out_dir, state, caption, record_image, build_image, record_link, build_link):
    """Copy the two staged images into ``out_dir`` and write ``index.html`` beside them; return the page's path.

    ``record_image`` / ``build_image`` are staged files whose suffix is in :data:`IMAGE_SUFFIXES` (else
    ``ValueError``, before anything is written); ``record_link`` / ``build_link`` are each capture's GitHub link or
    ``None``. An ``OSError`` from the copy or the write propagates; the caller reports it."""
    record_suffix, build_suffix = _image_suffix(record_image), _image_suffix(build_image)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for stale in list(out.glob("record.*")) + list(out.glob("build.*")):
        if stale.is_file() or stale.is_symlink():
            stale.unlink()
    record_name, build_name = "record" + record_suffix, "build" + build_suffix
    shutil.copyfile(record_image, out / record_name)
    shutil.copyfile(build_image, out / build_name)
    page = out / PAGE_NAME
    if page.is_symlink():
        page.unlink()
    page.write_text(render(state, caption, record_name, build_name, record_link, build_link), encoding="utf-8")
    return str(page)
