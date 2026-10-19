"""Execute the compiled C loader and packaged ARM app in isolated model RAM.

This is not a camera/REALOS emulator. The framebuffer, clock, keys, allocation
and code mapping are supplied by this harness; no original firmware runs here.
"""
from pathlib import Path
import struct

from .app_package import AppPackage, elf_image
from .baseline import FIRMWARE_SHA256
from .errors import FirmwareError
from .util import align

LOADER = 0x10000000
PACKAGE = 0x11000000
ARENA = 0x12000000
CONTEXT = 0x13000000
PROFILE = CONTEXT + 0x100
INFO = CONTEXT + 0x200
STATE = 0x14000000
FRAME = 0x15000000
STACK = 0x16000000
STOP = 0x17000000
HEADER_INFO = struct.Struct("<6I")
HOST = struct.Struct("<13I")
GAME_FIELDS = ("magic", "mode", "level", "score", "goal", "time_ms", "phase",
               "angle", "direction", "length_q8", "attached", "swing_ms",
               "bombs", "paused", "rng", "frames", "previous_buttons")


class AppProbe:
    def __init__(self, loader_elf: bytes, *, arena=ARENA):
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_PROT_READ, UC_PROT_EXEC
        from unicorn.arm_const import UC_CPU_ARM_926
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_926)
        image,bss,entry,relocs,self.symbols = elf_image(loader_elf)
        code = AppPackage(image,bss,entry,relocs,1,8192,"loader",FIRMWARE_SHA256).relocate(LOADER)
        self.uc.mem_map(LOADER,align(len(code),4096))
        self.uc.mem_write(LOADER,code)
        self.uc.mem_protect(LOADER,align(len(code),4096),UC_PROT_READ|UC_PROT_EXEC)
        self.uc.mem_map(PACKAGE,256*1024)
        self.arena = arena
        if arena % 4096:
            raise ValueError("Probe arena must start on a model page boundary")
        self.uc.mem_map(arena,192*1024)
        self.uc.mem_map(CONTEXT,4096)
        self.uc.mem_map(STATE,8192)
        self.uc.mem_map(FRAME,align(153600+64,4096))
        self.uc.mem_map(STACK,32768)
        self.uc.mem_map(STOP,4096,UC_PROT_READ|UC_PROT_EXEC)
        self.uc.mem_write(PROFILE,bytes.fromhex(FIRMWARE_SHA256))
        self.uc.mem_write(STACK,b"\xa5"*32768)
        self.uc.mem_write(FRAME,b"\xa5"*align(153600+64,4096))
        self.previous = 0
        self.returned = False
        self.package = None
        self.stack_size = 8192

    def call(self, entry, *arguments, budget=3_000_000):
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
            UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_CPSR)
        self.uc.reg_write(UC_ARM_REG_CPSR,0x13)
        values = tuple(arguments)+(0,0,0,0)
        for register,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),values):
            self.uc.reg_write(register,value)
        sp = STACK+self.stack_size-32
        for i,value in enumerate(arguments[4:]):
            self.uc.mem_write(sp+i*4,struct.pack("<I",value))
        self.uc.reg_write(UC_ARM_REG_SP,sp)
        self.uc.reg_write(UC_ARM_REG_LR,STOP)
        self.uc.emu_start(entry,STOP,timeout=3_000_000,count=budget)
        if self.uc.reg_read(UC_ARM_REG_PC) != STOP:
            raise FirmwareError("ARM app/loader exceeded execution budget")
        result = self.uc.reg_read(UC_ARM_REG_R0)
        self.returned = True
        return result-0x100000000 if result & 0x80000000 else result

    def native_validate(self, data, *, load=False, arena_bytes=192*1024):
        if len(data) > 256*1024:
            raise ValueError("Model package buffer too small")
        self.uc.mem_write(PACKAGE,data)
        self.uc.mem_write(INFO,b"\xcc"*24)
        self.uc.mem_write(self.arena,b"\xa5"*(192*1024))
        if load:
            result = self.call(LOADER+self.symbols["nxe_load"],PACKAGE,len(data),PROFILE,
                               self.arena,arena_bytes,INFO)
        else:
            result = self.call(LOADER+self.symbols["nxe_validate"],PACKAGE,len(data),PROFILE,INFO)
        return result,bytes(self.uc.mem_read(INFO,24))

    def load(self,data,*,seed=1):
        self.package = AppPackage.read(data)
        result,info = self.native_validate(data,load=True)
        if result:
            raise FirmwareError(f"Native ARM loader rejected package: {result}")
        expected = self.package.relocate(self.arena)
        actual = bytes(self.uc.mem_read(self.arena,len(expected)))
        if actual != expected:
            raise FirmwareError("C loader and independent Python relocator disagree")
        self.stack_size = self.package.stack_bytes
        self.uc.mem_write(STATE,b"\0"*8192)
        self.seed = seed
        self.previous = 0
        self.entry = self.arena+self.package.entry_offset
        return self.frame(0,elapsed_ms=0,event=0)

    def frame(self,buttons=0,*,elapsed_ms=33,event=1):
        if self.package is None:
            raise FirmwareError("No module loaded")
        pressed = buttons & ~self.previous
        self.previous = buttons
        self.uc.mem_write(CONTEXT,HOST.pack(1,HOST.size,FRAME+32,320,240,320,
            buttons,pressed,elapsed_ms,STATE,self.package.state_bytes,self.seed,event))
        result = self.call(self.entry,CONTEXT)
        self.check_guards()
        return result

    def check_guards(self):
        if self.uc.mem_read(FRAME,32) != b"\xa5"*32 or self.uc.mem_read(FRAME+32+153600,32) != b"\xa5"*32:
            raise FirmwareError("Game wrote outside its framebuffer")
        if self.uc.mem_read(STATE+self.package.state_bytes,32) != b"\0"*32:
            raise FirmwareError("Game wrote outside its declared state")
        if self.uc.mem_read(STACK,32) != b"\xa5"*32:
            raise FirmwareError("Game exceeded its declared stack")

    def game_state(self):
        values = struct.unpack("<17I",self.uc.mem_read(STATE,68))
        state = dict(zip(GAME_FIELDS,values))
        for name in ("direction","attached"):
            if state[name] & 0x80000000:
                state[name] -= 0x100000000
        state["items"] = [dict(zip(("x","y","r","kind","active"),
            struct.unpack("<5i",self.uc.mem_read(STATE+68+i*20,20)))) for i in range(13)]
        return state

    def image(self):
        from PIL import Image
        raw = self.uc.mem_read(FRAME+32,153600)
        out = bytearray(320*240*3)
        for i,(p,) in enumerate(struct.iter_unpack("<H",raw)):
            r,g,b = (p>>11)&31,(p>>5)&63,p&31
            out[3*i:3*i+3] = bytes(((r<<3)|(r>>2),(g<<2)|(g>>4),(b<<3)|(b>>2)))
        return Image.frombytes("RGB",(320,240),bytes(out))

    @classmethod
    def from_build(cls,path: Path,*,arena=ARENA,seed=1):
        probe = cls((path/"loader.elf").read_bytes(),arena=arena)
        probe.load((path/"gold_miner.nxa").read_bytes(),seed=seed)
        return probe


