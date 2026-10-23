"""Numbers and symbols as a person would say them, before F5-TTS reads the text.

F5 reads characters, not meaning: in the bake-off (docs/voice-bakeoff.md) most of its word errors were numbers ("1,284" came out
garbled, "0.9 c" as noise). So everything numeric is spelled out first: "1,284" -> "one thousand two hundred eighty-four",
"0.9 c" -> "zero point nine times the speed of light", "44 %" -> "forty-four percent", "11.2 km/s" -> "eleven point two kilometres per
second", "4x" -> "four times", "3rd" -> "third", "$12" -> "twelve dollars". Pure Python, no dependencies, so it is tested anywhere.
"""
from __future__ import annotations

import re

ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen",
        "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
SCALES = [(10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand")]
ORDINAL = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}

# A unit right after a number, longest first. Plural only when the number isn't exactly one ("1 km" is "one kilometre").
UNITS = [
    ("km/s", "kilometre per second", "kilometres per second"), ("km/h", "kilometre per hour", "kilometres per hour"),
    ("m/s²", "metre per second squared", "metres per second squared"), ("m/s", "metre per second", "metres per second"),
    ("mph", "mile per hour", "miles per hour"), ("GB", "gigabyte", "gigabytes"), ("MB", "megabyte", "megabytes"),
    ("KB", "kilobyte", "kilobytes"), ("TB", "terabyte", "terabytes"), ("GHz", "gigahertz", "gigahertz"), ("MHz", "megahertz", "megahertz"),
    ("kHz", "kilohertz", "kilohertz"), ("Hz", "hertz", "hertz"), ("km", "kilometre", "kilometres"), ("cm", "centimetre", "centimetres"),
    ("mm", "millimetre", "millimetres"), ("kg", "kilogram", "kilograms"), ("mg", "milligram", "milligrams"), ("ms", "millisecond", "milliseconds"),
    ("µs", "microsecond", "microseconds"), ("ns", "nanosecond", "nanoseconds"), ("kW", "kilowatt", "kilowatts"), ("MW", "megawatt", "megawatts"),
    ("W", "watt", "watts"), ("kV", "kilovolt", "kilovolts"), ("V", "volt", "volts"), ("eV", "electronvolt", "electronvolts"),
    ("°C", "degree Celsius", "degrees Celsius"), ("°F", "degree Fahrenheit", "degrees Fahrenheit"), ("K", "kelvin", "kelvin"),
    ("°", "degree", "degrees"), ("%", "percent", "percent"), ("c", "times the speed of light", "times the speed of light"),
    ("x", "times", "times"), ("×", "times", "times"), ("m", "metre", "metres"), ("g", "gram", "grams"), ("s", "second", "seconds"),
]
_UNIT_RE = "|".join(re.escape(u) for u, _, _ in UNITS)
_UNIT = {u: (one, many) for u, one, many in UNITS}
CURRENCY = {"$": ("dollar", "dollars", "cent", "cents"), "£": ("pound", "pounds", "penny", "pence"), "€": ("euro", "euros", "cent", "cents")}

# One number: an optional sign or currency, digits with thousands commas or plain, an optional decimal part; then an optional
# ordinal suffix, or a unit (that must end the word: "5 m" and "5m" are metres, "5 men" is not).
NUMBER = re.compile(
    r"(?<![\w.])(?P<cur>[$£€])?(?P<sign>[-−])?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<dec>\d+))?"
    r"(?:(?P<ord>st|nd|rd|th)\b|\s?(?P<unit>" + _UNIT_RE + r")(?![\w/]))?")


def below_thousand(n: int) -> str:
    words = []
    if n >= 100:
        words.append(f"{ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        words.append(TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else ""))
    elif n or not words:
        words.append(ONES[n])
    return " ".join(words)


def cardinal(n: int) -> str:
    if n < 0:
        return "minus " + cardinal(-n)
    if n < 1000:
        return below_thousand(n)
    words = []
    for size, name in SCALES:
        if n >= size:
            words.append(f"{cardinal(n // size)} {name}")
            n %= size
    if n:
        words.append(below_thousand(n))
    return " ".join(words)


def ordinal(n: int) -> str:
    words = cardinal(n)
    head, sep, last = words.rpartition(" ") if "-" not in words.rsplit(" ", 1)[-1] else words.rpartition("-")
    if last in ORDINAL:
        last = ORDINAL[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


def year(n: int) -> str:
    """1984 -> nineteen eighty-four, 2026 -> twenty twenty-six, 2005 -> two thousand five, 1900 -> nineteen hundred."""
    hi, lo = divmod(n, 100)
    if 2000 <= n < 2010:
        return cardinal(n)
    if lo == 0:
        return f"{cardinal(hi)} hundred"
    return f"{cardinal(hi)} {'oh ' + ONES[lo] if lo < 10 else cardinal(lo)}"


def _say(m: re.Match) -> str:
    digits = m["int"].replace(",", "")
    n = int(digits)
    dec, unit, cur = m["dec"], m["unit"], m["cur"]
    if m["ord"] and not dec:
        return ("minus " if m["sign"] else "") + ordinal(n)
    if cur and dec and len(dec) == 2:                                    # $12.50: twelve dollars and fifty cents
        one, many, c1, cs = CURRENCY[cur]
        cents = int(dec)
        said = f"{cardinal(n)} {one if n == 1 else many}" + (f" and {cardinal(cents)} {c1 if cents == 1 else cs}" if cents else "")
        return ("minus " if m["sign"] else "") + said
    if not dec and not unit and not cur and not m["sign"] and "," not in m["int"] and len(digits) == 4 and 1600 <= n <= 2099:
        words = year(n)                                                  # a bare four-digit number in that range is read as a year
    elif len(digits) > 1 and digits.startswith("0"):                    # 007, a code: digit by digit
        words = " ".join(ONES[int(d)] for d in digits)
    else:
        words = cardinal(n)
    if dec:
        words += " point " + " ".join(ONES[int(d)] for d in dec)
    if m["sign"]:
        words = "minus " + words
    exactly_one = n == 1 and not dec
    if cur:
        one, many, _, _ = CURRENCY[cur]
        words += " " + (one if exactly_one else many)
    if unit:
        one, many = _UNIT[unit]
        words += " " + (one if exactly_one else many)
    return words


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
DATE = re.compile(r"(?<![\d-])(\d{4})-(\d{2})-(\d{2})(?![\d-])")
CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
DOTTED = re.compile(r"(?<![\w.])\d+(?:\.\d+){2,}(?![\w.])")
RANGE = re.compile(r"(?<![\d-])(\d{1,3})[-–](\d{1,3})(?![\d-])")


def _date(m: re.Match) -> str:
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m[0]
    return f"{MONTHS[mo - 1]} {ordinal(d)}, {year(y) if 1600 <= y <= 2099 else cardinal(y)}"


def _clock(m: re.Match) -> str:
    h, mi = int(m[1]), int(m[2])
    return cardinal(h) + (" hundred" if mi == 0 and h > 12 else " o'clock" if mi == 0 else f" oh {ONES[mi]}" if mi < 10 else f" {cardinal(mi)}")


def say_numbers(text: str) -> str:
    """The text with every number (and its unit) in words, and a few symbols said aloud. Anything else is left as it is."""
    t = DATE.sub(_date, str(text or ""))                                  # 2026-10-23: October twenty-third, twenty twenty-six
    t = CLOCK.sub(_clock, t)                                              # 13:19: thirteen nineteen; 9:05: nine oh five
    t = DOTTED.sub(lambda m: " point ".join(cardinal(int(p)) for p in m[0].split(".")), t)   # version 1.2.3
    t = RANGE.sub(r"\1 to \2", t)                                         # 3-5 minutes: three to five
    t = NUMBER.sub(_say, t)
    t = t.replace("&", " and ").replace("+", " plus ").replace("=", " equals ")
    return re.sub(r"[ \t]{2,}", " ", t).strip()
