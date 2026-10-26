from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

from . import protocol
from .device import (
    AulaError,
    UnsupportedSetting,
    WiredKeyboard,
    WirelessKeyboard,
    format_device_line,
    open_keyboard,
    scan_devices,
)
from .keys import resolve_key


DESCRIPTION = "Write persistent RGB settings to an AULA F87 Pro keyboard."


def _write_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-d", "--device", help="hidraw control interface, e.g. /dev/hidraw3")
    parser.add_argument("-v", "--verbose", action="store_true", help="print every control packet")
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def _add_dry_run(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-n", "--dry-run", action="store_true", help="validate and show the action without writing")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aula-f87", description=DESCRIPTION)
    parser.add_argument("--version", action="version", version="aula-f87 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("gui", help="open the GTK lighting frontend")
    p.set_defaults(command="gui")

    p = sub.add_parser("scan", help="list AULA F87 HID interfaces")
    _add_common(p)

    p = sub.add_parser("status", aliases=["info"], help="read the onboard configuration")
    _add_common(p)
    p.add_argument("--raw", action="store_true", help="include raw configuration frames")

    p = sub.add_parser("effects", help="list effect ids and names")
    _add_common(p)

    p = sub.add_parser("effect", help="set an onboard lighting effect")
    _add_common(p)
    _add_dry_run(p)
    p.add_argument("name", help="effect name, alias, or numeric id")
    p.add_argument("--color", help="custom colour (#RRGGBB or a name)")
    p.add_argument("--spectrum", action="store_true", help="use the effect's spectrum/rainbow mode")
    p.add_argument("--brightness", type=int, help="brightness 0-4 (default: the current value)")
    p.add_argument("--speed", type=int, help="effect speed 0-4 (default: the current value)")

    p = sub.add_parser("static", help="set a fixed colour on every key")
    _add_common(p)
    _add_dry_run(p)
    p.add_argument("color", help="colour (#RRGGBB or a name)")
    p.add_argument("--brightness", type=int, default=4, help="brightness 0-4 (default: 4)")

    p = sub.add_parser("off", help="turn the keyboard lighting off")
    _add_common(p)
    _add_dry_run(p)

    p = sub.add_parser("per-key", help="write a persistent per-key colour layout")
    _add_common(p)
    _add_dry_run(p)
    p.add_argument("--file", help="JSON file with key/led to colour mappings")
    p.add_argument("--set", action="append", metavar="KEY=COLOR", help="set one key (repeatable)")
    p.add_argument("--base", help="fill every LED with this colour before applying overrides")

    p = sub.add_parser("sleep", help="set the wireless sleep timer in minutes (0-60)")
    _add_common(p)
    _add_dry_run(p)
    p.add_argument("minutes", type=int)

    p = sub.add_parser("debounce", help="set the wireless debounce time in milliseconds (1-5)")
    _add_common(p)
    _add_dry_run(p)
    p.add_argument("milliseconds", type=int)

    p = sub.add_parser("backup", help="save the current onboard config to a JSON file")
    _add_common(p)
    p.add_argument("file", help="output file, or - for stdout")

    p = sub.add_parser("restore", help="restore an onboard config backup")
    _add_common(p)
    _add_dry_run(p)
    p.add_argument("file", help="input backup file, or - for stdin")

    p = sub.add_parser("reset", help="restore factory defaults (wireless only)")
    _add_common(p)
    _add_dry_run(p)
    p.add_argument("--yes", action="store_true", help="confirm the factory reset")

    return parser


def _open(args: argparse.Namespace) -> WirelessKeyboard | WiredKeyboard:
    return open_keyboard(args.device, verbose=args.verbose)


def _print_status(summary: dict[str, Any], include_raw: bool = False) -> None:
    print(f"Device:      {summary['device']}  {summary['vid']}:{summary['pid']}  {summary['mode']}  {summary['path']}")
    print(f"Effect:      {summary['effect_id']}  {summary['effect']}")
    if summary.get("brightness") is None:
        print("Brightness:  n/a")
    else:
        print(f"Brightness:  {summary['brightness']} / 4")
    if summary.get("speed") is None:
        print("Speed:       n/a")
    else:
        print(f"Speed:       {summary['speed']} / 4")
    if summary.get("colorful") is None:
        print("Colour mode: n/a")
    else:
        print(f"Colour mode: {'Spectrum' if summary['colorful'] else 'Custom colour'}")
    if summary.get("sleep_minutes") is not None:
        print(f"Sleep:       {summary['sleep_minutes']} min (raw {summary['sleep_raw']})")
    if summary.get("debounce_ms") is not None:
        print(f"Debounce:    {summary['debounce_ms']} ms (raw {summary['debounce_raw']})")
    if include_raw:
        if "frames" in summary:
            for index, item in enumerate(summary["frames"]):  # type: ignore[index]
                print(f"Frame {index}:   {item}")
        elif "config" in summary:
            print(f"Config:      {summary['config']}")


def _current_defaults(kb: WirelessKeyboard | WiredKeyboard) -> tuple[int, int]:
    summary = kb.status()
    brightness = summary.get("brightness")
    speed = summary.get("speed")
    return (
        brightness if isinstance(brightness, int) else 4,
        speed if isinstance(speed, int) else 2,
    )


def _check_dry_run(kb: WirelessKeyboard | WiredKeyboard, action: str, args: argparse.Namespace) -> None:
    # Read the device first so a dry run still proves that the control
    # interface is reachable and permissions are correct.
    kb.read_config()
    print(f"Dry run: would {action}; no changes were written.")
    if args.json:
        _write_json({"dry_run": True, "action": action, "protocol": kb.protocol_name, "path": kb.info.path})


def _cmd_scan(args: argparse.Namespace) -> int:
    infos = scan_devices()
    if args.json:
        _write_json(
            [
                {
                    "path": info.path,
                    "vid": f"{info.vid:04X}",
                    "pid": f"{info.pid:04X}",
                    "name": info.name,
                    "mode": info.mode,
                    "protocol": info.protocol,
                    "interface": info.interface,
                }
                for info in infos
            ]
        )
    else:
        if not infos:
            print("No AULA F87 interfaces found.")
        for info in infos:
            print(format_device_line(info))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    kb = _open(args)
    try:
        summary = kb.status(include_raw=args.raw)
    finally:
        kb.close()
    if args.json:
        _write_json(summary)
    else:
        _print_status(summary, include_raw=args.raw)
    return 0


def _cmd_effects(args: argparse.Namespace) -> int:
    rows = []
    for effect in protocol.EFFECTS:
        rows.append(
            {
                "id": effect.id,
                "name": effect.name,
                "speed": effect.supports_speed,
                "color": effect.supports_color,
                "wireless": effect.supported_wireless,
                "wired": effect.supported_wired,
            }
        )
    if args.json:
        _write_json(rows)
        return 0
    print("ID  Name              Speed  Colour  Receiver  Wired")
    for effect in protocol.EFFECTS:
        print(
            f"{effect.id:>2}  {effect.name:<16}  "
            f"{'yes' if effect.supports_speed else 'no':<5}  "
            f"{'yes' if effect.supports_color else 'no':<6}  "
            f"{'yes' if effect.supported_wireless else 'no':<8}  "
            f"{'yes' if effect.supported_wired else 'no'}"
        )
    print("\nEffects not marked as supported are present in the UI metadata but are not "
          "accepted by the verified F87 firmware.  Per-key custom mode is effect 21.")
    return 0


def _validate_effect_args(
    effect: protocol.Effect,
    color: Optional[tuple[int, int, int]],
    spectrum: bool,
) -> None:
    if color is not None and spectrum:
        raise AulaError("choose either --color or --spectrum, not both")
    if color is not None and not effect.supports_color:
        raise AulaError(
            f"{effect.name} does not support a custom colour; use --spectrum for its built-in colours"
        )
    if effect.supports_color and effect.id != 0 and color is None and not spectrum:
        raise AulaError(
            f"{effect.name} supports a custom colour: pass --color #RRGGBB or --spectrum"
        )


def _cmd_effect(args: argparse.Namespace) -> int:
    effect = protocol.effect_by_name(args.name)
    if effect.id not in protocol.WIRELESS_EFFECT_IDS:
        raise UnsupportedSetting(
            f"effect {effect.name!r} is not supported by the verified F87 firmware"
        )
    color = protocol.parse_color(args.color) if args.color else None
    _validate_effect_args(effect, color, args.spectrum)
    if args.brightness is not None and not 0 <= args.brightness <= 4:
        raise AulaError("brightness must be between 0 and 4")
    if args.speed is not None and not 0 <= args.speed <= 4:
        raise AulaError("speed must be between 0 and 4")

    kb = _open(args)
    try:
        default_brightness, default_speed = _current_defaults(kb)
        brightness = args.brightness if args.brightness is not None else default_brightness
        speed = args.speed if args.speed is not None else default_speed
        if args.dry_run:
            _check_dry_run(
                kb,
                f"apply {effect.name} brightness={brightness} speed={speed}"
                f"{' spectrum' if args.spectrum or not effect.supports_color else ' custom-colour'}",
                args,
            )
            return 0
        result = kb.apply_effect(
            effect_id=effect.id,
            brightness=brightness,
            speed=speed,
            colorful=args.spectrum,
            color=color,
        )
    finally:
        kb.close()

    if args.json:
        _write_json(result)
    else:
        print(
            f"Saved {result['effect']} to the keyboard: brightness {result['brightness']}/4, "
            f"speed {result['speed']}/4, "
            f"{'spectrum' if result['colorful'] else 'custom colour'}."
        )
    return 0


def _cmd_static(args: argparse.Namespace) -> int:
    color = protocol.parse_color(args.color)
    if not 0 <= args.brightness <= 4:
        raise AulaError("brightness must be between 0 and 4")
    effect = protocol.effect_by_name("fixed")
    kb = _open(args)
    try:
        if args.dry_run:
            _check_dry_run(
                kb,
                f"apply a fixed {protocol.format_color(color)} colour at brightness {args.brightness}",
                args,
            )
            return 0
        result = kb.apply_effect(
            effect_id=effect.id,
            brightness=args.brightness,
            speed=0,
            colorful=False,
            color=color,
        )
    finally:
        kb.close()
    if args.json:
        _write_json(result)
    else:
        print(
            f"Saved a fixed {protocol.format_color(color)} colour to the keyboard "
            f"(brightness {result['brightness']}/4)."
        )
    return 0


def _cmd_off(args: argparse.Namespace) -> int:
    kb = _open(args)
    try:
        if args.dry_run:
            _check_dry_run(kb, "turn the lighting off", args)
            return 0
        result = kb.apply_effect(
            effect_id=0,
            brightness=0,
            speed=0,
            colorful=False,
            color=None,
        )
    finally:
        kb.close()
    if args.json:
        _write_json(result)
    else:
        print("Saved lighting off to the keyboard.")
    return 0


def _coerce_json_color(value: object) -> tuple[int, int, int]:
    if isinstance(value, str):
        return protocol.parse_color(value)
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return protocol.parse_color([int(part) for part in value])  # type: ignore[arg-type]
    if isinstance(value, dict) and {"r", "g", "b"} <= set(value):
        return protocol.parse_color([int(value["r"]), int(value["g"]), int(value["b"])])  # type: ignore[index]
    raise AulaError(f"invalid colour value {value!r}; use #RRGGBB or [r, g, b]")


def _load_per_key_colors(args: argparse.Namespace) -> dict[int, tuple[int, int, int]]:
    colors: dict[int, tuple[int, int, int]] = {}
    if args.base:
        base = protocol.parse_color(args.base)
        colors.update({index: base for index in range(protocol.PER_KEY_PLANES)})

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except OSError as exc:
            raise AulaError(f"cannot read {args.file}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise AulaError(f"invalid JSON in {args.file}: {exc}") from exc

        if isinstance(data, dict) and "colors" in data:
            data = data["colors"]
        if isinstance(data, list):
            for index, value in enumerate(data):
                if value is None:
                    continue
                colors[index] = _coerce_json_color(value)
        elif isinstance(data, dict):
            for key, value in data.items():
                colors[resolve_key(str(key))] = _coerce_json_color(value)
        else:
            raise AulaError("per-key JSON must be an object, a list, or {\"colors\": ...}")

    for item in args.set or []:
        key, separator, value = item.partition("=")
        if not separator:
            raise AulaError(f"invalid --set {item!r}; use KEY=COLOR")
        colors[resolve_key(key.strip())] = protocol.parse_color(value.strip())

    if not colors:
        raise AulaError("no per-key colours were given; use --file, --set, or --base")
    return colors


def _cmd_per_key(args: argparse.Namespace) -> int:
    colors = _load_per_key_colors(args)
    kb = _open(args)
    try:
        if args.dry_run:
            _check_dry_run(kb, f"write {len(colors)} per-key colours", args)
            return 0
        result = kb.apply_per_key(colors)
    finally:
        kb.close()
    if args.json:
        _write_json(result)
    else:
        print(f"Saved {result['per_key_count']} per-key colours to the keyboard (custom mode).")
    return 0


def _cmd_sleep(args: argparse.Namespace) -> int:
    if not 0 <= args.minutes <= 60:
        raise AulaError("sleep minutes must be between 0 and 60")
    kb = _open(args)
    try:
        if args.dry_run:
            _check_dry_run(kb, f"set the sleep timer to {args.minutes} minutes", args)
            return 0
        result = kb.apply_sleep(args.minutes)
    finally:
        kb.close()
    if args.json:
        _write_json(result)
    else:
        print(f"Saved a {result['sleep_minutes']} minute sleep timer to the keyboard.")
    return 0


def _cmd_debounce(args: argparse.Namespace) -> int:
    if not 1 <= args.milliseconds <= 5:
        raise AulaError("debounce must be between 1 and 5 milliseconds")
    kb = _open(args)
    try:
        if args.dry_run:
            _check_dry_run(kb, f"set debounce to {args.milliseconds} ms", args)
            return 0
        result = kb.apply_debounce(args.milliseconds)
    finally:
        kb.close()
    if args.json:
        _write_json(result)
    else:
        print(f"Saved a {result['debounce_ms']} ms debounce time to the keyboard.")
    return 0


def _cmd_backup(args: argparse.Namespace) -> int:
    kb = _open(args)
    try:
        config = kb.read_config()
        if isinstance(kb, WirelessKeyboard):
            frames = [item.hex(" ") for item in config]  # type: ignore[arg-type]
            payload: dict[str, object] = {
                "format": "aula-f87-config-v1",
                "protocol": "wireless",
                "device": {
                    "path": kb.info.path,
                    "vid": f"{kb.info.vid:04X}",
                    "pid": f"{kb.info.pid:04X}",
                    "name": kb.info.name,
                },
                "frames": frames,
            }
        else:
            payload = {
                "format": "aula-f87-config-v1",
                "protocol": "wired",
                "device": {
                    "path": kb.info.path,
                    "vid": f"{kb.info.vid:04X}",
                    "pid": f"{kb.info.pid:04X}",
                    "name": kb.info.name,
                },
                "config": config.hex(" "),  # type: ignore[union-attr]
                "note": "wired restore is not implemented in this version",
            }
    finally:
        kb.close()

    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.file == "-":
        sys.stdout.write(text)
    else:
        try:
            with open(args.file, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            raise AulaError(f"cannot write {args.file}: {exc}") from exc
        if not args.json:
            print(f"Saved configuration backup to {args.file}.")
    return 0


def _load_backup(path: str) -> list[bytes]:
    if path == "-":
        text = sys.stdin.read()
    else:
        try:
            text = open(path, "r", encoding="utf-8").read()
        except OSError as exc:
            raise AulaError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AulaError(f"invalid backup JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") != "aula-f87-config-v1":
        raise AulaError("this is not an aula-f87-config-v1 backup")
    if data.get("protocol") != "wireless":
        raise AulaError("only wireless configuration backups can be restored")
    frames = data.get("frames")
    if not isinstance(frames, list) or len(frames) != protocol.WIRELESS_CONFIG_FRAMES:
        raise AulaError("backup does not contain 10 frames")
    result: list[bytes] = []
    for index, item in enumerate(frames):
        if not isinstance(item, str):
            raise AulaError(f"backup frame {index} is not a string")
        try:
            frame = bytes.fromhex(item)
        except ValueError as exc:
            raise AulaError(f"backup frame {index} is not valid hex") from exc
        if len(frame) != 20:
            raise AulaError(f"backup frame {index} is {len(frame)} bytes, expected 20")
        result.append(frame)
    return result


def _cmd_restore(args: argparse.Namespace) -> int:
    frames = _load_backup(args.file)
    kb = _open(args)
    try:
        if not isinstance(kb, WirelessKeyboard):
            raise UnsupportedSetting("configuration restore is available only on the 2.4 GHz receiver")
        if args.dry_run:
            _check_dry_run(kb, "restore the saved configuration", args)
            return 0
        result = kb.restore_config(frames)
    finally:
        kb.close()
    if args.json:
        _write_json(result)
    else:
        print(f"Restored the saved configuration to the keyboard ({result['effect']}).")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes and not args.dry_run:
        raise AulaError("factory reset requires --yes")
    kb = _open(args)
    try:
        if args.dry_run:
            _check_dry_run(kb, "restore factory defaults", args)
            return 0
        result = kb.factory_reset()
    finally:
        kb.close()
    if args.json:
        _write_json(result)
    else:
        print("Restored factory defaults to the keyboard.")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "gui":
            from .gui import main as gui_main

            return gui_main([])
        if args.command == "scan":
            return _cmd_scan(args)
        if args.command in {"status", "info"}:
            return _cmd_status(args)
        if args.command == "effects":
            return _cmd_effects(args)
        if args.command == "effect":
            return _cmd_effect(args)
        if args.command == "static":
            return _cmd_static(args)
        if args.command == "off":
            return _cmd_off(args)
        if args.command == "per-key":
            return _cmd_per_key(args)
        if args.command == "sleep":
            return _cmd_sleep(args)
        if args.command == "debounce":
            return _cmd_debounce(args)
        if args.command == "backup":
            return _cmd_backup(args)
        if args.command == "restore":
            return _cmd_restore(args)
        if args.command == "reset":
            return _cmd_reset(args)
        parser.error(f"unknown command {args.command}")
    except (AulaError, ValueError) as exc:
        print(f"aula-f87: error: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
