package editor

import "core:net"
import "core:os"
import "core:fmt"
import "core:strings"
import "core:time"
import "core:path/filepath"
import win32 "core:sys/windows"

// ---------------------------------------------------------------------------
// IPC message types
// ---------------------------------------------------------------------------
IPC_Message_Kind :: enum u8 {
    Draft_Request,
    Draft_Response,
    Validate_Response,
    Status_Update,
}

IPC_Status :: enum u8 {
    Disconnected,
    Connecting,
    Connected,
    Error,
}

Issue_Severity :: enum u8 {
    Info,
    Warning,
    Error,
}

// ---------------------------------------------------------------------------
// IPC data structures
// ---------------------------------------------------------------------------
Draft_Request :: struct {
    context_id:     string,
    session_id:     string,
    buffer_content: string,
    cursor_offset:  int,
    language:       Language,
}

Draft_Response :: struct {
    context_id:   string,
    session_id:   string,
    draft_text:   string,
    confidence:   f32,
    issues:       [dynamic]Validation_Issue,
}

Validation_Issue :: struct {
    line:     int,
    col:      int,
    end_line: int,
    end_col:  int,
    severity: Issue_Severity,
    message:  string,
}

Validate_Response :: struct {
    context_id: string,
    session_id: string,
    issues:     [dynamic]Validation_Issue,
}

Status_Update :: struct {
    context_id: string,
    session_id: string,
    status:     string,
}

IPC_Message :: struct {
    kind: IPC_Message_Kind,
    data: union {
        Draft_Response,
        Validate_Response,
        Status_Update,
    },
}

RECV_BUF_INITIAL :: 65536
RECV_BUF_CHUNK   :: 8192

IPC_Connection :: struct {
    status:        IPC_Status,
    socket:        net.TCP_Socket,
    host:          string,
    port:          int,
    recv_buf:      [dynamic]u8,
    recv_len:      int,
    last_error:    string,
    session_id:    int,
    symbol_count:  int,
    project_root:  string,
    server_process: win32.HANDLE,
}

// ---------------------------------------------------------------------------
// IPC lifecycle
// ---------------------------------------------------------------------------
ipc_init :: proc(conn: ^IPC_Connection, host: string, port: int) {
    conn.status = .Disconnected
    conn.host = host
    conn.port = port
    conn.recv_buf = make([dynamic]u8, RECV_BUF_INITIAL)
    conn.recv_len = 0
    conn.last_error = ""
    conn.session_id = -1
    conn.symbol_count = 0
    conn.project_root = ""
    conn.server_process = nil
}

ipc_destroy :: proc(conn: ^IPC_Connection) {
    if conn.status == .Connected {
        // Send session_end before disconnecting
        ipc_send_session_end(conn)
        ipc_disconnect(conn)
    }
    delete(conn.recv_buf)
}

// ---------------------------------------------------------------------------
// Detect project root — walk up looking for .kettle or .git
// ---------------------------------------------------------------------------
detect_project_root :: proc() -> string {
    cwd := os.get_current_directory()
    dir := cwd

    for {
        // Check for .kettle directory
        kettle_path := filepath.join({dir, ".kettle"})
        if os.exists(kettle_path) {
            delete(kettle_path)
            return dir
        }
        delete(kettle_path)

        // Check for .git directory
        git_path := filepath.join({dir, ".git"})
        if os.exists(git_path) {
            delete(git_path)
            return dir
        }
        delete(git_path)

        // Move up one directory
        parent := filepath.dir(dir)
        if parent == dir do break  // reached root
        dir = parent
    }

    // Fall back to cwd and create .kettle there
    return cwd
}

