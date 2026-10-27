"""Linux hidraw transport and AULA F87 Pro onboard configuration operations."""

from __future__ import annotations

import fcntl
import glob
import os
import select
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import protocol
from .keys import resolve_key


class AulaError(Exception):
    """Base error shown to the user."""


class DeviceNotFound(AulaError):
    pass


class DevicePermissionError(AulaError):
    pass


class ProtocolError(AulaError):
    pass


class VerificationError(AulaError):
    pass


class UnsupportedSetting(AulaError):
    pass


# Linux _IOC encoding (asm-generic).  HIDIOCSFEATURE/GFEATURE both use
# _IOC_WRITE|_IOC_READ and the report size in the size field.
def _ioc(direction: int, type_char: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | number


_IOC_WRITE = 1
_IOC_READ = 2
HIDIOCSFEATURE = lambda size: _ioc(_IOC_WRITE | _IOC_READ, "H", 0x06, size)
HIDIOCGFEATURE = lambda size: _ioc(_IOC_WRITE | _IOC_READ, "H", 0x07, size)


@dataclass(frozen=True)
class DeviceInfo:
    path: str
    vid: int
    pid: int
    name: str
    protocol: Optional[str]  # "wireless", "wired", or None for a non-control interface
    interface: str = ""

    @property
    def mode(self) -> str:
        if self.protocol == "wireless":
            return "2.4 GHz"
        if self.protocol == "wired":
            return "USB"
        return "unknown"


def _sysfs_uevent(hidraw_path: str) -> dict[str, str]:
    sys_name = Path(hidraw_path).name
    uevent_path = Path("/sys/class/hidraw") / sys_name / "device" / "uevent"
    try:
        text = uevent_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def _report_descriptor(hidraw_path: str) -> bytes:
    sys_name = Path(hidraw_path).name
    descriptor_path = Path("/sys/class/hidraw") / sys_name / "device" / "report_descriptor"
    try:
        return descriptor_path.read_bytes()
    except OSError:
        return b""


def scan_devices() -> list[DeviceInfo]:
    """Return every AULA F87 wired receiver/keyboard hidraw interface."""

    result: list[DeviceInfo] = []
    for sys_path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev_path = "/dev/" + Path(sys_path).name
        uevent = _sysfs_uevent(dev_path)
        hid_id = uevent.get("HID_ID", "")
        name = uevent.get("HID_NAME", "AULA F87")
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        try:
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
        except ValueError:
            continue
        if (vid, pid) not in {(0x3554, 0xFA09), (0x258A, 0x010C)}:
            continue

        descriptor = _report_descriptor(dev_path)
        if (vid, pid) == (0x3554, 0xFA09):
            # 3554:FA09 exposes both the normal keyboard collection and the
            # vendor 0xFF02 collection with report id 0x13.  Only the latter
            # can be used for configuration.
            if b"\x06\x02\xff" in descriptor and b"\x85\x13" in descriptor:
                protocol_name: Optional[str] = "wireless"
            else:
                protocol_name = None
        else:
            # The wired device exposes a 520-byte feature report; try any
            # 258A:010C interface that looks vendor-specific.
            if b"\x06\x00\xff" in descriptor or b"\x85\x06" in descriptor:
                protocol_name = "wired"
            else:
                protocol_name = None

        result.append(
            DeviceInfo(
                path=dev_path,
                vid=vid,
                pid=pid,
                name=name,
                protocol=protocol_name,
                interface=uevent.get("ID_USB_INTERFACE_NUM", ""),
            )
        )
    return result


class HidrawDevice:
    def __init__(self, path: str):
        self.path = path
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except PermissionError as exc:
            raise DevicePermissionError(
                f"permission denied opening {path}. Install the included udev rule or run as a user "
                "with an ACL on the device (see README.md)."
            ) from exc
        except FileNotFoundError as exc:
            raise DeviceNotFound(f"device {path} does not exist") from exc
        except OSError as exc:
            raise AulaError(f"cannot open {path}: {exc}") from exc

    def close(self) -> None:
        if getattr(self, "fd", None) is not None:
            try:
                os.close(self.fd)
            finally:
                self.fd = None  # type: ignore[assignment]

    def __enter__(self) -> "HidrawDevice":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def write(self, data: bytes) -> None:
        try:
            sent = os.write(self.fd, data)
        except OSError as exc:
            raise ProtocolError(f"write to {self.path} failed: {exc}") from exc
        if sent != len(data):
            raise ProtocolError(f"short write to {self.path}: {sent}/{len(data)} bytes")

    def read(self, timeout: float = 0.12) -> Optional[bytes]:
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
        except OSError as exc:
            raise ProtocolError(f"select on {self.path} failed: {exc}") from exc
        if not ready:
            return None
        try:
            return os.read(self.fd, 4096)
        except BlockingIOError:
            return None
        except OSError as exc:
            raise ProtocolError(f"read from {self.path} failed: {exc}") from exc

    def drain(self) -> None:
        while True:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0)
            except OSError:
                return
            if not ready:
                return
            try:
                if not os.read(self.fd, 4096):
                    return
            except OSError:
                return

    def send_feature(self, data: bytes) -> None:
        buf = bytearray(data)
        try:
            fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(buf)), buf, True)
        except OSError as exc:
            raise ProtocolError(f"feature report write to {self.path} failed: {exc}") from exc

    def get_feature(self, report_id: int = 0x06, length: int = protocol.WIRED_REPORT_SIZE) -> bytes:
        buf = bytearray(length)
        buf[0] = report_id
        try:
            count = fcntl.ioctl(self.fd, HIDIOCGFEATURE(length), buf, True)
        except OSError as exc:
            raise ProtocolError(f"feature report read from {self.path} failed: {exc}") from exc
        if count <= 0:
            raise ProtocolError(f"feature report read from {self.path} returned no data")
        return bytes(buf[:count])


