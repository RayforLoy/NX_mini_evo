// Export references to capture log strings, including interior format matches.
// @category NXmini
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import com.google.gson.GsonBuilder;
import ghidra.app.script.GhidraScript;

public class FindCaptureRefs extends GhidraScript {
    @Override public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) throw new IllegalArgumentException("output-json and strings required");
        Map<String,Object> result = new LinkedHashMap<>();
        for (int i=1; i<args.length; ++i) {
            List<Object> matches = new ArrayList<>();
            var start = currentProgram.getMinAddress();
            while (start != null) {
                var a = currentProgram.getMemory().findBytes(start, currentProgram.getMaxAddress(),
                    args[i].getBytes(StandardCharsets.US_ASCII), null, true, monitor);
                if (a == null) break;
                Set<ghidra.program.model.symbol.Reference> refs = new LinkedHashSet<>();
                for (var ref : getReferencesTo(a)) refs.add(ref);
                var prefix = a;
                for (int n=0; n<120 && prefix.compareTo(currentProgram.getMinAddress())>0; ++n) {
                    prefix=prefix.previous();
                    if (currentProgram.getMemory().getByte(prefix)==0) break;
                    for (var ref : getReferencesTo(prefix)) refs.add(ref);
                }
                List<Object> found = new ArrayList<>();
                for (var ref : refs) {
                    Map<String,Object> r = new LinkedHashMap<>();
                    r.put("from", ref.getFromAddress().toString());
                    r.put("type", ref.getReferenceType().toString());
                    var f=getFunctionContaining(ref.getFromAddress());
                    r.put("function", f == null ? null : f.getEntryPoint().toString());
                    found.add(r);
                }
                matches.add(Map.of("string_address",a.toString(),"references",found));
                start=a.next();
            }
            result.put(args[i],matches);
        }
        Files.writeString(Paths.get(args[0]),new GsonBuilder().setPrettyPrinting().create().toJson(result),StandardCharsets.UTF_8);
    }
}
