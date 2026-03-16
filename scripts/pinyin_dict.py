"""Pinyin syllable dictionary for informal pinyin detection."""

import re

# Complete set of valid Mandarin pinyin syllables (without tones).
# Approximately 410+ entries covering all standard initials + finals combinations,
# zero-initial syllables, and common informal variants.
PINYIN_SYLLABLES = frozenset({
    # ---- Zero-initial / standalone vowel syllables ----
    "a", "o", "e", "ai", "ei", "ao", "ou",
    "an", "en", "ang", "eng", "er",

    # ---- y- initial (zero-initial i/u-medial in standard notation) ----
    "yi", "ya", "ye", "yao", "you", "yan", "yin", "yang", "ying", "yong",
    "yu", "yue", "yuan", "yun",

    # ---- w- initial (zero-initial u-medial in standard notation) ----
    "wu", "wa", "wo", "wai", "wei", "wan", "wen", "wang", "weng",

    # ---- b- ----
    "ba", "bo", "bai", "bei", "bao", "ban", "bang", "ben", "beng",
    "bi", "bie", "biao", "bian", "bin", "bing", "bu",

    # ---- p- ----
    "pa", "po", "pai", "pei", "pao", "pou", "pan", "pang", "pen", "peng",
    "pi", "pie", "piao", "pian", "pin", "ping", "pu",

    # ---- m- ----
    "ma", "mo", "me", "mai", "mei", "mao", "mou", "man", "mang", "men", "meng",
    "mi", "mie", "miao", "miu", "mian", "min", "ming", "mu",

    # ---- f- ----
    "fa", "fo", "fei", "fou", "fan", "fang", "fen", "feng", "fu",

    # ---- d- ----
    "da", "de", "dai", "dei", "dao", "dou", "dan", "dang", "den", "deng",
    "di", "die", "diao", "diu", "dian", "ding",
    "du", "duo", "dui", "duan", "dun", "dong",

    # ---- t- ----
    "ta", "te", "tai", "tei", "tao", "tou", "tan", "tang", "teng",
    "ti", "tie", "tiao", "tian", "ting",
    "tu", "tuo", "tui", "tuan", "tun", "tong",

    # ---- n- ----
    "na", "ne", "nai", "nei", "nao", "nou", "nan", "nang", "nen", "neng",
    "ni", "nie", "niao", "niu", "nian", "nin", "niang", "ning",
    "nu", "nuo", "nuan", "nong",
    "nv", "nve",  # informal for nu:/nue: with u-umlaut

    # ---- l- ----
    "la", "le", "lai", "lei", "lao", "lou", "lan", "lang", "len", "leng",
    "li", "lia", "lie", "liao", "liu", "lian", "lin", "liang", "ling",
    "lu", "luo", "luan", "lun", "long",
    "lv", "lve",  # informal for lu:/lue: with u-umlaut

    # ---- g- ----
    "ga", "ge", "gai", "gei", "gao", "gou", "gan", "gang", "gen", "geng",
    "gu", "gua", "guo", "guai", "gui", "guan", "guang", "gun", "gong",

    # ---- k- ----
    "ka", "ke", "kai", "kei", "kao", "kou", "kan", "kang", "ken", "keng",
    "ku", "kua", "kuo", "kuai", "kui", "kuan", "kuang", "kun", "kong",

    # ---- h- ----
    "ha", "he", "hai", "hei", "hao", "hou", "han", "hang", "hen", "heng",
    "hu", "hua", "huo", "huai", "hui", "huan", "huang", "hun", "hong",

    # ---- j- ----
    "ji", "jia", "jie", "jiao", "jiu", "jian", "jin", "jiang", "jing",
    "ju", "jue", "juan", "jun", "jiong",

    # ---- q- ----
    "qi", "qia", "qie", "qiao", "qiu", "qian", "qin", "qiang", "qing",
    "qu", "que", "quan", "qun", "qiong",

    # ---- x- ----
    "xi", "xia", "xie", "xiao", "xiu", "xian", "xin", "xiang", "xing",
    "xu", "xue", "xuan", "xun", "xiong",

    # ---- zh- ----
    "zha", "zhe", "zhi", "zhai", "zhei", "zhao", "zhou",
    "zhan", "zhang", "zhen", "zheng",
    "zhu", "zhua", "zhuo", "zhuai", "zhui", "zhuan", "zhuang", "zhun", "zhong",

    # ---- ch- ----
    "cha", "che", "chi", "chai", "chao", "chou",
    "chan", "chang", "chen", "cheng",
    "chu", "chua", "chuo", "chuai", "chui", "chuan", "chuang", "chun", "chong",

    # ---- sh- ----
    "sha", "she", "shi", "shai", "shei", "shao", "shou",
    "shan", "shang", "shen", "sheng",
    "shu", "shua", "shuo", "shuai", "shui", "shuan", "shuang", "shun",

    # ---- r- ----
    "re", "ri", "rao", "rou", "ran", "rang", "ren", "reng",
    "ru", "rua", "ruo", "rui", "ruan", "run", "rong",

    # ---- z- ----
    "za", "ze", "zi", "zai", "zei", "zao", "zou", "zan", "zang", "zen", "zeng",
    "zu", "zuo", "zui", "zuan", "zun", "zong",

    # ---- c- ----
    "ca", "ce", "ci", "cai", "cao", "cou", "can", "cang", "cen", "ceng",
    "cu", "cuo", "cui", "cuan", "cun", "cong",

    # ---- s- ----
    "sa", "se", "si", "sai", "sao", "sou", "san", "sang", "sen", "seng",
    "su", "suo", "sui", "suan", "sun", "song",

    # ---- Interjections and informal standalone sounds ----
    "r",    # standalone r (e.g., verbal filler)
    "n",    # standalone n (interjection, e.g., "n, hao de")
    "m",    # standalone m (interjection, hmm)
    "ng",   # nasal interjection
    "hm",   # interjection
    "hng",  # interjection
    "yo",   # interjection (yo!)
    "lo",   # sentence-final particle (colloquial)
    "nia",  # colloquial variant of ne/na
})


