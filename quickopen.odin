package editor

import rl "vendor:raylib"
import "core:fmt"
import "core:strings"
import "core:os"
import "core:path/filepath"

// ---------------------------------------------------------------------------
// Quick Open state
// ---------------------------------------------------------------------------
QUICKOPEN_MAX_RESULTS :: 15

Quick_Open :: struct {
    active:       bool,
    query_buf:    [256]u8,
    query_len:    int,
    results:      [dynamic]string,   // full paths of matching files
    selected:     int,               // index in results
    all_files:    [dynamic]string,   // cached list of all files in project
}

quick_open_init :: proc(qo: ^Quick_Open) {
    qo.active    = false
    qo.query_len = 0
    qo.selected  = 0
    qo.results   = make([dynamic]string)
    qo.all_files = make([dynamic]string)
}

quick_open_destroy :: proc(qo: ^Quick_Open) {
    for &s in qo.all_files { delete(s) }
    delete(qo.all_files)
    // results are views into all_files, don't free
    delete(qo.results)
}

// Scan the project directory for all files (recursive)
quick_open_scan :: proc(qo: ^Quick_Open, root: string) {
    for &s in qo.all_files { delete(s) }
    clear(&qo.all_files)
    scan_files_recursive(qo, root)
}

scan_files_recursive :: proc(qo: ^Quick_Open, dir: string) {
    dh, err := os.open(dir)
    if err != os.ERROR_NONE do return
    defer os.close(dh)

    infos, rerr := os.read_dir(dh, -1)
    if rerr != os.ERROR_NONE do return

    for &info in infos {
        name := filepath.base(info.fullpath)
        if len(name) > 0 && name[0] == '.' do continue
        if name == "node_modules" || name == "__pycache__" do continue

        if info.is_dir {
            scan_files_recursive(qo, info.fullpath)
        } else {
            append(&qo.all_files, strings.clone(info.fullpath))
        }
    }
}

// Open the quick open dialog
quick_open_activate :: proc(qo: ^Quick_Open, root: string) {
    qo.active = true
    qo.query_len = 0
    qo.selected = 0
    quick_open_scan(qo, root)
    quick_open_filter(qo)
}

// Filter results based on current query (fuzzy match)
quick_open_filter :: proc(qo: ^Quick_Open) {
    clear(&qo.results)
    qo.selected = 0

    query := string(qo.query_buf[:qo.query_len])

    for &fp in qo.all_files {
        if len(qo.results) >= QUICKOPEN_MAX_RESULTS do break

        if qo.query_len == 0 {
            append(&qo.results, fp)
        } else if fuzzy_match(filepath.base(fp), query) {
            append(&qo.results, fp)
        }
    }
}

// Simple fuzzy match: all query chars must appear in order in the target
fuzzy_match :: proc(target: string, query: string) -> bool {
    ti := 0
    for qi := 0; qi < len(query); qi += 1 {
        qch := to_lower(query[qi])
        found := false
        for ti < len(target) {
            if to_lower(target[ti]) == qch {
                ti += 1
                found = true
                break
            }
            ti += 1
        }
        if !found do return false
    }
    return true
}

to_lower :: proc(ch: u8) -> u8 {
    if ch >= 'A' && ch <= 'Z' do return ch + 32
    return ch
}

// Handle input for quick open. Returns selected file path or "".
quick_open_update :: proc(qo: ^Quick_Open) -> string {
    if !qo.active do return ""

    // Escape to close
    if rl.IsKeyPressed(.ESCAPE) {
        qo.active = false
        return ""
    }

    // Enter to select
    if rl.IsKeyPressed(.ENTER) {
        if qo.selected >= 0 && qo.selected < len(qo.results) {
            result := qo.results[qo.selected]
            qo.active = false
            return result
        }
        qo.active = false
        return ""
    }

    // Arrow up/down to navigate
    if rl.IsKeyPressed(.UP) || rl.IsKeyPressedRepeat(.UP) {
        if qo.selected > 0 do qo.selected -= 1
    }
    if rl.IsKeyPressed(.DOWN) || rl.IsKeyPressedRepeat(.DOWN) {
        if qo.selected < len(qo.results) - 1 do qo.selected += 1
    }

    // Backspace
    if rl.IsKeyPressed(.BACKSPACE) || rl.IsKeyPressedRepeat(.BACKSPACE) {
        if qo.query_len > 0 {
            qo.query_len -= 1
            quick_open_filter(qo)
        }
        return ""
    }

    // Character input
    for {
        ch := rl.GetCharPressed()
        if ch == 0 do break
        if ch < 32 do continue
        if qo.query_len < 255 {
            qo.query_buf[qo.query_len] = u8(ch)
            qo.query_len += 1
            quick_open_filter(qo)
        }
    }

    return ""
}

// Render the quick open overlay
quick_open_render :: proc(qo: ^Quick_Open, font: rl.Font, char_width: f32, win_w: int, win_h: int) {
    if !qo.active do return

    spacing := f32(0)

    // Dim background
    rl.DrawRectangle(0, 0, i32(win_w), i32(win_h), rl.Color{0, 0, 0, 120})

    // Dialog box
    box_w := 500
    box_x := (win_w - box_w) / 2
    box_y := 80
    item_h := 26
    box_h := 36 + len(qo.results) * item_h + 8

    rl.DrawRectangle(i32(box_x), i32(box_y), i32(box_w), i32(box_h), rl.Color{40, 40, 40, 255})
    rl.DrawRectangleLines(i32(box_x), i32(box_y), i32(box_w), i32(box_h), rl.Color{80, 80, 80, 255})

    // Query field
    query_str := string(qo.query_buf[:qo.query_len])
    field_y := box_y + 8
    prompt := "> "
    prompt_cstr := strings.clone_to_cstring(prompt, context.temp_allocator)
    rl.DrawTextEx(font, prompt_cstr, {f32(box_x + 12), f32(field_y)}, FONT_SIZE, spacing, rl.Color{100, 100, 100, 255})

    query_cstr := strings.clone_to_cstring(query_str, context.temp_allocator)
    rl.DrawTextEx(font, query_cstr, {f32(box_x + 12) + 2 * char_width, f32(field_y)}, FONT_SIZE, spacing, TEXT_COLOR)

    // Cursor
    cx := f32(box_x + 12) + f32(qo.query_len + 2) * char_width
    rl.DrawRectangle(i32(cx), i32(field_y), 2, i32(FONT_SIZE), CURSOR_COLOR)

    // Results
    for i := 0; i < len(qo.results); i += 1 {
        ry := field_y + 30 + i * item_h

        if i == qo.selected {
            rl.DrawRectangle(i32(box_x + 4), i32(ry - 2), i32(box_w - 8), i32(item_h), rl.Color{60, 80, 120, 255})
        }

        // Show just the filename, with path dimmed
        fp := qo.results[i]
        base := filepath.base(fp)
        dir := filepath.dir(fp, context.temp_allocator)

        base_cstr := strings.clone_to_cstring(base, context.temp_allocator)
        rl.DrawTextEx(font, base_cstr, {f32(box_x + 16), f32(ry)}, FONT_SIZE, spacing, TEXT_COLOR)

        // Dim path after filename
        base_w := rl.MeasureTextEx(font, base_cstr, FONT_SIZE, spacing)
        dir_cstr := strings.clone_to_cstring(dir, context.temp_allocator)
        rl.DrawTextEx(font, dir_cstr, {f32(box_x + 20) + base_w.x, f32(ry)}, FONT_SIZE, spacing, rl.Color{90, 90, 90, 255})
    }
}
