from django.test import SimpleTestCase

from core.article_scraper import (
    _validate_page_source,
    alternate_article_url,
    extract_article_body_html,
    primary_article_url,
)
from core.bot.services import _site_body_as_plain_text
from core.topic_filter import assess_topic
from core.url_utils import normalize_article_url


class ArticleFetchUrlTests(SimpleTestCase):
    def test_primary_url_uses_rss_link_not_normalized_form(self):
        original = (
            "https://www.tasnimnews.com/fa/news/1404/03/31/3234567/"
            "sample-title/"
        )
        canonical = normalize_article_url(original)

        self.assertEqual(primary_article_url(original), original)
        self.assertNotEqual(primary_article_url(original), canonical)

    def test_alternate_url_adds_www_when_missing(self):
        original = "https://tasnimnews.com/fa/news/1404/03/31/123/test"
        alternate = alternate_article_url(original)
        self.assertEqual(alternate, "https://www.tasnimnews.com/fa/news/1404/03/31/123/test")

    def test_alternate_url_none_when_www_present(self):
        original = "https://www.tasnimnews.ir/fa/news/1404/03/31/123/test"
        self.assertIsNone(alternate_article_url(original))


class PageSourceValidationTests(SimpleTestCase):
    def test_validate_page_source_rejects_empty_html(self):
        html, error = _validate_page_source("", "https://example.com/article")
        self.assertEqual(html, "")
        self.assertIn("empty", error)

    def test_extract_article_body_from_story_markup(self):
        page = """
        <html><body>
          <nav>menu</nav>
          <div class="story-text">
            <p>""" + ("خبر ورزشی " * 40) + """</p>
          </div>
        </body></html>
        """
        body = extract_article_body_html(page)
        self.assertIn("story-text", body)
        self.assertGreater(len(body), 100)


class TopicFilterTests(SimpleTestCase):
    def test_skips_routine_nfl_without_exception(self):
        result = assess_topic("NFL injury report: three starters ruled out")
        self.assertTrue(result.skip)

    def test_keeps_arsenal_owner_baseball_story(self):
        result = assess_topic(
            "Arsenal owner buys Major League Baseball franchise"
        )
        self.assertFalse(result.skip)

    def test_skips_non_sport_celebrity(self):
        result = assess_topic("Nicole Kidman reveals new look at movie premiere")
        self.assertTrue(result.skip)

    def test_keeps_premier_league_transfer(self):
        result = assess_topic(
            "Manchester City close to signing Enzo Fernandez from Chelsea"
        )
        self.assertFalse(result.skip)


class SiteBodyPreviewTests(SimpleTestCase):
    def test_strips_html_tags_for_operator_preview(self):
        raw = "<h2>عنوان</h2><p>متن خبر درباره انتقال.</p>"
        plain = _site_body_as_plain_text(raw)
        self.assertNotIn("<", plain)
        self.assertIn("عنوان", plain)
        self.assertIn("متن خبر", plain)
