package editor

// ---------------------------------------------------------------------------
// Operation type enumeration
// ---------------------------------------------------------------------------
Op_Kind :: enum u8 {
    Insert,
    Delete,
    Replace,
}

// ---------------------------------------------------------------------------
// Operation record — stores enough to execute, undo, and redo
//
// The key idea: we store the piece_index where the change happened,
// the old pieces that were there before, and the new pieces that replaced
// them. Executing = swap old for new. Undoing = swap new for old.
// ---------------------------------------------------------------------------
Operation :: struct {
    kind:           Op_Kind,
    piece_index:    int,        // where in the piece sequence the change starts
    old_pieces:     []Piece,    // pieces before the operation (heap-allocated clone)
    new_pieces:     []Piece,    // pieces after the operation (heap-allocated clone)
    cursor_before:  int,        // document offset of cursor before op
    cursor_after:   int,        // document offset of cursor after op
    timestamp_ms:   i64,        // for merge decisions
}

operation_destroy :: proc(op: ^Operation) {
    if op.old_pieces != nil do delete(op.old_pieces)
    if op.new_pieces != nil do delete(op.new_pieces)
}

// ---------------------------------------------------------------------------
// Execute an insert at a document offset
// ---------------------------------------------------------------------------
execute_insert :: proc(pt: ^Piece_Table, doc_offset: int, content: string, cursor_before: int, timestamp_ms: i64) -> Operation {
    assert(len(content) > 0, "execute_insert called with empty content")
    assert(doc_offset >= 0 && doc_offset <= pt.doc_length, "insert offset out of range")

    // Append to add buffer
    add_start := add_buffer_append(pt, content)
    raw := transmute([]u8)content
    nl := count_newlines(raw)

    new_piece := Piece{
        buffer        = .Add,
        start         = add_start,
        length        = len(content),
        newline_count = nl,
    }

    op: Operation
    op.kind          = .Insert
    op.cursor_before = cursor_before
    op.cursor_after  = doc_offset + len(content)
    op.timestamp_ms  = timestamp_ms

    if pt.doc_length == 0 || doc_offset == pt.doc_length {
        // Insert at empty document or at the very end
        if doc_offset == pt.doc_length && pt.doc_length > 0 {
            // Appending at end — no pieces removed
            op.piece_index = len(pt.pieces)
            op.old_pieces  = nil
            op.new_pieces  = clone_pieces([]Piece{new_piece})
            append(&pt.pieces, new_piece)
        } else {
            // Empty document
            op.piece_index = 0
            op.old_pieces  = nil
            op.new_pieces  = clone_pieces([]Piece{new_piece})
            append(&pt.pieces, new_piece)
        }
    } else {
        pidx, local := locate_offset(pt, doc_offset)

        if local == 0 {
            // Insert at the boundary before piece[pidx] — no split needed
            op.piece_index = pidx
            op.old_pieces  = nil
            op.new_pieces  = clone_pieces([]Piece{new_piece})
            inject_at(&pt.pieces, pidx, new_piece)
        } else {
            // Split piece[pidx] at local offset
            original := pt.pieces[pidx]
            left, right := split_piece(pt, original, local)

            op.piece_index = pidx
            op.old_pieces  = clone_pieces([]Piece{original})
            op.new_pieces  = clone_pieces([]Piece{left, new_piece, right})

            // Replace original piece with left, new_piece, right
            ordered_remove(&pt.pieces, pidx)
            inject_at(&pt.pieces, pidx, right)
            inject_at(&pt.pieces, pidx, new_piece)
            inject_at(&pt.pieces, pidx, left)
        }
    }

    recalculate_totals(pt)
    return op
}

