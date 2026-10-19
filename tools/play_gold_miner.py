"""Play the packaged ARM binary on the PC, using the compiled C module loader."""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nxmini.app_probe import SessionProbe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build",type=Path,default=ROOT/"dist/nxmini-apps-A01")
    parser.add_argument("--screenshot",type=Path)
    parser.add_argument("--smoke-test",action="store_true")
    args = parser.parse_args()
    probe = SessionProbe.from_build(args.build)
    if args.screenshot:
        args.screenshot.parent.mkdir(parents=True,exist_ok=True)
        probe.image().resize((960,720),resample=0).save(args.screenshot)
        print(args.screenshot.resolve())
        return
    import tkinter as tk
    from PIL import ImageTk
    root = tk.Tk()
    root.title("NX mini evo | Gold Miner ARM preview")
    root.configure(bg="#121922")
    root.resizable(False,False)
    label = tk.Label(root,borderwidth=0)
    label.pack()
    tk.Label(root,text="Space / Enter: fire   Up: bomb   P: pause   Esc: exit  |  ARM preview on PC",
             bg="#121922",fg="#e7e5d3",padx=12,pady=9).pack()
    buttons = 0
    alive = True
    keymap = {"space":1,"Return":1,"Up":2,"p":4,"P":4,"Escape":8}
    held_keys = set()
    last = time.perf_counter()

    def key(event,down):
        nonlocal buttons
        if down: held_keys.add(event.keysym)
        else: held_keys.discard(event.keysym)
        buttons = 0
        for name in held_keys: buttons |= keymap.get(name,0)

    def blur(_):
        nonlocal buttons
        held_keys.clear(); buttons = 0

    def close():
        nonlocal alive
        alive = False
        probe.frame(event=2)
        root.destroy()

    def tick():
        nonlocal last
        if not alive: return
        now = time.perf_counter()
        elapsed = max(1,min(100,int((now-last)*1000)))
        last = now
        if probe.frame(buttons,elapsed_ms=elapsed) == 1:
            close(); return
        label.preview_image = ImageTk.PhotoImage(probe.image().resize((960,720),resample=0))
        label.configure(image=label.preview_image)
        root.after(max(1,33-int((time.perf_counter()-now)*1000)),tick)

    root.bind("<KeyPress>",lambda event:key(event,True))
    root.bind("<KeyRelease>",lambda event:key(event,False))
    root.bind("<FocusOut>",blur)
    root.protocol("WM_DELETE_WINDOW",close)
    if args.smoke_test: root.after(500,close)
    tick()
    root.mainloop()
    if args.smoke_test: print("Tk ARM preview started, rendered and closed successfully")


if __name__ == "__main__":
    main()
