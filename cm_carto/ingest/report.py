"""Отчет импорта: распознано, не распознано, исправлено, требует подтверждения, изменения против прошлой сборки."""

from dataclasses import dataclass, field


def num(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def at(x: float, y: float) -> str:
    """Место на чертеже: восток и север в системе координат проекта."""
    return f"около В {x:.0f}, С {y:.0f}"


@dataclass
class Report:
    title: str
    header: list[str]
    recognized: list[tuple[str, str, int]] = field(default_factory=list)   # (откуда, слой геопакета, объектов)
    unrecognized: list[str] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    confirm: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    unknown_layers: list[tuple[str, int]] = field(default_factory=list)   # слои чертежа без правила и не в ignore: (имя, объектов)
    changes: list[str] = field(default_factory=list)

    def compare(self, before: set[str] | None, after: set[str]) -> None:
        """Исчезнувшие и новые object_id против прошлой сборки этого входа: база перепривязывает по ним fact_ids."""
        if before is None:
            self.changes = ["прошлой сборки нет"]
            return
        gone, new = sorted(before - after), sorted(after - before)
        self.changes = [f"исчезли ({len(gone)}): {', '.join(gone[:40])}{' и еще ' + str(len(gone) - 40) if len(gone) > 40 else ''}"
                        if gone else "исчезнувших нет",
                        f"новые ({len(new)}): {', '.join(new[:40])}{' и еще ' + str(len(new) - 40) if len(new) > 40 else ''}"
                        if new else "новых нет"]

    def render(self) -> str:
        out = [f"# {self.title}", "", *[f"- {line}" for line in self.header], "", "## Распознано", ""]
        if self.recognized:
            out += ["| откуда | слой геопакета | объектов |", "|---|---|---|"]
            out += [f"| {src} | `{dst}` | {n} |" for src, dst, n in self.recognized]
        else:
            out.append("ничего")
        for title, items in [("Не распознано", self.unrecognized), ("Исправлено", self.fixed),
                             ("Требует подтверждения Романа", self.confirm), ("Игнорировано правилами", self.ignored),
                             ("Против прошлой сборки", self.changes)]:
            out += ["", f"## {title}", ""]
            out += [f"- {item}" for item in items] if items else ["нет"]
        return "\n".join(out) + "\n"
