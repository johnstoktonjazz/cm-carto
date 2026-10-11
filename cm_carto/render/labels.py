"""Подпись объекта на карте по TEXT.md верстки — до записи в overlay.json.

Правила и списки — rules/labels.yaml: без «ё»; без организационно-правовых форм (МБДОУ, ООО …); сокращения
раскрыты («д/с» → «детский сад»); строчные у нарицательных слов не в начале; «№» перед номером после типа
учреждения, между «№» и номером — неразрывный пробел; кавычки «елочки», внутри „лапки“; один пробел между словами,
без пробелов у кавычек внутри; первая буква — заглавная.

    clean('МБДОУ д/с 20 "Красная Шапочка"') == 'Детский сад №\\u00a020 «Красная Шапочка»'
"""

import re
from functools import cache
from pathlib import Path

import yaml

RULES = Path(__file__).resolve().parents[2] / "rules" / "labels.yaml"
NBSP = " "
OPEN = "«„"
CLOSE = "»“"


@cache
def rules() -> dict:
    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    missing = {"org_forms", "expand", "lowercase", "numbered"} - set(data)
    if missing:
        raise ValueError(f"{RULES}: нет ключей {', '.join(sorted(missing))}")
    return data


def _word(text: str) -> str:
    """Слово целиком: не внутри другого слова и не часть «д/с»."""
    return rf"(?<![\w/]){re.escape(text)}(?![\w/])" if text[-1].isalnum() else rf"(?<![\w/]){re.escape(text)}"


def quotes(text: str) -> str:
    """Прямые и английские кавычки → «елочки», вложенные → „лапки“. Вне кавычек прямая кавычка открывает; внутри —
    открывает вложенные, если перед ней пробел (или начало, скобка), а после — буква или цифра, иначе закрывает."""
    out, depth = [], 0
    for i, ch in enumerate(text):
        if ch not in "\"“”„«»":
            out.append(ch)
            continue
        prev = text[i - 1] if i else " "
        after = text[i + 1] if i + 1 < len(text) else " "
        opening = ch in "«„" or (ch in "\"“”" and (depth == 0 or ((prev.isspace() or prev in "(["  + OPEN)
                                                                   and after.isalnum())))
        if opening:
            out.append(OPEN[min(depth, 1)])
            depth += 1
        else:
            depth = max(depth - 1, 0)
            out.append(CLOSE[min(depth, 1)])
    return "".join(out)


def _outside_quotes(text: str, fn) -> str:
    """fn к частям текста вне кавычек: имя в кавычках («Школа радости») остается как есть."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in OPEN:
            if depth == 0:
                parts.append(fn(text[start:i], start == 0))
                start = i
            depth += 1
        elif ch in CLOSE and depth:
            depth -= 1
            if depth == 0:
                parts.append(text[start:i + 1])
                start = i + 1
    parts.append(fn(text[start:], start == 0) if depth == 0 else text[start:])
    return "".join(parts)


def clean(text: str) -> str:
    """Подпись по правилам rules/labels.yaml (модуль выше)."""
    r = rules()
    text = text.replace("ё", "е").replace("Ё", "Е")
    text = re.sub(r"\s+", " ", text).strip()
    for short, full in r["expand"].items():
        flags = 0 if short.isupper() else re.IGNORECASE
        text = re.sub(_word(short), full, text, flags=flags)
    for form in r["org_forms"]:
        text = re.sub(_word(form), "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,-—")
    text = quotes(text)
    text = re.sub(r"([«„]) +", r"\1", text)
    text = re.sub(r" +([»“])", r"\1", text)
    lower = {w.lower() for w in r["lowercase"]}

    def lowercase(part: str, first: bool) -> str:
        def one(m: re.Match) -> str:
            return m.group(0).lower() if m.group(0).lower() in lower and not (first and m.start() == 0) else m.group(0)
        return re.sub(r"\w+", one, part)

    text = _outside_quotes(text, lowercase)
    kinds = "|".join(re.escape(k) for k in sorted(r["numbered"], key=len, reverse=True))
    text = re.sub(rf"(?i)(?<!\w)({kinds}) +(?:№ *)?(\d+)(?=$| |[«„])", rf"\1 №{NBSP}\2", text)
    text = re.sub(rf"№[ {NBSP}]*(?=\d)", f"№{NBSP}", text)
    return text[:1].upper() + text[1:]
