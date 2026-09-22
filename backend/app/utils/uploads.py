"""上传文件的资源边界。"""
from __future__ import annotations

from fastapi import UploadFile


class UploadTooLargeError(ValueError):
    """文件超过服务端允许的单文件大小。"""


async def read_upload_limited(
    upload: UploadFile,
    *,
    max_bytes: int,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    """分段读取上传，超限立刻停止，避免 ``read()`` 无上限占用内存。"""
    if max_bytes < 1:
        raise ValueError("上传大小上限必须大于 0")

    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(chunk_size):
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLargeError(f"文件超过单文件大小上限（{max_bytes // (1024 * 1024)} MiB）")
        chunks.append(chunk)
    return b"".join(chunks)
