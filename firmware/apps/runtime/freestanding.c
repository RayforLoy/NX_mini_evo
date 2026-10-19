#include <stdint.h>
#include <stddef.h>

void *memcpy(void *dest, const void *src, size_t n) {
    uint8_t *d = dest; const uint8_t *s = src;
    while (n--) *d++ = *s++;
    return dest;
}
void *memset(void *dest, int value, size_t n) {
    uint8_t *d = dest;
    while (n--) *d++ = (uint8_t)value;
    return dest;
}

/* ARMv5 has no integer divide instruction. Keep the module independent of
 * compiler runtime libraries and of the proprietary firmware's C library.
 */
uint32_t __aeabi_uidiv(uint32_t n, uint32_t d) {
    uint32_t q = 0, bit = 1;
    if (!d) return 0;
    while (d <= 0x7fffffffu && d < n) { d <<= 1; bit <<= 1; }
    while (bit) {
        if (n >= d) { n -= d; q |= bit; }
        d >>= 1; bit >>= 1;
    }
    return q;
}
int32_t __aeabi_idiv(int32_t n, int32_t d) {
    uint32_t a = n < 0 ? 0u - (uint32_t)n : (uint32_t)n;
    uint32_t b = d < 0 ? 0u - (uint32_t)d : (uint32_t)d;
    uint32_t q = __aeabi_uidiv(a, b);
    return (int32_t)(((n < 0) != (d < 0)) ? 0u - q : q);
}

/* EABI divmod returns quotient in r0 and remainder in r1. An unsigned
 * 64-bit scalar return has exactly this register layout on little endian ARM.
 */
uint64_t __aeabi_uidivmod(uint32_t n, uint32_t d) {
    uint32_t q = __aeabi_uidiv(n,d);
    return ((uint64_t)(n-q*d) << 32) | q;
}
uint64_t __aeabi_idivmod(int32_t n, int32_t d) {
    int32_t q = __aeabi_idiv(n,d);
    uint32_t remainder = (uint32_t)n - (uint32_t)q*(uint32_t)d;
    return ((uint64_t)remainder << 32) | (uint32_t)q;
}