def _effect_summary_from_wireless(config: list[bytes], include_raw: bool = False) -> dict[str, object]:
    effect_id = config[0][15]
    location = protocol.effect_table_location(effect_id)
    brightness: Optional[int] = None
    speed: Optional[int] = None
    colorful: Optional[bool] = None
    if location is not None:
        sequence, offset = location
        brightness = config[sequence][offset]
        speed_byte = config[sequence][offset + 1]
        speed = (speed_byte >> 4) & 0x0F
        colorful = (speed_byte & 0x0F) == 0x07
    sleep_raw = config[1][15]
    debounce_raw = config[0][8]
    summary: dict[str, object] = {
        "effect_id": effect_id,
        "effect": protocol.effect_name(effect_id),
        "custom": config[0][14] == 0x01,
        "brightness": brightness,
        "speed": speed,
        "colorful": colorful,
        "sleep_minutes": sleep_raw // 2 if sleep_raw <= 120 else None,
        "sleep_raw": sleep_raw,
        "debounce_ms": debounce_raw + 1 if debounce_raw <= 4 else None,
        "debounce_raw": debounce_raw,
    }
    if include_raw:
        summary["frames"] = [item.hex(" ") for item in config]
    return summary


def _effect_summary_from_wired(config: bytes, include_raw: bool = False) -> dict[str, object]:
    effect_id = config[18]
    offset = 64 + 2 * effect_id
    brightness: Optional[int] = None
    speed: Optional[int] = None
    colorful: Optional[bool] = None
    if config and offset + 1 < len(config):
        brightness = config[offset]
        speed_byte = config[offset + 1]
        speed = (speed_byte >> 4) & 0x0F
        colorful = (speed_byte & 0x0F) == 0x07
    summary: dict[str, object] = {
        "effect_id": effect_id,
        "effect": protocol.effect_name(effect_id),
        "brightness": brightness,
        "speed": speed,
        "colorful": colorful,
        "sleep_minutes": None,
        "debounce_ms": None,
    }
    if include_raw:
        summary["config"] = config[: protocol.WIRED_CONFIG_SIZE].hex(" ")
    return summary


