import argparse
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import collect_papers as cp
import digest


def make_topic(topic_id="speech", name="Speech Enhancement", keywords=None, categories=None):
    return cp.Topic(
        id=topic_id,
        name=name,
        description="speech enhancement and denoising",
        keywords=keywords or ["speech enhancement", "noise suppression"],
        arxiv_categories=categories or ["eess.AS", "cs.SD"],
    )


def make_candidate(candidate_id, track="frontier", llm_score=None, title=None, citation_count=None):
    candidate = {
        "id": candidate_id,
        "arxiv_id": candidate_id.removeprefix("arxiv:"),
        "title": title or f"Paper {candidate_id}",
        "authors": ["A. Author"],
        "summary": "A speech enhancement model with noise suppression evaluated on benchmarks." * 3,
        "published": "2026-07-05T00:00:00+00:00",
        "paper_url": "https://arxiv.org/abs/2607.00001",
        "pdf_url": "https://arxiv.org/pdf/2607.00001",
        "categories": ["eess.AS"],
        "venue": "",
        "track": track,
        "seed_topic": "speech",
        "citation_count": citation_count,
        "best_match": {"topic_id": "speech", "topic_name": "Speech Enhancement", "score": 0.5, "level": "medium", "reason": "", "keyword_hits": []},
    }
    if llm_score is not None:
        candidate["llm_score"] = llm_score
        candidate["justification"] = "理由"
    return candidate


class NormalizeArxivIdTest(unittest.TestCase):
    def test_strips_version_suffix(self):
        self.assertEqual(digest.normalize_arxiv_id("2501.12345v2"), "2501.12345")

    def test_strips_abs_url(self):
        self.assertEqual(digest.normalize_arxiv_id("https://arxiv.org/abs/2501.12345v1"), "2501.12345")

    def test_handles_old_style_ids(self):
        self.assertEqual(digest.normalize_arxiv_id("cs/0301012v1"), "cs/0301012")

    def test_empty(self):
        self.assertEqual(digest.normalize_arxiv_id(""), "")


class CandidateKeysTest(unittest.TestCase):
    def test_includes_id_arxiv_id_and_title_key(self):
        keys = digest.candidate_keys({"id": "arxiv:2501.12345", "arxiv_id": "2501.12345", "title": "Flow Matching for Speech!"})
        self.assertIn("arxiv:2501.12345", keys)
        self.assertIn("2501.12345", keys)
        self.assertIn("title:flow matching for speech", keys)

    def test_same_paper_from_different_sources_shares_keys(self):
        frontier = digest.candidate_keys({"id": "arxiv:2501.12345", "arxiv_id": "2501.12345", "title": "Flow Matching for Speech"})
        s2 = digest.candidate_keys({"id": "s2:abcdef", "arxiv_id": "2501.12345", "title": "Flow  Matching for Speech."})
        self.assertTrue(frontier & s2)


class DigestSettingsTest(unittest.TestCase):
    def test_defaults_when_config_missing(self):
        settings = digest.digest_settings({})
        self.assertEqual(settings["max_labor_paper"], 200)
        self.assertEqual(settings["max_daily_added"], 5)
        self.assertEqual(settings["min_score"], 7.0)
        self.assertEqual(settings["frontier_budget"] + settings["foundation_budget"], 200)

    def test_config_values_used(self):
        settings = digest.digest_settings({"digest": {"max_labor_paper": 100, "max_daily_added": 3, "min_score": 8.0}})
        self.assertEqual(settings["max_labor_paper"], 100)
        self.assertEqual(settings["max_daily_added"], 3)
        self.assertEqual(settings["min_score"], 8.0)

    def test_env_overrides_config(self):
        with mock.patch.dict("os.environ", {"MAX_DAILY_ADDED": "1", "MIN_SCORE": "9.5"}):
            settings = digest.digest_settings({"digest": {"max_daily_added": 3, "min_score": 8.0}})
        self.assertEqual(settings["max_daily_added"], 1)
        self.assertEqual(settings["min_score"], 9.5)


