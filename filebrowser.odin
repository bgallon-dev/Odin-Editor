package editor

import rl "vendor:raylib"
import "core:os"
import "core:fmt"
import "core:strings"
import "core:path/filepath"

// File browser config values are in cfg (see config.odin / editor.conf)

// ---------------------------------------------------------------------------
// File tree entry
// ---------------------------------------------------------------------------
File_Entry :: struct {
    name:      string,
    full_path: string,
    is_dir:    bool,
    depth:     int,
    expanded:  bool,
    children:  [dynamic]File_Entry,
}

File_Browser :: struct {
    visible:    bool,
    root:       File_Entry,
    scroll_y:   int,
    root_path:  string,
}

file_browser_init :: proc(fb: ^File_Browser, root_path: string) {
    fb.visible   = false
    fb.scroll_y  = 0
    fb.root_path = strings.clone(root_path)
    // Don't scan yet — scan lazily when first toggled visible
    fb.root.name      = strings.clone(".")
    fb.root.full_path = strings.clone(root_path)
    fb.root.is_dir    = true
    fb.root.depth     = 0
    fb.root.expanded  = false
    fb.root.children  = make([dynamic]File_Entry)
}

file_browser_destroy :: proc(fb: ^File_Browser) {
    free_entry(&fb.root)
    if len(fb.root_path) > 0 do delete(fb.root_path)
}

free_entry :: proc(entry: ^File_Entry) {
    for i := 0; i < len(entry.children); i += 1 {
        free_entry(&entry.children[i])
    }
    delete(entry.children)
    if len(entry.name) > 0      do delete(entry.name)
    if len(entry.full_path) > 0 do delete(entry.full_path)
}

// ---------------------------------------------------------------------------
// Scan a directory (non-recursive initially; expand on click)
// ---------------------------------------------------------------------------
scan_directory :: proc(path: string, depth: int) -> File_Entry {
    entry: File_Entry
    base := filepath.base(path)
    entry.name      = strings.clone(base) if len(base) > 0 else strings.clone(path)
    entry.full_path = strings.clone(path)
    entry.is_dir    = true
    entry.depth     = depth
    entry.expanded  = false
    entry.children  = make([dynamic]File_Entry)

    // Read directory contents
    dh, err := os.open(path)
    if err != os.ERROR_NONE do return entry
    defer os.close(dh)

    infos, rerr := os.read_dir(dh, -1)
    if rerr != os.ERROR_NONE do return entry

    // Sort: directories first, then files, alphabetical
    // Simple insertion sort is fine for directory listings
    dirs:  [dynamic]os.File_Info
    files: [dynamic]os.File_Info
    defer delete(dirs)
    defer delete(files)

    for &info in infos {
        name := filepath.base(info.fullpath)
        // Skip hidden files and common noise
        if len(name) > 0 && name[0] == '.' do continue
        if name == "node_modules" || name == "__pycache__" do continue

        if info.is_dir {
            append(&dirs, info)
        } else {
            append(&files, info)
        }
    }

    for &d in dirs {
        child := scan_directory(d.fullpath, depth + 1)
        append(&entry.children, child)
    }

    for &f in files {
        child: File_Entry
        child.name      = strings.clone(filepath.base(f.fullpath))
        child.full_path = strings.clone(f.fullpath)
        child.is_dir    = false
        child.depth     = depth + 1
        child.expanded  = false
        child.children  = make([dynamic]File_Entry)
        append(&entry.children, child)
    }

    return entry
}

