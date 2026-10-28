"""Small authenticated bridge to Apache Guacamole's native guacd protocol.

The browser uses the Apache guacamole-common-js client. The connection handshake
is server-owned, so no browser-provided host, protocol, or password is trusted.
Reference: https://guacamole.apache.org/doc/gug/guacamole-protocol.html (1.6.0).
"""
from __future__ import annotations

import asyncio


class GuacdProtocolError(Exception):
    def __init__(self, message: str, *, reason: str = "desktop_unavailable"):
        super().__init__(message)
        self.reason = reason


def instruction(opcode: str, *values: str) -> bytes:
    """Encode a Guacamole instruction (lengths count UTF-8 codepoints)."""
    pieces = (opcode, *values)
    if any(len(value) > 65536 for value in pieces):
        raise GuacdProtocolError("Guacamole element is too long")
    return (",".join(f"{len(value)}.{value}" for value in pieces) + ";").encode("utf-8")


def client_instructions(payload: str) -> list[tuple[str, list[str]]]:
    """Parse whole browser frames; never forward opaque instructions to guacd."""
    result: list[tuple[str, list[str]]] = []
    position = 0
    while position < len(payload):
        fields: list[str] = []
        while True:
            dot = payload.find(".", position, position + 8)
            if dot < 0 or not payload[position:dot].isdigit() or dot == position:
                raise GuacdProtocolError("Invalid client instruction", reason="access_denied")
            size = int(payload[position:dot])
            if size > 65536 or len(fields) >= 12:
                raise GuacdProtocolError("Instruction limit exceeded", reason="access_denied")
            position = dot + 1
            if position + size >= len(payload):
                raise GuacdProtocolError("Truncated client instruction", reason="access_denied")
            fields.append(payload[position:position + size])
            position += size
            delimiter = payload[position]
            position += 1
            if delimiter == ";":
                result.append((fields[0], fields[1:]))
                break
            if delimiter != ",":
                raise GuacdProtocolError("Invalid client delimiter", reason="access_denied")
        if len(result) > 64:
            raise GuacdProtocolError("Too many instructions", reason="access_denied")
    return result


async def read_instruction(reader: asyncio.StreamReader) -> tuple[str, list[str]]:
    """Read one bounded Guacamole instruction, including fragmented TCP frames."""
    fields: list[str] = []
    while True:
        length_buffer = await asyncio.wait_for(reader.readuntil(b"."), timeout=12)
        length_ascii = length_buffer[:-1]
        if not 1 <= len(length_ascii) <= 6 or not length_ascii.isdigit():
            raise GuacdProtocolError("Invalid Guacamole length")
        length = int(length_ascii)
        if length > 65536 or len(fields) >= 128:
            raise GuacdProtocolError("Guacamole instruction limit exceeded")
        # Handshake fields are ASCII; non-ASCII parameters are not needed here.
        raw_value = await asyncio.wait_for(reader.readexactly(length), timeout=12)
        try:
            value = raw_value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise GuacdProtocolError("Non-ASCII handshake") from exc
        fields.append(value)
        separator = await asyncio.wait_for(reader.readexactly(1), timeout=12)
        if separator == b";":
            return fields[0], fields[1:]
        if separator != b",":
            raise GuacdProtocolError("Invalid Guacamole delimiter")


async def start_guacd(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    target: dict[str, str],
    size: tuple[int, int],
) -> bytes:
    """Negotiate the target; return the WebSocket tunnel's internal UUID."""
    writer.write(instruction("select", target["protocol"]))
    await writer.drain()
    opcode, args = await read_instruction(reader)
    if opcode != "args" or len(args) > 100:
        raise GuacdProtocolError("No supported guacd protocol")
    values = {
        "hostname": target["hostname"],
        "port": target["port"],
        "username": target.get("username", ""),
        "password": target.get("password", ""),
        "domain": target.get("domain", ""),
        "security": "nla" if target["protocol"] == "rdp" else "",
        "read-only": "false" if target.get("input") == "true" else "true",
        "disable-copy": "false" if target.get("clipboard") == "true" else "true",
        "disable-paste": "false" if target.get("clipboard") == "true" else "true",
        "disable-audio": "true",
        "enable-drive": "false",
        "enable-printing": "false",
    }
    # Guacamole 1.6 announces VERSION_1_1_0 as the first argument.
    version = args[0] if args and args[0].startswith("VERSION_") else None
    parameters = args[1:] if version else args
    writer.write(instruction("size", str(size[0]), str(size[1]), "96"))
    writer.write(instruction("audio"))
    writer.write(instruction("video"))
    writer.write(instruction("image", "image/png", "image/jpeg"))
    writer.write(instruction("timezone", "Etc/UTC"))
    writer.write(instruction("connect", *([version] if version else []),
                             *(values.get(field, "") for field in parameters)))
    await writer.drain()
    opcode, args = await read_instruction(reader)
    if opcode == "error":
        # Guacamole status codes: 0x0201 unauthorized, 0x0202 forbidden.
        # Distinguish refused credentials from an unreachable transport.
        reason = "access_denied" if args and args[-1] in ("0x0201", "0x0202") else "desktop_unavailable"
        raise GuacdProtocolError("Guacd refused connection", reason=reason)
    if opcode != "ready" or len(args) != 1:
        raise GuacdProtocolError("Guacd rejected the remote desktop")
    return instruction("", *args)
