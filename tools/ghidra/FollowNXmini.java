// Seed UI functions that are reached through C++ virtual tables.
// @category NXmini
import java.math.BigInteger;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;

public class FollowNXmini extends GhidraScript {
    @Override public void run() throws Exception {
        // Automatic analysis misidentified these vtable-only ARM ranges as Thumb.
        // Only project annotations are cleared; firmware bytes are never changed.
        long[][] ranges = {{0x4055c720L, 0x4055cb00L}, {0x405021b8L, 0x40502b00L},
            {0x403f0e84L, 0x403f0ed4L}, {0x40448440L, 0x40448500L}, {0x406d4324L, 0x406d4358L},
            {0x40347fe8L, 0x40348078L}, {0x40348e9cL, 0x40348f10L},
            {0x403eea24L, 0x403ef874L}, {0x404414acL, 0x404414bcL}};
        for (long[] r : ranges) {
            var iterator = currentProgram.getFunctionManager().getFunctions(toAddr(r[0]), true);
            java.util.List<ghidra.program.model.address.Address> remove = new java.util.ArrayList<>();
            var containing = getFunctionContaining(toAddr(r[0]));
            if (containing != null) remove.add(containing.getEntryPoint());
            while (iterator.hasNext()) {
                var f = iterator.next();
                if (f.getEntryPoint().compareTo(toAddr(r[1])) > 0) break;
                remove.add(f.getEntryPoint());
            }
            for (var a : remove) currentProgram.getFunctionManager().removeFunction(a);
            clearListing(toAddr(r[0]), toAddr(r[1]));
            currentProgram.getProgramContext().setValue(currentProgram.getRegister("TMode"), toAddr(r[0]), toAddr(r[1]), BigInteger.ZERO);
        }
        long[] addresses = {0x4055c720L, 0x4055c7bcL, 0x4055c880L, 0x4055c8d8L, 0x405021b8L,
            0x403f0e84L, 0x403f0e98L, 0x403f0ea8L, 0x40448440L, 0x406d4324L,
            0x40347fe8L, 0x40348e9cL, 0x403eea24L, 0x404414acL};
        for (long value : addresses) {
            Address a = toAddr(value);
            currentProgram.getProgramContext().setValue(currentProgram.getRegister("TMode"), a, a, BigInteger.ZERO);
            disassemble(a);
            if (getFunctionAt(a) == null) createFunction(a, null);
        }
        // The mode-to-setting selector has an inline ARM branch table.
        for (long value = 0x403eea70L; value < 0x403eedc0L; value += 4) disassemble(toAddr(value));
    }
}
