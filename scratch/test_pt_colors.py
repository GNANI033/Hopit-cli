import sys

# We can find how prompt_toolkit maps hex colors by inspecting Vt100_Output's color mapping or similar.
# Or we can just use the standard 256-color mapping algorithm:
# For any RGB color, we find the closest xterm-256 color.
def rgb_to_xterm(r, g, b):
    # The 256 color palette:
    # 0-7: standard colors
    # 8-15: high intensity
    # 16-231: 6x6x6 color cube (red, green, blue steps 0..5)
    #   values are 0, 95, 135, 175, 215, 255
    # 232-255: grayscale from black to white
    
    # 1. Check grayscale
    # If r, g, b are very close, it might be grayscale.
    if abs(r - g) < 8 and abs(g - b) < 8 and abs(r - b) < 8:
        # grayscale values are 8, 18, 28, ... 238
        # index is (val - 8) / 10, plus 232
        best_idx = 232
        best_diff = 10000
        for i in range(24):
            val = 8 + i * 10
            diff = abs(r - val) + abs(g - val) + abs(b - val)
            if diff < best_diff:
                best_diff = diff
                best_idx = 232 + i
        # Also compare with xterm 16 and 231
        for idx, (vr, vg, vb) in [(16, (0,0,0)), (231, (255,255,255))]:
            diff = abs(r - vr) + abs(g - vg) + abs(b - vb)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx
        return best_idx
        
    # 2. Check cube colors
    cube_vals = [0, 95, 135, 175, 215, 255]
    best_idx = 16
    best_diff = 10000
    for ir, vr in enumerate(cube_vals):
        for ig, vg in enumerate(cube_vals):
            for ib, vb in enumerate(cube_vals):
                idx = 16 + ir * 36 + ig * 6 + ib
                diff = abs(r - vr) + abs(g - vg) + abs(b - vb)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = idx
    return best_idx

def xterm_to_hex(idx):
    if idx < 16:
        return f"ansi_color_{idx}"
    if idx >= 232:
        val = 8 + (idx - 232) * 10
        return f"#{val:02x}{val:02x}{val:02x}"
    cube_vals = [0, 95, 135, 175, 215, 255]
    idx -= 16
    r = cube_vals[idx // 36]
    g = cube_vals[(idx % 36) // 6]
    b = cube_vals[idx % 6]
    return f"#{r:02x}{g:02x}{b:02x}"

tints = {
    "0% (Base)": "#b8d769",
    "10%": "#bfdb78",
    "20%": "#c6df87",
    "30%": "#cde396",
    "40%": "#d4e7a5",
    "50%": "#dcebb4",
    "60%": "#e3efc3",
    "70%": "#eaf3d2",
    "80%": "#f1f7e1",
    "90%": "#f8fbf0"
}

dark_tints = {
    "0%": "#35433f",
    "10%": "#495652",
    "20%": "#5d6965",
    "30%": "#727b79",
    "40%": "#868e8c",
    "50%": "#9aa19f",
    "60%": "#aeb4b2",
    "70%": "#c2c7c5",
    "80%": "#d7d9d9",
    "90%": "#ebecec"
}

print("Light Tints:")
for name, hex_code in tints.items():
    r = int(hex_code[1:3], 16)
    g = int(hex_code[3:5], 16)
    b = int(hex_code[5:7], 16)
    idx = rgb_to_xterm(r, g, b)
    mapped_hex = xterm_to_hex(idx)
    print(f"  {name}: {hex_code} -> xterm {idx} ({mapped_hex})")

print("\nDark Tints:")
for name, hex_code in dark_tints.items():
    r = int(hex_code[1:3], 16)
    g = int(hex_code[3:5], 16)
    b = int(hex_code[5:7], 16)
    idx = rgb_to_xterm(r, g, b)
    mapped_hex = xterm_to_hex(idx)
    print(f"  {name}: {hex_code} -> xterm {idx} ({mapped_hex})")
