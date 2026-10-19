#ifndef NXE_RUNTIME_H
#define NXE_RUNTIME_H
#include "nxe_app.h"
#include "nxe_loader.h"

/* A camera adapter must supply all six capabilities and all callbacks.
 * The checked-in NX mini profile is deliberately unbound: native function
 * addresses, memory ownership and the UI lifecycle still require verification.
 */
#define NXE_CAPABILITIES 63u
enum { NXE_E_UNBOUND = -20, NXE_E_PLATFORM = -21, NXE_E_WORKSPACE = -22 };
typedef struct NxePlatform {
    uint32_t abi, struct_bytes, capabilities;
    uint8_t profile_sha256[32];
    int (*read_package)(const char *path, uint8_t *buffer, uint32_t capacity, uint32_t *bytes);
    int (*claim_display)(NxeHost *host);
    int (*present)(const NxeHost *host);
    void (*release_display)(void);
    int (*sync_code)(uint8_t *image, uint32_t bytes);
} NxePlatform;

typedef struct NxeWorkspace {
    uint8_t *package; uint32_t package_capacity;
    uint8_t *image; uint32_t image_capacity;
    uint8_t *state; uint32_t state_capacity;
    uint8_t *stack; uint32_t stack_capacity;
} NxeWorkspace;

typedef struct NxeSession {
    NxeHost host;
    NxeInfo info;
    const NxePlatform *platform;
    NxeEntry entry;
    uint8_t *stack;
    uint32_t active;
} NxeSession;

int nxe_call_on_stack(NxeEntry entry, NxeHost *host, uint8_t *stack_top);
int nxe_session_start(NxeSession *session, const NxePlatform *platform,
                       const NxeWorkspace *workspace, const char *path, uint32_t seed);
int nxe_session_frame(NxeSession *session, uint32_t buttons, uint32_t elapsed_ms);
void nxe_session_close(NxeSession *session);
#endif