// ---------------------------------------------------------------------------
// Spawn the Python IPC server as a subprocess
// ---------------------------------------------------------------------------
spawn_server :: proc(conn: ^IPC_Connection) -> bool {
    project_root := detect_project_root()
    conn.project_root = project_root

    // Ensure .kettle directory exists
    kettle_dir := filepath.join({project_root, ".kettle"})
    os.make_directory(kettle_dir)

    // Remove stale ready file
    ready_path := filepath.join({kettle_dir, "server.ready"})
    os.remove(ready_path)

    // Build command line
    project_db := filepath.join({kettle_dir, "memory.db"})

    // Use a temporary buffer to build the command line
    cmd_buf: [2048]u8
    home_dir := os.get_env("USERPROFILE")
    if len(home_dir) == 0 do home_dir = os.get_env("HOME")
    global_db_dir := filepath.join({home_dir, ".kettle"})
    os.make_directory(global_db_dir)
    global_db := filepath.join({global_db_dir, "global.db"})

    // Find kettle_server.py relative to the executable or project root
    server_script := filepath.join({project_root, "kettle_server.py"})
    if !os.exists(server_script) {
        delete(server_script)
        conn.last_error = "kettle_server.py not found"
        conn.status = .Error
        // Clean up
        delete(kettle_dir); delete(ready_path); delete(project_db)
        delete(global_db_dir); delete(global_db)
        if len(home_dir) > 0 do delete(home_dir)
        return false
    }

    cmd := fmt.tprintf(
        "python \"%s\" --project-db \"%s\" --global-db \"%s\" --port %d --project-root \"%s\"",
        server_script, project_db, global_db, conn.port, project_root,
    )

    // Clean up allocated strings
    delete(kettle_dir); delete(project_db)
    delete(server_script)
    delete(global_db_dir); delete(global_db)
    if len(home_dir) > 0 do delete(home_dir)

    // Launch the process using CreateProcessW (Windows)
    when ODIN_OS == .Windows {
        si: win32.STARTUPINFOW
        si.cb = size_of(win32.STARTUPINFOW)
        pi: win32.PROCESS_INFORMATION

        // Convert command to wide string
        cmd_wide := win32.utf8_to_wstring(cmd)

        // CREATE_NO_WINDOW = 0x08000000
        CREATE_NO_WINDOW : win32.DWORD : 0x08000000

        ok := win32.CreateProcessW(
            nil,              // lpApplicationName
            cmd_wide,         // lpCommandLine
            nil,              // lpProcessAttributes
            nil,              // lpThreadAttributes
            false,            // bInheritHandles
            CREATE_NO_WINDOW, // dwCreationFlags
            nil,              // lpEnvironment
            nil,              // lpCurrentDirectory
            &si,              // lpStartupInfo
            &pi,              // lpProcessInformation
        )

        if ok == false {
            conn.last_error = "failed to spawn Python server"
            conn.status = .Error
            delete(ready_path)
            return false
        }

        conn.server_process = pi.hProcess
        win32.CloseHandle(pi.hThread)
    }

    // Wait for the ready file (poll up to 5 seconds)
    ready := false
    for attempt := 0; attempt < 50; attempt += 1 {
        time.sleep(100 * time.Millisecond)
        if os.exists(ready_path) {
            ready = true
            break
        }
    }

    delete(ready_path)

    if !ready {
        conn.last_error = "server did not become ready in time"
        conn.status = .Error
        return false
    }

    return true
}

// ---------------------------------------------------------------------------
// Connect to the Python server via TCP
// ---------------------------------------------------------------------------
ipc_connect :: proc(conn: ^IPC_Connection) -> bool {
    // Try to connect
    ep := net.Endpoint{
        address = net.IP4_Loopback,
        port    = conn.port,
    }
    socket, err := net.dial_tcp(ep)
    if err != nil {
        conn.last_error = "failed to connect to server"
        conn.status = .Error
        return false
    }

    conn.socket = socket
    conn.status = .Connected

    // Set non-blocking for polling
    net.set_blocking(conn.socket, false)

    // Send session_start
    project_root := conn.project_root if len(conn.project_root) > 0 else "."
    msg := fmt.tprintf(
        "{{\"type\":\"session_start\",\"payload\":{{\"project_root\":\"%s\",\"cwd\":\"%s\"}}}}\n",
        json_escape_string(project_root),
        json_escape_string(project_root),
    )
    ipc_send_raw(conn, msg)

    // Read the response (blocking briefly)
    net.set_blocking(conn.socket, true)
    // Set a short receive timeout via polling
    resp := ipc_recv_line(conn)
    net.set_blocking(conn.socket, false)

    if len(resp) > 0 {
        // Parse session_id and symbol_count from response
        conn.session_id = json_extract_int(resp, "session_id")
        conn.symbol_count = json_extract_int(resp, "total_symbols")
        if conn.symbol_count < 0 do conn.symbol_count = json_extract_int(resp, "symbol_count")
    }

    return true
}

ipc_disconnect :: proc(conn: ^IPC_Connection) {
    if conn.status == .Connected {
        net.close(conn.socket)
    }
    conn.status = .Disconnected
}

ipc_is_connected :: proc(conn: ^IPC_Connection) -> bool {
    return conn.status == .Connected
}

// ---------------------------------------------------------------------------
// Send raw JSON message
// ---------------------------------------------------------------------------
ipc_send_raw :: proc(conn: ^IPC_Connection, msg: string) -> bool {
    if conn.status != .Connected do return false
    data := transmute([]u8)msg
    _, err := net.send_tcp(conn.socket, data)
    if err != nil {
        conn.status = .Error
        conn.last_error = "send failed"
        return false
    }
    return true
}