class WirelessKeyboard:
    protocol_name = "wireless"

    def __init__(self, raw: HidrawDevice, info: DeviceInfo):
        self.raw = raw
        self.info = info
        self.verbose = False

    def close(self) -> None:
        self.raw.close()

    def probe(self) -> None:
        self.read_config()

    def read_config(self, verbose: bool = False) -> list[bytes]:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            if attempt:
                time.sleep(0.1)
            self.raw.drain()
            self.raw.write(protocol.read_request())
            time.sleep(0.05)
            config: list[Optional[bytes]] = [None] * protocol.WIRELESS_CONFIG_FRAMES
            deadline = time.monotonic() + 1.6
            while time.monotonic() < deadline and any(item is None for item in config):
                try:
                    report = self.raw.read(0.12)
                except ProtocolError as exc:
                    last_error = exc
                    break
                if not report or len(report) < 20:
                    continue
                frame = report[:20]
                if frame[0] != protocol.REPORT_ID:
                    continue
                if frame[1] != protocol.CMD_READ or frame[2] != protocol.SUB_CONFIG:
                    continue
                if frame[19] != protocol.checksum(frame):
                    continue
                sequence = frame[3]
                if 0 <= sequence < protocol.WIRELESS_CONFIG_FRAMES:
                    config[sequence] = frame
            if all(item is not None for item in config):
                return [bytes(item) for item in config]  # type: ignore[arg-type]
        detail = f" ({last_error})" if last_error else ""
        raise ProtocolError(
            "the 2.4 GHz receiver did not return all 10 configuration frames; "
            "make sure the keyboard is powered on and connected" + detail
        )

    def status(self, include_raw: bool = False) -> dict[str, object]:
        config = self.read_config()
        summary = _effect_summary_from_wireless(config, include_raw=include_raw)
        summary.update(
            {
                "protocol": "wireless",
                "path": self.info.path,
                "vid": f"{self.info.vid:04X}",
                "pid": f"{self.info.pid:04X}",
                "device": self.info.name,
                "mode": "2.4 GHz",
            }
        )
        return summary

    def _transmit_one(self, item: bytes, require_echo: bool = True) -> None:
        attempts = 2 if require_echo else 1
        if self.verbose:
            print(f"  -> {item.hex(' ')}")
        for _attempt in range(attempts):
            self.raw.write(item)
            time.sleep(0.003)
            deadline = time.monotonic() + 0.35
            while time.monotonic() < deadline:
                report = self.raw.read(0.07)
                if report is None:
                    continue
                if len(report) >= len(item) and report[: len(item)] == item:
                    if self.verbose:
                        print("  <- echo")
                    return
        if require_echo:
            command = f"{item[1]:02X}/{item[2]:02X}" if len(item) > 2 else "unknown"
            raise ProtocolError(
                f"the keyboard did not echo control packet {command}; stopped before saving"
            )

    def _transmit(self, frames: list[bytes]) -> None:
        for item in frames:
            self._transmit_one(item)

    def _save_and_read(self) -> list[bytes]:
        self._transmit_one(protocol.save_frame(), require_echo=False)
        # The receiver briefly stops answering while it commits the profile.
        time.sleep(0.30)
        return self.read_config()

    def apply_effect(
        self,
        effect_id: int,
        brightness: int,
        speed: int,
        colorful: bool,
        color: Optional[tuple[int, int, int]],
    ) -> dict[str, object]:
        effect = protocol.EFFECT_BY_ID.get(effect_id)
        if effect is None or effect_id not in protocol.WIRELESS_EFFECT_IDS:
            raise UnsupportedSetting(
                f"effect {protocol.effect_name(effect_id)!r} is not supported by the 2.4 GHz receiver"
            )
        if effect_id == 0:
            colorful = False
        elif not effect.supports_color:
            colorful = True
        elif not colorful and color is None:
            raise AulaError(
                f"{effect.name} supports a custom colour: pass --color #RRGGBB or --spectrum"
            )
        custom_color = bool(effect.supports_color and effect_id != 0 and not colorful)
        base = self.read_config()
        writes = protocol.update_effect(
            base,
            effect_id=effect_id,
            brightness=brightness,
            speed=speed,
            colorful=colorful,
            uses_custom_color=custom_color,
        )
        self._transmit(writes)
        self._transmit(protocol.palette_frames(color if custom_color else None))
        verified = self._save_and_read()
        if verified[0][15] != effect_id:
            raise VerificationError(
                f"the keyboard replied, but did not activate {effect.name} "
                f"(reported effect {protocol.effect_name(verified[0][15])})"
            )
        location = protocol.effect_table_location(effect_id)
        if location is not None:
            sequence, offset = location
            expected_brightness = max(0, min(4, brightness))
            expected_speed = protocol.speed_byte(speed, colorful)
            if (
                verified[sequence][offset] != expected_brightness
                or verified[sequence][offset + 1] != expected_speed
            ):
                raise VerificationError(
                    "the effect changed, but the keyboard did not retain the requested "
                    "brightness, speed, or colour mode"
                )
        return _effect_summary_from_wireless(verified)

    def apply_per_key(self, colors: dict[int, tuple[int, int, int]]) -> dict[str, object]:
        if not colors:
            raise AulaError("no per-key colours were given")
        base = self.read_config()
        self._transmit(protocol.custom_mode_config(base))
        self._transmit(protocol.per_key_frames(colors))
        verified = self._save_and_read()
        if verified[0][15] != protocol.CUSTOM_EFFECT or verified[0][14] != 0x01:
            raise VerificationError(
                "the keyboard replied, but did not retain per-key colour mode "
                "or its custom-mode flag"
            )
        summary = _effect_summary_from_wireless(verified)
        summary["per_key_count"] = len(colors)
        return summary

    def apply_sleep(self, minutes: int) -> dict[str, object]:
        expected = max(0, min(60, minutes)) * 2
        config = self.read_config()
        output = [bytearray(item) for item in config]
        for index, item in enumerate(output):
            item[1] = protocol.CMD_WRITE
            if index == 0:
                item[8] = 0x01
            if index == 1:
                item[15] = expected
            item[19] = protocol.checksum(item)
        self._transmit([bytes(item) for item in output])
        verified = self._save_and_read()
        if verified[1][15] != expected:
            raise VerificationError("the keyboard did not confirm the requested sleep timer")
        return _effect_summary_from_wireless(verified)

    def apply_debounce(self, milliseconds: int) -> dict[str, object]:
        expected = max(1, min(5, milliseconds)) - 1
        config = self.read_config()
        output = [bytearray(item) for item in config]
        for item in output:
            item[1] = protocol.CMD_WRITE
            item[19] = protocol.checksum(item)
        output[0][8] = expected
        output[0][19] = protocol.checksum(output[0])
        self._transmit([bytes(item) for item in output])
        verified = self._save_and_read()
        if verified[0][8] != expected:
            raise VerificationError("the keyboard did not confirm the requested debounce time")
        return _effect_summary_from_wireless(verified)

    def factory_reset(self) -> dict[str, object]:
        self._transmit(protocol.factory_config_frames())
        self._transmit(protocol.palette_frames(None))
        verified = self._save_and_read()
        if verified[0][15] != 0 or verified[1][15] != 0x0A or verified[0][8] != 0x01:
            raise VerificationError("the keyboard did not confirm all factory-default settings")
        return _effect_summary_from_wireless(verified)

    def restore_config(self, config: list[bytes]) -> dict[str, object]:
        if len(config) != protocol.WIRELESS_CONFIG_FRAMES:
            raise AulaError("backup does not contain 10 configuration frames")
        for index, item in enumerate(config):
            if len(item) != 20:
                raise AulaError(f"backup frame {index} is not 20 bytes")
            if item[0] != protocol.REPORT_ID or item[2] != protocol.SUB_CONFIG or item[3] != index:
                raise AulaError(f"backup frame {index} is not a valid config frame")
        writes: list[bytes] = []
        for item in config:
            output = bytearray(item)
            output[1] = protocol.CMD_WRITE
            output[19] = protocol.checksum(output)
            writes.append(bytes(output))
        self._transmit(writes)
        verified = self._save_and_read()
        for index, expected in enumerate(config):
            # Frame 9 is a trailer containing the 0x5A 0xA5 marker plus a
            # small amount of firmware-managed state.  The firmware updates
            # those bytes after a save, so only frames 0-8 are stable.
            if index < 9 and verified[index][2:19] != expected[2:19]:
                raise VerificationError(
                    f"the keyboard accepted the backup, but configuration frame {index} "
                    "does not match after read-back"
                )
        return _effect_summary_from_wireless(verified)


