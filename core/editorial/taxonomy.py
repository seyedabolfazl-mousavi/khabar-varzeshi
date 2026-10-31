"""نماد صنعت — دسته‌بندی ISIC، محور موضوعی و منطقه، مطابق پیشنهاد مهر ۱۴۰۵."""

from __future__ import annotations

BRAND_NAME = "نماد صنعت"

CATEGORIES: tuple[str, ...] = (
    "فولاد، فلزات و معدن",
    "نفت، گاز و پتروشیمی",
    "برق و انرژی",
    "خودرو و قطعه",
    "هوافضا، ریلی و دریایی",
    "ماشین‌آلات و تجهیزات صنعتی",
    "الکترونیک و تجهیزات برقی",
    "شیمی، دارو و تجهیزات پزشکی",
    "مصالح و ساخت‌وساز",
    "غذا، کشاورزی و بسته‌بندی",
    "صنایع سبک و مصرفی",
    "لجستیک، کشتیرانی و زنجیره تأمین",
    "آب، پسماند و بازیافت",
)

AXES: tuple[str, ...] = (
    "اقتصاد و بازار صنعت",
    "تجارت، تعرفه و مقررات",
    "صنعت ۴.۰ و فناوری",
    "پایداری و کربن‌زدایی",
    "نوآوری و پژوهش",
    "مدیریت و نیروی کار",
    "رویدادها",
)

REGIONS: tuple[str, ...] = (
    "ایران",
    "خاورمیانه و خلیج فارس",
    "ترکیه و قفقاز",
    "روسیه و آسیای میانه",
    "چین",
    "ژاپن و کره",
    "هند و جنوب آسیا",
    "اروپا",
    "آمریکا",
    "آفریقا",
)


def canonicalize(value: str, choices: tuple[str, ...]) -> str:
    """Map a model label onto an exact taxonomy string, or return empty."""
    text = (value or "").strip().replace("ي", "ی").replace("ك", "ک")
    text = text.replace("4.0", "۴.۰").replace("4٫0", "۴.۰")
    if text in choices:
        return text
    if not text:
        return ""
    for choice in choices:
        if text in choice or choice in text:
            return choice
    return ""


def canonicalize_axes(values: object) -> list[str]:
    if isinstance(values, str):
        raw_items = [part.strip() for part in values.replace("،", ",").split(",")]
    elif isinstance(values, (list, tuple)):
        raw_items = [str(item) for item in values]
    else:
        raw_items = []
    found: list[str] = []
    for item in raw_items:
        label = canonicalize(item, AXES)
        if label and label not in found:
            found.append(label)
    return found
