"""Probe original 1.10 file wrappers with an explicit model filesystem backend."""
import struct

from .baseline import DECODED_SHA256
from .errors import FirmwareError
from .util import align, sha256


class NativeFileProbe:
    BASE = 0x40000000
    RAM = 0x50000000

    def __init__(self, decoded):
        if sha256(decoded) != DECODED_SHA256[1]:
            raise FirmwareError("File wrapper probe requires original 1.10 section 1")
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn.arm_const import UC_CPU_ARM_CORTEX_A9
        self.uc = Uc(UC_ARCH_ARM,UC_MODE_ARM)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
        self.uc.mem_map(self.BASE,align(len(decoded),4096))
        self.uc.mem_write(self.BASE,decoded)
        self.uc.mem_map(self.RAM,0x20000)
        self.uc.hook_add(UC_HOOK_CODE,self._hook)
        self.backend_return = 0
        self.payload = b""
        self.calls = []

    def _hook(self,uc,address,size,_):
        from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_PC,UC_ARM_REG_LR
        args = [uc.reg_read(r) for r in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3)]
        if address in (0x403d6ebe,0x403d6f42,0x403d6f06,0x403e71f8):
            self.calls.append({"address":hex(address),"arguments":args,"kind":"modelled-backend-or-log"})
            if address == 0x403d6f42:
                data = self.payload[:args[2]]
                if data: uc.mem_write(args[1],data)
                uc.mem_write(args[3],struct.pack("<I",len(data)))
            uc.reg_write(UC_ARM_REG_R0,self.backend_return & 0xffffffff)
            uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
        elif address == self.RAM:
            uc.emu_stop()
        elif not (0x4035756c <= address < 0x40357618 or 0x403577b4 <= address < 0x40357810):
            raise FirmwareError(f"Unmodelled native file wrapper dependency: {address:#x}")

    def call(self,entry,*arguments):
        from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,UC_ARM_REG_PC,UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_CPSR
        self.calls.clear()
        self.uc.reg_write(UC_ARM_REG_CPSR,0x13)
        for r,v in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),tuple(arguments)+(0,0,0,0)):
            self.uc.reg_write(r,v)
        self.uc.reg_write(UC_ARM_REG_SP,self.RAM+0x1f000)
        for i,value in enumerate(arguments[4:]):
            self.uc.mem_write(self.RAM+0x1f000+i*4,struct.pack("<I",value))
        self.uc.reg_write(UC_ARM_REG_LR,self.RAM)
        self.uc.emu_start(entry,self.RAM,count=1000,timeout=1_000_000)
        if self.uc.reg_read(UC_ARM_REG_PC) != self.RAM:
            raise FirmwareError("Native file wrapper did not return")
        return self.uc.reg_read(UC_ARM_REG_R0)

    def evidence(self):
        path = self.RAM+0x100
        buffer = self.RAM+0x200
        self.uc.mem_write(path,b"I:/NXE/apps/gold_miner.nxa\0")
        rows = []
        for backend in (7,0,-1):
            self.backend_return = backend
            result = self.call(0x4035756c,path)
            rows.append({"operation":"open-wrapper","backend_result":backend,"result":result,"calls":list(self.calls)})
        for payload,status in ((b"NXA1data",0),(b"NXA",0),(b"",-1)):
            self.payload = payload; self.backend_return = status
            self.uc.mem_write(buffer,b"\xa5"*32)
            result = self.call(0x403575f4,7,buffer,8)
            rows.append({"operation":"read-wrapper","requested_bytes":8,"model_payload_bytes":len(payload),
                         "backend_status":status,"returned_byte_count":result,"calls":list(self.calls)})
        for status in (0,-1):
            self.backend_return = status
            result = self.call(0x403577b4,7)
            rows.append({"operation":"close-wrapper","backend_status":status,"result":result,"calls":list(self.calls)})
        return {"firmware_section_sha256":DECODED_SHA256[1],"evidence":"original-wrapper-instructions-with-modelled-filesystem-backend",
                "records":rows,"camera_filesystem_executed":False,"hardware_verified":False,
                "findings":["read wrapper returns backend output byte count through r3, not the backend status",
                            "open passes 0,0 flags; both raw handle 0 and negative-open failure become wrapper result 0",
                            "close wrapper returns 1 for any nonzero handle regardless of model backend status"]}


class NativeCanvasProbe(NativeFileProbe):
    """Execute original CPU canvas writes; mutex creation is the only model call."""

    def _hook(self,uc,address,size,_):
        from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_PC,UC_ARM_REG_LR
        if address == 0x403ca390:
            self.calls.append({"address":hex(address),"kind":"modelled-mutex-creation"})
            uc.reg_write(UC_ARM_REG_R0,1)
            uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR))
        elif address == self.RAM:
            uc.emu_stop()
        elif not (0x406a07bc <= address < 0x406a08cc or 0x406a09e8 <= address < 0x406a0a8c):
            raise FirmwareError(f"Unmodelled native canvas dependency: {address:#x}")

    def evidence(self):
        obj,buffer = self.RAM+0x1000,self.RAM+0x2000
        width,height = 8,6
        self.uc.mem_write(buffer,b"\xa5"*(16+width*height*4+16))
        result=self.call(0x406a07e8,obj,width,height,buffer+16)
        metadata=struct.unpack("<5I",self.uc.mem_read(obj+4,20))
        assert result==1 and metadata==(width,height,width*4,width*height*4,buffer+16)
        self.call(0x406a0854,obj,0x12345678)
        assert self.uc.mem_read(buffer+16,width*height*4)==struct.pack("<I",0x12345678)*(width*height)
        self.call(0x406a09e8,obj,1,1,2,3,0xdeadbeef)
        pixels=struct.unpack("<48I",self.uc.mem_read(buffer+16,width*height*4))
        for y in range(height):
            for x in range(width):
                assert pixels[y*width+x]==(0xdeadbeef if 1<=x<3 and 1<=y<4 else 0x12345678)
        assert self.uc.mem_read(buffer,16)==b"\xa5"*16
        assert self.uc.mem_read(buffer+16+width*height*4,16)==b"\xa5"*16
        return {"firmware_section_sha256":DECODED_SHA256[1],
                "evidence":"original-canvas-instructions-in-model-CPU-RAM-with-modelled-mutex",
                "rtti_name":"14CGuiCanvasImpl","vtable":"0x4076fef0",
                "initialization":"0x406a07e8","clear_nonzero_color":"0x406a0854",
                "fill_rectangle":"0x406a09e8","model_dimensions":[width,height],
                "metadata":{"width_offset":"0x4","height_offset":"0x8","stride_bytes_offset":"0xc",
                            "buffer_size_offset":"0x10","buffer_pointer_offset":"0x14"},
                "cpu_pixel_store_bits":32,"raw_word_roundtrip_verified":True,
                "rectangle_geometry_and_guards_verified":True,
                "native_color_channel_order_verified":False,"native_alpha_semantics_verified":False,
                "real_display_presented":False,"buffer_ownership_verified":False,"hardware_verified":False}
