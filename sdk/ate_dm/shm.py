# -*- coding: utf-8 -*-
"""数据面：共享内存环的读写约定

为什么单独做数据面
    示波器一次抓 20 万点（float32 约 800 KB），走控制面会让 JSON 序列化、管道拷贝和控制
    指令互相排队；产线上一轮采集 5~10 次，延迟会直接顶到节拍。因此大块数据放共享内存，
    控制面只传描述符。

槽位布局（每个槽 1 MB，默认 8 个槽轮转）
    偏移 0   magic  8s   固定 b"ATEDM001"
    偏移 8   seq    u64  写入序号（覆盖检测依据）
    偏移 16  nbytes u32  有效载荷长度
    偏移 20  crc32  u32  载荷校验
    偏移 24  payload ... 原始字节（float32 / float64 / int32 / uint8）

覆盖保护
    服务端轮转复用槽位。客户端读之前先取一次头，拷贝完成后校验头部 seq 未变，
    变了就判定该块已被覆盖并抛 stale，绝不让上层拿到撕裂数据。
"""

from __future__ import annotations

import array
import struct
import zlib
from dataclasses import dataclass, field
from typing import Any, Optional

MAGIC = b"ATEDM001"
HEADER = struct.Struct("<8sQII")          # magic, seq, nbytes, crc32
HEADER_SIZE = HEADER.size                  # 24
DEFAULT_SLOTS = 8
DEFAULT_BLOCK = 1 << 20                    # 1 MB

DTYPE_MAP = {
    "f": ("f", 4),      # float32
    "d": ("d", 8),      # float64
    "i": ("i", 4),      # int32
    "l": ("l", 8),      # int64
    "B": ("B", 1),      # uint8
    "h": ("h", 2),      # int16
}
DEFAULT_DTYPE = "f"


@dataclass
class StreamBlock:
    """共享内存块描述符（服务端下发，客户端据此读取）"""
    shm: str
    offset: int
    nbytes: int
    seq: int
    dtype: str = DEFAULT_DTYPE
    shape: list = field(default_factory=list)
    crc32: int = 0
    slot: int = 0

    @classmethod
    def from_dict(cls, raw: dict) -> "StreamBlock":
        return cls(
            shm=str(raw.get("shm", "")),
            offset=int(raw.get("offset", 0)),
            nbytes=int(raw.get("nbytes", 0)),
            seq=int(raw.get("seq", 0)),
            dtype=str(raw.get("dtype") or DEFAULT_DTYPE),
            shape=list(raw.get("shape") or []),
            crc32=int(raw.get("crc32", 0)),
            slot=int(raw.get("slot", 0)),
        )


class AttachedRing:
    """客户端侧的共享内存附件（只读），支持复用与显式关闭"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._mem: Any = None

    def attach(self) -> Any:
        if self._mem is None:
            from multiprocessing import shared_memory
            self._mem = shared_memory.SharedMemory(name=self.name, create=False)
        return self._mem

    def close(self) -> None:
        if self._mem is not None:
            try:
                self._mem.close()
            finally:
                self._mem = None

    def read(self, block: StreamBlock) -> bytes:
        mem = self.attach()
        buf = mem.buf
        head = bytes(buf[block.offset:block.offset + HEADER_SIZE])
        magic, seq, nbytes, crc = HEADER.unpack(head)
        if magic != MAGIC:
            raise ValueError("共享内存块头无效（magic 不匹配）")
        if nbytes != block.nbytes:
            raise ValueError(f"块长度不一致：描述为 {block.nbytes}，实际 {nbytes}")
        data = bytes(buf[block.offset + HEADER_SIZE:block.offset + HEADER_SIZE + nbytes])
        head2 = bytes(buf[block.offset:block.offset + HEADER_SIZE])
        magic2, seq2, nbytes2, _ = HEADER.unpack(head2)
        if (seq2, nbytes2) != (seq, nbytes):
            raise BufferError("块在读取期间被覆盖，数据不可用")
        if crc and zlib.crc32(data) & 0xFFFFFFFF != crc:
            raise ValueError("块校验和不匹配，数据损坏")
        return data


class StreamData:
    """一次数据面读取的结果：原始字节 + 按 dtype 解出的数值"""

    __slots__ = ("data", "dtype", "shape", "nbytes")

    def __init__(self, data: bytes, dtype: str = DEFAULT_DTYPE, shape: Optional[list] = None):
        self.data = data
        self.dtype = dtype if dtype in DTYPE_MAP else DEFAULT_DTYPE
        self.shape = list(shape or [])
        self.nbytes = len(data)

    @property
    def count(self) -> int:
        if self.shape:
            return int(self.shape[-1])
        return len(self.data) // DTYPE_MAP[self.dtype][1]

    def values(self) -> list:
        code = DTYPE_MAP[self.dtype][0]
        arr = array.array(code)
        usable = self.nbytes - self.nbytes % arr.itemsize
        arr.frombytes(self.data[:usable])
        return arr.tolist()

    def __len__(self) -> int:
        return self.count

    def __repr__(self) -> str:      # pragma: no cover - 仅调试用
        return f"<StreamData dtype={self.dtype} n={self.count} bytes={self.nbytes}>"
