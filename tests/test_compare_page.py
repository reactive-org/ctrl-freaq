"""Tests for `scripts/compare_page.py` (#237): the review walk's comparison page."""

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import compare_page as cp
from pipelib.limits import ATTACH_IMAGE_TYPES


class WritePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gh-pipeline-test-compare-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.staged = self.tmp / "staged"
        self.staged.mkdir()
        self.record = self.staged / "record.png"
        self.record.write_bytes(b"R")
        self.build = self.staged / "build.svg"
        self.build.write_bytes(b"<svg/>")
        self.out = self.tmp / "out" / "1-F7"

    def _write(self, state="empty", caption="no illustration", record_link="https://github.com/user-attachments/assets/r",
               build_link="https://github.com/o/r/blob/abc/design/b.png"):
        return Path(cp.write_page(self.out, state, caption, str(self.record), str(self.build), record_link,
                                  build_link))

    def _files(self, root):
        return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*"))

    def test_the_page_and_both_images_are_written_in_out_dir_only(self):
        before = self._files(self.tmp)
        page = self._write()
        self.assertEqual(page, self.out / "index.html")
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["build.svg", "index.html", "record.png"])
        added = set(self._files(self.tmp)) - set(before)
        self.assertTrue(all(a.startswith("out") for a in added), added)
        self.assertEqual((self.out / "record.png").read_bytes(), b"R")
        text = page.read_text(encoding="utf-8")
        self.assertIn('src="record.png"', text)
        self.assertIn('src="build.svg"', text)

    def test_text_is_escaped(self):
        text = self._write(state='<script>alert(1)</script>', caption='a "b" <img src=x onerror=y> & -->').read_text(
            encoding="utf-8")
        self.assertNotIn("<script>alert", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertNotIn("<img src=x", text)
        self.assertIn("&lt;img src=x onerror=y&gt; &amp; --&gt;", text)

    def test_controls_and_csp_are_present(self):
        text = self._write().read_text(encoding="utf-8")
        self.assertRegex(text, r'<meta http-equiv="Content-Security-Policy" content="default-src &#x27;none&#x27;')
        self.assertIn('data-view="flip"', text)
        self.assertIn('data-view="overlay"', text)
        self.assertIn('type="range" min="0" max="100"', text)
        self.assertIn("prefers-color-scheme: dark", text)
        self.assertIn("e.key === ' '", text)

    def test_no_remote_resource_loads_and_only_https_links_are_anchors(self):
        text = self._write(build_link="/tmp/local/build.png").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'\bsrc="(?:https?:)?//', text), [])
        hrefs = re.findall(r'href="([^"]*)"', text)
        self.assertEqual(hrefs, ["https://github.com/user-attachments/assets/r"])
        self.assertIn('<span class="link">/tmp/local/build.png</span>', text)
        self.assertNotIn("<object", text)
        self.assertNotIn("<embed", text)
        self.assertNotIn("<iframe", text)

    def test_a_link_with_a_javascript_scheme_is_text(self):
        text = self._write(record_link="javascript:alert(1)").read_text(encoding="utf-8")
        self.assertNotIn('href="javascript', text)

    def test_a_non_image_capture_raises_before_writing(self):
        pdf = self.staged / "record.pdf"
        pdf.write_bytes(b"%PDF")
        with self.assertRaises(ValueError):
            cp.write_page(self.out, "empty", None, str(pdf), str(self.build), None, None)
        self.assertFalse(self.out.exists())

    def test_a_re_write_replaces_earlier_images_and_never_follows_a_planted_symlink(self):
        self.out.mkdir(parents=True)
        outside = self.tmp / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        os.symlink(outside, self.out / "record.png")
        (self.out / "build.gif").write_bytes(b"old")
        self._write()
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.out / "record.png").is_symlink())
        self.assertFalse((self.out / "build.gif").exists())

    def test_the_suffixes_are_the_image_types_gh_uploads(self):
        self.assertEqual(set(cp.IMAGE_SUFFIXES), {"." + t for t in ATTACH_IMAGE_TYPES})
        for suffix in cp.IMAGE_SUFFIXES:
            with self.subTest(suffix=suffix):
                image = self.staged / ("any" + suffix.upper())
                image.write_bytes(b"x")
                page = Path(cp.write_page(self.out / suffix.strip("."), "empty", None, str(image), str(self.build),
                                          None, None))
                self.assertTrue((page.parent / ("record" + suffix)).exists())

    def test_no_caption_renders_no_caption_paragraph(self):
        text = self._write(caption=None).read_text(encoding="utf-8")
        self.assertNotIn('class="caption"', text)


if __name__ == "__main__":
    unittest.main()
