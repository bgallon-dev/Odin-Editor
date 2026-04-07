package editor

// ---------------------------------------------------------------------------
// Undo stack — dynamic array of operations with a position pointer
//
// Stack layout:
//   [op0, op1, op2, op3]
//                  ^--- position (points to next slot)
//
// position == 3 means ops 0..2 are committed, undo goes to op2.
// If we undo to position 1, then type something new, ops 1..2 are discarded
// (naive branching) and the new op goes at position 1.
// ---------------------------------------------------------------------------

MERGE_WINDOW_MS :: 300  // milliseconds between keystrokes to merge

Undo_Stack :: struct {
    ops:      [dynamic]Operation,
    position: int,                 // index of next slot (== number of active ops)
}

undo_stack_init :: proc(us: ^Undo_Stack) {
    us.ops      = make([dynamic]Operation)
    us.position = 0
}

undo_stack_destroy :: proc(us: ^Undo_Stack) {
    for i := 0; i < len(us.ops); i += 1 {
        operation_destroy(&us.ops[i])
    }
    delete(us.ops)
}

// ---------------------------------------------------------------------------
// Push a new operation onto the stack.
// Discards any ops after current position (naive branch pruning).
// Attempts to merge with the previous operation if eligible.
// ---------------------------------------------------------------------------
undo_stack_push :: proc(us: ^Undo_Stack, pt: ^Piece_Table, op: Operation) {
    // Discard any undone operations beyond the current position
    for i := us.position; i < len(us.ops); i += 1 {
        operation_destroy(&us.ops[i])
    }
    resize(&us.ops, us.position)

    // Copy to local so we can take its address
    op_local := op

    // Try to merge with previous operation
    if us.position > 0 {
        prev := &us.ops[us.position - 1]
        if can_merge(prev, &op_local) {
            if merge_into(prev, &op_local, pt) {
                return
            }
        }
    }

    append(&us.ops, op_local)
    us.position += 1
}

// ---------------------------------------------------------------------------
// Undo: reverse the most recent operation, move position back
// Returns the cursor position to restore, or -1 if nothing to undo.
// ---------------------------------------------------------------------------
undo_stack_undo :: proc(us: ^Undo_Stack, pt: ^Piece_Table) -> int {
    if us.position == 0 do return -1

    us.position -= 1
    op := &us.ops[us.position]
    undo_operation(pt, op)
    return op.cursor_before
}

// ---------------------------------------------------------------------------
// Redo: re-execute the next undone operation, move position forward
// Returns the cursor position to restore, or -1 if nothing to redo.
// ---------------------------------------------------------------------------
undo_stack_redo :: proc(us: ^Undo_Stack, pt: ^Piece_Table) -> int {
    if us.position >= len(us.ops) do return -1

    op := &us.ops[us.position]
    redo_operation(pt, op)
    us.position += 1
    return op.cursor_after
}

// ---------------------------------------------------------------------------
// Merge eligibility: consecutive single-character inserts that are adjacent
// in the document and within the time window.
// ---------------------------------------------------------------------------
can_merge :: proc(prev: ^Operation, next: ^Operation) -> bool {
    if prev.kind != .Insert || next.kind != .Insert do return false

    // Must be adjacent: previous insert ended where new one starts
    if prev.cursor_after != next.cursor_after - 1 do return false

    // Check time window
    if next.timestamp_ms - prev.timestamp_ms > MERGE_WINDOW_MS do return false

    // Only merge single-character inserts
    if next.new_pieces == nil || len(next.new_pieces) == 0 do return false

    // Don't merge across newlines — each line should be a separate undo step
    for &p in next.new_pieces {
        if p.newline_count > 0 do return false
    }

    return true
}

// ---------------------------------------------------------------------------
// Merge the next operation into the previous one.
// The merged operation undoes back to prev's original state and redoes
// to next's final state.
// ---------------------------------------------------------------------------
merge_into :: proc(prev: ^Operation, next: ^Operation, pt: ^Piece_Table) -> bool {
    // The merged operation keeps prev's old_pieces (the state before the whole
    // sequence of inserts) and extends new_pieces to cover the combined insert.
    //
    // Returns false if the pieces are not contiguous in the add buffer,
    // meaning the merge cannot proceed safely. The caller should treat the
    // operations as separate entries in that case.

    if prev.new_pieces == nil || len(prev.new_pieces) == 0 do return false
    if next.new_pieces == nil || len(next.new_pieces) == 0 do return false

    last_new := &prev.new_pieces[len(prev.new_pieces) - 1]
    np := &next.new_pieces[0]

    // Must be contiguous in the add buffer
    if !(last_new.buffer == .Add && np.buffer == .Add &&
         last_new.start + last_new.length == np.start) {
        return false
    }

    // Extend the stored new_pieces record
    last_new.length        += np.length
    last_new.newline_count += np.newline_count

    // Also consolidate the real piece sequence:
    // The next op inserted a piece at next.piece_index.
    // Merge it into the piece at (next.piece_index - 1).
    real_idx := next.piece_index
    if real_idx > 0 && real_idx < len(pt.pieces) {
        prev_real := &pt.pieces[real_idx - 1]
        next_real := &pt.pieces[real_idx]
        if prev_real.buffer == .Add && next_real.buffer == .Add &&
           prev_real.start + prev_real.length == next_real.start {
            prev_real.length        += next_real.length
            prev_real.newline_count += next_real.newline_count
            ordered_remove(&pt.pieces, real_idx)
        }
    }

    prev.cursor_after  = next.cursor_after
    prev.timestamp_ms  = next.timestamp_ms

    // Clean up the consumed operation's allocations
    if next.new_pieces != nil do delete(next.new_pieces)
    if next.old_pieces != nil do delete(next.old_pieces)
    next.new_pieces = nil
    next.old_pieces = nil
    return true
}

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------
undo_stack_can_undo :: proc(us: ^Undo_Stack) -> bool {
    return us.position > 0
}

undo_stack_can_redo :: proc(us: ^Undo_Stack) -> bool {
    return us.position < len(us.ops)
}