class WiredKeyboard:
    protocol_name = "wired"

    def __init__(self, raw: HidrawDevice, info: DeviceInfo):
        self.raw = raw
        self.info = info
        self.verbose = False
        self._primed = False

    def close(self) -> None:
        self.raw.close()

    def _prime(self) -> None:
        self.raw.send_feature(protocol.wired_model_query())
        time.sleep(0.008)
        self.raw.get_feature(length=protocol.WIRED_REPORT_SIZE)
        self._primed = True

    def probe(self) -> None:
        if not self._primed:
            self._prime()
        self.read_config()

    def read_config(self) -> bytes:
        if not self._primed:
            self._prime()
        self.raw.send_feature(protocol.wired_read_trigger())
        time.sleep(0.008)
        response = self.raw.get_feature(length=protocol.WIRED_REPORT_SIZE)
        if len(response) < protocol.WIRED_CONFIG_SIZE:
            raise ProtocolError(
                f"wired config response was only {len(response)} bytes; expected at least "
                f"{protocol.WIRED_CONFIG_SIZE}"
            )
        if response[134] != 0x5A or response[135] != 0xA5:
            header = response[:20].hex(" ")
            raise ProtocolError(f"unexpected wired config response (header {header})")
        return response

    def status(self, include_raw: bool = False) -> dict[str, object]:
        config = self.read_config()
        summary = _effect_summary_from_wired(config, include_raw=include_raw)
        summary.update(
            {
                "protocol": "wired",
                "path": self.info.path,
                "vid": f"{self.info.vid:04X}",
                "pid": f"{self.info.pid:04X}",
                "device": self.info.name,
                "mode": "USB",
            }
        )
        return summary

    def apply_effect(
        self,
        effect_id: int,
        brightness: int,
        speed: int,
        colorful: bool,
        color: Optional[tuple[int, int, int]],
    ) -> dict[str, object]:
        effect = protocol.EFFECT_BY_ID.get(effect_id)
        if effect is None or effect_id not in protocol.WIRED_EFFECT_IDS:
            raise UnsupportedSetting(
                f"effect {protocol.effect_name(effect_id)!r} is not supported by the wired firmware"
            )
        if effect_id == 0:
            colorful = False
        elif not effect.supports_color:
            colorful = True
        elif not colorful and color is None:
            raise AulaError(
                f"{effect.name} supports a custom colour: pass --color #RRGGBB or --spectrum"
            )
        has_color = bool(effect.supports_color and effect_id != 0 and not colorful)
        if has_color and color is not None:
            self.raw.send_feature(protocol.wired_custom_color(color))
        config = self.read_config()
        write = protocol.wired_config_write(
            config,
            effect_id=effect_id,
            brightness=max(1, brightness),
            speed=speed,
            colorful=colorful or not effect.supports_color,
        )
        self.raw.send_feature(write)
        verified = self.read_config()
        if verified[18] != effect_id:
            raise VerificationError(
                f"the keyboard replied, but did not activate {effect.name} "
                f"(reported effect {protocol.effect_name(verified[18])})"
            )
        if 1 <= effect_id <= 18:
            offset = 64 + 2 * effect_id
            expected_brightness = max(1, min(4, brightness))
            expected_speed = ((max(0, min(4, speed)) & 0x07) << 4) | (
                0x07 if (colorful or not effect.supports_color) else 0x00
            )
            if verified[offset] != expected_brightness or verified[offset + 1] != expected_speed:
                raise VerificationError(
                    "the effect changed, but the keyboard did not retain the requested "
                    "brightness, speed, or colour mode"
                )
        return _effect_summary_from_wired(verified)

    def apply_per_key(self, colors: dict[int, tuple[int, int, int]]) -> dict[str, object]:
        if not colors:
            raise AulaError("no per-key colours were given")
        self.raw.send_feature(protocol.wired_per_key_colors(colors))
        config = self.read_config()
        offset = 64 + 2 * 18
        brightness = config[offset] if offset < len(config) and 1 <= config[offset] <= 4 else 4
        self.raw.send_feature(
            protocol.wired_config_write(
                config,
                effect_id=18,
                brightness=brightness,
                speed=0,
                colorful=False,
            )
        )
        verified = self.read_config()
        if verified[18] != 18:
            raise VerificationError(
                "the keyboard replied, but did not enter per-key colour mode"
            )
        summary = _effect_summary_from_wired(verified)
        summary["per_key_count"] = len(colors)
        return summary

    def apply_sleep(self, minutes: int) -> dict[str, object]:
        raise UnsupportedSetting(
            "sleep timing has not been decoded safely for the wired firmware; use the 2.4 GHz receiver"
        )

    def apply_debounce(self, milliseconds: int) -> dict[str, object]:
        raise UnsupportedSetting(
            "debounce timing has not been decoded safely for the wired firmware; use the 2.4 GHz receiver"
        )

    def factory_reset(self) -> dict[str, object]:
        raise UnsupportedSetting(
            "factory reset is available only on the verified 2.4 GHz control protocol"
        )

    def restore_config(self, config: list[bytes]) -> dict[str, object]:
        raise UnsupportedSetting("configuration restore is available only on the 2.4 GHz receiver")


