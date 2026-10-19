// Export anchor references and decompilation. All output is local research data.
// @category NXmini
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import com.google.gson.GsonBuilder;
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class ExportNXmini extends GhidraScript {
    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        Path dir = Paths.get(args[0]);
        Files.createDirectories(dir);
        String[] anchors = {
            "I:/nolens.txt", "I:/nolens_card.txt", "LM_STATUS_NO_LENS",
            "isLensDetached", "isKMountAttached", "MSG_FLASH_NX_MOUNT",
            "MODE IN:: SELFSHOT MODE", "Don't Enter SelfShot Mode",
            "FlashEnable", "Checksum(Whole F/W File)", "Checksum(Code Area)",
            "REAL OS/ITRON4", "SELF-SHOT MODE", "spear hist_self",
            "NO LENS DETECTED", "lens detached!", "nolens off", "nolens on",
            "UiSetEventDefer : [LENS] LENS_DETACHED", "onLensDetached",
            "Lens is not attached", "[APP] LENS_DETACHED"
        };
        Map<String, Object> result = new LinkedHashMap<>();
        Set<Function> functions = new LinkedHashSet<>();
        for (long a : new long[]{0x40000000L, 0x40091f98L, 0x4022e200L,
                0x4055c720L, 0x4055c7bcL, 0x4055c880L, 0x4055c8d8L, 0x405021b8L}) {
            Function f = getFunctionAt(toAddr(a));
            if (f != null) functions.add(f);
        }
        for (String anchor : anchors) {
            List<Object> matches = new ArrayList<>();
            Address start = currentProgram.getMinAddress();
            while (start != null) {
                Address a = currentProgram.getMemory().findBytes(start, currentProgram.getMaxAddress(),
                        anchor.getBytes(StandardCharsets.US_ASCII), null, true, monitor);
                if (a == null) break;
                Map<String, Object> entry = new LinkedHashMap<>();
                entry.put("address", a.toString());
                List<Object> refs = new ArrayList<>();
                Set<Reference> allRefs = new LinkedHashSet<>();
                for (Reference ref : getReferencesTo(a)) allRefs.add(ref);
                // Anchors may begin inside a format string or after an ANSI escape.
                Address prefix = a;
                for (int n = 0; n < 160 && prefix.compareTo(currentProgram.getMinAddress()) > 0; n++) {
                    prefix = prefix.previous();
                    if (currentProgram.getMemory().getByte(prefix) == 0) break;
                    for (Reference ref : getReferencesTo(prefix)) allRefs.add(ref);
                }
                for (Reference ref : allRefs) {
                    Map<String, Object> r = new LinkedHashMap<>();
                    r.put("from", ref.getFromAddress().toString());
                    r.put("type", ref.getReferenceType().toString());
                    Function f = getFunctionContaining(ref.getFromAddress());
                    if (f != null) {
                        functions.add(f);
                        r.put("function", f.getEntryPoint().toString());
                    }
                    refs.add(r);
                }
                entry.put("references", refs);
                matches.add(entry);
                start = a.next();
            }
            result.put(anchor, matches);
        }
        List<Function> selected = new ArrayList<>(functions);
        // Include direct callees of relevant functions, bounded to keep reports reviewable.
        for (Function f : selected) {
            for (Function called : f.getCalledFunctions(monitor)) {
                if (functions.size() < 90) functions.add(called);
            }
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        List<Object> exported = new ArrayList<>();
        for (Function f : functions) {
            monitor.checkCancelled();
            DecompileResults decomp = decompiler.decompileFunction(f, 45, monitor);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("name", f.getName());
            row.put("entry", f.getEntryPoint().toString());
            row.put("decompiled", decomp.decompileCompleted());
            if (decomp.decompileCompleted()) {
                Path target = dir.resolve(f.getEntryPoint().toString() + ".c");
                Files.writeString(target, decomp.getDecompiledFunction().getC(), StandardCharsets.UTF_8);
            } else row.put("error", decomp.getErrorMessage());
            exported.add(row);
        }
        result.put("functions", exported);
        result.put("function_count", currentProgram.getFunctionManager().getFunctionCount());
        result.put("program_image_base_property", currentProgram.getImageBase().toString());
        result.put("memory_start", currentProgram.getMinAddress().toString());
        result.put("analysis_complete", false);
        Files.writeString(dir.resolve("references.json"), new GsonBuilder().setPrettyPrinting().create().toJson(result), StandardCharsets.UTF_8);
        decompiler.dispose();
        println("NX mini export: " + exported.size() + " functions");
    }
}
