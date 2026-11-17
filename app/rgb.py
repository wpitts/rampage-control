"""Small OpenRGB SDK client for protocol versions 0 through 4.

Motherboard Aura on the Rampage V Edition 10 is an SMBus device. This client
talks to an OpenRGB server rather than poking the Super I/O directly.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass, field


MAGIC = b"ORGB"
CLIENT_PROTOCOL = 4

REQUEST_CONTROLLER_COUNT = 0
REQUEST_CONTROLLER_DATA = 1
REQUEST_PROTOCOL_VERSION = 40
SET_CLIENT_NAME = 50
UPDATE_LEDS = 1050
SET_CUSTOM_MODE = 1100


class ProtocolError(RuntimeError):
    pass


class Reader:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        if end > len(self.payload):
            raise ProtocolError("controller data ended early")
        chunk = self.payload[self.offset:end]
        self.offset = end
        return chunk

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def text(self) -> str:
        length = self.u16()
        raw = self.take(length)
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


@dataclass
class ZoneInfo:
    name: str
    leds: int


@dataclass
class DeviceInfo:
    index: int
    name: str
    vendor: str
    description: str
    led_count: int
    zones: list[ZoneInfo] = field(default_factory=list)


def _skip_mode(reader: Reader, protocol: int) -> None:
    reader.text()
    if protocol < 6:
        reader.i32()
    reader.u32()
    reader.u32()
    reader.u32()
    if protocol >= 3:
        reader.u32()
        reader.u32()
    reader.u32()
    reader.u32()
    reader.u32()
    if protocol >= 3:
        reader.u32()
    reader.u32()
    reader.u32()
    colors = reader.u16()
    reader.take(4 * colors)


def _skip_matrix(reader: Reader) -> None:
    length = reader.u16()
    if length:
        reader.take(length)


def _skip_segment(reader: Reader, protocol: int) -> None:
    reader.text()
    reader.i32()
    reader.u32()
    reader.u32()
    if protocol >= 6:
        _skip_matrix(reader)
        reader.u32()


def parse_controller_data(payload: bytes, protocol: int) -> DeviceInfo:
    reader = Reader(payload)
    declared = reader.u32()
    if declared not in {len(payload), max(0, len(payload) - 4)}:
        # Some servers report the size of the device block rather than the packet.
        if declared != len(payload) - 4:
            pass
    device_type = reader.i32()
    del device_type
    name = reader.text()
    vendor = reader.text() if protocol >= 1 else ""
    description = reader.text()
    reader.text()  # version
    reader.text()  # serial
    reader.text()  # location
    mode_count = reader.u16()
    reader.i32()
    for _ in range(mode_count):
        _skip_mode(reader, protocol)
    zone_count = reader.u16()
    zones: list[ZoneInfo] = []
    for _ in range(zone_count):
        zone_name = reader.text()
        reader.i32()
        reader.u32()
        reader.u32()
        led_count = reader.u32()
        _skip_matrix(reader)
        if protocol >= 4:
            segments = reader.u16()
            for _segment in range(segments):
                _skip_segment(reader, protocol)
        if protocol >= 5:
            reader.u32()
        if protocol >= 6:
            reader.i32()
            zone_modes = reader.u16()
            for _mode in range(zone_modes):
                _skip_mode(reader, protocol)
            reader.text()
        zones.append(ZoneInfo(zone_name, led_count))
    led_count = reader.u16()
    return DeviceInfo(
        index=-1,
        name=name,
        vendor=vendor,
        description=description,
        led_count=led_count,
        zones=zones,
    )


def _packet(device_id: int, packet_id: int, payload: bytes = b"") -> bytes:
    header = struct.pack("<4sIII", MAGIC, device_id, packet_id, len(payload))
    return header + payload


class OpenRGBClient:
    def __init__(self, host: str, port: int, timeout: float = 1.5):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self.protocol = 0
        self.devices: list[DeviceInfo] = []
        self._custom: set[int] = set()

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self.devices = []
        self._custom.clear()

    def connect(self) -> None:
        self.close()
        sock = socket.create_connection((self.host, self.port), self.timeout)
        sock.settimeout(self.timeout)
        self._socket = sock
        self._send(0, REQUEST_PROTOCOL_VERSION, struct.pack("<I", CLIENT_PROTOCOL))
        _device, packet_id, payload = self._recv_matching(REQUEST_PROTOCOL_VERSION)
        if len(payload) < 4:
            raise ProtocolError("protocol version response was empty")
        server = struct.unpack("<I", payload[:4])[0]
        self.protocol = min(CLIENT_PROTOCOL, server)
        name = b"rampage-control\x00"
        self._send(0, SET_CLIENT_NAME, name)
        self.refresh()

    def refresh(self) -> None:
        self._send(0, REQUEST_CONTROLLER_COUNT, b"")
        _device, _packet_id, payload = self._recv_matching(REQUEST_CONTROLLER_COUNT)
        if len(payload) < 4:
            raise ProtocolError("controller count response was empty")
        count = struct.unpack("<I", payload[:4])[0]
        if count > 64:
            raise ProtocolError("controller count is not plausible")
        found: list[DeviceInfo] = []
        for index in range(count):
            body = struct.pack("<I", self.protocol) if self.protocol >= 1 else b""
            self._send(index, REQUEST_CONTROLLER_DATA, body)
            _device, _packet_id, data = self._recv_matching(REQUEST_CONTROLLER_DATA)
            info = parse_controller_data(data, self.protocol)
            info.index = index
            found.append(info)
        self.devices = found
        self._custom.clear()

    def set_color(self, indexes: list[int], color: tuple[int, int, int]) -> None:
        red, green, blue = color
        packed = struct.pack("<BBBB", red & 255, green & 255, blue & 255, 0)
        for index in indexes:
            device = next((item for item in self.devices if item.index == index), None)
            if device is None or device.led_count <= 0:
                continue
            count = min(device.led_count, 1024)
            if index not in self._custom:
                self._send(index, SET_CUSTOM_MODE, b"")
                self._custom.add(index)
            colors = packed * count
            block = struct.pack("<IH", 6 + len(colors), count) + colors
            # data_size covers the whole block.
            block = struct.pack("<IH", len(block), count) + colors
            self._send(index, UPDATE_LEDS, block)

    def _send(self, device_id: int, packet_id: int, payload: bytes) -> None:
        if self._socket is None:
            raise ProtocolError("not connected")
        self._socket.sendall(_packet(device_id, packet_id, payload))

    def _recv_exact(self, size: int) -> bytes:
        if self._socket is None:
            raise ProtocolError("not connected")
        chunks = []
        remaining = size
        while remaining:
            piece = self._socket.recv(remaining)
            if not piece:
                raise ProtocolError("OpenRGB closed the connection")
            chunks.append(piece)
            remaining -= len(piece)
        return b"".join(chunks)

    def _recv_matching(self, packet_id: int) -> tuple[int, int, bytes]:
        for _ in range(8):
            header = self._recv_exact(16)
            magic, device_id, incoming, size = struct.unpack("<4sIII", header)
            if magic != MAGIC:
                raise ProtocolError("OpenRGB response magic was not ORGB")
            if size > 8_000_000:
                raise ProtocolError("OpenRGB packet is too large")
            payload = self._recv_exact(size) if size else b""
            if incoming == packet_id:
                return device_id, incoming, payload
        raise ProtocolError("OpenRGB did not answer")
