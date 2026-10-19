// Seed explicitly verified ARM entry addresses (for example original vtables).
// Repairs annotations only. Never discovers entries or modifies firmware bytes.
// @category NXmini
import java.math.BigInteger;
import ghidra.app.script.GhidraScript;

public class SeedArmEntries extends GhidraScript {
    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length == 0) throw new IllegalArgumentException("verified hexadecimal ARM entries required");
        for (String value : args) {
            long number = Long.parseUnsignedLong(value.replace("0x", ""), 16);
            if ((number & 3) != 0) throw new IllegalArgumentException("ARM entry must be word aligned: " + value);
            var entry = toAddr(number);
            var function = getFunctionContaining(entry);
            if (function != null && function.getEntryPoint().equals(entry)) continue;
            if (function != null) currentProgram.getFunctionManager().removeFunction(function.getEntryPoint());
            clearListing(entry, entry.add(3));
            currentProgram.getProgramContext().setValue(currentProgram.getRegister("TMode"),
                entry, entry.add(3), BigInteger.ZERO);
            disassemble(entry);
            if (createFunction(entry, null) == null) throw new IllegalStateException("Could not seed " + value);
            println("Seeded verified ARM entry " + entry);
        }
    }
}
