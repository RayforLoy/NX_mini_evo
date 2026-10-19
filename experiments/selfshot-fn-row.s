@ Source annotation for the fixed-address C06 SelfShot Fn tail.
@ ARM little endian, official NX mini 1.10 section 1.
@ 0x40509460: push {r1,r2,r4,r5,r6,lr} (same 24-byte frame)
@ 0x40509470: mov r0,#4 (four rows)
@ The original twelve grid-cell calls through 0x4050956c remain unchanged.
@ The manifest stores the exact assembled bytes and checked preimages.
.syntax unified
.arm
.text
@ The following starts at analysis address 0x40509570.
selfshot_flash_row:
    ldr r4, [r4, #0x20]      @ Existing CSmartPanelFlash registry entry
    mov r6, #0
1:
    mov r3, r4
    mov r2, r6
    mov r1, #3
    mov r0, r5
    bl 0x40286990           @ Native grid insertion; initializes only first span
    add r6, r6, #1
    cmp r6, #4
    blt 1b
    mov r2, #0
    movw r1, #0x373
    mov r0, r5
    bl 0x4028709c           @ Original SelfShot title
    mov r0, r5
    bl 0x40288c74           @ Original focus finalizer
    mov r0, #1
    pop {r1,r2,r4,r5,r6,pc}
    mov r0, r0
    mov r0, r0
    mov r0, r0