def is_pinyin_syllable(word: str) -> bool:
    """Check if *word* (lowercase, no tones) is a valid pinyin syllable.

    Parameters
    ----------
    word : str
        A single token to test. The function lowercases it internally for
        convenience, but the caller should already have stripped tones and
        diacritics.

    Returns
    -------
    bool
        ``True`` if the word appears in :data:`PINYIN_SYLLABLES`.
    """
    return word.lower().strip() in PINYIN_SYLLABLES


# Pre-compiled pattern: split on anything that is not an ASCII letter.
_TOKEN_SPLIT_RE = re.compile(r"[^a-zA-Z]+")


def extract_pinyin_tokens(text: str) -> list[str]:
    """Return all tokens from *text* that are valid pinyin syllables.

    The text is split on whitespace and non-alphabetic characters, then each
    resulting token is checked against :data:`PINYIN_SYLLABLES`.

    Parameters
    ----------
    text : str
        Arbitrary input text (e.g., a chat message).

    Returns
    -------
    list[str]
        Lowercased tokens that matched valid pinyin syllables, in the order
        they appeared.
    """
    tokens = _TOKEN_SPLIT_RE.split(text.lower())
    return [t for t in tokens if t and t in PINYIN_SYLLABLES]


def contains_informal_pinyin(text: str) -> bool:
    """Detect whether *text* contains informal (un-toned, space-separated) pinyin.

    Heuristic: returns ``True`` when at least **2** distinct pinyin-syllable
    tokens are found in *text*.  The threshold of 2 avoids false positives from
    single-letter matches such as ``"a"`` or ``"e"`` that are also common
    English words.

    Handles common informal patterns like ``"kan le"``, ``"mei you"``,
    ``"bu hao"``, etc.

    Parameters
    ----------
    text : str
        Arbitrary input text.

    Returns
    -------
    bool
        ``True`` if at least 2 valid pinyin syllables are detected.
    """
    return len(extract_pinyin_tokens(text)) >= 2
