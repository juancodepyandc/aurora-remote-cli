"""Validate mesh data and connected materials, independently of a generator.

This is a technical delivery gate. It does not establish visual resemblance.
"""
from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path


def validate_glb(path: Path, *, require_textures: bool = False) -> str:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError("GLB header absent")
    version, declared = struct.unpack_from("<II", data, 4)
    if version != 2 or declared != len(data):
        raise ValueError("GLB tronqué ou version inconnue")
    chunks: list[tuple[bytes, bytes]] = []
    offset = 12
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("En-tête de chunk GLB tronqué")
        length, kind = struct.unpack_from("<I4s", data, offset)
        offset += 8
        if length % 4 or offset + length > len(data):
            raise ValueError("Chunk GLB tronqué ou mal aligné")
        chunks.append((kind, data[offset:offset + length]))
        offset += length
    if not chunks or chunks[0][0] != b"JSON":
        raise ValueError("Chunk JSON GLB absent")
    if sum(kind == b"JSON" for kind, _ in chunks) != 1:
        raise ValueError("Chunks JSON GLB multiples")
    doc = json.loads(chunks[0][1].decode("utf-8"))
    if not isinstance(doc, dict) or doc.get("asset", {}).get("version") != "2.0":
        raise ValueError("Description glTF 2.0 absente")
    bins = [payload for kind, payload in chunks if kind == b"BIN\0"]
    if len(bins) != 1:
        raise ValueError("Buffer géométrique embarqué absent ou multiple")
    binary = bins[0]
    buffers = doc.get("buffers", [])
    if (len(buffers) != 1 or buffers[0].get("uri") is not None
            or not isinstance(buffers[0].get("byteLength"), int)
            or not 0 < buffers[0]["byteLength"] <= len(binary)
            or len(binary) - buffers[0]["byteLength"] > 3):
        raise ValueError("Buffer GLB incomplet ou externe")

    def entry(key, index):
        values = doc.get(key, [])
        if type(index) is not int or index < 0 or index >= len(values):
            raise ValueError(f"Référence {key} invalide")
        value = values[index]
        if not isinstance(value, dict):
            raise ValueError(f"Entrée {key} invalide")
        return value

    def view(index):
        value = entry("bufferViews", index)
        start, length = value.get("byteOffset", 0), value.get("byteLength", 0)
        if (value.get("buffer") != 0 or type(start) is not int or type(length) is not int
                or start < 0 or length <= 0 or start + length > buffers[0]["byteLength"]):
            raise ValueError("Vue du buffer hors limites")
        return value, binary[start:start + length]

    def accessor(index, expected_type):
        value = entry("accessors", index)
        if value.get("type") != expected_type or "sparse" in value:
            raise ValueError("Type d'accessor inattendu ou sparse non pris en charge")
        formats = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
        fmt = formats.get(value.get("componentType"))
        count = value.get("count", 0)
        if not fmt or type(count) is not int or count <= 0:
            raise ValueError("Accessor vide ou non pris en charge")
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[expected_type]
        unpack = struct.Struct("<" + fmt * width)
        v, payload = view(value.get("bufferView"))
        stride, start = v.get("byteStride", unpack.size), value.get("byteOffset", 0)
        if (type(stride) is not int or type(start) is not int or start < 0
                or stride < unpack.size or start + (count - 1) * stride + unpack.size > len(payload)):
            raise ValueError("Accessor hors limites")
        values = [unpack.unpack_from(payload, start + i * stride) for i in range(count)]
        if not all(math.isfinite(c) for row in values for c in row):
            raise ValueError("Coordonnées non finies")
        return values

    def image_bytes(index):
        value = entry("images", index)
        if "bufferView" in value:
            return view(value["bufferView"])[1]
        uri = value.get("uri", "")
        if not uri.startswith("data:image/") or ";base64," not in uri:
            raise ValueError("Texture externe ou absente du GLB livré")
        return base64.b64decode(uri.split(",", 1)[1], validate=True)

    scenes = doc.get("scenes", [])
    if not scenes:
        raise ValueError("Scène GLB absente")
    scene = entry("scenes", doc.get("scene", 0))
    pending = list(scene.get("nodes", []))
    reachable: set[int] = set()
    used_meshes: set[int] = set()
    while pending:
        index = pending.pop()
        if index in reachable:
            continue
        node = entry("nodes", index)
        reachable.add(index)
        pending.extend(node.get("children", []))
        if "mesh" in node:
            used_meshes.add(node["mesh"])
    if not used_meshes:
        raise ValueError("Aucun maillage dans la scène")
    triangles = 0
    for mesh_index in used_meshes:
        primitives = entry("meshes", mesh_index).get("primitives", [])
        if not primitives:
            raise ValueError("Maillage sans primitive")
        for primitive in primitives:
            if primitive.get("mode", 4) != 4:
                raise ValueError("La livraison doit contenir des triangles")
            attributes = primitive.get("attributes", {})
            positions = accessor(attributes.get("POSITION"), "VEC3")
            indices = ([row[0] for row in accessor(primitive["indices"], "SCALAR")]
                       if "indices" in primitive else list(range(len(positions))))
            if len(indices) < 3 or len(indices) % 3:
                raise ValueError("Indices de triangles invalides")
            if any(type(i) is not int or i < 0 or i >= len(positions) for i in indices):
                raise ValueError("Indice de sommet hors limites")
            nondegenerate = False
            for k in range(0, len(indices), 3):
                a, b, c = (positions[i] for i in indices[k:k + 3])
                u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
                cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
                if any(x != 0 for x in cross):
                    nondegenerate = True
                    break
            if not nondegenerate:
                raise ValueError("Maillage composé uniquement de triangles dégénérés")
            triangles += len(indices) // 3
            if require_textures:
                material = entry("materials", primitive.get("material"))
                base = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
                if not isinstance(base, dict):
                    raise ValueError("Primitive sans texture PBR de couleur")
                uv = accessor(attributes.get(f"TEXCOORD_{base.get('texCoord', 0)}"), "VEC2")
                if len(uv) != len(positions):
                    raise ValueError("UV et sommets non alignés")
                texture = entry("textures", base.get("index"))
                payload = image_bytes(texture.get("source"))
                png = (len(payload) >= 45 and payload.startswith(b"\x89PNG\r\n\x1a\n")
                       and payload[8:16] == b"\0\0\0\rIHDR"
                       and all(struct.unpack_from(">II", payload, 16))
                       and b"IEND" in payload[-12:])
                jpeg = (len(payload) >= 20 and payload.startswith(b"\xff\xd8\xff")
                        and payload.endswith(b"\xff\xd9"))
                if not (png or jpeg):
                    raise ValueError("Données de texture PNG/JPEG absentes")
    return f"Structure GLB validée · {triangles} triangles · {len(data) / 1024 ** 2:.1f} Mo. Ressemblance 3D non évaluée."
