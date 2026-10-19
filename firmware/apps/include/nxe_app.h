#ifndef NXE_APP_H
#define NXE_APP_H
#include <stdint.h>

#define NXE_ABI 1u
#define NXE_WIDTH 320u
#define NXE_HEIGHT 240u
#define NXE_STATE_LIMIT 8192u

enum { NXE_INIT = 0, NXE_FRAME = 1, NXE_STOP = 2 };
enum { NXE_FIRE = 1, NXE_BOMB = 2, NXE_PAUSE = 4, NXE_BACK = 8 };
enum { NXE_CONTINUE = 0, NXE_EXIT = 1, NXE_BAD_HOST = -1 };

/* ARM EABI, little endian, soft float; all offsets are 32-bit on target.
 * The caller owns pixels, state, stack and scheduling. No firmware address is
 * embedded in applications. Buttons are logical inputs, not camera key codes.
 */
typedef struct NxeHost {
    uint32_t abi, struct_bytes;
    uint16_t *pixels;
    uint32_t width, height, stride;
    uint32_t buttons, pressed, elapsed_ms;
    void *state;
    uint32_t state_bytes, seed, event;
} NxeHost;

typedef int (*NxeEntry)(NxeHost *host);
int nxe_app_entry(NxeHost *host);
#endif