// ---------------------------------------------------------------------------
// Send typed messages
// ---------------------------------------------------------------------------
ipc_send_file_saved :: proc(conn: ^IPC_Connection, file_path: string) -> bool {
    if conn.status != .Connected do return false
    msg := fmt.tprintf(
        "{{\"type\":\"file_saved\",\"payload\":{{\"file_path\":\"%s\"}}}}\n",
        json_escape_string(file_path),
    )
    ok := ipc_send_raw(conn, msg)
    if !ok do return false

    // Try to read the ack (non-blocking)
    resp := ipc_recv_line(conn)
    if len(resp) > 0 {
        new_count := json_extract_int(resp, "total_symbols")
        if new_count >= 0 do conn.symbol_count = new_count
    }
    return true
}

ipc_send_draft_request :: proc(conn: ^IPC_Connection, file_path: string, cursor_offset: int) -> bool {
    if conn.status != .Connected do return false
    msg := fmt.tprintf(
        "{{\"type\":\"draft_request\",\"payload\":{{\"file_path\":\"%s\",\"cursor_offset\":%d}}}}\n",
        json_escape_string(file_path),
        cursor_offset,
    )
    return ipc_send_raw(conn, msg)
}

ipc_send_session_end :: proc(conn: ^IPC_Connection) -> bool {
    if conn.status != .Connected do return false
    msg := "{\"type\":\"session_end\",\"payload\":{}}\n"
    return ipc_send_raw(conn, msg)
}

ipc_send_draft_accept :: proc(conn: ^IPC_Connection) -> bool {
    if conn.status != .Connected do return false
    msg := "{\"type\":\"draft_accept\",\"payload\":{}}\n"
    return ipc_send_raw(conn, msg)
}

ipc_send_draft_dismiss :: proc(conn: ^IPC_Connection) -> bool {
    if conn.status != .Connected do return false
    msg := "{\"type\":\"draft_dismiss\",\"payload\":{}}\n"
    return ipc_send_raw(conn, msg)
}

// ---------------------------------------------------------------------------
// Receive a line from the socket (reads until newline or buffer full)
// ---------------------------------------------------------------------------
ipc_recv_line :: proc(conn: ^IPC_Connection) -> string {
    if conn.status != .Connected do return ""

    // Check if we already have a complete line in the buffer
    line_result := extract_line_from_buf(conn)
    if len(line_result) > 0 do return line_result

    // Try to read more data, growing the buffer if needed
    space := len(conn.recv_buf) - conn.recv_len
    if space < RECV_BUF_CHUNK {
        // Grow the buffer
        new_cap := len(conn.recv_buf) + RECV_BUF_CHUNK
        resize(&conn.recv_buf, new_cap)
    }

    buf_slice := conn.recv_buf[conn.recv_len:]
    bytes_read, err := net.recv_tcp(conn.socket, buf_slice)
    if err != nil do return ""
    if bytes_read <= 0 do return ""

    conn.recv_len += bytes_read

    // Check again for complete line
    return extract_line_from_buf(conn)
}

// Scan buffer for a newline, extract the line, shift remaining data
extract_line_from_buf :: proc(conn: ^IPC_Connection) -> string {
    for i := 0; i < conn.recv_len; i += 1 {
        if conn.recv_buf[i] == '\n' {
            line := string(conn.recv_buf[:i])
            remaining := conn.recv_len - i - 1
            if remaining > 0 {
                for j := 0; j < remaining; j += 1 {
                    conn.recv_buf[j] = conn.recv_buf[i + 1 + j]
                }
            }
            conn.recv_len = remaining
            return strings.clone(line, context.temp_allocator)
        }
    }
    return ""
}

