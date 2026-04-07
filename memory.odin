package editor

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
    if !ipc_is_connected(conn) do return
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
