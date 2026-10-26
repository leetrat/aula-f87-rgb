"""F87 Pro key labels and LED indices.

The 88-key layout uses LED indices 0-101 with gaps between rows.  The
protocol reserves 126 positions per colour plane, so unused positions are left
black.
"""

from __future__ import annotations

from dataclasses import dataclass

# (canonical name, LED index)
KEY_LAYOUT: tuple[tuple[str, int], ...] = (
    ("esc", 0),
    ("f1", 12),
    ("f2", 18),
    ("f3", 24),
    ("f4", 30),
    ("f5", 36),
    ("f6", 42),
    ("f7", 48),
    ("f8", 54),
    ("f9", 60),
    ("f10", 66),
    ("f11", 72),
    ("f12", 78),
    ("print", 84),
    ("scroll", 90),
    ("pause", 96),
    ("grave", 1),
    ("1", 7),
    ("2", 13),
    ("3", 19),
    ("4", 25),
    ("5", 31),
    ("6", 37),
    ("7", 43),
    ("8", 49),
    ("9", 55),
    ("0", 61),
    ("minus", 67),
    ("equal", 73),
    ("backspace", 79),
    ("insert", 85),
    ("home", 91),
    ("pageup", 97),
    ("tab", 2),
    ("q", 8),
    ("w", 14),
    ("e", 20),
    ("r", 26),
    ("t", 32),
    ("y", 38),
    ("u", 44),
    ("i", 50),
    ("o", 56),
    ("p", 62),
    ("leftbracket", 68),
    ("rightbracket", 74),
    ("backslash", 80),
    ("delete", 86),
    ("end", 92),
    ("pagedown", 98),
    ("caps", 3),
    ("a", 9),
    ("s", 15),
    ("d", 21),
    ("f", 27),
    ("g", 33),
    ("h", 39),
    ("j", 45),
    ("k", 51),
    ("l", 57),
    ("semicolon", 63),
    ("apostrophe", 69),
    ("enter", 81),
    ("lshift", 4),
    ("iso", 76),
    ("z", 10),
    ("x", 16),
    ("c", 22),
    ("v", 28),
    ("b", 34),
    ("n", 40),
    ("m", 46),
    ("comma", 52),
    ("period", 58),
    ("slash", 64),
    ("rshift", 82),
    ("up", 94),
    ("lctrl", 5),
    ("lwin", 11),
    ("lalt", 17),
    ("space", 35),
    ("ralt", 53),
    ("fn", 59),
    ("menu", 65),
    ("rctrl", 83),
    ("left", 89),
    ("down", 95),
    ("right", 101),
)


@dataclass(frozen=True)
class KeySpec:
    """One physical keycap in the visual keyboard layout."""

    label: str
    led: int
    width: float = 1.0


@dataclass(frozen=True)
class KeyRow:
    """A row of keycaps and the gaps inserted before particular keys."""

    keys: tuple[KeySpec, ...]
    gaps: tuple[tuple[int, float], ...] = ()


# Visual layout of the F87 Pro.  Widths are in keyboard units (1u); gaps are
# the extra space before the key at the given index.  The ISO key is included
# between left Shift and Z, which keeps the row width identical to the compact
# macOS layout while exposing LED 76 to the painting UI.
LAYOUT_ROWS: tuple[KeyRow, ...] = (
    KeyRow(
        keys=(
            KeySpec("Esc", 0),
            KeySpec("F1", 12),
            KeySpec("F2", 18),
            KeySpec("F3", 24),
            KeySpec("F4", 30),
            KeySpec("F5", 36),
            KeySpec("F6", 42),
            KeySpec("F7", 48),
            KeySpec("F8", 54),
            KeySpec("F9", 60),
            KeySpec("F10", 66),
            KeySpec("F11", 72),
            KeySpec("F12", 78),
            KeySpec("Prt", 84),
            KeySpec("Scr", 90),
            KeySpec("Pse", 96),
        ),
        gaps=((1, 1.0), (5, 0.5), (9, 0.5), (13, 0.25)),
    ),
    KeyRow(
        keys=(
            KeySpec("`", 1),
            KeySpec("1", 7),
            KeySpec("2", 13),
            KeySpec("3", 19),
            KeySpec("4", 25),
            KeySpec("5", 31),
            KeySpec("6", 37),
            KeySpec("7", 43),
            KeySpec("8", 49),
            KeySpec("9", 55),
            KeySpec("0", 61),
            KeySpec("-", 67),
            KeySpec("=", 73),
            KeySpec("Bksp", 79, 2.0),
            KeySpec("Ins", 85),
            KeySpec("Home", 91),
            KeySpec("PgUp", 97),
        ),
        gaps=((14, 0.25),),
    ),
    KeyRow(
        keys=(
            KeySpec("Tab", 2, 1.5),
            KeySpec("Q", 8),
            KeySpec("W", 14),
            KeySpec("E", 20),
            KeySpec("R", 26),
            KeySpec("T", 32),
            KeySpec("Y", 38),
            KeySpec("U", 44),
            KeySpec("I", 50),
            KeySpec("O", 56),
            KeySpec("P", 62),
            KeySpec("[", 68),
            KeySpec("]", 74),
            KeySpec("\\", 80, 1.5),
            KeySpec("Del", 86),
            KeySpec("End", 92),
            KeySpec("PgDn", 98),
        ),
        gaps=((14, 0.25),),
    ),
    KeyRow(
        keys=(
            KeySpec("Caps", 3, 1.75),
            KeySpec("A", 9),
            KeySpec("S", 15),
            KeySpec("D", 21),
            KeySpec("F", 27),
            KeySpec("G", 33),
            KeySpec("H", 39),
            KeySpec("J", 45),
            KeySpec("K", 51),
            KeySpec("L", 57),
            KeySpec(";", 63),
            KeySpec("'", 69),
            KeySpec("Enter", 81, 2.25),
        ),
    ),
    KeyRow(
        keys=(
            KeySpec("Shift", 4, 1.25),
            KeySpec("\\", 76),
            KeySpec("Z", 10),
            KeySpec("X", 16),
            KeySpec("C", 22),
            KeySpec("V", 28),
            KeySpec("B", 34),
            KeySpec("N", 40),
            KeySpec("M", 46),
            KeySpec(",", 52),
            KeySpec(".", 58),
            KeySpec("/", 64),
            KeySpec("Shift", 82, 2.75),
            KeySpec("↑", 94),
        ),
        gaps=((13, 1.25),),
    ),
    KeyRow(
        keys=(
            KeySpec("Ctrl", 5, 1.25),
            KeySpec("Win", 11, 1.25),
            KeySpec("Alt", 17, 1.25),
            KeySpec("Space", 35, 6.25),
            KeySpec("Alt", 53, 1.25),
            KeySpec("Fn", 59, 1.25),
            KeySpec("App", 65, 1.25),
            KeySpec("Ctrl", 83, 1.25),
            KeySpec("←", 89),
            KeySpec("↓", 95),
            KeySpec("→", 101),
        ),
        gaps=((8, 0.25),),
    ),
)

