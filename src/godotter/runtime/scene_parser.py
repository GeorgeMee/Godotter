from __future__ import annotations

from dataclasses import dataclass, field
import os
import time
from pathlib import Path
import random
import re

from godotter.utils.textio import atomic_write_text_utf8, read_text_utf8


UID_ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789'
SCENE_HEADER_RE = re.compile(r'^\[gd_scene(?P<attrs>[^\]]*)\]$')
EXT_RESOURCE_RE = re.compile(r'^\[ext_resource(?P<attrs>[^\]]*)\]$')
NODE_RE = re.compile(r'^\[node(?P<attrs>.*)\]$')
CONNECTION_RE = re.compile(r'^\[connection(?P<attrs>[^\]]*)\]$')
ATTR_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^\s]+))')
PROPERTY_RE = re.compile(r'^(?P<key>[A-Za-z0-9_:/]+)\s*=\s*(?P<value>.+)$')


@dataclass(slots=True)
class SceneHeader:
    format: int
    load_steps: int | None
    uid: str | None


@dataclass(slots=True)
class SceneProperty:
    key: str
    value: str


@dataclass(slots=True)
class ExtResource:
    id: str
    resource_type: str
    path: str
    uid: str | None = None


@dataclass(slots=True)
class SceneNode:
    name: str
    node_type: str
    parent: str | None = None
    instance: str | None = None
    properties: list[SceneProperty] = field(default_factory=list)


@dataclass(slots=True)
class SceneConnection:
    signal: str
    from_node: str
    to_node: str
    method: str


@dataclass(slots=True)
class ParsedScene:
    header: SceneHeader | None
    ext_resources: list[ExtResource]
    nodes: list[SceneNode]
    connections: list[SceneConnection]


def filename_to_node_name(scene_path: str) -> str:
    stem = Path(scene_path).stem
    parts = [part for part in re.split(r'[_\-\s]+', stem) if part]
    if not parts:
        return 'Root'
    return ''.join(part[:1].upper() + part[1:] for part in parts)


def generate_uid() -> str:
    seed = int(time.time_ns()) ^ os.getpid()
    rng = random.Random(seed)
    token = ''.join(rng.choice(UID_ALPHABET) for _ in range(13))
    return f'uid://{token}'


def generate_minimal_scene(root_type: str, root_name: str, uid: str, script_path: str | None = None) -> str:
    if script_path:
        return (
            f'[gd_scene load_steps=2 format=3 uid="{uid}"]\n\n'
            f'[ext_resource type="Script" path="{script_path}" id="1_script"]\n\n'
            f'[node name="{root_name}" type="{root_type}"]\n'
            f'script = ExtResource("1_script")\n'
        )
    return f'[gd_scene format=3 uid="{uid}"]\n\n[node name="{root_name}" type="{root_type}"]\n'


def parse_scene_header(content: str) -> SceneHeader | None:
    for line in content.splitlines():
        stripped = line.strip()
        match = SCENE_HEADER_RE.match(stripped)
        if not match:
            continue
        attrs = _parse_attrs(match.group('attrs'))
        format_value = int(attrs.get('format', '3'))
        load_steps = int(attrs['load_steps']) if 'load_steps' in attrs else None
        uid = attrs.get('uid')
        return SceneHeader(format=format_value, load_steps=load_steps, uid=uid)
    return None


def parse_scene(path: Path) -> ParsedScene:
    return parse_scene_text(read_text_utf8(path))


def parse_scene_text(content: str) -> ParsedScene:
    header: SceneHeader | None = None
    ext_resources: list[ExtResource] = []
    nodes: list[SceneNode] = []
    connections: list[SceneConnection] = []
    current_node: SceneNode | None = None

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        header_match = SCENE_HEADER_RE.match(stripped)
        if header_match:
            attrs = _parse_attrs(header_match.group('attrs'))
            header = SceneHeader(
                format=int(attrs.get('format', '3')),
                load_steps=int(attrs['load_steps']) if 'load_steps' in attrs else None,
                uid=attrs.get('uid'),
            )
            current_node = None
            continue

        ext_match = EXT_RESOURCE_RE.match(stripped)
        if ext_match:
            attrs = _parse_attrs(ext_match.group('attrs'))
            ext_resources.append(
                ExtResource(
                    id=attrs.get('id', ''),
                    resource_type=attrs.get('type', ''),
                    path=attrs.get('path', ''),
                    uid=attrs.get('uid'),
                )
            )
            current_node = None
            continue

        node_match = NODE_RE.match(stripped)
        if node_match:
            attrs = _parse_attrs(node_match.group('attrs'))
            current_node = SceneNode(
                name=attrs.get('name', ''),
                node_type=attrs.get('type', ''),
                parent=attrs.get('parent'),
                instance=attrs.get('instance'),
            )
            nodes.append(current_node)
            continue

        connection_match = CONNECTION_RE.match(stripped)
        if connection_match:
            attrs = _parse_attrs(connection_match.group('attrs'))
            connections.append(
                SceneConnection(
                    signal=attrs.get('signal', ''),
                    from_node=attrs.get('from', ''),
                    to_node=attrs.get('to', ''),
                    method=attrs.get('method', ''),
                )
            )
            current_node = None
            continue

        property_match = PROPERTY_RE.match(stripped)
        if property_match and current_node is not None:
            current_node.properties.append(
                SceneProperty(
                    key=property_match.group('key'),
                    value=property_match.group('value'),
                )
            )

    return ParsedScene(header=header, ext_resources=ext_resources, nodes=nodes, connections=connections)


def atomic_write(path: Path, content: str) -> None:
    atomic_write_text_utf8(path, content)


def _parse_attrs(raw_attrs: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, quoted, bare in ATTR_RE.findall(raw_attrs):
        attrs[key] = quoted or bare
    return attrs

