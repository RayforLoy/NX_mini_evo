#ifndef NXE_LOADER_H
#define NXE_LOADER_H
#include <stdint.h>

#define NXE_HEADER_BYTES 128u
#define NXE_IMAGE_LIMIT (128u * 1024u)
#define NXE_BSS_LIMIT (64u * 1024u)
#define NXE_RELOC_LIMIT 4096u
#define NXE_STACK_LIMIT (32u * 1024u)

enum {
    NXE_OK = 0, NXE_E_HEADER = -10, NXE_E_ABI = -11,
    NXE_E_SIZE = -12, NXE_E_CRC = -13, NXE_E_PROFILE = -14,
    NXE_E_RELOC = -15, NXE_E_ARENA = -16
};

typedef struct NxeInfo {
    uint32_t image_bytes, bss_bytes, entry_offset, reloc_count;
    uint32_t state_bytes, stack_bytes;
} NxeInfo;

/* Validation performs no writes to the image arena and publishes info only
 * on success. CRC32 detects corruption; packages are trusted native code.
 */
int nxe_validate(const uint8_t *package, uint32_t bytes,
                 const uint8_t profile_sha256[32], NxeInfo *info);
int nxe_load(const uint8_t *package, uint32_t bytes,
             const uint8_t profile_sha256[32], uint8_t *arena,
             uint32_t arena_bytes, NxeInfo *info);
#endif
