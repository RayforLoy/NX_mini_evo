#include "nxe_loader.h"

static uint32_t read32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 |
           (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static void write32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
static uint32_t package_crc(const uint8_t *p, uint32_t n) {
    uint32_t crc = 0xffffffffu, i, bit;
    for (i = 0; i < n; ++i) {
        crc ^= (i >= 44 && i < 48) ? 0 : p[i];
        for (bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

int nxe_validate(const uint8_t *p, uint32_t bytes,
                 const uint8_t profile[32], NxeInfo *out) {
    NxeInfo v;
    uint32_t i, off, prev = 0, target, size;
    int ended = 0;
    if (!p || !profile || !out || bytes < NXE_HEADER_BYTES)
        return NXE_E_HEADER;
    if (p[0] != 'N' || p[1] != 'X' || p[2] != 'A' || p[3] != '1' ||
        p[4] != 1 || p[5] != 0 || p[6] != 128 || p[7] != 0)
        return NXE_E_HEADER;
    if (read32(p + 8) != 1 || read32(p + 12) != 1 || read32(p + 16) != 0)
        return NXE_E_ABI;
    v.image_bytes = read32(p + 20); v.bss_bytes = read32(p + 24);
    v.entry_offset = read32(p + 28); v.reloc_count = read32(p + 32);
    v.state_bytes = read32(p + 36); v.stack_bytes = read32(p + 40);
    if (v.image_bytes < 4 || v.image_bytes > NXE_IMAGE_LIMIT ||
        v.bss_bytes > NXE_BSS_LIMIT || v.reloc_count > NXE_RELOC_LIMIT ||
        v.state_bytes > 8192 || v.state_bytes == 0 ||
        v.stack_bytes < 1024 || v.stack_bytes > NXE_STACK_LIMIT ||
        (v.stack_bytes & 7) || (v.entry_offset & 3) ||
        v.entry_offset > v.image_bytes - 4)
        return NXE_E_SIZE;
    size = NXE_HEADER_BYTES + v.image_bytes + v.reloc_count * 4;
    if (bytes != size) return NXE_E_SIZE;
    for (i = 0; i < 32; ++i) {
        if (p[48 + i] != profile[i]) return NXE_E_PROFILE;
        if (!p[80 + i]) ended = 1;
        else if (ended || !((p[80 + i] >= 'a' && p[80 + i] <= 'z') ||
                           (p[80 + i] >= '0' && p[80 + i] <= '9') ||
                           p[80 + i] == '_' || p[80 + i] == '-'))
            return NXE_E_HEADER;
    }
    if (!ended || !p[80]) return NXE_E_HEADER;
    for (i = 112; i < 128; ++i) if (p[i]) return NXE_E_HEADER;
    if (package_crc(p, bytes) != read32(p + 44)) return NXE_E_CRC;
    for (i = 0; i < v.reloc_count; ++i) {
        off = read32(p + NXE_HEADER_BYTES + v.image_bytes + i * 4);
        if ((off & 3) || off > v.image_bytes - 4 || (i && off <= prev))
            return NXE_E_RELOC;
        target = read32(p + NXE_HEADER_BYTES + off);
        if (target >= v.image_bytes + v.bss_bytes) return NXE_E_RELOC;
        prev = off;
    }
    *out = v;
    return NXE_OK;
}

int nxe_load(const uint8_t *p, uint32_t bytes, const uint8_t profile[32],
             uint8_t *arena, uint32_t arena_bytes, NxeInfo *out) {
    NxeInfo v;
    uint32_t i, off, address = (uint32_t)(uintptr_t)arena;
    uintptr_t source = (uintptr_t)p;
    int error;
    if (!out) return NXE_E_HEADER;
    error = nxe_validate(p, bytes, profile, &v);
    if (error) return error;
    if (!arena || (address & 7) || arena_bytes < v.image_bytes + v.bss_bytes ||
        address > 0xffffffffu - v.image_bytes - v.bss_bytes ||
        source > (uintptr_t)-1 - bytes ||
        ((uintptr_t)arena < source + bytes &&
         source < (uintptr_t)arena + v.image_bytes + v.bss_bytes))
        return NXE_E_ARENA;
    for (i = 0; i < v.image_bytes; ++i) arena[i] = p[NXE_HEADER_BYTES + i];
    for (; i < v.image_bytes + v.bss_bytes; ++i) arena[i] = 0;
    for (i = 0; i < v.reloc_count; ++i) {
        off = read32(p + NXE_HEADER_BYTES + v.image_bytes + i * 4);
        write32(arena + off, read32(arena + off) + address);
    }
    *out = v;
    /* The platform MUST synchronize D/I caches before calling the entry.
     * Cache maintenance and executable memory ownership are platform APIs.
     */
    return NXE_OK;
}
