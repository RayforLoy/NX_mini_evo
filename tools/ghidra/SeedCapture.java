// Repair vtable-only ARM functions used by capture settings and flash menus.
// Changes Ghidra annotations only; never firmware bytes.
// @category NXmini
import java.math.BigInteger;
import java.util.*;
import ghidra.app.script.GhidraScript;

public class SeedCapture extends GhidraScript {
    @Override public void run() throws Exception {
        long[][] ranges = {
            {0x403253f4L, 0x403254a0L}, {0x403284a8L, 0x403287b0L},
            {0x4032cad0L, 0x4032cbb8L}, {0x4032cc30L, 0x4032cd48L},
            {0x40346dd0L, 0x40346e44L}, {0x40348154L, 0x403485a8L},
            {0x403492b0L, 0x403497f0L}, {0x40354a94L, 0x4035654cL},
            {0x40442af4L, 0x40442ca8L}, {0x4053a47cL, 0x4053a490L},
            {0x406c7b00L, 0x406c8024L}, {0x406c7874L, 0x406c7948L},
            {0x4014daf0L, 0x4014deb4L}, {0x4014fa24L, 0x40150028L},
            {0x40558340L, 0x40558650L}, {0x4055a970L, 0x4055abacL},
            {0x406d3a10L, 0x406d3c2cL}, {0x40354560L, 0x40354644L},
            {0x40346b88L, 0x40346c00L}, {0x40345ba8L, 0x40345cb4L},
            {0x4027bde0L, 0x4027c0d4L}, {0x4014eb60L, 0x4014ee44L},
            {0x4014ee68L, 0x4014ee94L}, {0x4014ee98L, 0x4014f518L},
            {0x4023cb88L, 0x4023cebcL}, {0x4014f51cL, 0x4014fa20L},
            {0x40345eb0L, 0x403469c0L}, {0x403469c4L, 0x40346b84L},
            {0x40346c04L, 0x40346d40L}, {0x40346d44L, 0x40346d74L},
            {0x40346d78L, 0x40346dccL}, {0x40349924L, 0x40349b00L},
            {0x40267810L, 0x40267898L}, {0x40345b48L, 0x40345b5cL},
            {0x406c1c94L, 0x406c23dcL}, {0x406c2bc8L, 0x406c2bd8L},
            {0x406c3078L, 0x406c318cL}, {0x406c3ad0L, 0x406c3b9cL},
            {0x406c3cc0L, 0x406c3d30L}, {0x4055cc30L, 0x4055cd14L},
            {0x40444b68L, 0x40444e34L}, {0x40444ee8L, 0x40444fa8L}
        };
        Set<ghidra.program.model.address.Address> remove = new LinkedHashSet<>();
        for (long[] r : ranges) {
            var f = getFunctionContaining(toAddr(r[0]));
            if (f != null) remove.add(f.getEntryPoint());
            var it = currentProgram.getFunctionManager().getFunctions(toAddr(r[0]), true);
            while (it.hasNext()) {
                f = it.next();
                if (f.getEntryPoint().compareTo(toAddr(r[1])) > 0) break;
                remove.add(f.getEntryPoint());
            }
        }
        for (var a : remove) currentProgram.getFunctionManager().removeFunction(a);
        for (long[] r : ranges) {
            clearListing(toAddr(r[0]), toAddr(r[1]));
            currentProgram.getProgramContext().setValue(currentProgram.getRegister("TMode"),
                toAddr(r[0]), toAddr(r[1]), BigInteger.ZERO);
            disassemble(toAddr(r[0]));
        }
        for (long a = 0x40348188L; a < 0x403481fcL; a += 4) disassemble(toAddr(a));
        for (long a = 0x403492f0L; a < 0x40349360L; a += 4) disassemble(toAddr(a));
        for (long a = 0x406c1f50L; a < 0x406c1fb4L; a += 4) disassemble(toAddr(a));
        for (long a = 0x406c3ae4L; a < 0x406c3b08L; a += 4) disassemble(toAddr(a));
        for (long[] r : ranges) {
            if (getFunctionAt(toAddr(r[0])) == null) createFunction(toAddr(r[0]), null);
        }
        println("Seeded capture menu functions (ARM, static addresses only)");
    }
}
