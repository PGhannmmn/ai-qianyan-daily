import json
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from pipeline import daily
from pipeline.vendor import storage as S
from pipeline.vendor import sitegen
from .support import BASE, DAY, saved, brief, sandbox, snapshot


class StorageSiteTests(unittest.TestCase):
    def test_zero_new_drafts_does_not_create_state(self):
        with sandbox() as path:
            self.assertFalse(S.append_drafts(path / "state", DAY, []))
            self.assertFalse((path / "state").exists())

    def test_same_url_preserves_original_bytes_mtime_and_metadata(self):
        with sandbox() as path:
            old = saved()
            old["editorial_annotation"] = "validated earlier"
            S.append_drafts(path / "state", DAY, [old])
            before = snapshot(path)
            changed = dict(saved(), text="模型的新版本内容不得替换原稿。")
            self.assertFalse(S.append_drafts(path / "state", DAY, [changed]))
            self.assertEqual(snapshot(path), before)
            self.assertEqual(S.read_drafts(path / "state", DAY)["drafts"], [old])

    def test_empty_rerun_preserves_existing_draft_file(self):
        with sandbox() as path:
            S.append_drafts(path / "state", DAY, [saved()])
            before = snapshot(path)
            self.assertFalse(daily._write_briefs_as_drafts(DAY, []))
            self.assertEqual(snapshot(path), before)

    def test_new_url_appends_without_changing_first_slug_or_metadata(self):
        with sandbox() as path:
            first = saved()
            S.append_drafts(path / "state", DAY, [first])
            S.append_drafts(path / "state", DAY, [saved(1)])
            data = S.read_drafts(path / "state", DAY)
            self.assertEqual(data["drafts"][0], first)
            self.assertEqual(data["tiers"], ["llm", "llm"])
            articles = sitegen.load_articles([DAY])
            self.assertEqual({a["url"]: a["slug"] for a in articles}[first["url"]], DAY + "-1")

    def test_tracking_variants_do_not_duplicate_drafts(self):
        a, b = saved(), dict(saved())
        b["url"] += "?utm_source=other#x"
        self.assertEqual(S.merge_drafts([a], [b]), [a])

    def test_duplicate_incoming_urls_are_saved_once(self):
        self.assertEqual(len(S.merge_drafts([], [saved(), saved()])), 1)

    def test_mutating_input_after_merge_does_not_change_preserved_data(self):
        original = saved()
        merged = S.merge_drafts([original], [])
        original["provenance"]["key_points"].clear()
        self.assertTrue(merged[0]["provenance"]["key_points"])

    def test_atomic_replace_failure_keeps_old_file(self):
        with sandbox() as path:
            S.append_drafts(path / "state", DAY, [saved()])
            before = snapshot(path)
            with patch.object(S.os, "replace", side_effect=OSError("private contents")):
                with self.assertRaisesRegex(S.StateError, "^state_write_failed$"):
                    S.append_drafts(path / "state", DAY, [saved(1)])
            self.assertEqual(snapshot(path), before)
            self.assertFalse(list((path / "state").glob("*.tmp-*")))

    def test_corrupt_state_is_not_overwritten(self):
        with sandbox() as path:
            (path / "state").mkdir()
            file = path / "state" / ("simp_drafts_" + DAY + ".json")
            file.write_text("invalid json", encoding="utf-8")
            before = snapshot(path)
            with self.assertRaises(S.StateError):
                S.append_drafts(path / "state", DAY, [saved()])
            self.assertEqual(snapshot(path), before)

    def test_wrong_state_schema_is_rejected(self):
        with sandbox() as path:
            S.atomic_json(path / "state" / ("simp_drafts_" + DAY + ".json"), {"drafts": [saved()], "assembled": False})
            with self.assertRaises(S.StateError):
                S.read_drafts(path / "state", DAY)

    def test_tiers_alignment_is_checked(self):
        with sandbox() as path:
            S.atomic_json(path / "state" / ("simp_drafts_" + DAY + ".json"), {"drafts": [saved()], "assembled": True, "tiers": ["llm", "llm"]})
            with self.assertRaises(S.StateError):
                S.read_drafts(path / "state", DAY)

    def test_invalid_date_cannot_escape_state_directory(self):
        with self.assertRaises(S.StateError):
            S.read_drafts("state", "../../secret")

    def test_cross_day_dedup_uses_local_metadata(self):
        with sandbox() as path:
            S.append_drafts(path / "state", "2026-10-07", [saved()])
            self.assertIn(saved()["url"], daily._published_urls())
            self.assertEqual(daily._all_draft_dates(DAY), ["2026-10-07", DAY])

    def test_legacy_date_only_metadata_and_unknown_fields_are_preserved(self):
        with sandbox() as path:
            legacy = dict(saved(), published_at="2026-10-07", legacy_qa_result="passed")
            legacy.pop("tier")
            S.atomic_json(path / "state" / ("simp_drafts_" + DAY + ".json"),
                          {"drafts": [legacy], "assembled": True, "tiers": ["llm"]})
            S.append_drafts(path / "state", DAY, [saved(1)])
            self.assertEqual(S.read_drafts(path / "state", DAY)["drafts"][0], legacy)

    def test_atomic_writer_rejects_symlink_paths_before_writes(self):
        with sandbox() as path, patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaisesRegex(S.StateError, "^state_symlink_rejected$"):
                S.atomic_json(path / "state/file.json", {"drafts": []})

    def test_history_and_original_source_metadata_survive_next_day(self):
        with sandbox() as path:
            original = saved()
            S.append_drafts(path / "state", "2026-10-07", [original])
            result = sitegen.generate_site(["2026-10-07", DAY], str(path / "site"), BASE)
            self.assertEqual(result["articles"], 1)
            article = (path / "site/articles/2026-10-07-1.html").read_text(encoding="utf-8")
            self.assertIn(original["url"], article)
            self.assertIn(original["source_name"], article)
            self.assertIn(original["published_at"][:10], article)
            self.assertIn("2026-10-07-1.html", (path / "site/sitemap.xml").read_text())
            feed = ET.parse(path / "site/feed.xml")
            self.assertEqual(len(feed.findall("./channel/item")), 1)
            self.assertTrue(feed.find("./channel/item/pubDate").text.endswith("GMT"))

    def test_memory_rendering_writes_nothing(self):
        with sandbox() as path:
            before = snapshot(path)
            files = sitegen.render_site(sitegen.articles_from_drafts({DAY: [saved()]}), BASE)
            self.assertIn("articles/" + DAY + "-1.html", files)
            self.assertEqual(snapshot(path), before)

    def test_source_markup_is_escaped(self):
        draft = dict(saved(), text="测试文本 <b>Gemini</b>，介绍人工智能产品。")
        files = sitegen.render_site(sitegen.articles_from_drafts({DAY: [draft]}), BASE)
        self.assertIn("&lt;b&gt;Gemini&lt;/b&gt;", files["articles/" + DAY + "-1.html"])

    def test_unsafe_saved_urls_cannot_be_rendered(self):
        with self.assertRaises(Exception):
            sitegen.articles_from_drafts({DAY: [dict(saved(), url="javascript:alert(1)")]})

    def test_output_scan_blocks_secret_before_any_local_write(self):
        with sandbox() as path:
            S.append_drafts(path / "state", DAY, [saved()])
            with patch.object(sitegen, "render_site", return_value={"index.html": "Bearer private-synthetic-value"}):
                result = sitegen.generate_site([DAY], str(path / "site"), BASE)
            self.assertTrue(result["safety_issues"])
            self.assertFalse((path / "site").exists())

    def test_deploy_is_always_disabled(self):
        with self.assertRaisesRegex(RuntimeError, "^deployment_disabled$"):
            sitegen.deploy("site")
