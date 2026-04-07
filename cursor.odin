package editor

import "core:fmt"

// ---------------------------------------------------------------------------
// Editor cursor — tracks position as a document offset and caches
// line/column for rendering. Also handles selection anchor.
// ---------------------------------------------------------------------------
Cursor :: struct {
    offset:         int,    // byte offset into the document
    line:           int,    // 0-indexed line number
    col:            int,    // 0-indexed column (byte-based for now)
    preferred_col:  int,    // for vertical movement — remembers column intent
    sel_anchor:     int,    // selection anchor offset, -1 if no selection
}

cursor_init :: proc(c: ^Cursor) {
    c.offset        = 0
    c.line          = 0
    c.col           = 0
    c.preferred_col = 0
    c.sel_anchor    = -1
}

// ---------------------------------------------------------------------------
// Recompute line and column from the current offset by scanning pieces.
// This is O(doc_size) in the worst case but fine for documents under ~1MB.
// ---------------------------------------------------------------------------
cursor_recompute_line_col :: proc(pt: ^Piece_Table, c: ^Cursor) {
    c.line = 0
    c.col  = 0
    remaining := c.offset

    for &p in pt.pieces {
        if remaining <= 0 do break

        bytes := piece_bytes(pt, &p)
        scan_len := min(remaining, p.length)

        for i := 0; i < scan_len; i += 1 {
            if bytes[i] == '\n' {
                c.line += 1
                c.col   = 0
            } else {
                c.col += 1
            }
        }
        remaining -= scan_len
    }
}

// ---------------------------------------------------------------------------
// Find the document offset of the start of a given line number.
// Returns doc_length if line_num exceeds total lines.
// ---------------------------------------------------------------------------
find_line_start :: proc(pt: ^Piece_Table, line_num: int) -> int {
    if line_num == 0 do return 0

    current_line := 0
    doc_offset := 0

    for &p in pt.pieces {
        bytes := piece_bytes(pt, &p)
        for i := 0; i < p.length; i += 1 {
            if bytes[i] == '\n' {
                current_line += 1
                if current_line == line_num {
                    return doc_offset + i + 1
                }
            }
        }
        doc_offset += p.length
    }

    return pt.doc_length
}

// ---------------------------------------------------------------------------
// Find the length of a given line (not counting the newline character).
// ---------------------------------------------------------------------------
find_line_length :: proc(pt: ^Piece_Table, line_num: int) -> int {
    line_start := find_line_start(pt, line_num)
    length := 0
    remaining := line_start
    started := false

    doc_offset := 0
    for &p in pt.pieces {
        bytes := piece_bytes(pt, &p)
        for i := 0; i < p.length; i += 1 {
            pos := doc_offset + i
            if pos < line_start do continue
            if !started do started = true
            if bytes[i] == '\n' do return length
            length += 1
        }
        doc_offset += p.length
    }

    return length
}

// ---------------------------------------------------------------------------
// Move cursor to a specific line and column (clamped to line length).
// ---------------------------------------------------------------------------
cursor_move_to_line_col :: proc(pt: ^Piece_Table, c: ^Cursor, line: int, col: int) {
    target_line := clamp(line, 0, pt.total_lines - 1)
    line_start := find_line_start(pt, target_line)
    line_len := find_line_length(pt, target_line)
    target_col := clamp(col, 0, line_len)

    c.offset = line_start + target_col
    c.line   = target_line
    c.col    = target_col
}

// ---------------------------------------------------------------------------
// Movement primitives
// ---------------------------------------------------------------------------
cursor_move_right :: proc(pt: ^Piece_Table, c: ^Cursor) {
    if c.offset < pt.doc_length {
        c.offset += 1
        cursor_recompute_line_col(pt, c)
        c.preferred_col = c.col
    }
}

cursor_move_left :: proc(pt: ^Piece_Table, c: ^Cursor) {
    if c.offset > 0 {
        c.offset -= 1
        cursor_recompute_line_col(pt, c)
        c.preferred_col = c.col
    }
}

cursor_move_up :: proc(pt: ^Piece_Table, c: ^Cursor) {
    if c.line > 0 {
        cursor_move_to_line_col(pt, c, c.line - 1, c.preferred_col)
    }
}

cursor_move_down :: proc(pt: ^Piece_Table, c: ^Cursor) {
    if c.line < pt.total_lines - 1 {
        cursor_move_to_line_col(pt, c, c.line + 1, c.preferred_col)
    }
}

cursor_move_home :: proc(pt: ^Piece_Table, c: ^Cursor) {
    // Smart home: go to first non-whitespace, or to column 0 if already there
    line_start := find_line_start(pt, c.line)

    // Find first non-whitespace on this line
    first_nonws := 0
    doc_offset := 0
    scanning := false
    for &p in pt.pieces {
        bytes := piece_bytes(pt, &p)
        for i := 0; i < p.length; i += 1 {
            pos := doc_offset + i
            if pos < line_start do continue
            if !scanning do scanning = true
            if bytes[i] == '\n' {
                // Empty line or all whitespace
                break
            }
            if bytes[i] != ' ' && bytes[i] != '\t' {
                first_nonws = pos - line_start
                // Jump to first_nonws if we're not already there, else to 0
                if c.col == first_nonws {
                    c.offset = line_start
                    c.col    = 0
                } else {
                    c.offset = line_start + first_nonws
                    c.col    = first_nonws
                }
                c.preferred_col = c.col
                return
            }
            first_nonws = pos - line_start + 1
        }
        doc_offset += p.length
    }

    // Line is all whitespace or we fell through — go to line start
    c.offset = line_start
    c.col    = 0
    c.preferred_col = c.col
}

