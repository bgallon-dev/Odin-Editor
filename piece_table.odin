package editor

import "core:mem"
import "core:fmt"
import "core:strings"
import "core:slice"

// ---------------------------------------------------------------------------
// Buffer identity — which buffer a piece points into
// ---------------------------------------------------------------------------
Buffer_Kind :: enum u8 {
    Original,
    Add,
}

// ---------------------------------------------------------------------------
// Piece descriptor — a view into one of the two buffers
// 32 bytes on 64-bit: buffer(1) + pad(3) + start(4) + length(4) + newlines(4) + pad(16)
// We keep it simple and let Odin align naturally.
// ---------------------------------------------------------------------------
Piece :: struct {
    buffer:        Buffer_Kind,
    start:         int,       // byte offset into the buffer
    length:        int,       // byte length of this piece's content
    newline_count: int,       // number of '\n' characters in this span
}

// ---------------------------------------------------------------------------
// The piece table itself
// ---------------------------------------------------------------------------
Piece_Table :: struct {
    original_buf: []u8,               // immutable after init
    add_buf:      [dynamic]u8,        // append-only
    pieces:       [dynamic]Piece,     // the piece sequence — this is "the document"
    doc_length:   int,                // cached total byte length
    total_lines:  int,                // cached total newline count + 1
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------
piece_table_init :: proc(pt: ^Piece_Table, initial_content: string) {
    raw := transmute([]u8)initial_content

    // Copy the original content into an owned slice
    pt.original_buf = make([]u8, len(raw))
    copy(pt.original_buf, raw)

    pt.add_buf = make([dynamic]u8)
    pt.pieces  = make([dynamic]Piece)

    if len(raw) > 0 {
        nl := count_newlines(raw[:])
        append(&pt.pieces, Piece{
            buffer        = .Original,
            start         = 0,
            length        = len(raw),
            newline_count = nl,
        })
        pt.doc_length  = len(raw)
        pt.total_lines = nl + 1
    } else {
        pt.doc_length  = 0
        pt.total_lines = 1
    }
}

piece_table_destroy :: proc(pt: ^Piece_Table) {
    delete(pt.original_buf)
    delete(pt.add_buf)
    delete(pt.pieces)
}

// ---------------------------------------------------------------------------
// Content reconstruction — reads the full document or a slice of it
// ---------------------------------------------------------------------------
piece_table_content :: proc(pt: ^Piece_Table, allocator := context.allocator) -> string {
    if pt.doc_length == 0 do return ""

    buf := make([]u8, pt.doc_length, allocator)
    offset := 0
    for &p in pt.pieces {
        src := piece_bytes(pt, &p)
        copy(buf[offset:], src)
        offset += p.length
    }
    return string(buf)
}

// ---------------------------------------------------------------------------
// Piece content accessor
// ---------------------------------------------------------------------------
piece_bytes :: proc(pt: ^Piece_Table, p: ^Piece) -> []u8 {
    switch p.buffer {
    case .Original:
        return pt.original_buf[p.start : p.start + p.length]
    case .Add:
        return pt.add_buf[p.start : p.start + p.length]
    }
    return nil
}

// ---------------------------------------------------------------------------
// Locate which piece contains a given document offset.
// Returns: piece_index, offset_within_piece
// If doc_offset == doc_length, returns one-past-end position for appending.
// ---------------------------------------------------------------------------
locate_offset :: proc(pt: ^Piece_Table, doc_offset: int) -> (piece_idx: int, local_offset: int) {
    assert(doc_offset >= 0 && doc_offset <= pt.doc_length,
           "doc_offset out of range in locate_offset")

    running := 0
    for i := 0; i < len(pt.pieces); i += 1 {
        p := &pt.pieces[i]
        if doc_offset < running + p.length {
            return i, doc_offset - running
        }
        running += p.length
    }
    // doc_offset == doc_length: position is at the very end
    return len(pt.pieces), 0
}

// ---------------------------------------------------------------------------
// Split a piece at a local offset, producing two pieces.
// Does NOT mutate the piece sequence — caller is responsible for that.
// ---------------------------------------------------------------------------
split_piece :: proc(pt: ^Piece_Table, p: Piece, local_offset: int) -> (left: Piece, right: Piece) {
    assert(local_offset > 0 && local_offset < p.length,
           "split_piece called with offset at boundary")

    left_bytes  := piece_bytes_from(pt, p, 0, local_offset)
    right_bytes := piece_bytes_from(pt, p, local_offset, p.length - local_offset)

    left = Piece{
        buffer        = p.buffer,
        start         = p.start,
        length        = local_offset,
        newline_count = count_newlines(left_bytes),
    }
    right = Piece{
        buffer        = p.buffer,
        start         = p.start + local_offset,
        length        = p.length - local_offset,
        newline_count = count_newlines(right_bytes),
    }
    return
}

// ---------------------------------------------------------------------------
// Helper: get bytes from a piece by value (not pointer) with sub-range
// ---------------------------------------------------------------------------
piece_bytes_from :: proc(pt: ^Piece_Table, p: Piece, offset: int, length: int) -> []u8 {
    switch p.buffer {
    case .Original:
        return pt.original_buf[p.start + offset : p.start + offset + length]
    case .Add:
        return pt.add_buf[p.start + offset : p.start + offset + length]
    }
    return nil
}

// ---------------------------------------------------------------------------
// Append content to the add buffer. Returns the start offset in the add buffer.
// ---------------------------------------------------------------------------
add_buffer_append :: proc(pt: ^Piece_Table, content: string) -> int {
    start := len(pt.add_buf)
    raw := transmute([]u8)content
    for b in raw {
        append(&pt.add_buf, b)
    }
    return start
}

// ---------------------------------------------------------------------------
// Recalculate cached totals from the piece sequence.
// Called after any piece sequence mutation.
// ---------------------------------------------------------------------------
recalculate_totals :: proc(pt: ^Piece_Table) {
    pt.doc_length  = 0
    pt.total_lines = 1
    for &p in pt.pieces {
        pt.doc_length  += p.length
        pt.total_lines += p.newline_count
    }
}

// ---------------------------------------------------------------------------
// Count newlines in a byte slice
// ---------------------------------------------------------------------------
count_newlines :: proc(data: []u8) -> int {
    count := 0
    for b in data {
        if b == '\n' do count += 1
    }
    return count
}

// ---------------------------------------------------------------------------
// Clone a slice of pieces (for storing in operation records)
// ---------------------------------------------------------------------------
clone_pieces :: proc(pieces: []Piece, allocator := context.allocator) -> []Piece {
    if len(pieces) == 0 do return nil
    result := make([]Piece, len(pieces), allocator)
    copy(result, pieces)
    return result
}