class AdmitTest(unittest.TestCase):
    def setUp(self):
        self.settings = digest.digest_settings({"digest": {"max_daily_added": 2, "min_score": 7.0}})

    def test_threshold_and_cap(self):
        shortlist = [
            make_candidate("arxiv:1", llm_score=9.0),
            make_candidate("arxiv:2", llm_score=8.0),
            make_candidate("arxiv:3", llm_score=7.5),
            make_candidate("arxiv:4", llm_score=5.0),
        ]
        admitted, rejected = digest.admit(shortlist, self.settings, remaining_cap=2)
        self.assertEqual([p["id"] for p in admitted], ["arxiv:1", "arxiv:2"])
        reasons = {paper["id"]: reason for paper, reason in rejected}
        self.assertEqual(reasons["arxiv:3"], "over_cap")
        self.assertEqual(reasons["arxiv:4"], "below_threshold")

    def test_weak_day_admits_nothing(self):
        shortlist = [make_candidate("arxiv:1", llm_score=6.9), make_candidate("arxiv:2", llm_score=3.0)]
        admitted, rejected = digest.admit(shortlist, self.settings, remaining_cap=2)
        self.assertEqual(admitted, [])
        self.assertEqual({reason for _, reason in rejected}, {"below_threshold"})

    def test_zero_remaining_cap(self):
        shortlist = [make_candidate("arxiv:1", llm_score=9.0)]
        admitted, rejected = digest.admit(shortlist, self.settings, remaining_cap=0)
        self.assertEqual(admitted, [])
        self.assertEqual(rejected[0][1], "over_cap")

    def test_unscored_papers_are_neither_admitted_nor_rejected(self):
        shortlist = [make_candidate("arxiv:1", llm_score=9.0), make_candidate("arxiv:2")]
        admitted, rejected = digest.admit(shortlist, self.settings, remaining_cap=2)
        self.assertEqual([p["id"] for p in admitted], ["arxiv:1"])
        self.assertEqual(rejected, [])