cursor_move_end :: proc(pt: ^Piece_Table, c: ^Cursor) {
    line_len := find_line_length(pt, c.line)
    line_start := find_line_start(pt, c.line)
    c.offset = line_start + line_len
    c.col    = line_len
    c.preferred_col = c.col
}

// ---------------------------------------------------------------------------
// Selection helpers
// ---------------------------------------------------------------------------
cursor_sel_start :: proc(c: ^Cursor) -> int {
    if c.sel_anchor < 0 do return c.offset
    return min(c.sel_anchor, c.offset)
}

cursor_sel_end :: proc(c: ^Cursor) -> int {
    if c.sel_anchor < 0 do return c.offset
    return max(c.sel_anchor, c.offset)
}

cursor_has_selection :: proc(c: ^Cursor) -> bool {
    return c.sel_anchor >= 0 && c.sel_anchor != c.offset
}

cursor_clear_selection :: proc(c: ^Cursor) {
    c.sel_anchor = -1
}

cursor_begin_selection :: proc(c: ^Cursor) {
    if c.sel_anchor < 0 {
        c.sel_anchor = c.offset
    }
}

// ---------------------------------------------------------------------------
// Get character at a document offset (returns 0 if out of range)
// ---------------------------------------------------------------------------
char_at_offset :: proc(pt: ^Piece_Table, offset: int) -> u8 {
    if offset < 0 || offset >= pt.doc_length do return 0
    running := 0
    for &p in pt.pieces {
        if offset < running + p.length {
            bytes := piece_bytes(pt, &p)
            return bytes[offset - running]
        }
        running += p.length
    }
    return 0
}

// ---------------------------------------------------------------------------
// Word movement — skip whitespace then skip word characters (or vice versa)
// ---------------------------------------------------------------------------
is_word_char :: proc(ch: u8) -> bool {
    return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
           (ch >= '0' && ch <= '9') || ch == '_'
}

cursor_word_left :: proc(pt: ^Piece_Table, c: ^Cursor) {
    if c.offset == 0 do return

    // Skip whitespace/newlines going left
    for c.offset > 0 {
        ch := char_at_offset(pt, c.offset - 1)
        if is_word_char(ch) do break
        c.offset -= 1
    }
    // Skip word characters going left
    for c.offset > 0 {
        ch := char_at_offset(pt, c.offset - 1)
        if !is_word_char(ch) do break
        c.offset -= 1
    }

    cursor_recompute_line_col(pt, c)
    c.preferred_col = c.col
}

cursor_word_right :: proc(pt: ^Piece_Table, c: ^Cursor) {
    if c.offset >= pt.doc_length do return

    // Skip word characters going right
    for c.offset < pt.doc_length {
        ch := char_at_offset(pt, c.offset)
        if !is_word_char(ch) do break
        c.offset += 1
    }
    // Skip whitespace/newlines going right
    for c.offset < pt.doc_length {
        ch := char_at_offset(pt, c.offset)
        if is_word_char(ch) do break
        c.offset += 1
    }

    cursor_recompute_line_col(pt, c)
    c.preferred_col = c.col
}

// ---------------------------------------------------------------------------
// Get the leading whitespace of the line the cursor is currently on.
// Returns a slice of bytes (spaces and tabs) from the temp allocator.
// ---------------------------------------------------------------------------
get_current_line_indent :: proc(pt: ^Piece_Table, c: ^Cursor) -> []u8 {
    line_start := find_line_start(pt, c.line)

    indent_buf: [256]u8
    indent_len := 0
    offset := line_start

    for offset < pt.doc_length && indent_len < len(indent_buf) {
        ch := char_at_offset(pt, offset)
        if ch == ' ' || ch == '\t' {
            indent_buf[indent_len] = ch
            indent_len += 1
            offset += 1
        } else {
            break
        }
    }

    if indent_len == 0 do return nil
    result := make([]u8, indent_len, context.temp_allocator)
    for i := 0; i < indent_len; i += 1 {
        result[i] = indent_buf[i]
    }
    return result
}

// ---------------------------------------------------------------------------
// Select the word under the cursor (for double-click)
// ---------------------------------------------------------------------------
select_word_at_cursor :: proc(pt: ^Piece_Table, c: ^Cursor) {
    start := c.offset
    for start > 0 && is_word_char(char_at_offset(pt, start - 1)) {
        start -= 1
    }
    end := c.offset
    for end < pt.doc_length && is_word_char(char_at_offset(pt, end)) {
        end += 1
    }
    c.sel_anchor = start
    c.offset = end
    cursor_recompute_line_col(pt, c)
    c.preferred_col = c.col
}
