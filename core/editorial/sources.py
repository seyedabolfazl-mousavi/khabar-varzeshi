"""Twenty-five public industrial RSS feeds.

The first fifteen were checked on the publisher page. The extra ten are
feeds that still returned live items, chosen because they publish often
enough to raise the daily pool.

Each URL was fetched and returned at least three titled entries, and the
same URL (or its on-page rss/feed href) was present in the site HTML.
English feeds are preferred so the Persian rewrite is not seeded with
Chinese, Japanese, or Korean characters.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NemadSource:
    name: str
    url: str
    category: str
    priority: int


NEMAD_SOURCES: tuple[NemadSource, ...] = (
    NemadSource(
        "Mining.com",
        "https://www.mining.com/feed/",
        "فولاد و معدن",
        10,
    ),
    NemadSource(
        "worldsteel",
        "https://worldsteel.org/feed/",
        "فولاد و معدن",
        40,
    ),
    NemadSource(
        "Offshore Energy",
        "https://www.offshore-energy.biz/feed/",
        "نفت و گاز",
        12,
    ),
    NemadSource(
        "EIA Today in Energy",
        "https://www.eia.gov/rss/todayinenergy.xml",
        "برق و انرژی",
        20,
    ),
    NemadSource(
        "Canary Media",
        "https://www.canarymedia.com/rss.rss",
        "برق و انرژی",
        14,
    ),
    NemadSource(
        "pv magazine",
        "https://www.pv-magazine.com/feed/",
        "برق و انرژی",
        16,
    ),
    NemadSource(
        "Electrek",
        "https://electrek.co/feed/",
        "خودرو و قطعه",
        18,
    ),
    NemadSource(
        "CnEVPost",
        "https://cnevpost.com/feed/",
        "خودرو و قطعه",
        22,
    ),
    NemadSource(
        "IndustryWeek",
        "https://www.industryweek.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22home%22%7D",
        "تولید صنعتی",
        11,
    ),
    NemadSource(
        "Automation World",
        "https://www.automationworld.com/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22home%22%7D",
        "صنعت ۴.۰",
        24,
    ),
    NemadSource(
        "IEEE Spectrum",
        "https://spectrum.ieee.org/feeds/feed.rss",
        "الکترونیک",
        26,
    ),
    NemadSource(
        "Semiconductor Engineering",
        "https://semiengineering.com/feed/",
        "الکترونیک",
        28,
    ),
    NemadSource(
        "FreightWaves",
        "https://www.freightwaves.com/feed",
        "لجستیک",
        30,
    ),
    NemadSource(
        "gCaptain",
        "https://gcaptain.com/feed/",
        "کشتیرانی",
        32,
    ),
    NemadSource(
        "Mercom India",
        "https://mercomindia.com/feed/",
        "برق و انرژی",
        34,
    ),
    NemadSource(
        "Hellenic Shipping News",
        "https://www.hellenicshippingnews.com/feed/",
        "کشتیرانی",
        13,
    ),
    NemadSource(
        "Oilprice",
        "https://oilprice.com/rss/main",
        "نفت و گاز",
        15,
    ),
    NemadSource(
        "The National",
        "https://www.thenationalnews.com/arc/outboundfeeds/rss/category/business/?outputType=xml",
        "اقتصاد خاورمیانه",
        17,
    ),
    NemadSource(
        "Manufacturing.net",
        "https://www.manufacturing.net/__rss/website-scheduled-content.xml?input=%7B%22sectionAlias%22%3A%22home%22%7D",
        "تولید صنعتی",
        19,
    ),
    NemadSource(
        "Chemical Engineering",
        "https://www.chemengonline.com/feed/",
        "شیمی",
        21,
    ),
    NemadSource(
        "The Robot Report",
        "https://www.therobotreport.com/feed/",
        "صنعت ۴.۰",
        23,
    ),
    NemadSource(
        "EE Times",
        "https://www.eetimes.com/feed/",
        "الکترونیک",
        25,
    ),
    NemadSource(
        "Oil & Gas Middle East",
        "https://www.oilandgasmiddleeast.com/feed",
        "نفت و گاز",
        27,
    ),
    NemadSource(
        "Splash247",
        "https://splash247.com/feed/",
        "کشتیرانی",
        29,
    ),
    NemadSource(
        "POWER Magazine",
        "https://www.powermag.com/feed/",
        "برق و انرژی",
        31,
    ),
)
