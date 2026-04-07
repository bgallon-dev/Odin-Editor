package editor

import rl "vendor:raylib"
import "core:strings"
import "core:os"
import "core:path/filepath"

// ---------------------------------------------------------------------------
// Update
// ---------------------------------------------------------------------------
editor_update :: proc(ed: ^Editor_State) {
    dt := f64(rl.GetFrameTime())
    ed.blink_timer += dt
    if ed.blink_timer >= CURSOR_BLINK { ed.blink_timer -= CURSOR_BLINK; ed.blink_on = !ed.blink_on }
    if ed.warn_timer > 0 { ed.warn_timer -= dt; if ed.warn_timer <= 0 { ed.warn_timer = 0; ed.close_blocked = false } }

    // Poll IPC for incoming messages
    if ipc_is_connected(&ed.ipc) {
        ipc_msg, ipc_ok := ipc_poll(&ed.ipc)
        if ipc_ok {
            #partial switch v in ipc_msg.data {
            case Draft_Response:
                if len(v.draft_text) > 0 {
                    side_panel_set_draft(&ed.side_panel, v.draft_text, v.confidence)
                    // Move issues to the side panel
                    if len(v.issues) > 0 {
                        side_panel_set_issues(&ed.side_panel, v.issues[:])
                    }
                } else {
                    ed.side_panel.drafting = false
                }
                // Clean up the response's owned strings
                delete(v.draft_text)
                for &issue in v.issues {
                    delete(issue.message)
                }
                delete(v.issues)
            }
        }
    }

    buf := tab_active_buf(&ed.tab_bar)
    shift_held := rl.IsKeyDown(.LEFT_SHIFT) || rl.IsKeyDown(.RIGHT_SHIFT)
    ctrl_held  := rl.IsKeyDown(.LEFT_CONTROL) || rl.IsKeyDown(.RIGHT_CONTROL)

    // --- Quick Open ---
    if ed.quick_open.active {
        result := quick_open_update(&ed.quick_open)
        if len(result) > 0 {
            tab_open_file(&ed.tab_bar, result)
        }
        return
    }

    // Ctrl+P = Quick Open
    if ctrl_held && rl.IsKeyPressed(.P) {
        quick_open_activate(&ed.quick_open, ".")
        return
    }

    // Ctrl+B = Toggle file browser
    if ctrl_held && rl.IsKeyPressed(.B) {
        ed.file_browser.visible = !ed.file_browser.visible
        return
    }

    // Ctrl+M = Toggle minimap
    if ctrl_held && rl.IsKeyPressed(.M) {
        ed.show_minimap = !ed.show_minimap
        return
    }

    // Ctrl+J = Toggle side panel
    if ctrl_held && rl.IsKeyPressed(.J) {
        ed.side_panel.visible = !ed.side_panel.visible
        return
    }

    // Ctrl+D = Request draft from memory system
    if ctrl_held && rl.IsKeyPressed(.D) {
        if ipc_is_connected(&ed.ipc) && len(buf.save_path) > 0 {
            memory_on_draft_request(&ed.ipc, buf)
            ed.side_panel.drafting = true
            ed.side_panel.draft_ready = false
            ed.side_panel.visible = true
            ed.side_panel.active_tab = .Draft
        }
        return
    }

    // Ctrl+Tab / Ctrl+Shift+Tab = switch tabs
    if ctrl_held && rl.IsKeyPressed(.TAB) {
        if shift_held { tab_prev(&ed.tab_bar) } else { tab_next(&ed.tab_bar) }
        return
    }

    // Ctrl+W = close tab
    if ctrl_held && rl.IsKeyPressed(.W) {
        if buf.dirty {
            ed.warn_timer = 2.0
        } else {
            tab_close_active(&ed.tab_bar)
        }
        return
    }

    // Ctrl+N = new tab
    if ctrl_held && rl.IsKeyPressed(.N) {
        tab_new(&ed.tab_bar)
        return
    }

    // --- Find mode ---
    if buf.find.active {
        if rl.IsKeyPressed(.ESCAPE) {
            buf.find.active = false
            clear(&buf.find.matches)
            buf.find.current_match = -1
            return
        }
        if rl.IsKeyPressed(.TAB) && buf.find.show_replace {
            buf.find.focus_replace = !buf.find.focus_replace
            return
        }
        if rl.IsKeyPressed(.ENTER) {
            if buf.find.focus_replace && buf.find.show_replace {
                find_replace_current_buf(ed, buf)
            } else {
                find_next_buf(ed, buf)
            }
            return
        }
        active_buf_ptr := &buf.find.search_buf if !buf.find.focus_replace else &buf.find.replace_buf
        active_len := &buf.find.search_len if !buf.find.focus_replace else &buf.find.replace_len
        if rl.IsKeyPressed(.BACKSPACE) || rl.IsKeyPressedRepeat(.BACKSPACE) {
            if active_len^ > 0 do active_len^ -= 1
            if !buf.find.focus_replace do find_update_matches_buf(buf)
            return
        }
        for { ch := rl.GetCharPressed(); if ch == 0 do break; if ch < 32 do continue
            if active_len^ < 255 { active_buf_ptr[active_len^] = u8(ch); active_len^ += 1 }
            if !buf.find.focus_replace do find_update_matches_buf(buf)
        }
        return
    }

    // --- Normal shortcuts ---
    if ctrl_held && rl.IsKeyPressed(.F) {
        buf.find.active = true; buf.find.show_replace = false; buf.find.focus_replace = false
        if cursor_has_selection(&buf.cursor) {
            sel_s := cursor_sel_start(&buf.cursor); sel_e := cursor_sel_end(&buf.cursor)
            sel_len := min(sel_e - sel_s, 255)
            all := piece_table_content(&buf.pt, context.temp_allocator); raw := transmute([]u8)all
            for i := 0; i < sel_len; i += 1 { buf.find.search_buf[i] = raw[sel_s + i] }
            buf.find.search_len = sel_len
            find_update_matches_buf(buf)
        }
        return
    }
    if ctrl_held && rl.IsKeyPressed(.H) { buf.find.active = true; buf.find.show_replace = true; buf.find.focus_replace = false; return }
    if rl.IsKeyPressed(.ESCAPE) { if cursor_has_selection(&buf.cursor) { cursor_clear_selection(&buf.cursor); return } }

    if ctrl_held && rl.IsKeyPressed(.Z) { cp := undo_stack_undo(&buf.undo_stack, &buf.pt); if cp >= 0 { buf.cursor.offset = cp; cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; cursor_clear_selection(&buf.cursor); reset_blink(ed); buf.dirty = true }; ensure_cursor_visible_buf(ed, buf); return }
    if ctrl_held && rl.IsKeyPressed(.Y) { cp := undo_stack_redo(&buf.undo_stack, &buf.pt); if cp >= 0 { buf.cursor.offset = cp; cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; cursor_clear_selection(&buf.cursor); reset_blink(ed); buf.dirty = true }; ensure_cursor_visible_buf(ed, buf); return }
    if ctrl_held && rl.IsKeyPressed(.S) { save_file_buf(buf, &ed.ipc); ed.close_blocked = false; ed.warn_timer = 0; return }
    if ctrl_held && rl.IsKeyPressed(.A) { buf.cursor.sel_anchor = 0; buf.cursor.offset = buf.pt.doc_length; cursor_recompute_line_col(&buf.pt, &buf.cursor); return }
    if ctrl_held && rl.IsKeyPressed(.C) { if cursor_has_selection(&buf.cursor) do copy_selection_buf(buf); return }
    if ctrl_held && rl.IsKeyPressed(.X) {
        if cursor_has_selection(&buf.cursor) { copy_selection_buf(buf); ts := current_time_ms(); ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); op := execute_delete(&buf.pt, ss, se, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset = ss; cursor_clear_selection(&buf.cursor); cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; reset_blink(ed); buf.dirty = true; ensure_cursor_visible_buf(ed, buf) }; return
    }
    if ctrl_held && rl.IsKeyPressed(.V) {
        clip := rl.GetClipboardText(); if clip != nil { ps := string(clip); if len(ps) > 0 { ts := current_time_ms()
            if cursor_has_selection(&buf.cursor) { ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); op := execute_replace(&buf.pt, ss, se, ps, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset = ss + len(ps); cursor_clear_selection(&buf.cursor)
            } else { op := execute_insert(&buf.pt, buf.cursor.offset, ps, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset += len(ps) }
            cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; reset_blink(ed); buf.dirty = true; ensure_cursor_visible_buf(ed, buf) } }; return
    }

    // Ctrl+Arrow word jump
    if ctrl_held && (rl.IsKeyPressed(.LEFT) || rl.IsKeyPressedRepeat(.LEFT)) { if shift_held do cursor_begin_selection(&buf.cursor); cursor_word_left(&buf.pt, &buf.cursor); if !shift_held do cursor_clear_selection(&buf.cursor); reset_blink(ed); ensure_cursor_visible_buf(ed, buf); return }
    if ctrl_held && (rl.IsKeyPressed(.RIGHT) || rl.IsKeyPressedRepeat(.RIGHT)) { if shift_held do cursor_begin_selection(&buf.cursor); cursor_word_right(&buf.pt, &buf.cursor); if !shift_held do cursor_clear_selection(&buf.cursor); reset_blink(ed); ensure_cursor_visible_buf(ed, buf); return }

    // Arrow keys
    arrow_move :: proc(ed: ^Editor_State, buf: ^Buffer, key: rl.KeyboardKey, shift_held: bool, move: proc(pt: ^Piece_Table, c: ^Cursor)) {
        if rl.IsKeyPressed(key) || rl.IsKeyPressedRepeat(key) {
            if shift_held { cursor_begin_selection(&buf.cursor) } else if cursor_has_selection(&buf.cursor) { cursor_clear_selection(&buf.cursor) }
            move(&buf.pt, &buf.cursor)
            if !shift_held do cursor_clear_selection(&buf.cursor)
            reset_blink(ed); ensure_cursor_visible_buf(ed, buf)
        }
    }
    if !ctrl_held { arrow_move(ed, buf, .LEFT, shift_held, cursor_move_left); arrow_move(ed, buf, .RIGHT, shift_held, cursor_move_right); arrow_move(ed, buf, .UP, shift_held, cursor_move_up); arrow_move(ed, buf, .DOWN, shift_held, cursor_move_down) }

    // Page Up/Down
    vis := get_visible_lines(ed)
    if rl.IsKeyPressed(.PAGE_UP) || rl.IsKeyPressedRepeat(.PAGE_UP) { if shift_held do cursor_begin_selection(&buf.cursor); for i := 0; i < vis-1; i+=1 { cursor_move_up(&buf.pt, &buf.cursor) }; if !shift_held do cursor_clear_selection(&buf.cursor); buf.scroll_y = max(0, buf.scroll_y - vis + 1); reset_blink(ed); ensure_cursor_visible_buf(ed, buf) }
    if rl.IsKeyPressed(.PAGE_DOWN) || rl.IsKeyPressedRepeat(.PAGE_DOWN) { if shift_held do cursor_begin_selection(&buf.cursor); for i := 0; i < vis-1; i+=1 { cursor_move_down(&buf.pt, &buf.cursor) }; if !shift_held do cursor_clear_selection(&buf.cursor); buf.scroll_y = min(get_max_scroll(ed), buf.scroll_y + vis - 1); reset_blink(ed); ensure_cursor_visible_buf(ed, buf) }

    // Home/End
    if rl.IsKeyPressed(.HOME) || rl.IsKeyPressedRepeat(.HOME) { if shift_held do cursor_begin_selection(&buf.cursor); if ctrl_held { buf.cursor.offset = 0; cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col } else { cursor_move_home(&buf.pt, &buf.cursor) }; if !shift_held do cursor_clear_selection(&buf.cursor); reset_blink(ed); ensure_cursor_visible_buf(ed, buf) }
    if rl.IsKeyPressed(.END) || rl.IsKeyPressedRepeat(.END) { if shift_held do cursor_begin_selection(&buf.cursor); if ctrl_held { buf.cursor.offset = buf.pt.doc_length; cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col } else { cursor_move_end(&buf.pt, &buf.cursor) }; if !shift_held do cursor_clear_selection(&buf.cursor); reset_blink(ed); ensure_cursor_visible_buf(ed, buf) }

    // Backspace
    if rl.IsKeyPressed(.BACKSPACE) || rl.IsKeyPressedRepeat(.BACKSPACE) { ts := current_time_ms()
        if cursor_has_selection(&buf.cursor) { ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); op := execute_delete(&buf.pt, ss, se, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset = ss; cursor_clear_selection(&buf.cursor)
        } else if buf.cursor.offset > 0 { op := execute_delete(&buf.pt, buf.cursor.offset-1, buf.cursor.offset, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset -= 1 }
        cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; reset_blink(ed); buf.dirty = true; ensure_cursor_visible_buf(ed, buf) }

    // Delete
    if rl.IsKeyPressed(.DELETE) || rl.IsKeyPressedRepeat(.DELETE) { ts := current_time_ms()
        if cursor_has_selection(&buf.cursor) { ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); op := execute_delete(&buf.pt, ss, se, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset = ss; cursor_clear_selection(&buf.cursor)
        } else if buf.cursor.offset < buf.pt.doc_length { op := execute_delete(&buf.pt, buf.cursor.offset, buf.cursor.offset+1, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op) }
        cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; reset_blink(ed); buf.dirty = true; ensure_cursor_visible_buf(ed, buf) }

    // Enter (auto-indent)
    if rl.IsKeyPressed(.ENTER) || rl.IsKeyPressedRepeat(.ENTER) { ts := current_time_ms()
        indent := get_current_line_indent(&buf.pt, &buf.cursor); nlb: [256]u8; nlb[0] = '\n'
        il := min(len(indent), 255); for i := 0; i < il; i+=1 { nlb[1+i] = indent[i] }; ns := string(nlb[:1+il])
        if cursor_has_selection(&buf.cursor) { ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); op := execute_replace(&buf.pt, ss, se, ns, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset = ss + len(ns); cursor_clear_selection(&buf.cursor)
        } else { op := execute_insert(&buf.pt, buf.cursor.offset, ns, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset += len(ns) }
        cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; reset_blink(ed); buf.dirty = true; ensure_cursor_visible_buf(ed, buf) }

    // Tab (not ctrl+tab)
    if !ctrl_held && rl.IsKeyPressed(.TAB) { ts := current_time_ms(); ts_str := "    "
        if cursor_has_selection(&buf.cursor) { ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); op := execute_replace(&buf.pt, ss, se, ts_str, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset = ss + 4; cursor_clear_selection(&buf.cursor)
        } else { op := execute_insert(&buf.pt, buf.cursor.offset, ts_str, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset += 4 }
        cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; reset_blink(ed); buf.dirty = true; ensure_cursor_visible_buf(ed, buf) }

    // Char input
    for { ch := rl.GetCharPressed(); if ch == 0 do break; if ch < 32 do continue
        ts := current_time_ms(); b: [4]u8; cl := encode_utf8(b[:], ch); cs := string(b[:cl])
        if cursor_has_selection(&buf.cursor) { ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); op := execute_replace(&buf.pt, ss, se, cs, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset = ss + cl; cursor_clear_selection(&buf.cursor)
        } else { op := execute_insert(&buf.pt, buf.cursor.offset, cs, buf.cursor.offset, ts); undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.cursor.offset += cl }
        cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col; reset_blink(ed); buf.dirty = true; ensure_cursor_visible_buf(ed, buf)
    }

    // === Mouse ===
    win_h := int(rl.GetScreenHeight())
    left_m := get_left_margin(ed)
    text_top := get_text_top()
    text_bottom := win_h - LINE_HEIGHT
    if buf.find.active { text_bottom -= SEARCH_BAR_H; if buf.find.show_replace do text_bottom -= SEARCH_BAR_H }

    mx := int(rl.GetMouseX())
    my := int(rl.GetMouseY())

    // Tab bar clicks
    if (rl.IsMouseButtonPressed(.LEFT) || rl.IsMouseButtonPressed(.MIDDLE)) && my < TAB_BAR_H {
        sidebar_off := 0
        if ed.file_browser.visible do sidebar_off = SIDEBAR_W
        tab_idx := (mx - sidebar_off) / TAB_W
        if tab_idx >= 0 && tab_idx < len(ed.tab_bar.tabs) {
            if rl.IsMouseButtonPressed(.MIDDLE) {
                // Middle-click closes the tab
                tab_close(&ed.tab_bar, tab_idx)
            } else {
                // Left-click: close button or switch tab
                local_x := (mx - sidebar_off) - tab_idx * TAB_W
                if local_x >= TAB_W - 20 {
                    tab_close(&ed.tab_bar, tab_idx)
                } else {
                    ed.tab_bar.active = tab_idx
                }
            }
        }
        return
    }

    // Text area clicks
    if rl.IsMouseButtonPressed(.LEFT) && my >= text_top && my < text_bottom && mx >= left_m - GUTTER_W {
        now := rl.GetTime()

        if mx < left_m {
            // Gutter click: select line
            screen_line := (my - text_top) / LINE_HEIGHT
            clicked_line := buf.scroll_y + screen_line
            clicked_line = clamp(clicked_line, 0, max(0, buf.pt.total_lines - 1))
            ls := find_line_start(&buf.pt, clicked_line)
            le := find_line_start(&buf.pt, clicked_line + 1) if clicked_line < buf.pt.total_lines - 1 else buf.pt.doc_length
            buf.cursor.sel_anchor = ls; buf.cursor.offset = le
            cursor_recompute_line_col(&buf.pt, &buf.cursor); buf.cursor.preferred_col = buf.cursor.col
            reset_blink(ed); ed.is_dragging = false
        } else {
            // Text area click
            dx := mx - ed.last_click_x; dy := my - ed.last_click_y
            if now - ed.last_click_time < DOUBLE_CLICK_T && dx*dx+dy*dy < 100 { ed.click_count += 1 } else { ed.click_count = 1 }
            ed.last_click_time = now; ed.last_click_x = mx; ed.last_click_y = my

            screen_line := (my - text_top) / LINE_HEIGHT
            clicked_line := buf.scroll_y + screen_line
            clicked_col := int(f32(mx - left_m) / ed.char_width + 0.5)
            clicked_line = clamp(clicked_line, 0, max(0, buf.pt.total_lines - 1))
            if clicked_col < 0 do clicked_col = 0

            if ed.click_count == 3 {
                ls := find_line_start(&buf.pt, clicked_line)
                le := find_line_start(&buf.pt, clicked_line + 1) if clicked_line < buf.pt.total_lines - 1 else buf.pt.doc_length
                buf.cursor.sel_anchor = ls; buf.cursor.offset = le
                cursor_recompute_line_col(&buf.pt, &buf.cursor); ed.is_dragging = false
            } else if ed.click_count == 2 {
                cursor_move_to_line_col(&buf.pt, &buf.cursor, clicked_line, clicked_col)
                select_word_at_cursor(&buf.pt, &buf.cursor); ed.is_dragging = false
            } else {
                if shift_held { cursor_begin_selection(&buf.cursor) } else { cursor_clear_selection(&buf.cursor) }
                cursor_move_to_line_col(&buf.pt, &buf.cursor, clicked_line, clicked_col)
                buf.cursor.preferred_col = buf.cursor.col
                if !shift_held { ed.is_dragging = true; buf.cursor.sel_anchor = buf.cursor.offset }
            }
            reset_blink(ed)
        }
    }

    // Drag
    if ed.is_dragging && rl.IsMouseButtonDown(.LEFT) && mx >= left_m && my >= text_top && my < text_bottom {
        sl := (my - text_top) / LINE_HEIGHT; dl := buf.scroll_y + sl; dc := int(f32(mx - left_m) / ed.char_width + 0.5)
        dl = clamp(dl, 0, max(0, buf.pt.total_lines - 1)); if dc < 0 do dc = 0
        cursor_move_to_line_col(&buf.pt, &buf.cursor, dl, dc); buf.cursor.preferred_col = buf.cursor.col
        reset_blink(ed); ensure_cursor_visible_buf(ed, buf)
    }
    if rl.IsMouseButtonReleased(.LEFT) { if ed.is_dragging { ed.is_dragging = false; if buf.cursor.sel_anchor == buf.cursor.offset do cursor_clear_selection(&buf.cursor) } }

    // Scroll wheel
    wheel := rl.GetMouseWheelMove()
    if wheel != 0 && mx >= left_m { buf.scroll_y -= int(wheel * 3); buf.scroll_y = clamp(buf.scroll_y, 0, get_max_scroll(ed)) }
}

// ---------------------------------------------------------------------------
// Input helper procs
// ---------------------------------------------------------------------------
reset_blink :: proc(ed: ^Editor_State) { ed.blink_timer = 0; ed.blink_on = true }

ensure_cursor_visible_buf :: proc(ed: ^Editor_State, buf: ^Buffer) {
    vis := get_visible_lines(ed)
    if buf.cursor.line < buf.scroll_y do buf.scroll_y = buf.cursor.line
    if buf.cursor.line >= buf.scroll_y + vis do buf.scroll_y = buf.cursor.line - vis + 1
}

copy_selection_buf :: proc(buf: ^Buffer) {
    ss := cursor_sel_start(&buf.cursor); se := cursor_sel_end(&buf.cursor); if ss >= se do return
    all := piece_table_content(&buf.pt, context.temp_allocator); raw := transmute([]u8)all
    cstr := strings.clone_to_cstring(string(raw[ss:se]), context.temp_allocator); rl.SetClipboardText(cstr)
}

find_update_matches_buf :: proc(buf: ^Buffer) {
    clear(&buf.find.matches); buf.find.current_match = -1; if buf.find.search_len == 0 do return
    needle := string(buf.find.search_buf[:buf.find.search_len])
    all := piece_table_content(&buf.pt, context.temp_allocator); raw := transmute([]u8)all
    for i := 0; i <= len(raw) - buf.find.search_len; i += 1 {
        found := true; for j := 0; j < buf.find.search_len; j += 1 { if raw[i+j] != needle[j] { found = false; break } }
        if found do append(&buf.find.matches, i)
    }
    for mi := 0; mi < len(buf.find.matches); mi += 1 { if buf.find.matches[mi] >= buf.cursor.offset { buf.find.current_match = mi; break } }
    if buf.find.current_match < 0 && len(buf.find.matches) > 0 do buf.find.current_match = 0
}

find_next_buf :: proc(ed: ^Editor_State, buf: ^Buffer) {
    if len(buf.find.matches) == 0 { find_update_matches_buf(buf); if len(buf.find.matches) == 0 do return }
    if buf.find.current_match >= 0 { buf.find.current_match = (buf.find.current_match + 1) % len(buf.find.matches) } else { buf.find.current_match = 0 }
    mo := buf.find.matches[buf.find.current_match]; buf.cursor.sel_anchor = mo; buf.cursor.offset = mo + buf.find.search_len
    cursor_recompute_line_col(&buf.pt, &buf.cursor); ensure_cursor_visible_buf(ed, buf)
}

find_replace_current_buf :: proc(ed: ^Editor_State, buf: ^Buffer) {
    if buf.find.current_match < 0 || buf.find.current_match >= len(buf.find.matches) do return
    mo := buf.find.matches[buf.find.current_match]; rs := string(buf.find.replace_buf[:buf.find.replace_len])
    ts := current_time_ms(); op := execute_replace(&buf.pt, mo, mo + buf.find.search_len, rs, buf.cursor.offset, ts)
    undo_stack_push(&buf.undo_stack, &buf.pt, op); buf.dirty = true; buf.cursor.offset = mo + len(rs)
    cursor_recompute_line_col(&buf.pt, &buf.cursor); cursor_clear_selection(&buf.cursor); find_update_matches_buf(buf); ensure_cursor_visible_buf(ed, buf)
}

save_file_buf :: proc(buf: ^Buffer, conn: ^IPC_Connection = nil) {
    if len(buf.save_path) == 0 {
        if len(buf.filepath) == 0 || buf.filepath == "[new]" { buf.filepath = "untitled.txt" }
        os.make_directory(DOCS_DIR); buf.save_path = filepath.join({DOCS_DIR, buf.filepath})
    }
    content := piece_table_content(&buf.pt); defer delete(content)
    raw := transmute([]u8)content; ok := os.write_entire_file(buf.save_path, raw)
    if ok {
        buf.dirty = false
        // Notify the memory system
        if conn != nil {
            memory_on_file_saved(conn, buf.save_path)
        }
    }
}
