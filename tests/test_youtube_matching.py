import unittest
from pathlib import Path
from unittest.mock import patch

from sift.engines.music.models import Track, VideoResult
from sift.engines.music.services.youtube import YouTubeResolver


class YouTubeMatchingTests(unittest.TestCase):
    def setUp(self):
        self.resolver = YouTubeResolver()
        self.track = Track(
            title="Manase Maya (From 'Mundina Nildana')",
            artist="Sooraj Santhosh",
            album="Mundina Nildana (Original Motion Picture Soundtrack)",
            duration_ms=210_000,
        )

    def test_rejects_same_title_with_wrong_artist_and_album(self):
        wrong = VideoResult(
            id="wrong",
            title="Manase Maya Maya - Lyrical Kannada | CHIMERA",
            channel="Vignesh Raja",
            duration=240,
            url="https://youtube.example/wrong",
        )

        self.assertLess(self.resolver._calculate_score(self.track, wrong), 80)

    def test_rejects_cover_even_when_singer_is_named(self):
        cover = VideoResult(
            id="cover",
            title="Manase maaya | Vinay Chaitanya | Sooraj Santhosh | Masala Coffee",
            channel="Vinay Chaitanya",
            duration=220,
            url="https://youtube.example/cover",
            rank=1,
        )

        self.assertLess(self.resolver._calculate_score(self.track, cover), 80)

    def test_accepts_title_with_spotify_artist_metadata(self):
        correct = VideoResult(
            id="correct",
            title="Manase Maya - Mundina Nildana - Official Audio",
            channel="Sooraj Santhosh - Topic",
            duration=245,
            url="https://youtube.example/correct",
        )

        self.assertGreaterEqual(self.resolver._calculate_score(self.track, correct), 65)

    def test_accepts_official_label_upload_with_title_and_album_evidence(self):
        correct = VideoResult(
            id="official",
            title=(
                "Mundina Nildana - Manase Maya Video Song (4K) "
                "I Masala Coffee I Ananya Kashyap"
            ),
            channel="PRK Audio",
            duration=210,
            url="https://youtube.example/official",
            rank=1,
            description="Official video for Manase Maya from Mundina Nildana.",
        )

        self.assertGreaterEqual(self.resolver._calculate_score(self.track, correct), 80)

    def test_rejects_making_video(self):
        making_video = VideoResult(
            id="making",
            title=(
                "Mundina Nildana - Manase Maya (Making Video) "
                "I Masala Coffee I Sooraj Santosh"
            ),
            channel="PRK Audio",
            duration=205,
            url="https://youtube.example/making",
            rank=2,
        )

        self.assertEqual(self.resolver._calculate_score(self.track, making_video), 0)

    def test_freeform_query_prefers_top_relevant_result(self):
        query = Track(title="manase maya sooraj santosh", is_dummy=True)
        correct = VideoResult(
            id="official",
            title="Mundina Nildana - Manase Maya Video Song (4K)",
            channel="PRK Audio",
            duration=210,
            url="https://youtube.example/official",
            rank=1,
        )
        unrelated = VideoResult(
            id="unrelated",
            title="Adiye - Sooraj Santosh",
            channel="Music Channel",
            duration=240,
            url="https://youtube.example/unrelated",
            rank=5,
        )

        correct_score = self.resolver._calculate_score(query, correct)
        unrelated_score = self.resolver._calculate_score(query, unrelated)
        self.assertGreaterEqual(correct_score, 50)
        self.assertGreater(correct_score, unrelated_score)

    def test_search_query_contains_title_artist_and_album(self):
        query = self.track.search_query.casefold()

        self.assertIn("manase maya", query)
        self.assertIn("sooraj santhosh", query)
        self.assertIn("mundina nildana", query)

    def test_topic_channel_with_close_duration_is_high_confidence(self):
        track = Track(
            title="Example Song",
            artist="Example Artist",
            album="Example Album",
            duration_ms=201_000,
        )
        candidate = VideoResult(
            id="topic",
            title="Example Song",
            channel="Example Artist - Topic",
            duration=200,
            url="https://youtube.example/topic",
            description="Provided to YouTube by Example Records",
            rank=4,
        )

        self.assertGreaterEqual(self.resolver._calculate_score(track, candidate), 80)

    def test_low_confidence_returns_candidates_without_url(self):
        track = Track(
            title="Exact Song",
            artist="Expected Artist",
            album="Expected Album",
            duration_ms=200_000,
        )
        candidate = VideoResult(
            id="uncertain",
            title="Exact Song",
            channel="Unknown Upload",
            duration=260,
            url="https://youtube.example/uncertain",
            rank=1,
        )
        self.resolver._run_yt_command = lambda query, limit: [candidate]

        result = self.resolver.resolve_track(track)

        self.assertIsNone(result["url"])
        self.assertEqual(result["candidates"][0]["url"], candidate.url)

    def test_public_resolve_track_reuses_cached_match(self):
        track = Track(title="Cached Song", artist="Artist", album="Album", duration_ms=180_000)
        candidate = VideoResult(
            id="cached",
            title="Cached Song",
            channel="Artist - Topic",
            duration=180,
            url="https://youtube.example/cached",
            description="Provided to YouTube by Example Records",
            rank=1,
        )
        resolver = YouTubeResolver()
        resolver.cache = {}
        resolver._run_yt_command = lambda query, limit: [candidate]

        first = resolver.resolve_track(track)
        resolver._run_yt_command = lambda query, limit: (_ for _ in ()).throw(AssertionError("cache miss"))
        second = resolver.resolve_track(track)

        self.assertEqual(first["url"], candidate.url)
        self.assertEqual(second["url"], candidate.url)


class YouTubeJobReviewTests(unittest.TestCase):
    @patch("sift.app.api.routes.music._register_music_artifacts", return_value=[])
    @patch("sift.app.api.routes.music.create_job_output_dir")
    @patch("sift.app.api.routes.music.YouTubeResolver")
    def test_youtube_job_returns_review_candidates(
        self,
        resolver_class,
        create_output_dir,
        _register_artifacts,
    ):
        from sift.app.api.routes.music import _yt_job

        create_output_dir.return_value = (Path("."), "ephemeral")
        resolver_class.return_value.resolve_track.return_value = {
            "url": None,
            "error": "Low confidence (best score: 58)",
            "candidates": [{
                "title": "Candidate",
                "channel": "Channel",
                "duration": 200,
                "url": "https://www.youtube.com/watch?v=test",
                "score": 58,
            }],
        }

        result = _yt_job("task-id", ["example song"], None)

        self.assertEqual(result["stats"]["failed_count"], 0)
        self.assertEqual(result["stats"]["needs_review_count"], 1)
        self.assertEqual(result["items"][0]["status"], "needs_review")


if __name__ == "__main__":
    unittest.main()