def open_keyboard(device: Optional[str] = None, verbose: bool = False) -> WirelessKeyboard | WiredKeyboard:
    infos = scan_devices()
    if not infos:
        raise DeviceNotFound(
            "no AULA F87 Pro control interface found. Connect the 2.4 GHz receiver or the USB "
            "cable; the wired device is 258A:010C and the receiver is 3554:FA09."
        )
    if device:
        matches = [info for info in infos if info.path == device]
        if not matches:
            known = ", ".join(info.path for info in infos)
            raise DeviceNotFound(f"no AULA F87 interface at {device}; known control interfaces: {known}")
        infos = matches
    else:
        # The 2.4 GHz protocol is the verified path.  If both are connected,
        # prefer the receiver and let --device override it.
        infos.sort(key=lambda info: 0 if info.protocol == "wireless" else 1 if info.protocol == "wired" else 2)

    errors: list[str] = []
    for info in infos:
        if info.protocol is None:
            errors.append(f"{info.path}: not the vendor control interface")
            continue
        raw: Optional[HidrawDevice] = None
        try:
            raw = HidrawDevice(info.path)
            if info.protocol == "wireless":
                keyboard: WirelessKeyboard | WiredKeyboard = WirelessKeyboard(raw, info)
            else:
                keyboard = WiredKeyboard(raw, info)
            keyboard.verbose = verbose
            keyboard.probe()
            return keyboard
        except (AulaError, OSError) as exc:
            if raw is not None:
                raw.close()
            errors.append(f"{info.path}: {exc}")
    raise AulaError("could not open the AULA F87 control interface:\n  " + "\n  ".join(errors))


def format_device_line(info: DeviceInfo) -> str:
    status = info.protocol or "not a config interface"
    interface = f" interface {info.interface}" if info.interface else ""
    return f"{info.path}  {info.vid:04X}:{info.pid:04X}  {info.mode}{interface}  {status}  {info.name}"


__all__ = [
    "AulaError",
    "DeviceInfo",
    "DeviceNotFound",
    "DevicePermissionError",
    "HidrawDevice",
    "ProtocolError",
    "UnsupportedSetting",
    "VerificationError",
    "WiredKeyboard",
    "WirelessKeyboard",
    "format_device_line",
    "open_keyboard",
    "scan_devices",
]
