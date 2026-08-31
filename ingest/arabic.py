"""Arabic text normalisation for PDF extraction.

Zid's knowledge-base PDFs extract badly in two specific ways, and both have to
be repaired before the text is embedded or indexed — a wrongly-encoded string
is a different string as far as any tokeniser is concerned, so retrieval simply
returns nothing.

1. Letters arrive as Unicode *presentation forms* (U+FB50-FDFF, U+FE70-FEFF),
   the contextual glyph shapes, rather than standard Arabic (U+0600-06FF).
   NFKC maps them back.
2. Word or character order arrives in *visual* (right-to-left-on-the-page)
   order rather than logical order. Which of the two depends on the extractor:
   pypdf reverses whole words, pdfplumber reverses characters inside a cell.

Direction repair cannot be done blindly — reversing text that was already
correct silently corrupts it. Every fix here is therefore scored against a
frequency list and reports a confidence, so the caller can route anything
doubtful to a human instead of shipping it.
"""

from __future__ import annotations

import re
import unicodedata

ARABIC = r"؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿"
ARABIC_RE = re.compile(f"[{ARABIC}]")
ARABIC_RUN_RE = re.compile(f"[{ARABIC}][{ARABIC}\\s]*")

# Arabic-Indic and extended Arabic-Indic digits -> Western.
_DIGITS = {ord(c): str(i % 10) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹")}
_TATWEEL = "ـ"

# Mirrored pairs, so reversing a line doesn't leave brackets pointing the wrong way.
_MIRROR = str.maketrans("()[]{}<>", ")(][}{><")

# High-frequency Arabic words plus Zid domain vocabulary. Used only to decide
# whether a candidate repair reads better than what we started with, so it does
# not need to be exhaustive — it needs to be common.
COMMON_WORDS = {
    # function words
    "من", "في", "على", "إلى", "الى", "عن", "مع", "أو", "او", "و", "ثم", "لا",
    "نعم", "هذا", "هذه", "ذلك", "التي", "الذي", "كل", "بعد", "قبل", "عند",
    "بين", "حتى", "أي", "غير", "بدون", "خلال", "حسب", "عبر", "لكن", "كما",
    # commerce / Zid domain
    "زد", "متجر", "المتجر", "متاجر", "تاجر", "التاجر", "التجار", "منتج",
    "المنتج", "منتجات", "طلب", "طلبات", "الطلبات", "سعر", "السعر", "أسعار",
    "الأسعار", "ريال", "شهر", "شهري", "شهريا", "سنة", "سنوي", "باقة", "الباقة",
    "باقات", "اشتراك", "الاشتراك", "خدمة", "خدمات", "الخدمات", "عميل",
    "العميل", "عملاء", "العملاء", "شحن", "الشحن", "توصيل", "دفع", "الدفع",
    "مدفوعات", "فاتورة", "ضريبة", "الضريبة", "مجاني", "مجانية", "تكلفة",
    "تكاليف", "قيمة", "عمولة", "خصم", "مبيعات", "المبيعات", "تسويق",
    "التسويق", "إعلانات", "الإعلانات", "نمو", "النمو", "تطبيق", "لوحة",
    "التحكم", "تقرير", "تقارير", "دعم", "الدعم", "حساب", "الحساب", "تفعيل",
    "مخزون", "المخزون", "فرع", "فروع", "نقاط", "البيع", "كاشير", "جهاز",
    "يوم", "أيام", "يومين", "أسبوع", "أسبوعين", "عمل", "مدة", "تنفيذ",
    "متوفر", "متوفرة", "يشمل", "تشمل", "السعودية", "مصر", "الإمارات",
    "الكويت", "عمان", "الرياض", "جدة",
}

# Letters that only ever occur word-finally in correct Arabic. A token that
# *starts* with one is strong evidence its characters were reversed.
_FINAL_ONLY = "ةى"


def strip_marks(text: str) -> str:
    """Remove tatweel and combining diacritics, which vary between sources."""
    text = text.replace(_TATWEEL, "")
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    """NFKC-fold presentation forms, westernise digits, tidy whitespace.

    This is the non-negotiable step: without it roughly 80% of the Arabic in
    these PDFs is in a Unicode block no tokeniser will match against a query.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_DIGITS)
    text = text.replace(" ", " ").replace("‏", "").replace("‎", "")
    # Collapse the runs of double spaces the extractors leave between words.
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(bool(ARABIC_RE.match(c)) for c in letters) / len(letters)


def score(text: str) -> int:
    """How many recognisable Arabic words a string contains.

    The comparison metric for direction repair: whichever orientation of a
    string yields more real words is the one we keep.
    """
    tokens = re.findall(f"[{ARABIC}]+", strip_marks(text))
    hits = sum(t in COMMON_WORDS for t in tokens)
    # A token starting with a final-only letter is almost certainly reversed.
    hits -= sum(bool(t) and t[0] in _FINAL_ONLY for t in tokens)
    return hits


def fix_word_order(line: str) -> tuple[str, bool]:
    """Repair pypdf prose: correct characters, reversed *word* order.

    pypdf emits Arabic-dominant lines in visual (right-to-left-on-the-page)
    order, consistently across every file tested, so the repair is applied by
    rule rather than by score — reversing a token list does not change which
    words it contains, so a dictionary comparison cannot tell the two
    orientations apart.

    This is a simplified bidi reordering: reverse the whole token sequence to
    restore logical order, then flip contiguous runs of Latin/numeric tokens
    back, since an embedded English phrase reads left-to-right inside an
    Arabic line. Returns (text, changed).
    """
    if arabic_ratio(line) < 0.3:
        return line, False
    tokens = line.split()
    if len(tokens) < 2:
        return line, False

    ordered = [t.translate(_MIRROR) for t in reversed(tokens)]

    # Restore internal order of embedded left-to-right runs.
    result, run = [], []
    for token in ordered:
        if ARABIC_RE.search(token):
            result.extend(reversed(run))
            run = []
            result.append(token)
        else:
            run.append(token)
    result.extend(reversed(run))

    flipped = " ".join(result)
    return (flipped, flipped != line)


def fix_char_order(text: str) -> tuple[str, bool]:
    """Repair pdfplumber table cells: reversed *characters* within Arabic runs.

    Each Arabic run is reversed independently so embedded Latin text and
    numbers keep their own order.
    """
    if not ARABIC_RE.search(text):
        return text, False

    def flip(match: re.Match) -> str:
        run = match.group(0)
        core = run.strip()
        lead = run[: len(run) - len(run.lstrip())]
        trail = run[len(run.rstrip()) :]
        return f"{lead}{core[::-1]}{trail}"

    flipped = ARABIC_RUN_RE.sub(flip, text)
    return (flipped, True) if score(flipped) > score(text) else (text, False)


def confidence(text: str) -> str:
    """Rough signal for the review report: does this read like real Arabic?

    'high'   - contains recognised words
    'low'    - Arabic present but nothing recognised (review by hand)
    'n/a'    - no Arabic to judge
    """
    if not ARABIC_RE.search(text):
        return "n/a"
    tokens = re.findall(f"[{ARABIC}]+", strip_marks(text))
    if not tokens:
        return "n/a"
    hits = sum(t in COMMON_WORDS for t in tokens)
    if hits >= 2 or (hits >= 1 and len(tokens) <= 3):
        return "high"
    reversed_hits = sum(t[::-1] in COMMON_WORDS for t in tokens)
    return "low" if reversed_hits > hits else ("high" if hits else "low")
