from __future__ import annotations

import unittest

from aula_f87 import protocol


BASE_CONFIG = [
    bytes.fromhex("13 44 0a 00 0e 00 03 03 02 00 00 04 04 07 00 0f 20 01 00 b6"),
    bytes.fromhex("13 44 0a 01 0e 01 00 00 00 04 00 03 02 00 ff 02 00 00 00 7b"),
    bytes.fromhex("13 44 0a 02 0e 03 00 04 01 00 00 00 00 00 00 00 00 00 00 79"),
    bytes.fromhex("13 44 0a 03 0e 00 00 00 00 00 00 00 00 00 00 00 00 00 00 72"),
    bytes.fromhex("13 44 0a 04 0e ff ff 04 37 04 07 01 07 04 37 04 37 04 37 70"),
    bytes.fromhex("13 44 0a 05 0e 04 37 04 37 04 37 04 37 04 37 04 37 04 37 11"),
    bytes.fromhex("13 44 0a 06 0e 04 37 01 07 04 37 04 37 04 37 04 37 04 37 df"),
    bytes.fromhex("13 44 0a 07 0e 07 47 07 47 07 44 07 44 07 44 07 44 07 44 89"),
    bytes.fromhex("13 44 0a 08 0e 07 44 07 44 04 04 04 04 04 04 04 04 04 04 35"),
    bytes.fromhex("13 44 0a 09 02 5a a5 00 00 00 00 00 00 00 00 00 00 00 00 6b"),
]


class WirelessProtocolTests(unittest.TestCase):
    def test_read_request(self) -> None:
        self.assertEqual(
            protocol.read_request(),
            bytes.fromhex("13 44 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 58"),
        )

    def test_every_generated_frame_has_a_valid_checksum(self) -> None:
        frames = (
            [protocol.read_request(), protocol.save_frame()]
            + protocol.palette_frames((1, 2, 3))
            + protocol.per_key_frames({0: (255, 0, 0)})
            + protocol.custom_mode_config(BASE_CONFIG)
            + protocol.factory_config_frames()
        )
        for frame in frames:
            self.assertEqual(frame[19], protocol.checksum(frame), frame.hex(" "))

    def test_update_effect_changes_configuration_and_checksum(self) -> None:
        frames = protocol.update_effect(
            BASE_CONFIG,
            effect_id=1,
            brightness=3,
            speed=0,
            colorful=False,
            uses_custom_color=True,
        )
        self.assertEqual(frames[0][1], protocol.CMD_WRITE)
        self.assertEqual(frames[0][8], 0x01)
        self.assertEqual(frames[0][14], 0x00)
        self.assertEqual(frames[0][15], 0x01)
        self.assertEqual(frames[0][17], 0x01)
        self.assertEqual(frames[4][7], 3)  # effect 1 brightness location
        self.assertEqual(frames[4][8], 0x00)
        self.assertEqual(frames[0][14], 0x00)  # normal effects clear the custom flag
        for frame in frames:
            self.assertEqual(frame[19], protocol.checksum(frame))

    def test_custom_mode_sets_oem_custom_flag(self) -> None:
        frames = protocol.custom_mode_config(BASE_CONFIG)
        self.assertEqual(frames[0][8], 0x01)
        self.assertEqual(frames[0][14], 0x01)  # config block offset 9
        self.assertEqual(frames[0][15], protocol.CUSTOM_EFFECT)
        self.assertEqual(frames[0][17], 0x01)
        for original, changed in zip(BASE_CONFIG[1:], frames[1:]):
            self.assertEqual(changed[2:19], original[2:19])
        for frame in frames:
            self.assertEqual(frame[19], protocol.checksum(frame))

    def test_palette_puts_custom_colour_in_frame_one(self) -> None:
        frames = protocol.palette_frames((0x8A, 0x5C, 0xF6))
        self.assertEqual(len(frames), protocol.PALETTE_FRAMES)
        self.assertEqual(frames[1][12:15], bytes((0x8A, 0x5C, 0xF6)))
        self.assertEqual(frames[36][4:9], bytes((0x08, 0x00, 0x00, 0x5A, 0xA5)))

    def test_per_key_planes_and_trailer(self) -> None:
        frames = protocol.per_key_frames({0: (10, 20, 30), 125: (40, 50, 60)})
        self.assertEqual(len(frames), 28)
        self.assertEqual(frames[0][4], 0x0E)
        self.assertEqual(frames[0][5], 10)   # red plane, LED 0
        self.assertEqual(frames[9][5], 20)   # green plane, LED 0
        self.assertEqual(frames[18][5], 30)  # blue plane, LED 0
        self.assertEqual(frames[8][18], 40)  # red plane, LED 125 (part 8 offset 13)
        self.assertEqual(frames[17][18], 50)
        self.assertEqual(frames[26][18], 60)
        self.assertEqual(frames[27][4], 0x06)
        self.assertEqual(frames[27][7:9], bytes((0x5A, 0xA5)))

    def test_factory_sequence_changes_first_payload_byte(self) -> None:
        frames = protocol.factory_config_frames()
        self.assertEqual(len(frames), 10)
        self.assertEqual(frames[0][1], protocol.CMD_WRITE)
        self.assertEqual(frames[0][8], 0x01)  # payload[4] shifted by 4
        self.assertEqual(frames[9][4:6], bytes((0x02, 0x5A)))


class WiredProtocolTests(unittest.TestCase):
    def test_config_write(self) -> None:
        config = bytearray(protocol.WIRED_CONFIG_SIZE)
        config[0] = 0x06
        config[8:136] = bytes(range(128))
        data = protocol.wired_config_write(config, effect_id=2, brightness=3, speed=1, colorful=True)
        self.assertEqual(len(data), protocol.WIRED_REPORT_SIZE)
        self.assertEqual(data[0], 0x06)
        self.assertEqual(data[1], 0x04)
        self.assertEqual(data[4], 0x01)
        self.assertEqual(data[6], 0x80)
        self.assertEqual(data[8:17], bytes(range(9)))
        self.assertEqual(data[19:68], bytes(range(11, 60)))
        self.assertEqual(data[70:136], bytes(range(62, 128)))
        self.assertEqual(data[18], 2)
        self.assertEqual(data[64 + 4], 3)
        self.assertEqual(data[64 + 5], 0x17)

    def test_custom_colour_fills_led_area(self) -> None:
        data = protocol.wired_custom_color((0x8A, 0x5C, 0xF6))
        self.assertEqual(data[0], 0x06)
        self.assertEqual(data[1], 0x0A)
        self.assertEqual(data[7], 0x02)
        self.assertEqual(data[29:32], bytes((0x8A, 0x5C, 0xF6)))
        self.assertEqual(data[514:516], bytes((0x5A, 0xA5)))


if __name__ == "__main__":
    unittest.main()
