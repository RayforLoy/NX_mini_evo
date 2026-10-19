ENTRY(nxe_app_entry)
PHDRS { code PT_LOAD FLAGS(5); data PT_LOAD FLAGS(6); }
SECTIONS {
    . = 0;
    .text : { KEEP(*(.text.nxe_app_entry)) *(.text*) } :code
    .rodata : ALIGN(4) { *(.rodata*) } :code
    .data : ALIGN(4) { *(.data*) } :data
    . = ALIGN(8);
    __image_end = .;
    .bss (NOLOAD) : { *(.bss*) *(COMMON) } :data
    . = ALIGN(8);
    __memory_end = .;
    /DISCARD/ : { *(.comment*) *(.note*) *(.ARM.exidx*) *(.ARM.extab*) }
}
