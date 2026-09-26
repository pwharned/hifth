"""
quran_data.py
-------------
Python port of hifth/shared/src/.../domain/QuranData.scala.
Only the pure lookup tables + segment math needed to group verified
per-Surah word alignments into Quarter-Hizb (QH) spans. Kept independent
from the Scala codebase on purpose (this is a separate export pipeline,
not a shared module) - copy over here if the Scala tables ever change.
"""
from dataclasses import dataclass

# Index 0 = Surah 1 (Al-Fatiha)
SURAH_AYAH_COUNTS = [
    7, 286, 200, 176, 120, 165, 206, 75, 129, 109, 123, 111, 43, 52, 99, 128,
    111, 110, 98, 135, 112, 78, 118, 64, 77, 227, 93, 88, 69, 60, 34, 30, 73,
    54, 45, 83, 182, 88, 75, 85, 54, 53, 89, 59, 37, 35, 38, 29, 18, 45, 60, 49,
    62, 55, 78, 96, 29, 22, 24, 13, 14, 11, 11, 18, 12, 12, 30, 52, 52, 44, 28,
    28, 20, 56, 40, 31, 50, 40, 46, 42, 29, 19, 36, 25, 22, 17, 19, 26, 30, 20,
    15, 21, 11, 8, 8, 19, 5, 8, 8, 11, 11, 8, 3, 9, 5, 4, 7, 3, 6, 3, 5, 4, 5,
    6
]

SURAH_NAMES = [
    "الفاتحة", "البقرة", "آل عمران", "النساء", "المائدة", "الأنعام", "الأعراف",
    "الأنفال", "التوبة", "يونس", "هود", "يوسف", "الرعد", "إبراهيم", "الحجر",
    "النحل", "الإسراء", "الكهف", "مريم", "طه", "الأنبياء", "الحج", "المؤمنون",
    "النور", "الفرقان", "الشعراء", "النمل", "القصص", "العنكبوت", "الروم",
    "لقمان", "السجدة", "الأحزاب", "سبأ", "فاطر", "يس", "الصافات", "ص",
    "الزمر", "غافر", "فصلت", "الشورى", "الزخرف", "الدخان", "الجاثية",
    "الأحقاف", "محمد", "الفتح", "الحجرات", "ق", "الذاريات", "الطور", "النجم",
    "القمر", "الرحمن", "الواقعة", "الحديد", "المجادلة", "الحشر", "الممتحنة",
    "الصف", "الجمعة", "المنافقون", "التغابن", "الطلاق", "التحريم", "الملك",
    "القلم", "الحاقة", "المعارج", "نوح", "الجن", "المزمل", "المدثر",
    "القيامة", "الإنسان", "المرسلات", "النبأ", "النازعات", "عبس", "التكوير",
    "الانفطار", "المطففين", "الانشقاق", "البروج", "الطارق", "الأعلى",
    "الغاشية", "الفجر", "البلد", "الشمس", "الليل", "الضحى", "الشرح", "التين",
    "العلق", "القدر", "البينة", "الزلزلة", "العاديات", "القارعة", "التكاثر",
    "العصر", "الهمزة", "الفيل", "قريش", "الماعون", "الكوثر", "الكافرون",
    "النصر", "المسد", "الإخلاص", "الفلق", "الناس"
]