class RejectedKeysTest(unittest.TestCase):
    def test_only_below_threshold_entries_block_rescoring(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rejected.jsonl"
            entries = [
                {"id": "arxiv:1", "arxiv_id": "1", "title": "Below", "reason": "below_threshold"},
                {"id": "arxiv:2", "arxiv_id": "2", "title": "Over Cap", "reason": "over_cap"},
            ]
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
            keys = digest.load_rejected_keys(path)
        self.assertIn("arxiv:1", keys)
        self.assertNotIn("arxiv:2", keys)

    def test_missing_file(self):
        self.assertEqual(digest.load_rejected_keys(Path("/nonexistent/rejected.jsonl")), set())


class Stage1ShortlistTest(unittest.TestCase):
    def test_respects_size_and_mixes_tracks(self):
        topics = [make_topic()]
        frontier = [make_candidate(f"arxiv:f{i}") for i in range(30)]
        foundation = [make_candidate(f"arxiv:g{i}", track="foundation", citation_count=100 * (i + 1)) for i in range(20)]
        settings = digest.digest_settings({"digest": {"shortlist_size": 10}})
        shortlist = digest.stage1_shortlist(frontier, foundation, topics, settings)
        self.assertEqual(len(shortlist), 10)
        tracks = {paper["track"] for paper in shortlist}
        self.assertEqual(tracks, {"frontier", "foundation"})

    def test_refills_from_other_track_when_one_is_empty(self):
        topics = [make_topic()]
        foundation = [make_candidate(f"arxiv:g{i}", track="foundation", citation_count=50) for i in range(15)]
        settings = digest.digest_settings({"digest": {"shortlist_size": 10}})
        shortlist = digest.stage1_shortlist([], foundation, topics, settings)
        self.assertEqual(len(shortlist), 10)


class GatherFrontierDedupeTest(unittest.TestCase):
    def test_skips_papers_already_in_library(self):
        topic = make_topic()
        now = dt.datetime.now(dt.timezone.utc)
        arxiv_entries = [
            {
                "id": "2607.00001v1",
                "title": "Known Paper",
                "authors": [],
                "summary": "s",
                "published": now.isoformat(),
                "updated": now.isoformat(),
                "paper_url": "https://arxiv.org/abs/2607.00001v1",
                "pdf_url": "",
                "categories": ["eess.AS"],
            },
            {
                "id": "2607.00002v1",
                "title": "New Paper",
                "authors": [],
                "summary": "s",
                "published": now.isoformat(),
                "updated": now.isoformat(),
                "paper_url": "https://arxiv.org/abs/2607.00002v1",
                "pdf_url": "",
                "categories": ["eess.AS"],
            },
        ]
        settings = digest.digest_settings({})
        skip_keys = digest.candidate_keys({"id": "arxiv:2607.00001", "arxiv_id": "2607.00001", "title": "Known Paper"})
        with mock.patch.object(cp, "fetch_arxiv_query", return_value=arxiv_entries):
            candidates = digest.gather_frontier([topic], settings, skip_keys, now)
        self.assertEqual([c["id"] for c in candidates], ["arxiv:2607.00002"])

    def test_falls_back_to_openalex_when_arxiv_fails(self):
        topic = make_topic()
        now = dt.datetime.now(dt.timezone.utc)
        settings = digest.digest_settings({})
        openalex_response = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Fallback Speech Enhancement Paper",
                    "doi": "https://doi.org/10.48550/arXiv.2607.01234",
                    "publication_date": now.date().isoformat(),
                    "abstract_inverted_index": {
                        f"enhancement{i}": [i] for i in range(30)
                    },
                    "authorships": [{"author": {"display_name": "A. Author"}}],
                    "primary_location": {"landing_page_url": "https://arxiv.org/abs/2607.01234"},
                    "concepts": [],
                    "locations": [],
                }
            ]
        }
        with (
            mock.patch.object(cp, "fetch_arxiv_query", side_effect=RuntimeError("HTTP Error 429")),
            mock.patch.object(cp, "request_json", return_value=openalex_response),
        ):
            candidates = digest.gather_frontier([topic], settings, set(), now)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "arxiv:2607.01234")
        self.assertEqual(candidates[0]["arxiv_id"], "2607.01234")
        self.assertEqual(candidates[0]["track"], "frontier")

    def test_fetch_failure_raises_digest_error_when_fallback_also_fails(self):
        topic = make_topic()
        now = dt.datetime.now(dt.timezone.utc)
        settings = digest.digest_settings({})
        with (
            mock.patch.object(cp, "fetch_arxiv_query", side_effect=RuntimeError("arXiv down")),
            mock.patch.object(cp, "request_json", side_effect=RuntimeError("OpenAlex down")),
        ):
            with self.assertRaises(digest.DigestError):
                digest.gather_frontier([topic], settings, set(), now)