// ---------------------------------------------------------------------------
// Execute a delete between two document offsets [start_offset, end_offset)
// ---------------------------------------------------------------------------
execute_delete :: proc(pt: ^Piece_Table, start_offset: int, end_offset: int, cursor_before: int, timestamp_ms: i64) -> Operation {
    assert(start_offset >= 0 && end_offset <= pt.doc_length, "delete range out of bounds")
    assert(start_offset < end_offset, "delete range is empty")

    op: Operation
    op.kind          = .Delete
    op.cursor_before = cursor_before
    op.cursor_after  = start_offset
    op.timestamp_ms  = timestamp_ms

    // Find the range of pieces affected
    s_pidx, s_local := locate_offset(pt, start_offset)
    e_pidx, e_local := locate_offset(pt, end_offset)

    // Store the old pieces (the full range from s_pidx to e_pidx inclusive)
    // Handle the case where e_local == 0 meaning the end falls exactly on a boundary
    last_affected := e_pidx
    if e_local == 0 && e_pidx > s_pidx {
        last_affected = e_pidx - 1
    } else if e_local == 0 && e_pidx == s_pidx {
        // Deleting zero bytes from start of piece — shouldn't happen given our assert
        assert(false, "unexpected state in execute_delete")
    }

    op.piece_index = s_pidx
    old_piece_count := last_affected - s_pidx + 1
    op.old_pieces = clone_pieces(pt.pieces[s_pidx : s_pidx + old_piece_count])

    // Build the new pieces — at most two: the kept prefix of the first piece
    // and the kept suffix of the last piece
    new_pieces_buf: [dynamic]Piece
    defer delete(new_pieces_buf)

    // Left remainder: content of pieces[s_pidx] before start_offset
    if s_local > 0 {
        orig := pt.pieces[s_pidx]
        left_bytes := piece_bytes_from(pt, orig, 0, s_local)
        append(&new_pieces_buf, Piece{
            buffer        = orig.buffer,
            start         = orig.start,
            length        = s_local,
            newline_count = count_newlines(left_bytes),
        })
    }

    // Right remainder: content of pieces[last_affected] after end_offset
    if last_affected == e_pidx && e_local > 0 && e_local < pt.pieces[e_pidx].length {
        orig := pt.pieces[e_pidx]
        right_len := orig.length - e_local
        right_bytes := piece_bytes_from(pt, orig, e_local, right_len)
        append(&new_pieces_buf, Piece{
            buffer        = orig.buffer,
            start         = orig.start + e_local,
            length        = right_len,
            newline_count = count_newlines(right_bytes),
        })
    } else if e_local == 0 && e_pidx > last_affected {
        // End falls exactly on a piece boundary — nothing to keep from the right
    }

    op.new_pieces = clone_pieces(new_pieces_buf[:])

    // Apply: remove old pieces, insert new pieces
    for i := 0; i < old_piece_count; i += 1 {
        ordered_remove(&pt.pieces, s_pidx)
    }
    for i := 0; i < len(new_pieces_buf); i += 1 {
        inject_at(&pt.pieces, s_pidx + i, new_pieces_buf[i])
    }

    recalculate_totals(pt)
    return op
}

