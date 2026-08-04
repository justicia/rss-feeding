from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from fetch_news import Source, article_text, candidate_links, canonical_url, fetch_source, parse_date, published_at


class FetchNewsTests(unittest.TestCase):
    def test_canonical_url_removes_tracking_and_trailing_slash(self):
        self.assertEqual(canonical_url("https://example.com", "/story/?utm_source=x#top"), "https://example.com/story")

    def test_candidate_links_are_unique_and_same_domain(self):
        soup = BeautifulSoup("""
          <article><h2><a href="/criticas/a/">A sufficiently long article</a></h2></article>
          <h2><a href="/criticas/a/?ref=home">A sufficiently long article</a></h2>
          <h2><a href="https://bad.example/x">External sufficiently long article</a></h2>
        """, "html.parser")
        source = Source("Slipped Disc", "https://scherzo.es/noticias/criticas/", "scherzo.es")
        self.assertEqual(candidate_links(source, soup), [("A sufficiently long article", "https://scherzo.es/criticas/a")])

    def test_published_at_reads_metadata(self):
        soup = BeautifulSoup('<meta property="article:published_time" content="2026-08-03T09:00:00+02:00">', "html.parser")
        self.assertEqual(published_at(soup), datetime(2026, 8, 3, 7, tzinfo=timezone.utc))

    def test_parse_spanish_numeric_date_and_extract_article(self):
        self.assertEqual(parse_date("04/08/2026"), datetime(2026, 8, 4, tzinfo=timezone.utc))
        soup = BeautifulSoup("<article><nav><p>" + "x" * 80 + "</p></nav><p>" + "Music " * 20 + "</p></article>", "html.parser")
        self.assertIn("Music", article_text(soup))
        self.assertNotIn("x" * 20, article_text(soup))

    @patch("fetch_news.get_soup")
    @patch("fetch_news.summarize")
    def test_history_url_skips_article_fetch_and_openai(self, summarize, get_soup):
        source = Source("Slipped Disc", "https://slippedisc.com/", "slippedisc.com")
        homepage = BeautifulSoup(
            '<article><h2><a href="/already-seen/">An already summarized classical article</a></h2></article>',
            "html.parser",
        )
        get_soup.return_value = homepage
        history = {"https://slippedisc.com/already-seen": {"url": "https://slippedisc.com/already-seen"}}
        result = fetch_source(source, history, Mock(), datetime(2026, 8, 4, tzinfo=timezone.utc))
        self.assertEqual(result, [])
        self.assertEqual(get_soup.call_count, 1)
        summarize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