# 240 entries + sentinel (115, 1) at index 240
QUARTER_HIZB_STARTS = [
    (1, 1), (2, 26), (2, 44), (2, 60), (2, 75), (2, 92), (2, 106), (2, 124),
    (2, 142), (2, 158), (2, 177), (2, 189), (2, 203), (2, 219), (2, 233),
    (2, 243), (2, 253), (2, 263), (2, 272), (2, 283), (3, 15), (3, 33),
    (3, 52), (3, 75), (3, 93), (3, 113), (3, 133), (3, 153), (3, 171),
    (3, 186), (4, 1), (4, 12), (4, 24), (4, 36), (4, 58), (4, 74), (4, 88),
    (4, 100), (4, 114), (4, 135), (4, 148), (4, 163), (5, 1), (5, 12),
    (5, 27), (5, 41), (5, 51), (5, 67), (5, 82), (5, 97), (5, 109), (6, 13),
    (6, 36), (6, 59), (6, 74), (6, 95), (6, 111), (6, 127), (6, 141),
    (6, 151), (7, 1), (7, 31), (7, 47), (7, 65), (7, 88), (7, 117), (7, 142),
    (7, 156), (7, 171), (7, 189), (8, 1), (8, 22), (8, 41), (8, 61), (9, 1),
    (9, 19), (9, 34), (9, 46), (9, 60), (9, 75), (9, 93), (9, 111), (9, 122),
    (10, 11), (10, 26), (10, 53), (10, 71), (10, 90), (11, 6), (11, 24),
    (11, 41), (11, 61), (11, 84), (11, 108), (12, 7), (12, 30), (12, 53),
    (12, 77), (12, 101), (13, 5), (13, 19), (13, 35), (14, 10), (14, 28),
    (15, 1), (15, 50), (16, 1), (16, 30), (16, 51), (16, 75), (16, 90),
    (16, 111), (17, 1), (17, 23), (17, 50), (17, 70), (17, 99), (18, 17),
    (18, 32), (18, 51), (18, 75), (18, 99), (19, 22), (19, 59), (20, 1),
    (20, 55), (20, 83), (20, 111), (21, 1), (21, 29), (21, 51), (21, 83),
    (22, 1), (22, 19), (22, 38), (22, 60), (23, 1), (23, 36), (23, 75),
    (24, 1), (24, 21), (24, 35), (24, 53), (25, 1), (25, 21), (25, 53),
    (26, 1), (26, 52), (26, 111), (26, 181), (27, 1), (27, 27), (27, 56),
    (27, 82), (28, 12), (28, 29), (28, 51), (28, 76), (29, 1), (29, 26),
    (29, 46), (30, 1), (30, 31), (30, 54), (31, 22), (32, 11), (33, 1),
    (33, 18), (33, 31), (33, 51), (33, 60), (34, 10), (34, 24), (34, 46),
    (35, 15), (35, 41), (36, 28), (36, 60), (37, 22), (37, 83), (37, 145),
    (38, 21), (38, 52), (39, 8), (39, 32), (39, 53), (40, 1), (40, 21),
    (40, 41), (40, 66), (41, 9), (41, 25), (41, 47), (42, 13), (42, 27),
    (42, 51), (43, 24), (43, 57), (44, 17), (45, 12), (46, 1), (46, 21),
    (47, 10), (47, 33), (48, 18), (49, 1), (49, 14), (50, 27), (51, 31),
    (52, 24), (53, 26), (54, 9), (55, 1), (56, 1), (56, 75), (57, 16),
    (58, 1), (58, 14), (59, 11), (60, 7), (62, 1), (63, 4), (65, 1), (66, 1),
    (67, 1), (68, 1), (69, 1), (70, 19), (72, 1), (73, 20), (75, 1),
    (76, 19), (78, 1), (80, 1), (82, 1), (84, 1), (87, 1), (90, 1), (94, 1),
    (100, 9),
    # Sentinel
    (115, 1),
]

CLOZE_STEPS = [0, 10, 25, 50, 75, 90, 95]


@dataclass(frozen=True)
class Segment:
    surah_number: int
    start_ayah: int
    end_ayah: int
    seg_idx: int


def segments_for_qh(quarter_hizb_id: int) -> list[Segment]:
    if not (1 <= quarter_hizb_id <= 240):
        raise ValueError(f"Invalid QH id: {quarter_hizb_id}")
    start_surah, start_ayah = QUARTER_HIZB_STARTS[quarter_hizb_id - 1]
    next_surah, next_ayah = QUARTER_HIZB_STARTS[quarter_hizb_id]
    if next_ayah == 1:
        end_surah = next_surah - 1
        end_ayah = SURAH_AYAH_COUNTS[end_surah - 1]
    else:
        end_surah = next_surah
        end_ayah = next_ayah - 1

    raw = []
    cur_surah, cur_ayah = start_surah, start_ayah
    while cur_surah < end_surah:
        raw.append((cur_surah, cur_ayah, SURAH_AYAH_COUNTS[cur_surah - 1]))
        cur_surah += 1
        cur_ayah = 1
    raw.append((cur_surah, cur_ayah, end_ayah))

    return [
        Segment(surah_number=s, start_ayah=a, end_ayah=e, seg_idx=i)
        for i, (s, a, e) in enumerate(raw)
    ]


def segments_for_qh_with_context(quarter_hizb_id: int) -> list[Segment]:
    """Return a QH plus one adjacent ayah on each available side."""
    raw = [
        (segment.surah_number, segment.start_ayah, segment.end_ayah)
        for segment in segments_for_qh(quarter_hizb_id)
    ]

    first_surah, first_start, first_end = raw[0]
    if first_start > 1:
        raw[0] = (first_surah, first_start - 1, first_end)
    elif first_surah > 1:
        previous_end = SURAH_AYAH_COUNTS[first_surah - 2]
        raw.insert(0, (first_surah - 1, previous_end, previous_end))

    last_surah, last_start, last_end = raw[-1]
    surah_end = SURAH_AYAH_COUNTS[last_surah - 1]
    if last_end < surah_end:
        raw[-1] = (last_surah, last_start, last_end + 1)
    elif last_surah < len(SURAH_AYAH_COUNTS):
        raw.append((last_surah + 1, 1, 1))

    return [
        Segment(surah_number=s, start_ayah=a, end_ayah=e, seg_idx=i)
        for i, (s, a, e) in enumerate(raw)
    ]


def primary_surah_for_qh(quarter_hizb_id: int) -> int:
    return QUARTER_HIZB_STARTS[quarter_hizb_id - 1][0]


def surah_name(surah_number: int) -> str:
    return SURAH_NAMES[surah_number - 1]
