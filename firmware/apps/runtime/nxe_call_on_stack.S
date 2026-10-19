/* AAPCS32: caller owns an aligned private stack for the trusted module. */
.syntax unified
.arch armv5te
.arm
.section .text.nxe_call_on_stack,"ax",%progbits
.global nxe_call_on_stack
.type nxe_call_on_stack,%function
nxe_call_on_stack:
    push {r4-r11,r12,lr}
    mov r4,sp
    mov r3,r0
    mov r0,r1
    bic sp,r2,#7
    blx r3
    mov sp,r4
    pop {r4-r11,r12,pc}
.size nxe_call_on_stack,.-nxe_call_on_stack
.section .note.GNU-stack,"",%progbits
