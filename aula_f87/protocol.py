"""Wireless (2.4 GHz) and wired AULA F87 Pro HID protocols.

The wireless protocol is a sequence of 20-byte reports with report id 0x13.
The wired protocol uses 520-byte HID feature reports with report id 0x06.

This module only builds and validates packets.  Device I/O lives in device.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

REPORT_ID = 0x13
CMD_READ = 0x44
CMD_WRITE = 0x04
CMD_COLOR = 0x09
CMD_PER_KEY = 0x02
CMD_SAVE = 0x0A
CMD_AUDIO = 0x88

SUB_CONFIG = 0x0A
SUB_PALETTE = 0x25
SUB_PER_KEY = 0x1C
SUB_CONFIRM = 0x01

CUSTOM_EFFECT = 21
WIRELESS_CONFIG_FRAMES = 10
PALETTE_FRAMES = 37
PER_KEY_PLANES = 126

WIRELESS_EFFECT_IDS = frozenset((0, 1, 2, 3, 4, 5, 7, 8, 10, 11, 12, 13, 15, 16, 17))
WIRED_EFFECT_IDS = WIRELESS_EFFECT_IDS

WIRED_REPORT_SIZE = 520
WIRED_CONFIG_SIZE = 136


@dataclass(frozen=True)
class Effect:
    id: int
    name: str
    supports_speed: bool
    supports_color: bool
    aliases: tuple[str, ...] = ()

    @property
    def supported_wireless(self) -> bool:
        return self.id in WIRELESS_EFFECT_IDS

    @property
    def supported_wired(self) -> bool:
        return self.id in WIRED_EFFECT_IDS


EFFECTS: tuple[Effect, ...] = (
    Effect(0, "Off", False, False, ("none", "dark", "disable")),
    Effect(1, "Fixed On", False, True, ("fixed", "fixedon", "static", "solid", "constant", "on")),
    Effect(2, "Breathing", True, True, ("breathe", "breath")),
    Effect(3, "Rainbow", True, False, ("rainbow", "spectrum")),
    Effect(4, "Flash Away", True, True, ("flashaway", "flash")),
    Effect(5, "Raindrops", True, True, ("raindrops", "rain")),
    Effect(6, "Rainbow Wheel", True, True, ("rainbowwheel",)),
    Effect(7, "Ripple", True, True, ()),
    Effect(8, "Starlight", True, True, ("star",)),
    Effect(9, "Shadow", True, True, ()),
    Effect(10, "Retro Snake", True, True, ("retrosnake", "snake")),
    Effect(11, "Neon Stream", True, True, ("neonstream", "stream")),
    Effect(12, "Reaction", True, True, ("reactive",)),
    Effect(13, "Sine Wave", True, True, ("sinewave", "sine", "wave")),
    Effect(14, "Scanning", True, True, ("scan",)),
    Effect(15, "Windmill", True, False, ("rotatingwindmill",)),
    Effect(16, "Waterfall", True, False, ()),
    Effect(17, "Blossoming", True, False, ("blossom",)),
    Effect(18, "Rotating Storm", True, True, ("rotatingstorm",)),
)

EFFECT_BY_ID = {effect.id: effect for effect in EFFECTS}

# Each colour-capable hardware effect reads its custom colour from a different
# palette frame.  The mapping comes from the OEM configuration (LedOptN in
# Dev/kb/F87PRO/KB.ini); Fixed On is special because frame 1 also stores its
# on/off state and colour at a different offset.
PALETTE_FRAME_BY_EFFECT = {
    1: 1,
    2: 3,
    3: 2,
    4: 19,
    5: 15,
    6: 13,
    7: 20,
    8: 16,
    9: 18,
    10: 5,
    11: 7,
    12: 17,
    13: 12,
    14: 8,
    15: 28,
    16: 30,
    17: 14,
    18: 29,
}


def _normalise_effect_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


_EFFECT_LOOKUP: dict[str, Effect] = {}
for _effect in EFFECTS:
    _EFFECT_LOOKUP[_normalise_effect_name(_effect.name)] = _effect
    for _alias in _effect.aliases:
        _EFFECT_LOOKUP[_normalise_effect_name(_alias)] = _effect


def effect_by_name(value: str) -> Effect:
    """Resolve an effect name, alias, or numeric id."""

    text = value.strip()
    if text.isdigit():
        effect = EFFECT_BY_ID.get(int(text))
        if effect is not None:
            return effect
        raise ValueError(f"unknown effect id {text}")
    effect = _EFFECT_LOOKUP.get(_normalise_effect_name(text))
    if effect is None:
        raise ValueError(f"unknown effect {value!r}")
    return effect


def effect_name(effect_id: int) -> str:
    if effect_id == CUSTOM_EFFECT:
        return "Per-key custom"
    effect = EFFECT_BY_ID.get(effect_id)
    return effect.name if effect else f"Unknown ({effect_id})"


def supports_color(effect_id: int) -> bool:
    effect = EFFECT_BY_ID.get(effect_id)
    return bool(effect and effect.supports_color)


# ---------------------------------------------------------------------------
# Wireless 20-byte packets


def checksum(frame: Iterable[int]) -> int:
    data = list(frame)
    return sum(data[:19]) & 0xFF


def frame(command: int, subcommand: int, sequence: int = 0, payload: Iterable[int] = ()) -> bytes:
    result = bytearray(20)
    result[0] = REPORT_ID
    result[1] = command & 0xFF
    result[2] = subcommand & 0xFF
    result[3] = sequence & 0xFF
    payload = list(payload)
    for index, value in enumerate(payload[:15]):
        result[4 + index] = value & 0xFF
    result[19] = checksum(result)
    return bytes(result)


def read_request() -> bytes:
    return frame(CMD_READ, SUB_CONFIRM, 0)


def save_frame() -> bytes:
    return frame(CMD_SAVE, SUB_CONFIRM, 0, [0x04, 0x07] + [0] * 13)


def effect_table_location(effect_id: int) -> Optional[tuple[int, int]]:
    if 1 <= effect_id <= 6:
        return 4, 7 + (effect_id - 1) * 2
    if 7 <= effect_id <= 13:
        return 5, 5 + (effect_id - 7) * 2
    if 14 <= effect_id <= 18:
        return 6, 5 + (effect_id - 14) * 2
    return None


def speed_byte(speed: int, colorful: bool) -> int:
    return ((max(0, min(4, speed)) & 0x07) << 4) | (0x07 if colorful else 0x00)


def _validate_config(config: list[bytes]) -> None:
    if len(config) != WIRELESS_CONFIG_FRAMES:
        raise ValueError(f"expected {WIRELESS_CONFIG_FRAMES} config frames, got {len(config)}")
    for index, item in enumerate(config):
        if len(item) != 20:
            raise ValueError(f"config frame {index} is {len(item)} bytes, expected 20")
        if item[0] != REPORT_ID or item[1] != CMD_READ or item[2] != SUB_CONFIG:
            raise ValueError(f"config frame {index} is not a read/config frame")
        if item[3] != index:
            raise ValueError(f"config frame {index} has sequence {item[3]}")


def update_effect(
    config: list[bytes],
    effect_id: int,
    brightness: int,
    speed: int,
    colorful: bool,
    uses_custom_color: bool,
) -> list[bytes]:
    _validate_config(config)
    if not 0 <= effect_id <= 255:
        raise ValueError("effect id out of range")
    output = [bytearray(item) for item in config]
    for index, item in enumerate(output):
        item[1] = CMD_WRITE
        if index == 0:
            item[8] = 0x01
            item[14] = 0x00
            item[15] = effect_id
            item[17] = 0x01 if (uses_custom_color or colorful) else 0x03
        location = effect_table_location(effect_id)
        if location is not None and index == location[0]:
            offset = location[1]
            item[offset] = max(0, min(4, brightness))
            item[offset + 1] = speed_byte(speed, colorful)
        item[19] = checksum(item)
    return [bytes(item) for item in output]


def palette_frames(
    color: Optional[tuple[int, int, int]], effect_id: int = 1
) -> list[bytes]:
    """Build all 37 palette frames, placing *color* in the active effect's frame.

    Fixed On (effect 1) keeps its colour at payload offset 8 and uses offset 12
    as its on/off flag.  Every other effect reads the first colour triplet of
    its own palette frame, so writing only frame 1 leaves those effects with
    their default colours.
    """

    a = [0x0E, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
    b = [0x0E, 0xFF, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF, 0x00, 0xFF, 0x00]
    c = [0x0E, 0xFF, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00]
    target_sequence = PALETTE_FRAME_BY_EFFECT.get(effect_id, 1)
    frames: list[bytes] = []
    for sequence in range(PALETTE_FRAMES):
        if sequence == 0:
            payload = [0x0E] + [0] * 14
        elif sequence == 1:
            payload = [0x0E, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0, 0, 0, 0xFF, 0, 0]
            if color is not None and target_sequence == 1:
                payload[8] = color[0]
                payload[9] = color[1]
                payload[10] = color[2]
                payload[12] = 0xFF
        elif 2 <= sequence <= 20:
            payload = [a, b, c][(sequence - 2) % 3][:]
        elif sequence == 36:
            payload = [0x08, 0x00, 0x00, 0x5A, 0xA5] + [0] * 10
        else:
            payload = [0x0E] + [0] * 14
        if color is not None and sequence == target_sequence and sequence != 1:
            payload[1:4] = list(color)
        frames.append(frame(CMD_COLOR, SUB_PALETTE, sequence, payload))
    return frames


def per_key_frames(colors: dict[int, tuple[int, int, int]]) -> list[bytes]:
    planes = [[0] * PER_KEY_PLANES for _ in range(3)]
    for index, color in colors.items():
        if 0 <= index < PER_KEY_PLANES:
            planes[0][index] = color[0]
            planes[1][index] = color[1]
            planes[2][index] = color[2]
    result: list[bytes] = []
    sequence = 0
    for plane in planes:
        for part in range(9):
            start = part * 14
            payload = [0x0E] + plane[start : start + 14]
            result.append(frame(CMD_PER_KEY, SUB_PER_KEY, sequence, payload))
            sequence += 1
    trailer = [0] * 15
    trailer[0] = 0x06
    trailer[3] = 0x5A
    trailer[4] = 0xA5
    result.append(frame(CMD_PER_KEY, SUB_PER_KEY, sequence, trailer))
    return result


def custom_mode_config(config: list[bytes]) -> list[bytes]:
    _validate_config(config)
    output = [bytearray(item) for item in config]
    for index, item in enumerate(output):
        item[1] = CMD_WRITE
        if index == 0:
            item[8] = 0x01
            # Config block offset 9: the OEM self-define write sets this flag
            # together with effect 21 so the firmware treats the stored
            # per-key map as the active custom mode.
            item[14] = 0x01
            item[15] = CUSTOM_EFFECT
            item[17] = 0x01
        item[19] = checksum(item)
    return [bytes(item) for item in output]


FACTORY_CONFIG_PAYLOADS: tuple[tuple[int, ...], ...] = (
    (0x0E, 0x00, 0x03, 0x03, 0x01, 0x00, 0x00, 0x04, 0x04, 0x07, 0x00, 0x00, 0x20, 0x03, 0x00),
    (0x0E, 0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x02, 0x01, 0x00, 0xFF, 0x0A, 0x00, 0x00, 0x00),
    (0x0E, 0x01, 0x00, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    (0x0E, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
    (0x0E, 0xFF, 0xFF, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47),
    (0x0E, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47),
    (0x0E, 0x04, 0x47, 0x04, 0x47, 0x04, 0x47, 0x04, 0x37, 0x04, 0x37, 0x04, 0x37, 0x04, 0x37),
    (0x0E, 0x07, 0x47, 0x07, 0x47, 0x07, 0x44, 0x07, 0x44, 0x07, 0x44, 0x07, 0x44, 0x07, 0x44),
    (0x0E, 0x07, 0x44, 0x07, 0x44, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    (0x02, 0x5A, 0xA5, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00),
)


def factory_config_frames() -> list[bytes]:
    frames: list[bytes] = []
    for sequence, original in enumerate(FACTORY_CONFIG_PAYLOADS):
        payload = list(original)
        if sequence == 0:
            payload[4] = 0x01
        frames.append(frame(CMD_WRITE, SUB_CONFIG, sequence, payload))
    return frames


# ---------------------------------------------------------------------------
# Wired 520-byte feature packets


def feature_packet(command: int) -> bytearray:
    data = bytearray(WIRED_REPORT_SIZE)
    data[0] = 0x06
    data[1] = command & 0xFF
    data[4] = 0x01
    return data


def wired_model_query() -> bytes:
    data = feature_packet(0x82)
    data[2] = 0x01
    data[6] = 0x06
    return bytes(data)


def wired_read_trigger() -> bytes:
    data = feature_packet(0x84)
    data[6] = 0x80
    return bytes(data)


def wired_config_write(
    config: bytes,
    effect_id: int,
    brightness: int,
    speed: int,
    colorful: bool,
) -> bytes:
    if len(config) < WIRED_CONFIG_SIZE or config[0] != 0x06:
        raise ValueError("invalid wired configuration")
    data = feature_packet(0x04)
    data[6] = 0x80
    data[8:WIRED_CONFIG_SIZE] = config[8:WIRED_CONFIG_SIZE]
    data[17] = 0x01 if effect_id == 18 else 0x00
    data[18] = effect_id & 0xFF
    if 1 <= effect_id <= 18:
        offset = 64 + 2 * effect_id
        data[offset] = max(1, min(4, brightness))
        data[offset + 1] = ((max(0, min(4, speed)) & 0x07) << 4) | (0x07 if colorful else 0x00)
    return bytes(data)


def wired_custom_color(color: tuple[int, int, int]) -> bytes:
    data = feature_packet(0x0A)
    data[7] = 0x02
    offset = 29
    while offset + 2 < 514:
        data[offset] = color[0]
        data[offset + 1] = color[1]
        data[offset + 2] = color[2]
        offset += 3
    data[514] = 0x5A
    data[515] = 0xA5
    return bytes(data)


def wired_per_key_colors(colors: dict[int, tuple[int, int, int]]) -> bytes:
    data = feature_packet(0x06)
    data[6] = 0x7A
    data[7] = 0x01
    for index, color in colors.items():
        if 0 <= index < 122:
            data[8 + index] = color[0]
            data[134 + index] = color[1]
            data[260 + index] = color[2]
    return bytes(data)


# ---------------------------------------------------------------------------
# Colour parsing used by the CLI


NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "off": (0, 0, 0),
    "orange": (255, 128, 0),
    "amber": (255, 191, 0),
    "yellow": (255, 255, 0),
    "lime": (128, 255, 0),
    "cyan": (0, 255, 255),
    "teal": (0, 128, 128),
    "magenta": (255, 0, 255),
    "pink": (255, 105, 180),
    "purple": (128, 0, 255),
    "violet": (143, 0, 255),
    "lavender": (180, 160, 255),
    "indigo": (75, 0, 130),
    "warmwhite": (255, 214, 170),
    "warm": (255, 214, 170),
    "coolwhite": (200, 220, 255),
}


def parse_color(value: str | tuple[int, int, int] | list[int]) -> tuple[int, int, int]:
    if isinstance(value, str):
        text = value.strip().lower()
        named = NAMED_COLORS.get(text.replace(" ", "").replace("_", ""))
        if named is not None:
            return named
        if text.startswith("rgb(") and text.endswith(")"):
            parts = text[4:-1].split(",")
            if len(parts) == 3:
                try:
                    return tuple(max(0, min(255, int(part.strip()))) for part in parts)  # type: ignore[return-value]
                except ValueError:
                    raise ValueError(f"invalid colour {value!r}") from None
        if "," in text:
            parts = text.split(",")
            if len(parts) == 3:
                try:
                    return tuple(max(0, min(255, int(part.strip()))) for part in parts)  # type: ignore[return-value]
                except ValueError:
                    raise ValueError(f"invalid colour {value!r}") from None
        if text.startswith("#"):
            text = text[1:]
        if text.startswith("0x"):
            text = text[2:]
        if len(text) == 3 and all(ch in "0123456789abcdef" for ch in text):
            text = "".join(ch * 2 for ch in text)
        if len(text) == 6 and all(ch in "0123456789abcdef" for ch in text):
            return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
        raise ValueError(f"invalid colour {value!r}; use #RRGGBB or a colour name")
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return tuple(max(0, min(255, int(part))) for part in value)  # type: ignore[return-value]
    raise ValueError(f"invalid colour {value!r}")


def format_color(color: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)