LAYOUT_KEYS: tuple[KeySpec, ...] = tuple(key for row in LAYOUT_ROWS for key in row.keys)
KEY_LABEL_BY_LED: dict[int, str] = {key.led: key.label for key in LAYOUT_KEYS}

# Keep the visual layout and the flat command-line map in sync.
assert {key.led for key in LAYOUT_KEYS} == {led for _name, led in KEY_LAYOUT}

KEY_ALIASES: dict[str, str] = {
    "escape": "esc",
    "`": "grave",
    "backtick": "grave",
    "tilde": "grave",
    "bksp": "backspace",
    "←": "left",
    "→": "right",
    "↑": "up",
    "↓": "down",
    "printscreen": "print",
    "prtsc": "print",
    "prtscn": "print",
    "scrolllock": "scroll",
    "scr": "scroll",
    "break": "pause",
    "pse": "pause",
    "capslock": "caps",
    "leftshift": "lshift",
    "rightshift": "rshift",
    "shift": "lshift",
    "leftctrl": "lctrl",
    "rightctrl": "rctrl",
    "ctrl": "lctrl",
    "control": "lctrl",
    "leftcontrol": "lctrl",
    "rightcontrol": "rctrl",
    "win": "lwin",
    "windows": "lwin",
    "super": "lwin",
    "meta": "lwin",
    "cmd": "lwin",
    "command": "lwin",
    "leftwin": "lwin",
    "leftalt": "lalt",
    "rightalt": "ralt",
    "altgr": "ralt",
    "alt": "lalt",
    "spacebar": "space",
    "application": "menu",
    "app": "menu",
    "context": "menu",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "page_down": "pagedown",
    "page_up": "pageup",
    "del": "delete",
    "ins": "insert",
    "lbracket": "leftbracket",
    "rbracket": "rightbracket",
    "[": "leftbracket",
    "]": "rightbracket",
    "leftbrace": "leftbracket",
    "rightbrace": "rightbracket",
    "\\": "backslash",
    "semi": "semicolon",
    ";": "semicolon",
    "quote": "apostrophe",
    "'": "apostrophe",
    "comma": "comma",
    ",": "comma",
    "dot": "period",
    "period": "period",
    ".": "period",
    "/": "slash",
    "minus": "minus",
    "-": "minus",
    "equal": "equal",
    "=": "equal",
    "intlbackslash": "iso",
    "international": "iso",
    "oem102": "iso",
}

KEY_INDEX: dict[str, int] = {name: led for name, led in KEY_LAYOUT}
KEY_NAME_BY_INDEX: dict[int, str] = {led: name for name, led in KEY_LAYOUT}


def _normalise(name: str) -> str:
    return name.strip().lower().replace(" ", "").replace("-", "").replace("_", "")


def resolve_key(name: str | int) -> int:
    """Resolve a key label, alias, LED index, or ``ledN``/``#N`` to an LED index."""

    if isinstance(name, int):
        if 0 <= name < 126:
            return name
        raise ValueError(f"LED index {name} is outside 0..125")
    text = name.strip()
    lowered = text.lower()
    if lowered.startswith("#") and lowered[1:].isdigit():
        index = int(lowered[1:])
        if 0 <= index < 126:
            return index
        raise ValueError(f"LED index {index} is outside 0..125")
    if lowered.startswith("led") and lowered[3:].isdigit():
        index = int(lowered[3:])
        if 0 <= index < 126:
            return index
        raise ValueError(f"LED index {index} is outside 0..125")
    normalised = _normalise(text)
    if normalised in KEY_INDEX:
        return KEY_INDEX[normalised]
    alias = KEY_ALIASES.get(normalised)
    if alias is not None:
        return KEY_INDEX[alias]
    if text.isdigit():
        # Bare numbers are key labels ("1".."0"), not LED indices.
        if text in KEY_INDEX:
            return KEY_INDEX[text]
        raise ValueError(f"unknown key {name!r}; use ledN for a raw LED index")
    suggestions = [key for key in KEY_INDEX if normalised in key or key in normalised]
    hint = f" Did you mean: {', '.join(suggestions[:5])}?" if suggestions else ""
    raise ValueError(f"unknown key {name!r}.{hint}")


def key_name(index: int) -> str:
    return KEY_NAME_BY_INDEX.get(index, f"led{index}")