// ---------------------------------------------------------------------------
// Refresh a directory entry (re-scan its children)
// ---------------------------------------------------------------------------
refresh_entry :: proc(entry: ^File_Entry) {
    // Free old children
    for i := 0; i < len(entry.children); i += 1 {
        free_entry(&entry.children[i])
    }
    clear(&entry.children)

    // Re-scan
    dh, err := os.open(entry.full_path)
    if err != os.ERROR_NONE do return
    defer os.close(dh)

    infos, rerr := os.read_dir(dh, -1)
    if rerr != os.ERROR_NONE do return

    dirs:  [dynamic]os.File_Info
    files: [dynamic]os.File_Info
    defer delete(dirs)
    defer delete(files)

    for &info in infos {
        name := filepath.base(info.fullpath)
        if len(name) > 0 && name[0] == '.' do continue
        if name == "node_modules" || name == "__pycache__" do continue
        if info.is_dir {
            append(&dirs, info)
        } else {
            append(&files, info)
        }
    }

    for &d in dirs {
        child := scan_directory(d.fullpath, entry.depth + 1)
        append(&entry.children, child)
    }

    for &f in files {
        child: File_Entry
        child.name      = strings.clone(filepath.base(f.fullpath))
        child.full_path = strings.clone(f.fullpath)
        child.is_dir    = false
        child.depth     = entry.depth + 1
        child.expanded  = false
        child.children  = make([dynamic]File_Entry)
        append(&entry.children, child)
    }
}

// ---------------------------------------------------------------------------
// Render the file browser. Returns the path of a clicked file, or empty string.
// ---------------------------------------------------------------------------
file_browser_render :: proc(fb: ^File_Browser, font: rl.Font, win_h: int) -> string {
    if !fb.visible do return ""

    spacing := f32(0)
    rl.DrawRectangle(0, 0, i32(cfg.sidebar_w), i32(win_h), cfg.sidebar_bg)
    rl.DrawRectangle(i32(cfg.sidebar_w) - 1, 0, 1, i32(win_h), cfg.sidebar_border)

    mx := int(rl.GetMouseX())
    my := int(rl.GetMouseY())
    clicked := rl.IsMouseButtonPressed(.LEFT) && mx < cfg.sidebar_w

    // Flatten visible entries for rendering
    clicked_path := ""
    y_pos := 4 - fb.scroll_y * cfg.fb_line_h
    file_browser_render_entry(fb, &fb.root, font, spacing, &y_pos, win_h, mx, my, clicked, &clicked_path)

    // Scroll
    if mx < cfg.sidebar_w {
        wheel := rl.GetMouseWheelMove()
        if wheel != 0 {
            fb.scroll_y -= int(wheel * 3)
            if fb.scroll_y < 0 do fb.scroll_y = 0
        }
    }

    return clicked_path
}

file_browser_render_entry :: proc(fb: ^File_Browser, entry: ^File_Entry, font: rl.Font,
                                    spacing: f32, y_pos: ^int, win_h: int,
                                    mx: int, my: int, clicked: bool, clicked_path: ^string) {
    if y_pos^ >= -cfg.fb_line_h && y_pos^ < win_h {
        x := 8 + entry.depth * cfg.fb_indent
        fy := f32(y_pos^)

        // Hover highlight
        if mx >= 0 && mx < cfg.sidebar_w && my >= y_pos^ && my < y_pos^ + cfg.fb_line_h {
            rl.DrawRectangle(0, i32(fy), i32(cfg.sidebar_w), i32(cfg.fb_line_h), cfg.sidebar_hover)

            if clicked {
                if entry.is_dir {
                    entry.expanded = !entry.expanded
                    if entry.expanded && len(entry.children) == 0 {
                        refresh_entry(entry)
                    }
                } else {
                    clicked_path^ = entry.full_path
                }
            }
        }

        // Icon prefix
        prefix := "  " if !entry.is_dir else ("v " if entry.expanded else "> ")
        color := cfg.sidebar_dir_clr if entry.is_dir else cfg.sidebar_text

        display_buf: [128]u8
        display := fmt.bprintf(display_buf[:], "%s%s", prefix, entry.name)
        display_cstr := strings.clone_to_cstring(display, context.temp_allocator)
        rl.DrawTextEx(font, display_cstr, {f32(x), fy + 2}, f32(cfg.font_size - 2), spacing, color)
    }

    y_pos^ += cfg.fb_line_h

    if entry.expanded {
        for i := 0; i < len(entry.children); i += 1 {
            file_browser_render_entry(fb, &entry.children[i], font, spacing, y_pos, win_h, mx, my, clicked, clicked_path)
        }
    }
}