class SessionProbe(AppProbe):
    """Run read -> C runtime -> loader -> private stack -> app -> present.

    Callbacks below are model adapters. They do not bind camera addresses or
    prove display/key/cache behaviour on the physical NX mini.
    """
    PLATFORM = CONTEXT+0x300
    WORKSPACE = CONTEXT+0x380
    SESSION = CONTEXT+0x400
    PATH = CONTEXT+0x500
    APP_STACK = 0x18000000
    CALLBACKS = 0x19000000

    def __init__(self,loader_elf,*,arena=ARENA):
        from unicorn import UC_HOOK_CODE, UC_PROT_READ, UC_PROT_EXEC
        super().__init__(loader_elf,arena=arena)
        self.uc.mem_map(self.APP_STACK,12288)
        self.uc.mem_write(self.APP_STACK,b"\xa5"*12288)
        self.uc.mem_map(self.CALLBACKS,4096,UC_PROT_READ|UC_PROT_EXEC)
        self.uc.hook_add(UC_HOOK_CODE,self._callback,begin=self.CALLBACKS,end=self.CALLBACKS+20)
        self.callback_log = []
        self.callback_errors = {}
        self.presented = 0
        self.released = 0

    def _callback(self,uc,address,size,_):
        from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_PC,UC_ARM_REG_LR
        names = ("read_package","claim_display","present","release_display","sync_code")
        index = (address-self.CALLBACKS)//4
        if index >= len(names): raise FirmwareError("Invalid model adapter callback")
        name = names[index]
        args = [uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)]
        self.callback_log.append({"name":name,"arguments":args,"modelled":True})
        error = self.callback_errors.get(name,0)
        if name == "read_package" and not error:
            if len(self.blob)>args[2]: error=-1
            else:
                uc.mem_write(args[1],self.blob)
                uc.mem_write(args[3],struct.pack("<I",len(self.blob)))
        elif name == "claim_display" and not error:
            uc.mem_write(args[0]+8,struct.pack("<I",FRAME+32))
        elif name == "present": self.presented+=1
        elif name == "release_display": self.released+=1
        uc.reg_write(UC_ARM_REG_R0,error & 0xffffffff)
        uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))

    def start(self,data,*,seed=1,capabilities=63):
        self.blob = data
        self.package = AppPackage.read(data)
        self.uc.mem_write(self.PLATFORM,struct.pack("<3I32s5I",1,64,capabilities,
            bytes.fromhex(FIRMWARE_SHA256),*[self.CALLBACKS+i*4 for i in range(5)]))
        self.uc.mem_write(self.WORKSPACE,struct.pack("<8I",PACKAGE,256*1024,self.arena,192*1024,
            STATE,8192,self.APP_STACK+32,8192))
        self.uc.mem_write(self.PATH,b"I:/NXE/apps/gold_miner.nxa\0")
        return self.call(LOADER+self.symbols["nxe_session_start"],self.SESSION,self.PLATFORM,
                         self.WORKSPACE,self.PATH,seed)

    def frame(self,buttons=0,*,elapsed_ms=33,event=1):
        if event == 2:
            self.call(LOADER+self.symbols["nxe_session_close"],self.SESSION)
            result = 1
        elif event != 1:
            raise ValueError("Use start() to initialize a native session")
        else:
            result = self.call(LOADER+self.symbols["nxe_session_frame"],self.SESSION,buttons,elapsed_ms)
        self.check_guards()
        if self.uc.mem_read(self.APP_STACK,32)!=b"\xa5"*32 or self.uc.mem_read(self.APP_STACK+32+8192,32)!=b"\xa5"*32:
            raise FirmwareError("Module private stack guard modified")
        return result

    @classmethod
    def from_build(cls,path: Path,*,arena=ARENA,seed=1):
        probe = cls((path/"loader.elf").read_bytes(),arena=arena)
        result = probe.start((path/"gold_miner.nxa").read_bytes(),seed=seed)
        if result: raise FirmwareError(f"Native session failed to start: {result}")
        return probe
