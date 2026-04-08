package editor

import "core:fmt"
import "core:strings"

// ---------------------------------------------------------------------------
// Memory system — editor-facing API
//
// Sits above ipc.odin and provides typed procedures that the editor calls
// at key moments. Each procedure assembles the appropriate message and
// sends it over the IPC connection.
//
// The memory system is an enhancement, not a dependency. If the IPC
// connection is not active, all procedures are no-ops.
// ---------------------------------------------------------------------------

// Called after a file is written to disk. Sends the file path and content
// to the Python server, which extracts symbols and records the save event.
memory_on_file_saved :: proc(conn: ^IPC_Connection, file_path: string) {
    if !ipc_is_connected(conn) do return
    ipc_send_file_saved(conn, file_path)
}

// Called when the user requests a draft (Ctrl+D or similar).
// Sends the current buffer content and cursor position.
memory_on_draft_request :: proc(conn: ^IPC_Connection, buf: ^Buffer) {
    if !ipc_is_connected(conn) {
        fmt.printfln("[DEBUG MEMORY] draft_request skipped: IPC not connected")
        return
    }
    fmt.printfln("[DEBUG MEMORY] draft_request: path='%s', cursor_offset=%d", buf.save_path, buf.cursor.offset)
    ipc_send_draft_request(conn, buf.save_path, buf.cursor.offset)
}

// Called when the user accepts a draft from the side panel.
memory_on_draft_accept :: proc(conn: ^IPC_Connection) {
    if !ipc_is_connected(conn) do return
    ipc_send_draft_accept(conn)
}

// Called when the user dismisses a draft from the side panel.
memory_on_draft_dismiss :: proc(conn: ^IPC_Connection) {
    if !ipc_is_connected(conn) do return
    ipc_send_draft_dismiss(conn)
}

// Returns the current symbol count from the last server response.
// Used by the status bar to show "N symbols indexed".
memory_symbol_count :: proc(conn: ^IPC_Connection) -> int {
    return conn.symbol_count
}

// Returns true if the memory system is active.
memory_is_active :: proc(conn: ^IPC_Connection) -> bool {
    return ipc_is_connected(conn)
}

// Sanitize draft text before insertion into the piece table.
// Handles mixed-format output where some portions use escaped literals
// (\\n, \\t, \\") and others use real newlines. Unconditionally converts
// escape sequences since legitimate Odin code won't contain them outside
// string literals (which the LLM isn't generating correctly anyway).
sanitize_draft_text :: proc(text: string) -> (string, bool) {
    // Quick scan: if no escaped literals exist, return original
    has_escapes := false
    for i := 0; i < len(text) - 1; i += 1 {
        if text[i] == '\\' && (text[i+1] == 'n' || text[i+1] == 't' || text[i+1] == '"') {
            has_escapes = true
            break
        }
    }
    if !has_escapes do return text, false

    // Build cleaned version — works on mixed content where some parts
    // already have real newlines and others have escaped literals
    b := strings.builder_make()
    i := 0
    for i < len(text) {
        if i + 1 < len(text) && text[i] == '\\' {
            if text[i+1] == 'n'  { strings.write_byte(&b, '\n'); i += 2; continue }
            if text[i+1] == 't'  { strings.write_byte(&b, '\t'); i += 2; continue }
            if text[i+1] == '"'  { strings.write_byte(&b, '"');  i += 2; continue }
            if text[i+1] == '\\' { strings.write_byte(&b, '\\'); i += 2; continue }
        }
        strings.write_byte(&b, text[i])
        i += 1
    }
    return strings.to_string(b), true
}