// ---------------------------------------------------------------------------
// Poll for incoming messages (non-blocking)
// ---------------------------------------------------------------------------
ipc_poll :: proc(conn: ^IPC_Connection) -> (msg: IPC_Message, ok: bool) {
    if conn.status != .Connected do return {}, false
    resp := ipc_recv_line(conn)
    if len(resp) == 0 do return {}, false

    // Parse the response type
    msg_type := json_extract_string(resp, "type")

    if msg_type == "draft_response" {
        draft := Draft_Response{
            draft_text = strings.clone(json_extract_string(resp, "draft_text")),
            confidence = json_extract_float(resp, "confidence"),
        }

        // Parse indexed findings
        findings_count := json_extract_int(resp, "findings_count")
        if findings_count > 0 {
            for i := 0; i < findings_count; i += 1 {
                sev_key := fmt.tprintf("finding_%d_severity", i)
                line_key := fmt.tprintf("finding_%d_line", i)
                msg_key := fmt.tprintf("finding_%d_message", i)

                sev_str := json_extract_string(resp, sev_key)
                severity := Issue_Severity.Info
                if sev_str == "error" do severity = .Error
                else if sev_str == "warning" do severity = .Warning

                issue := Validation_Issue{
                    line     = json_extract_int(resp, line_key),
                    col      = 0,
                    end_line = json_extract_int(resp, line_key),
                    end_col  = 0,
                    severity = severity,
                    message  = strings.clone(json_extract_string(resp, msg_key)),
                }
                append(&draft.issues, issue)
            }
        }

        // Check for error
        error_str := json_extract_string(resp, "error")

        msg.kind = .Draft_Response
        msg.data = draft
        return msg, len(draft.draft_text) > 0 || len(error_str) > 0
    }

    // Update symbol count from any ack that contains it
    new_count := json_extract_int(resp, "total_symbols")
    if new_count >= 0 do conn.symbol_count = new_count

    return {}, false
}

// ---------------------------------------------------------------------------
// Minimal JSON helpers (no external JSON library needed for simple messages)
// ---------------------------------------------------------------------------
json_escape_string :: proc(s: string) -> string {
    // Escape special chars for JSON embedding
    // Uses temp allocator
    b := strings.builder_make(context.temp_allocator)
    for i := 0; i < len(s); i += 1 {
        ch := s[i]
        switch ch {
        case '"':  strings.write_string(&b, "\\\"")
        case '\\': strings.write_string(&b, "\\\\")
        case '\n': strings.write_string(&b, "\\n")
        case '\r': strings.write_string(&b, "\\r")
        case '\t': strings.write_string(&b, "\\t")
        case:
            if ch < 0x20 {
                // Control character — skip
            } else {
                strings.write_byte(&b, ch)
            }
        }
    }
    return strings.to_string(b)
}

// Extract an integer value from a JSON string by key name
// Simple substring search — works for flat JSON objects
json_extract_int :: proc(json_str: string, key: string) -> int {
    // Look for "key": <number>
    search := fmt.tprintf("\"%s\":", key)
    idx := strings.index(json_str, search)
    if idx < 0 do return -1

    // Skip past the key and colon
    start := idx + len(search)
    // Skip whitespace
    for start < len(json_str) && (json_str[start] == ' ' || json_str[start] == '\t') {
        start += 1
    }
    if start >= len(json_str) do return -1

    // Parse the number
    neg := false
    if json_str[start] == '-' { neg = true; start += 1 }
    val := 0
    for start < len(json_str) && json_str[start] >= '0' && json_str[start] <= '9' {
        val = val * 10 + int(json_str[start] - '0')
        start += 1
    }
    if neg do val = -val
    return val
}

// Extract a string value from a JSON string by key name
json_extract_string :: proc(json_str: string, key: string) -> string {
    search := fmt.tprintf("\"%s\":", key)
    idx := strings.index(json_str, search)
    if idx < 0 do return ""

    start := idx + len(search)
    for start < len(json_str) && (json_str[start] == ' ' || json_str[start] == '\t') {
        start += 1
    }
    if start >= len(json_str) || json_str[start] != '"' do return ""
    start += 1 // skip opening quote

    end := start
    for end < len(json_str) && json_str[end] != '"' {
        if json_str[end] == '\\' do end += 1  // skip escaped char
        end += 1
    }
    if end >= len(json_str) do return ""
    return json_str[start:end]
}

// Extract a float value from a JSON string by key name
json_extract_float :: proc(json_str: string, key: string) -> f32 {
    search := fmt.tprintf("\"%s\":", key)
    idx := strings.index(json_str, search)
    if idx < 0 do return 0.0

    start := idx + len(search)
    for start < len(json_str) && (json_str[start] == ' ' || json_str[start] == '\t') {
        start += 1
    }
    if start >= len(json_str) do return 0.0

    // Parse sign
    neg := false
    if json_str[start] == '-' { neg = true; start += 1 }

    // Parse integer part
    val: f64 = 0
    for start < len(json_str) && json_str[start] >= '0' && json_str[start] <= '9' {
        val = val * 10 + f64(json_str[start] - '0')
        start += 1
    }

    // Parse fractional part
    if start < len(json_str) && json_str[start] == '.' {
        start += 1
        frac: f64 = 0.1
        for start < len(json_str) && json_str[start] >= '0' && json_str[start] <= '9' {
            val += f64(json_str[start] - '0') * frac
            frac *= 0.1
            start += 1
        }
    }

    if neg do val = -val
    return f32(val)
}
