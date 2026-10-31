from django.test import SimpleTestCase

from core.article_scraper import (
    _validate_page_source,
    alternate_article_url,
    extract_article_body_html,
    primary_article_url,
)
from core.bot.services import _site_body_as_plain_text
from core.editorial.persian import persian_problems
from core.editorial.sources import NEMAD_SOURCES
from core.editorial.taxonomy import canonicalize, canonicalize_axes, CATEGORIES
from core.news_pool.lexical import jaccard, title_tokens
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
    def test_skips_routine_sports(self):
        result = assess_topic("NFL injury report: three starters ruled out")
        self.assertTrue(result.skip)

    def test_keeps_industrial_item_that_mentions_a_league_word(self):
        result = assess_topic(
            "Steel plant signs power contract ahead of the World Cup construction rush"
        )
        self.assertFalse(result.skip)

    def test_skips_celebrity(self):
        result = assess_topic("Celebrity stuns on the red carpet before the premiere")
        self.assertTrue(result.skip)

    def test_keeps_plain_industry_headline(self):
        result = assess_topic("Copper mine cuts output after smelter outage")
        self.assertFalse(result.skip)


class PersianGuardTests(SimpleTestCase):
    def test_rejects_cjk(self):
        problems = persian_problems("قیمت فولاد در 上海 بالا رفت")
        self.assertTrue(problems)

    def test_accepts_persian_with_acronym(self):
        problems = persian_problems(
            "صادرات LNG ایران در گزارش اوپک ثابت ماند و اثری بر تولید پتروشیمی دارد."
        )
        self.assertEqual(problems, [])


class TaxonomyTests(SimpleTestCase):
    def test_canonical_category(self):
        self.assertEqual(
            canonicalize("فولاد", CATEGORIES),
            "فولاد، فلزات و معدن",
        )

    def test_axes_list(self):
        self.assertEqual(
            canonicalize_axes(["پایداری", "تعرفه"]),
            ["پایداری و کربن‌زدایی", "تجارت، تعرفه و مقررات"],
        )


class LexicalDedupeTests(SimpleTestCase):
    def test_similar_titles_cluster(self):
        left = title_tokens("Copper mine cuts output after smelter outage")
        right = title_tokens("Copper mine cuts output after a smelter outage")
        self.assertGreaterEqual(jaccard(left, right), 0.5)


class SourceListTests(SimpleTestCase):
    def test_twenty_five_unique_feeds(self):
        urls = [item.url for item in NEMAD_SOURCES]
        self.assertEqual(len(urls), 25)
        self.assertEqual(len(set(urls)), 25)


class SiteBodyPreviewTests(SimpleTestCase):
    def test_strips_html_tags_for_operator_preview(self):
        raw = "<h2>عنوان</h2><p>متن خبر درباره انتقال.</p>"
        plain = _site_body_as_plain_text(raw)
        self.assertNotIn("<", plain)
        self.assertIn("عنوان", plain)
        self.assertIn("متن خبر", plain)
