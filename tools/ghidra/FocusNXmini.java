// Export explicitly selected functions and their graph neighbours.
// @category NXmini
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import com.google.gson.GsonBuilder;
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.*;
import ghidra.program.model.listing.Function;

public class FocusNXmini extends GhidraScript {
    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) throw new IllegalArgumentException("output-directory and hexadecimal addresses required");
        Path dir = Paths.get(args[0]);
        Files.createDirectories(dir);
        Set<Function> selected = new LinkedHashSet<>();
        List<Object> missing = new ArrayList<>();
        for (int i = 1; i < args.length; i++) {
            var a = toAddr(Long.parseUnsignedLong(args[i].replace("0x", ""), 16));
            var f = getFunctionContaining(a);
            if (f == null) missing.add(a.toString());
            else selected.add(f);
        }
        for (Function f : new ArrayList<>(selected)) {
            for (Function called : f.getCalledFunctions(monitor)) if (selected.size() < 100) selected.add(called);
        }
        DecompInterface decompiler = new DecompInterface();
        decompiler.openProgram(currentProgram);
        List<Object> functions = new ArrayList<>();
        for (Function f : selected) {
            monitor.checkCancelled();
            Map<String,Object> row = new LinkedHashMap<>();
            row.put("entry", f.getEntryPoint().toString());
            row.put("name", f.getName());
            row.put("body", f.getBody().toString());
            row.put("callers", f.getCallingFunctions(monitor).stream().map(x -> x.getEntryPoint().toString()).sorted().toArray());
            row.put("callees", f.getCalledFunctions(monitor).stream().map(x -> x.getEntryPoint().toString()).sorted().toArray());
            var result = decompiler.decompileFunction(f, 30, monitor);
            row.put("decompiled", result.decompileCompleted());
            if (result.decompileCompleted()) Files.writeString(dir.resolve(f.getEntryPoint().toString()+".c"), result.getDecompiledFunction().getC(), StandardCharsets.UTF_8);
            else row.put("error", result.getErrorMessage());
            functions.add(row);
        }
        Map<String,Object> report = new LinkedHashMap<>();
        report.put("functions", functions);
        report.put("unresolved_addresses", missing);
        report.put("analysis_complete", false);
        report.put("runtime_mapping_verified", false);
        Files.writeString(dir.resolve("graph.json"), new GsonBuilder().setPrettyPrinting().create().toJson(report), StandardCharsets.UTF_8);
        decompiler.dispose();
        println("Focused export: "+functions.size()+" functions; "+missing.size()+" unresolved");
    }
}
