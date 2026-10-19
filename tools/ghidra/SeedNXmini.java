// Seed known ARM and Thumb functions in the decoded NX mini 1.10 section 1.
// @category NXmini
import java.math.BigInteger;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.lang.Register;

public class SeedNXmini extends GhidraScript {
    private void seed(long value, boolean thumb, String name) throws Exception {
        Address a = toAddr(value);
        Register mode = currentProgram.getRegister("TMode");
        currentProgram.getProgramContext().setValue(mode, a, a, thumb ? BigInteger.ONE : BigInteger.ZERO);
        disassemble(a);
        if (getFunctionAt(a) == null) createFunction(a, name);
        createLabel(a, name, true);
        currentProgram.getSymbolTable().addExternalEntryPoint(a);
    }
    @Override public void run() throws Exception {
        seed(0x40000000L, false, "nxmini_application_entry");
        seed(0x40091f98L, true, "nxmini_check_whole_firmware_sum32");
        seed(0x4022e200L, false, "nxmini_select_production_boot_mode");
    }
}
