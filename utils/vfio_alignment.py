import logging

log = logging.getLogger(__name__)

GiB = 1 << 30
MiB = 1 << 20
KiB = 1 << 10


def _format_out(s: int) -> str:
    if s >= GiB:
        return f"{s // GiB} GiB" if s % GiB == 0 else f"{s / GiB:.2f} GiB"
    if s >= MiB:
        return f"{s // MiB} MiB" if s % MiB == 0 else f"{s / MiB:.2f} MiB"
    if s >= KiB:
        return f"{s // KiB} KiB"
    return f"{s} B"


def _count_zero(v: int) -> int:
    n = 0
    while v and not (v & 1):
        n += 1
        v >>= 1
    return n


def _next_power_of_two(v: int) -> int:
    p = 1
    while p < v:
        p <<= 1
    return p


def check_vfio_alignment(mmap: str) -> bool:
    # Loop all mmap lines and check alignment
    log.info("================= Check BAR alignment ================")

    result = True

    for line in mmap.splitlines():
        if not line:
            continue
        line = line.strip()

        parts = line.split()
        range_str = parts[0]
        offset = int(parts[2], 16)
        backing = parts[-1]

        start_hex, end_hex = range_str.split("-")
        start = int(start_hex, 16)
        end = int(end_hex, 16)
        size = end - start
        region = offset >> 40
        align = 1 << _count_zero(start)
        expected = min(_next_power_of_two(size), GiB)

        log.info(
            f"{start_hex}-{end_hex}  Region {region}  [size={_format_out(size):>12s}]  aligned={_format_out(align):<10s} {backing}"
        )

        if align < expected:
            result = False
            log.error(
                f"Underaligned region {region}: expected {_format_out(expected)}, got {_format_out(align)}"
            )

    return result