// ---------------------------------------------------------------------------
// Execute a replace: delete [start, end) then insert content at start
// Atomic single operation for undo purposes.
// ---------------------------------------------------------------------------
execute_replace :: proc(pt: ^Piece_Table, start_offset: int, end_offset: int, content: string, cursor_before: int, timestamp_ms: i64) -> Operation {
    assert(start_offset >= 0 && end_offset <= pt.doc_length, "replace range out of bounds")
    assert(start_offset <= end_offset, "replace range is inverted")

    op: Operation
    op.kind          = .Replace
    op.cursor_before = cursor_before
    op.cursor_after  = start_offset + len(content)
    op.timestamp_ms  = timestamp_ms

    // Snapshot old pieces covering [start_offset, end_offset)
    s_pidx, s_local := locate_offset(pt, start_offset)

    if end_offset > start_offset {
        e_pidx, e_local := locate_offset(pt, end_offset)
        last_affected := e_pidx
        if e_local == 0 && e_pidx > s_pidx {
            last_affected = e_pidx - 1
        }
        old_count := last_affected - s_pidx + 1
        op.piece_index = s_pidx
        op.old_pieces  = clone_pieces(pt.pieces[s_pidx : s_pidx + old_count])

        // Remove old pieces
        for i := 0; i < old_count; i += 1 {
            ordered_remove(&pt.pieces, s_pidx)
        }
    } else {
        // Pure insert (no deletion range)
        op.piece_index = s_pidx
        op.old_pieces  = nil
    }

    // Build new pieces
    new_pieces_buf: [dynamic]Piece
    defer delete(new_pieces_buf)

    // Left remainder from the first old piece
    if s_local > 0 && len(op.old_pieces) > 0 {
        orig := op.old_pieces[0]
        left_bytes := piece_bytes_from(pt, orig, 0, s_local)
        append(&new_pieces_buf, Piece{
            buffer        = orig.buffer,
            start         = orig.start,
            length        = s_local,
            newline_count = count_newlines(left_bytes),
        })
    }

    // The inserted content
    if len(content) > 0 {
        add_start := add_buffer_append(pt, content)
        raw := transmute([]u8)content
        append(&new_pieces_buf, Piece{
            buffer        = .Add,
            start         = add_start,
            length        = len(content),
            newline_count = count_newlines(raw),
        })
    }

    // Right remainder: content in old pieces beyond the deleted range
    if end_offset > start_offset && len(op.old_pieces) > 0 {
        total_old_len := 0
        for &p in op.old_pieces {
            total_old_len += p.length
        }
        deleted_span := end_offset - start_offset
        right_remainder := total_old_len - s_local - deleted_span
        if right_remainder > 0 {
            // Find which old piece (and offset within it) the right remainder starts from
            consumed := s_local + deleted_span
            e_pidx, e_local := locate_offset_in_old_pieces(op.old_pieces, consumed)
            if e_pidx < len(op.old_pieces) {
                // Partial piece at e_pidx
                src := op.old_pieces[e_pidx]
                partial_len := src.length - e_local
                rb := piece_bytes_from(pt, src, e_local, partial_len)
                append(&new_pieces_buf, Piece{
                    buffer        = src.buffer,
                    start         = src.start + e_local,
                    length        = partial_len,
                    newline_count = count_newlines(rb),
                })
                // Any full pieces after e_pidx also survive
                for pidx := e_pidx + 1; pidx < len(op.old_pieces); pidx += 1 {
                    remaining := op.old_pieces[pidx]
                    fb := piece_bytes_from(pt, remaining, 0, remaining.length)
                    append(&new_pieces_buf, Piece{
                        buffer        = remaining.buffer,
                        start         = remaining.start,
                        length        = remaining.length,
                        newline_count = count_newlines(fb),
                    })
                }
            }
        }
    }

    op.new_pieces = clone_pieces(new_pieces_buf[:])

    // Insert new pieces
    for i := 0; i < len(new_pieces_buf); i += 1 {
        inject_at(&pt.pieces, s_pidx + i, new_pieces_buf[i])
    }

    recalculate_totals(pt)
    return op
}

// ---------------------------------------------------------------------------
// Undo an operation: swap new pieces back to old pieces
// ---------------------------------------------------------------------------
undo_operation :: proc(pt: ^Piece_Table, op: ^Operation) {
    // Remove the new pieces
    new_count := len(op.new_pieces) if op.new_pieces != nil else 0
    for i := 0; i < new_count; i += 1 {
        ordered_remove(&pt.pieces, op.piece_index)
    }
    // Re-insert the old pieces
    old_count := len(op.old_pieces) if op.old_pieces != nil else 0
    for i := 0; i < old_count; i += 1 {
        inject_at(&pt.pieces, op.piece_index + i, op.old_pieces[i])
    }
    recalculate_totals(pt)
}

// ---------------------------------------------------------------------------
// Redo an operation: swap old pieces back to new pieces
// ---------------------------------------------------------------------------
redo_operation :: proc(pt: ^Piece_Table, op: ^Operation) {
    // Remove the old pieces
    old_count := len(op.old_pieces) if op.old_pieces != nil else 0
    for i := 0; i < old_count; i += 1 {
        ordered_remove(&pt.pieces, op.piece_index)
    }
    // Re-insert the new pieces
    new_count := len(op.new_pieces) if op.new_pieces != nil else 0
    for i := 0; i < new_count; i += 1 {
        inject_at(&pt.pieces, op.piece_index + i, op.new_pieces[i])
    }
    recalculate_totals(pt)
}

// ---------------------------------------------------------------------------
// Locate a byte offset within a slice of old pieces (used by execute_replace)
// ---------------------------------------------------------------------------
locate_offset_in_old_pieces :: proc(pieces: []Piece, target: int) -> (idx: int, local: int) {
    running := 0
    for i := 0; i < len(pieces); i += 1 {
        if target < running + pieces[i].length {
            return i, target - running
        }
        running += pieces[i].length
    }
    return len(pieces), 0
}
