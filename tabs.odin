package editor

import "core:os"
import "core:strings"
import "core:path/filepath"

// ---------------------------------------------------------------------------
// Find/Replace state (per-buffer)
// ---------------------------------------------------------------------------
Find_State :: struct {
    active:          bool,
    show_replace:    bool,
    search_buf:      [256]u8,
    search_len:      int,
    replace_buf:     [256]u8,
    replace_len:     int,
    matches:         [dynamic]int,
    current_match:   int,
    focus_replace:   bool,
}

// ---------------------------------------------------------------------------
// A single editor buffer (tab). Each open file gets one of these.
// ---------------------------------------------------------------------------
Buffer :: struct {
    pt:         Piece_Table,
    undo_stack: Undo_Stack,
    cursor:     Cursor,
    scroll_y:   int,
    dirty:      bool,
    filepath:   string,   // display name
    save_path:  string,   // actual path on disk
    find:       Find_State,
    language:   Language,
    context_id: string,
    session_id: string,
}

buffer_init :: proc(buf: ^Buffer, content: string, filepath: string, save_path: string) {
    piece_table_init(&buf.pt, content)
    undo_stack_init(&buf.undo_stack)
    cursor_init(&buf.cursor)
    buf.scroll_y  = 0
    buf.dirty     = false
    buf.filepath  = strings.clone(filepath) if len(filepath) > 0 else ""
    buf.save_path = strings.clone(save_path) if len(save_path) > 0 else ""
    buf.find.active = false
    buf.find.current_match = -1
    buf.find.matches = make([dynamic]int)
    buf.language = detect_language(save_path if len(save_path) > 0 else filepath)
    buf.context_id = ""
    buf.session_id = ""
}

buffer_destroy :: proc(buf: ^Buffer) {
    piece_table_destroy(&buf.pt)
    undo_stack_destroy(&buf.undo_stack)
    delete(buf.find.matches)
    if len(buf.filepath) > 0  do delete(buf.filepath)
    if len(buf.save_path) > 0 do delete(buf.save_path)
    if len(buf.context_id) > 0 do delete(buf.context_id)
    if len(buf.session_id) > 0 do delete(buf.session_id)
}

// ---------------------------------------------------------------------------
// Tab bar management
// ---------------------------------------------------------------------------
Tab_Bar :: struct {
    tabs:       [dynamic]Buffer,
    active:     int,
}

tab_bar_init :: proc(tb: ^Tab_Bar) {
    tb.tabs   = make([dynamic]Buffer)
    tb.active = 0
}

tab_bar_destroy :: proc(tb: ^Tab_Bar) {
    for i := 0; i < len(tb.tabs); i += 1 {
        buffer_destroy(&tb.tabs[i])
    }
    delete(tb.tabs)
}

// Open a file in a new tab. If file is already open, switch to it.
tab_open_file :: proc(tb: ^Tab_Bar, filepath_arg: string) {
    // Check if already open
    for i := 0; i < len(tb.tabs); i += 1 {
        if tb.tabs[i].save_path == filepath_arg || tb.tabs[i].filepath == filepath_arg {
            tb.active = i
            return
        }
    }

    // Try to load the file
    content := ""
    save_path := ""
    display_name := filepath_arg

    // Try documents/ first, then raw path
    docs_path := filepath.join({DOCS_DIR, filepath_arg})
    data, ok := os.read_entire_file(docs_path)
    if ok {
        content = string(data)
        save_path = docs_path
    } else {
        delete(docs_path)
        data, ok = os.read_entire_file(filepath_arg)
        if ok {
            content = string(data)
            save_path = filepath_arg
        }
    }

    // Extract just the filename for display
    base := filepath.base(filepath_arg)
    if len(base) > 0 {
        display_name = base
    }

    buf: Buffer
    buffer_init(&buf, content, display_name, save_path)
    append(&tb.tabs, buf)
    tb.active = len(tb.tabs) - 1
}

// Open a new empty tab
tab_new :: proc(tb: ^Tab_Bar) {
    buf: Buffer
    buffer_init(&buf, "", "", "")
    buf.filepath = strings.clone("[new]")
    append(&tb.tabs, buf)
    tb.active = len(tb.tabs) - 1
}

// Close a tab by index. Returns false if it was the last tab.
tab_close :: proc(tb: ^Tab_Bar, idx: int) -> bool {
    if len(tb.tabs) <= 1 do return false
    if idx < 0 || idx >= len(tb.tabs) do return false

    buffer_destroy(&tb.tabs[idx])
    ordered_remove(&tb.tabs, idx)

    // Adjust active index
    if tb.active >= len(tb.tabs) {
        tb.active = len(tb.tabs) - 1
    } else if tb.active > idx {
        tb.active -= 1
    }
    return true
}

// Close the active tab. Returns false if it was the last tab.
tab_close_active :: proc(tb: ^Tab_Bar) -> bool {
    return tab_close(tb, tb.active)
}

// Get the active buffer
tab_active_buf :: proc(tb: ^Tab_Bar) -> ^Buffer {
    return &tb.tabs[tb.active]
}

// Switch to next/previous tab
tab_next :: proc(tb: ^Tab_Bar) {
    if len(tb.tabs) > 1 {
        tb.active = (tb.active + 1) % len(tb.tabs)
    }
}

tab_prev :: proc(tb: ^Tab_Bar) {
    if len(tb.tabs) > 1 {
        tb.active = (tb.active - 1 + len(tb.tabs)) % len(tb.tabs)
    }
}

// Detect language from file extension
detect_language :: proc(path: string) -> Language {
    ext := filepath.ext(path)
    if ext == ".odin" do return .Odin
    if ext == ".py" || ext == ".pyw" || ext == ".pyi" do return .Python
    return .Plain
}