class RunDigestIdempotencyTest(unittest.TestCase):
    """Two consecutive runs over the same candidates add no duplicates."""

    def _args(self, tmpdir):
        return argparse.Namespace(
            config=Path(tmpdir) / "config.json",
            library=Path(tmpdir) / "library.json",
            web_library=Path(tmpdir) / "web_library.json",
            state=Path(tmpdir) / "state.json",
            rejected=Path(tmpdir) / "rejected.jsonl",
            dry_run=False,
        )

    def test_double_run_no_duplicates(self):
        now = dt.datetime.now(dt.timezone.utc)
        config = {
            "digest": {"max_daily_added": 5, "min_score": 7.0, "shortlist_size": 10},
            "topics": [
                {
                    "id": "speech",
                    "name": "Speech Enhancement",
                    "description": "speech enhancement",
                    "keywords": ["speech enhancement"],
                    "arxiv_categories": ["eess.AS"],
                }
            ],
        }
        arxiv_entries = [
            {
                "id": "2607.00001v1",
                "title": "Great Speech Enhancement Paper",
                "authors": ["A"],
                "summary": "speech enhancement " * 20,
                "published": now.isoformat(),
                "updated": now.isoformat(),
                "paper_url": "https://arxiv.org/abs/2607.00001v1",
                "pdf_url": "",
                "categories": ["eess.AS"],
            }
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            args = self._args(tmpdir)
            args.config.write_text(json.dumps(config), encoding="utf-8")
            patches = [
                mock.patch.object(cp, "fetch_arxiv_query", return_value=arxiv_entries),
                mock.patch.object(digest, "gather_foundation", return_value=[]),
                mock.patch.object(cp, "llm_enabled", return_value=True),
                mock.patch.object(digest, "score_one", return_value={"score": 9.0, "justification": "好"}),
                mock.patch.object(digest, "summarize_admitted", side_effect=lambda admitted, topics: None),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                self.assertEqual(digest.run_digest(args), 0)
                library_after_first = json.loads(args.library.read_text(encoding="utf-8"))
                self.assertEqual(len(library_after_first["papers"]), 1)
                self.assertEqual(library_after_first["papers"][0]["track"], "frontier")
                self.assertIn("date_added", library_after_first["papers"][0])
                self.assertIn("justification", library_after_first["papers"][0])

                self.assertEqual(digest.run_digest(args), 0)
                library_after_second = json.loads(args.library.read_text(encoding="utf-8"))
                self.assertEqual(len(library_after_second["papers"]), 1)

    def test_daily_cap_shared_across_runs(self):
        now = dt.datetime.now(dt.timezone.utc)
        config = {
            "digest": {"max_daily_added": 1, "min_score": 7.0, "shortlist_size": 10},
            "topics": [
                {
                    "id": "speech",
                    "name": "Speech Enhancement",
                    "description": "speech enhancement",
                    "keywords": ["speech enhancement"],
                    "arxiv_categories": ["eess.AS"],
                }
            ],
        }

        def entry(index):
            return {
                "id": f"2607.0000{index}v1",
                "title": f"Speech Enhancement Paper {index}",
                "authors": ["A"],
                "summary": "speech enhancement " * 20,
                "published": now.isoformat(),
                "updated": now.isoformat(),
                "paper_url": f"https://arxiv.org/abs/2607.0000{index}v1",
                "pdf_url": "",
                "categories": ["eess.AS"],
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            args = self._args(tmpdir)
            args.config.write_text(json.dumps(config), encoding="utf-8")
            with (
                mock.patch.object(cp, "fetch_arxiv_query", return_value=[entry(1), entry(2)]),
                mock.patch.object(digest, "gather_foundation", return_value=[]),
                mock.patch.object(cp, "llm_enabled", return_value=True),
                mock.patch.object(digest, "score_one", return_value={"score": 9.0, "justification": "好"}),
                mock.patch.object(digest, "summarize_admitted", side_effect=lambda admitted, topics: None),
            ):
                digest.run_digest(args)
                first = json.loads(args.library.read_text(encoding="utf-8"))
                self.assertEqual(len(first["papers"]), 1)
                # Second run the same day: cap already reached, nothing added.
                digest.run_digest(args)
                second = json.loads(args.library.read_text(encoding="utf-8"))
                self.assertEqual(len(second["papers"]), 1)


class PruneAddedByDayTest(unittest.TestCase):
    def test_prunes_old_days(self):
        today = dt.date(2026, 7, 6)
        pruned = digest.prune_added_by_day({"2026-07-06": 2, "2026-06-01": 5, "bogus": 1}, today)
        self.assertEqual(pruned, {"2026-07-06": 2})


if __name__ == "__main__":
    unittest.main()
