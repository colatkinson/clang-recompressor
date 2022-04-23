#!/usr/bin/env python3
import asyncio
import hashlib
import logging
import lzma
import multiprocessing
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from typing import BinaryIO, cast
from urllib.parse import urlparse

import aiohttp
import zstandard

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s.%(msecs)03d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s",
        datefmt="%Y%m%d.%H%M%S",
    )


async def download(
    pool: ThreadPoolExecutor,
    session: aiohttp.ClientSession,
    url: str,
    out_file: BinaryIO,
) -> None:
    log.info("Downloading %s", url)
    loop = asyncio.get_running_loop()
    async with session.get(url, allow_redirects=True) as resp:
        async for chunk in resp.content.iter_chunked(32768):
            await loop.run_in_executor(pool, out_file.write, chunk)
    log.info("Completed %s", url)


def _verify_hash_inner(url: str, in_file: BinaryIO, ex_digest: str) -> None:
    log.info("Verifying hash for %s", url)
    real_digest = _hash_file(in_file)

    if real_digest != ex_digest:
        raise ValueError(
            f"Hash mismatch for {url} [expected={ex_digest}, actual={real_digest}]"
        )


async def verify_hash(
    pool: ThreadPoolExecutor, url: str, in_file: BinaryIO, ex_digest: str
) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(pool, _verify_hash_inner, url, in_file, ex_digest)


def _recompress_inner(in_file: BinaryIO, out_file_path: str) -> None:
    in_file.seek(0)
    decompress_ctx = lzma.LZMAFile(in_file)

    compress_ctx = zstandard.ZstdCompressor(
        threads=multiprocessing.cpu_count(), level=22
    )

    log.info("Recompressing to %s", out_file_path)
    with open(out_file_path, "wb+") as out_file:
        compress_ctx.copy_stream(decompress_ctx, out_file)


def recompress(url: str, in_file: BinaryIO, out_dir: str) -> str:
    parsed = urlparse(url)

    orig_name = os.path.basename(parsed.path)
    just_tar, _ = os.path.splitext(orig_name)
    zstd_name = just_tar + ".zst"

    os.makedirs(out_dir, exist_ok=True)
    out_file_path = os.path.join(out_dir, zstd_name)

    _recompress_inner(in_file, out_file_path)
    return out_file_path


def _hash_file(handle: BinaryIO) -> str:
    sha256 = hashlib.sha256()
    handle.seek(0)

    while True:
        data = handle.read(32768)
        if not data:
            break
        sha256.update(data)

    return sha256.hexdigest()


def _gen_hash_file_inner(in_path: str) -> None:
    with open(in_path, "rb") as f_in:
        digest = _hash_file(f_in)

    with open(in_path + ".sha256", "w+", encoding="utf8") as f_out:
        base_name = os.path.basename(in_path)
        f_out.write(f"{digest}  {base_name}\n")


async def gen_hash_file(pool: ThreadPoolExecutor, in_path: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(pool, _gen_hash_file_inner, in_path)


async def main(args: list[str]) -> None:
    if len(args) != 2:
        print("Must specify exactly one arg, the output dir", file=sys.stderr)
        sys.exit(1)

    _configure_logging()

    out_dir = args[1]

    urls = {
        "https://github.com/llvm/llvm-project/releases/download/llvmorg-14.0.0/clang+llvm-14.0.0-x86_64-linux-gnu-ubuntu-18.04.tar.xz": "61582215dafafb7b576ea30cc136be92c877ba1f1c31ddbbd372d6d65622fef5",
        "https://github.com/llvm/llvm-project/releases/download/llvmorg-13.0.0/clang+llvm-13.0.0-x86_64-linux-gnu-ubuntu-16.04.tar.xz": "76d0bf002ede7a893f69d9ad2c4e101d15a8f4186fbfe24e74856c8449acd7c1",
    }

    download_files: dict[str, BinaryIO] = {
        url: cast(BinaryIO, tempfile.TemporaryFile(mode="rb+")) for url in urls
    }

    with ExitStack() as stack:
        for tmp in download_files.values():
            stack.enter_context(tmp)

        with ThreadPoolExecutor() as pool:
            async with aiohttp.ClientSession() as session:
                download_futs = [
                    download(pool, session, url, download_files[url]) for url in urls
                ]
                await asyncio.gather(*download_futs)

                check_hash_futs = [
                    verify_hash(pool, url, download_files[url], digest)
                    for (url, digest) in urls.items()
                ]
                await asyncio.gather(*check_hash_futs)

                out_paths = [
                    recompress(url, download_files[url], out_dir) for url in urls
                ]

                gen_hash_futs = [gen_hash_file(pool, path) for path in out_paths]
                await asyncio.gather(*gen_hash_futs)

                for path in out_paths:
                    print(path)


if __name__ == "__main__":
    asyncio.run(main(sys.argv))
